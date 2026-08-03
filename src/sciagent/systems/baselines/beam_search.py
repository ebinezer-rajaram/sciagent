"""B5: symbolic beam search over the agent grammar, scored by predictive fit.

SPEC §5's enumeration-versus-generation comparator. B5 produces the *same output
type* as an LLM proposal layer -- a structure the agent grammar licenses -- by
searching for it rather than generating it. If enumeration matches generation on
the slice, the case for an LLM proposal layer rests on scenarios where the space
is too large to enumerate, and that is a finding worth having.

Cost is the whole design problem
--------------------------------

Scoring a candidate means knowing what it predicts, which means a table row,
which means simulating it. The agent grammar licenses about eighteen million
single edits, so scoring is rationed twice: the beam only ever visits ``width``
survivors per level, and the fit function is injected so the harness decides how
many replicates a *search* estimate is worth. That is deliberately far fewer
than the engine's own table uses -- a search that has to be as precise as the
posterior it feeds would be unaffordable, and does not have to be, because the
engine re-scores whatever B5 proposes at full precision anyway.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence

from sciagent.core.edits import Defect, EditGrammar, canonical, target_of
from sciagent.core.errors import (
    ExecutionError,
    GrammarError,
    InvestigationError,
    OutOfRangeError,
)
from sciagent.core.types import Diagnosis, HypothesisId
from sciagent.experiments.dsl import defect_key
from sciagent.hypothesis.validator import find_duplicate
from sciagent.inference.empirical import EmpiricalTable
from sciagent.inference.interface import Observation, Simulator
from sciagent.systems.base import Investigation

__all__ = ["BeamSearch", "PredictiveFit", "table_fit"]

#: How well a candidate structure explains what has been observed, in nats.
#: Higher is better. Injected rather than fixed so that the precision -- and
#: therefore the cost -- of a search estimate is the harness's decision.
type PredictiveFit = Callable[[Defect, Sequence[Observation]], float]

#: The two ways a candidate can turn out to have no predictive fit at all.
#: ``ExecutionError`` is a diagnostic that cannot be computed on what the
#: structure generated -- a rate so high the run spans less than one measurement
#: window, say. ``OutOfRangeError`` is a value outside the metric's declared
#: range, so the discretisation has no cell for it.
#:
#: Both are properties of the *candidate*, not faults: an edit space's corners
#: contain parameterisations that produce degenerate programmes, and a search
#: that enumerates corners will find them. They are scored ``-inf`` rather than
#: skipped, so an unmeasurable structure is ranked last by a stated verdict
#: instead of vanishing from a search that then looks exhaustive.
_UNSCORABLE = (ExecutionError, OutOfRangeError)


def table_fit(table: EmpiricalTable, simulate: Simulator) -> PredictiveFit:
    """Return a fit function backed by a table, filling rows on demand.

    The returned callable is stateful: a structure simulated once is kept, so a
    beam that revisits a candidate pays for it once per process. That makes the
    reported cost of a search depend on the order candidates are visited in,
    which is why :class:`BeamSearch` visits them in a fixed order.

    ``table`` should be a *search* table -- cheap, low-replicate -- and not the
    engine's. Sharing the engine's would make every candidate cost what a
    calibrated row costs, for an estimate that is thrown away as soon as the
    beam moves on.

    A candidate the environment cannot measure scores ``-inf``; see
    :data:`_UNSCORABLE`. Nothing else is caught, so a genuine framework fault
    still propagates rather than being ranked last and forgotten.
    """
    held = {"table": table}

    def fit(defect: Defect, observations: Sequence[Observation]) -> float:
        current = held["table"]
        if not current.holds(defect):
            try:
                current, _ = current.with_structure(defect, simulate)
            except _UNSCORABLE:
                return -math.inf
            held["table"] = current
        return math.fsum(
            current.estimate(
                defect, observation.template, observation.result
            ).log_likelihood
            for observation in observations
        )

    return fit


class BeamSearch:
    """SPEC §5's B5.

    Guarantees the search is deterministic -- candidates are enumerated in the
    grammar's fixed order and ties break by canonical structure key, never by
    dict or set order -- and that every structure it proposes is one the injected
    grammar licenses.
    """

    __slots__ = ("_fit", "_grammar", "_levels", "_parameters", "_width")

    def __init__(
        self,
        grammar: EditGrammar,
        fit: PredictiveFit,
        *,
        width: int = 3,
        levels: int = 2,
        parameters_per_structure: int | None = 1,
    ) -> None:
        if width < 1:
            raise InvestigationError(
                f"a beam of width {width} searches nothing; ask for at least one"
            )
        if levels < 1:
            raise InvestigationError(
                f"a search of {levels} level(s) considers no edit; ask for at least one"
            )
        self._grammar = grammar
        self._fit = fit
        self._width = width
        self._levels = levels
        self._parameters = parameters_per_structure

    @property
    def name(self) -> str:
        """Return SPEC §5's identifier."""
        return "B5"

    def investigate(self, investigation: Investigation) -> Diagnosis:
        """Observe, search, propose the best structure, then spend what is left.

        Like B4, half the budget is spent before searching: predictive fit is
        defined against observations, so a search with none would rank candidates
        by their priors alone.
        """
        total = int(investigation.budget.remaining)
        self._rotate(investigation, (total + 1) // 2)

        beam = self._search(investigation)
        proposed: list[HypothesisId] = []
        for rank, (_, key, defect) in enumerate(beam[:1]):
            if find_duplicate(investigation.graph, defect) is not None:
                continue
            node_id = HypothesisId(f"beam/{rank}/{key}")
            investigation.propose(
                node_id,
                program_edit=defect,
                rationale=f"beam search, predictive fit rank {rank}",
            )
            proposed.append(node_id)

        self._rotate(investigation, int(investigation.budget.remaining))
        return investigation.conclude(
            residual_candidates=tuple(
                HypothesisId(f"beam/{rank}/{key}")
                for rank, (_, key, _) in enumerate(beam)
                if rank >= 1
            ),
        )

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _rotate(investigation: Investigation, count: int) -> None:
        """Run ``count`` experiments in the scenario's design order, repeating."""
        designs = investigation.designs
        for step in range(max(0, count)):
            if not investigation.affords():
                return
            investigation.run(designs[step % len(designs)])

    def _search(
        self, investigation: Investigation
    ) -> tuple[tuple[float, str, Defect], ...]:
        """Return the final beam, best first, as ``(fit, key, defect)`` triples.

        Level one scores every single edit the grammar licenses at the requested
        parameter resolution. Each later level extends each survivor by one more
        edit, which is how a compound defect (SPEC §4.5 S8) is reachable at all.
        """
        observations = investigation.engine.observations
        if not observations:
            return ()
        beam: tuple[tuple[float, str, Defect], ...] = ()
        seen: set[str] = set()
        for level in range(self._levels):
            candidates = self._singles() if level == 0 else self._extensions(beam)
            scored: list[tuple[float, str, Defect]] = []
            for defect in candidates:
                key = defect_key(defect)
                if key in seen:
                    continue
                seen.add(key)
                scored.append((self._fit(defect, observations), key, defect))
            if not scored:
                break
            merged = [*beam, *scored]
            # Sort by descending fit, then by key, so ties never depend on the
            # order candidates happened to be generated in.
            merged.sort(key=lambda item: (-item[0], item[1]))
            beam = tuple(merged[: self._width])
        return beam

    def _singles(self) -> Iterable[Defect]:
        """Yield every single-edit defect, in the grammar's enumeration order."""
        for edit in self._grammar.enumerate_edits(self._parameters):
            yield frozenset({edit})

    def _extensions(
        self, beam: Sequence[tuple[float, str, Defect]]
    ) -> Iterable[Defect]:
        """Yield each beam member extended by one further licensed edit.

        An extension the grammar refuses is skipped rather than raised on: the
        product of a beam and an edit space contains combinations no programme
        admits, and discovering that is what validation is for.
        """
        for _, _, defect in beam:
            for edit in self._grammar.enumerate_edits(self._parameters):
                extended = frozenset({*defect, edit})
                if len(extended) == len(defect):
                    continue
                if _conflicts(extended):
                    continue
                try:
                    self._grammar.validate_defect(extended)
                except GrammarError:
                    continue
                yield extended


def _conflicts(defect: Defect) -> bool:
    """Return whether two edits of ``defect`` are of one type on one component.

    Two reparameterisations of the same component are not a compound mechanism;
    they are one mechanism written twice, and whichever the grammar applied last
    would silently decide what the structure meant.
    """
    seen: set[tuple[str, str]] = set()
    for edit in canonical(defect):
        signature = (type(edit).__name__, str(target_of(edit)))
        if signature in seen:
            return True
        seen.add(signature)
    return False

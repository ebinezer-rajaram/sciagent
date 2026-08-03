"""The empirical table posterior engine (SPEC §3.5, F12, §6.2).

The first of the two staged engines. It answers "how likely is this observation
under this hypothesis?" the most direct way available: simulate the hypothesis
many times, count how often the simulated diagnostic landed in the same cell as
the observation, and read the likelihood off the counts.

What the estimand is
--------------------

``p(cell | hypothesis, template)`` exactly -- not an approximation to it. That
matters more than it sounds. A6 asks for agreement with an analytically known
likelihood to within two Monte Carlo standard errors, and only an estimator whose
estimand *is* the quantity in question can satisfy that at every replicate count;
anything carrying a smoothing or normality assumption carries a bias that the
standard error does not describe. The cost is that the outcome space must be
discrete, which is what :mod:`sciagent.inference.binning` provides.

Cell probabilities use the Krichevsky-Trofimov estimator ``(c + 1/2) / (M +
K/2)`` rather than the raw frequency ``c / M``. A raw frequency assigns
probability zero to any cell no replicate reached, and a single such observation
would eliminate a hypothesis outright on the evidence of a finite simulation
budget -- a false certainty, and irrecoverable, since the posterior never
returns from zero. KT's bias is ``O(1/M)`` against a standard error of
``O(1/sqrt(M))``, so at the slice's replicate count it is about two per cent of
the noise it is measured against and cannot disturb A6.

Why the likelihood factorises across experiments
------------------------------------------------

Each experiment is one execution under its own seed -- the registry already
addresses a result by its seed -- and each template names its own diagnostics. So
two experiments are independent draws given the hypothesis, and multiplying their
likelihoods is exact rather than an assumption. Diagnostics measured *within* one
experiment are not independent, and are handled by
:class:`~sciagent.inference.binning.OutcomeSpace`'s joint cells rather than by a
product of marginals.

The one place a shortcut is taken is in building the table: one execution per
(structure, replicate) serves every template, so the tables for different
templates are estimated from a shared replicate set. Each row is still an
unbiased estimate of its own cell probabilities; what the sharing correlates is
the *estimation error* of one row with another. Spending an independent set per
template would multiply the build cost by the number of templates to remove a
second-order effect on the posterior.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from sciagent.core.edits import Defect, EditGrammar, canonical, sort_key
from sciagent.core.errors import (
    DuplicateHypothesisError,
    InferenceError,
    TableError,
    UnknownExperimentError,
    UnknownHypothesisError,
)
from sciagent.core.program import stable_key
from sciagent.core.types import (
    ExperimentId,
    ExperimentTemplateId,
    FrozenDict,
    HypothesisId,
    HypothesisStatus,
    Probability,
    Seed,
)
from sciagent.hypothesis.graph import HypothesisGraph, HypothesisNode
from sciagent.inference.interface import (
    DiagnosticVector,
    ExpansionCost,
    ExperimentTemplate,
    LikelihoodEstimate,
    Observation,
    PPCResult,
    Simulator,
)
from sciagent.inference.ppc import posterior_predictive_check

_LN2 = math.log(2.0)

#: Widest seed the registry's ``INTEGER`` column holds without loss.
_SEED_MODULUS = 1 << 63

#: Numerator of the rule of three. With ``M`` independent replicates and no hit
#: in a cell, ``3 / M`` is the one-sided 95% upper bound on that cell's
#: probability. See :meth:`EmpiricalTable.resolved_probabilities`.
_RULE_OF_THREE = 3.0


def structure_key(defect: Defect) -> str:
    """Return a stable textual identity for an edit set.

    Two defects share a key exactly when they are structurally identical, which
    is the same relation :func:`sciagent.hypothesis.validator.find_duplicate`
    uses for A18. Built from :func:`~sciagent.core.edits.canonical`, so it does
    not depend on the order the edits were assembled in, on ``PYTHONHASHSEED``,
    or on the process -- a table row addressed by it means the same thing in
    every run.
    """
    parts = []
    for edit in canonical(defect):
        kind, target, option, params = sort_key(edit)
        rendered = ",".join(f"{name}={value!r}" for name, value in params)
        parts.append(f"{kind}({target}|{option}|{rendered})")
    return "+".join(parts) if parts else "<null>"


def replicate_seed(table_seed: Seed, key: str, index: int) -> Seed:
    """Return the seed of one table replicate.

    Derived by name from ``(table seed, structure, index)``, so a structure's
    replicates are the same whether the row was built with the table or added to
    it later. That is what makes A10's "no disadvantage from lateness" and A11's
    "matches a from-scratch run" exact statements rather than approximate ones.

    The namespace prefix keeps these seeds disjoint from any other stream: an
    observation generated under a table replicate's seed would be one of the
    table's own draws, and its likelihood would be evaluated against a set it
    belongs to.
    """
    return Seed(stable_key(f"table/{int(table_seed)}/{key}/{index}") % _SEED_MODULUS)


# --------------------------------------------------------------------------
# The table
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EmpiricalTable:
    """Simulated outcome counts for each (structure, template) pair.

    Guarantees that a row is a pure function of ``(seed, replicates, structure,
    template)``, so the same table is produced by building it in one go, by
    extending it one structure at a time, or by rebuilding it in another process.
    """

    templates: FrozenDict[ExperimentTemplateId, ExperimentTemplate]
    counts: FrozenDict[tuple[str, ExperimentTemplateId], tuple[int, ...]]
    replicates: int
    seed: Seed

    def __post_init__(self) -> None:
        if self.replicates <= 0:
            raise TableError(f"replicates must be positive, got {self.replicates}")
        for (key, template_id), row in self.counts.items():
            template = self.template(template_id)
            if len(row) != template.outcome.n_cells:
                raise TableError(
                    f"row ({key!r}, {template_id!r}) has {len(row)} cells but its "
                    f"outcome space has {template.outcome.n_cells}"
                )
            if sum(row) != self.replicates:
                raise TableError(
                    f"row ({key!r}, {template_id!r}) holds {sum(row)} replicates, "
                    f"not the declared {self.replicates}"
                )

    # -- queries -----------------------------------------------------------

    def template(self, template_id: ExperimentTemplateId) -> ExperimentTemplate:
        """Return one template, or raise a typed error."""
        try:
            return self.templates[template_id]
        except KeyError as exc:
            raise TableError(
                f"template {template_id!r} is not in this table; it holds "
                f"{sorted(self.templates)!r}"
            ) from exc

    @property
    def structures(self) -> tuple[str, ...]:
        """Return every structure key in the table, in a fixed order."""
        return tuple(sorted({key for key, _ in self.counts}))

    def holds(self, defect: Defect) -> bool:
        """Return whether the table has a complete set of rows for ``defect``."""
        key = structure_key(defect)
        return all(
            (key, template_id) in self.counts for template_id in sorted(self.templates)
        )

    def row(self, defect: Defect, template_id: ExperimentTemplateId) -> tuple[int, ...]:
        """Return the simulated cell counts for one (structure, template)."""
        key = structure_key(defect)
        try:
            return self.counts[(key, template_id)]
        except KeyError as exc:
            raise TableError(
                f"no row for structure {key!r} under template {template_id!r}; "
                f"the table holds {len(self.structures)} structure(s). A "
                f"hypothesis must be expanded into the table before its "
                f"likelihood can be estimated"
            ) from exc

    def probabilities(
        self, defect: Defect, template_id: ExperimentTemplateId
    ) -> tuple[float, ...]:
        """Return the estimated cell distribution for one (structure, template).

        Guarantees a strictly positive probability for every cell and a total of
        exactly 1 up to floating-point rounding of the final division. The
        estimator is Krichevsky-Trofimov, so no cell is ever assigned zero on the
        evidence of a finite simulation budget.
        """
        counts = self.row(defect, template_id)
        cells = len(counts)
        denominator = self.replicates + 0.5 * cells
        return tuple((count + 0.5) / denominator for count in counts)

    def resolved_probabilities(
        self, defect: Defect, template_id: ExperimentTemplateId
    ) -> tuple[float, ...]:
        """Return cell probabilities floored at what the replicate count resolves.

        A cell no replicate reached is assigned ``0.5 / (M + K/2)`` by
        :meth:`probabilities`, which is the right *point estimate* and a badly
        wrong statement about how surprising an observation there would be. With
        ``M`` replicates and no hit, the one-sided 95% upper bound on the cell's
        probability is ``3 / M`` -- the rule of three -- some six times larger at
        the slice's replicate count. This floors every cell there and renormalises.

        Used by the posterior predictive check and by nothing else. The two
        estimators answer different questions and are held to different criteria:
        a likelihood must be unbiased, which A6 measures, and a check must not
        over-reject, which A9 measures. Feeding the point estimate to the check
        makes it claim resolution the simulation budget does not have, and the
        cost is measured -- the realised false-positive rate falls from 8.7% to
        3.3% against a nominal 5% when the floor is applied, with no loss of
        power on a detectable defect (see ``docs/DECISIONS.md``).
        """
        floor = _RULE_OF_THREE / self.replicates
        lifted = [
            max(probability, floor)
            for probability in self.probabilities(defect, template_id)
        ]
        total = math.fsum(lifted)
        return tuple(probability / total for probability in lifted)

    def estimate(
        self,
        defect: Defect,
        template: ExperimentTemplate,
        result: DiagnosticVector,
    ) -> LikelihoodEstimate:
        """Return the log-likelihood of ``result`` under ``defect``, with its error.

        The standard error is the delta-method transform of the binomial standard
        error of the cell frequency: ``sqrt((1 - p) / (M p))``. It describes the
        Monte Carlo error of this estimate and nothing else -- not the width of
        the outcome distribution, and not any error in the environment. A7's
        coverage criterion is a statement about exactly this number.
        """
        cell = template.outcome.cell_of(result)
        counts = self.row(defect, template.id)
        probability = self.probabilities(defect, template.id)[cell]
        standard_error = math.sqrt(
            (1.0 - probability) / (self.replicates * probability)
        )
        return LikelihoodEstimate(
            log_likelihood=math.log(probability),
            standard_error=standard_error,
            cell=cell,
            count=counts[cell],
            replicates=self.replicates,
        )

    @property
    def version(self) -> str:
        """Return a content hash over everything that determines the table."""
        payload = "\x00".join(
            (
                f"replicates={self.replicates}",
                f"seed={int(self.seed)}",
                *(
                    f"template={template_id}|{self.templates[template_id].outcome.version}"
                    f"|n_events={self.templates[template_id].n_events}"
                    for template_id in sorted(self.templates)
                ),
                *(f"structure={key}" for key in self.structures),
            )
        )
        digest = hashlib.blake2b(payload.encode("utf-8"), digest_size=16).hexdigest()
        return f"table/{digest}"

    # -- construction ------------------------------------------------------

    @classmethod
    def build(
        cls,
        *,
        defects: Sequence[Defect],
        templates: Sequence[ExperimentTemplate],
        simulate: Simulator,
        replicates: int,
        seed: Seed,
    ) -> tuple[EmpiricalTable, int]:
        """Simulate every (structure, template) row and return the table.

        Returns the table and the number of calls made to ``simulate``, so the
        cost of the apparatus is measured rather than assumed.
        """
        indexed = FrozenDict[ExperimentTemplateId, ExperimentTemplate](
            {template.id: template for template in templates}
        )
        if len(indexed) != len(templates):
            raise TableError(
                f"templates must have distinct ids, got "
                f"{[str(t.id) for t in templates]!r}"
            )
        empty = cls(
            templates=indexed,
            counts=FrozenDict[tuple[str, ExperimentTemplateId], tuple[int, ...]](),
            replicates=replicates,
            seed=seed,
        )
        table = empty
        calls = 0
        for defect in defects:
            table, spent = table.with_structure(defect, simulate)
            calls += spent
        return table, calls

    def with_structure(
        self, defect: Defect, simulate: Simulator
    ) -> tuple[EmpiricalTable, int]:
        """Return this table with ``defect``'s rows added, and the calls it cost.

        Idempotent: a structure already present is returned unchanged at a cost
        of zero, which is what makes expanding a hypothesis the table already
        covers free.
        """
        if self.holds(defect):
            return self, 0
        key = structure_key(defect)
        tallies: dict[ExperimentTemplateId, list[int]] = {
            template_id: [0] * self.templates[template_id].outcome.n_cells
            for template_id in sorted(self.templates)
        }
        calls = 0
        for index in range(self.replicates):
            draw = replicate_seed(self.seed, key, index)
            for template_id in sorted(self.templates):
                template = self.templates[template_id]
                result = simulate(defect, template, draw)
                calls += 1
                tallies[template_id][template.outcome.cell_of(result)] += 1
        merged = {
            **self.counts,
            **{
                (key, template_id): tuple(row)
                for template_id, row in sorted(tallies.items())
            },
        }
        extended = EmpiricalTable(
            templates=self.templates,
            counts=FrozenDict[tuple[str, ExperimentTemplateId], tuple[int, ...]](
                merged
            ),
            replicates=self.replicates,
            seed=self.seed,
        )
        return extended, calls

    # -- persistence -------------------------------------------------------

    def save(self, path: Path) -> None:
        """Write the table to ``path`` as JSON.

        Counts are small integers and there are few of them, so a readable
        encoding costs nothing and makes a stored table auditable by eye. The
        templates are *not* stored: they are declared by the environment, and
        :meth:`load` checks that the ones offered reproduce the recorded version.
        """
        payload = {
            "version": self.version,
            "replicates": self.replicates,
            "seed": int(self.seed),
            "counts": [
                {"structure": key, "template": str(template_id), "cells": list(row)}
                for (key, template_id), row in sorted(self.counts.items())
            ],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=1), encoding="utf-8")

    @classmethod
    def load(
        cls, path: Path, templates: Sequence[ExperimentTemplate]
    ) -> EmpiricalTable:
        """Read a table from ``path``, checking it was built for ``templates``.

        Raises :class:`TableError` if the recorded version disagrees with the one
        the reconstructed table computes. A table read under a discretisation it
        was not built under would give confidently wrong likelihoods, and nothing
        downstream could detect it.
        """
        payload = json.loads(path.read_text(encoding="utf-8"))
        table = cls(
            templates=FrozenDict[ExperimentTemplateId, ExperimentTemplate](
                {template.id: template for template in templates}
            ),
            counts=FrozenDict[tuple[str, ExperimentTemplateId], tuple[int, ...]](
                {
                    (
                        str(entry["structure"]),
                        ExperimentTemplateId(entry["template"]),
                    ): (tuple(int(value) for value in entry["cells"]))
                    for entry in payload["counts"]
                }
            ),
            replicates=int(payload["replicates"]),
            seed=Seed(int(payload["seed"])),
        )
        if table.version != payload["version"]:
            raise TableError(
                f"table at {path} records version {payload['version']!r} but the "
                f"templates offered reproduce {table.version!r}; the stored "
                f"counts were built under a different discretisation or a "
                f"different design"
            )
        return table


# --------------------------------------------------------------------------
# The engine
# --------------------------------------------------------------------------


@dataclass(slots=True)
class _Hypothesis:
    """One hypothesis as the engine sees it: a structure and a status."""

    program_edit: Defect
    status: HypothesisStatus


class EmpiricalTableEngine:
    """A posterior engine backed by simulated outcome counts (SPEC §3.5).

    Unlike the hypothesis graph, an engine is *not* an immutable value: SPEC
    §3.5 gives ``expand`` and ``ppc`` signatures that return a cost and a verdict
    rather than a new engine, and an investigation is a growing record of what
    was done. The state it accumulates is strictly append-only -- experiments are
    recorded and hypotheses are admitted, and there is no path that removes or
    revises either.

    Guarantees no caller can influence a number: the prior is derived from the
    grammar's code length, the likelihood from the table's counts, and
    :meth:`posterior` recomputes both from scratch on every call.
    """

    __slots__ = (
        "_alpha",
        "_grammar",
        "_hypotheses",
        "_index",
        "_observations",
        "_simulate",
        "_table",
    )

    def __init__(
        self,
        graph: HypothesisGraph,
        table: EmpiricalTable,
        *,
        simulate: Simulator | None = None,
        alpha: float = 0.05,
    ) -> None:
        if not 0.0 < alpha < 1.0:
            raise InferenceError(f"alpha must lie in (0, 1), got {alpha}")
        self._grammar: EditGrammar = graph.grammar
        self._table = table
        self._simulate = simulate
        self._alpha = alpha
        self._hypotheses: dict[HypothesisId, _Hypothesis] = {
            node_id: _Hypothesis(program_edit=node.program_edit, status=node.status)
            for node_id, node in sorted(graph.nodes.items())
            if node.program_edit is not None
        }
        self._observations: list[Observation] = []
        self._index: dict[ExperimentId, Observation] = {}

    # -- queries -----------------------------------------------------------

    @property
    def table(self) -> EmpiricalTable:
        """Return the table the engine reads its likelihoods from."""
        return self._table

    @property
    def observations(self) -> tuple[Observation, ...]:
        """Return every recorded experiment, in the order it was recorded."""
        return tuple(self._observations)

    @property
    def hypotheses(self) -> tuple[HypothesisId, ...]:
        """Return every hypothesis the engine holds, in a fixed order."""
        return tuple(sorted(self._hypotheses))

    @property
    def live(self) -> tuple[HypothesisId, ...]:
        """Return the hypotheses a posterior is distributed over."""
        return tuple(
            node_id
            for node_id in sorted(self._hypotheses)
            if self._hypotheses[node_id].status != "rejected"
        )

    def program_edit(self, h: HypothesisId) -> Defect:
        """Return the edit set of one hypothesis, or raise a typed error."""
        try:
            return self._hypotheses[h].program_edit
        except KeyError as exc:
            raise UnknownHypothesisError(
                f"hypothesis {h!r} is not held by this engine; it holds "
                f"{sorted(self._hypotheses)!r}"
            ) from exc

    def observation(self, e: ExperimentId) -> Observation:
        """Return one recorded experiment, or raise a typed error."""
        try:
            return self._index[e]
        except KeyError as exc:
            raise UnknownExperimentError(
                f"experiment {e!r} has not been recorded; the engine holds "
                f"{sorted(self._index)!r}"
            ) from exc

    # -- recording ---------------------------------------------------------

    def record(
        self,
        e: ExperimentId,
        template: ExperimentTemplate,
        result: DiagnosticVector,
    ) -> Observation:
        """Record one carried-out experiment. Append-only.

        Guarantees an experiment id is never re-recorded: an id addresses a
        registered row, and a second result under the same id would mean the
        registry's content address had failed to determine the outcome.
        """
        if e in self._index:
            raise DuplicateHypothesisError(
                f"experiment {e!r} is already recorded with result "
                f"{self._index[e].result!r}"
            )
        stored = self._table.template(template.id)
        if stored != template:
            raise TableError(
                f"template {template.id!r} differs from the one the table was "
                f"built under; likelihoods read under a changed design would be "
                f"confidently wrong"
            )
        observation = Observation(experiment=e, template=template, result=result)
        self._observations.append(observation)
        self._index[e] = observation
        return observation

    # -- the protocol ------------------------------------------------------

    def estimate(self, h: HypothesisId, e: ExperimentId) -> LikelihoodEstimate:
        """Return the full likelihood estimate of experiment ``e`` under ``h``."""
        observation = self.observation(e)
        return self._table.estimate(
            self.program_edit(h), observation.template, observation.result
        )

    def log_likelihood(
        self, h: HypothesisId, e: ExperimentId, result: DiagnosticVector
    ) -> tuple[float, float]:
        """Return ``(log-likelihood, Monte Carlo standard error)`` in nats.

        ``result`` must agree with what was recorded for ``e``. The argument is
        in SPEC §3.5's signature and is honoured rather than ignored, but an
        experiment has one result, and accepting a second silently would let a
        caller evaluate a hypothesis against data that was never observed.
        """
        observation = self.observation(e)
        if tuple(result) != observation.result:
            raise InferenceError(
                f"experiment {e!r} was recorded with result {observation.result!r}, "
                f"not {tuple(result)!r}"
            )
        return self._table.estimate(
            self.program_edit(h), observation.template, observation.result
        ).as_tuple()

    def log_prior(self, h: HypothesisId) -> float:
        """Return the unnormalised structural log-prior of ``h``, in nats.

        SPEC §0's parsimony prior is ``p(D) proportional to 2 ** -L(D)``, so this
        is ``-L(D) ln 2``. It is deliberately *unnormalised*: the normalising
        constant is common to every hypothesis and cancels in
        :meth:`posterior`, which is what lets a hypothesis be admitted
        mid-investigation without the engine having to re-derive anyone else's
        prior (SPEC §6.2 A10).
        """
        return -self._grammar.code_length(self.program_edit(h)) * _LN2

    def log_likelihood_total(self, h: HypothesisId) -> float:
        """Return the summed log-likelihood of every recorded experiment under ``h``.

        The evidence a hypothesis has accumulated, with no prior in it. Published
        separately from :meth:`posterior` because SPEC §0 makes the structural
        prior a statement of belief about parsimony that is deliberately *not*
        matched to how often each defect appears in a benchmark. A calibration
        study over a benchmark therefore has to be able to see the likelihood on
        its own; see acceptance test A8, which does exactly that.

        Guarantees reproducibility: experiments are summed in the order they were
        recorded, through :func:`math.fsum`.
        """
        defect = self.program_edit(h)
        return math.fsum(
            self._table.estimate(
                defect, observation.template, observation.result
            ).log_likelihood
            for observation in self._observations
        )

    def log_posterior_weights(self) -> Mapping[HypothesisId, float]:
        """Return each live hypothesis's unnormalised log-posterior, in nats."""
        return {
            node_id: math.fsum(
                (self.log_prior(node_id), self.log_likelihood_total(node_id))
            )
            for node_id in self.live
        }

    def posterior(self) -> Mapping[HypothesisId, Probability]:
        """Return the posterior over every hypothesis the engine holds.

        Rejected hypotheses receive exactly zero and the rest are normalised over
        themselves. Rejection is an evidential judgement and belongs here, in the
        posterior; the structural prior does not renormalise away from a rejected
        hypothesis, because the prior is a statement about structure alone.

        Guarantees the result is bit-reproducible: hypotheses are visited in
        sorted order, experiments in the order they were recorded, and every sum
        goes through :func:`math.fsum`.
        """
        weights = self.log_posterior_weights()
        if not weights:
            return {node_id: Probability(0.0) for node_id in self.hypotheses}
        ceiling = max(weights[node_id] for node_id in sorted(weights))
        shifted = {
            node_id: math.exp(weights[node_id] - ceiling) for node_id in sorted(weights)
        }
        total = math.fsum(shifted[node_id] for node_id in sorted(shifted))
        return {
            node_id: Probability(shifted.get(node_id, 0.0) / total)
            for node_id in self.hypotheses
        }

    def ensure_structure(self, defect: Defect) -> int:
        """Fill ``defect``'s table row if it is missing; return simulator calls.

        Registers nothing and changes no belief, so it is safe to call for a
        structure that may never become a hypothesis.

        It exists because :meth:`expand` cannot be the only way to fill a row. A
        hypothesis needs a refutable prediction before a graph will admit it, a
        table-derived prediction needs the structure's row, and that row is what
        ``expand`` would have filled -- so deriving the prediction and admitting
        the hypothesis cannot both wait for the other.

        Raises :class:`~sciagent.core.errors.TableError` if the row is missing
        and this engine holds no simulator, rather than returning a row of zeros
        that would read as "this structure never produces anything".
        """
        if self._table.holds(defect):
            return 0
        if self._simulate is None:
            raise TableError(
                f"structure {structure_key(defect)!r} is not in the table and this "
                f"engine was built without a simulator, so its row cannot be filled"
            )
        self._table, calls = self._table.with_structure(defect, self._simulate)
        return calls

    def expand(self, h: HypothesisNode) -> ExpansionCost:
        """Admit a hypothesis mid-investigation and report what it cost.

        Guarantees the admitted hypothesis is evaluated against every experiment
        already recorded, on exactly the same footing as one present from the
        start: its table row is a function of the structure and the table seed
        alone, so it is identical to the row a from-scratch build would have
        produced, and its prior is its own code length (SPEC F9, §6.2 A10-A11).

        ``simulator_calls`` counts calls to the injected simulator, which is zero
        when the table already covers the structure.
        """
        if h.id in self._hypotheses:
            raise DuplicateHypothesisError(
                f"hypothesis {h.id!r} is already held by this engine"
            )
        if h.program_edit is None:
            raise InferenceError(
                f"hypothesis {h.id!r} has no compiled edit set, so it has neither "
                f"a structural prior nor anything to simulate"
            )
        calls = self.ensure_structure(h.program_edit)
        self._hypotheses[h.id] = _Hypothesis(
            program_edit=h.program_edit, status=h.status
        )
        return ExpansionCost(
            simulator_calls=calls, experiments_reevaluated=len(self._observations)
        )

    def ppc(self) -> PPCResult:
        """Return a posterior predictive check over every recorded experiment.

        Delegates to :func:`sciagent.inference.ppc.posterior_predictive_check`;
        see there for what the p-value means and why it is conservative.
        """
        posterior = self.posterior()
        predictive: list[tuple[ExperimentId, tuple[float, ...], int]] = []
        for observation in self._observations:
            template = observation.template
            cells = template.outcome.n_cells
            resolved = {
                node_id: self._table.resolved_probabilities(
                    self._hypotheses[node_id].program_edit, template.id
                )
                for node_id in self.live
            }
            mixture = [
                math.fsum(
                    posterior[node_id] * resolved[node_id][cell]
                    for node_id in self.live
                )
                for cell in range(cells)
            ]
            predictive.append(
                (
                    observation.experiment,
                    tuple(mixture),
                    template.outcome.cell_of(observation.result),
                )
            )
        return posterior_predictive_check(predictive, alpha=self._alpha)


#: ``ExperimentTemplate`` is re-exported so that a caller building a table does
#: not also have to import the interface module for the one type it needs.
__all__ = [
    "EmpiricalTable",
    "EmpiricalTableEngine",
    "ExperimentTemplate",
    "replicate_seed",
    "structure_key",
]

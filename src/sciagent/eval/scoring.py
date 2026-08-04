"""Scoring a diagnosis (SPEC §8).

Two scores, for two kinds of task. :func:`closed_world_score` is the proper score
over a closed hypothesis set that S1-S10 are read under. :func:`dimension_vector`
is §8's six-dimensional vector, which is what an out-of-library or compound
scenario needs -- there, the question is not "which of these five" but "how good
is the structure this system invented", and a proper score over a set the truth
is not in answers it with zero however good the answer was.

**Never collapsed into one number.** §8 is explicit, and the reason is R7: a
structurally wrong but interventionally equivalent explanation is a legitimate
scientific success, and any scalarisation would have to decide in advance how
much of D1 a unit of D3 is worth. :class:`DimensionVector` therefore has six
fields and no ``total``. Which one is primary is the task's business --
closed-world reads the proper score, out-of-library reads D3 and D2 with D1
secondary, compound reads D1 -- and :func:`primary_dimension` records that
mapping so a report can say which dimension a headline figure is.

Nothing here is reachable from a research system. Every function takes the truth,
which no ``Investigation`` exposes, so these are harness-side by construction
rather than by discipline.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sciagent.core.edits import Defect, EditGrammar
from sciagent.core.errors import DiagnosisError
from sciagent.core.types import (
    Diagnosis,
    ExperimentTemplateId,
    HypothesisId,
    Probability,
)
from sciagent.eval.scenarios import ScenarioClass
from sciagent.experiments.boed import Predictive, expected_information_gain
from sciagent.experiments.dsl import ExperimentDesign, defect_key
from sciagent.inference.empirical import EmpiricalTable
from sciagent.inference.interface import Observation, Simulator

__all__ = [
    "ClosedWorldScore",
    "DimensionVector",
    "closed_world_score",
    "dimension_vector",
    "jensen_shannon_bits",
    "leading_structure",
    "primary_dimension",
]


@dataclass(frozen=True, slots=True)
class ClosedWorldScore:
    """A proper score over a closed hypothesis set, and its parts.

    ``log_score`` is ``log2 p(truth)``: zero when the truth is held with
    certainty and increasingly negative as mass moves away from it. Proper, so a
    system cannot improve it by reporting anything other than its actual belief,
    which is the property that makes it safe to compare systems on.
    """

    truth_mass: Probability
    """Posterior mass the system put on the true structure."""

    log_score: float
    """``log2`` of :attr:`truth_mass`, in bits. ``-inf`` if the truth got zero."""

    leading_mass: Probability
    """Mass on whichever hypothesis led, true or not."""

    correct: bool
    """Whether the leading hypothesis is the true one."""

    identified: bool
    """Whether the truth led *and* carried more than half the mass.

    Stricter than :attr:`correct`, and the honest reading of "recovered the
    diagnosis" when the alternative is a three-way split whose winner leads by a
    rounding error.
    """


def closed_world_score(
    diagnosis: Diagnosis,
    truth: Defect,
    program_edit: Mapping[HypothesisId, Defect],
) -> ClosedWorldScore:
    """Score a diagnosis against the structure the environment actually had.

    Matching is by :func:`~sciagent.experiments.dsl.defect_key`, so a system that
    proposed the true structure under its own name is credited for it -- which is
    the whole point of allowing systems to propose. Mass on several hypotheses
    holding the same structure is summed.

    Guarantees the score is a pure function of its arguments. Raises
    :class:`~sciagent.core.errors.DiagnosisError` if the distribution names a
    hypothesis ``program_edit`` does not, since a mass on an unidentifiable
    structure could otherwise be silently scored as zero.
    """
    wanted = defect_key(truth)
    truth_mass = 0.0
    leading_mass = 0.0
    leader: HypothesisId | None = None
    for node_id in sorted(diagnosis.distribution):
        mass = float(diagnosis.distribution[node_id])
        if node_id not in program_edit:
            raise DiagnosisError(
                f"diagnosis puts mass {mass!r} on hypothesis {node_id!r}, whose "
                f"structure is unknown to the scorer; it knows "
                f"{sorted(program_edit)!r}"
            )
        if defect_key(program_edit[node_id]) == wanted:
            truth_mass += mass
        if mass > leading_mass:
            leading_mass, leader = mass, node_id
    correct = leader is not None and defect_key(program_edit[leader]) == wanted
    return ClosedWorldScore(
        truth_mass=Probability(truth_mass),
        log_score=math.log2(truth_mass) if truth_mass > 0.0 else -math.inf,
        leading_mass=Probability(leading_mass),
        correct=correct,
        identified=correct and truth_mass > 0.5,
    )


# --------------------------------------------------------------------------
# SPEC §8's six dimensions
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DimensionVector:
    """SPEC §8's six dimensions, reported separately and never combined.

    There is deliberately no ``total`` and no weighting. R7 asks whether
    interventional equivalence without structural recovery is common, and any
    scalarisation would answer it by fiat: it would have to decide in advance how
    many edits of D1 a bit of D3 is worth, which is the question itself.
    """

    d1_structural_distance: float
    """``grammar.distance(candidate, truth)``, in units of one edit. Lower is
    better; ``0`` is exact structural recovery."""

    d2_held_out_predictive: float
    """Mean log2 probability the candidate assigns to the outcome the truth most
    often produces, on designs the investigation never ran. Higher is better;
    ``-inf`` if the candidate ruled out something that happens."""

    d3_intervention_similarity: float
    """One minus the mean Jensen-Shannon divergence between the candidate's and
    the truth's outcome distributions over the held-out battery. In ``[0, 1]``;
    ``1`` is interventional indistinguishability.

    §8 makes this primary for out-of-library scenarios, and it is the dimension
    SPEC §9's preregistered contrast is stated on."""

    d4_explanatory_coverage: float
    """Total log2-likelihood improvement the candidate offers over the best
    hypothesis already entertained, summed across recorded experiments where it
    does better. Non-negative by construction: §8 asks about *improvement* on
    poorly-explained results, so an experiment the existing set already explains
    contributes nothing rather than a penalty."""

    d5_enabled_experiment_value: float
    """Expected information gain, in bits, of the best experiment the candidate
    makes available -- computed on a belief that includes it. Measures what the
    proposal opens up rather than what it explains."""

    d6_complexity: float
    """``grammar.code_length(candidate)``, in bits. Grammar-relative, so any
    reported figure must say which grammar produced it."""

    n_held_out: int
    """How many designs D2 and D3 were averaged over. Zero means the battery was
    empty and both are ``nan``, rather than a silent ``0`` that would read as a
    measurement."""


#: Which dimension a task's headline figure is, per SPEC §8's "primary
#: interpretation by task". The closed-world classes map to ``None``: §8 says
#: D1-D6 are not applicable to them and they are read under
#: :func:`closed_world_score` instead.
_PRIMARY: Mapping[ScenarioClass, str | None] = {
    "single": None,
    "confounded": None,
    "null": None,
    "non_identifiable": None,
    "garden_path": None,
    "out_of_library": "d3_intervention_similarity",
    "compound": "d1_structural_distance",
}


def primary_dimension(scenario_class: ScenarioClass) -> str | None:
    """Return the dimension a scenario of this class is primarily read on.

    Out-of-library is D3 -- §8 also makes D2 primary and D1 secondary there, and
    the field named here is the one a headline figure cites. Compound is D1,
    since decomposition accuracy is the capability that scenario tests.
    """
    return _PRIMARY[scenario_class]


def jensen_shannon_bits(left: Sequence[float], right: Sequence[float]) -> float:
    """Return the Jensen-Shannon divergence between two distributions, in bits.

    Chosen over Kullback-Leibler for two properties this use needs. It is
    symmetric, so "how far is the candidate from the truth" does not depend on
    which is named first. And it is bounded in ``[0, 1]`` bits, which is what
    lets divergences over several designs be averaged into a number that means
    something -- an unbounded divergence would let one design where the candidate
    assigns near-zero to a frequent outcome dominate the whole battery.

    Guarantees a result in ``[0, 1]``, and exactly ``0`` for identical inputs.
    Summation is by :func:`math.fsum` over the cells in order.
    """
    if len(left) != len(right):
        raise DiagnosisError(
            f"cannot compare distributions over {len(left)} and {len(right)} cells"
        )
    if not left:
        raise DiagnosisError("cannot compare distributions over no cells")
    mixture = [0.5 * (a + b) for a, b in zip(left, right, strict=True)]
    divergence = _entropy_bits(mixture) - 0.5 * (
        _entropy_bits(left) + _entropy_bits(right)
    )
    return min(1.0, max(0.0, divergence))


def _entropy_bits(values: Sequence[float]) -> float:
    """Return the Shannon entropy of a distribution, in bits, skipping zeros."""
    return -math.fsum(value * math.log2(value) for value in values if value > 0.0)


def leading_structure(
    diagnosis: Diagnosis, program_edit: Mapping[HypothesisId, Defect]
) -> Defect:
    """Return the structure of whichever hypothesis carries the most mass.

    Ties break to the lexicographically first hypothesis id, so the answer never
    depends on a mapping's iteration order. Which id wins a tie is arbitrary;
    that it is the same id in every process is not.

    What a system is scored on is what it actually concluded -- its leading
    hypothesis, not its best one. Crediting a system for a structure it
    entertained and then rejected would be scoring the library rather than the
    investigation.
    """
    if not diagnosis.distribution:
        raise DiagnosisError("an empty diagnosis has no leading structure")
    leader = max(
        sorted(diagnosis.distribution),
        key=lambda node_id: diagnosis.distribution[node_id],
    )
    if leader not in program_edit:
        raise DiagnosisError(
            f"the leading hypothesis {leader!r} has no known structure; the "
            f"scorer knows {sorted(program_edit)!r}"
        )
    return program_edit[leader]


def dimension_vector(
    candidate: Defect,
    truth: Defect,
    *,
    grammar: EditGrammar,
    table: EmpiricalTable,
    simulate: Simulator,
    held_out: Sequence[ExperimentDesign],
    observations: Sequence[Observation] = (),
    entertained: Mapping[HypothesisId, Defect] | None = None,
    posterior: Mapping[HypothesisId, Probability] | None = None,
) -> tuple[DimensionVector, EmpiricalTable]:
    """Return SPEC §8's six dimensions for one candidate structure.

    Returns the grown table alongside the vector, because D2 and D3 need rows for
    the candidate and for the truth on every held-out design, and filling one
    costs a simulation. Threading it back lets a caller score many candidates
    against one battery without re-simulating -- the difference between a scoring
    pass that costs seconds and one that costs an hour.

    ``held_out`` must be designs the investigation did *not* run: §8 measures D2
    "on diagnostics unused during the investigation", and a design the system
    already saw would measure fit rather than prediction. Nothing here checks it,
    because this function cannot see what was run. The caller holds the history
    and it is the caller's to honour.

    ``grammar`` should be the **environment's**, not the agent's. On an
    out-of-library scenario the truth is by construction outside the agent's
    grammar, so a distance or a code length computed under the agent's would
    raise on exactly the scenario this vector exists for -- the same reason
    :attr:`~sciagent.eval.campaign.ScenarioRun.structural_distance` uses
    ``Executor.grammar``.

    Guarantees every figure is derived here, from the truth and the table. A
    research system cannot influence one: the truth is not on the surface an
    ``Investigation`` exposes, and this function is never called from inside one.
    """
    grown = table
    for defect in (candidate, truth):
        if not grown.holds(defect):
            grown, _ = grown.with_structure(defect, simulate)
    known = dict(entertained or {})
    belief = dict(posterior or {})
    predictive, similarity = _held_out_dimensions(candidate, truth, grown, held_out)
    return (
        DimensionVector(
            d1_structural_distance=grammar.distance(candidate, truth),
            d2_held_out_predictive=predictive,
            d3_intervention_similarity=similarity,
            d4_explanatory_coverage=_explanatory_coverage(
                candidate, grown, observations, known
            ),
            d5_enabled_experiment_value=_enabled_value(
                candidate, grown, held_out, known, belief
            ),
            d6_complexity=grammar.code_length(candidate),
            n_held_out=len(held_out),
        ),
        grown,
    )


def _held_out_dimensions(
    candidate: Defect,
    truth: Defect,
    table: EmpiricalTable,
    held_out: Sequence[ExperimentDesign],
) -> tuple[float, float]:
    """Return D2 and D3, which share the held-out battery and its table rows.

    D2 reads the candidate's predictive at the truth's *modal* cell -- the
    outcome the world most often actually produces -- while D3 compares the whole
    distributions. The two answer different questions: "would this candidate have
    predicted what happens" versus "does it respond to intervention the way the
    truth does". §8 keeps them apart for that reason, and R7 is the reason it
    matters: a structure can be wrong on the first and right on the second.

    Both read ``resolved_probabilities``, which floors an unreached cell at the
    rule-of-three bound, so neither figure treats a cell the simulation budget
    simply never visited as impossible.
    """
    if not held_out:
        return math.nan, math.nan
    predictive: list[float] = []
    similarity: list[float] = []
    for design in held_out:
        mine = table.resolved_probabilities(candidate, design.id)
        theirs = table.resolved_probabilities(truth, design.id)
        modal = max(range(len(theirs)), key=lambda cell: (theirs[cell], -cell))
        predictive.append(math.log2(mine[modal]) if mine[modal] > 0.0 else -math.inf)
        similarity.append(1.0 - jensen_shannon_bits(mine, theirs))
    mean_predictive = (
        math.fsum(predictive) / len(predictive)
        if all(math.isfinite(value) for value in predictive)
        else -math.inf
    )
    return mean_predictive, math.fsum(similarity) / len(similarity)


def _explanatory_coverage(
    candidate: Defect,
    table: EmpiricalTable,
    observations: Sequence[Observation],
    entertained: Mapping[HypothesisId, Defect],
) -> float:
    """Return D4: how much better the candidate explains what was already seen.

    Per recorded experiment, the candidate's log-likelihood against the best any
    entertained hypothesis achieved, converted to bits. Only positive differences
    are summed, because §8 asks for "likelihood improvement on previously
    poorly-explained registered results": an experiment the existing set already
    explains well is not a result the candidate was supposed to rescue.
    """
    scored = [
        entertained[node_id]
        for node_id in sorted(entertained)
        if table.holds(entertained[node_id])
    ]
    if not observations or not scored:
        return 0.0
    total = 0.0
    for observation in observations:
        best = max(
            table.estimate(
                defect, observation.template, observation.result
            ).log_likelihood
            for defect in scored
        )
        mine = table.estimate(
            candidate, observation.template, observation.result
        ).log_likelihood
        total += max(0.0, (mine - best) / math.log(2.0))
    return total


def _enabled_value(
    candidate: Defect,
    table: EmpiricalTable,
    designs: Sequence[ExperimentDesign],
    entertained: Mapping[HypothesisId, Defect],
    posterior: Mapping[HypothesisId, Probability],
) -> float:
    """Return D5: the best expected information gain the candidate makes available.

    The belief is the reported posterior scaled down by one share, with that
    share given to the candidate -- the belief a system would hold *had* it
    proposed this, keeping the relative weights of everything already
    entertained. So the figure is what the proposal opens up, not what was
    already there to be learned.

    ``0.0`` when no design can be evaluated, which is a statement that nothing
    was enabled rather than a missing measurement. That is the opposite of D2 and
    D3's ``nan`` on an empty battery, where the question was never asked.
    """
    usable = [
        design
        for design in designs
        if table.holds(candidate)
        and all(
            table.holds(entertained[node_id])
            for node_id in sorted(entertained)
            if node_id in posterior
        )
    ]
    if not usable:
        return 0.0
    admitted = HypothesisId("__candidate__")
    structures: dict[HypothesisId, Defect] = {
        node_id: entertained[node_id]
        for node_id in sorted(entertained)
        if node_id in posterior and table.holds(entertained[node_id])
    }
    structures[admitted] = candidate
    share = 1.0 / len(structures)
    belief: dict[HypothesisId, Probability] = {
        node_id: Probability((1.0 - share) * float(posterior.get(node_id, 0.0)))
        for node_id in structures
        if node_id != admitted
    }
    belief[admitted] = Probability(share)
    total = math.fsum(belief[node_id] for node_id in sorted(belief))
    if total <= 0.0:
        return 0.0
    belief = {node_id: Probability(value / total) for node_id, value in belief.items()}

    def predict(hypothesis: HypothesisId, template: ExperimentTemplateId) -> Predictive:
        return Predictive(
            cells=table.probabilities(structures[hypothesis], template),
            samples=float(table.replicates),
        )

    return max(
        expected_information_gain(design.id, belief, predict).bits for design in usable
    )

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
from typing import Final

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
    "DIMENSION_VERSION",
    "ClosedWorldScore",
    "DimensionVector",
    "closed_world_score",
    "dimension_vector",
    "jensen_shannon_bits",
    "leading_structure",
    "primary_dimension",
]

#: Which reading of §8's six dimensions a recorded cell was scored under. In
#: every cell address, so a campaign scored under one reading cannot be pooled
#: with, or silently resumed from, one scored under another.
#:
#: **This is the eval layer's own version, and it exists because no other term
#: moves when a dimension changes.** A cell's address carries ``env_version``,
#: ``data_version`` and ``metric_version``; the first two describe the
#: environment and its data, and the third is a content hash over the
#: *environment's diagnostic catalogue*. D1-D6 are computed here, from the truth
#: and the table, and appear in none of them -- so before this constant a change
#: to a dimension moved nothing, and
#: :func:`~sciagent.eval.matrix.run_matrix`'s ``skip_recorded`` default would
#: report the stale reading as the new campaign's without executing anything to
#: disagree with it.
#:
#: Bumping ``METRIC_VERSION`` instead was the obvious alternative and is wrong,
#: measured rather than argued: the metric version reaches every
#: :class:`~sciagent.inference.binning.Discretisation`'s content hash through
#: ``str(MetricRef)``, so it addresses the empirical tables too. A dimension
#: change would then invalidate every cached table and force a 3m11s rebuild to
#: reproduce bit-identical rows, on every machine and in every worktree, for a
#: change that touches no estimator.
#:
#: Carried inside the cell key's ``config`` rather than as a column on
#: :class:`~sciagent.eval.matrix.CampaignAddress`, exactly as
#: :data:`~sciagent.eval.matrix.MATRIX_VERSION` is -- a version the eval layer
#: owns, in the address, with no ledger schema change.
#:
#: ``spec8/2`` is the A26 reading: D2 proper rather than modal, D4 excluding the
#: candidate's own structure. ``spec8/1`` was never written down, and is what
#: every row recorded before 2026-08-19 was scored under.
#:
#: ``spec8/3`` is A29: :meth:`~sciagent.eval.matrix.CellReading.as_payload`
#: gained ``probe_p_value`` and ``probe_inadequate``, so a row recorded under
#: ``spec8/2`` cannot answer a question about the Stage A probe and a reader
#: folding the two generations together would summarise a detection rate over
#: whichever rows happened to carry the key. D1-D6 are unchanged and no cached
#: table moves, which is exactly why this is the term that bumps and
#: ``METRIC_VERSION`` is not -- ``docs/DECISIONS.md`` (2026-08-19) names A29
#: among the changes that must not reach it. No recorded row is stranded by the
#: bump: the 1,120 rows of the frozen campaign carry no ``dimensions`` key at
#: all and :func:`~sciagent.eval.report._at_address` already excludes them.
#:
#: ``spec8/4`` is A31, and bumps for A29's reason rather than for a new one:
#: :meth:`~sciagent.eval.matrix.CellReading.as_payload` gained
#: ``autonomy_fraction``, ``entertained``, ``escalated``, ``null_mass``,
#: ``abstain_mass`` and ``max_defect_mass``, so a ``spec8/3`` row cannot answer a
#: question about §12 criterion 9 or 11 and a reader pooling the two generations
#: would summarise agency over whichever rows happened to carry the key. D1-D6
#: are again unchanged and no cached table moves. What the bump costs is
#: precisely nothing in recompute: the fields are read off a
#: :class:`~sciagent.eval.campaign.ScenarioRun` that already existed, and every
#: estimator is untouched.
DIMENSION_VERSION: Final = "spec8/4"


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
    """Mean expected log2 probability the candidate assigns to the truth's
    outcomes, on designs the investigation never ran. Higher is better; ``-inf``
    if the candidate ruled out something that happens.

    A **proper** score: over all distributions on the same cells it is maximised
    by the truth, so hedging correctly cannot lose to overconfidence. Until A26
    it read the truth's modal cell alone, which a point mass on that cell
    maximised."""

    d3_intervention_similarity: float
    """One minus the mean Jensen-Shannon divergence between the candidate's and
    the truth's outcome distributions over the held-out battery. In ``[0, 1]``;
    ``1`` is interventional indistinguishability.

    §8 makes this primary for out-of-library scenarios, and it is the dimension
    SPEC §9's preregistered contrast is stated on."""

    d4_explanatory_coverage: float
    """Total log2-likelihood improvement the candidate offers over the best
    *other* hypothesis already entertained, summed across recorded experiments
    where it does better. Non-negative by construction: §8 asks about
    *improvement* on poorly-explained results, so an experiment the existing set
    already explains contributes nothing rather than a penalty.

    The candidate's own structure is excluded from the comparison set, which A26
    added and without which this is identically zero -- see
    :func:`_explanatory_coverage`. The clipping is per experiment, so an
    experiment the candidate loses cannot cancel one it wins."""

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


def _predictive_log_score(mine: Sequence[float], theirs: Sequence[float]) -> float:
    """Return D2's per-design term: the expected log2 probability under ``theirs``.

    Guarantees propriety. By Gibbs' inequality the value is maximised, over every
    distribution on the same cells, exactly when ``mine`` equals ``theirs`` -- so
    a candidate cannot improve its score by being more confident than the truth
    warrants. That is the property the dimension exists to have and the reading
    this replaced did not: D2 was ``log2 mine[modal]`` at the truth's most
    frequent cell alone, which a point mass on that cell maximises. See A26.

    ``-inf`` when the candidate rules out a cell the truth reaches, which is what
    the modal reading got right and is kept. A cell the truth never reaches
    contributes nothing whatever the candidate says about it -- the ``0 log 0 =
    0`` convention, not a special case.

    Summation is by :func:`math.fsum` over the cells in order.
    """
    if len(mine) != len(theirs):
        raise DiagnosisError(
            f"cannot score a distribution over {len(mine)} cells against one "
            f"over {len(theirs)}"
        )
    if not mine:
        raise DiagnosisError("cannot score a distribution over no cells")
    terms: list[float] = []
    for weight, probability in zip(theirs, mine, strict=True):
        if weight <= 0.0:
            continue
        if probability <= 0.0:
            return -math.inf
        terms.append(weight * math.log2(probability))
    return math.fsum(terms)


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

    ``held_out`` is the battery D2, D3 **and D5** are defined over -- three
    dimensions, not the two the first two names suggest, since
    :func:`_enabled_value` reads it as the set of questions a candidate could
    still be asked.

    §8 words it as "diagnostics unused during the investigation", and **that is
    no longer a promise this function's caller makes.** Gate A27 replaced the
    per-run derivation -- every offered design the arm did not run -- with a
    battery declared on the scenario, because the derivation made the question
    set a function of what the arm chose and graded B1 on an empty battery. So a
    design the system ran *can* be in here. Nothing checks it and nothing is
    meant to: see :attr:`~sciagent.eval.scenarios.Scenario.held_out` for why the
    weaker guarantee is the better instrument, and what it costs.

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

    D2 is the candidate's expected log2 probability under the truth's outcome
    distribution -- a proper score, so the truth maximises it -- while D3
    compares the two distributions symmetrically. The two answer different
    questions: "would this candidate have predicted what happens" versus "does it
    respond to intervention the way the truth does". §8 keeps them apart for that
    reason, and R7 is the reason it matters: a structure can be wrong on the
    first and right on the second.

    D2 read ``log2 mine[modal]`` at the truth's single most frequent cell until
    A26. That was improper -- a point mass there beat the truth itself -- so the
    dimension paid for overconfidence. Both readings are ``-inf`` on a candidate
    that rules out something that happens, and the mean propagates that rather
    than averaging it away.

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
        predictive.append(_predictive_log_score(mine, theirs))
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
    *other* entertained hypothesis achieved, converted to bits. Only positive
    differences are summed, because §8 asks for "likelihood improvement on
    previously poorly-explained registered results": an experiment the existing
    set already explains well is not a result the candidate was supposed to
    rescue.

    **The candidate's own structure is excluded from the comparison set**, and
    that word "other" is the whole of A26. :func:`~sciagent.eval.matrix.reading_of`
    scores a run's *leading* structure and passes the run's whole edit map as
    ``entertained``, so the candidate is always a member; comparing it with
    itself makes ``best >= mine`` hold on every observation and
    ``max(0.0, mine - best)`` identically zero. Every one of the 1,120 rows of
    the recorded campaign reads ``d4 == 0`` for that reason -- a constant
    reported as a comparison.

    Exclusion is by structure rather than by hypothesis id. A :data:`Defect` is a
    ``frozenset`` of edits, so equality is exact and independent of iteration
    order, and two ids carrying one structure are one structure: a candidate
    entertained twice has still rescued nothing.

    Returns ``0.0`` when the exclusion leaves nothing to compare against. There
    is no alternative the candidate improves on, which is a coverage of zero and
    not a missing measurement.
    """
    scored = [
        entertained[node_id]
        for node_id in sorted(entertained)
        if entertained[node_id] != candidate and table.holds(entertained[node_id])
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
    # A guard on the belief, not a filter on the designs: every design is usable
    # or none is, since what it turns on is whether the table holds a row for the
    # candidate and for each hypothesis the belief prices. Written as a filter
    # once, which read as though it varied per design and re-derived the same
    # answer for each one.
    scorable = table.holds(candidate) and all(
        table.holds(entertained[node_id])
        for node_id in sorted(entertained)
        if node_id in posterior
    )
    if not scorable or not designs:
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
        expected_information_gain(design.id, belief, predict).bits for design in designs
    )

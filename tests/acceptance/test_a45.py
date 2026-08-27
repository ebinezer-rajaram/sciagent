"""Acceptance test A45: criterion 4 is a bar something can fail.

A45 is a post-freeze gate. SPEC §6 stops at A24, so the criterion is stated in
``docs/BACKLOG.md`` under *"Criterion 4 is now unfailable, and needs an absolute
bar or none at all"*, and reads:

    ``test_a45_criterion_four_is_falsifiable`` -- there exists a probe rate
    vector over S1-S11 that the criterion rejects. A criterion no input can fail
    is what this entry exists to remove, so the gate is a demonstration that some
    input fails it.

What was wrong
--------------

C1 re-specified §12 criterion 4 as two comparisons of V7's Stage A probe rate
against B1's. Gate A29 then made the probe **arm-symmetric** -- it is evaluated
by the harness before ``investigate`` is called, so its verdict is a function of
the scenario and the seed alone. That is what A29's gate asked for and it removed
the confound C1 diagnosed. It also removed the variance the criterion was
reading: ``replicate_seeds`` pairs every arm on one seed sequence, so V7's rate
and B1's are bit-identical on every scenario and **neither clause can fail**.

The decision this gate encodes
------------------------------

Taken cold on 2026-08-26 and recorded in ``docs/DECISIONS.md``: criterion 4 is
**moved from §12's Capability block to its Infrastructure block** and reworded as
an absolute bar, rather than struck. The move is the substance: §12's Capability
heading reads *"(V7 versus baselines, 20 seeds, S1-S12)"*, and after A29
criterion 4 cannot be a V7-versus-baseline comparison of anything. Criterion 4
keeps its number, so nothing else renumbers.

**What this cannot buy, because the obvious expectation is wrong.** It does not
restore V7-versus-B1 grading, and no threshold could: the probe is computed
before ``investigate``, so its value is identical for every arm by construction.
Criterion 4 grades the apparatus, and a capability criterion on S11 detection
would have to be built on some other instrument.

Revised at gate A47 (2026-08-27)
--------------------------------

A45's bar implemented "does not fire" as exactly zero -- deliberately, because
choosing a threshold was a decision nobody had taken. The completed campaign
then took the measurement: a correctly calibrated probe cleared exactly-zero
about once in 1,583 campaigns, and the recorded campaign failed it at a pooled
rate *below* the instrument's A9-measured size. The decision A47 encodes moved
the size clause to the **pooled** quiet set against ``SIZE_TOLERANCE`` (0.045),
and the input became per-scenario **counts**, because pooled rates cannot be
recovered from per-scenario rates once draw counts differ.

This file holds A45's own claim under the new form: the criterion is a bar that
some input fails and some input passes, evaluated over all nine named scenarios
or not at all. The per-scenario exactly-zero pins this file used to carry were
that wording's, not this claim's, and ``test_a47.py`` now owns the boundary --
at, below and above the tolerance, and the draws-weighted pooling.

Which scenarios the criterion names, and which it does not
----------------------------------------------------------

Fires on S11; pooled size over S1-S7 and S9. **S8, S10 and S12 are deliberately
unconstrained**, and that is not an oversight to be tidied up by a stricter
implementation. Per SPEC §4.5, S8 is compound (two edits, so a single-edit space
can be genuinely strained), S10 is non-identifiable by construction with a budget
below the discriminating threshold, and S12 carries a censoring nuisance that
produces a strong spurious periodic signature. A probe firing on any of those is
not obviously wrong, so the criterion says nothing about them. The quiet set is
exactly the scenarios where the space is both adequate and identifiable.

A criterion nothing can satisfy is as useless as one nothing can fail, so the
acceptance case is here beside the rejection cases and neither stands alone.
"""

from __future__ import annotations

from typing import cast

import pytest

from sciagent.core.errors import MalformedDesignError
from sciagent.core.types import ScenarioId
from sciagent.eval.report import CriterionFour, ProbeCount, criterion_four

#: The eight scenarios the criterion requires the probe to stay quiet on, spelled
#: out rather than derived. ``range(1, 8)`` plus S9 is the natural way to write
#: this and the natural way to get it wrong, because the set is **not
#: contiguous** -- S8 sits inside the span and is not a member. A test that
#: imported the implementation's own constant would agree with that mistake, so
#: this list is written from SPEC §12 criterion 4's text and from nothing else.
_QUIET = (
    ScenarioId("S1"),
    ScenarioId("S2"),
    ScenarioId("S3"),
    ScenarioId("S4"),
    ScenarioId("S5"),
    ScenarioId("S6"),
    ScenarioId("S7"),
    ScenarioId("S9"),
)

#: SPEC §9's replicate count: twenty draws a scenario, so the quiet set is 160.
_DRAWS = 20


def _counts(**overrides: tuple[int, int]) -> dict[ScenarioId, ProbeCount]:
    """Return a probe-count vector over S1-S12, perturbed by ``overrides``.

    The unperturbed vector is the discriminating instrument the slice measured:
    quiet everywhere, firing on S11. Each case below changes one entry, so what a
    test pins is the effect of that entry and not of a whole hand-built vector.
    """
    counts = {
        ScenarioId(f"S{index}"): ProbeCount(fired=0, draws=_DRAWS)
        for index in range(1, 13)
    }
    counts[ScenarioId("S11")] = ProbeCount(fired=_DRAWS, draws=_DRAWS)
    for name, (fired, draws) in overrides.items():
        counts[ScenarioId(name)] = ProbeCount(fired=fired, draws=draws)
    return counts


class TestA45CriterionFour:
    """§12 criterion 4 is a bar, and some probe count vector fails it."""

    def test_a45_criterion_four_is_falsifiable(self) -> None:
        """The gate: a probe blind to S11 is rejected.

        The criterion's power clause, and half of what the gate asks for. The
        other half -- that a probe firing beyond its size is also rejected --
        is the sibling test below rather than a second block here, because a
        failure in this block would short-circuit it and leave assertions that
        were never watched failing.

        ``false_positives`` is empty here and that is asserted rather than
        ignored: a verdict whose explanatory field contradicts its own stated
        reason is not usable even when its boolean is right. This vector fires
        nowhere, so nothing about it is a false positive.
        """
        blind = criterion_four(_counts(S11=(0, _DRAWS)))
        assert not blind.holds
        assert not blind.fired_on_s11
        assert blind.false_positives == ()

    def test_a45_a_probe_firing_beyond_its_size_is_rejected(self) -> None:
        """The size clause, on **every** scenario the criterion names.

        One representative scenario is not enough, and the reason is specific
        rather than a general preference for coverage: the quiet set is not
        contiguous. An implementation writing it as ``range(1, 8)`` plus nothing
        drops S9 -- the one member the criterion names separately -- and accepts a
        probe firing on every S9 replicate while passing a test that only ever
        perturbs S3. Looping over all eight is what closes that.

        Eight firings in twenty put the pooled rate at 8/160 = 0.05, above the
        tolerance whichever scenario carries them. The absence of any size term
        is what ``docs/OPEN-DECISIONS.md`` §1 called the original wording's
        clearest defect: it stated a power with no size, so an arm firing on
        all twelve scenarios would have passed.
        """
        for scenario in _QUIET:
            verdict = criterion_four(_counts(**{scenario: (8, _DRAWS)}))
            assert not verdict.holds, f"a probe at 0.05 pooled via {scenario} passed"
            assert verdict.fired_on_s11
            assert verdict.false_positives == (scenario,)

    def test_a45_a_firing_within_the_measured_size_is_reported_not_failed(
        self,
    ) -> None:
        """One firing in 160 draws holds, and is still named a false positive.

        The boundary this file used to pin -- *any* positive rate fails -- was
        A45's exactly-zero wording, revised at gate A47 after the measurement
        showed a calibrated probe failing it in all but one campaign in 1,583.
        What survives the revision is the reporting claim: a firing on an
        adequate space is a false positive whether or not the pooled rate
        tolerates it, and a verdict that stopped listing them would hide the
        one figure a reader needs beside a passing rate.
        """
        verdict = criterion_four(_counts(S6=(1, _DRAWS)))
        assert verdict.holds
        assert verdict.false_positives == (ScenarioId("S6"),)
        assert verdict.quiet_fired == 1

    def test_a45_the_criterion_accepts_a_discriminating_instrument(self) -> None:
        """The instrument the slice measured passes, so the bar is satisfiable.

        Without this the gate above is met by ``return False``, which fails on
        every input and is not a criterion. The pair is the claim: something
        fails it *and* something passes it.

        The firing clause is ``> 0`` rather than a rate threshold, which is the
        entry's own wording -- "fires on S11" -- and deliberately not
        strengthened here. Choosing a numeric power threshold is a further
        decision nobody has taken, and inventing one in a test would be taking
        it.
        """
        assert criterion_four(_counts()).holds
        assert criterion_four(_counts(S11=(1, _DRAWS))).holds

    def test_a45_the_unconstrained_scenarios_do_not_move_the_verdict(self) -> None:
        """S8, S10 and S12 say nothing about the criterion, in either direction.

        The natural over-strengthening is "quiet everywhere except S11", which
        this rejects. Those three scenarios are ones where a firing probe is not
        evidence of a bad instrument -- §4.5's compound, non-identifiable and
        garden-path cases -- so a criterion that failed on them would fail an
        instrument that is behaving correctly.
        """
        for scenario in ("S8", "S10", "S12"):
            verdict = criterion_four(_counts(**{scenario: (_DRAWS, _DRAWS)}))
            assert verdict.holds, f"{scenario} is not part of the criterion"
            assert verdict.false_positives == ()

    def test_a45_an_incomplete_vector_is_refused(self) -> None:
        """A missing scenario raises rather than defaulting to a verdict.

        Both defaults are wrong in the way this gate exists to prevent. Treating
        an absent scenario as zero draws shrinks the pooled denominator against
        an instrument nobody measured; treating it as "not applicable" passes
        the criterion by having nothing left to check, which is unfailability
        arriving by a second door. The message names the scenario, so a caller
        handed a partial report can tell which one.

        Every scenario the criterion names is deleted in turn, not a
        representative one. A criterion deriving its quiet set from whichever
        keys the caller happened to supply passes a two-key vector and is
        unfailable again by exactly the route this test exists to close.

        Matched with a trailing ``\\.`` rather than on the bare id, because these
        ids are prefixes of one another: a bare ``match="S1"`` is a regex search
        that a message naming **S11** also satisfies, so an implementation that
        always blamed S11 would pass the S1 iteration.
        """
        for scenario in (*_QUIET, ScenarioId("S11")):
            partial = _counts()
            del partial[scenario]
            with pytest.raises(MalformedDesignError, match=rf"missing {scenario}\."):
                criterion_four(partial)

    def test_a45_a_verdict_cannot_contradict_itself(self) -> None:
        """``holds`` may not disagree with the facts it is a conjunction of.

        The house discipline for this shape is written down and cited to the
        second invariant: :class:`~sciagent.eval.campaign.Adjudication` guards
        its four bare ints in ``__post_init__`` because "a frozen dataclass of
        four bare ints is exactly where a comment would otherwise have been the
        whole of it". This type takes the same guard, so the relation between
        the fields is enforced rather than merely described by the field
        docstrings.
        """
        with pytest.raises(MalformedDesignError, match="holds"):
            CriterionFour(
                holds=True,
                fired_on_s11=False,
                false_positives=(ScenarioId("S1"),),
                quiet_fired=1,
                quiet_draws=160,
            )
        with pytest.raises(MalformedDesignError, match="holds"):
            CriterionFour(
                holds=False,
                fired_on_s11=True,
                false_positives=(),
                quiet_fired=0,
                quiet_draws=160,
            )

    def test_a45_a_count_that_is_not_a_count_is_refused(self) -> None:
        """A fractional count raises rather than pooling as a rate.

        The successor of this file's rate-range guard, kept because the same
        defect survives the counts migration by one route ``mypy`` cannot
        close at runtime: a caller mechanically converting A45-era rates ends
        up with ``fired=0.5``, which passes the ``[0, draws]`` range check and
        would poison the pooled sum silently. Integrality is asserted by the
        value type itself, so no path carries a rate to the comparison.

        ``cast`` rather than a plain literal, because the guard under test is
        the *runtime* one: ``mypy`` already rejects the literal, and this test
        is about the caller ``mypy`` never saw.
        """
        with pytest.raises(MalformedDesignError, match="integer"):
            ProbeCount(fired=cast(int, 0.5), draws=_DRAWS)

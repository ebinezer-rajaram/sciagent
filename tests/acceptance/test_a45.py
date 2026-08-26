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

The instrument is not the problem. The probe fires on S11 and stays quiet on the
other eleven, measured 2026-08-16, and that discrimination is real. What was
missing is a *threshold*, and the reason there was not one is that C1 inherited
the comparative form from the wording it replaced, where the comparison was the
whole point.

The decision this gate encodes
------------------------------

Taken cold on 2026-08-26 and recorded in ``docs/DECISIONS.md``: criterion 4 is
**moved from §12's Capability block to its Infrastructure block** and reworded as
an absolute bar, rather than struck.

The move is the substance, not presentation. §12's Capability heading reads
*"(V7 versus baselines, 20 seeds, S1-S12)"*, and after A29 criterion 4 cannot be
a V7-versus-baseline comparison of anything -- so it does not belong under that
heading by the heading's own terms. That mismatch is what made the criterion
*read* as a claim about an agent when it is a claim about an instrument. Striking
it would have fixed the misreading by deleting a check that genuinely
discriminates; moving it fixes the misreading and keeps the check, beside §12's
other apparatus bars -- "A1-A24 passing", "zero imports", "100% reproducibility"
-- which are also all currently satisfied and are kept as regression bars anyway.

Criterion 4 keeps its number. Infrastructure becomes 1-4 and Capability becomes
5-9, so no other criterion is renumbered and the many ``criterion 8`` /
``criterion 10`` / ``criterion 11`` references through the code stay valid.

**What this cannot buy, because the obvious expectation is wrong.** It does not
restore V7-versus-B1 grading, and no threshold could: the probe is computed
before ``investigate``, so its value is identical for every arm by construction,
and an absolute bar is therefore the same pass or fail for B1, V1 and V7 forever.
Criterion 4 grades the apparatus under this wording, and a capability criterion
on S11 detection would have to be built on some other instrument.

Which scenarios the criterion names, and which it does not
----------------------------------------------------------

Fires on S11; quiet on S1-S7 and S9. **S8, S10 and S12 are deliberately
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

import math

import pytest

from sciagent.core.errors import MalformedDesignError
from sciagent.core.types import ScenarioId
from sciagent.eval.report import CriterionFour, criterion_four


def _rates(**overrides: float) -> dict[ScenarioId, float]:
    """Return a probe-rate vector over S1-S12, perturbed by ``overrides``.

    The unperturbed vector is the discriminating instrument the slice measured:
    quiet everywhere, firing on S11. Each case below changes one entry, so what a
    test pins is the effect of that entry and not of a whole hand-built vector.
    """
    rates = {ScenarioId(f"S{index}"): 0.0 for index in range(1, 13)}
    rates[ScenarioId("S11")] = 1.0
    for name, value in overrides.items():
        rates[ScenarioId(name)] = value
    return rates


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


class TestA45CriterionFour:
    """§12 criterion 4 is a bar, and some probe rate vector fails it."""

    def test_a45_criterion_four_is_falsifiable(self) -> None:
        """The gate: a probe blind to S11 is rejected.

        The criterion's power clause, and half of what the gate asks for. The
        other half -- that a probe firing on an adequate space is also rejected
        -- is the sibling test below rather than a second block here, because a
        failure in this block would short-circuit it and leave assertions that
        were never watched failing.

        ``false_positives`` is empty here and that is asserted rather than
        ignored: a verdict whose explanatory field contradicts its own stated
        reason is not usable even when its boolean is right. This vector fires
        nowhere, so nothing about it is a false positive.
        """
        blind = criterion_four(_rates(S11=0.0))
        assert not blind.holds
        assert not blind.fired_on_s11
        assert blind.false_positives == ()

    def test_a45_a_probe_firing_on_an_adequate_space_is_rejected(self) -> None:
        """The size clause, on **every** scenario the criterion names.

        One representative scenario is not enough, and the reason is specific
        rather than a general preference for coverage: the quiet set is not
        contiguous. An implementation writing it as ``range(1, 8)`` plus nothing
        drops S9 -- the one member the criterion names separately -- and accepts a
        probe firing on every S9 replicate while passing a test that only ever
        perturbs S3. Looping over all eight is what closes that.

        The absence of this term is what ``docs/OPEN-DECISIONS.md`` §1 calls the
        original wording's clearest defect: it stated a power with no size, so an
        arm firing on all twelve scenarios would have passed.
        """
        for scenario in _QUIET:
            verdict = criterion_four(_rates(**{scenario: 0.05}))
            assert not verdict.holds, f"a probe firing on {scenario} is not quiet"
            assert verdict.fired_on_s11
            assert verdict.false_positives == (scenario,)

    def test_a45_any_firing_at_all_is_a_false_positive(self) -> None:
        """The quiet clause is "does not fire", not "fires seldom".

        0.05 is the smallest non-zero rate twenty replicates can produce, so the
        sibling test above already rejects every threshold at or above one
        replicate in twenty. This pins the boundary itself: the criterion's words
        are *does not fire*, so any positive rate is a false positive, and a
        threshold chosen anywhere in between would be a numeric decision nobody
        has taken.
        """
        verdict = criterion_four(_rates(S6=0.005))
        assert not verdict.holds
        assert verdict.false_positives == (ScenarioId("S6"),)

    def test_a45_the_criterion_accepts_a_discriminating_instrument(self) -> None:
        """The instrument the slice measured passes, so the bar is satisfiable.

        Without this the gate above is met by ``return False``, which fails on
        every input and is not a criterion. The pair is the claim: something
        fails it *and* something passes it.

        The firing clause is ``> 0`` rather than a rate threshold, which is the
        entry's own wording -- "fires on S11; does not fire on S1-S7 or S9" --
        and deliberately not strengthened here. Choosing a numeric power
        threshold is a further decision nobody has taken, and inventing one in a
        test would be taking it.
        """
        assert criterion_four(_rates()).holds
        assert criterion_four(_rates(S11=0.05)).holds

    def test_a45_the_unconstrained_scenarios_do_not_move_the_verdict(self) -> None:
        """S8, S10 and S12 say nothing about the criterion, in either direction.

        The natural over-strengthening is "quiet everywhere except S11", which
        this rejects. Those three scenarios are ones where a firing probe is not
        evidence of a bad instrument -- §4.5's compound, non-identifiable and
        garden-path cases -- so a criterion that failed on them would fail an
        instrument that is behaving correctly.
        """
        for scenario in ("S8", "S10", "S12"):
            verdict = criterion_four(_rates(**{scenario: 1.0}))
            assert verdict.holds, f"{scenario} is not part of the criterion"
            assert verdict.false_positives == ()

    def test_a45_an_incomplete_vector_is_refused(self) -> None:
        """A missing scenario raises rather than defaulting to a verdict.

        Both defaults are wrong in the way this gate exists to prevent. Treating
        an absent scenario as ``0.0`` fails an instrument nobody measured;
        treating it as "not applicable" passes the criterion by having nothing
        left to check, which is unfailability arriving by a second door. The
        message names the scenario, so a caller handed a partial report can tell
        which one.

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
            partial = _rates()
            del partial[scenario]
            with pytest.raises(MalformedDesignError, match=rf"missing {scenario}\."):
                criterion_four(partial)

    def test_a45_a_verdict_cannot_contradict_itself(self) -> None:
        """``holds`` may not disagree with the two facts it is a conjunction of.

        The house discipline for this shape is written down and cited to the
        second invariant: :class:`~sciagent.eval.campaign.Adjudication` guards
        its four bare ints in ``__post_init__`` because "a frozen dataclass of
        four bare ints is exactly where a comment would otherwise have been the
        whole of it". This type is two booleans and a tuple in the same position
        and takes the same guard, so the relation between the fields is enforced
        rather than merely described by the field docstrings.
        """
        with pytest.raises(MalformedDesignError, match="holds"):
            CriterionFour(
                holds=True, fired_on_s11=False, false_positives=(ScenarioId("S1"),)
            )
        with pytest.raises(MalformedDesignError, match="holds"):
            CriterionFour(holds=False, fired_on_s11=True, false_positives=())

    def test_a45_a_rate_that_is_not_a_rate_is_refused(self) -> None:
        """A non-finite or out-of-range rate raises rather than reading as quiet.

        The third door into the same defect the missing-scenario guard closes.
        ``nan > 0.0`` is ``False``, so a NaN on a quiet scenario would read as
        "did not fire" and pass the criterion on a scenario nobody successfully
        measured -- which is the partial vector again, wearing a float.

        Unreachable through today's only producer: ``_flags`` refuses any value
        but ``0.0`` or ``1.0`` and ``mean`` raises on an empty array rather than
        returning ``nan``. Guarded anyway because :func:`criterion_four` takes a
        bare mapping from any caller, and it has no production caller yet -- the
        site that will build this mapping does not exist, so the boundary cannot
        be argued from the one that does.
        """
        for bad in (math.nan, -0.1, 1.5):
            with pytest.raises(MalformedDesignError, match="S6"):
                criterion_four(_rates(S6=bad))

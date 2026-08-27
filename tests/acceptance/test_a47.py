"""Acceptance test A47: the size clause admits a calibrated probe.

A47 is a post-freeze gate. SPEC §6 stops at A24, so the criterion is stated in
``docs/BACKLOG.md`` under *"Criterion 4's size clause demands a false-positive
rate no calibrated probe can deliver"*, and reads:

    ``test_a47_the_size_clause_admits_a_calibrated_probe`` -- a probe firing at
    or below its A9-measured size on the quiet set passes the criterion, and one
    firing materially above it fails; the power clause still fails a probe
    silent on S11, and S8, S10 and S12 still move the verdict in neither
    direction.

What was wrong
--------------

A45 implemented "does not fire" as exactly zero, deliberately, because choosing
a threshold was a decision nobody had taken. The completed campaign then took
the measurement that shows what that costs: the probe is a posterior predictive
check firing at ``p < alpha``, a test of positive size produces false
positives, and a criterion forbidding all of them is cleared by a correctly
calibrated probe about once in 1,583 campaigns -- ``(1 - 0.045)^160``. The
recorded campaign fired 5 times in 160 quiet draws, a rate of 0.031, *below*
the realised size of at most 0.045 that A9 measures for this instrument, and
failed the criterion anyway.

The decision this gate encodes
------------------------------

Taken by the user on 2026-08-27 and recorded in ``docs/DECISIONS.md``: the size
clause is stated over the **pooled** quiet set -- one rate over all quiet draws
-- against **A9's measured 0.045**, not per scenario and not the nominal 0.05.

Why pooled, and why the input became counts
-------------------------------------------

At twenty seeds a per-scenario rate is a multiple of 0.05, so any per-scenario
tolerance below 0.05 admits zero firings -- the exactly-zero clause again,
arrived at by arithmetic rather than by wording. Pooling reads the instrument's
size as the single figure it is calibrated as.

A pooled rate is ``total firings / total draws``, which per-scenario *rates*
cannot recover once draw counts differ -- and they do differ, because
``--replicates N`` exists. So :func:`~sciagent.eval.report.criterion_four` now
takes per-scenario **counts**, and the mean-of-rates shortcut is pinned out by a
test below in which the two disagree.

What is deliberately kept from A45
-----------------------------------

The power clause (fires on S11, as any positive rate), the refusal of a partial
vector, the S8/S10/S12 silence, and the self-consistency guard on the verdict
type. ``false_positives`` still names every quiet scenario that fired -- a
firing on an adequate space is still a false positive; the verdict now tolerates
a calibrated *rate* of them rather than their existence.
"""

from __future__ import annotations

from typing import cast

import pytest

from sciagent.core.errors import MalformedDesignError
from sciagent.core.types import ScenarioId
from sciagent.eval.report import (
    SIZE_TOLERANCE,
    CriterionFour,
    ProbeCount,
    criterion_four,
)

#: The eight scenarios the criterion requires the probe to stay quiet on,
#: written from the criterion's text and not imported from the implementation,
#: for the reason ``test_a45.py`` records: the set is **not contiguous** -- S8
#: sits inside the span and is not a member -- and a test importing the
#: implementation's constant would agree with an implementation that got it
#: wrong.
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

#: SPEC §9's replicate count. The recorded campaign ran every scenario at
#: twenty seeds, so the quiet set is 160 draws and the arithmetic in the
#: BACKLOG entry -- 5/160 = 0.031 -- is stated in these units.
_DRAWS = 20


def _counts(**overrides: tuple[int, int]) -> dict[ScenarioId, ProbeCount]:
    """Return a probe-count vector over S1-S12, perturbed by ``overrides``.

    The unperturbed vector is a discriminating instrument at twenty draws a
    scenario: quiet everywhere, firing on S11 at 17/20, which is the rate the
    recorded campaign measured. Each override is ``(fired, draws)``.
    """
    counts = {
        ScenarioId(f"S{index}"): ProbeCount(fired=0, draws=_DRAWS)
        for index in range(1, 13)
    }
    counts[ScenarioId("S11")] = ProbeCount(fired=17, draws=_DRAWS)
    for name, (fired, draws) in overrides.items():
        counts[ScenarioId(name)] = ProbeCount(fired=fired, draws=draws)
    return counts


class TestA47TheSizeClauseAdmitsACalibratedProbe:
    """§12 criterion 4's size clause is one a correctly calibrated probe meets."""

    def test_a47_the_size_clause_admits_a_calibrated_probe(self) -> None:
        """The gate: the campaign the entry measured passes the criterion.

        The exact recorded shape -- one firing in twenty on S2, S5 and S6, two
        on S4, quiet elsewhere -- is 5 firings in 160 quiet draws, a rate of
        0.03125, below the 0.045 the instrument promises. Under A45's
        exactly-zero clause this failed; the decision this gate encodes is that
        it passes.

        ``false_positives`` still names all four firing scenarios: the firings
        are real false positives, and a verdict that stopped reporting them
        would hide exactly the figure a reader needs to see the rate is within
        calibration.
        """
        verdict = criterion_four(
            _counts(S2=(1, _DRAWS), S4=(2, _DRAWS), S5=(1, _DRAWS), S6=(1, _DRAWS))
        )
        assert verdict.holds
        assert verdict.fired_on_s11
        assert verdict.quiet_fired == 5
        assert verdict.quiet_draws == 160
        assert verdict.false_positives == (
            ScenarioId("S2"),
            ScenarioId("S4"),
            ScenarioId("S5"),
            ScenarioId("S6"),
        )

    def test_a47_a_probe_materially_above_its_size_fails(self) -> None:
        """The clause is still a bar: a probe firing at 0.10 pooled is rejected.

        Two firings in twenty on every quiet scenario is 16/160 = 0.10, which is
        more than double the measured size. An instrument doing that is not
        behaving as calibrated, whatever its power, and a size clause that
        admitted it would be A45's *"a criterion no input can fail"* returning
        by the opposite door.
        """
        verdict = criterion_four(_counts(**{str(name): (2, _DRAWS) for name in _QUIET}))
        assert not verdict.holds
        assert verdict.fired_on_s11
        assert verdict.quiet_fired == 16

    def test_a47_the_bar_sits_at_the_measured_size(self) -> None:
        """ "At or below" 0.045, pinned below, above, and exactly *at*.

        Over 160 quiet draws, 7 firings is 0.04375 and passes; 8 is 0.05 and
        fails. Both cases put every firing on one scenario -- S4 at 7/20 is a
        per-scenario rate of 0.35 -- because the pooled form deliberately reads
        one figure: concentration inside a passing pooled rate is within the
        bar, and a re-implementation that quietly re-imposed a per-scenario
        clause would fail the first case.

        The exactly-at case is S4 at 9/60 beside seven quiet scenarios at
        0/20: 9 firings in 200 draws, and ``9/200 == 0.045`` is exact in
        binary floating point, so "at or below" and "strictly below" genuinely
        part company on it -- ``/test-review`` showed every other case in this
        module passes under both. It is a reachable campaign too:
        ``--replicates 25`` puts the quiet set at 200 draws.

        The tolerance itself is asserted once, here, where the boundary is the
        subject: a bar at the nominal 0.05 would pass 8/160 and is the other
        half of the decision this gate encodes.
        """
        assert SIZE_TOLERANCE == 0.045
        assert criterion_four(_counts(S4=(7, _DRAWS))).holds
        assert not criterion_four(_counts(S4=(8, _DRAWS))).holds
        assert criterion_four(_counts(S4=(9, 60))).holds

    def test_a47_pooling_is_by_draws_not_by_scenario(self) -> None:
        """The pooled rate is total firings over total draws, not a mean of rates.

        One scenario at 30/500 beside seven at 0/20 pools to 30/640 = 0.0469,
        above the tolerance -- while the mean of the eight per-scenario rates is
        0.0075 and would pass. The two disagree only when draw counts differ,
        which is exactly the case per-scenario *rates* cannot represent and the
        reason the input became counts.
        """
        verdict = criterion_four(_counts(S4=(30, 500)))
        assert not verdict.holds
        assert verdict.quiet_fired == 30
        assert verdict.quiet_draws == 640

    def test_a47_the_power_clause_still_fails_a_silent_probe(self) -> None:
        """A probe that never fires on S11 is rejected, exactly as under A45.

        The size decision moved one clause and this pins that it moved only
        that one. ``false_positives`` is empty -- a silent probe has nothing to
        report -- and that is asserted so a failing verdict cannot carry a
        contradictory explanation.

        The positive side is pinned at the smallest rate twenty draws can
        produce: one firing in twenty is "fires", exactly as under A45, so a
        re-implementation that quietly raised the power clause to a rate
        threshold fails here rather than surviving on the 17/20 the other
        cases use.
        """
        verdict = criterion_four(_counts(S11=(0, _DRAWS)))
        assert not verdict.holds
        assert not verdict.fired_on_s11
        assert verdict.false_positives == ()
        assert criterion_four(_counts(S11=(1, _DRAWS))).holds

    def test_a47_the_unconstrained_scenarios_move_the_verdict_in_neither_direction(
        self,
    ) -> None:
        """S8, S10 and S12 neither fail the criterion nor enter the pool.

        The first half is A45's own case restated over counts. The second half
        is new and sharper: the quiet set is held at the passing boundary --
        7/160 -- while S8, S10 and S12 fire on all twenty draws each. If the
        unconstrained scenarios leaked into the pooled denominator the rate
        would read 67/220 and fail, so a pass here pins that the pool is
        exactly the eight named scenarios.
        """
        verdict = criterion_four(
            _counts(
                S4=(7, _DRAWS),
                S8=(_DRAWS, _DRAWS),
                S10=(_DRAWS, _DRAWS),
                S12=(_DRAWS, _DRAWS),
            )
        )
        assert verdict.holds
        assert verdict.quiet_fired == 7
        assert verdict.quiet_draws == 160
        assert verdict.false_positives == (ScenarioId("S4"),)

    def test_a47_an_incomplete_vector_is_refused(self) -> None:
        """A missing scenario raises rather than defaulting to a verdict.

        Carried over from A45 unchanged in meaning, because the pooled form
        opens the same two doors: an absent quiet scenario shrinks the
        denominator, and an absent S11 has no power reading. Matched with a
        trailing ``\\.`` for the reason ``test_a45.py`` records -- these ids
        are prefixes of one another, so a bare ``match="S1"`` is satisfied by a
        message blaming S11.
        """
        for scenario in (*_QUIET, ScenarioId("S11")):
            partial = _counts()
            del partial[scenario]
            with pytest.raises(MalformedDesignError, match=rf"missing {scenario}\."):
                criterion_four(partial)

    def test_a47_a_count_that_is_not_a_count_is_refused(self) -> None:
        """Counts that describe no measurement raise at construction.

        Zero draws is a scenario nobody measured wearing an integer; more
        firings than draws is not a fraction of anything; a negative firing
        count is not a count. Each is refused by the value type itself rather
        than at the criterion, so no path can carry one to the comparison.
        """
        for fired, draws in ((0, 0), (21, 20), (-1, 20), (1, -1)):
            with pytest.raises(MalformedDesignError):
                ProbeCount(fired=fired, draws=draws)

    def test_a47_a_rate_shaped_mapping_is_refused_with_a_typed_error(self) -> None:
        """A mapping of floats -- the A45-era shape -- raises naming the scenario.

        The likeliest wrong caller is a mechanical migration still passing
        rates. Without the guard it surfaced as an untyped ``AttributeError``
        naming nothing; the convention is typed exceptions from
        ``sciagent.core.errors``, and the message says what changed and why.
        Found by ``/code-review``.
        """
        rates = {ScenarioId(f"S{index}"): 0.05 for index in range(1, 13)}
        with pytest.raises(MalformedDesignError, match=r"S1\b"):
            criterion_four(cast("dict[ScenarioId, ProbeCount]", rates))

    def test_a47_a_verdict_cannot_contradict_itself(self) -> None:
        """``holds`` may not disagree with the facts it is a conjunction of.

        The same ``__post_init__`` discipline A45 pinned, restated over the new
        fields: a verdict claiming to hold at a pooled rate above the tolerance
        is refused, as is one claiming to fail while both clauses are met, and
        one whose firing count disagrees with its own list of firing scenarios.
        """
        with pytest.raises(MalformedDesignError, match="holds"):
            CriterionFour(
                holds=True,
                fired_on_s11=True,
                false_positives=(ScenarioId("S4"),),
                quiet_fired=8,
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
        with pytest.raises(MalformedDesignError):
            CriterionFour(
                holds=True,
                fired_on_s11=True,
                false_positives=(),
                quiet_fired=5,
                quiet_draws=160,
            )
        # One firing cannot land on three scenarios, and a scenario outside
        # the quiet set is not a false positive the size clause counted --
        # both found constructible by /code-review, both now refused.
        with pytest.raises(MalformedDesignError):
            CriterionFour(
                holds=True,
                fired_on_s11=True,
                false_positives=(ScenarioId("S1"), ScenarioId("S2"), ScenarioId("S3")),
                quiet_fired=1,
                quiet_draws=160,
            )
        with pytest.raises(MalformedDesignError, match="quiet set"):
            CriterionFour(
                holds=True,
                fired_on_s11=True,
                false_positives=(ScenarioId("S8"),),
                quiet_fired=1,
                quiet_draws=160,
            )

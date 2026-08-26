"""Acceptance test A29: the harness-evaluated probe verdict is arm-symmetric.

A29 is a post-freeze gate. SPEC §6 stops at A24, so the criterion is stated in
``docs/BACKLOG.md`` under *"Criterion 4's observable, implemented
arm-symmetrically once decided cold"*, and reads:

    ``test_a29_the_probe_verdict_is_arm_symmetric`` -- for a fixed (scenario,
    seed), the harness-evaluated probe verdict is identical whichever system
    ran, and both flags are recorded and distinguishable in the payload.

The same entry's **Idea** is what the gate is a check on: *"the harness
evaluates the named probe for every arm uniformly at the gate point, records
both flags (probe verdict and whole-record PPC) clearly labelled, and reports
detection with a false-positive term."*

What was wrong
--------------

SPEC §12 criterion 4 -- *"Detects inadequacy on S11 at a rate at least matching
B1"* -- named no check, and two answered to the name. The recorded matrix
carries only one of them: :attr:`~sciagent.eval.matrix.CellReading.inadequate`
is the **whole-record** posterior predictive check, which fired on 0 of V7's 20
S11 replicates, while the **Stage A probe** -- the check
:meth:`~sciagent.systems.base.Investigation.ppc` scopes and SPEC F6's gate is
conditional upon -- opened on 17 of those same 20. ``report_matrix --contrast``
exits 3 because it conditions on the first while V7 acted on the second.

``docs/OPEN-DECISIONS.md`` §1 wrote the fork up to be taken cold, and
``docs/DECISIONS.md`` (2026-08-21) records it taken as **C1**: power against
size on the named Stage A probe, *"the harness evaluates the probe for every arm
regardless of whether the arm consults it"*.

Where the probe is evaluated, and why it is there
-------------------------------------------------

The scoped check reads the engine's live set and its posterior, and both are
arm-specific: recorded end-of-run, S11's probe reads 0.0112 under V7 and 0.0294
under B1, and V7's own gate saw 0.0128. So *when* the harness evaluates it is
the whole of whether this gate can hold.

It is evaluated **before ``investigate`` is called**, on the
:func:`~sciagent.systems.base.null_seeded_graph` the harness hands every arm and
the Stage A reading :func:`~sciagent.eval.campaign.run_scenario` has just taken.
No system code has run at that point, so *identical whichever system ran* is
structural rather than an agreement between arms that happens to hold on one
recorded matrix.

Two consequences worth stating, because each is a way this could have been got
wrong:

**Evaluating it per arm at the end of the run is the design that was already
withdrawn.** A ``ScenarioRun.adequacy`` field was written on 2026-08-16 and
removed the same day, and :class:`~sciagent.eval.campaign.ScenarioRun` still
records why: the end-of-run posterior is the one the arm's *proposals* moved, so
"a system that successfully proposed a structure explaining the probe would be
recorded as having failed to detect", inverting the criterion it is supposed to
serve.

**The pre-run value is not a degenerate instrument.** B1 holds only the null and
proposes nothing, so its live set never changes and its posterior never moves --
its probe reading is the pre-run reading, throughout. That is the 0.0294 that
fires on S11 and stays quiet on the other eleven, so the arm-symmetric probe is
the discriminating one the 2026-08-16 measurement recorded.

**What it costs is criterion 4 as a bar, in both of its clauses.** C1 words the
criterion as two comparisons of V7 against B1, and
:func:`~sciagent.eval.matrix.replicate_seeds` pairs every arm on one seed
sequence, so two arms reading one instrument agree exactly and neither clause can
fail -- not the power clause on S11, and not the false-positive clause on S1-S7
and S9. ``test_a29_the_probe_fires_on_s11_and_not_on_an_in_library_scenario``
asserts that equality on S1 itself, which is one of the scenarios the
false-positive clause is read on: this module proves the criterion unfailable in
the course of establishing the gate. Restoring a bar needs an *absolute*
threshold instead of a comparison, which changes what V7 is graded on and was
left to ``docs/BACKLOG.md`` to decide cold.

**Decided 2026-08-26, gate A45.** The criterion was reworded absolutely -- fires
on S11, does not fire on S1-S7 or S9 -- and moved out of §12's Capability block
into Infrastructure, because what this gate established is that the reading is
shared by every arm and therefore grades the instrument rather than any agent.
The cost this section describes is permanent and was accepted, not repaired.

What the tests establish
------------------------

``test_a29_the_probe_verdict_is_arm_symmetric`` is the gate's own two clauses.
Three arms whose runs demonstrably differ -- in live set, in posterior, and in
the whole-record check -- agree on the probe's verdict *and* on its p-value, and
the payload carries both flags under keys **bound to the field each is named
for**.

That binding is the half worth explaining, because the obvious way to write this
clause omits it. Asserting only that the two payload keys hold different values
is invariant under swapping the two labels: a ``reading_of`` recording the
whole-record check as ``probe_inadequate`` and the probe as ``inadequate``
satisfies every such assertion, and it is the entry's own defect restated --
``docs/BACKLOG.md`` asks for both flags *"clearly labelled"* precisely because
1,120 recorded rows already mean the whole-record check under the name
``inadequate``, and :mod:`sciagent.eval.report` conditions its contrast on that
literal key. The same gap admits a payload holding an arm-*dependent* probe
while :attr:`~sciagent.eval.campaign.ScenarioRun.probe` stayed symmetric. Both
close by asserting each payload entry equals the attribute it claims to be.

The other three tests are what stop clause one being satisfied by something
weaker. A probe wired to a constant, or to the arm's own reading, would pass it,
so one test reads the value an arm saw at its *own* gate and shows it moved
while the harness's did not. A probe that fired everywhere would satisfy
criterion 4's power clause with no discrimination at all, so one test takes the
false-positive side on an in-library scenario.

The fourth is the sharpest statement of why any of this is needed. On S11 the
whole-record check does not merely differ across arms in its p-value: its
**verdict** differs. It fires for B1, whose space is inadequate on eleven of
twelve by construction because B1 holds only the null, and stays quiet for V1 on
the same scenario and seed. So the flag the recorded matrix conditions on is
arm-dependent in the one place §9's contrast is read, while the probe beside it
is not. An earlier version of this module asserted the two flags disagree for
*every* arm, which is false for exactly this reason and would have made the gate
unsatisfiable.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from slice_tables import (
    AGENT_GRAMMAR,
    GRAMMAR,
    METRICS,
    gate_table,
    save_gate_table,
)

from environments.pointproc.outcomes import (
    closed_set,
    executor,
    simulator,
    slice_designs,
)
from environments.pointproc.scenarios import scenario
from sciagent.core.types import Diagnosis
from sciagent.eval.campaign import ScenarioRun, run_scenario
from sciagent.eval.matrix import CellReading, reading_of
from sciagent.inference.empirical import EmpiricalTable, EmpiricalTableEngine
from sciagent.inference.interface import PPCResult
from sciagent.registry.store import ExperimentStore
from sciagent.systems.base import Investigation, entertain, null_seeded_graph
from sciagent.systems.baselines.boed_only import BOEDOnly
from sciagent.systems.baselines.ppc_only import PPCOnly

#: The three arms every scenario here is run with. B1 and V1 are SPEC §5's, so
#: the symmetry claim is made about systems the matrix actually contains;
#: ``Rotating`` is here because neither of those will say what it saw.
ARMS = ("B1", "V1", "rotating")


class Rotating:
    """A system that entertains the library and spends the budget in order.

    Neither V1 nor B1, deliberately, and it is here for one reason: it keeps the
    scoped check it read at its *own* gate point. That value is what a system
    acts on under SPEC F6, and this gate's claim is that the recorded probe is
    not it -- a claim nothing can check without an arm willing to say what it
    saw.

    The rotation is B1's design order rather than BOED's selection, so its
    posterior parts company with V1's over the same library, which is what makes
    the gate test's three arms three and not two.
    """

    def __init__(self) -> None:
        self._library = closed_set()
        self._seen: PPCResult | None = None

    @property
    def name(self) -> str:
        """Return this arm's identifier."""
        return "rotating"

    @property
    def seen(self) -> PPCResult:
        """Return the scoped check this system read at its own gate point."""
        if self._seen is None:
            raise AssertionError("investigate() has not been called")
        return self._seen

    def investigate(self, investigation: Investigation) -> Diagnosis:
        """Entertain, spend everything, then read the gate a system acts on."""
        entertain(investigation, self._library)
        designs = investigation.designs
        step = 0
        while investigation.affords():
            investigation.run(designs[step % len(designs)])
            step += 1
        self._seen = investigation.ppc()
        return investigation.conclude()


@dataclass(frozen=True, slots=True)
class Recorded:
    """One arm's run on one scenario, scored, with what the arm itself saw."""

    run: ScenarioRun
    reading: CellReading
    seen: PPCResult | None
    """The scoped check the system read at its own gate, where it kept one."""


@lru_cache(maxsize=1)
def _runs() -> dict[tuple[str, str], Recorded]:
    """Return every run this gate reads, keyed by ``(scenario, arm)``.

    Built once and cached, with the empirical table threaded from run to run and
    saved at the end, exactly as ``tests/baseline_runs.py`` does it: a structure
    entertained once is simulated once per machine rather than once per test.

    Each scenario runs at its own declared seed, which is the "fixed (scenario,
    seed)" the criterion quantifies over.
    """
    table: EmpiricalTable = gate_table()
    simulate = simulator(GRAMMAR)
    built: dict[tuple[str, str], Recorded] = {}
    for name in ("S11", "S1"):
        target = scenario(name)
        for arm in ARMS:
            system = _system(arm)
            graph = null_seeded_graph(AGENT_GRAMMAR, METRICS, table, slice_designs())
            engine = EmpiricalTableEngine(graph, table, simulate=simulate)
            runner = executor(
                GRAMMAR,
                store=ExperimentStore.in_memory(),
                budget=target.budget,
            )
            run = run_scenario(
                target,
                system,
                executor=runner,
                engine=engine,
                graph=graph,
            )
            reading, table = reading_of(
                run,
                grammar=GRAMMAR,
                table=engine.table,
                simulate=simulate,
                observations=engine.observations,
                program=runner.reference,
            )
            built[(name, arm)] = Recorded(
                run=run,
                reading=reading,
                seen=system.seen if isinstance(system, Rotating) else None,
            )
    save_gate_table(table)
    return built


def _system(arm: str) -> PPCOnly | BOEDOnly | Rotating:
    """Return a fresh system for one arm. Fresh because ``Rotating`` holds state."""
    if arm == "B1":
        return PPCOnly()
    if arm == "V1":
        return BOEDOnly(closed_set())
    return Rotating()


def _run(name: str, arm: str) -> ScenarioRun:
    """Return one recorded run."""
    return _runs()[(name, arm)].run


def _probe(name: str, arm: str) -> PPCResult:
    """Return one run's harness-evaluated probe, refusing ``None``.

    ``ScenarioRun.probe`` is optional because a scenario may declare no Stage A
    design, and every scenario this gate runs declares one. Asserting that here
    keeps the assertions below about the criterion rather than about the type,
    and turns a scenario table that quietly stopped declaring a probe into a
    failure of this gate rather than into a skipped comparison.
    """
    probe = _run(name, arm).probe
    assert probe is not None, f"{name} declares a Stage A design"
    return probe


def _payload(name: str, arm: str) -> dict[str, float]:
    """Return the ledger payload one recorded run was scored to."""
    return dict(_runs()[(name, arm)].reading.as_payload())


class TestA29ProbeArmSymmetry:
    """The probe a §12 criterion 4 rate is read off may not depend on the arm."""

    def test_a29_the_probe_verdict_is_arm_symmetric(self) -> None:
        """The gate: one verdict across arms, and two flags in the payload.

        The first three assertions establish that the three arms really did run
        differently. A symmetry claim over runs that happened to coincide would
        be worth nothing, and each of the three is a quantity the scoped check
        reads: which hypotheses are live, where the posterior sits, and what the
        record holds.
        """
        runs = [_run("S11", arm) for arm in ARMS]

        assert len({len(run.graph.nodes) for run in runs}) > 1
        assert (
            len({tuple(sorted(run.diagnosis.distribution.items())) for run in runs}) > 1
        )
        assert len({run.ppc.p_value for run in runs}) > 1

        # Clause one: the harness's probe does not move with any of that.
        probes = [_probe("S11", arm) for arm in ARMS]
        assert len({probe.inadequate for probe in probes}) == 1
        assert len({probe.p_value for probe in probes}) == 1

        # Clause two: both flags reach the payload, each under the name of the
        # field it actually holds. Asserting only that the two differ would be
        # satisfied by a transposition, which is the entry's own defect wearing
        # the new key's name -- see this module's docstring.
        for arm in ARMS:
            run = _run("S11", arm)
            probe = _probe("S11", arm)
            payload = _payload("S11", arm)
            assert payload["probe_inadequate"] == float(probe.inadequate)
            assert payload["probe_p_value"] == probe.p_value
            assert payload["inadequate"] == float(run.ppc.inadequate)
            assert payload["ppc_p_value"] == run.ppc.p_value
            assert payload["probe_inadequate"] in (0.0, 1.0)
            assert payload["inadequate"] in (0.0, 1.0)
            assert payload["probe_p_value"] != payload["ppc_p_value"]

    def test_a29_the_probe_is_not_the_gate_the_system_itself_saw(self) -> None:
        """The recorded probe is the harness's, not whatever the arm last read.

        ``Rotating`` reads the scoped check after spending its whole budget over
        a library it entertained, which is where SPEC F6's gate is consulted.
        That value is a function of the arm; the recorded one is not, and a probe
        wired to "whatever the system saw" would satisfy the symmetry clause only
        where the arms happened to agree.
        """
        recorded = _runs()[("S11", "rotating")]
        assert recorded.seen is not None
        assert recorded.seen.p_value != _probe("S11", "rotating").p_value
        assert _probe("S11", "rotating").p_value == _probe("S11", "B1").p_value

    def test_a29_the_probe_fires_on_s11_and_not_on_an_in_library_scenario(
        self,
    ) -> None:
        """The instrument discriminates, and criterion 4 as C1 worded it could not
        read that.

        The absolute wording taken at gate A45 on 2026-08-26 reads exactly this
        discrimination, which is what that rewording was for.

        Without this, clause one of the gate is satisfied by a probe that fires
        on everything or on nothing. Symmetry is asserted on the quiet scenario
        too, since a probe that is arm-symmetric only where it fires is not
        arm-symmetric.

        **The last two assertions are also the demonstration that §12 criterion
        4's false-positive clause cannot fail.** S1 is one of the scenarios that
        clause is read on, and "no higher than B1's" compares two numbers this
        test pins as identical. The discrimination the first two assertions
        establish is real and is a property of the *instrument*; it is not
        something the criterion, as C1 words it, is able to require of an arm.
        """
        assert _probe("S11", "B1").inadequate
        assert not _probe("S1", "B1").inadequate
        assert len({_probe("S1", arm).p_value for arm in ARMS}) == 1
        assert len({_probe("S1", arm).inadequate for arm in ARMS}) == 1

    def test_a29_the_whole_record_flag_is_arm_dependent_and_the_probe_is_not(
        self,
    ) -> None:
        """S11 is the case the entry was written about, and it survives here.

        Two facts, and the second is why the first matters. On V1 the probe
        fires while the whole-record check stays quiet -- the disagreement
        ``report_matrix --contrast`` exits 3 on, and recording one check under
        both names would erase it while satisfying "both flags" as a word count.

        And the whole-record *verdict* itself moves with the arm: it fires for
        B1, which holds only the null and whose space is therefore inadequate on
        eleven of twelve by construction, on the same scenario at the same seed
        where it is quiet for V1. The flag the recorded matrix conditions on is
        arm-dependent exactly where §9's contrast reads it. The probe beside it
        is not, which is the whole of what this gate buys.
        """
        v1 = _run("S11", "V1")
        v1_probe = _probe("S11", "V1")
        assert v1_probe.inadequate
        assert not v1.ppc.inadequate
        assert v1_probe.p_value < v1.ppc.p_value

        assert _run("S11", "B1").ppc.inadequate
        assert len({_run("S11", arm).ppc.inadequate for arm in ARMS}) > 1
        assert len({_probe("S11", arm).inadequate for arm in ARMS}) == 1

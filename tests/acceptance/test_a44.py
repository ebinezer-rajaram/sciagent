"""Acceptance test A44: a fault is not scored as a refusal.

A44 is a post-freeze gate. SPEC §6 stops at A24, so the criterion is stated in
``docs/BACKLOG.md`` under *"Audit the proposal path's failure taxonomy, rather
than extending it one crash at a time"*, and reads:

    ``test_a44_a_fault_is_not_scored_as_a_refusal`` -- a constructed failure of
    each enumerated kind reaches exactly one cause in the parallel non-scoring
    breakdown: a model declining reads as a refusal, a dead session as
    transport, and a fault of the machine propagates rather than reaching
    ``ProposalRecord`` at all; ``EditNotInGrammarError`` from ``apply`` lands
    where this entry decides rather than escaping ``_admit``; and
    ``ProposalRecord``'s five fields are untouched, pinned by the recorded
    campaign's ``yield_fraction`` coming out bit-identical across the change. No
    ``METRIC_VERSION`` bump: that is what T3 buys and what the gate must not
    quietly spend.

The same entry's **Idea** is what the gate is a check on: *"enumerate the
exceptions reachable from ``ProposalLayer.propose`` and ``Investigation.propose``,
decide for each whether it is a proposal outcome or a fault that should stop a
run, and pin the decision in tests. Then check ``Hybrid._propose_once`` and
``_admit`` handle exactly that set."*

What was wrong
--------------

``ProposalRecord.refused`` was one tier holding eighteen distinct conditions across
two backends, and ``docs/OPEN-DECISIONS.md`` §2 enumerates them: **only two are
unambiguously scientific events**, the two ``stop_reason == "refusal"`` rows.
Everything else was a fault of the machine, a dead session, or a response that
carried no readable payload -- all recorded as though a model had declined, and
all feeding SPEC §12 criterion 11's ``yield_fraction`` as such.

Two of the escapes at this seam were found in one afternoon on 2026-08-18, each
by a live campaign stopping rather than by a test, and each costing quota and
about two hours of compute to discover. That is the instrument this gate
replaces.

The decision this implements, and the one it must not overreach
--------------------------------------------------------------

**T3**, decided 2026-08-21 (``docs/DECISIONS.md``, and ``docs/OPEN-DECISIONS.md``
§2): keep ``ProposalRecord``'s five fields exactly as they are, so
``yield_fraction``'s denominator never moves, and carry the diagnostic question
in a **parallel non-scoring breakdown**. T2 -- splitting the tier itself -- was
superseded the same day when the premise that made its ``METRIC_VERSION`` bump
free turned out to be false: A40 re-derives on ``dimensions`` and ``battery`` and
bumps nothing, so the bump would have been a measured 3m11s table rebuild on
every machine for a change touching no estimator.

So **no condition is re-filed between the five scoring tiers**. A response that
will not parse stays in ``refused`` where it has always been counted, and gains
the cause ``malformed_response`` beside it. Re-filing it into ``malformed``
would be T2 wearing T3's clothes: it moves no denominator, but it moves which
outcome ``_extend`` breaks on, and therefore how many requests a run makes.

**T1a** survives from the superseded entry and is the half that does move code:
a fault of the machine leaves the tier by *propagating*. That moves no recorded
number, because a condition that propagates was never scored.

The A35/A40 boundary, which this gate sits on
---------------------------------------------

``docs/DECISIONS.md`` (2026-08-21) pins that A44 must widen the catch **by
kind** -- adding causes beside ``refused`` -- and never by moving up the
hierarchy to ``ProposalError``, which is the parent of ``TranscriptMissError``
and would record a replay miss as a refusal. A40's
``test_a40_a_replay_stops_on_a_miss_rather_than_recording_a_refusal`` reads the
same catch clause from the other side. ``Hybrid._propose_once`` still catches
``ProviderError`` and ``MalformedProposalError`` and nothing wider, and
:class:`TestA44AFaultIsNotScoredAsARefusal` asserts the miss still propagates.

Where the machine faults went
-----------------------------

Five conditions move from ``ProviderError`` to
:class:`~sciagent.core.errors.SystemConfigurationError`, which sits *outside*
``ProposalError`` at ``core/errors.py`` and therefore cannot be caught and
scored. They are the rows ``docs/OPEN-DECISIONS.md`` §2 marks **fault**: an
inherited environment variable that would change what the run produces, a call
made from inside a running event loop, a turn served by a non-``firstParty``
provider, a session reporting no per-model usage, and a session served by a
model other than the pinned one. None is a statement about the model's ability
to propose.

Two rows §2 calls *"arguably misfiled"* -- a stream that drains with no
``ResultMessage``, and a session ending ``is_error`` or non-``success`` -- stay
**in** the record. The gate text is explicit that a dead session reads as
``transport``, which is a cause in the breakdown rather than a propagation, and
the gate is the criterion.

What the tests establish
------------------------

``test_a44_a_fault_is_not_scored_as_a_refusal`` is the gate's first clause: its
three destinations, taken end to end through a real ``Hybrid`` on a real
scenario.

``test_a44_every_provider_condition_carries_exactly_one_cause`` is the
enumeration. It exercises the real provider code for every row of
``docs/OPEN-DECISIONS.md`` §2's two tables, which is the half that stops the
gate being satisfied by a taxonomy that is merely self-consistent.

``test_a44_a_cause_disagreeing_with_its_outcome_is_refused`` is what makes
"exactly one" structural rather than a convention. Each cause belongs to exactly
one scoring tier, and an attempt whose two fields disagree raises instead of
being counted -- the same shape as ``PROPOSAL_OUTCOMES`` being *derived* from
``ProposalRecord``'s fields rather than written out beside them.

``test_a44_a_grammar_refusal_at_apply_time_propagates`` takes the decision the
entry deferred to this change. ``EditNotInGrammarError`` reaching ``simulate``
means the agent grammar licensed a structure the edit grammar refuses -- a
divergence between two *framework*-supplied grammars, not a fact about the
candidate -- so it takes the propagating side, exactly as ``DeterminismError`` is
deliberately absent from ``CANDIDATE_FAULTS`` for the same reason. "Lands where
this entry decides rather than escaping ``_admit``" is discharged by pinning the
landing *by type*: it emerges from ``_admit`` as itself, converted to no outcome,
which is what makes it decided rather than accidental.

``test_a44_the_scoring_record_is_unmoved`` is the gate's third clause.

``test_a44_an_allowance_above_two_is_refused`` settles the break asymmetry the
same entry requires be settled *in this change*. ``_extend`` breaks on
``"refused"`` and continues on the other four, so at ``max_proposals >= 3`` a
model unable to produce a second admission would score strictly higher by
declining. The break rule is unchanged -- changing it would move a number the
recorded matrix reported -- and the ceiling becomes a guard, so raising the
allowance is a decision somebody takes rather than a constructor argument.

On the third clause's wording, and what is asserted instead
-----------------------------------------------------------

The gate asks for ``yield_fraction`` *"coming out bit-identical"* on the recorded
campaign. It cannot be read: the ledger's reading carries sixteen fields over all
1,120 rows of ``.cache/campaign/spec9.db`` and **not one proposal-outcome
field**, which is A31's omission observed from this end. Recomputing it would
mean replaying the eighteen LLM cells, which A35 records as crashing at the first
refusal and which contradicts the same entry's *"no live call is needed to grade
it"*.

So the pin is constructed instead, and the substitution is deliberate rather than
a weaker clause quietly satisfied: ``PROPOSAL_OUTCOMES`` is exactly the five
fields, no cause enters ``requested``, and a fixed corpus of attempt sequences
yields ``yield_fraction`` values written here as literals computed under the
pre-change taxonomy -- including the same sequences re-tagged across every cause,
which is the property that actually matters. Retagging a refusal as ``transport``
must not move the fraction, and that is what a recorded campaign would have been
evidence of.
"""

from __future__ import annotations

import pickle
from collections.abc import Callable, Mapping
from dataclasses import fields
from functools import lru_cache
from typing import Any, cast

import pytest
from slice_tables import (
    AGENT_GRAMMAR,
    GRAMMAR,
    METRICS,
    gate_table,
    save_gate_table,
)

from environments.pointproc.catalogue import METRIC_VERSION, metric_registry
from environments.pointproc.outcomes import (
    closed_set,
    executor,
    simulator,
    slice_designs,
)
from environments.pointproc.scenarios import scenario
from sciagent.core.edits import Defect
from sciagent.core.errors import (
    EditNotInGrammarError,
    InvestigationError,
    MalformedProposalError,
    ProposalError,
    ProviderError,
    ProviderUnavailableError,
    StructureNotMeasurableError,
    SystemConfigurationError,
    TranscriptMissError,
)
from sciagent.core.types import HypothesisId, ScenarioId
from sciagent.eval.agency import (
    OUTCOME_OF_CAUSE,
    PROPOSAL_CAUSES,
    PROPOSAL_OUTCOMES,
    AgencyMetrics,
    ProposalRecord,
    agency_report,
    proposal_causes,
    proposal_record,
)
from sciagent.eval.campaign import ScenarioRun, run_scenario
from sciagent.eval.scoring import DIMENSION_VERSION
from sciagent.inference.empirical import EmpiricalTable, EmpiricalTableEngine
from sciagent.registry.store import ExperimentStore
from sciagent.systems.base import Investigation, null_seeded_graph
from sciagent.systems.hybrid import MAX_PROPOSALS, Hybrid, ProposalAttempt
from sciagent.systems.llm import (
    RECORD,
    ProposalLayer,
    ScriptedProvider,
    TranscriptStore,
    fixed_payload,
)
from sciagent.systems.llm.encoding import (
    decode,
    draft_from_payload,
    structural_menu,
    tool_schema,
)
from sciagent.systems.llm.provider import Proposal

SCHEMA = tool_schema(structural_menu(AGENT_GRAMMAR))

#: A payload the scripted provider can return, for the cases that need one.
PAYLOAD: Mapping[str, Any] = fixed_payload(3, (20, 30, 40), name="size_mixture")

_TABLE: list[EmpiricalTable] = []


def _table() -> EmpiricalTable:
    """Return the shared gate table, loading it once.

    The same table ``tests/test_hybrid.py`` reads, for the same reason: a
    structure V7 proposes has no row in the calibrated table and filling one
    costs 2000 replicates, so it is built once per machine rather than once per
    module.
    """
    if not _TABLE:
        _TABLE.append(gate_table())
    return _TABLE[0]


@lru_cache(maxsize=1)
def _graph() -> Any:
    """Return the null-seeded graph every constructed case here reads."""
    return null_seeded_graph(AGENT_GRAMMAR, METRICS, _table(), slice_designs())


def _run(system: Hybrid, scenario_id: str = "S11") -> ScenarioRun:
    """Run one system on one scenario, growing the shared table.

    S11 by default: it is the one slice scenario whose mechanism is genuinely
    outside the library, so SPEC F6's Stage A gate opens and the proposal path is
    reached at all. On an in-library scenario the check passes, no proposal is
    made, and every assertion below would hold vacuously.
    """
    the_scenario = scenario(scenario_id)
    table = _table()
    graph = null_seeded_graph(AGENT_GRAMMAR, METRICS, table, slice_designs())
    engine = EmpiricalTableEngine(graph, table, simulate=simulator(GRAMMAR))
    run = run_scenario(
        the_scenario,
        system,
        executor=executor(
            GRAMMAR, store=ExperimentStore.in_memory(), budget=the_scenario.budget
        ),
        engine=engine,
        graph=graph,
    )
    _TABLE[0] = engine.table
    save_gate_table(_TABLE[0])
    return run


class _RaisingSource:
    """A :class:`~sciagent.systems.hybrid.ProposalSource` that always raises.

    The narrowest construction of "a proposal failed this way": it reaches no
    provider, spends no quota and touches no transcript, so what is exercised is
    the seam under test and nothing behind it. The exception it raises is a real
    instance of the class a real backend raises for that condition -- built by
    :func:`_provider_failure` from the provider's own code where the row is a
    provider row.
    """

    def __init__(self, failure: Exception) -> None:
        self._failure = failure

    def propose(self, investigation: Investigation) -> Proposal:
        raise self._failure


class _StubInvestigation:
    """Just enough of an :class:`Investigation` for ``Hybrid._admit`` to run.

    ``_admit`` reads three things -- the graph, the structures already proposed,
    and :meth:`propose` -- so a stub holding a *real* graph exercises the real
    duplicate check and the real slugging while letting ``propose`` raise the
    condition under test. Building a real investigation whose engine raises
    ``EditNotInGrammarError`` at apply time would mean driving a full scenario to
    reach one ``except`` clause.
    """

    def __init__(self, failure: Exception | None = None) -> None:
        self._failure = failure
        self.proposed: dict[HypothesisId, Defect] = {}

    @property
    def graph(self) -> Any:
        return _graph()

    def propose(self, node_id: HypothesisId, **_kwargs: Any) -> None:
        if self._failure is not None:
            raise self._failure


def _proposal(name: str = "constructed") -> Proposal:
    """Return a decoded proposal for a structure the graph does not hold."""
    defect = decode(
        AGENT_GRAMMAR,
        structural_menu(AGENT_GRAMMAR),
        draft_from_payload(fixed_payload(1, (32, 55, 29, 22), name=name)),
    )
    return Proposal(
        program_edit=defect, name=name, rationale="constructed", address="stub:0"
    )


# ---------------------------------------------------------------------------
# The enumeration: every row of ``docs/OPEN-DECISIONS.md`` §2's two tables.
# ---------------------------------------------------------------------------


def _model_usage(canonical: str | None = None) -> dict[str, Any]:
    """Return one per-model usage entry, in the SDK's camelCase wire shape."""
    entry: dict[str, Any] = {
        "inputTokens": 100,
        "outputTokens": 10,
        "cacheReadInputTokens": 0,
        "cacheCreationInputTokens": 0,
        "webSearchRequests": 0,
        "costUSD": 0.01,
        "contextWindow": 1_000_000,
        "maxOutputTokens": 128_000,
    }
    if canonical is not None:
        entry["canonicalModel"] = canonical
    return entry


def _result(**overrides: Any) -> Any:
    """Return a successful ``ResultMessage``, with fields optionally changed."""
    from claude_agent_sdk import ResultMessage

    payload: dict[str, Any] = {
        "subtype": "success",
        "duration_ms": 1,
        "duration_api_ms": 1,
        "is_error": False,
        "num_turns": 1,
        "session_id": "test-session",
        "structured_output": dict(PAYLOAD),
        "model_usage": {"claude-opus-5": _model_usage()},
    }
    payload.update(overrides)
    return ResultMessage(**payload)


def _stub_runner(result: Any) -> Any:
    """Return a stand-in for ``claude_agent_sdk.query`` that yields ``result``."""

    async def runner(*, prompt: str, options: Any) -> Any:
        yield result

    return runner


def _empty_runner() -> Any:
    """Return a runner whose stream ends without a ``ResultMessage``."""

    async def runner(*, prompt: str, options: Any) -> Any:
        return
        yield  # pragma: no cover - makes this an async generator

    return runner


def _text_response(text: str) -> Any:
    """Return a Messages-API response carrying one text block."""

    class _Block:
        type = "text"

        def __init__(self, value: str) -> None:
            self.text = value

    class _Response:
        stop_reason = "end_turn"
        model = "claude-opus-5"

        def __init__(self, value: str) -> None:
            self.content = [_Block(value)]

    return _Response(text)


def _stop_response(reason: str) -> Any:
    """Return a Messages-API response that stopped for ``reason``."""

    class _Response:
        stop_reason = reason
        model = "claude-opus-5"
        stop_details = None

        def __init__(self) -> None:
            self.content: list[Any] = []

    return _Response()


def _blockless_response() -> Any:
    """Return a Messages-API response carrying no text block."""

    class _Block:
        type = "thinking"

    class _Response:
        stop_reason = "end_turn"
        model = "claude-opus-5"

        def __init__(self) -> None:
            self.content = [_Block()]

    return _Response()


def _client_returning(response: Any) -> Any:
    """Return a stand-in Anthropic client whose ``messages.create`` returns it."""

    class _Messages:
        def create(self, **_kwargs: Any) -> Any:
            return response

    class _Client:
        messages = _Messages()

    return _Client()


def _client_raising(failure: Exception) -> Any:
    """Return a stand-in Anthropic client whose ``messages.create`` raises."""

    class _Messages:
        def create(self, **_kwargs: Any) -> Any:
            raise failure

    class _Client:
        messages = _Messages()

    return _Client()


def _agent_sdk(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> Any:
    """Return an ``AgentSdkProvider`` with the contaminating variables unset."""
    from sciagent.systems.llm.agent_sdk_provider import (
        CONTAMINATING_VARIABLES,
        AgentSdkProvider,
    )

    for name, _ in CONTAMINATING_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    return AgentSdkProvider(**kwargs)


#: The two classes ``Hybrid._propose_once`` converts into recorded outcomes.
#:
#: Not ``ProposalError``, and the difference is the A35/A40 boundary itself.
#: ``TranscriptMissError`` and ``ProviderUnavailableError`` are both
#: ``ProposalError`` subclasses that must **not** be caught, so "is a
#: ``ProposalError``" and "is scored" are different questions and only the second
#: is the one this gate asks. Written out here rather than imported from
#: ``hybrid`` on purpose: a test that reads the catch set from the code under
#: test cannot notice the catch set widening.
CAUGHT_BY_HYBRID: tuple[type[Exception], ...] = (ProviderError, MalformedProposalError)

#: Every enumerated condition, as ``(label, expected class, expected cause)``.
#:
#: ``cause`` is ``None`` where the condition **propagates** -- there is no
#: attempt to carry a cause, which is the whole of what T1a bought. The builder
#: for each row lives in :meth:`TestA44AFaultIsNotScoredAsARefusal._raise`, and
#: the labels are the ones ``docs/OPEN-DECISIONS.md`` §2 uses.
CONDITIONS: tuple[tuple[str, type[Exception], str | None], ...] = (
    # ``agent_sdk_provider.py``
    ("sdk:contaminated_env", SystemConfigurationError, None),
    ("sdk:running_event_loop", SystemConfigurationError, None),
    ("sdk:foreign_provider", SystemConfigurationError, None),
    ("sdk:no_model_usage", SystemConfigurationError, None),
    ("sdk:wrong_model_served", SystemConfigurationError, None),
    ("sdk:no_result_message", ProviderError, "transport"),
    ("sdk:session_error", ProviderError, "transport"),
    ("sdk:refusal", ProviderError, "declined"),
    ("sdk:max_tokens", ProviderError, "output_ceiling"),
    ("sdk:structured_output_absent", ProviderError, "malformed_response"),
    ("sdk:structured_output_not_object", ProviderError, "malformed_response"),
    ("sdk:session_died", ProviderUnavailableError, None),
    ("sdk:not_installed", ProviderUnavailableError, None),
    # ``anthropic_provider.py``
    ("api:refusal", ProviderError, "declined"),
    ("api:max_tokens", ProviderError, "output_ceiling"),
    ("api:no_text_block", ProviderError, "malformed_response"),
    ("api:not_json", ProviderError, "malformed_response"),
    ("api:json_not_object", ProviderError, "malformed_response"),
    ("api:transport", ProviderUnavailableError, None),
    ("api:not_installed", ProviderUnavailableError, None),
)


class TestA44AFaultIsNotScoredAsARefusal:
    """A44: the proposal path's failure taxonomy, audited rather than extended."""

    @staticmethod
    def _raise(label: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Drive the real provider code into the condition ``label`` names."""
        import sys

        import anthropic
        import httpx

        from sciagent.systems.llm.agent_sdk_provider import CONTAMINATING_VARIABLES
        from sciagent.systems.llm.anthropic_provider import AnthropicProvider

        if label == "sdk:contaminated_env":
            from sciagent.systems.llm.agent_sdk_provider import AgentSdkProvider

            name, _ = CONTAMINATING_VARIABLES[0]
            monkeypatch.setenv(name, "contaminated")
            AgentSdkProvider(runner=_stub_runner(_result())).complete("s", "b", SCHEMA)
        elif label == "sdk:running_event_loop":
            import asyncio

            provider = _agent_sdk(monkeypatch, runner=_stub_runner(_result()))

            async def inside() -> None:
                provider.complete("s", "b", SCHEMA)

            asyncio.run(inside())
        elif label == "sdk:foreign_provider":
            _agent_sdk(
                monkeypatch,
                runner=_stub_runner(
                    _result(model_usage={"bedrock/claude-opus-5": _model_usage()})
                ),
            ).complete("s", "b", SCHEMA)
        elif label == "sdk:no_model_usage":
            _agent_sdk(
                monkeypatch, runner=_stub_runner(_result(model_usage=None))
            ).complete("s", "b", SCHEMA)
        elif label == "sdk:wrong_model_served":
            _agent_sdk(
                monkeypatch,
                runner=_stub_runner(
                    _result(model_usage={"claude-sonnet-5": _model_usage()})
                ),
            ).complete("s", "b", SCHEMA)
        elif label == "sdk:no_result_message":
            _agent_sdk(monkeypatch, runner=_empty_runner()).complete("s", "b", SCHEMA)
        elif label == "sdk:session_error":
            _agent_sdk(
                monkeypatch,
                runner=_stub_runner(
                    _result(subtype="error_during_execution", is_error=True)
                ),
            ).complete("s", "b", SCHEMA)
        elif label == "sdk:refusal":
            _agent_sdk(
                monkeypatch, runner=_stub_runner(_result(stop_reason="refusal"))
            ).complete("s", "b", SCHEMA)
        elif label == "sdk:max_tokens":
            _agent_sdk(
                monkeypatch, runner=_stub_runner(_result(stop_reason="max_tokens"))
            ).complete("s", "b", SCHEMA)
        elif label == "sdk:structured_output_absent":
            _agent_sdk(
                monkeypatch, runner=_stub_runner(_result(structured_output=None))
            ).complete("s", "b", SCHEMA)
        elif label == "sdk:structured_output_not_object":
            _agent_sdk(
                monkeypatch, runner=_stub_runner(_result(structured_output=[1, 2]))
            ).complete("s", "b", SCHEMA)
        elif label == "sdk:session_died":
            import claude_agent_sdk

            def dying(*, prompt: str, options: Any) -> Any:
                raise claude_agent_sdk.CLIConnectionError("the session is gone")

            _agent_sdk(monkeypatch, runner=dying).complete("s", "b", SCHEMA)
        elif label == "sdk:not_installed":
            monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)
            _agent_sdk(monkeypatch).complete("s", "b", SCHEMA)
        elif label == "api:not_installed":
            monkeypatch.setitem(sys.modules, "anthropic", None)
            AnthropicProvider().complete("s", "b", SCHEMA)
        elif label == "api:refusal":
            AnthropicProvider(
                client=_client_returning(_stop_response("refusal"))
            ).complete("s", "b", SCHEMA)
        elif label == "api:max_tokens":
            AnthropicProvider(
                client=_client_returning(_stop_response("max_tokens"))
            ).complete("s", "b", SCHEMA)
        elif label == "api:no_text_block":
            AnthropicProvider(client=_client_returning(_blockless_response())).complete(
                "s", "b", SCHEMA
            )
        elif label == "api:not_json":
            AnthropicProvider(
                client=_client_returning(_text_response("not json at all"))
            ).complete("s", "b", SCHEMA)
        elif label == "api:json_not_object":
            AnthropicProvider(
                client=_client_returning(_text_response("[1, 2, 3]"))
            ).complete("s", "b", SCHEMA)
        elif label == "api:transport":
            request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
            AnthropicProvider(
                client=_client_raising(
                    anthropic.RateLimitError(
                        "429",
                        response=httpx.Response(429, request=request),
                        body=None,
                    )
                )
            ).complete("s", "b", SCHEMA)
        else:  # pragma: no cover - the table and this branch move together
            raise AssertionError(f"no builder for condition {label!r}")

    def test_a44_a_fault_is_not_scored_as_a_refusal(self) -> None:
        """The gate's first clause: three destinations, taken end to end.

        A model declining is recorded as ``"refused"`` with cause ``"declined"``.
        A dead session is recorded as ``"refused"`` too -- the tier does not move,
        which is T3 -- but with cause ``"transport"``, so the report can tell the
        two apart without ``yield_fraction``'s denominator moving. A fault of the
        machine reaches ``ProposalRecord`` at all: it propagates out of
        ``investigate``, and the run that would have scored it does not complete.

        Asserted through a real ``Hybrid`` on S11 rather than on the seam alone,
        because the thing that was wrong was not the taxonomy in the abstract --
        it was that a campaign carried on and wrote a number.
        """
        declining = Hybrid(
            closed_set(),
            _RaisingSource(ProviderError("the model declined", cause="declined")),
        )
        run = _run(declining)
        record = proposal_record(run.attempts or ())
        causes = proposal_causes(run.attempts or ())
        assert record.refused == 1, "a decline is a refusal, and the tier is frozen"
        assert causes.declined == 1
        assert causes.transport == 0

        dead = Hybrid(
            closed_set(),
            _RaisingSource(ProviderError("the session is gone", cause="transport")),
        )
        dead_run = _run(dead)
        dead_record = proposal_record(dead_run.attempts or ())
        dead_causes = proposal_causes(dead_run.attempts or ())
        assert dead_record.refused == 1, (
            "a dead session stays in the record, per the gate's own wording; "
            "moving it out would be a second denominator change"
        )
        assert dead_causes.transport == 1
        assert dead_causes.declined == 0

        # The two are indistinguishable in the scored record and distinguishable
        # beside it. That is precisely what T3 buys.
        assert record == dead_record
        assert causes != dead_causes

        faulted = Hybrid(
            closed_set(),
            _RaisingSource(SystemConfigurationError("the machine is misconfigured")),
        )
        with pytest.raises(SystemConfigurationError):
            _run(faulted)

        # The ``pytest.raises`` above is what carries this clause, and the
        # obvious assertion beside it does not: ``investigate`` clears
        # ``_attempts`` before ``_extend`` and assigns only after it returns, so
        # ``attempts == ()`` holds for *any* implementation that lets the fault
        # escape, including one that would have recorded it had it survived.
        # What can fail is the structural fact underneath -- that the class is
        # not one ``_propose_once`` converts -- so that is what is asserted.
        assert not isinstance(
            SystemConfigurationError("constructed"), CAUGHT_BY_HYBRID
        ), (
            "a machine fault is catchable as a proposal outcome, so it can be "
            "scored -- the whole defect this gate closes"
        )
        assert faulted.attempts == ()

    def test_a44_a_replay_miss_is_still_not_a_refusal(self) -> None:
        """The A35/A40 boundary, read from A44's side.

        ``docs/DECISIONS.md`` (2026-08-21) pins that this gate widens the catch
        by *kind* and never by moving up to ``ProposalError``.
        ``TranscriptMissError`` is a ``ProposalError`` sibling of ``ProviderError``
        precisely so that catching the latter cannot catch it, and A40's
        ``test_a40_a_replay_stops_on_a_miss_rather_than_recording_a_refusal``
        reads the same clause. A retiering that widened the catch would pass its
        own tests and silently break that one.
        """
        missing = Hybrid(
            closed_set(), _RaisingSource(TranscriptMissError("no such address"))
        )
        with pytest.raises(TranscriptMissError):
            _run(missing)

        # The hierarchy the boundary rests on, pinned from the other end. Both
        # of these are ``ProposalError`` subclasses that must not be caught, so
        # the family cannot be the catch set and reparenting either of them out
        # of it is not an available fix -- ``sciagent.systems.llm.transcripts``
        # guards on ``except ProposalError`` and would silently change meaning.
        assert issubclass(TranscriptMissError, ProposalError)
        assert issubclass(ProviderUnavailableError, ProposalError)
        assert not issubclass(TranscriptMissError, tuple(CAUGHT_BY_HYBRID))
        assert not issubclass(ProviderUnavailableError, tuple(CAUGHT_BY_HYBRID))

    def test_a44_the_break_set_is_unmoved(self) -> None:
        """The hazard T3 exists to avoid, in the one place it could still land.

        ``_extend`` breaks on ``"refused"`` and continues on the other four, so
        which outcome a condition is filed under decides how many requests a run
        makes -- and therefore ``requested``, and therefore ``yield_fraction``.
        That is why the retiering moves faults *out* of the record entirely and
        re-files nothing *between* the five tiers: filing a malformed provider
        response under ``malformed`` instead of ``refused`` moves no field
        definition and still moves the denominator, which is T2 wearing T3's
        clothes.

        A recorded campaign would have caught this, and is what the gate's
        unreadable third clause was reaching for. Constructed instead: a source
        that fails twice is asked twice where the outcome does not break, and
        once where it does.
        """
        breaking = Hybrid(
            closed_set(),
            _RaisingSource(ProviderError("declined", cause="declined")),
        )
        assert len(_run(breaking).attempts or ()) == 1, (
            "a refusal no longer breaks _extend, which changes requested and "
            "so yield_fraction on every recorded run that saw one"
        )

        continuing = Hybrid(
            closed_set(),
            _RaisingSource(MalformedProposalError("does not decode")),
        )
        assert len(_run(continuing).attempts or ()) == MAX_PROPOSALS, (
            "a malformed draft now breaks _extend; the denominator moves the "
            "other way and the retiering has re-filed between scoring tiers"
        )

    @pytest.mark.parametrize(("label", "expected", "cause"), CONDITIONS)
    def test_a44_every_provider_condition_carries_exactly_one_cause(
        self,
        monkeypatch: pytest.MonkeyPatch,
        label: str,
        expected: type[Exception],
        cause: str | None,
    ) -> None:
        """The enumeration, against the real provider code.

        ``docs/OPEN-DECISIONS.md`` §2 tables eighteen conditions across the two
        backends; two are scientific events and the rest were counted as though
        they were. All eighteen are here, plus the two session-death rows §2
        does not table because they arrive as exceptions rather than as a
        ``ResultMessage`` field. Every row is driven here through the provider
        that raises it, so a row re-tiered without its test moving fails rather
        than being quietly reclassified.

        A row with ``cause is None`` propagates, and what that means is
        precisely *not* "outside ``ProposalError``".
        :class:`~sciagent.core.errors.ProviderUnavailableError` is a
        ``ProposalError`` **by design** -- a sibling of ``ProviderError`` under
        it, exactly as ``TranscriptMissError`` is -- and the family is the wrong
        instrument here. What makes a condition propagate is that ``Hybrid`` does
        not catch it, and the catch set is two named classes.

        So the check is against :data:`CAUGHT_BY_HYBRID`, not against the base.
        Getting this wrong is worse than leaving it out: a red saying "raises a
        ProposalError" invites the implementer to reparent
        ``ProviderUnavailableError`` off the family, which would silently change
        the ``except ProposalError`` guards in
        :mod:`sciagent.systems.llm.transcripts` and break the A35/A40 boundary
        this gate is meant to hold.
        """
        with pytest.raises(expected) as raised:
            self._raise(label, monkeypatch)

        if cause is None:
            assert not isinstance(raised.value, CAUGHT_BY_HYBRID), (
                f"{label} raises a class Hybrid._propose_once catches, so a "
                f"fault of the machine is converted into a proposal outcome and "
                f"scored"
            )
            assert not hasattr(raised.value, "cause"), (
                f"{label} propagates, so there is no attempt to carry a cause "
                f"and nothing should offer one"
            )
        else:
            assert isinstance(raised.value, CAUGHT_BY_HYBRID)
            assert getattr(raised.value, "cause", None) == cause
            assert cause in PROPOSAL_CAUSES

    def test_a44_a_cause_disagreeing_with_its_outcome_is_refused(self) -> None:
        """ "Exactly one cause" is structural, not a convention held by hand.

        Every cause belongs to exactly one scoring tier, so an attempt whose
        outcome and cause disagree is a contradiction on its face and raises
        rather than being counted. Without this, a cause could be attached to the
        wrong tier and the two aggregations would silently disagree about the
        same run -- the failure ``PROPOSAL_OUTCOMES`` being *derived* from
        ``ProposalRecord``'s fields already makes unrepresentable one level up.
        """
        assert set(OUTCOME_OF_CAUSE) == set(PROPOSAL_CAUSES)
        assert set(OUTCOME_OF_CAUSE.values()) == set(PROPOSAL_OUTCOMES)

        contradiction = ProposalAttempt(None, None, "admitted", "declined", "both")
        with pytest.raises(InvestigationError, match="declined"):
            proposal_causes((contradiction,))

        unknown = ProposalAttempt(None, None, "refused", "vibes", "not a cause")
        with pytest.raises(InvestigationError, match="vibes"):
            proposal_causes((unknown,))

    def test_a44_a_grammar_refusal_at_apply_time_propagates(self) -> None:
        """The decision the entry deferred to this change, taken and pinned.

        ``CANDIDATE_FAULTS`` enumerates ``ProgramError`` members, but ``simulate``
        reaches ``EditGrammar.apply``, which raises ``EditNotInGrammarError`` -- a
        ``GrammarError``. Nothing caught it, so it escaped ``_admit`` and would
        stop a campaign; it is unreachable today only because
        ``agent_grammar() ⊆ edit_grammar()``, which is the "wiring, not a promise"
        argument the sibling change in ``provider.py`` already rejected.

        It **propagates**. Reaching apply-time refusal means the agent grammar
        licensed a structure the edit grammar refuses, which is a divergence
        between two framework-supplied grammars rather than a fact about the
        candidate -- the same reason ``DeterminismError`` is deliberately absent
        from ``CANDIDATE_FAULTS``. Widening that tuple would have a campaign carry
        on and score a wiring bug as a scientific outcome.

        Two halves, because there are two places it could have been absorbed: the
        table's guard must not convert it to ``StructureNotMeasurableError``, and
        ``_admit`` must not convert it to an outcome. The second is the gate's
        "lands where this entry decides rather than escaping ``_admit``" -- pinned
        by type, which is what makes the landing decided rather than accidental.
        """

        def refusing_grammar(*_args: Any) -> Any:
            raise EditNotInGrammarError("the edit grammar does not license this")

        candidate = decode(
            AGENT_GRAMMAR,
            structural_menu(AGENT_GRAMMAR),
            draft_from_payload(fixed_payload(1, (32, 55, 29, 22), name="off_table")),
        )
        assert not _table().holds(candidate), (
            "the table already holds this structure, so with_structure would "
            "return before reaching the guard under test"
        )
        # This one assertion carries both halves. ``EditNotInGrammarError`` is a
        # ``GrammarError`` and ``StructureNotMeasurableError`` a ``MetricError``,
        # disjoint subtrees -- so a conversion to the measurement tier would
        # raise the wrong class here and fail, and a separate "and it was not
        # absorbed" check below would be a second call that cannot say anything
        # the first did not.
        assert not issubclass(EditNotInGrammarError, StructureNotMeasurableError)
        with pytest.raises(EditNotInGrammarError):
            _table().with_structure(candidate, refusing_grammar)

        investigation = _StubInvestigation(
            EditNotInGrammarError("the edit grammar does not license this")
        )
        with pytest.raises(EditNotInGrammarError):
            # ``_admit`` is a staticmethod reading three members of what it is
            # handed, all of which the stub supplies -- see its docstring for why
            # a real investigation is the wrong instrument here. The cast states
            # that rather than hiding it; the alternative is not a narrower
            # annotation but a full scenario driven to reach one except clause.
            Hybrid._admit(cast(Investigation, investigation), _proposal())

    def test_a44_the_scoring_record_is_unmoved(self) -> None:
        """The gate's third clause: T3 keeps the five fields, and spends no bump.

        The ``yield_fraction`` values below are literals computed under the
        pre-change taxonomy. What the last of them asserts is the property a
        recorded campaign would have been evidence of: re-tagging a refusal
        across every cause the retiering adds moves nothing, because the cause
        never enters the denominator.
        """
        assert PROPOSAL_OUTCOMES == (
            "admitted",
            "duplicate",
            "refused",
            "malformed",
            "unmeasurable",
        )
        assert tuple(field.name for field in fields(ProposalRecord)) == (
            PROPOSAL_OUTCOMES
        )
        assert not set(PROPOSAL_CAUSES) & set(PROPOSAL_OUTCOMES) - {
            "admitted",
            "duplicate",
        }, "a cause named for a tier it does not belong to would confuse the two"

        shapes: tuple[tuple[ProposalRecord, float | None], ...] = (
            (ProposalRecord(0, 0, 0, 0, 0), None),
            (ProposalRecord(2, 0, 0, 0, 0), 1.0),
            (ProposalRecord(0, 0, 2, 0, 0), 0.0),
            (ProposalRecord(1, 0, 1, 0, 0), 0.5),
            (ProposalRecord(1, 1, 0, 1, 1), 0.25),
        )
        for record, expected in shapes:
            assert record.yield_fraction == expected
            assert record.requested == sum(
                getattr(record, name) for name in PROPOSAL_OUTCOMES
            )

        # The property the recorded campaign would have pinned: the cause a
        # refusal carries does not reach the fraction.
        for cause in (
            name for name, tier in OUTCOME_OF_CAUSE.items() if tier == "refused"
        ):
            attempts = (
                ProposalAttempt("a", HypothesisId("h"), "admitted", "admitted", ""),
                ProposalAttempt(None, None, "refused", cause, ""),
            )
            assert proposal_record(attempts).yield_fraction == 0.5

        # And the breakdown accounts for every attempt without being a second
        # denominator: it sums to ``requested`` and offers no fraction of its own.
        one_of_each = tuple(
            ProposalAttempt(None, None, OUTCOME_OF_CAUSE[cause], cause, "")
            for cause in PROPOSAL_CAUSES
        )
        causes = proposal_causes(one_of_each)
        assert (
            sum(getattr(causes, name) for name in PROPOSAL_CAUSES)
            == proposal_record(one_of_each).requested
        )
        assert not hasattr(causes, "yield_fraction")
        assert not hasattr(causes, "requested")

        # The version clause, pinned against the frozen ledger rather than
        # against a constant. ``METRIC_VERSION`` reaches the recorded address
        # through ``MetricRegistry.version``, and every one of the 1,120 rows in
        # ``.cache/campaign/spec9.db`` carries the digest below -- read off that
        # ledger and written here as a literal, because ``.cache/`` is
        # gitignored and a test that reads it would pass by absence on a clean
        # machine. Comparing the *constant* would be the weaker check: it goes
        # green on any change that leaves the string alone while moving what the
        # catalogue addresses over, which is most of what could go wrong here.
        assert (
            metric_registry().version == "metrics/9b1c54c9d49f49f656c30e32d21d4a7b"
        ), (
            "the metric version moved, so every recorded cell is re-addressed and "
            "every cached table invalidated -- the 3m11s rebuild on every machine "
            "that T3 exists to not spend"
        )
        assert METRIC_VERSION == "1.2.0"
        # Not named by the gate, and asserted anyway: A44 touches no estimator
        # and no dimension, so the term that *is* allowed to move for a scoring
        # change must not move for this one either.
        assert DIMENSION_VERSION == "spec8/3"

    def test_a44_an_allowance_above_two_is_refused(self) -> None:
        """The break asymmetry, settled in the same change as the entry requires.

        ``_extend`` breaks on ``"refused"`` and continues on the other four, so
        the denominator depends on which outcome arrives. Harmless at
        ``max_proposals = 2``, where declining can only lower the ratio; at 3 or
        more a model unable to produce a second admission would score strictly
        higher by declining, which is a metric rewarding the thing it is meant to
        measure the absence of.

        The break rule is deliberately **not** changed -- that would move a number
        the recorded matrix reported, which is the whole thing T3 exists to avoid.
        What changes is that the ceiling is a guard rather than a default, so
        raising the allowance is a decision somebody takes with this asymmetry in
        front of them.
        """
        assert MAX_PROPOSALS == 2
        Hybrid(closed_set(), _RaisingSource(ProviderError("x", cause="declined")))
        with pytest.raises(SystemConfigurationError, match="asymmetr"):
            Hybrid(
                closed_set(),
                _RaisingSource(ProviderError("x", cause="declined")),
                max_proposals=3,
            )


class TestTheBreakdownIsNotScoringApparatus:
    """SPEC §1 invariant 2, for the structure this gate adds.

    The framework writes numbers and agents write structure. A parallel
    breakdown of *why* a call failed is a number, so the thing worth checking is
    that nothing an agent authored decides which bin it lands in.
    """

    def test_a_cause_comes_from_the_exception_and_not_from_its_message(self) -> None:
        """The tag is set at the raise site, which is framework code.

        The alternative -- reading the cause out of the exception's message, or
        out of the ``detail`` string an attempt carries -- would let a provider's
        prose decide a count the framework reports, which is the same defect
        ``agency.py`` refuses when it declines to read ``rationale``.
        """
        error = ProviderError("the model declined", cause="declined")
        assert error.cause == "declined"

        # Two errors whose prose says opposite things and whose tags do not
        # follow it. If the cause were derived from the message -- or from the
        # ``detail`` an attempt carries, which is the same string -- these would
        # come back swapped, and a provider's wording would decide a count the
        # framework reports.
        misleading = ProviderError("transport failure, honestly", cause="declined")
        assert misleading.cause == "declined"
        inverse = ProviderError("the model declined, honestly", cause="transport")
        assert inverse.cause == "transport"

        # And the tag is not optional, because a default would silently pool
        # every un-tagged raise site into one bin that reads as a measurement.
        # Called through an untyped alias rather than under a suppression: the
        # claim is that the argument is required at *runtime*, and `mypy`
        # rejecting the direct call is the static half of the same fact rather
        # than something to silence.
        untyped: Callable[..., ProviderError] = ProviderError
        with pytest.raises(TypeError):
            untyped("no tag at all")

        # And it survives a round trip, which the required keyword breaks by
        # default: an exception rebuilds from ``args``, and ``args`` holds the
        # message alone.
        restored = pickle.loads(pickle.dumps(ProviderError("x", cause="transport")))
        assert restored.cause == "transport"

    def test_every_cause_is_reachable_from_some_enumerated_condition(self) -> None:
        """A cause no condition produces is a bin that can only ever read zero.

        Not merely untidy: ``PROPOSAL_CAUSES`` is what the report enumerates, so
        a dead bin is a column of zeros presented as a measurement. The two
        causes with no provider row are the ones raised inside ``Hybrid`` itself
        -- see the assertion's own list.
        """
        from_providers = {cause for _, _, cause in CONDITIONS if cause is not None}
        from_hybrid = {"admitted", "duplicate", "budget", "undecodable", "measurement"}
        # ``exhausted`` belongs to neither: it is raised by ScriptedProvider,
        # which is a source rather than a backend, and it is *driven* below
        # rather than merely named -- a set entry added to quiet this assertion
        # would leave exactly the dead bin the test exists to forbid.
        assert set(PROPOSAL_CAUSES) == from_providers | from_hybrid | {"exhausted"}

    def test_an_exhausted_script_is_not_counted_as_a_decline(self) -> None:
        """The bin the whole retiering rests on must not hold fixture length.

        ``_layer(script=())`` is how this repository simulates a refusal without
        reaching a provider, so tagging exhaustion ``"declined"`` would have put
        a script's length into the one count A44 calls unambiguously a fact
        about a model -- in every such run in the suite. Found by review, after
        the first implementation did exactly that.

        The *outcome* is still ``"refused"``, which is what keeps ``_extend``'s
        break behaviour and therefore ``yield_fraction`` where they were.
        """
        system = Hybrid(
            closed_set(),
            ProposalLayer(
                ScriptedProvider([]), AGENT_GRAMMAR, TranscriptStore(mode=RECORD)
            ),
        )
        run = _run(system)
        record = proposal_record(run.attempts or ())
        causes = proposal_causes(run.attempts or ())
        assert record.refused == 1
        assert causes.exhausted == 1
        assert causes.declined == 0, (
            "an exhausted script inflated the one cause the gate calls "
            "unambiguously a scientific event"
        )


class TestTheBreakdownSurvivesPooling:
    """`_pool` sums a second structure, and nothing else in the suite reads it.

    Found by review: `AgencyReport.causes` and `AgencyMetrics.causes` had no test
    touching them, so a wrong sum in `agency._pool` passed the whole suite. The
    scored `ProposalRecord` beside it is summed field by field and this one by a
    comprehension over `PROPOSAL_CAUSES`; the two forms agree here or nowhere.
    """

    @staticmethod
    def _metrics(system: str, attempts: tuple[ProposalAttempt, ...]) -> AgencyMetrics:
        return AgencyMetrics(
            system=system,
            scenario=ScenarioId("S11"),
            experiments=1,
            entertained=0,
            escalated=0,
            proposals=proposal_record(attempts),
            causes=proposal_causes(attempts),
        )

    def test_pooling_sums_every_cause_and_agrees_with_the_record(self) -> None:
        declined = ProposalAttempt(None, None, "refused", "declined", "")
        transport = ProposalAttempt(None, None, "refused", "transport", "")
        duplicate = ProposalAttempt(
            "a", HypothesisId("h"), "duplicate", "duplicate", ""
        )

        rows = agency_report(
            [
                self._metrics("V7", (declined, transport)),
                self._metrics("V7", (declined, duplicate)),
                self._metrics("B6", (transport,)),
            ]
        )
        pooled = {row.system: row for row in rows}
        assert tuple(row.system for row in rows) == ("B6", "V7"), (
            "agency_report must stay sorted by system name (invariant 3)"
        )

        v7 = pooled["V7"].causes
        assert v7 is not None
        assert (v7.declined, v7.transport, v7.duplicate) == (2, 1, 1)
        assert all(
            getattr(v7, name) == 0
            for name in PROPOSAL_CAUSES
            if name not in {"declined", "transport", "duplicate"}
        )

        # The two summing forms are independent code paths over the same runs,
        # so their totals agreeing is the check that neither drifted.
        record = pooled["V7"].proposals
        assert record is not None
        assert sum(getattr(v7, name) for name in PROPOSAL_CAUSES) == record.requested
        assert record.refused == v7.declined + v7.transport

        b6 = pooled["B6"].causes
        assert b6 is not None
        assert b6.transport == 1

    def test_a_system_with_no_layer_pools_to_no_breakdown(self) -> None:
        """`causes` is `None` exactly where `proposals` is, and not zero.

        A zero breakdown would pool into an aggregate as though it were a layer
        that was asked and produced nothing -- the same distinction
        `ProposalRecord` already draws, and the reason both fields are optional.
        """
        bare = AgencyMetrics(
            system="B1",
            scenario=ScenarioId("S11"),
            experiments=3,
            entertained=1,
            escalated=0,
            proposals=None,
            causes=None,
        )
        (row,) = agency_report([bare])
        assert row.proposals is None
        assert row.causes is None


class TestTheRecordedOutcomesStillHold:
    """The four outcomes that already had tests keep the tiers they had.

    A retiering is exactly the change that can move something nobody was looking
    at, and ``tests/test_hybrid.py`` asserts these tiers against *live-discovered*
    failures -- the two escapes of 2026-08-18. Restated here against the cause
    layer so that a future widening has to break this module too.
    """

    def test_a_malformed_draft_is_undecodable_and_not_a_refusal(self) -> None:
        broken = dict(fixed_payload(0, ()))
        broken["edits"] = [{"structure": 999, "parameters": []}]
        system = Hybrid(
            closed_set(),
            ProposalLayer(
                ScriptedProvider([broken]),
                AGENT_GRAMMAR,
                TranscriptStore(mode=RECORD),
            ),
        )
        run = _run(system)
        record = proposal_record(run.attempts or ())
        causes = proposal_causes(run.attempts or ())
        assert record.malformed == 1
        assert causes.undecodable == 1

    def test_an_unmeasurable_structure_keeps_its_own_tier(self) -> None:
        payload = fixed_payload(1, (32, 55, 29, 22), name="unmeasurable_regime")
        system = Hybrid(
            closed_set(),
            ProposalLayer(
                ScriptedProvider([dict(payload)]),
                AGENT_GRAMMAR,
                TranscriptStore(mode=RECORD),
            ),
        )
        run = _run(system)
        record = proposal_record(run.attempts or ())
        causes = proposal_causes(run.attempts or ())
        assert record.unmeasurable == 1
        assert causes.measurement == 1

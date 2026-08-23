"""Measure what one proposal costs, so item 15's recording run can be budgeted.

``docs/DECISIONS.md`` 2026-08-15 ("a second live backend, billed to a
subscription") left three things open; the 2026-08-16 entry on the call address
closes two and records that this is the only one still open::

    Subscription rate limits across a recording run are unmeasured. Item 15 is
    ~1120 proposals; Max has 5-hour and weekly caps. Whether a matrix fits, or
    needs to be spread over days, is a pilot measurement nobody has taken.

What this measures, and what it does not
----------------------------------------

It records ``--calls`` real proposals through
:class:`~sciagent.systems.llm.agent_sdk_provider.AgentSdkProvider` and reports,
per call, the wall-clock, the cost the session charged and the tokens it
consumed. From those it projects a recording run of any size.

It does **not** observe a cap biting. At the default twenty calls it is not
meant to, so the projection is an estimate against limits whose size is still
unmeasured, and a number off this script must be quoted as one. What it would
do if a cap did bite is stop and report the failed session verbatim -- the
error shape is worth having, because a resumable campaign driver has to
recognise it (``docs/BACKLOG.md``, "A resumable campaign driver").

Why it drives the proposal layer rather than V7
------------------------------------------------

:class:`~sciagent.systems.hybrid.Hybrid` proposes only when the scoped Stage A
check reports the entertained space inadequate (SPEC F6), and ``docs/DECISIONS``
2026-08-16 ("the gate is threaded") measures that gate opening on **S11 and
nowhere else** in twelve scenarios. A pilot driven through ``investigate`` would
therefore price one scenario's brief repeatedly and measure nothing about the
other eleven.

The quantity wanted here is the cost of *a proposal*, which does not depend on
the gate, so each investigation is advanced to exactly the state V7 proposes
from -- library entertained, half the budget spent by one-step-greedy BOED --
and the layer is asked directly. The brief is therefore the one V7 would have
sent, on every scenario rather than on the one that fires.

Nothing shared is written. The executor's store is in memory, no structure is
entertained into the engine, and the transcripts land under ``.cache/`` --
gitignored, and deliberately not the committed corpus: the brief depends on the
structural menu, ``METRIC_VERSION`` and the Stage A allocation, all of which the
2026-08-16 entries leave in flux, so these addresses would likely go stale
before item 15 records against them.

Usage::

    uv run python scripts/rate_limit_pilot.py --dry-run   # free, no model call
    uv run python scripts/rate_limit_pilot.py --calls 20
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Final

from claude_agent_sdk import Message, ResultMessage, query

from environments.pointproc.outcomes import (
    closed_set,
    executor,
    simulator,
    slice_designs,
)
from environments.pointproc.scenarios import slice_scenarios
from sciagent.core.errors import (
    InvalidEditError,
    MalformedProposalError,
    ProposalError,
    ProviderError,
    ProviderUnavailableError,
    SystemConfigurationError,
)
from sciagent.core.types import ExperimentTemplateId, HypothesisId, Seed
from sciagent.eval.campaign import stage_a_id
from sciagent.eval.scenarios import Scenario
from sciagent.experiments import boed
from sciagent.experiments.dsl import ExperimentDesign
from sciagent.experiments.executor import Executor
from sciagent.inference.empirical import (
    EmpiricalTable,
    EmpiricalTableEngine,
    replicate_seed,
)
from sciagent.registry.store import ExperimentStore
from sciagent.systems.ablation import ABLATION_SYSTEM_PROMPT
from sciagent.systems.base import Investigation, entertain, null_seeded_graph
from sciagent.systems.llm import (
    RECORD,
    Memory,
    ProposalLayer,
    ScriptedProvider,
    TranscriptStore,
    fixed_payload,
    render_brief,
    structural_menu,
)
from sciagent.systems.llm.agent_sdk_provider import AgentSdkProvider

# The slice's empirical table and its two grammars live in `tests/`, and are
# reached by path rather than restated. Rebuilding the table costs 3m11s cold
# against 1.055s through the shared cache, so a second copy of the acquisition
# logic here would be a second cache to keep warm as well as a second thing to
# keep correct.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from slice_tables import AGENT_GRAMMAR, GRAMMAR, METRICS, gate_table

#: The denominator the open item is written in: 56 cells at twenty seeds. It is
#: an investigation count and **not** a proposal count, which is the confusion
#: this script exists partly to remove -- reported first because it is the figure
#: a reader arrives with.
MATRIX_INVESTIGATIONS = 1120

#: What the matrix actually asks a model, derived rather than assumed.
#:
#: Only :class:`~sciagent.systems.hybrid.Hybrid` holds a proposal layer, so of
#: SPEC §9's 56 cells the model-bearing ones are V7's twelve plus V3/V4's six --
#: 18 cells, 360 investigations. Of those, ``docs/DECISIONS.md`` 2026-08-16 ("the
#: gate is threaded") measures the Stage A gate opening on **S11 alone**: V7 on
#: S11 is one cell, V3 and V4 on S11 are two more. Three cells at twenty seeds,
#: at ``max_proposals=2``, is 120 calls.
#:
#: Both bounds are worth keeping in view. Nothing fires anywhere else at the
#: scenarios' own seeds, so the floor is real; but that measurement is *one seed
#: per scenario*, S11's probe reads 0.0112 against an alpha of 0.05 and the
#: nearest in-library miss is 0.0595, so seeds may move a cell either way. The
#: ceiling if every model-bearing cell fired at every seed is 720.
MATRIX_PROPOSALS = 120
MATRIX_PROPOSALS_CEILING = 720

#: Memories, in the order the work list cycles them. ``BOTH`` first because that
#: is V7's; the other two are item 13's ablation arms and render a differently
#: sized brief, which is the thing being priced.
MEMORY_ORDER: tuple[Memory, ...] = (Memory.BOTH, Memory.RAW, Memory.GRAPH)

#: Distance between consecutive seeds of one scenario. The same grid
#: ``scripts/stage_a_seed_sweep.py`` sweeps, so a pair priced here is a pair
#: counted there, and ``k = 0`` is the scenario's own recorded seed.
STRIDE = 1_000_000


@dataclass(frozen=True, slots=True)
class CallRecord:
    """One proposal request, and everything it cost."""

    index: int
    scenario: str
    memory: str
    outcome: str
    """``"ok"``, or the name of the exception that ended the run."""

    brief_chars: int
    address: str
    wall_s: float
    """Measured around :meth:`ProposalLayer.propose`, so it includes process
    spawn, the model call and decoding -- which is what a recording run pays."""

    duration_ms: int
    duration_api_ms: int
    num_turns: int
    cost_usd: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    served_models: str
    detail: str
    """Empty on success; the provider's message otherwise."""


class UsageTap:
    """``claude_agent_sdk.query``, keeping the result message it streams past.

    The provider's :class:`~sciagent.systems.llm.transcripts.Completion` carries
    the payload and a provenance of two strings, which is the right thing for a
    corpus and is not enough to price a recording run -- cost and token counts
    live on the SDK's ``ResultMessage`` and are dropped before the layer sees
    them. Rather than widen what the provider records, this tees the stream:
    ``AgentSdkProvider(runner=...)`` exists so a caller can supply the query
    function, the real one is what is called, and every message passes through
    untouched. The provider is therefore exercised exactly as a recording run
    would exercise it.
    """

    __slots__ = ("last",)

    def __init__(self) -> None:
        self.last: ResultMessage | None = None

    def __call__(self, **kwargs: Any) -> AsyncIterator[Message]:
        """Return the SDK's stream, with result messages kept as they pass."""
        return self._stream(**kwargs)

    async def _stream(self, **kwargs: Any) -> AsyncIterator[Message]:
        async for message in query(**kwargs):
            if isinstance(message, ResultMessage):
                self.last = message
            yield message


#: Outcome names meaning no answer was served and the run stopped.
#:
#: Both, because ``CallRecord.outcome`` holds ``type(error).__name__`` and
#: ``ProviderUnavailableError`` is a *sibling* of ``ProviderError`` rather than a
#: subclass -- so every comparison against the one name silently excludes the
#: other. Three separate comparisons in this file needed it, which is why it is a
#: constant rather than a repeated literal: a stopped run was being counted as
#: billed, and the "THE RUN STOPPED" block that is the pilot's whole output on a
#: rate cap would not have printed.
STOPPED_OUTCOMES: Final = ("ProviderError", "ProviderUnavailableError")


def _stored_address(store: TranscriptStore, before: set[str]) -> str:
    """Return the address this call added to ``store``, or ``""`` if none.

    A decode failure is the case this exists for: the payload was resolved and
    stored before ``_build`` rejected it, so there *is* a transcript, and the
    report's "inspect these" listing is worth nothing without its address.

    Since gate A35 a ``ProviderError`` also leaves one. It used to be raised
    before anything was stored, and this docstring said so; ``resolve`` now
    records the refusal as a transcript in its own right and re-raises, so the
    refusal has an address and the listing can point at it. Nothing here needed
    changing for that -- the set difference was always the question -- but the
    sentence claiming otherwise did.

    An empty string still means "no transcript to inspect", and it is still the
    honest answer for a machine fault: ``ProviderUnavailableError`` and
    ``SystemConfigurationError`` are not converted and store nothing.
    """
    added = set(store.addresses()) - before
    return added.pop() if len(added) == 1 else ""


def work_list(calls: int, seeds: int = 20) -> tuple[tuple[Scenario, Memory], ...]:
    """Return ``calls`` (scenario, memory) pairs whose gate actually opens.

    Guarantees every pair is one whose gate opens on *this* grid. A brief is
    only sent when :meth:`~sciagent.systems.base.Investigation.ppc` reports the
    entertained space inadequate, so pricing a scenario whose gate stays shut
    prices a call V7 never makes. The scenarios are walked seed by seed -- seed
    ``k`` being ``scenario.seed + STRIDE * k``, the seed sweep's grid -- and a
    pair is kept only if its gate fires.

    **Not the matrix's grid.** An earlier wording promised "every pair is one a
    recording run would really pay for", and that is false:
    :func:`sciagent.eval.matrix.replicate_seeds` hashes rather than adding a
    stride, and the two seed sets were measured on 2026-08-18 to overlap in 0 of
    20. What this prices is the cost of *a proposal*, which is what the pilot is
    for and does not depend on which seeds fire. Any per-run total derived from
    the pair count -- ``MATRIX_PROPOSALS`` and its ceiling below -- is a figure
    about this grid, not a budget for §9's matrix, whose measured count is 112.

    Memory is cycled across the kept pairs rather than nested outside them, so a
    short run still covers more than one representation.

    **This screening is not cheap, and an earlier wording said it was.** It
    claimed "a gate evaluation per candidate", reasoning that the expensive part
    of an investigation is simulating a *proposed* structure and nothing is
    proposed here. The second half is true and the first is not:
    :func:`at_proposal_time` spends half the scenario budget through BOED before
    the gate can be read at all. A twenty-call run therefore screens on the order
    of 240 half-investigations in one process, on a machine ``docs/DECISIONS.md``
    records dying at around 300. Raising ``--calls`` or ``seeds`` moves that
    product, so treat both as memory settings and not only as quota settings.
    """
    scenarios = slice_scenarios()
    table = gate_table()
    kept: list[tuple[Scenario, Memory]] = []
    for k in range(seeds):
        for base in scenarios:
            if len(kept) == calls:
                return tuple(kept)
            candidate = replace(base, seed=Seed(int(base.seed) + STRIDE * k))
            if at_proposal_time(candidate, table).ppc().inadequate:
                kept.append((candidate, MEMORY_ORDER[len(kept) % len(MEMORY_ORDER)]))
    if len(kept) < calls:
        raise ValueError(
            f"{calls} calls were asked for and only {len(kept)} of the "
            f"{len(scenarios) * seeds} (scenario, seed) pairs searched open the "
            f"Stage A gate; raise --seeds, or ask for fewer calls"
        )
    return tuple(kept)


def at_proposal_time(the_scenario: Scenario, table: EmpiricalTable) -> Investigation:
    """Return an investigation in the state V7 proposes from.

    The Stage A probe taken, the library entertained, and half the budget spent
    by one-step-greedy BOED -- which is
    :meth:`~sciagent.systems.hybrid.Hybrid.investigate`'s state at the moment it
    consults the proposal layer. Nothing is registered anywhere shared: the
    executor's store is in memory.

    **The Stage A part is not optional and was missing for a day.** The brief
    renders the conventional adequacy verdict (``encoding._check_section``), and
    :meth:`~sciagent.systems.base.Investigation.ppc` scopes that check to the
    probe only when ``stage_a`` names it. Without it the model is shown an
    unscoped check over the BOED experiments, which on eleven of twelve scenarios
    reads *adequate* -- so the pilot was pricing a brief that told the model
    nothing was wrong while asking it to propose structure, which is close to the
    opposite of V7's situation.
    """
    the_executor = executor(
        GRAMMAR,
        store=ExperimentStore.in_memory(),
        budget=the_scenario.budget,
    )
    graph = null_seeded_graph(AGENT_GRAMMAR, METRICS, table, slice_designs())
    engine = EmpiricalTableEngine(graph, table, simulate=simulator(GRAMMAR))
    investigation = Investigation(
        scenario_id=the_scenario.id,
        designs=the_scenario.designs,
        truth=the_scenario.executed,
        executor=the_executor,
        engine=engine,
        graph=graph,
        seed=the_scenario.seed,
        stage_a=stage_a_id(the_scenario) if the_scenario.stage_a is not None else None,
    )
    _take_stage_a(the_scenario, executor=the_executor, engine=engine)
    entertain(investigation, closed_set())
    total = int(investigation.budget.remaining)
    _select(investigation, (total + 1) // 2)
    return investigation


def _take_stage_a(
    the_scenario: Scenario, *, executor: Executor, engine: EmpiricalTableEngine
) -> None:
    """Take the scenario's Stage A reading, as ``run_scenario`` does.

    Mirrors ``campaign._run_stage_a``, which is private. Through
    :meth:`~sciagent.inference.empirical.EmpiricalTableEngine.record_probe`
    rather than ``record``, so the reading reaches the adequacy check and
    nothing else -- routing it through ``record`` would put it in the posterior,
    which that function's docstring records as a real defect that cost B5 two
    thirds of its structural recovery. If the two ever disagree, this is the
    copy that is wrong.
    """
    if the_scenario.stage_a is None:
        return
    design = the_scenario.stage_a
    seed = replicate_seed(the_scenario.seed, f"stage_a:{design.id}", 0)
    result = executor.measure(design, the_scenario.executed, seed)
    engine.record_probe(stage_a_id(the_scenario), design.template(), result)


def _select(investigation: Investigation, steps: int) -> None:
    """Spend ``steps`` experiments by expected information gain.

    V7's ``_select``, restated rather than reached for: it is private, and a
    pilot that called into a system's internals would break the next time that
    system's loop was rearranged. If the two ever disagree, this is the copy
    that is wrong.
    """
    if steps < 1:
        return
    by_id: dict[ExperimentTemplateId, ExperimentDesign] = {
        design.id: design for design in investigation.designs
    }

    def observe(_index: int, template: ExperimentTemplateId) -> int:
        design = by_id[template]
        result = investigation.run(design, targets=_live(investigation))
        return design.template().outcome.cell_of(result.result)

    boed.plan(investigation.engine, tuple(by_id), observe, steps=steps)


def _live(investigation: Investigation) -> tuple[HypothesisId, ...]:
    """Return every live hypothesis, which is what V7 declares as targets."""
    return tuple(sorted(investigation.engine.live))


def run_pilot(
    *,
    calls: int,
    effort: str,
    model: str,
    dry_run: bool,
) -> tuple[list[CallRecord], TranscriptStore]:
    """Record ``calls`` proposals, stopping at the first provider failure.

    Guarantees that a failure ends the run rather than being retried: a cap that
    bit on call eleven makes calls twelve onward measurements of a rate-limited
    account, and averaging those into a cost per proposal would understate every
    projection drawn from them.
    """
    table = gate_table()
    store = TranscriptStore(mode=RECORD)
    tap = UsageTap()
    provider: AgentSdkProvider | ScriptedProvider = (
        ScriptedProvider(_scripted_policy())
        if dry_run
        else AgentSdkProvider(model=model, effort=_effort(effort), runner=tap)
    )
    # BOTH is V7's and takes the default prompt; RAW and GRAPH are item 13's V3
    # and V4, whose arms are built with ABLATION_SYSTEM_PROMPT. Handing them the
    # default would price a brief no real ablation run sends, and would record it
    # at an address no such run could resolve -- the system prompt is part of the
    # address.
    layers = {
        memory: ProposalLayer(
            provider,
            AGENT_GRAMMAR,
            store,
            memory=memory,
            system_prompt="" if memory is Memory.BOTH else ABLATION_SYSTEM_PROMPT,
        )
        for memory in MEMORY_ORDER
    }

    records: list[CallRecord] = []
    investigations: dict[str, Investigation] = {}
    for index, (the_scenario, memory) in enumerate(work_list(calls)):
        name = str(the_scenario.id)
        # Keyed on the seed as well as the id: the work list walks a seed grid,
        # so two entries can share a scenario id and be different investigations.
        key = f"{name}/{int(the_scenario.seed)}"
        if key not in investigations:
            investigations[key] = at_proposal_time(the_scenario, table)
        investigation = investigations[key]
        layer = layers[memory]
        # Rendered a second time purely to size it. `propose` renders its own and
        # does not hand it back, and a brief is a pure function of the state, so
        # this is the same string the call carried.
        brief_chars = len(render_brief(investigation, layer.menu, memory=memory))

        tap.last = None
        # The store is shared across layers and `propose` does not hand its
        # address back, so the address a *failed* call used is recovered by
        # diffing. It matters because `ProposalLayer.propose` resolves and stores
        # the transcript before decoding it: a draft that fails the grammar is
        # already in the corpus, and recording it against an empty address is
        # what made the report's "inspect these" rows unjoinable to the very
        # transcripts they name.
        before = set(store.addresses())
        started = time.perf_counter()
        try:
            proposal = layer.propose(investigation)
        # A bad proposal is an outcome, not a cap: record it and keep going, as
        # Hybrid does. Catching one type and not the rest is how the first re-run
        # threw away fifteen paid calls at the sixteenth.
        #
        # ``InvalidEditError`` is now unreachable here and is kept deliberately:
        # ``ProposalLayer._build`` widened to ``except GrammarError`` on
        # 2026-08-18 and wraps every one of them as ``MalformedProposalError``
        # before it can leave ``propose``. Keeping the name costs nothing and
        # holds the branch open if that widening is ever narrowed; what would
        # cost something is trusting the *old* comment here, which said the two
        # types "remain unrelated" and stopped being true in the same change.
        except (MalformedProposalError, InvalidEditError) as error:
            records.append(
                _record(
                    index=index,
                    scenario=name,
                    memory=memory,
                    outcome=type(error).__name__,
                    brief_chars=brief_chars,
                    address=_stored_address(store, before),
                    wall_s=time.perf_counter() - started,
                    result=tap.last,
                    detail=str(error),
                )
            )
            print(
                f"  call {index:>2}  {name:<3} {memory.value:<5} "
                f"{type(error).__name__} -- recorded, continuing"
            )
            continue
        # A provider failure is the one that stops the run: it may be the rate
        # cap this pilot exists to notice, and calls made after one would be
        # measurements of a throttled account.
        #
        # ``ProviderUnavailableError`` is listed explicitly because it is a
        # *sibling* of ``ProviderError`` under ``ProposalError`` rather than a
        # subclass, so catching the latter alone does not reach it. Missing it
        # was a real regression for the length of one review round: a rate cap
        # would have propagated out of `main`, skipping `report` and
        # `_save_transcripts` entirely and losing every call already billed in
        # this process — in the one script whose stated purpose is to be running
        # when a cap bites.
        #
        # ``SystemConfigurationError`` joined the list at gate A44, which moved
        # five machine faults out of the scored tier and into it -- a
        # contaminated environment, a call from inside an event loop, a foreign
        # provider, unverifiable provenance, a substitute model. Those propagate
        # by design, and ``Hybrid`` is right not to catch them. **This script is
        # not Hybrid.** It is the process holding the billed transcripts, so the
        # identical regression the paragraph above describes reopened for those
        # five the moment they changed class. Stopping the pilot is the correct
        # response to all three families; losing the corpus on the way out is
        # not, and that is the distinction this clause keeps.
        except (
            ProviderError,
            ProviderUnavailableError,
            SystemConfigurationError,
        ) as error:
            records.append(
                _record(
                    index=index,
                    scenario=name,
                    memory=memory,
                    outcome=type(error).__name__,
                    brief_chars=brief_chars,
                    address=_stored_address(store, before),
                    wall_s=time.perf_counter() - started,
                    result=tap.last,
                    detail=str(error),
                )
            )
            print(f"  call {index}: {type(error).__name__} -- run stopped")
            break
        wall = time.perf_counter() - started
        records.append(
            _record(
                index=index,
                scenario=name,
                memory=memory,
                outcome="ok",
                brief_chars=brief_chars,
                address=proposal.address,
                wall_s=wall,
                result=tap.last,
                detail="",
            )
        )
        latest = records[-1]
        print(
            f"  call {index:>2}  {name:<3} {memory.value:<5} "
            f"{wall:>6.1f}s  ${latest.cost_usd:.4f}  "
            f"in={latest.input_tokens} out={latest.output_tokens} "
            f"cache_r={latest.cache_read_tokens}"
        )

    return records, store


def _save_transcripts(store: TranscriptStore, out: Path) -> str:
    """Persist the recorded calls; return "" or why it could not be done.

    Never raises. :meth:`TranscriptStore.save` refuses to drop an address already
    on the path, which is right for a corpus and fatal for a pilot: a second run
    at a different ``--effort`` produces different addresses, the refusal lands
    *after* every call is billed, and without this the report and the JSON would
    be lost with it. The measurement is what was paid for; the transcripts are a
    by-product, so a failure to store them is reported rather than thrown.
    """
    if len(store) == 0:
        return "nothing recorded"
    path = out.with_suffix(".transcripts.json")
    try:
        store.save(path)
    except ProposalError as error:
        return f"{path} kept its existing calls, so these were not written: {error}"
    return ""


def _scripted_policy() -> Callable[[str], Mapping[str, Any]]:
    """Return a policy naming the menu's first cell, at grid index zero.

    Its only job is to decode, so that ``--dry-run`` exercises the whole path --
    brief, address, store, decoder, grammar validation -- and differs from a
    recording run in nothing but where the payload came from. The arity is read
    off the menu rather than assumed: the first cell takes three grid indices
    today, and a payload that hardcoded none would fail in the decoder and look
    like a fault in the pilot.
    """
    first = structural_menu(AGENT_GRAMMAR)[0]
    payload = fixed_payload(first.index, (0,) * first.arity)
    return lambda _brief: payload


def _effort(value: str) -> Any:
    """Return ``value`` as the SDK's effort literal.

    The provider types the argument as a ``Literal``; argparse supplies a
    ``str``. ``choices`` on the parser is what actually restricts it, so this is
    the one place the two type systems are reconciled.
    """
    return value


def _record(
    *,
    index: int,
    scenario: str,
    memory: Memory,
    outcome: str,
    brief_chars: int,
    address: str,
    wall_s: float,
    result: ResultMessage | None,
    detail: str,
) -> CallRecord:
    """Build a record from a call and whatever the tap caught."""
    usage: Mapping[str, Any] = (result.usage or {}) if result is not None else {}
    return CallRecord(
        index=index,
        scenario=scenario,
        memory=memory.value,
        outcome=outcome,
        brief_chars=brief_chars,
        address=address,
        wall_s=round(wall_s, 3),
        duration_ms=_int(result.duration_ms if result is not None else 0),
        duration_api_ms=_int(result.duration_api_ms if result is not None else 0),
        num_turns=_int(result.num_turns if result is not None else 0),
        cost_usd=_float(result.total_cost_usd if result is not None else 0.0),
        input_tokens=_int(usage.get("input_tokens")),
        output_tokens=_int(usage.get("output_tokens")),
        cache_read_tokens=_int(usage.get("cache_read_input_tokens")),
        cache_creation_tokens=_int(usage.get("cache_creation_input_tokens")),
        served_models=_served(result),
        detail=detail,
    )


def _served(result: ResultMessage | None) -> str:
    """Return the models the session attributed usage to, comma separated."""
    if result is None:
        return ""
    usage = result.model_usage or {}
    names = {entry.get("canonicalModel") or key for key, entry in usage.items()}
    return ",".join(sorted(names))


def _int(value: object) -> int:
    """Return ``value`` as an int, or zero where the SDK reported nothing."""
    return int(value) if isinstance(value, int | float) else 0


def _float(value: object) -> float:
    """Return ``value`` as a float, or zero where the SDK reported nothing."""
    return float(value) if isinstance(value, int | float) else 0.0


def report(records: Sequence[CallRecord], *, dry_run: bool) -> None:
    """Print the per-call table, the totals and the projection.

    The projection is stated as an estimate against unmeasured caps, and the
    wording is deliberate: this script never sees a cap bite, so a reader who
    takes its output for a measured limit has been misled by the report rather
    than by the number.
    """
    # Cost is measured over every call the model actually served, decoded or
    # not: a proposal that failed the grammar was still billed, and a recording
    # run pays for it exactly the same. `usable` is the narrower question of how
    # many produced a proposal, which is a fact about the model rather than
    # about the budget.
    #
    # Served-ness is read off the *outcome*, not off the cost. This gated on
    # `cost_usd > 0.0` until 2026-08-18, which discards the whole measurement
    # after the quota has already been spent whenever the session reports no
    # cost -- and this backend authenticates by subscription with `apiKeySource`
    # of "none", so a zero from the SDK's costing is the case to expect rather
    # than a hypothetical. A `ProviderError` is the one outcome where no answer
    # was served; everything else was.
    ok = [record for record in records if record.outcome not in STOPPED_OUTCOMES]
    usable = [record for record in records if record.outcome == "ok"]
    rejected = [
        record
        for record in records
        if record.outcome in {"MalformedProposalError", "InvalidEditError"}
    ]
    failed = [record for record in records if record.outcome in STOPPED_OUTCOMES]
    print()
    print(
        f"calls attempted: {len(records)}   billed: {len(ok)}   "
        f"yielded a proposal: {len(usable)}"
    )
    if rejected:
        print()
        print(
            f"{len(rejected)} billed call(s) produced nothing usable "
            f"({len(rejected) / max(len(ok), 1):.0%} of the run):"
        )
        for record in rejected:
            print(f"  call {record.index} {record.scenario}: {record.outcome}")
        print(
            "  These cost quota and yield no proposal, so a recording run needs\n"
            "  headroom above the raw call count -- and V7 must survive them.\n"
            "  Since 2026-08-18 ProposalLayer._build re-raises InvalidEditError\n"
            "  as MalformedProposalError, so Hybrid._propose_once now catches\n"
            "  both. This script still catches each by name because the two\n"
            "  types remain unrelated. See the DECISIONS entry."
        )
    if failed:
        print()
        print("THE RUN STOPPED. The last call reported:")
        print(f"  {failed[-1].outcome}: {failed[-1].detail}")
        print(
            "  If that is a rate cap, this is the measurement the pilot was for: "
            "record the wording, the call index and the wall-clock reached."
        )
    if not ok:
        return

    walls = [record.wall_s for record in ok]
    costs = [record.cost_usd for record in ok]
    inputs = [float(record.input_tokens) for record in ok]
    outputs = [float(record.output_tokens) for record in ok]
    cached = [float(record.cache_read_tokens) for record in ok]
    total_wall = sum(walls)

    print()
    print("per proposal            median      mean       min       max")
    _row("brief (chars)", [float(record.brief_chars) for record in ok])
    _row("wall-clock (s)", walls)
    _row("cost (USD)", costs, places=4)
    _row("input tokens", inputs)
    _row("output tokens", outputs)
    _row("cache-read tokens", cached)
    print()
    print(f"pilot total: {total_wall:.1f}s of calls, ${sum(costs):.4f}")
    if total_wall > 0:
        print(f"serial throughput: {3600.0 * len(ok) / total_wall:.1f} proposals/hour")

    if dry_run:
        print()
        print("--dry-run: no model was called, so every cost above is zero.")
        return

    median_cost = statistics.median(costs)
    median_wall = statistics.median(walls)
    if sum(costs) == 0.0:
        # Reachable since 2026-08-18: `ok` no longer gates on a positive cost, so
        # a run the SDK charged nothing for now reaches this projection instead
        # of being discarded. Discarding it was the worse bug -- the quota was
        # already spent -- but a cost table of zeros printed without comment is
        # its own way of misleading, since every projected figure below is then
        # $0.00 and looks like a measurement.
        print()
        print(
            "NO COST REPORTED. Every call was served and timed, so the wall-clock\n"
            "  and token figures stand, but the session charged nothing this run --\n"
            "  expected under subscription auth, where apiKeySource is 'none'.\n"
            "  Read every cost below as absent, not as zero."
        )
    print()
    print("projected, serially:")
    print("  calls        cost   model time   basis")
    for calls, basis in (
        (MATRIX_PROPOSALS, "S11 alone fires, 3 cells x 20 seeds x 2 proposals"),
        (MATRIX_PROPOSALS_CEILING, "every model-bearing cell fires at every seed"),
        (MATRIX_INVESTIGATIONS, "the ~1120 the open item is written in"),
    ):
        print(
            f"  {calls:>5}   {median_cost * calls:>8.2f}   "
            f"{median_wall * calls / 3600.0:>7.1f}h   {basis}"
        )
    print()
    print(
        "ESTIMATE, not a measured cap. No rate limit was reached in this run, so\n"
        "nothing here says how many of these fit in a 5-hour window or a week.\n"
        "The 1120 row is an investigation count, not a proposal count: only\n"
        "Hybrid holds a proposal layer, and only a cell whose Stage A gate opens\n"
        "asks the model at all. Read the first row as the estimate."
    )


def _row(label: str, values: Sequence[float], *, places: int = 1) -> None:
    """Print one summary row of the per-proposal table."""
    print(
        f"{label:<20} "
        f"{statistics.median(values):>9.{places}f} "
        f"{statistics.fmean(values):>9.{places}f} "
        f"{min(values):>9.{places}f} "
        f"{max(values):>9.{places}f}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--calls",
        type=int,
        default=20,
        help="proposals to record (default 20; 36 distinct briefs exist)",
    )
    parser.add_argument(
        "--effort",
        default="high",
        choices=("low", "medium", "high", "xhigh", "max"),
        help="reasoning effort; part of the transcript address (default high)",
    )
    parser.add_argument(
        "--model",
        default=AgentSdkProvider().model,
        help="model to record against (default the provider's)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="drive the whole path with a scripted backend; calls nothing",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(".cache/rate_limit_pilot/pilot.json"),
        help="where the report and transcripts are written (under .cache/)",
    )
    args = parser.parse_args(argv)

    print(f"acquiring the slice table, then recording {args.calls} proposal(s)")
    records, store = run_pilot(
        calls=args.calls,
        effort=args.effort,
        model=args.model,
        dry_run=args.dry_run,
    )
    report(records, dry_run=args.dry_run)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "model": args.model,
                "effort": args.effort,
                "dry_run": args.dry_run,
                "transcripts": len(store),
                "calls": [asdict(record) for record in records],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"\nwritten: {args.out}")

    # Last, and deliberately: the report and the JSON are what the calls were
    # paid for, and storing the transcripts must not be able to lose them.
    if not args.dry_run:
        complaint = _save_transcripts(store, args.out)
        print(complaint or f"written: {args.out.with_suffix('.transcripts.json')}")
    # Three outcomes, three codes. Until 2026-08-18 a rejected draft and a
    # stopped run both returned 1, which makes the one event this pilot exists
    # to detect indistinguishable from the one it explicitly carries on past:
    # a provider failure may be the rate cap biting, while a draft that does not
    # decode is an ordinary and expected cost of the run.
    if any(record.outcome in STOPPED_OUTCOMES for record in records):
        return 1
    if any(record.outcome != "ok" for record in records):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

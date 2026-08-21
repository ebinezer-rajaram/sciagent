"""Run SPEC section 9's experiment matrix, cell by cell, into a campaign ledger.

    uv run python scripts/run_matrix.py .cache/campaign/spec9.db --systems V1,B4,B5,B1

Thin by intent, like ``scripts/report_matrix.py``. Everything that decides a
number is in :mod:`sciagent.eval.matrix` and
:mod:`environments.pointproc.runner`, both tested; this selects cells, opens a
ledger and prints progress.

Resuming
--------

There is no progress file, and there should not be: the ledger *is* the
checkpoint. :func:`~sciagent.eval.matrix.run_matrix` skips any replicate whose
content address it already holds, so re-running this command after a stop -- a
crash, a closed laptop, an exhausted rate limit -- continues where it left off.
An exception propagates with every completed replicate already recorded.

``--replicates`` narrows a run for a smoke test, and interacts with resuming in
the way you want rather than the way you might fear: the replicate index is part
of the address, so ``--replicates 1`` records replicate 00 and a later full pass
runs 01 through 19 and skips 00.

Which arms a bare invocation runs
---------------------------------

``--systems`` defaults to section 9's seven arms and to those alone, so a
default pass records section 9's matrix and nothing beside it. B6 -- the
comparator SPEC section 12 criterion 5 names, which section 5 defers -- is
buildable and is opt-in: ``--systems B6`` runs its one cell, twenty
replicates of S11, with no provider and no API cost. It is opt-in rather
than default because its cell is not part of what section 9 preregistered,
and a flag that quietly widened the recorded matrix would make every later
"the matrix says" ambiguous about which matrix.

Why there is no scripted provider here
--------------------------------------

``--provider`` offers the two live backends and nothing else. A scripted
provider is invaluable in the suite and inadmissible here: its answers are
fixtures, and a ledger is a durable research artefact with no field saying "these
numbers came from a fixture". The refusal is the feature. Arms needing a provider
are simply not run without one -- see ``--systems``.

``--replay`` is not an exception to that, and the distinction is worth stating
because it looks like one. A scripted provider *invents* an answer; a replay
resolves every answer from a corpus of calls that really happened, and refuses
rather than inventing one when the corpus does not hold it. The ledger a replay
writes is addressed identically to the one the recording pass wrote and carries
the same numbers -- which is the point of it, since re-scoring a recorded
campaign under fixed instruments is the only way to read evidence already paid
for without paying for it again. What would reopen the hole is a replay that
filled its misses, so it does not: the store is opened in ``REPLAY``, where a
miss raises, and the provider handed to the proposal layer is a
``RefusingProvider`` that raises if it is called at all.

For the same reason ``--replay`` refuses ``--provider`` and ``--transcripts``
rather than ordering them: with a live backend beside it a miss would be filled
by a fresh call and the result reported as a replay, and with a ``--transcripts``
target the per-replicate checkpoint would write back over the corpus being
audited.

One platform
------------

Every cell must run on the one reference platform, which is Windows. The registry
content-addresses with no platform term, so a matrix built partly on each machine
would be internally incomparable with nothing in the registry to say so. This
script does not check that -- it cannot, since nothing records a platform -- which
is exactly why ``scripts/report_matrix.py`` makes you type ``--platform``.

Output is ASCII. A literal section sign comes back as a replacement character on
a Windows console at cp1252, which is why this docstring spells it out.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from environments.pointproc.matrix import ALL_CELLS
from environments.pointproc.runner import (
    ALL_SYSTEMS,
    CRITERION5_SYSTEMS,
    LLM_SYSTEMS,
    MATRIX_SYSTEMS,
    MatrixRunner,
    ProviderFactory,
    scenario_battery,
    scenario_seed,
)
from environments.pointproc.tables import matrix_table, save_matrix_table
from sciagent.core.errors import ProposalError, SciAgentError
from sciagent.eval.matrix import Cell, CellReading, CellTask, run_matrix
from sciagent.registry.ledger import CampaignLedger
from sciagent.systems.llm import RECORD, REPLAY, TranscriptStore
from sciagent.systems.llm.provider import Provider, RefusingProvider


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path, help="path to the campaign ledger")
    parser.add_argument(
        "--systems",
        default=",".join(MATRIX_SYSTEMS),
        help=(
            "comma-separated arms to run, from "
            f"{','.join(ALL_SYSTEMS)}. Defaults to section 9's "
            f"{','.join(MATRIX_SYSTEMS)} and not to all of them: "
            f"{','.join(sorted(CRITERION5_SYSTEMS))} is criterion 5's "
            "comparator, is not part of section 9's preregistered matrix, and "
            "is opt-in for that reason. A default run refuses rather than "
            "quietly running a "
            "smaller matrix than section 9's"
        ),
    )
    parser.add_argument(
        "--scenarios",
        default="",
        help="comma-separated scenarios to run; empty means every one the cell offers",
    )
    parser.add_argument(
        "--replicates",
        type=int,
        default=0,
        help="cap replicates per cell, for a smoke run; 0 means section 9's twenty",
    )
    parser.add_argument(
        "--provider",
        default="none",
        choices=("none", "anthropic", "agent-sdk"),
        help="live backend for the proposal arms; see the module docstring",
    )
    parser.add_argument(
        "--transcripts",
        type=Path,
        default=None,
        help="where model calls are recorded; required with a live provider",
    )
    parser.add_argument(
        "--replay",
        type=Path,
        default=None,
        help=(
            "re-run the campaign against a recorded corpus instead of a live "
            "backend. Every model call is resolved from the corpus and a miss "
            "stops the run; no call reaches a network. Mutually exclusive with "
            "--provider and --transcripts"
        ),
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help=(
            "re-execute every cell including recorded ones and let the ledger "
            "compare each fresh reading against the stored one. Invariant 3 "
            "audited at matrix scale; not the way to add cells"
        ),
    )
    return parser


def _selected(argv: argparse.Namespace) -> tuple[Cell, ...]:
    """Return the cells this invocation asks for, section 9 first."""
    systems = {name.strip() for name in argv.systems.split(",") if name.strip()}
    unknown = systems - set(ALL_SYSTEMS)
    if unknown:
        raise ValueError(
            f"unknown arm(s) {', '.join(sorted(unknown))}; "
            f"this environment builds {', '.join(ALL_SYSTEMS)}"
        )
    wanted = {name.strip() for name in argv.scenarios.split(",") if name.strip()}
    # Validated for the same reason `--systems` is, and missing here until a
    # review reproduced it: `--scenarios S9,S13,s11` ran S9 alone and exited 0
    # reporting "ran 1". A typo that silently yields a smaller matrix and calls
    # it complete is the worst failure this script has available.
    offered = {str(cell.scenario) for cell in ALL_CELLS}
    unknown_scenarios = wanted - offered
    if unknown_scenarios:
        raise ValueError(
            f"unknown scenario(s) {', '.join(sorted(unknown_scenarios))}; "
            f"this environment runs "
            f"{', '.join(sorted(offered, key=_scenario_order))}"
        )
    if argv.replicates < 0:
        raise ValueError(f"--replicates {argv.replicates} is not a shorter campaign")
    chosen = [
        cell
        for cell in ALL_CELLS
        if cell.system in systems and (not wanted or str(cell.scenario) in wanted)
    ]
    if argv.replicates:
        chosen = [
            Cell(cell.system, cell.scenario, min(cell.replicates, argv.replicates))
            for cell in chosen
        ]
    return tuple(chosen)


def _recorded_systems(ledger_path: Path) -> set[str]:
    """Return the arms a ledger already holds rows for, or an empty set."""
    if not ledger_path.exists():
        return set()
    with CampaignLedger.open(ledger_path) as ledger:
        # A row with no `system` term names no arm. Defaulting to `""` and
        # keeping it leaves `recorded - criterion5` holding `{""}` -- an empty
        # string, but a non-empty *set*, and `if here and there` tests the set.
        # So such a row reads as a section 9 arm and refuses a legitimate B6
        # run against a ledger holding nothing of section 9's: the guard's own
        # false positive.
        return {
            system
            for entry in ledger.entries()
            if (system := entry.key.config.get("system", ""))
        }


def _refuse_mixed_ledger(cells: Sequence[Cell], ledger_path: Path) -> None:
    """Refuse to put section 9's matrix and criterion 5's comparator in one file.

    The separation `environments.pointproc.matrix` declares is between two cell
    *sets*, and on its own that separation stops at cell selection. Every cell
    of either set is addressed under the same `CampaignAddress`, and
    `sciagent.eval.report.summarise` groups whatever rows an address matches --
    so a B6 row written into section 9's ledger comes back out of
    `report_matrix.py` as a section 9 cell, indistinguishable from a
    preregistered arm. That is exactly the ambiguity the separate declaration
    exists to prevent, arriving one layer downstream of where it was prevented.
    Found by review, not by a test.

    Two invocations against one path are one of the two cases, so this reads
    what the ledger already holds as well as what this invocation asks for. The
    second case is a single invocation naming both sets: it never becomes
    something the ledger "already holds", so reading history alone admitted
    ``--systems V1,B6`` against a fresh path. Found by review, again, after the
    first version shipped with only the history half.

    Refusing is right rather than harsh: the alternative fixes are a term
    in the campaign address, which would move every recorded cell, or a filter
    in `summarise`, which would put a section 9 concept inside the framework.
    A second ledger file costs nothing and keeps both readings honest.
    """
    wanted = {cell.system for cell in cells}
    criterion5 = set(CRITERION5_SYSTEMS)
    asked_for, alongside = wanted & criterion5, wanted - criterion5
    if asked_for and alongside:
        raise ValueError(
            f"refusing both cell sets in one invocation: criterion 5's "
            f"comparator ({', '.join(sorted(asked_for))}) alongside section 9's "
            f"arms ({', '.join(sorted(alongside))}). Section 9's "
            f"matrix and criterion 5's comparator share a campaign address, so "
            f"one ledger holding both reports them as one matrix. Run each cell "
            f"set separately, into its own ledger file"
        )
    recorded = _recorded_systems(ledger_path)
    for label, here, there in (
        ("criterion 5's comparator", asked_for, recorded - criterion5),
        ("section 9's matrix", alongside, recorded & criterion5),
    ):
        if here and there:
            raise ValueError(
                f"refusing to record {label} ({', '.join(sorted(here))}) into "
                f"{ledger_path}, which already holds "
                f"{', '.join(sorted(there))}. Section 9's matrix and "
                f"criterion 5's comparator share a campaign address, so one "
                f"ledger holding both reports them as one matrix. Use a "
                f"separate ledger file for each"
            )


def _scenario_order(name: str) -> tuple[int, str]:
    """Sort S2 before S10, so an error message reads in section 4.5's order."""
    return (int(name[1:]), name) if name[1:].isdigit() else (0, name)


def _replay_provider(store: TranscriptStore) -> ProviderFactory:
    """Return a factory for the backend that recorded ``store``.

    A replay must present the *recording* backend's identity, not a stand-in's.
    :func:`~sciagent.systems.llm.transcripts.call_address` hashes a provider's
    ``id``, ``model`` and ``settings`` along with the brief, so a stand-in
    computes a different address for every call and misses the entire corpus --
    a replay that cannot replay anything, reported as a corpus that does not
    match the code.

    Raises :class:`~sciagent.core.errors.ProposalError` if the corpus holds more
    than one identity, because then there is no single answer and picking one
    would silently replay part of it. One is what a campaign leaves behind: the
    recorded section 9 corpus holds 112 calls under
    ``claude-agent-sdk``/``claude-opus-5``/``effort=high``.

    Raises the same for an empty corpus, where there is no identity to read at
    all. That case would otherwise reach ``next(iter(...))`` as a ``StopIteration``
    inside a generator-driven call stack, which is the least legible way this
    could fail.
    """
    identities = sorted({(call.provider, call.model, call.settings) for call in store})
    if not identities:
        raise ProposalError(
            "the corpus holds no calls, so there is no backend identity to "
            "replay under. A replay resolves every call from the corpus; an "
            "empty one replays nothing and would miss on the first address"
        )
    if len(identities) > 1:
        raise ProposalError(
            f"the corpus holds {len(identities)} backend identities "
            f"({identities!r}) and a replay presents exactly one. A transcript "
            f"address covers the backend, so replaying under one identity would "
            f"miss every call recorded under the others -- replay each backend's "
            f"corpus separately"
        )
    backend, model, settings = identities[0]

    def refusing() -> Provider:
        return RefusingProvider(id=backend, model=model, settings=settings)

    return refusing


def _provider_factory(name: str) -> ProviderFactory:
    """Return a factory for a live backend, imported only when one is asked for."""
    if name == "anthropic":
        from sciagent.systems.llm.anthropic_provider import AnthropicProvider

        def anthropic() -> Provider:
            return AnthropicProvider()

        return anthropic

    from sciagent.systems.llm.agent_sdk_provider import AgentSdkProvider

    def agent_sdk() -> Provider:
        return AgentSdkProvider()

    return agent_sdk


def main(argv: Sequence[str] | None = None) -> int:
    """Run the selected cells, returning a process exit status."""
    args = _parser().parse_args(argv)
    try:
        cells = _selected(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not cells:
        print("no cells selected", file=sys.stderr)
        return 2

    try:
        _refuse_mixed_ledger(cells, args.ledger)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.replay is not None:
        # Both refusals are about the same hazard from opposite ends. A live
        # provider beside a replay would fill the corpus's gaps with fresh calls
        # and report the result as a replay; a `--transcripts` target beside one
        # would hand `checkpoint` the corpus to write back, and the corpus is the
        # artefact being audited. Refusing beats resolving a precedence, because
        # either precedence is a rule someone has to remember.
        if args.provider != "none":
            print(
                f"--replay and --provider {args.provider} ask for opposite "
                f"things: a replay resolves every call from the corpus and "
                f"reaches no network. Drop one",
                file=sys.stderr,
            )
            return 3
        if args.transcripts is not None:
            print(
                "--replay and --transcripts ask for opposite things: "
                "--transcripts is where a recording pass writes its calls, and "
                "a replay must not write the corpus it is auditing. Drop one",
                file=sys.stderr,
            )
            return 3
        if not args.replay.exists():
            print(f"no corpus at {args.replay}", file=sys.stderr)
            return 2

    blocked = {cell.system for cell in cells} & LLM_SYSTEMS
    if blocked and args.provider == "none" and args.replay is None:
        # Refused here rather than on the first such cell. Failing partway
        # through leaves a ledger that looks like a finished campaign missing
        # some arms, which is the state hardest to notice later.
        print(
            f"{', '.join(sorted(blocked))} need the LLM proposal layer and no "
            f"provider was given. Either pass --provider, or run the "
            f"conventional arms with "
            f"--systems {','.join(n for n in MATRIX_SYSTEMS if n not in LLM_SYSTEMS)}",
            file=sys.stderr,
        )
        return 3
    if blocked and args.provider != "none" and args.transcripts is None:
        print("--transcripts is required with a live provider", file=sys.stderr)
        return 3

    provider = _provider_factory(args.provider) if args.provider != "none" else None
    store: TranscriptStore | None = None
    try:
        if args.replay is not None:
            # REPLAY, not RECORD, and that is the whole difference between this
            # branch and the one below: in REPLAY a miss raises instead of
            # reaching a backend, so a corpus that does not cover the campaign
            # stops it rather than quietly re-deriving the part it lacks. The
            # provider comes from the corpus for the reason `_replay_provider`
            # gives -- the address covers the backend's identity.
            store = TranscriptStore.load(args.replay, mode=REPLAY)
            provider = _replay_provider(store)
        elif args.transcripts is not None:
            # Loaded rather than started empty, so a resumed campaign keeps the
            # calls an earlier pass recorded. RECORD, not REPLAY: a miss must
            # reach the backend, because the cells this pass adds have never
            # been called. Inside the guard because a stale corpus raises, and a
            # raw traceback is the wrong way to say "your transcripts do not
            # match this code".
            store = (
                TranscriptStore.load(args.transcripts, mode=RECORD)
                if args.transcripts.exists()
                else TranscriptStore(mode=RECORD)
            )
        runner = MatrixRunner(matrix_table(), provider=provider, store=store)
    except SciAgentError as exc:
        print(f"could not start: {exc}", file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError) as exc:
        # `exists()` above answers "is there a file", which is not "is there a
        # corpus". A truncated or hand-edited JSON file, or one that cannot be
        # read, reaches the operator as a traceback without this -- and the
        # branch that says "your transcripts do not match this code" already
        # exists one clause up for the `SciAgentError` half of the same
        # question. Found by review, by driving `main` at a malformed file.
        print(f"could not read the corpus at {args.replay}: {exc}", file=sys.stderr)
        return 2

    replicates = sum(cell.replicates for cell in cells)
    print(
        f"{len(cells)} cell(s), up to {replicates} replicate(s); "
        f"already-recorded ones are skipped. Ledger {args.ledger}",
        flush=True,
    )

    done = 0

    def checkpoint() -> None:
        """Persist what only this process holds: the table, and the transcripts.

        The ledger checkpoints itself -- ``run_matrix`` has already appended
        every completed replicate. These two have not, and losing them is not
        symmetric. The table costs re-simulation, which is only time. The
        transcripts are worse: on a resumed pass the ledger *skips* the
        replicates whose readings it holds, so their calls are never made again
        and never recorded, and the campaign is permanently unreplayable. That
        is why this runs per replicate rather than once at the end.
        """
        save_matrix_table(runner.table)
        if store is not None and args.transcripts is not None:
            store.save(args.transcripts)

    def execute(task: CellTask) -> CellReading:
        nonlocal done
        started = time.monotonic()
        reading = runner.execute(task)
        done += 1
        checkpoint()
        print(
            f"[{done:>4}/{replicates}] {task.name} {time.monotonic() - started:6.1f}s",
            flush=True,
        )
        return reading

    try:
        with CampaignLedger.open(args.ledger) as ledger:
            outcome = run_matrix(
                cells,
                address=runner.address,
                scenario_seed=scenario_seed,
                battery=scenario_battery,
                execute=execute,
                ledger=ledger,
                skip_recorded=not args.verify,
            )
    except SciAgentError as exc:
        print(f"stopped after {done} replicate(s): {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        # Not in a `finally`: a checkpoint that raised there would replace the
        # campaign's real error with its own, and discard the exit status the
        # handler above had already chosen. Each interesting path checkpoints
        # for itself, and `execute` has already done so after every replicate
        # that completed -- so there is nothing left to lose here anyway.
        print(f"interrupted after {done} replicate(s)", file=sys.stderr)
        return 1
    print(
        f"ran {outcome.ran}, skipped {outcome.skipped}, "
        f"simulated {runner.simulations} row(s)",
        flush=True,
    )
    if args.replay is not None and store is not None:
        # Reported because the backlog entry names it, and the number is worth
        # printing even though a REPLAY store cannot make it non-zero: `resolve`
        # increments the counter only on the branch that calls out, which REPLAY
        # never reaches. So this line says "nothing was re-derived" about a run
        # whose real evidence is that it *finished* -- a corpus short of the
        # campaign raises long before here. The check is kept rather than
        # simplified away because it is what would notice if this branch were
        # ever handed a RECORD store, which is the one mistake that would turn a
        # replay into a live run.
        # `len(store)` is the size of the *corpus*, not the number of calls this
        # pass resolved, and printing it alone was a false green a review caught:
        # a second run against a ledger that already holds every replicate skips
        # every cell, resolves nothing, and would still have announced a
        # full-corpus replay with `misses 0` and exit 0. What the operator needs
        # to read is how many replicates actually executed, so that is said
        # first and the corpus size is labelled as what it is.
        print(
            f"replay: {outcome.ran} replicate(s) executed against a corpus of "
            f"{len(store)} recorded call(s), misses {store.misses}"
        )
        if not outcome.ran:
            print(
                f"no replicate executed -- every cell was already recorded in "
                f"{args.ledger}, so nothing was replayed and this run audits "
                f"nothing. Point --replay at a fresh ledger to audit the corpus",
                file=sys.stderr,
            )
            return 1
        if store.misses:
            print(
                f"{store.misses} call(s) were re-derived rather than replayed, "
                f"so this run is not a replay of the recorded campaign",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

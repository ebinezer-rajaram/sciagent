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

Why there is no scripted provider here
--------------------------------------

``--provider`` offers the two live backends and nothing else. A scripted
provider is invaluable in the suite and inadmissible here: its answers are
fixtures, and a ledger is a durable research artefact with no field saying "these
numbers came from a fixture". The refusal is the feature. Arms needing a provider
are simply not run without one -- see ``--systems``.

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
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from environments.pointproc.matrix import SPEC9_CELLS
from environments.pointproc.runner import (
    LLM_SYSTEMS,
    MATRIX_SYSTEMS,
    MatrixRunner,
    ProviderFactory,
    scenario_battery,
    scenario_seed,
)
from environments.pointproc.tables import matrix_table, save_matrix_table
from sciagent.core.errors import SciAgentError
from sciagent.eval.matrix import Cell, CellReading, CellTask, run_matrix
from sciagent.registry.ledger import CampaignLedger
from sciagent.systems.llm import RECORD, TranscriptStore
from sciagent.systems.llm.provider import Provider


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path, help="path to the campaign ledger")
    parser.add_argument(
        "--systems",
        default=",".join(MATRIX_SYSTEMS),
        help=(
            "comma-separated arms to run, from "
            f"{','.join(MATRIX_SYSTEMS)}. Defaults to all of them, so a run "
            "that omits a provider refuses rather than quietly running a "
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
    """Return the cells this invocation asks for, in section 9's order."""
    systems = {name.strip() for name in argv.systems.split(",") if name.strip()}
    unknown = systems - set(MATRIX_SYSTEMS)
    if unknown:
        raise ValueError(
            f"unknown arm(s) {', '.join(sorted(unknown))}; "
            f"section 9's are {', '.join(MATRIX_SYSTEMS)}"
        )
    wanted = {name.strip() for name in argv.scenarios.split(",") if name.strip()}
    # Validated for the same reason `--systems` is, and missing here until a
    # review reproduced it: `--scenarios S9,S13,s11` ran S9 alone and exited 0
    # reporting "ran 1". A typo that silently yields a smaller matrix and calls
    # it complete is the worst failure this script has available.
    offered = {str(cell.scenario) for cell in SPEC9_CELLS}
    unknown_scenarios = wanted - offered
    if unknown_scenarios:
        raise ValueError(
            f"unknown scenario(s) {', '.join(sorted(unknown_scenarios))}; "
            f"section 9 runs {', '.join(sorted(offered, key=_scenario_order))}"
        )
    if argv.replicates < 0:
        raise ValueError(f"--replicates {argv.replicates} is not a shorter campaign")
    chosen = [
        cell
        for cell in SPEC9_CELLS
        if cell.system in systems and (not wanted or str(cell.scenario) in wanted)
    ]
    if argv.replicates:
        chosen = [
            Cell(cell.system, cell.scenario, min(cell.replicates, argv.replicates))
            for cell in chosen
        ]
    return tuple(chosen)


def _scenario_order(name: str) -> tuple[int, str]:
    """Sort S2 before S10, so an error message reads in section 4.5's order."""
    return (int(name[1:]), name) if name[1:].isdigit() else (0, name)


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

    blocked = {cell.system for cell in cells} & LLM_SYSTEMS
    if blocked and args.provider == "none":
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
    if blocked and args.transcripts is None:
        print("--transcripts is required with a live provider", file=sys.stderr)
        return 3

    provider = _provider_factory(args.provider) if args.provider != "none" else None
    store: TranscriptStore | None = None
    try:
        if args.transcripts is not None:
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

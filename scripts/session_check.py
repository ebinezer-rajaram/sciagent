"""Check that a rescore finds every stored LLM session of a pilot store.

    uv run python scripts/session_check.py --root .cache/minipilot \
        --ids dev-008,dev-013,dev-017 --seeds 1 --control-seeds 1

Rebuilds hunt B's ``store_check.py`` §2 and §5 (the Windows salvage,
``salvage/windows-2026-10/hunt/B/store_check.py``). Takes the same arguments
as ``scripts/pilot.py`` and rebuilds the run's configuration with the same
code (``pilot.campaign_config``), so it should be given the run's own command
line. Every ``llm-session`` cell must be found by the rescore's enumeration
(``sciagent.campaign.rescore.stored_sessions``); a missed one is listed with
the reason. Sessions whose stored void flag differs from the void policy are
listed as warnings. Exit status 0 when every session is found, 1 otherwise,
2 when ``--root`` is not a store. The contract is :mod:`sciagent.campaign.audit`
(:func:`~sciagent.campaign.audit.check_sessions`).
"""

from __future__ import annotations

import sys

from pilot import (
    build_parser,
    campaign_config,
    load_split,
    pointproc_environment,
    reconfigure_stdout,
    select,
    truth_entries,
)


def main(argv: list[str] | None = None) -> int:
    reconfigure_stdout()
    parser = build_parser()
    parser.description = __doc__.split("\n\n")[0]
    args = parser.parse_args(argv)
    if not (args.root / "ledger.sqlite").is_file():
        print(f"{args.root}: not a campaign store", file=sys.stderr)
        return 2
    from sciagent.campaign.audit import check_sessions
    from sciagent.campaign.store import ResultStore

    records = select(load_split(args.split), args.truths, args.ids)
    env = pointproc_environment(load_split(args.split))
    config = campaign_config(args)
    with ResultStore(args.root) as store:
        check = check_sessions(store, env, config, truth_entries(records))
    for f in check.issues:
        print(f"ISSUE {f.kind} {f.where}: {f.message}")
    for f in check.warnings:
        print(f"warning {f.kind} {f.where}: {f.message}")
    print(
        f"sessions: {check.sessions}; found: {check.found}; at the current "
        f"address: {check.at_current}; issues: {len(check.issues)}; "
        f"warnings: {len(check.warnings)}"
    )
    print("SESSIONS FOUND" if check.passed else "SESSIONS INCOMPLETE")
    return 0 if check.passed else 1


if __name__ == "__main__":
    sys.exit(main())

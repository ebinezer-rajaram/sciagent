#!/bin/bash
# Render: branch · dirty count · §11 cursor · gate coverage.
#
# WHY A STATUSLINE AT ALL
#
# The cursor, the gate count and the dirty-path count are things you want in
# front of you continuously and never want to re-read as prose. A statusline is
# rendered by the harness, so it costs zero context -- unlike the SessionStart
# report, which pays for every line, every session. Moving the constant part of
# that report here is what lets session-start.sh stay quiet.
#
# WHY IT READS A CACHE INSTEAD OF status.py
#
# `uv run python scripts/status.py` is measured at 4.2s. A statusline re-renders
# far too often to pay that. session-start.sh leaves the two derived values in
# .cache/claude/statusline.txt; this reads them. They go stale only when the
# backlog moves, which is the same event that reprints the full report anyway.
#
# Git state is read live, because it changes constantly and is cheap.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
hook_cd_project || exit 0

# Own tree first, then the shared checkout's copy.
#
# session-start.sh writes this cache, and it runs at session start -- but a
# worktree is created *mid*-session by EnterWorktree, so it never has one of its
# own and the statusline read `cursor unknown - ? gates` for the whole session.
# Since worktree-per-session became the default that was most sessions, which is
# a poor showing for a line whose entire job is to save you re-reading the
# report.
#
# The two derived values are repository-wide facts -- the §11 cursor and how many
# gates have tests -- so the main tree's copy is a good answer for any tree. Own
# tree still wins when it has one, because a worktree whose branch moved the
# backlog should report itself rather than `main`.
LINE=".cache/claude/statusline.txt"
if [ ! -f "$LINE" ]; then
    main_tree="$(hook_main_tree)"
    [ -n "$main_tree" ] && [ -f "$main_tree/$LINE" ] && LINE="$main_tree/$LINE"
fi

branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')
dirty=$(git status --porcelain 2>/dev/null | grep -c .)

cursor="cursor unknown"
gates="?"
if [ -f "$LINE" ]; then
    cursor=$(sed -n '1p' "$LINE")
    gates=$(sed -n '2p' "$LINE")
fi

# Compress the two long cursor forms status.py emits into something that fits.
case "$cursor" in
    "every gate-tracked backlog item is satisfied") cursor="all gates satisfied" ;;
    "item "*) cursor="${cursor%% —*}" ;;
esac

if [ "$dirty" -gt 0 ] 2>/dev/null; then
    dirty_part="${dirty} dirty"
else
    dirty_part="clean"
fi

printf '%s · %s · %s · %s gates\n' "$branch" "$dirty_part" "$cursor" "$gates"

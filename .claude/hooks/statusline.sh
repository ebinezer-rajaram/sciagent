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

LINE=".cache/claude/statusline.txt"

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

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

# Read stdin before anything else can consume it. The statusline payload carries
# the model, context and cost figures appended at the end of the rendered line.
# Stdin is a stream, so a second reader gets nothing.
hook_read_payload
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

# ── Colour ───────────────────────────────────────────────────────────────────
#
# Semantic ANSI slots, never hardcoded hex. The terminal's own palette supplies
# the actual colours, so this line follows whatever scheme the terminal is set to
# and needs no editing when the editor theme changes -- Catppuccin Mocha and
# Tokyo Night each define all 16 slots, so `green` is that theme's green either
# way. Truecolor escapes would pin the statusline to one of the two.
D='\033[2m'; R='\033[0m'
GRN='\033[32m'; YEL='\033[33m'; RED='\033[31m'; CYN='\033[36m'; MAG='\033[35m'

# Detached HEAD is worth seeing immediately; `git rev-parse --abbrev-ref` reports
# it literally as HEAD, which is otherwise indistinguishable from a branch name.
if [ "$branch" = "HEAD" ]; then branch_c="$RED"; else branch_c="$GRN"; fi

if [ "$dirty_part" = "clean" ]; then dirty_c="$D"; else dirty_c="$YEL"; fi

case "$cursor" in
    "all gates satisfied") cursor_c="$D" ;;
    *)                     cursor_c="$CYN" ;;
esac

# ── Agent state ──────────────────────────────────────────────────────────────
#
# Appended rather than replacing anything. The four repository fields above are
# what this line existed for; the model/context/cost trio answers a different
# question -- how much room is left and what the session has cost -- that nothing
# else on screen answers at all.
#
# Each segment is emitted only when its field is present, because absence is
# normal rather than exceptional: before the first API response the payload
# carries `null` for both the context and the cost figure.
#
# The model name is matched inline rather than through hook_field. That function
# pays three subprocesses and normalises backslashes for file paths; a display
# name needs neither, and a line that re-renders on every assistant message
# cannot afford the former.
model=""
[[ $hook_payload =~ \"display_name\"[[:space:]]*:[[:space:]]*\"([^\"]*)\" ]] &&
    model="${BASH_REMATCH[1]}"

ctx=$(hook_field_num remaining_percentage)
cost=$(hook_field_num total_cost_usd)

agent=""
[ -n "$model" ] && agent="${agent}${D} · ${R}${MAG}${model}${R}"

if [ -n "$ctx" ]; then
    # Rounded rather than assumed integral: the payload documents a
    # pre-calculated percentage, and 20.6 must not render as `20.6%`.
    #
    # No guard on the result, because hook_field_num only returns a well-formed
    # number and so this cannot fail. An earlier version guarded on the output
    # being non-empty and the comment claimed a bad value would skip the segment
    # -- the opposite of the truth. `printf '%.0f' abc` prints `0` with a nonzero
    # status rather than printing nothing, so that guard was dead code hiding a
    # false red `0%`. Validating the shape at extraction is what makes it
    # unreachable rather than merely unlikely.
    ctx_i=$(printf '%.0f' "$ctx")
    if   [ "$ctx_i" -lt 10 ]; then ctx_c="$RED"
    elif [ "$ctx_i" -lt 25 ]; then ctx_c="$YEL"
    else                           ctx_c="$D"
    fi
    agent="${agent}${D} · ${R}${ctx_c}${ctx_i}%${R}"
fi

[ -n "$cost" ] && agent="${agent}${D} · \$$(printf '%.2f' "$cost")${R}"

printf '%b\n' "${branch_c}${branch}${R}${D} · ${R}${dirty_c}${dirty_part}${R}${D} · ${R}${cursor_c}${cursor}${R}${D} · ${gates} gates${R}${agent}"

#!/bin/bash
# Save the state a compaction is about to destroy, for session-start.sh to
# print back afterwards.
#
# WHY THIS IS A FILE AND NOT AN echo
#
# This hook used to be an inline `echo` in settings.json telling the model to
# append anything not derivable from the repository to docs/DECISIONS.md. That
# text has never reached the model. The hooks reference is explicit: "For most
# events, stdout is written to the debug log but not shown in the transcript.
# The exceptions are UserPromptSubmit, UserPromptExpansion, and SessionStart,
# where Claude Code adds plain-text stdout as context that Claude can see and
# act on." PreCompact is not one of the three, so it was shouting into the
# debug log for as long as it existed.
#
# SessionStart *is* one of the three, and since 2026-08-20 its matcher includes
# `compact`. So the route is: write here, print there. What crosses the gap is
# a file, which also makes it inspectable after the fact.
#
# WHAT GOES IN IT
#
# Only what a summary is most likely to drop AND is cheap to restate. Nothing
# that costs a subprocess of any weight -- `status.py` is 4.2s, and the cursor
# and gate count it produces are already sitting in the statusline cache that
# session-start.sh writes. Read that instead of recomputing it.
#
# The session id is recorded so the reader can tell its own compaction from a
# stale file left by an earlier session in the same tree. Without it a fresh
# `/clear` in a tree whose last session compacted would be handed another
# session's branch and cursor as though they were its own.

set -u
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
hook_cd_project || exit 0

hook_read_payload
transcript=$(hook_field transcript_path)
session=$(hook_field session_id)

CACHE_DIR=".cache/claude"
STATE="$CACHE_DIR/precompact-state.txt"
mkdir -p "$CACHE_DIR" 2>/dev/null || exit 0

branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
sha=$(git rev-parse --short HEAD 2>/dev/null)
subject=$(git log -1 --format=%s 2>/dev/null)
dirty=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')

# The cursor and gate count as session-start.sh last wrote them: line 1 cursor,
# line 2 gates with tests. Own tree first, then the main tree's copy -- the same
# resolution statusline.sh:42-47 uses, and for the reason its comment gives:
# these two are repository-wide facts, so the main tree's copy is a good answer
# for any tree, while a worktree whose branch moved the backlog should still
# report itself. Without the fallback a worktree whose SessionStart never got as
# far as writing the file -- a failing `status.py` exits it early -- carries
# `unknown` and `0` across the compaction, which is worse than saying nothing.
LINE=".cache/claude/statusline.txt"
if [ ! -f "$LINE" ]; then
    main_tree="$(hook_main_tree)"
    [ -n "$main_tree" ] && [ -f "$main_tree/$LINE" ] && LINE="$main_tree/$LINE"
fi
cursor=$(sed -n '1p' "$LINE" 2>/dev/null)
gates=$(sed -n '2p' "$LINE" 2>/dev/null)

if bash "$(dirname "${BASH_SOURCE[0]}")/suite-freshness.sh" check >/dev/null 2>&1; then
    freshness="a full suite run has passed on this exact tree"
else
    freshness="STALE — no green recorded for this tree; re-run before claiming one"
fi

# Written to a temporary and moved into place, the way suite-freshness.sh records
# a green. `{ ... } > "$STATE"` truncates before it writes, so a hook killed at
# its 30s timeout could leave a file holding only the `session:` line -- which is
# exactly the line session-start.sh matches on before printing. It would then
# print an empty state block and exit, suppressing the full report it should have
# fallen back to. A partial write must not look like a complete one.
tmp="$STATE.$$.tmp"
{
    printf 'session: %s\n' "${session:-unknown}"
    printf '### state carried across the compaction ###\n'
    printf '  branch  : %s @ %s %s\n' "${branch:-unknown}" "${sha:-unknown}" "${subject:-}"
    printf '  tree    : %s uncommitted path(s); %s\n' "${dirty:-?}" "$freshness"
    printf '  cursor  : %s\n' "${cursor:-unknown}"
    printf '  gates   : %s with tests\n' "${gates:-0}"
    [ -n "$transcript" ] && printf '  before  : %s\n' "$transcript"
    printf '\n'
    printf 'The summary above this line is lossy. Anything decided but not yet written\n'
    printf 'down is gone -- /decide the moment a decision is made, not at the end of a\n'
    printf 'session. Modified files and the commands that verified them are in the diff\n'
    printf 'and in the transcript named above, not in the summary; re-read rather than\n'
    printf 'recall them. Do not re-run scripts/status.py to orient -- the cursor is here.\n'
} > "$tmp" 2>/dev/null || { rm -f "$tmp" "$STATE"; exit 0; }

# Retried, for the reason EmpiricalTable.save retries its own atomic replace:
# this is Windows, and a transient sharing violation on a replace is a measured
# failure mode here, not a hypothetical (docs/DECISIONS.md, 2026-08-16).
#
# And on giving up, $STATE is REMOVED rather than left alone. A failed replace
# leaves the PREVIOUS compaction's file in place -- an older branch, an older
# dirty count, and an older suite verdict -- which session-start.sh would then
# print as this compaction's state. Nothing is strictly better than something
# stale here: the reader falls back to the full status report, which is correct
# and merely more expensive. This is the false-green shape suite-freshness.sh
# exists to refuse, and it must not be reintroduced through a different door.
for _ in 1 2 3 4 5 6 7 8; do
    mv -f "$tmp" "$STATE" 2>/dev/null && break
done
[ -f "$tmp" ] && rm -f "$tmp" "$STATE"

exit 0

#!/bin/bash
# Print the build status, but only at length when it has something new to say.
#
# WHY THIS WRAPPER EXISTS
#
# `scripts/status.py` prints ~45 lines: the SPEC §11 backlog, then one line per
# acceptance gate. That was worth reading at every session start while gates
# were still going green. They are all green now -- the cursor has read
# "every gate-tracked backlog item is satisfied" since item 14 landed -- so the
# same 45 lines arrive every session carrying no information, and cost context
# before a word is typed.
#
# So: diff against the previous run and print the whole report only when the
# backlog or the gates actually moved. Git state is deliberately excluded from
# that comparison, because the SHA and the dirty count change constantly and
# would make every session look "changed". They are on the statusline instead,
# which costs no context at all.
#
# This wraps `scripts/status.py` rather than modifying it. That keeps the tool
# free of presentation concerns, and -- since other sessions work this
# repository concurrently -- avoids editing a file somebody else may be holding.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
hook_cd_project || exit 0

CACHE_DIR=".cache/claude"
PREV="$CACHE_DIR/status-prev.txt"
LINE="$CACHE_DIR/statusline.txt"
STATE="$CACHE_DIR/precompact-state.txt"
mkdir -p "$CACHE_DIR" 2>/dev/null

# WHY THIS READS STDIN AT ALL
#
# The matcher is `startup|resume|clear|compact|fork`, and the five want different
# things. Four want the build status. The fifth -- `compact` -- has just lost the
# branch, the cursor and the gate count to a summary, and wants those back rather
# than a 45-line report it did not ask for and a 4.2s `status.py` call to produce
# it. The payload's `source` field is the only thing that distinguishes them, so
# it has to be read.
hook_read_payload
source_field=$(hook_field source)
session=$(hook_field session_id)

# A cloud session is the one case where an otherwise-situational document is
# always relevant, and `CLAUDE_CODE_REMOTE` is the documented signal -- "true"
# there, unset locally. Printing the pointer costs nothing on this machine and
# fires exactly when it matters, which is what lets docs/CLOUD.md live outside
# CLAUDE.md without becoming invisible to the sessions it governs.
cloud_pointer() {
    [ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || return 0
    echo "cloud session -- read docs/CLOUD.md before working. Not optional here:"
    echo "  push only from this session's own branch, and produce no numbers."
}

# After a compaction, hand back what the summary dropped and stop. The session id
# must match: a state file left by an earlier session in this tree describes that
# session's branch and cursor, and handing those to a fresh /clear would be worse
# than saying nothing. Falling through to the full report is the safe default.
# CONSUMED, not merely read. The state describes ONE compaction, and matching on
# the session id alone does not make it current: a second compaction in the same
# session whose hook fails to write -- any of pre-compact.sh's silent exits, or
# its 30s timeout -- would leave the FIRST compaction's file in place, still
# carrying that session id. It would then be replayed as though it described the
# tree now, including its `a full suite run has passed on this exact tree` line.
# That is the false green suite-freshness.sh exists to refuse, reached through a
# door it does not watch. Removing the file on the way out makes a replay
# impossible: a second compaction either writes fresh state or gets none, and
# getting none correctly falls through to the full report.
if [ "$source_field" = "compact" ] && [ -f "$STATE" ]; then
    if [ "$(sed -n 's/^session: //p' "$STATE" | head -1)" = "$session" ]; then
        block=$(sed '1d' "$STATE")
        rm -f "$STATE"
        cloud_pointer
        printf '%s\n' "$block"
        exit 0
    fi
    # Another session's compaction. It can never become valid here, so drop it
    # rather than leaving it to be matched by some later session's id.
    rm -f "$STATE"
fi

cloud_pointer

report=$(uv run python scripts/status.py 2>&1) || { printf '%s\n' "$report"; exit 0; }

# The comparable part: everything except the two lines carrying git state.
# `status.py` prints them as "  <branch> @ <sha> <subject>" and
# "  N uncommitted path(s)" near the top.
stable=$(printf '%s\n' "$report" | grep -vE '^  [^ ]+ @ [0-9a-f]{7}|uncommitted path\(s\)')

# Feed the statusline: the cursor line, trimmed, plus how many gates have tests.
cursor=$(printf '%s\n' "$report" | sed -n 's/^cursor: //p' | head -1)
# Gates with tests, which is not the same as gate rows printed. The report also
# names every gate the cursor is blocked on in full, marked "next up, not yet
# written", so counting rows overstates by however many those are -- one for a
# BACKLOG entry, which carries a single gate, but a SPEC §11 item may name a
# range and block on several. Excluding the marker covers all of them.
# This used to be invisible: with §11 satisfied there was no cursor and no
# blocking gate. Since the cursor falls through to docs/BACKLOG.md there is
# always at least one, and the count was high for every session until this.
gates=$(printf '%s\n' "$report" | grep -E '^  A[0-9]+ ' | grep -cv 'not yet written')
printf '%s\n%s\n' "${cursor:-unknown}" "${gates:-0}" > "$LINE" 2>/dev/null

if [ -f "$PREV" ] && printf '%s\n' "$stable" | diff -q - "$PREV" >/dev/null 2>&1; then
    echo "sciagent — backlog and gates unchanged since last session (${gates} gates with tests)"
    echo "cursor: ${cursor:-unknown}"
    echo "  full report: uv run python scripts/status.py   (--run to verify by execution)"
else
    if [ -f "$PREV" ]; then
        echo "### status changed since last session — full report follows ###"
    fi
    printf '%s\n' "$report"
fi

printf '%s\n' "$stable" > "$PREV" 2>/dev/null
exit 0

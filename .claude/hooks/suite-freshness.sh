#!/bin/bash
# Has the full suite already passed on exactly this tree?
#
#   .claude/hooks/suite-freshness.sh begin    -> snapshot the tree, before pytest
#   .claude/hooks/suite-freshness.sh record   -> promote that snapshot to green
#   .claude/hooks/suite-freshness.sh check    -> exit 0 if fresh, 1 if not
#
# `begin` and `record` are a pair and `record` refuses without one. That is not
# ceremony -- see CONCURRENT EDITS below for the run that made it necessary.
#
# WHY
#
# `/next` runs the suite, hands to `/ship`, which runs it again, and `/ship`
# then re-runs it after the review. Three full runs per backlog item, at a
# measured 6m50s each, and the first two are routinely on a byte-identical tree.
# That is ~14 minutes of the ~21 spent re-answering a question already answered.
#
# WHY IT FAILS TOWARD RUNNING
#
# A wrong "fresh" verdict means shipping code the suite never saw, on a project
# whose whole discipline is that looking correct is not evidence. So every
# uncertain path here exits 1: a missing record, an unreadable file, a hashing
# command that fails, a different commit. The cost of a false "stale" is seven
# minutes; the cost of a false "fresh" is a broken guarantee. They are not
# comparable, and this is not a close call.
#
# WHAT THE HASH COVERS
#
# Every .py under src, tests and scripts -- which is what RANDOMNESS_ROOTS and
# mypy's configured file set both cover -- plus pyproject.toml (test config,
# ruff and mypy settings) and uv.lock (the dependency set the result depends
# on). Content, not mtime: a touched file with identical bytes cannot change a
# test outcome, and mtimes churn for reasons that are not edits.
#
# It deliberately does NOT cover docs/. A DECISIONS.md entry cannot change a
# test result, and treating it as though it could would defeat the whole
# mechanism, since /decide runs between the two suite invocations by design.
#
# CONCURRENT EDITS -- why `record` alone was not enough
#
# The first version of this script recorded the tree as it stood when `record`
# was called. That is the wrong instant. On 2026-08-15 a suite ran 18:00-18:07
# while a second session added a dependency at 18:05:56; `record` at 18:07:10
# hashed the new pyproject.toml and uv.lock and certified a tree the suite had
# never executed against. Nothing in the mechanism noticed, because the tree it
# compared against was the one in front of it rather than the one pytest saw.
#
# Two sessions in one working tree is normal here, so this is a live hazard and
# not a thought experiment. `begin` fixes it by pinning the hash *before* pytest
# starts; `record` promotes that pin only if the tree still matches it, and
# refuses otherwise. A run overlapped by somebody else's edit now yields no
# green at all, which is the correct answer -- the run does not describe any
# single state of the tree, so there is nothing truthful to record.
#
# What this still does not verify is that pytest ran at all, or passed. The
# caller asserts that. Closing it would mean this script owning the run, which
# would forfeit backgrounding -- the thing that makes a 6m30s suite tolerable.
# Worktree-per-session removes the hazard at the root rather than detecting it.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# Fatal, not advisory. Hashing the wrong tree is how this reports a green that
# never happened -- see the count check below.
hook_cd_project || {
    echo "suite-freshness: not in the sciagent tree; refusing to answer" >&2
    exit 1
}

RECORD=".cache/claude/last-green.txt"
PENDING=".cache/claude/pending-run.txt"

# Fewer than this many .py files means the search did not find the tree, not
# that the tree shrank. The repository has ~107; mypy reports the same number.
# The bound is deliberately far below that so it never trips on real deletions.
MIN_FILES=50

file_count=$(find src tests scripts -name '*.py' -type f 2>/dev/null | grep -c .)

tree_hash() {
    {
        find src tests scripts -name '*.py' -type f -exec sha256sum {} + 2>/dev/null | sort
        sha256sum pyproject.toml uv.lock 2>/dev/null
    } | sha256sum | cut -d' ' -f1
}

current=$(tree_hash)

# Two checks, because the length check alone cannot do the job. sha256 of an
# empty input is still 64 characters -- e3b0c442...b855, the well-known digest
# of nothing -- so a run that hashed zero files produces a perfectly valid
# hash, and `record` then `check` in that state agree with each other and
# report FRESH. That is the exact false-green this script exists to prevent,
# and only the file count catches it.
[ "${#current}" -eq 64 ] || exit 1
[ "$file_count" -ge "$MIN_FILES" ] || {
    echo "suite-freshness: found only $file_count .py files, expected >= $MIN_FILES" >&2
    exit 1
}

case "$1" in
    check)
        [ -f "$RECORD" ] || exit 1
        recorded=$(head -1 "$RECORD" 2>/dev/null)
        [ "$recorded" = "$current" ] || exit 1
        exit 0
        ;;
    begin)
        mkdir -p "$(dirname "$PENDING")" 2>/dev/null || exit 1
        printf '%s\n' "$current" > "$PENDING" || exit 1
        echo "suite-freshness: pinned tree $current — run pytest, then 'record'"
        exit 0
        ;;
    record)
        # No pin means either `begin` was skipped or a previous `record`
        # consumed it. Either way there is no evidence about which tree the run
        # saw, and inventing one is the failure this pairing exists to prevent.
        if [ ! -f "$PENDING" ]; then
            echo "suite-freshness: no pin from 'begin'; refusing to record a green." >&2
            echo "  Run 'begin' before pytest so the tree can be pinned, then 'record'." >&2
            exit 1
        fi
        pinned=$(head -1 "$PENDING" 2>/dev/null)
        if [ "$pinned" != "$current" ]; then
            rm -f "$PENDING"
            {
                echo "suite-freshness: the tree changed while the suite ran; NOT recording a green."
                echo "  pinned at start : $pinned"
                echo "  now             : $current"
                echo "  Another session almost certainly edited a tracked file mid-run, so the"
                echo "  result does not describe any single tree. Re-run on a settled tree."
            } >&2
            exit 1
        fi
        mkdir -p "$(dirname "$RECORD")" 2>/dev/null || exit 1
        printf '%s\n' "$current" > "$RECORD" || exit 1
        rm -f "$PENDING"
        echo "recorded green suite for tree $current"
        exit 0
        ;;
    *)
        echo "usage: suite-freshness.sh begin|record|check" >&2
        exit 1
        ;;
esac

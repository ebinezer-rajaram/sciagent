#!/bin/bash
# Has the full suite already passed on exactly this tree?
#
#   .claude/hooks/suite-freshness.sh check    -> exit 0 if fresh, 1 if not
#   .claude/hooks/suite-freshness.sh record   -> mark the current tree green
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

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# Fatal, not advisory. Hashing the wrong tree is how this reports a green that
# never happened -- see the count check below.
hook_cd_project || {
    echo "suite-freshness: not in the sciagent tree; refusing to answer" >&2
    exit 1
}

RECORD=".cache/claude/last-green.txt"

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
    record)
        mkdir -p "$(dirname "$RECORD")" 2>/dev/null || exit 1
        printf '%s\n' "$current" > "$RECORD" || exit 1
        echo "recorded green suite for tree $current"
        exit 0
        ;;
    *)
        echo "usage: suite-freshness.sh check|record" >&2
        exit 1
        ;;
esac

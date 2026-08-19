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
# `/next` runs the suite, hands to `/preflight`, which would run it again, and
# would re-run it after the review. Three full runs per backlog item, at a
# measured 6m50s each, and the first two are routinely on a byte-identical tree.
# That is ~14 minutes of the ~21 spent re-answering a question already answered.
# (Those three runs were `/next` plus `/ship` steps 1-2 when this was measured.
# The verify/ship split moved where the runs live, not how many there are.)
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
# It deliberately does NOT cover docs/ in general. A DECISIONS.md entry cannot
# change a test result, and treating it as though it could would defeat the
# whole mechanism, since /decide runs between the two suite invocations by
# design.
#
# NON-PYTHON INPUTS -- the exception that premise now has
#
# "A doc cannot change a test result" stopped being true on 2026-08-19, when
# gates A38 and A39 landed acceptance tests that read files no .py glob covers:
# `tests/acceptance/test_a39.py` reads `docs/SCALE-UP.md`, and
# `tests/acceptance/test_a38.py` reads `LICENSE` and `.github/workflows/`. Under
# the original hash, deleting a section of SCALE-UP.md left `check` reporting
# FRESH while A39 was red -- the exact false green this script exists to prevent,
# reintroduced through a door it was not watching.
#
# So the rule is not "code only", it is *every input an acceptance gate reads*.
# The three named below are that set today. Anything that adds a gate over a
# non-.py file belongs here in the same commit as the gate; the general docs/
# exclusion survives because nothing asserts over the rest of it.
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
# would forfeit backgrounding -- the thing that makes a minutes-long suite
# tolerable. (Delegating the run to the suite-runner agent is the other way to
# get that back; it owns the run without this script having to.)
# Worktree-per-session removes the hazard at the root rather than detecting it.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# Fatal, not advisory. Hashing the wrong tree is how this reports a green that
# never happened -- see the count check below.
hook_cd_project || {
    echo "suite-freshness: not in the sciagent tree; refusing to answer" >&2
    exit 1
}

# The green record is SHARED between worktrees; the pin is not. That split is
# the whole design, and it follows from what each one means.
#
# A green says "the full suite passed on this exact content". The key is a hash
# of the content, so the fact is global -- if some tree verified that hash, any
# tree holding it is verified, and which directory ran pytest is irrelevant.
# Keeping it per-tree meant a fresh worktree had no record at all and `check`
# exited 1 on the missing file, so every new worktree paid a full ~7-minute
# suite for its first ship no matter how little it changed -- even when it was
# byte-identical to a main tree that had just gone green.
#
# A pin is the opposite: it names one in-flight run in one tree. Sharing it
# would let two worktrees overwrite each other's pin, and `record` would then
# compare against somebody else's starting hash -- silently reintroducing the
# false green the pairing exists to prevent.
#
# The record is a SET, one hash per line, not a single value. With one slot,
# worktree A going green erased worktree B's, and B re-ran seven minutes to
# rediscover something already known. `check` therefore matches any recorded
# line, and a one-line file from the previous format still reads correctly.
#
# SHARING DEPENDS ON LINE ENDINGS, which is not obvious and was not free. The
# hash is over bytes, so two trees at the same commit share a green only if they
# agree byte for byte. They did not: `.gitattributes` asks for `eol=lf`, a fresh
# worktree checkout obeys it, and the main working tree did not -- it held 71 of
# 140 tracked files as CRLF, checked out before that rule landed. `git status`
# reported clean throughout, because git normalises CRLF away on read. The two
# trees hashed differently at the same commit and no worktree could ever reuse
# the main tree's green, which is exactly the case this sharing was written for.
# Refreshing the main tree's working files settled it; see DECISIONS 2026-08-16.
# If a tree ever drifts back, the symptom is a permanent STALE that no amount of
# re-running fixes: compare `wc -c` against `git show HEAD:<path> | wc -c`.
main_tree="$(hook_main_tree)"
if [ -n "$main_tree" ]; then
    CLAUDE_CACHE="$main_tree/.cache/claude"
else
    CLAUDE_CACHE=".cache/claude"
fi

RECORD="$CLAUDE_CACHE/last-green.txt"
PENDING=".cache/claude/pending-run.txt"

#: How many green trees to remember. Enough for several worktrees plus a little
#: history; the file is one 64-character line each, so the cap is about hygiene
#: rather than space.
KEEP_GREENS=20

# Fewer than this many .py files means the search did not find the tree, not
# that the tree shrank. The repository has ~107; mypy reports the same number.
# The bound is deliberately far below that so it never trips on real deletions.
MIN_FILES=50

file_count=$(find src tests scripts -name '*.py' -type f 2>/dev/null | grep -c .)

tree_hash() {
    {
        find src tests scripts -name '*.py' -type f -exec sha256sum {} + 2>/dev/null | sort
        sha256sum pyproject.toml uv.lock 2>/dev/null
        # The non-.py files an acceptance gate reads. See NON-PYTHON INPUTS above.
        find .github/workflows -type f 2>/dev/null -exec sha256sum {} + | sort
        sha256sum LICENSE docs/SCALE-UP.md 2>/dev/null
        # And the git *index* for those same paths, because two of the gates
        # assert tracked-ness rather than content: `test_a38_every_named_path_is_
        # tracked` and `test_a39_the_document_is_tracked` shell out to
        # `git ls-files --error-unmatch`. `git rm --cached LICENSE` leaves every
        # byte on disk, so the content hashes above do not move and `check`
        # reported FRESH while A38 was red -- the same false green the section
        # above closed, one door further along. Found by review, not by the hook.
        git ls-files -s LICENSE docs/SCALE-UP.md .github/workflows 2>/dev/null
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
        # -F -x: a hash is a literal and must match the whole line, so no part
        # of one recorded hash can satisfy a query for another.
        grep -Fxq "$current" "$RECORD" 2>/dev/null || exit 1
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
        # Newest first, previous entries kept, duplicates dropped. Written via a
        # temporary and moved into place, so a *reader* sees either the old set
        # or the new one, never a half-written file. $$ keeps concurrent writers
        # off each other's temporary.
        #
        # It is still a read-modify-write, so two trees recording at the same
        # instant can lose one of the two greens. That is left alone: the loser
        # re-runs a suite it need not have, which costs seven minutes and no
        # correctness, and closing it means a lock file whose own failure modes
        # are worse than the thing it prevents.
        tmp="$RECORD.$$.tmp"
        {
            printf '%s\n' "$current"
            [ -f "$RECORD" ] && grep -Fxv "$current" "$RECORD" 2>/dev/null
        } | head -n "$KEEP_GREENS" > "$tmp" || { rm -f "$tmp"; exit 1; }
        mv -f "$tmp" "$RECORD" || { rm -f "$tmp"; exit 1; }
        rm -f "$PENDING"
        echo "recorded green suite for tree $current"
        exit 0
        ;;
    *)
        echo "usage: suite-freshness.sh begin|record|check" >&2
        exit 1
        ;;
esac

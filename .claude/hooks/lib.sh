# Shared helpers for this project's hooks. Source it; do not execute it.
#
# Hook input arrives as JSON on stdin. The obvious parse is Python, and that is
# what these hooks used to do, but Python interpreter startup on Windows is
# measured at 517ms per call against 38ms for sed -- and a PostToolUse hook pays
# that on *every* edit. The whole ruff hook was 402ms, of which ~350ms was
# waiting for `import json`.
#
# So: sed for the flat string fields, and Python only where correctness needs
# it, behind a cheap pre-filter that almost always short-circuits first.

# Read stdin once into hook_payload. Stdin is a stream; a second reader gets
# nothing, so every field extraction below works off this one capture.
hook_read_payload() {
    hook_payload=$(cat)
}

# cd to the repository root, or fail. Returns nonzero if it cannot get there.
#
# The naive form of this -- `cd "$CLAUDE_PROJECT_DIR" || cd <fallback>` -- is
# broken, and subtly enough that it shipped once already. When the variable is
# unset the expansion is the empty string, and `cd ""` *succeeds* in bash as a
# no-op returning 0, so the `||` fallback is dead code and the script carries on
# in whatever directory it happened to start in. `/ship` invokes these hooks
# with CLAUDE_PROJECT_DIR unset, so this is the normal case, not an edge one.
#
# Guarding on emptiness is necessary but nowhere near sufficient: a caller could
# be anywhere, and one candidate being non-empty says nothing about it being the
# right tree. Each candidate below is therefore *validated* before it is
# accepted, which is also what keeps the later fallbacks reachable at all --
# `dirname ""` returns `.`, so a candidate is essentially never empty and an
# emptiness guard would make every branch after the first dead code.
#
# WHICH tree, when there are several. One git worktree per concurrent session
# means neither CLAUDE_PROJECT_DIR nor BASH_SOURCE is a synonym for "the tree
# this invocation is about":
#
#   - CLAUDE_PROJECT_DIR can name the main tree while the session edits a
#     worktree. Measured: the old precedence then resolved to the main tree,
#     whose hash differs, so suite-freshness.sh pinned and checked against a tree
#     the run never touched -- the false green it exists to prevent.
#   - BASH_SOURCE does not fix that, because settings.json invokes these hooks as
#     `bash "$CLAUDE_PROJECT_DIR/.claude/hooks/X.sh"`. The script path is then an
#     absolute path into the *main* tree however the session was started, so
#     guard-determinism.sh would check invariant 3 against the wrong tree and
#     report clean. Measured on 2026-08-16: settings.json form -> main tree,
#     manual form -> worktree, for the same session.
#
# So ask git, which is the only participant that actually knows. The tree
# containing the *edited file* is the most precise answer, and callers with a
# file in hand pass it as $1; otherwise the session's cwd decides. Cost is 0.04s
# against the 0.038s `sed` already accepted per edit -- see the 0.517s Python
# startup rejected above -- so this is affordable in a PostToolUse hook.
_hook_is_project_root() {
    [ -n "$1" ] && [ -d "$1" ] &&
        [ -f "$1/pyproject.toml" ] && [ -d "$1/src" ] && [ -d "$1/tests" ]
}

hook_cd_project() {
    local hint="${1:-}" candidate="" root=""

    # 1. The tree containing the file being edited. Absolute paths only: a
    #    relative file_path is meaningless until after the cd, which is the very
    #    thing being decided here.
    case "$hint" in
        /* | [A-Za-z]:[/\\]*)
            if [ -e "$hint" ]; then
                candidate="$(git -C "$(dirname "$hint")" rev-parse --show-toplevel 2>/dev/null)"
                _hook_is_project_root "$candidate" && root="$candidate"
            fi
            ;;
    esac

    # 2. The tree the session is working in.
    if [ -z "$root" ]; then
        candidate="$(git rev-parse --show-toplevel 2>/dev/null)"
        _hook_is_project_root "$candidate" && root="$candidate"
    fi

    # 3. The tree this script was read from.
    if [ -z "$root" ]; then
        candidate="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)"
        _hook_is_project_root "$candidate" && root="$candidate"
    fi

    # 4. Last resort, for a caller with no git, no readable script path and no
    #    usable cwd. Reachable now only because each branch above validates.
    if [ -z "$root" ] && [ -n "${CLAUDE_PROJECT_DIR:-}" ]; then
        _hook_is_project_root "$CLAUDE_PROJECT_DIR" && root="$CLAUDE_PROJECT_DIR"
    fi

    [ -n "$root" ] || return 1
    cd "$root" 2>/dev/null
}

# Print the path of the shared checkout -- the main working tree -- or nothing.
# Returns nonzero when it cannot be resolved.
#
# Every worktree shares one `.git`, and `--git-common-dir` names it: a relative
# `.git` from the main tree, an absolute path to it from a worktree. Its parent
# is therefore the main tree from anywhere in the repository, which is what makes
# a cache written by one tree findable by all of them with nothing to configure.
#
# Validated the same way hook_cd_project validates its candidates, and for the
# same reason: a tree lacking its own `.git` would otherwise resolve the enclosing
# repository's root and put this repository's cache in somebody else's.
hook_main_tree() {
    local common="" main_tree=""
    common="$(git rev-parse --git-common-dir 2>/dev/null)" || return 1
    [ -n "$common" ] && [ -d "$common" ] || return 1
    main_tree="$(cd "$common/.." 2>/dev/null && pwd)" || return 1
    _hook_is_project_root "$main_tree" || return 1
    printf '%s' "$main_tree"
}

# Extract a flat JSON string field by name. Safe for `file_path` and other
# values that contain no escaped quotes -- which is every path, since a quote is
# not legal in a Windows path and would be unusual in a POSIX one. Not safe for
# `content` or `new_string`, which is why nothing here uses it for those.
#
# Backslashes arrive JSON-escaped (C:\\Users\\x). Git Bash accepts forward
# slashes throughout, so normalise rather than trying to unescape in place.
hook_field() {
    printf '%s' "$hook_payload" \
        | sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" \
        | head -1 \
        | sed 's|\\\\|/|g'
}

# True when the path is one the determinism invariant governs. These mirror
# RANDOMNESS_ROOTS in tests/test_invariants.py -- (src, scripts, tests) -- and
# must keep mirroring it: a guard scoped narrower than the gate it enforces
# reports "clean" for edits the gate would reject.
#
# Including `tests` is safe despite tests/test_invariants.py naming
# `np.random.normal` in its own string literals, because the check this gates is
# an AST walk. A literal is a Constant node, never an Attribute, so it cannot
# match. That is precisely the reason this hook defers to the real test instead
# of grepping.
#
# Accepts absolute and relative forms and both slash conventions, because the
# payload's shape depends on how the caller addressed the file.
hook_path_governed_by_determinism() {
    case "${1//\\//}" in
        */src/*|src/*|*/scripts/*|scripts/*|*/tests/*|tests/*) return 0 ;;
        *) return 1 ;;
    esac
}

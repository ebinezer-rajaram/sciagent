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
# Guarding on emptiness is therefore necessary but still not sufficient: a
# caller could be anywhere. The landmark check is what makes the result
# trustworthy, and callers whose correctness depends on being in the right tree
# must treat failure here as fatal rather than carrying on.
hook_cd_project() {
    local root=""
    if [ -n "${CLAUDE_PROJECT_DIR:-}" ] && [ -d "${CLAUDE_PROJECT_DIR}" ]; then
        root="$CLAUDE_PROJECT_DIR"
    else
        root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)"
    fi
    [ -n "$root" ] || return 1
    cd "$root" 2>/dev/null || return 1
    # Landmarks: being in *a* directory is not being in *this* repository.
    [ -f pyproject.toml ] && [ -d src ] && [ -d tests ]
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

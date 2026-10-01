#!/bin/bash
# PostToolUse on Edit|Write: format the edited Python file, then check the
# determinism invariant if the edit mentions randomness.
#
# The guard runs the real AST test (tests/test_invariants.py) instead of
# grepping, so the hook and the test can never disagree. Exit 2 puts the message
# in front of the model so a violation is fixed in the same turn.

set -u
payload=$(cat)

# file_path is a flat JSON string; sed is ~10x cheaper than starting Python.
file_path=$(printf '%s' "$payload" \
    | sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
    | head -1 | sed 's|\\\\|/|g')

case "$file_path" in *.py) ;; *) exit 0 ;; esac
[ -e "$file_path" ] || exit 0

# Resolve the tree from the edited file, not from the script path: settings.json
# names the main tree's hook even when the session is editing a worktree.
root=$(git -C "$(dirname "$file_path")" rev-parse --show-toplevel 2>/dev/null)
[ -n "$root" ] && [ -f "$root/pyproject.toml" ] || exit 0
cd "$root" || exit 0

# Formatting is a convenience: bounded, and never blocks.
if command -v timeout >/dev/null 2>&1; then t="timeout 30"; else t=""; fi
$t uv run ruff format --force-exclude "$file_path" >/dev/null 2>&1
$t uv run ruff check --fix --force-exclude "$file_path" >/dev/null 2>&1

# Only paths the invariant governs (RANDOMNESS_ROOTS in tests/test_invariants.py).
case "$file_path" in */src/*|*/scripts/*|*/tests/*|src/*|scripts/*|tests/*) ;; *) exit 0 ;; esac

# Cheap pre-filter; `default_rng` is separate because it contains no "random".
printf '%s' "$payload" | grep -qE "random|default_rng" || exit 0

output=$(uv run pytest tests/test_invariants.py -k no_unseeded_randomness -q 2>&1)
status=$?
[ "$status" -eq 0 ] && exit 0

if [ "$status" -ne 1 ]; then
    # 5 = no tests selected (the test was renamed); others are harness errors.
    echo "after-edit: could not check determinism (pytest exit $status); this is the hook failing, not your edit." >&2
    printf '%s\n' "$output" | tail -15 >&2
    exit 2
fi

{
    echo "DETERMINISM VIOLATED by the edit to $file_path"
    echo "All randomness must go through explicitly passed seeded generators:"
    echo "never \`random.\`, never bare \`np.random.<dist>\`, never \`default_rng()\` without a seed."
    printf '%s\n' "$output" | tail -25
} >&2
exit 2

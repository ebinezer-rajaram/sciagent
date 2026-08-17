#!/bin/bash
# Catch a violation of invariant 3 at the edit that causes it, not at the suite
# run seven minutes later.
#
# WHY THIS RUNS THE REAL TEST INSTEAD OF ITS OWN GREP
#
# `tests/test_invariants.py::test_no_unseeded_randomness` already decides this
# question, and it decides it by AST: it knows SEEDED_CONSTRUCTORS, it catches
# `np.random.<attr>`, `from numpy.random import <dist>` and
# `import numpy.random as r` alike, and it flags a bare `default_rng()` whose
# missing seed makes it global randomness wearing a seeded name.
#
# A shell approximation of that would be a second, worse definition of the
# invariant, free to drift from the one the gate enforces. A guard that
# disagrees with the gate is worse than no guard. So this runs the gate.
#
# WHY PostToolUse AND NOT PreToolUse
#
# PreToolUse fires before the write lands, so the file on disk still holds the
# old content and the test would pass vacuously. Running after the write means
# this does not prevent the edit -- it reports it immediately, with exit 2, so
# the violation is fixed in the same breath rather than surfacing at `/preflight`.
#
# COST, measured on this machine
#
#   payload pre-filter (no "random" anywhere)      ~40ms   <- the common case
#   full test when the pre-filter hits            1.84s    (107 files, by AST)
#
# The expensive branch is only reached by an edit that actually mentions
# randomness, which is rare, so the amortised cost is the pre-filter.

. "$(dirname "$0")/lib.sh"
hook_read_payload

file_path=$(hook_field file_path)

case "$file_path" in
    *.py) ;;
    *) exit 0 ;;
esac

# Scoped to exactly what the gate covers -- RANDOMNESS_ROOTS is
# (src, scripts, tests), not src alone. An earlier draft of this hook watched
# only src/, which would have reported "clean" for a scripts/ edit the suite
# then failed on.
hook_path_governed_by_determinism "$file_path" || exit 0

# The pre-filter. Matching the raw payload rather than the parsed content field
# is deliberate: JSON escaping does not disturb these substrings, and this costs
# one grep instead of an interpreter start. It over-matches -- an edit that
# *removes* randomness also hits -- and that is the safe direction, because a
# false hit costs 1.84s while a false miss costs the invariant.
#
# `default_rng` is matched separately and is not redundant: it contains no
# substring `random`, so an edit whose only change is dropping the seed from
# `default_rng(seed)` to `default_rng()` would otherwise slip through -- and an
# unseeded default_rng is precisely one of the three things the gate flags.
printf '%s' "$hook_payload" | grep -qE "random|default_rng" || exit 0

# Fatal rather than advisory: a guard that silently disables itself when it
# cannot find the tree is worse than no guard, because the absence of a
# complaint reads as a pass.
# The edited file's own tree, not this script's. settings.json invokes hooks by
# an absolute path into the main tree, so without the hint a worktree session
# would run the AST test over the main tree and report clean -- a guard that
# passes because it looked somewhere else, which is the failure this block's own
# comment calls worse than no guard.
hook_cd_project "$file_path" || {
    echo "guard-determinism: not in the sciagent tree; invariant 3 NOT checked" >&2
    exit 2
}

output=$(uv run pytest tests/test_invariants.py -k no_unseeded_randomness -q 2>&1)
status=$?

[ "$status" -eq 0 ] && exit 0

# Only exit 1 means tests ran and failed. Everything else is the harness itself
# going wrong -- 5 is "no tests selected", which is what happens the day the
# test gets renamed, and 2/3/4 are interrupts, internal errors and usage
# mistakes. Reporting any of those as "INVARIANT 3 VIOLATED by your edit" would
# be a lie about the user's code, and the renaming case would make every edit
# touching randomness scream until somebody worked out why.
if [ "$status" -ne 1 ]; then
    {
        echo "guard-determinism: could not check invariant 3 (pytest exit $status)."
        echo "This is the guard failing, NOT a violation in $file_path."
        [ "$status" -eq 5 ] && echo "Exit 5 = no tests selected: has test_no_unseeded_randomness been renamed?"
        printf '%s\n' "$output" | tail -15
    } >&2
    exit 2
fi

# Exit 2 puts this on stderr and in front of the model, which is the point:
# invariant 3 is non-negotiable and the fix belongs in this turn.
{
    echo "INVARIANT 3 VIOLATED by the edit to $file_path"
    echo
    echo "All randomness must go through explicitly passed seeded generators."
    echo "Never \`random.\`, never a bare \`np.random.<dist>\`, and never"
    echo "\`default_rng()\` with no seed -- see CLAUDE.md invariant 3."
    echo
    printf '%s\n' "$output" | tail -25
} >&2

exit 2

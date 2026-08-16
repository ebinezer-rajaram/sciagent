#!/bin/bash
# Run the two post-edit hooks in sequence. Ordering is the whole point.
#
# WHY THIS WRAPPER EXISTS
#
# Claude Code runs every hook matching an event *in parallel*, and offers no way
# to order them -- there is no "sequence" field, and array order in settings.json
# means nothing. The documented remedy for a dependency between two hooks is to
# make them one hook, which is this file.
#
# The dependency is real. `ruff-after-edit.sh` rewrites the edited file while
# `guard-determinism.sh` walks the same tree by AST. The AST walk reads every
# .py under src, scripts and tests with `path.read_text()`; a file caught
# mid-rewrite parses as a SyntaxError, which raises *inside the test*. pytest
# reports that as a failing test and exits 1 -- and exit 1 is precisely the
# status guard-determinism.sh is entitled to read as a genuine violation. It
# would then print "INVARIANT 3 VIOLATED by the edit to <file>" about an edit
# that was fine, and the accusation would be unreproducible, because re-running
# it against the settled file passes.
#
# The window is narrow -- ruff is ~100ms against the guard's 1.84s, and the
# guard's pre-filter skips ~all edits before either runs -- which makes it worse
# to leave, not better: a rare wrong answer about a non-negotiable invariant is
# the kind that gets believed.
#
# COST OF SERIALISING
#
# None worth measuring. On the common path the pre-filter exits in ~40ms and the
# total is ruff's ~100ms, exactly as before. Only an edit that actually mentions
# randomness pays both, and that edit was going to pay 1.84s anyway.

set -u
here="$(dirname "${BASH_SOURCE[0]}")"

# Stdin is a stream: whoever reads it first consumes it. Capture once, feed both.
payload=$(cat)

# Formatting first, so the guard judges the file as it will finally stand.
# This hook always exits 0 by design -- it is a convenience, not a gate -- so
# its status is deliberately not propagated.
#
# Bounded, though, and that is not decoration. Serialising puts ruff ahead of the
# guard inside ONE timeout: while they ran as separate hooks a wedged ruff cost
# only formatting, but in sequence it would eat the whole 90s budget and the
# invariant-3 check would never run at all -- silently, since a killed hook
# reports nothing. The gate must not be starved by the convenience in front of
# it. 30s is ~300x the measured ~100ms.
if command -v timeout >/dev/null 2>&1; then
    printf '%s' "$payload" | timeout 30 bash "$here/ruff-after-edit.sh"
else
    printf '%s' "$payload" | bash "$here/ruff-after-edit.sh"
fi

# The guard's status is the one that matters. Exit 2 puts its stderr in front of
# the model, which is how a violation gets fixed in the same turn; exit 2 is
# also how it reports being unable to check. Pass it through unchanged.
printf '%s' "$payload" | bash "$here/guard-determinism.sh"
exit $?

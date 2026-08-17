#!/bin/bash
# Format and autofix the single Python file just edited.
#
# The edited path arrives as JSON on stdin (.tool_input.file_path); there is no
# environment variable for it. jq is not installed on this machine. The parse
# used to go through Python, which cost 517ms of interpreter startup per edit
# out of a 402ms total -- see .claude/hooks/lib.sh for the measurement and why
# sed replaced it. Measured after: ~100ms per edit.
#
# --force-exclude is load-bearing: passing an explicit path to ruff otherwise
# bypasses the `extend-exclude = ["docs"]` in pyproject.toml, which exists to
# stop ruff reformatting the Python blocks inside the frozen docs/SPEC.md.
#
# Always exits 0. This hook is a convenience, not a gate; `/preflight` runs
# `ruff check .` as the real check and is where an unfixable finding surfaces.

. "$(dirname "$0")/lib.sh"
hook_read_payload
file_path=$(hook_field file_path)

case "$file_path" in
    *.py) ;;
    *) exit 0 ;;
esac

# cd first: a relative file_path is only meaningful once the working directory
# is the repository root, so testing existence before the cd would resolve it
# against wherever the hook happened to start.
#
# The path is passed as a hint so the *file's own* tree wins. With one worktree
# per session the alternatives all name the wrong one: settings.json invokes
# this hook by an absolute path into the main tree, so neither BASH_SOURCE nor
# CLAUDE_PROJECT_DIR distinguishes the tree being edited. A relative hint is
# ignored by hook_cd_project, which is the case this comment is about.
hook_cd_project "$file_path" || exit 0
[ -f "$file_path" ] || exit 0

uv run ruff format --force-exclude "$file_path" >/dev/null 2>&1
uv run ruff check --fix --force-exclude "$file_path" >/dev/null 2>&1

exit 0

#!/bin/bash
# Format and autofix the single Python file just edited.
#
# The edited path arrives as JSON on stdin (.tool_input.file_path); there is no
# environment variable for it. jq is not installed on this machine, so the
# parse goes through Python, which is guaranteed present in a uv project.
#
# --force-exclude is load-bearing: passing an explicit path to ruff otherwise
# bypasses the `extend-exclude = ["docs"]` in pyproject.toml, which exists to
# stop ruff reformatting the Python blocks inside the frozen docs/SPEC.md.
#
# Always exits 0. This hook is a convenience, not a gate; `/ship` runs
# `ruff check .` as the real check and is where an unfixable finding surfaces.

file_path=$(python -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))' 2>/dev/null)

case "$file_path" in
    *.py) ;;
    *) exit 0 ;;
esac

[ -f "$file_path" ] || exit 0
cd "$CLAUDE_PROJECT_DIR" 2>/dev/null || exit 0

uv run ruff format --force-exclude "$file_path" >/dev/null 2>&1
uv run ruff check --fix --force-exclude "$file_path" >/dev/null 2>&1

exit 0

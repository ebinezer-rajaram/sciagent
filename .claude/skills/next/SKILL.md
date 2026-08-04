---
name: next
description: Drive one SPEC §11 backlog item end to end — resolve the cursor, write the A-test first, watch it fail, implement, verify. Invoke as /next, or /next 12 for a specific item.
---

# Next backlog item

## 1. Resolve the item

If the user named a number, use it. Otherwise:

1. `uv run python scripts/status.py`
2. If it prints `cursor: item N — title`, that is the item.
3. If it prints `cursor: every gate-tracked backlog item is satisfied`, the
   remaining items carry no A-gate and the script cannot order them. Take the
   untracked numbers it lists, then `git log --oneline --grep='backlog item'`,
   and pick the lowest-numbered item with no commit naming it.

State the item number and title before touching anything.

## 2. Read only what the item needs

- The §11 row for this item in `docs/SPEC.md`.
- The §6 definition of each gate that row names — those, and no others.
- `grep -n "item N" docs/DECISIONS.md` for prior decisions on this item.

Do not read SPEC.md end to end. It is 540 lines and the item needs one row.

## 3. Build

Tests first, for anything with an acceptance criterion:

1. **Write the A-test.** Name it for its criterion: `test_a7_...` inside class
   `TestA7MonteCarloError`. This naming is load-bearing — `scripts/status.py`
   parses it to derive gate coverage, and a test named otherwise is invisible
   to the report.
2. **Run it and watch it fail.** A test that passes before the implementation
   exists is testing nothing. If it passes, say so and fix the test, not the
   report.
3. Implement.
4. `uv run pytest` and `uv run mypy`. Paste the real output, not a summary.
   (`mypy` needs no arguments — `pyproject.toml` sets `strict` and the file
   set. Naming a path checks *less* than the configured set.)
5. If a gate is still red, **stop**. SPEC §6 is the contract; do not proceed
   past a gate.

An item with no A-gate still needs tests — it just has no gate to name them
for. Say explicitly that the item is untracked and what you tested instead.

## 4. Close out

- Anything the repository cannot tell you → `/decide`.
- Then `/ship` to review, commit and push.

Do not commit here; `/ship` owns that.

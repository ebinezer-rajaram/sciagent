---
name: next
description: Drive one SPEC §11 backlog item end to end — resolve the cursor, write the A-test first, watch it fail, review the test, implement, verify. Invoke as /next, or /next 12 for a specific item.
---

# Next backlog item

## 1. Resolve the item

If the user named a number, use it. Otherwise use what the SessionStart hook
already printed — **do not re-run `scripts/status.py` to orient**, per CLAUDE.md.
The statusline carries the cursor continuously, and the hook prints the full
report whenever the backlog or gates have moved.

1. Read the cursor from the session-start output or the statusline.
2. If it names `item N — title`, that is the item.
3. If it reads `every gate-tracked backlog item is satisfied` — which it has
   since item 14 landed — the remaining items carry no A-gate and the script
   cannot order them. Take the untracked numbers it lists, then
   `git log --oneline --grep='backlog item'`, and pick the lowest-numbered item
   with no commit naming it. This is now the normal path, not the exception.

Only re-run `scripts/status.py` if you need `--run`, which verifies gates by
execution rather than by their tests merely existing.

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
3. **Review the test before you build to it — run `/test-review`, once.** It
   owns the fan-out: give it every gate the item names and it launches one agent
   per gate itself. Do not invoke it per gate — that squares the agent count.
   Do it *now*, while the test is the only thing that exists:
   once the implementation lands, a weak test and a sound one both go green and
   nothing tells them apart, and `/preflight`'s `/code-review` sees the test only
   in that final form.

   Hand it the three inputs it requires — the SPEC §6 text for each gate (or,
   for an untracked item, its §11 row plus your own statement of what the test
   is meant to establish), the test's absolute path and class, and the pytest
   output from step 2 in full. That skill carries the brief, the agent and model
   choice, the path-not-excerpt rule, and what to do with a finding.

   This runs before step 5's suite, never beside it — the contention rule below
   is absolute.

   **If `/test-review` does not resolve** — `Unknown skill` — do not treat that
   as permission to skip it. A skill authored in a worktree is invisible to that
   worktree's own session until its branch reaches `main`
   (`docs/DECISIONS.md`, **2026-08-17** — the 2026-08-16 entry settles this for
   `.claude/agents/` and explicitly leaves skills open), and this is the
   likeliest cause. Open
   `.claude/skills/test-review/SKILL.md`, carry out its steps inline, and say
   that is what you did.
4. Implement.
5. Verify. Paste the real output, not a summary.

   ```sh
   uv run mypy                    # 1.9s, and needs no arguments: pyproject.toml
                                  # sets strict and the file set, so naming a
                                  # path checks *less* than the configured one
   uv run pytest -n 4 --dist loadfile   # ~2m30s - background it, see below
   ```

   **Background the suite.** At `-n 4 --dist loadfile` it is measured at
   **151.30s**, against 262.44s serial, and one test
   (`test_the_floor_never_exceeds_what_greedy_achieves[S1]`) is 89.74s of the
   serial figure. `--dist loadfile` is not optional: without it a module's tests
   split across workers and each re-simulates rows its siblings already built,
   which is worse than serial. Run it with `run_in_background` and read the diff
   or draft the
   `/decide` entry while it runs, rather than blocking. Two rules:

   - **Never start a subagent while it runs.** Measured 2026-08-16: four
     read-only agents took a `-n 4` run from ~163s to 183.25s and 185.37s, about
     12–15%, while alive for a fifth of it. Read-only is not free. This applies
     across worktrees too: the contention is CPU, and isolation does nothing for
     it. Delegating the run itself to `suite-runner` is the way to have the
     context back without paying this.
   - Pin the tree *before* starting it and record after, so `/preflight` need not
     repeat the run:

     ```sh
     bash .claude/hooks/suite-freshness.sh begin    # before pytest
     uv run pytest -n 4 --dist loadfile             # backgrounded
     bash .claude/hooks/suite-freshness.sh record   # refuses if the tree moved
     ```

     `record` refuses if a tracked file moved mid-run. In a worktree that is
     usually your *own* edit while the suite ran in the background — re-run on a
     settled tree rather than recording anyway.

6. If a gate is still red, **stop**. SPEC §6 is the contract; do not proceed
   past a gate.

An item with no A-gate still needs tests — it just has no gate to name them
for. Say explicitly that the item is untracked and what you tested instead.
**Step 3's `/test-review` still applies**, and on the current backlog it is the
only way this skill ever reaches it: the cursor reads *"every gate-tracked
backlog item is satisfied"*, so every remaining item is untracked. Give it the
§11 row and your own statement of what the test is meant to establish, in place
of a §6 criterion — that skill's §0 covers this case and says what makes a
self-written standard worth anything. A test with no gate to name it for is held
to the claim you made for it instead.

## 4. Close out

- **First, wait for step 5's suite to have actually finished, and for `record`
  to have run.** `/preflight` launches five review subagents and may start a
  suite of its own; either one alongside a live `-n 4` run is exactly the
  contention step 5 forbids, measured at 12–15%. While `/ship` was user-invoked
  this was handled for free by the turn boundary — you could not type it before
  the run came back. Triggering `/preflight` yourself removes that boundary, so
  the wait has to be written down.
- Anything the repository cannot tell you → `/decide`.
- Then run `/preflight` — scope, `mypy`, `ruff`, the suite and the independent
  review. Do this **without asking**: it lands nothing, and it is where defects
  are actually caught. Report what it found.
- **If `/preflight` does not resolve** — `Unknown skill` — do not treat that as
  permission to skip it. A skill authored in a worktree is invisible to that
  worktree's own session until its branch reaches `main`, exactly as
  `.claude/agents/` behaves (`docs/DECISIONS.md`, 2026-08-16). Open
  `.claude/skills/preflight/SKILL.md`, carry out its three steps inline, and say
  that is what you did. Silently losing the review is the failure this whole
  step exists to prevent, and an unresolvable skill is the likeliest way to
  arrive at it.
- Then stop, and leave `/ship` to the user.

Do not commit here, and do not invoke `/ship` yourself. `/ship` owns committing
and pushing, and the user's invocation of it is the authorisation. Your own
confidence that the work is finished is not — `docs/DECISIONS.md` (2026-08-16)
records `136550a`, whose own verification claimed to have checked the two
defects it shipped to `main`.

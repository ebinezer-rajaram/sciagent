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
3. **Review the test before you build to it.** Launch the A-test lens — one
   agent per gate the item names, in one message; for an untracked item, one for
   the test set you chose. Do it *now*, while the test is the only thing that
   exists. Once the implementation lands, a weak test and a sound one both go
   green and nothing tells them apart; `/preflight`'s `/code-review` sees the test
   only in that final form, so this is the one moment anything looks at it
   alone.

   Nothing else covers this. `/gate` guards the two adjacent failure modes — no
   test written, test skipped — and `suite-runner` is forbidden from acting on a
   test it believes is wrong. A test that passes for the wrong reason is caught
   by nobody, and a vacuous `test_aN_` still counts as a covered gate in
   `scripts/status.py`.

   Give each agent the criterion's own text, the test source, and the failure
   you just watched:

   > You are reviewing a test, not the code it will eventually test. You have
   > not seen the author's reasoning; do not ask for it.
   >
   > Criterion: `<SPEC §6 text for AN — or, for an untracked item, its §11 row
   > and what the test is claimed to establish>`
   > Test: `<absolute path>`, class `<TestAN...>`. Read the whole module as
   > needed; helpers, fixtures and sibling tests are in scope as context,
   > since they are what the test actually runs with.
   > Observed failure: `<the pytest output from step 2, in full>`
   >
   > Answer three questions, each with `file:line` evidence:
   >
   > 1. Did it fail **at the assertion**, or before reaching one? An
   >    `ImportError`, a collection error or a fixture error is a failure that
   >    proves nothing about what the test asserts. Quote the line the traceback
   >    ends on. Then check the failure came from **this** test: if the message,
   >    the line number or the data in it cannot be produced by the source
   >    above, say so — an uncorroborated red is not a watched failure.
   > 2. Does the assertion encode the criterion, or something **weaker** that
   >    the criterion merely implies? Name the gap if there is one.
   > 3. Name a wrong implementation this test **accepts and the criterion
   >    rejects**. That gap is the only thing that makes a test too weak.
   >    An implementation the criterion *also* accepts is out of scope however
   >    unsatisfying it looks — do the arithmetic, say it clears the criterion,
   >    and do not count it against the test. If no such gap exists, say so
   >    explicitly; that is the finding, and it is the common answer.
   >
   > Verdict: **SOUND** (no gap between test and criterion) or **TOO WEAK**
   > (name the gap, and show the arithmetic that puts it outside the
   > criterion). Say which question drove it.
   >
   > If you think the *criterion itself* is too weak, that is worth saying —
   > but report it separately and do not let it change the verdict. The test
   > is answerable for the criterion, not for correctness in general.
   >
   > Report findings only. Do not edit anything.

   Run it on **`evidence-checker`** with that brief inlined, not on a new named
   agent and not on `general-purpose`. It is already on `main`, so it resolves
   from any worktree — `docs/DECISIONS.md` (2026-08-16) records that an agent
   authored in a worktree cannot run there until its branch lands. It fits by
   contract: CLAUDE.md already names it for "independently verifying a claim you
   have already made", and *this test encodes A7* is exactly that claim. And its
   toolset (`Read, Glob, Grep, Bash`) is the narrowest that still does the job —
   no `Edit`, no `Write`, so it cannot casually modify what it is reading, and
   arithmetic goes through `Bash`.

   **Do not overstate that last point, because the obvious overstatement is
   false.** `Bash` writes files, so "this agent *cannot* edit" is not true of
   `evidence-checker` and is not true of `invariant-auditor` either — both are
   `Read, Glob, Grep, Bash`, and `decisions-sweeper` is the only agent here that
   is mechanically write-incapable. Every read-only guarantee this repository
   runs on, including `/preflight`'s four lenses, is **contractual**: it holds
   because the brief says report-only and the agent obeys it. That is the
   standing this lens has too. It is a real reason to prefer the narrow toolset
   over `general-purpose`'s full one, and not a reason to claim a guarantee
   nothing enforces.

   **Omit the model**, so it inherits the session's. Graded by what a false
   negative costs, per CLAUDE.md: a missed vacuous A-test is a silent wrong
   entry in the SPEC contract and no grep recovers it. Same reasoning that
   leaves `/preflight`'s lens 2 unpinned. This runs before step 5's suite, not
   beside it — the contention rule below is absolute.

   **Give it the path, never a pasted excerpt.** Measured while this step was
   written: the same A7 test judged as a 40-line extract came back TOO WEAK on
   a gap that does not exist in the module, because the guard closing it lives
   in a *sibling* criterion's test over the same `lru_cache`d fixture
   (`assert len(rows) == TRIALS`, A6). Excerpting changes the answer. The module
   is the unit.

   **Check a finding against `/recall` before acting on it.** The lens will
   rediscover settled decisions and argue with them — it has no access to
   `docs/DECISIONS.md` and no way to tell a defect from a choice. Measured on
   the same probe: it reported A6 and A7 measuring one statistic as an
   invariant-5 defect, having read and then argued against the very module
   comment that encodes the 2026-08-03 decision resolving it ("A6 fixes the
   *standard*, A7 fixes the *number*"). A real finding survives that check;
   most of this class will not.

   Fix what it finds *in the test*, then re-run step 2 and watch the corrected
   test fail again. A finding you disagree with needs a stated reason, not
   silence.
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
**The step-3 lens still applies**, and on the current backlog it is the only way
it ever fires: the cursor reads *"every gate-tracked backlog item is
satisfied"*, so every remaining item is untracked. Give the lens the §11 row and
your own statement of what the test is meant to establish, in place of a §6
criterion. A test with no gate to name it for is held to the claim you made for
it instead.

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

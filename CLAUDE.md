# sciagent

Research framework for evaluating constrained agentic scientific investigation
under model misspecification. Full design: `docs/SPEC.md`. Read it before
non-trivial work. It is frozen; do not propose architectural changes.

## What this is

Environments are compositional executable programmes. Defects are typed
structural edits to those programmes. A research system investigates which
edit was applied. We evaluate LLM-based systems against conventional
baselines on scenarios with known ground truth.

## Non-negotiable invariants

Violating any of these is a bug regardless of tests passing.

1. **`sciagent/` never imports from `environments/`.** The framework is
   domain-independent. There is a test for this. Do not weaken it.
2. **The framework writes numbers; agents write structure.** No code path
   reachable from an agent may set `plausibility`, a posterior value, a
   metric definition, or a score. Enforce with runtime assertions, not
   comments.
3. **Bit-exact determinism.** Same seed + same config + same version =>
   byte-identical output. All randomness through explicitly passed seeded
   generators. Never `random.` or bare `np.random.`. No dict/set iteration
   order dependence in anything affecting output.
4. **The registry is append-only.** No update or delete path exists. Content-
   addressed by hash over (env version, config, data version, metric version,
   seed).
5. **Acceptance tests are the contract.** A1-A24 in `docs/SPEC.md` §6. A
   subsystem is not done until its tests pass. Do not proceed past a gate.
6. **No LLM code until backlog item 12.** ~~Live~~ **Discharged at a380a21.**
   Items 2-11 are pure conventional machinery, and they were built before the
   agent existed, which is what this invariant was for: agent performance can
   never be confounded with framework immaturity. It is kept here rather than
   deleted because the *reason* still governs — anything that would move
   evaluation apparatus after the agent that is scored by it reopens the
   confound. `systems/llm/`, `systems/hybrid.py` and `systems/ablation.py` are
   in bounds; a new gate written after the system it grades is not.

<!--
DO NOT COMPRESS INVARIANT 6. It is left uncompressed on purpose, and it is the
one part of this file a size reduction must not touch.

docs/DECISIONS.md, 2026-08-18, "the CLAUDE.md trim was attempted, reviewed twice,
and abandoned" records two attempts at exactly that and why both were wrong. The
invariant carries TWO prohibitions, and dropping either one breaks it:

  broad  -- "anything that would move evaluation apparatus after the agent that
            is scored by it reopens the confound"
  narrow -- "a new gate written after the system it grades is not [in bounds]"

Keeping only the broad clause makes the headline an absolute that `c4dcee3` --
the item 15 matrix, which grades V7 and was written thirteen days after it --
already violates, so a session reading it literally would refuse the remaining
LLM cells. That rejected wording was "Evaluation apparatus is never written
after the system it grades."

Keeping only the narrow clause permits the SPEC §12 criterion 4 rewrite this
repository withdrew on 2026-08-16 for violating invariant 6 -- an exit criterion
is not an acceptance test, so "gate" alone does not reach it. That rejected
wording was "A gate is never written after the system it grades."
-->

## Stack

- Python 3.12+, `uv` for dependencies (`uv add`, `uv run`)
- `pytest` + `hypothesis` for property-based testing
- `numpy`, `scipy`; `numba` only if profiling justifies it. `anthropic` reaches
  the network from one module and never during a replay
- Strict typing: `mypy` clean — it takes no arguments, since `pyproject.toml`
  sets `strict` and the file set. `from __future__ import annotations`.
  **`mypy` is the gate; `pyright` is advisory.** Never edit code to satisfy a
  pyright-only complaint.
- `@dataclass(frozen=True)` for all value types. No mutable global state

<!--
The pyright LSP plugin supplies inline diagnostics and agreed with `mypy`
exactly when both were run over `registry/` and `verify/` -- 15 files, zero
findings each. If they ever disagree, `mypy` decides.
-->

## Conventions

- Small modules, one concept each. Prefer explicit over clever.
- Type aliases (`ComponentId`, `Seed`, `Probability`) via `NewType`, not bare `str`/`float`.
- Every public function gets a docstring stating what it guarantees, not what it does.
- Errors are typed exceptions from `sciagent.core.errors`, never bare `ValueError`.
- No I/O in `core/`. Pure functions only.
- **Acceptance tests are named for their criterion**: `test_a7_...` in a class
  `TestA7MonteCarloError`. This naming is load-bearing — `scripts/status.py`
  derives gate coverage from it. A test not named this way is invisible to the
  status report.

## Working defaults

<!--
General habits, not project facts. They live in the repository rather than in a
personal config file because cloud sessions clone the repo and see nothing from
`~/.claude/`.
-->

- Show evidence, not assertions: the command you ran and its output.
- Fix root causes. Never suppress an error to make a check pass: no bare
  `except`, no `# type: ignore`, no `# noqa`, no skipped tests.
- **If a task will touch more than three files, plan first and get the plan
  approved before editing anything.** Gate on the *estimate*, made before you
  start — not on the moment the sprawl becomes obvious, which is a gate that
  fires after licensing exactly the edits it existed to stop. This binds `/next`
  too: a backlog row authorises the *work*, not the edits, and the A-test is an
  edit like any other.
- If you are more than 50% unsure what was meant, ask one specific question
  rather than guessing and building the wrong thing.
- Prefer editing an existing file over creating a new one.
- Never commit unless asked, and never push without asking. Invoking `/ship`
  is that asking.
- Platform depends on the surface, so check rather than assume — `uname -s`
  settles it. Local sessions run on Windows through Git Bash: prefer forward
  slashes and do not assume GNU coreutils flags exist. Cloud sessions run
  Ubuntu 24.04, where neither caution applies.
- **Do not prefix shell commands with `cd <project dir> &&`.** The working
  directory is already set.
- **Run the suite as `uv run pytest -n 4 --dist loadfile`, backgrounded rather
  than blocking.** `--dist loadfile` is what makes the run correct, not a tuning
  knob: tests in one module share simulated rows through the gate table, so
  splitting a module across workers re-simulates at 2000 replicates what a
  sibling already did. Memory binds before cores, so do not "improve" it to
  `-n auto`, and it does not belong in `addopts` either. Read the tail of the
  output, never the passed count, and quote the invocation beside any timing.
- Before repeating the suite, ask `bash .claude/hooks/suite-freshness.sh check`.
  Pin with `begin` before pytest and `record` after — `record` refuses if a
  tracked file moved mid-run, since such a run describes no single tree.
- **Do not start a subagent alongside the suite — except review that never
  reads the suite's result.** The tax is real (**4-18%** for six agents,
  scaling with how much of the run they are alive for) and
  "read-only, so no CPU work" is false as a premise. But it lands inside a
  review fan-out that outlasts the suite, while serialising adds the suite's
  *whole* duration to the critical path. `/preflight`'s lenses read a diff, so
  they may overlap; anything that reads a test result waits. `mypy` is fast and
  needs no such care.

<!--
Suite timings, the `-n 4 --dist loadfile` adoption, the `-n auto` MemoryError,
the per-test evidence for `loadfile`, and the 12-15% cost of four read-only
agents alongside a `-n 4` run are all in docs/DECISIONS.md, 2026-08-16,
"pytest-xdist adopted at -n 4, and what the contention rule actually is". Every
figure in DECISIONS.md before that date is serial and not comparable.

The carve-out above is measured on 2026-08-20: 200.65s idle against 236.59s
under six `/preflight`-shaped agents, 1505 passed / 7 skipped in both, so the
tax is time and not correctness. The mechanism the 2026-08-16 entry did not
name is that the agents are bound on *inference latency*, not local CPU — under
full suite load `/code-review` ran 350.0s against 360.6s idle. That is why the
suite pays 17.9% and the agents pay nothing, and why the fan-out, not the suite,
is the critical path. Do not quote a net saving: agent depth varies run to run
by up to 2.6x, which swamps the effect.

That entry also disowns the 30m37s contention figure this file used to carry: it
was a 4 vCPU cloud-VM measurement against a 7m50s baseline on that same VM,
spliced onto the desktop's idle figure from six days later. Do not requote it.

The shared table cache costs 3m11s cold against 1.055s warm, and both halves of
the read/write race are guarded. docs/DECISIONS.md, 2026-08-16.
-->

- You decide when to delegate to subagents; do not ask each time. Delegate for
  coverage — wide sweeps, locating call sites, enumerating across many files,
  independently verifying a claim you have already made. Do it yourself for
  comprehension. The test is whether the result could be checked with a grep; if
  checking it means redoing the work, do not delegate. The one exception is work
  that does not fit one context at all: reading 485KB of `DECISIONS.md` is not
  grep-checkable either, but the alternative there is not doing it rather than
  doing it yourself, so the test does not apply — which is what licenses
  `/recall`'s four-way `decisions-sweeper` fan-out over that same file. Say what
  you delegated and what came back. For the verification case use the
  `evidence-checker` subagent: it has not seen your reasoning, which is the
  whole point.
- **Launch in parallel only where the work is genuinely independent.** A list is
  not fan-out: three items touching one file is a sequence wearing a list's
  clothing. Three places clear the bar — `/preflight` step 2's four invariant
  lenses plus `/code-review`, `/recall`'s four `decisions-sweeper` slices, and
  triaging a run with more than about three unrelated failures. The contention
  is **CPU**, and concurrent read-only agents are affordable, which is not the
  same as free. What it forbids is an agent that must *read the suite's result*
  running beside it; review that reads only a diff is the carve-out above.
- **Read-only review is authorised by the work; irreversible action only by the
  user.** That licenses `/preflight`'s subagents without asking, including when
  you invoked `/preflight` yourself: `/code-review`, plus `invariant-auditor`'s
  four lenses when the diff touches `src/sciagent/` or an agent-reachable path
  — *before* committing.
- **`/preflight` may run on your own initiative; `/ship` never may.** `/preflight`
  scopes, verifies and reviews, and lands nothing. `/ship` commits, merges and
  pushes, and the user's invocation of it *is* the authorisation — never supply
  that yourself on the strength of your own confidence that the work is finished.
- **Grade a subagent's model by what a false negative costs, not by what the
  task looks like.** An agent that misses something reports "nothing found",
  indistinguishable from a clean sweep, so cheapness is affordable only where
  you could catch the miss yourself. `haiku` where a grep would recover the
  answer; `sonnet` for bounded judgement or extraction from prose; otherwise
  omit the model and inherit the session — **unless the session is itself
  running below what the step needs**, in which case pin the stronger model
  explicitly. Inheriting is the default because it usually lands on the strong
  model, not because pinning is forbidden; in a Fable or Sonnet session that
  reasoning inverts and omitting sends the expensive lenses to the weakest
  model in this table, which is the false negative the rule exists to prevent.

<!--
The `src/sciagent/` path above is spelled from the repository root deliberately:
there is no top-level `sciagent/`, and a condition naming a directory that does
not exist never fires. Shipping 136550a without those reviews cost two defects
that reached `main` -- a hook fix verified against the wrong invocation form, and
a cache override in an untracked file no worktree could read. Both were found by
the review minutes after the push, and either would have been caught before it.
136550a's own verification claimed to have checked both defects it shipped, which
is why /ship is never self-authorised.

The rule against the `cd <project dir> &&` prefix came from a transcript
measurement: 1,141 of 1,402 commands across 50 transcripts carried it, and none
of them needed it.
-->

When compacting, preserve the list of modified files, the commands used to
verify the work, and any decisions the user pushed back on.

## Concurrent local sessions

**Work in a git worktree, one per session.** Use `EnterWorktree` at the start of
a session that will edit anything. It creates the tree under
`.claude/worktrees/`, which is gitignored — so the parent `git status` stays
clean and VS Code's search skips it.

- **`/ship` cannot finish from inside the worktree and will stop to ask.** Every
  route to `main` needs `git -C` against the shared checkout, which the harness
  refuses from a worktree-isolated session, and `ExitWorktree` is not callable by
  the agent. Approve leaving (`keep`); the merge and push then run in the main tree.
- **A merge voids the green.** The suite verified *your* tree; merging `main` in
  changes it, so the result no longer describes what lands on `main`. Merge
  first, then verify — never the reverse. The merge moves the tree hash, so
  `suite-freshness.sh check` reports STALE by itself.
- **Never resolve another session's conflict.** Stop and report it. The tree may
  be mid-refactor in a session you cannot see, and that call is the user's.
- **A skill or agent written in a worktree is invisible to that worktree's own
  session.** Both are read from the shared checkout; the remedy is the merge,
  not a restart. If `/test-review` does not resolve, carry out
  `.claude/skills/test-review/SKILL.md` inline and say that is what you did.
- Worktrees do **not** buy parallel testing — the contention is CPU and applies
  across worktrees exactly as within one.
- All trees share one table cache, resolved from `git rev-parse --git-common-dir`,
  so every worktree finds the main tree's `.cache/tables` with nothing to
  configure (`SCIAGENT_TABLE_CACHE` still overrides). Anything new that reads a
  cached artefact should go through the same door — `EmpiricalTable.save` retries
  its atomic replace and `_read_text_contended` retries the read, and both halves
  of that race are guarded on purpose.

<!--
Why one worktree per session, with the incidents behind it:

- Sharing one working tree let sessions edit each other's files. A suite that ran
  18:00-18:07 on 2026-08-15 was certified green against a tree a second session
  had changed at 18:05:56. docs/DECISIONS.md, 2026-08-15.
- It also blocked real work: a one-line fix left unmade "because another session
  was holding the file", and pytest-xdist left uninstalled because `uv add`
  writes two files another session was committing to.
- A cold worktree costs 3m11s acquiring the slice table against 1.055s warm, so
  sharing the cache matters more than the contention it avoids. An earlier
  version set this through `SCIAGENT_TABLE_CACHE` in the untracked
  `.claude/settings.local.json`, which no worktree checkout could contain, so
  every worktree silently took the cold path. `.worktreeinclude` now copies that
  file into new worktrees as well.
- The reader half of the cache race was added 2026-08-16 after a `-n 4` run died
  with PermissionError on the gate table. Before it, a probe at four writers and
  six readers lost 4 reads in 900; after it, none in 900, and none in 1200 at six
  and eight readers.
- "A skill or agent written in a worktree is invisible to it" is settled, not
  suspected: docs/DECISIONS.md, 2026-08-16, "a worktree reads its agents from the
  shared checkout". The registry does refresh mid-session, so a restart is not
  the remedy and never was.
-->

## Working style

- **Ask before assuming.** If the spec is ambiguous, ask rather than picking.
  Ambiguity in the spec is a real finding worth surfacing.
- **Tests first for anything with an acceptance criterion.** Write the A-test,
  watch it fail, run `/test-review` on it, then implement.
- **`/next` is not the only way to write code here.** A direct request —
  "implement X", or something we discussed and planned — is a first-class path.
  It drops only `/next`'s **§1 and §2**, which resolve the cursor and read the
  §11 row and the §6 gates; an ad-hoc request has no backlog row to resolve.
  **§3 is kept entire** — write the test, watch it fail, `/test-review` it,
  implement, verify — then §4 as written: `/decide`, then `/preflight`.
  The test review is the one step that follows from nothing else on this page,
  which is why it is named here as well as in both skills; skipping it because
  the work arrived as a sentence rather than a backlog row is how ad-hoc work
  silently becomes the untested kind. What replaces the §6 criterion is your own
  written statement of what the test establishes — written before the review,
  and not narrowed to fit it.
- Run `uv run pytest -n 4 --dist loadfile` and `uv run mypy` before saying done.
  `mypy` takes no arguments: naming a path checks *less* than the configured set,
  since `pyproject.toml` sets `strict` and the file list.
- If something in the spec seems wrong, say so. Do not silently work around it.
- **Append to `docs/DECISIONS.md` the moment a decision is made**, not at the
  end of a session — sessions end by context exhaustion or a closed laptop.
  Use `/decide`, which carries the format and the four-category filter.

## Skills

`/next`, `/test-review`, `/decide`, `/gate A9`, `/preflight`, `/ship`,
`/recall <topic>`, `/handoff`, `/matrix`, `/platform-check`. Each carries its own
description; what those do not say:

- **`/next`** resolves a cursor across two backlogs in order — SPEC §11, then
  `docs/BACKLOG.md`'s gated entries by `**Rank.**` once §11 is satisfied, which
  is where it sits now.
- **`/test-review`** is the only moment anything looks at a test alone. `/next`
  calls it at step 3; on the ad-hoc path you call it yourself.
- **`/preflight`** lands nothing, so it needs no permission. **`/ship`**,
  **`/matrix`** and **`/platform-check`** carry `disable-model-invocation`: the
  user invokes them, you never do. `/platform-check` is dormant — the instrument
  for lifting the Windows pin, kept against the day that matters.

The SessionStart hook prints `scripts/status.py` in full only when the backlog
or the gates have moved, three lines otherwise, and after a compaction the state
the summary dropped. Branch, dirty count, cursor and gate coverage are on the
statusline at no cost — do not re-run the script to orient. Use `--run` when you
need gates verified by execution rather than by their tests merely existing.

**Do not read `docs/DECISIONS.md` end to end** — ~485KB across ~169 entries, and
well over 100k tokens; it grows every session, so treat any figure here as a
floor. Use
`/recall`, which greps headers and reads only what matches.

## Cloud sessions

`CLAUDE_CODE_REMOTE` is `"true"` in a cloud session and unset locally; that is
the signal to branch on, not a guess from the platform. **In a cloud session,
read `docs/CLOUD.md` first** — `.claude/hooks/session-start.sh` says so too. The
two rules that bite hardest: work happens on the session's own branch, never
`main`; and cloud sessions edit code but never produce numbers, because Windows
is this project's one reference platform.

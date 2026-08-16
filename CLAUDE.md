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

## Stack

- Python 3.12+, `uv` for dependencies (`uv add`, `uv run`)
- `pytest` + `hypothesis` for property-based testing
- `numpy`, `scipy`; `numba` only if profiling justifies it. `anthropic` reaches
  the network from one module and never during a replay
- Strict typing: `mypy` clean — it takes no arguments, since `pyproject.toml`
  sets `strict` and the file set. `from __future__ import annotations`.
  **`mypy` is the gate; `pyright` is advisory.** The pyright LSP plugin supplies
  inline diagnostics and agreed with `mypy` exactly when both were run over
  `registry/` and `verify/` — 15 files, zero findings each. If they ever
  disagree, `mypy` decides; never edit code to satisfy a pyright-only complaint.
- `@dataclass(frozen=True)` for all value types. No mutable global state

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

General habits, not project facts. They live in the repository rather than in a
personal config file because cloud sessions clone the repo and see nothing from
`~/.claude/` — see **Cloud sessions** below.

- Show evidence, not assertions. When you say something works, include the
  command you ran and its output.
- Fix root causes. Never suppress an error to make a check pass: no bare
  `except`, no `# type: ignore`, no `# noqa`, no skipped tests.
- If a task will touch more than three files, plan first and get the plan
  approved before editing anything.
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
  directory is already set. Measured across 50 transcripts, 1,141 of 1,402
  commands carried this prefix and none needed it.
- **The suite is ~7 minutes; background it rather than blocking.** One test,
  `test_the_floor_never_exceeds_what_greedy_achieves[S1]`, is 147s of that, and
  the slowest 25 are about 6 minutes of it. Never run a subagent alongside it —
  contention alone took one run from 6m30s to 30m37s. `mypy` is 1.9s and needs
  no such care. Before repeating the suite, ask
  `bash .claude/hooks/suite-freshness.sh check`: it reports whether a full run
  already passed on a byte-identical tree, and `/next` and `/ship` between them
  used to run it three times per item. Pin with `begin` before starting pytest
  and `record` after — `record` refuses if a tracked file moved mid-run, because
  such a run describes no single state of the tree. Worktrees make that rare
  rather than routine, but it still catches your own edits during a backgrounded
  run, which is exactly what backgrounding invites.
- You decide when to delegate to subagents; do not ask each time. Delegate for
  coverage: wide sweeps, locating call sites, enumerating across many files,
  and independently verifying a claim you have already made. Do it yourself for
  comprehension: how a module works, whether an invariant holds. The test is
  whether the result could be checked with a grep — if checking it means
  redoing the work, do not delegate. Say what you delegated and what came back.
- For the verification case specifically, use the `evidence-checker` subagent.
  It has not seen your reasoning, which is the whole point: you are the worst
  judge of a claim you just made.
- **Invoking `/ship` is the authorisation for its step 2 subagents.** Run
  `/code-review` and, when the diff touches `sciagent/` or an agent-reachable
  path, `invariant-auditor` — without asking, and *before* committing. Shipping
  136550a without them cost two defects that reached `main`: a hook fix verified
  against the wrong invocation form, and a cache override in an untracked file
  no worktree could read. Both were found by the review minutes after the push,
  and either would have been caught before it.
- Choose a subagent model only when the fit is obvious: haiku or sonnet for
  mechanical enumeration and pattern matching. Otherwise omit the model and
  inherit the session. Never pin opus explicitly.

When compacting, preserve the list of modified files, the commands used to
verify the work, and any decisions the user pushed back on.

## Concurrent local sessions

**Work in a git worktree, one per session.** Several local sessions run against
this repository at once. Sharing one working tree meant they edited each other's
files, and the cost was not theoretical: a suite that ran 18:00–18:07 on
2026-08-15 was certified green against a tree a second session had changed at
18:05:56. It also blocked real work — `DECISIONS.md` records a one-line fix left
unmade "because another session was holding the file", and `pytest-xdist` (a
measured threefold win) left uninstalled because `uv add` writes two files
another session was committing to.

Use `EnterWorktree` at the start of a session that will edit anything. It
creates the tree under `.claude/worktrees/`, which is gitignored — so the parent
`git status` stays clean, VS Code's search skips it, and the explorer can still
browse it. `/ship` merges the branch back to `main`.

Two things follow that are easy to get wrong:

- **A merge voids the green.** The suite verified *your* tree; merging `main` in
  changes it, so the result no longer describes what lands on `main`. Merge
  first, then verify — never the reverse. Nothing extra is needed to notice: the
  merge moves the tree hash, so `suite-freshness.sh check` reports STALE by
  itself.
- **Never resolve another session's conflict.** Stop and report it. The tree may
  be mid-refactor in a session you cannot see, and that call is the user's.

Worktrees do **not** buy parallel testing: the 30m37s contention figure is CPU,
and it applies across worktrees exactly as within one.

All trees share one table cache, resolved from `git rev-parse --git-common-dir`
so that every worktree finds the main tree's `.cache/tables` with nothing to
configure. Acquiring the slice table is **3m11s** cold against **1.055s** warm,
so a cold worktree costs more than the contention it avoids. An earlier version
set this through an environment variable in `.claude/settings.local.json`; that
file is untracked, so no worktree checkout could contain it and every worktree
silently took the cold path. `SCIAGENT_TABLE_CACHE` still overrides if set.

## Working style

- **Ask before assuming.** If the spec is ambiguous, ask rather than picking.
  Ambiguity in the spec is a real finding worth surfacing.
- **Tests first for anything with an acceptance criterion.** Write the A-test,
  watch it fail, then implement.
- Run `uv run pytest` and `uv run mypy` before saying done. `mypy` takes no
  arguments: `pyproject.toml` sets `strict` and the file set, so naming a path
  checks less than the configured one.
- If something in the spec seems wrong, say so. Do not silently work around it.
- **Append to `docs/DECISIONS.md` the moment a decision is made**, not at the
  end of a session — sessions end by context exhaustion or a closed laptop.
  Use `/decide`, which carries the format and the four-category filter.

## Skills

- `/next` (or `/next 12`) — drive one SPEC §11 backlog item: resolve the
  cursor, A-test first, watch it fail, implement, verify.
- `/decide` — append to `docs/DECISIONS.md`.
- `/gate A9` — run one acceptance criterion and report the real outcome.
- `/ship` — verify, review independently, commit and push.
- `/recall <topic>` — find what was already decided, without reading 190KB.
- `/handoff` — write a note so a session ending badly does not strand its work.
  Prefer `claude --resume`; this is the fallback when resuming is impossible.
- `/platform-check` — localise the measured Windows/Ubuntu divergence.
- `/matrix` — drive item 15, the first experiment matrix.

The SessionStart hook prints `scripts/status.py`, but only in full when the
backlog or the gates have actually moved; otherwise it prints three lines, and
the statusline carries branch, dirty count, cursor and gate coverage
continuously at no cost in context. Do not re-run the script to orient. Do run
`--run` when you need gates verified by execution rather than by their tests
merely existing.

**Do not read `docs/DECISIONS.md` end to end.** It is ~190KB across ~90 entries
and grows every session; reading it whole costs about 50k tokens. Use `/recall`,
which greps headers and reads only what matches. The file still holds what the
repository cannot tell you — that has not changed, only how to get at it.

## Cloud sessions

This repository is set up to be worked on from Claude Code on the web, so that
it can be driven from a phone with no local machine running. `CLAUDE_CODE_REMOTE`
is `"true"` in a cloud session and unset locally; that is the signal to branch
on, not a guess from the platform.

Python and `uv` are on the cloud image, and the default **Trusted** network
level reaches PyPI. The project's own pinned `pytest`, `mypy` and `ruff` are
`[dependency-groups] dev` entries resolved from `uv.lock`, so the first `uv run`
installs them; nothing needs configuring beyond that.

Three differences from a local session actually change behaviour:

- **Work happens on the session's own branch, never `main`.** The GitHub proxy
  accepts a push only for the branch the session is already on, so a commit
  made on `main` cannot be pushed. `/ship` handles this; do not work around it.
- **The VM is 4 vCPU / 16 GB / 30 GB.** The suite runs in about 8 minutes here
  with the machine to itself, close to the 6m30s–7m45s a developer desktop takes,
  and a cold first session spends roughly half a minute installing dependencies
  before the status hook prints. Four vCPUs is the thing to plan around: a suite
  run measured at **30m37s** while a subagent was working in the same container,
  a fourfold slowdown from contention alone. Do not start a long run and a
  subagent together and then read the timing as the suite's. Do not use the test
  count as a health check either — it moves with every backlog item — read the
  tail of the pytest output instead.
- **Cross-platform determinism is unverified.** Invariant 3 demands byte-identical
  output, and the registry content-addresses over (env version, config, data
  version, metric version, seed) with no platform term. Local runs are Windows;
  cloud runs are Ubuntu on x86-64. If BLAS resolution differs, two registry
  entries could share an address while holding different numbers.

  Do not try to settle this with A1 and A15 as a whole: both are self-referential
  within one process — they rebuild the expected value in-process and compare
  against it, so they pass on any platform. What yields comparable evidence is
  `tests/acceptance/determinism_child.py`, which writes `name sha256` lines to
  stdout (see `test_a1_byte_identical_across_processes`). Run it on both
  platforms and diff the output **before** trusting any cloud-produced registry
  entry.
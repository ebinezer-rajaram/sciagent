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
6. **No LLM code until backlog item 12.** Items 2-11 are pure conventional
   machinery. This is deliberate: the evaluation apparatus must be validated
   before the agent exists.

## Stack

- Python 3.12+, `uv` for dependencies (`uv add`, `uv run`)
- `pytest` + `hypothesis` for property-based testing
- `numpy`, `scipy`; `numba` only if profiling justifies it
- Strict typing: `mypy --strict` clean. `from __future__ import annotations`
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
- You decide when to delegate to subagents; do not ask each time. Delegate for
  coverage: wide sweeps, locating call sites, enumerating across many files,
  and independently verifying a claim you have already made. Do it yourself for
  comprehension: how a module works, whether an invariant holds. The test is
  whether the result could be checked with a grep — if checking it means
  redoing the work, do not delegate. Say what you delegated and what came back.
- For the verification case specifically, use the `evidence-checker` subagent.
  It has not seen your reasoning, which is the whole point: you are the worst
  judge of a claim you just made.
- Choose a subagent model only when the fit is obvious: haiku or sonnet for
  mechanical enumeration and pattern matching. Otherwise omit the model and
  inherit the session. Never pin opus explicitly.

When compacting, preserve the list of modified files, the commands used to
verify the work, and any decisions the user pushed back on.

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

The SessionStart hook already prints `scripts/status.py`, so the §11 cursor and
gate coverage are in context at the top of every session. Do not re-run it to
orient; do run `--run` when you need gates verified by execution rather than by
their tests merely existing. Read `docs/DECISIONS.md` for what the repository
cannot tell you.

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
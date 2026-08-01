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

## Working style

- **Ask before assuming.** If the spec is ambiguous, ask rather than picking.
  Ambiguity in the spec is a real finding worth surfacing.
- **Tests first for anything with an acceptance criterion.** Write the A-test,
  watch it fail, then implement.
- Run `uv run pytest` and `uv run mypy --strict sciagent` before saying done.
- Commit at each working state with a message naming the backlog item.
- If something in the spec seems wrong, say so. Do not silently work around it.
- **Append to `docs/DECISIONS.md` the moment a decision is made**, not at the
  end of a session. Sessions end by context exhaustion or a closed laptop, and
  neither offers a chance to write things down. Four things go in it: a spec
  ambiguity and how it was resolved, an approach tried and abandoned, a measured
  number that is expensive to reproduce, and work left deliberately incomplete.
  Nothing else — if `scripts/status.py`, the diff or `git log` already shows it,
  it does not belong there. Append only; never edit an existing entry. New ideas
  that would touch a frozen decision go to `docs/BACKLOG.md` per SPEC §13.

## Current position

Run `uv run python scripts/status.py` — it derives the §11 cursor and A-gate
coverage from the repository. Add `--run` to verify gates by execution rather
than by their tests merely existing. Do not maintain a hand-written status here;
it would drift. Read `docs/DECISIONS.md` for what the repository cannot tell you.
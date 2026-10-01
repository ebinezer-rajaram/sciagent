# sciagent

A benchmark for LLM research agents doing open-world mechanism discovery on
marked point processes. Design: `docs/SPEC.md` (v2). Read the section you are
working on before non-trivial work. v1 is frozen at the git tag `v1.0`, and its
record is in `docs/v1/` (start with `docs/v1/RESULTS.md`).

## Invariants

Violating one of these is a bug even when the tests pass.

1. **`sciagent/` never imports from `environments/`.** The framework is
   domain-independent; `tests/test_invariants.py` enforces it.
2. **The framework writes numbers; agents write structure.** No path an agent
   can reach may set a score, a fitted parameter of the submitted model, a
   posterior, or held-out data. Agents may compute anything for their own
   reasoning. Enforce with code, not comments.
3. **Determinism.** Same seed + config + version gives byte-identical output.
   All randomness goes through explicitly passed seeded generators: never
   `random.` or bare `np.random.`. No dict/set iteration-order dependence in
   output. The PostToolUse hook runs the AST check after every relevant edit.
4. **The results store is append-only and content-addressed.**

## Stack and commands

Python 3.12+, `uv` (never `pip`), numpy/scipy, pytest + hypothesis, `mypy`
strict (the gate; pyright is advisory), `ruff`. `from __future__ import
annotations`; `@dataclass(frozen=True)` for value types; typed errors from
`sciagent.core.errors`.

```sh
uv run pytest -m "not slow"   # fast tier, the default while iterating
uv run pytest -m slow         # simulation-heavy; before a merge that touches src/
uv run mypy                   # no arguments; pyproject sets strict and the files
uv run ruff format . && uv run ruff check --fix .
```

Mark a test `@pytest.mark.slow` if it takes more than ~2 s. Run a single test
file while iterating; read the tail of pytest's output, not the passed count.

## How to work

- **Instruments get tests first.** Anything that produces a number the
  evaluation reads is an instrument (likelihood, fitting, distance, truth
  sampler, scoring, tool budgets, the sandbox boundary, replay). The full list
  is SPEC §6.3. Write its test, watch it fail, then implement. Ordinary code
  gets ordinary tests.
- **Record real design decisions** in `docs/v2/LOG.md` as one short dated
  paragraph: what was decided, and why. Skip routine choices.
- **Plan first if a change touches more than ~5 files** or changes the
  SPEC. Get the plan approved.
- **Ask one specific question** rather than guess when you are more than 50%
  unsure.
- **Positive controls.** Never report a comparison between systems unless a
  control has shown the instrument can separate them (SPEC §6.4). This is v1's
  main lesson.
- Show evidence (the command and its output), fix root causes, and never
  suppress an error: no bare `except`, no `# type: ignore`, no `# noqa`, no
  skipped tests.
- Never commit unless asked, and never push without asking. `/ship` is the
  ask for a commit only.

## Platform

Local sessions run Windows through Git Bash: use forward slashes, and don't
assume GNU coreutils flags. Don't prefix commands with `cd <project dir> &&`.
Windows is the one reference platform for numbers. Cloud sessions
(`CLAUDE_CODE_REMOTE=true`) work on their own branch, never `main`, and edit
code but never produce reported numbers.

---
name: gate
description: Run one acceptance criterion's tests by execution and report the real outcome. Invoke as /gate A9.
allowed-tools: Bash, Read, Grep
---

# Run one acceptance gate

Given a criterion `AN`:

```sh
uv run pytest -k "test_a<N>_" -v
```

**One spelling, not two.** `scripts/status.py` matches `^test_a0*(\d+)_`, so a
zero-padded `test_a09_` would be legal and counted — but do **not** add it to
`-k` as a second term. `-k` matches the whole test id, module name included, and
the acceptance tests live in files like `test_a06_a11.py`. Measured:

| selector | collects |
|---|---|
| `-k "test_a6_"` | 3 — correct |
| `-k "test_a6_ or test_a06_"` | 26 — all of A6–A11, plus one from `test_invariants.py` |

So the padded term does not widen the net harmlessly; it swallows every
criterion sharing a module and reports their tests as this gate's. A6 would then
pass or fail on A9's behaviour.

No padded test exists today, so the single term is complete. If one is ever
written, this selector needs re-deriving — matching on the module name is the
trap, and `--collect-only` is how you check you have only what you asked for.

If that selects nothing, the gate has no tests written. Say exactly that —
"no tests written for AN" — and do not report it as passing. Absent evidence
is not green.

## Report

- The criterion's text from `docs/SPEC.md` §6.
- Real pytest output. Paste it.
- Pass/fail per test, and for a failure the assertion, not a paraphrase.

A `SKIPPED` acceptance test is **not** evidence the criterion holds. Report it
as unproven.

## Whole-repo view

For every gate at once, use `uv run python scripts/status.py --run` rather than
looping this skill. Without `--run` the report says only which gates have tests
*written* — it never claims a gate passes.

---
name: gate
description: Run one acceptance criterion's tests by execution and report the real outcome. Invoke as /gate A9.
allowed-tools: Bash, Read, Grep
---

# Run one acceptance gate

Given a criterion `AN`:

```sh
uv run pytest -k "test_a<N>_ or test_a0<N>_" -v
```

Both spellings are needed: `scripts/status.py` matches `^test_a0*(\d+)_`, so
tests exist as `test_a9_` and `test_a09_`.

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

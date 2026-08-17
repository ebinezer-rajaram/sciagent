---
name: matrix
description: Drive SPEC §11 item 15, the first experiment matrix — 56 cells over twelve slice scenarios at twenty seeds each. Invoke as /matrix to plan, run, or resume it. Covers running it all on the one reference platform, the preregistered contrast, and the rule against collapsing D1–D6.
allowed-tools: Bash, Read, Grep, Write, Edit
---

# First experiment matrix — SPEC §11 item 15

## Run every cell on the one reference platform

**Windows — the desktop.** Not a precaution to weigh up; a settled constraint,
and the only thing about platforms this skill asks of you.

The temptation it forecloses is worth naming, because it looks like good sense.
This matrix is days of compute over ~1,120 investigations, so splitting it across
the desktop and a few cloud sessions is the obvious way to finish it sooner. It
is the wrong one. `docs/DECISIONS.md` 2026-08-15 measured a Windows/Ubuntu
divergence in a reported number, and the registry content-addresses over (env
version, config, data version, metric version, seed) **with no platform term** —
so a matrix built partly on each would be internally incomparable and nothing in
the registry would say so. Two cells could share a content address while holding
different numbers.

Running it all in one place makes that impossible rather than unlikely, which is
why the pin is the remedy the project took. `.cache/tables/` inherits the same
rule for the same reason: it is keyed on replicates, seed and design set, not on
the machine that filled it, so use the desktop's cache and do not import one
built elsewhere.

Record the platform in the entry that reports the results — `summarise()`
requires it, so this is a field to fill rather than a discipline to remember.

## The matrix — §9, exactly

| systems | scenarios | cells |
|---|---|---|
| V1 BOED, V7 Hybrid, B4 Retrieval, B5 Beam search | all twelve S1–S12 | 48 |
| B1 PPC-only, Stage A only | S9, S11 | 2 |
| V3 raw history, V4 graph — targeted ablation | S8, S11, S12 | 6 |

**56 cells, twenty seeds each, ~1,120 investigations.** Do not add an arm, a
scenario or a seed count. §9 says "small and interpretable"; the frozen campaign
is where breadth goes, and a new arm belongs in `docs/BACKLOG.md` per §13.

## The preregistered contrast

> On S11 Stage B, conditional on inadequacy detection, does V7 exceed **B4** on
> D3 (intervention-response similarity)?

B4 is the comparator **by prior designation**, because it is the baseline most
likely to deflate the claim. Do not substitute a comparator that shows V7 in a
better light, and do not report the contrast against a different baseline
without saying that is what happened.

Slice results are **exploratory by construction**. They inform the frozen
campaign; they are not reportable as confirmatory findings. Say this wherever
the numbers are reported.

## Rules that bite here

- **§8 forbids collapsing D1–D6 into a single number.** Report the dimensions
  separately. There is no overall score, no mean-of-dimensions, no ranking
  column. If a summary seems needed, that is the prohibition working.
- **Invariant 2** — the framework writes numbers, agents write structure. No
  path reachable from an agent sets a metric, a posterior or a score.
- **Invariant 4** — the registry is append-only. A re-run does not replace a
  cell; it appends, and the content address distinguishes them. A cell that
  needs re-running because its inputs changed is a *new* address, not a
  correction of the old one.
- **Item 15 has no A-gate.** `scripts/status.py` lists it untracked. So there is
  no gate to name tests for — say explicitly what was tested instead, per
  `/next`'s rule for untracked items.

## Running it

Twenty seeds per cell, all randomness through explicitly passed seeded
generators (invariant 3). The suite's own economics apply: this is long, so
background it rather than blocking, and **do not run a subagent alongside** —
four read-only agents alongside a `-n 4` suite cost 12–15% (measured 2026-08-16,
recorded in `CLAUDE.md`), and a matrix cell is far longer than a suite run.

Checkpoint as cells complete. Days of compute is longer than any session, so the
matrix must be resumable from the registry rather than from a session's memory:
before starting a cell, check whether its content address is already registered.

## Reporting

- Which platform every cell ran on, and that it was one platform.
- The preregistered contrast, stated as exploratory.
- D1–D6 separately.
- `/decide` for the measured numbers — they are expensive to reproduce, which is
  exactly what `docs/DECISIONS.md` is for.

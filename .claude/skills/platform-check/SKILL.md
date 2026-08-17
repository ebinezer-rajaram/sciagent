---
name: platform-check
description: Dormant. Run the cross-platform determinism instrument and diff it against the other platform's baseline, to localise the measured Windows/Ubuntu divergence to either the event loop or the metric layer. The project is pinned to Windows and this is not part of ordinary work — invoke it only when deliberately lifting that pin, or when a number produced on one platform is about to be compared with one produced on the other.
allowed-tools: Bash, Read, Grep
---

# Cross-platform determinism check

## This skill is dormant. Check that you want it

The project is **pinned to Windows**: every registry entry, cached table and
reported number comes from the desktop, and cloud sessions edit code without
producing results. Under that pin the divergence below cannot bite, because
nothing else produces a number to disagree with. See CLAUDE.md, "Cloud sessions".

So this is not a precondition on anything, and nothing routine should invoke it.
It is the instrument for two situations only:

1. Somebody wants to **lift the pin** — run results on a second platform, or
   accept a cloud-produced artefact.
2. A number from one platform is **about to be compared** with one from another,
   knowingly.

If neither is true, stop here and say the pin makes the check unnecessary. The
material below is preserved because it is expensive to re-derive, not because it
is waiting on somebody.

## What is already settled — do not re-derive it

`docs/DECISIONS.md`, entry **2026-08-15 "the cross-platform divergence is real,
and it has been measured"** (line ~2957). Read it before running anything.

The divergence is **demonstrated, not suspected**. S12's final PPC p-value is
`0.101100` on Windows and `0.1009` on Ubuntu, and the last row of that entry's
table settles that the code is not the difference: a detached worktree at
Ubuntu's own commit `200d218`, run on Windows, gives the Windows number. Same
source, same seed, two platforms, two answers.

The consequence is the serious part, and it is why this skill exists: **the
registry content-addresses over (env version, config, data version, metric
version, seed) with no platform term.** Two entries can share a content address
while holding different numbers. `.cache/tables/` is keyed on replicates, seed
and design set — not on the machine that filled it — so a table shared between
platforms is suspect too.

That entry's standing instruction — *no further cloud-produced number should be
trusted until this is settled* — has been **discharged by the Windows pin**
rather than by settling the divergence. No cloud-produced number is trusted
because none is produced. Do not quote it as a live blocker.

## The one open question

Whether the divergence lives in the **metric layer** specifically. That is what
this run answers, and it is one command away.

`tests/acceptance/determinism_child.py` emits two layers:

| line form | digests |
|---|---|
| `<name> <sha256>` | the event log |
| `metrics/<name> <sha256>` | every value the SPEC §4.3 catalogue computes *from* that log |

The second layer is the one that matters. An earlier attempt at this check used
only the first, and a log digest is identical whether or not BLAS sums a dot
product in a different order — so the instrument and the suspicion never met,
and it produced a clean diff that proved nothing.

## Run it

On **both** platforms, from the repository root, at the **same commit**:

```sh
git rev-parse HEAD                                    # record it; must match
uv run python tests/acceptance/determinism_child.py > digests-<platform>.txt
```

Then diff:

```sh
diff digests-windows.txt digests-ubuntu.txt
```

## The decision rule — apply it exactly

| what differs | conclusion |
|---|---|
| `metrics/` lines differ, log lines identical | **The diagnostics are the site.** The event loop is exonerated. This is the expected outcome and the one that localises the bug. |
| log lines differ | Larger than suspected — the event loop itself is platform-dependent. Stop and report; this changes the scope of the problem. |
| nothing differs | The divergence is downstream of both layers, or the run did not reproduce it. Do **not** report this as "platforms agree" — check the commits matched and that the child covers the path the number came from. |

## Reporting

- The commit, and confirmation it was identical on both sides.
- The real `diff` output, not a summary of it.
- Which row of the rule above fired.

Then `/decide`, referencing the 2026-08-15 entry.

**Do not choose the remedy here.** That entry deliberately leaves open whether
the content address gains a platform term, whether tables become
platform-scoped, or whether the §4.3 estimators are taken out of BLAS. The first
two retire every stored artefact and the third changes frozen estimators; the
choice wants this localisation first, and it is the user's to make.

## If you only have one platform

Say so and stop. Half of a diff is not evidence. A cloud session can produce the
Ubuntu side; the Windows baseline is recorded in the entry above.

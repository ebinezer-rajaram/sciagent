# Pre-registration amendment: search misses on P4's fits (fit/5)

**Status: DRAFT, not frozen.** Written in lane session 34 under the control room's acceptance of the amendment (INBOX c81749a; LOG 2026-11-05) and the plan `docs/v2/P4-MISS-PLAN.md`. One red-team round. `F2` is the commit that freezes this file and `R2` the later commit that adds the script and its test. "The pilot rule" means `docs/v2/prereg/2026-11-04_pilot-search-misses.md` at `F` = 3226f9b, and "PR x" means its section x. Sections here that say "as PR x" take that text unchanged, with "pilot" read as "P4" only where this file says so. The pilot's records and label stay as they are (`docs/v2/PILOT-MISSES.md`).

**What has been touched.** No P4 unit exists, no test split exists, and no contrast has been computed on anything. The pilot's results are known and informed this draft: the pilot check found 25 miss rows on 25 fits, largest |Δ| 0.02198, group A 5 of 6, groups B and D caught, and 25.41 CPU hours (`docs/v2/PILOT-MISSES.md`). So nothing below is blind to how R1 did on the pilot.

**A dependency this draft cannot close.** The frozen P4 contrast code does not exist. At `p4-rehearsal` a9e43a6 no file under `src/sciagent/campaign/` or `scripts/` computes C1 to C6: `scoring/stats.py` has the paired bootstrap, and nothing assembles a contrast. `P4_ARMS` holds the four pilot arms on the frozen prompt variant, `llm_model` is one value per campaign, and neither an AG-S-boed arm nor a Haiku or Opus arm is planned in code. This file therefore fixes an interface (section 2.7) and the script cannot reach `R2` until that code exists. It is analysis code under SPEC §6.1, so it freezes in the same place.

## 1. Question and falsifiable hypotheses

**Question.** fit/5's coordinate ψ search can stop below the best point of its own grid. How many of P4's scored rows carry such a fit, how much does the better fit move the held-out reading, and does each preregistered contrast keep its sign and its CI's relation to zero when every better fit found is substituted at once?

- **H1 (instrument).** As PR 1, under section 2.9 below.
- **H2 (count).** As PR 1: no scored row carries a miss the reference finds. Falsified if M ≥ 1.
- **H3 (size).** As PR 1: no miss found moves its row's held-out reading by more than 0.01 nats per event.
- **H4 (contrasts).** Every read of section 2.7 keeps its sign, its CI's relation to zero and its reportable state. Falsified if any does not.

H2 and H3 can be falsified but never confirmed. The pilot falsified both, so no one should expect M = 0. "Restate the bound" as PR 1: a lower bound on M, the largest |Δ| found, and no bound on misses not found.

## 2. Design

### 2.1 Which units

The population is every cell the frozen P4 report reads from the P4 store, classified by the script as PR 2.1 classifies, with the same classes: coordinate path (G > 512), exhaustive (G ≤ 512), no ψ search (B-np, B-lib out-of-grammar member), no submission. A fit is (truth, seed, stored structure text), as PR 2.1. All rows are classified and none sampled.

**Changed from PR 2.1: no hand census.** The pilot's census was read before its freeze. Here the script classifies, and prints counts only. It prints no score, `oracle_*` value, `certified` value or contrast before the label.

Fits on charged efficiency curves stay out, as PR 2.1, and the report says so.

### 2.2 to 2.5 The reference, the miss rule, held-out size, selection effects

As PR 2.2, 2.3, 2.4 and 2.5, on `FIT_VERSION` fit/5. Changes:

- **Seeding.** Root **20261108** in place of 20261104, with `c = f"{truth}|{seed}|{text}"` as before, where `truth` is the test split's truth id. `c = f"t2|..."`, `f"hunt|..."` and `f"s2|..."` as PR 2.2. This stream has not been run on anything. Tests use 20261107 as before, and never the frozen stream.
- **Selection effects, two rows added.** AG-S-boed: as the LLM arms, not covered. The §4.2 policy arms on B-sym: as B-sym, not covered, and the policy's own choice of experiments is not rerun; on AG-c-S: as the LLM arms.
- **Byte check.** As PR 2.4, on every coordinate row, against the P4 store's own cell. A mismatch stops the run.

### 2.6 LLM rows

PR 2.6's first and fourth points do not hold here, and are replaced.

1. LLM rows **are** read by the outcome: C1 to C4 read them.
2. As PR 2.6: no truth-ψ rescue.
3. As PR 2.6, and it weighs more: sessions cannot be replayed under a better fit, the agent saw fit/5's numbers, and P4's claims are about those sessions. Substitution corrects a score, not what the agent did after seeing fit/5's number. The report says this beside every contrast whose rows include a substituted LLM row.
4. No control covers an LLM arm (SPEC §6.4). So the label says what the found misses do to a contrast, and it is not evidence that an arm's fits are sound. The per-arm counts are given without comparison, as PR 4's exploratory table.

### 2.7 What "the contrasts survive" means

**The interface the contrast code must meet.** One pure function, frozen with the §6.1 commit:

```python
reads = read_contrasts(table, rng_seed, resamples)   # Mapping[str, ContrastRead]
ContrastRead(point: float, low: float, high: float, reportable: bool, rows: tuple[RowKey, ...])
```

It takes a table of rows after `analysis.derive`, applies the §6.4 controls and their drop rule itself, and draws every bootstrap from a generator built from `rng_seed` inside the call, so that two calls resample the same truths in the same order. `reportable` is false when a control gating that read fails or its rows are too few to compute. `rows` lists every row the read used, gap references included.

**The reads.** One per fit-based statement of SPEC §6.2. `C1`; `C2-AG-c`, `C2-AG-o`; `C3-boed` (AG-c-S against AG-S-boed), `C3-free` (AG-o-S against AG-c-S at equal E); `C4`; `C5-gap`. C5's second half, the rate of observing the telling diagnostic, reads no fit, so substitution cannot touch it and it has no read here. C6 is exploratory and has no label, as accepted.

**The substitution.**

```python
corrected = {row key: per_event}          # rows with a reading miss only
rows = [replace(r, per_event=corrected.get(key(r), r.per_event)) for r in table.rows]
table2 = replace(table, rows=tuple(analysis.derive(rows)))
stored = read_contrasts(table, 20261110, 10_000)
fixed = read_contrasts(table2, 20261110, 10_000)
```

Every system's better fit goes in at one pass, ORACLE included, so gaps and kept truths are recomputed against corrected references, as PR 2.7. `read_contrasts` on `replace(table, rows=tuple(analysis.derive(table.rows)))` with an empty `corrected` must equal `stored` field by field, otherwise the run stops (`BYTE_FAIL`). `oracle_underfit` as PR 2.7.

**A read changes** when, between `stored` and `fixed`, any of these differs: the sign of `point`; whether `[low, high]` excludes zero; `reportable`. A point of exactly zero has its own sign. The third clause goes beyond the words of INBOX c81749a, which name the first two: without it, a corrected table that fails a §6.4 control would have no read to compare. It only ever adds a change.

**Survival is a statement about found misses only.** All reads unchanged, every tier 1 fit processed and H1 standing gives `CONTRASTS_SURVIVE_FOUND_MISSES`, with this wording:

> The contrasts survive substitution of the N misses found (N counts rows a read uses). Of the T fits the reads use, E are exact over their certified grid points, U are searched and unvalidated, X are exhaustive but uncertified and were not checked, and A are outside the check. Nothing is said about misses on the U fits that the search did not find, or about what any agent would have done under a better fit.

The label line gives those counts per read, and the control state per stratum as PR 2.7. A CI that touches zero under both tables is reported as unchanged, and the report prints both intervals in full, so a read that moved within its class can be seen.

**If a read changes**, the label is `CONTRASTS_CHANGE`, whether or not R1's control passed, because a substituted fit is real, certified and better. The report names the read, its rows and both reads. **The stored numbers stay the record, both are reported, and the owner decides what P4 can claim.**

**What stays unknown either way:** as PR 2.7, with "the 140 searched fits" read as P4's searched count.

### 2.8 The store is not touched

As PR 2.8, on the P4 store. No contrast or generated report is produced from corrected numbers except this check's own output.

**Keeping the contrasts unread.** The script computes `stored` and `fixed` together at the end, in one step, and writes them to `decision.json` at the same moment. No session and no helper computes or reads a contrast from the P4 store before that file exists, and P4's report is not generated until then. Reads of coverage, wall time and row counts during the campaign are allowed, and print no score. `journal.jsonl` as PR 9, point 4.

### 2.9 Positive control (SPEC §6.4), first

As PR 2.9: groups A, D, C and B in that order, the same catch rule, the same pass rule (at least 5 of 6 in A, B caught, D caught), R1 alone and blind. Changes:

- **Fresh streams.** Group A, B and D's R1 runs use root 20261108, and the hunt uses root **20261109** in place of 20261106, with the same four sub-strata, the same case rule and the same 6 CPU hour cap. Rerunning the pilot's streams would only repeat their answer. The pilot's result is not carried over: a control passes or fails on this run.
- **Group B no longer reads a store.** After F5 the pilot store cannot be read from the merged head (LOG 2026-11-05). Group B stays ORACLE dev-015 s3, run fresh through `Oracle(...).run(data)` on the dev split. Its known better value and its R0 readings are committed constants, taken as hex from the pilot check's `units.jsonl`. R0 must reproduce them bit for bit, otherwise `BYTE_FAIL`. This doubles as the check that fit/5's behaviour did not move between 900e66a and the freeze head.
- **Group E, added, no pass threshold.** Every ORACLE unit of the test split whose stored `oracle_search_ll` is below its stored `oracle_truth_psi_ll` by more than the tolerance, with the truth-ψ point certified. These are the only P4-scale cases with a known better value. Each is run blind, as group B. Its catch count is reported as a rate with its denominator. Group E runs in tier 1's order, not first, because reading those two fields is reading a P4 row. The script selects the cases and prints only their count before the label.

"If H1 fails, the run continues" and "what the controls do not cover", as PR 2.9. The group C limits stand, and P4's logs are not shorter than the pilot's.

### 2.10 Code

- **Tests first** (CLAUDE.md, SPEC §6.3), as PR 2.10's list where it applies, plus: the guards below each refuse when they should; `read_contrasts` on a toy table reproduces a hand-computed paired CI; the same seed resamples identically on `stored` and `fixed`; a planted shift flips each clause of "a read changes"; a control that fails only on the corrected table sets `reportable` false and the label `CONTRASTS_CHANGE`; nothing is printed or written from `read_contrasts` before the final step; SIGTERM between `_stop_reason()` and the `terminating` check cannot end a stage as finished (LOG 2026-11-05). No test touches a P4 unit. Toy tables only.
- **Script:** a new file `scripts/p4_search_misses.py`, importing from `scripts/pilot_search_misses.py`'s logic by copy, so the pilot's frozen file stays byte-identical. Its own test `tests/campaign/test_p4_search_misses.py`.
- **Fit code.** `git diff --quiet 900e66a HEAD -- src/sciagent/glm/fit.py src/sciagent/glm/fit_solve.py src/sciagent/glm/fit_bounds.py src/sciagent/glm/likelihood.py src/sciagent/glm/features.py src/sciagent/glm/grids.py` is clean, and `FIT_VERSION` is `fit/5`. Other `glm` files differ and may yet differ (`designs.py`, `interventions.py`, `simulate.py`, and error text in `grammar.py`), so a wider guard cannot hold. Group B's bit check and every row's byte check cover the behaviour.
- **The freeze has two commits, and no tuning window.** `F2` freezes this file. `R2` adds the two files and the LOG lines recording their hashes, and precedes or equals the §6.1 commit. At start the script refuses unless:
  - `PREREG_SHA256` and `F2_COMMIT` match, as PR 2.10, and the script and test hashes are the ones the LOG records in `R2`;
  - `git diff --name-only <S> HEAD` is empty outside `docs/`, where `S` is the §6.1 commit. `S` is read from `docs/v2/FREEZE-6.1.txt`, a one-line file committed straight after it, and must be an ancestor of HEAD whose tree holds this script with the recorded hash. The file's own commit is in `docs/`, so it passes;
  - the fit guard above is clean, the tree is clean, and the three BLAS variables are 1.
- **The splits.** Two hashes. `--split` for the dev split, with PR 2.10's constant, for groups B and D. `--test-split` outside the repository, hashed by the same recipe, against the hash in the §6.1 commit. A mismatch or a missing cell stops the run.
- **Where it runs:** a clean detached worktree at the head of v2 when the campaign ends, which the guards tie to `S`.
- **Output:** `/home/ubuntu/research/records/sciagent/p4-search-misses-F2/`, outside the repository, because it carries test-split readings; `s3://delaymargin-results-482913/sciagent/p4/search-misses-F2/` at the end. Record formats as PR 2.10.

**Rehearsal, before the freeze.** The script runs at the freeze head on a small store made at that head: dev split, baseline systems and scripted sessions, no LLM call. It never reads the pilot store, so the old address derivation is not carried. Exploratory, may not be cited as a P4 result, ceiling 5 CPU hours and $0, LEDGER row.

## 3. Primary analyses

| H | Statistic | Threshold | Falsified when |
|---|---|---|---|
| H1 | Group A caught, of 6; group B caught; group D caught | At least 5 of 6, yes, yes | Any part below that |
| H2 | M = rows carrying a reading miss, over the coordinate rows (also as distinct fits) | 0 | M ≥ 1 |
| H3 | max over miss rows of \|Δ\|, a move to or from `-inf` counting as above | 0.01 nats per event | Above 0.01 |
| H4 | Reads changed, section 2.7 | 0 | Any |

## 4. Secondary and exploratory analyses

- **S1 to S4** as PR 4. S2's second table adds its rows to `corrected`, and `read_contrasts` runs on it as `fixed2`. If any read differs from `fixed`, the label line says so in those words, whatever the label. S4's "§7.1 statistic" is replaced by each read's stored and corrected point and interval.
- **S5, the unsubstituted LLM caveat, sized.** For each read, the share of its rows that are substituted LLM rows. Counts only.
- **Exploratory**, labelled, as PR 4. No difference between systems in miss counts is claimed.

## 5. Decision rules

**This section alone sets the label.** One label to `decision.json`, by the first rule that applies. Exit codes as PR 5.

1. `BYTE_FAIL` (3): any byte check, a start-up guard, a split hash, a missing cell, the empty-`corrected` equality, group B's bit check or the store manifest failed, in any stage.
2. `CONTRASTS_CHANGE` (2): any read changed, on whatever has been found. It does not depend on H1, and it outranks every shortfall. If tier 1 did not finish, `decision.json` adds `partial: true`.
3. `INCOMPLETE` (5): no read changed and tier 1 fell short, as PR 5.
4. `CONTROL_FAIL` (4): no read changed and H1 is falsified.
5. `CONTRASTS_SURVIVE_FOUND_MISSES` (0): no read changed and H1 stands.

Beside the label as PR 5, with the per-read line in place of the per-gate line. A stop after tier 1 finished does not alter the label.

| Label | Next |
|---|---|
| `CONTRASTS_SURVIVE_FOUND_MISSES` | P4's report is generated from the stored table, and carries section 2.7's wording and the per-read line |
| `CONTRASTS_CHANGE` | Both reads are reported, the stored numbers stay the record, and the owner decides what P4 can claim. With `partial: true` nothing is applied until the owner has decided |
| `CONTROL_FAIL` | The stored contrasts are not put forward as checked. Exact class reported. A frozen amendment with a stronger reference, or the owner's own decision to go on with the gap stated |
| `INCOMPLETE` | Same as `CONTROL_FAIL`. Resume rules as PR 5 |
| `BYTE_FAIL` | Same, and it is an instrument fault: root cause first, by amendment, before any reading |

Under every label P4's stored contrasts remain what was computed. The control room's decision, not the lane's, sends them onward.

## 6. Exclusions and failure handling

As PR 6, with these classes on P4's rows. No row is excluded for its outcome. A row `read_contrasts` drops under §6.4's rule is still classified and checked.

## 7. Sample size

A census, fixed by the store. P4-PROPOSAL §4 gives 1,520 scored sessions and 740 baseline units, 2,260 rows. Tier 1 by the proposal's table, if `read_contrasts` reads what SPEC §6.2 names: AG-c-S named and anon 160, AG-o-S named and anon 160, AG-S-boed 80, B-sym 80, B-sparse 80, ORACLE 80, B-lib 80, the FSD and EIG arms 160, and the rows the §6.4 controls read that are not yet counted (planted-hint 80, B-rand 80, B-sym@10F 40): 1,080 rows. The rule is the code's `rows`, not this count. Coverage statements as PR 7, and no recall bound is quoted.

**Power.** This check has none to speak of on a contrast: C1 to C5 pair over 40 truths, and the label compares two intervals, each from one fixed resampling. A read near zero can flip on a small move. That is why both intervals are printed.

## 8. Cost ceiling and wall clock

**Ceiling: 178 CPU hours over all units of this freeze, $0.** No LLM call, no instance beyond dm-devbox, S3 a few MB. Dispatch stops at **172 CPU hours**, and at most 2 jobs in flight at 3 CPU hours each take it to 178, enforced as PR 8. It costs machine time, and no money.

**Arithmetic.** The pilot check used 25.41 CPU hours on 670 rows, 0.0379 per row, so 2,260 rows is about 86 CPU hours and tier 1's 1,080 rows about 41. The stop is twice the expected figure, because P4's mix differs from the pilot's (62 of 240 LLM rows were on the coordinate path there, 205 of 430 baseline rows) and controls that do not grow are inside the 0.0379. A guide, not a bound: the pilot's own estimate was 55 to 106 and it used 25.41. The stop is enforced and not assumed, and at the stop the label follows section 5. If §6.1's plan has more than 2,260 rows, this ceiling needs an amendment before the freeze.

**Machine and wall clock.** As PR 8: 2 workers, `nice` 10, launch at `MemAvailable` ≥ 8 GiB. At 0.81 of a core per worker, 172 hours is about 106 wall hours and 86 hours about 53. `RuntimeMaxSec` 120 hours. The check adds 2 to 5 days after the campaign, before anything is read.

**Order, fixed and outcome-neutral.** Controls A, D, C, B; tier 1 with group E; S2; tier 2. Inside a stage by `sha256(f"{truth}|{seed}|{text}")`.

## 9. Early-stop conditions

As PR 9, with "the baseline `Verdict` equality" read as the empty-`corrected` equality. There is no stop on outcome.

## 10. Reported whatever the outcome

As PR 10, in one note under `docs/v2/`, red-teamed, carrying no test-split record beyond what P4's report itself publishes, with `read_contrasts`' stored and corrected reads in place of the verdict lines, S5, group E's rate, and section 2.6's third point in words. It sits beside the pilot's note and replaces nothing.

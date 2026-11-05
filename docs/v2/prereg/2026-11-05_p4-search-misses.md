# Pre-registration amendment: search misses on P4's fits (fit/5)

**Status: DRAFT, round 2, not frozen.** Written in lane session 34 under the control room's acceptance of the amendment (INBOX c81749a; LOG 2026-11-05) and the plan `docs/v2/P4-MISS-PLAN.md`. Round 1 (71997c2): NOT READY, nine blocking (`docs/v2/redteam/2026-11-05_p4-search-misses.md`). This revision answers them, finding 6 by the control room's decision (below). Four commits matter: `F2` freezes this file, `R2` adds the script and its test, `S` is the §6.1 commit, and `T` is the one commit after `S` that adds `docs/v2/FREEZE-6.1.txt`. "The pilot rule" means `docs/v2/prereg/2026-11-04_pilot-search-misses.md` at `F` = 3226f9b, and "PR x" means its section x. **Every part of the pilot rule carries over unless this file changes it**, with "pilot" read as "P4", "the 78-hour stop" as 172 CPU hours, "84" as 178, "60 hours" as 120, "root 20261104" as 20261111 and "root 20261106" as 20261112. The pilot's records and label stay as they are (`docs/v2/PILOT-MISSES.md`).

**What has been touched.** No P4 unit exists, no test split exists, and no contrast has been computed on anything. The pilot's results are known and informed this draft: the pilot check found 25 miss rows on 25 fits, largest |Δ| 0.02198, group A 5 of 6, groups B and D caught, and 25.41 CPU hours (`docs/v2/PILOT-MISSES.md`). So nothing below is blind to how R1 did on the pilot.

**Dependencies this draft cannot close.**

- **The contrast code does not exist.** At `p4-rehearsal` a9e43a6 nothing under `src/sciagent/campaign/` or `scripts/` computes C1 to C6: `scoring/stats.py` has the paired bootstrap, and nothing assembles a contrast. `P4_ARMS` holds the four pilot arms, and the `rescaled` condition is not built (`controls_p4.py`). This file fixes an interface (section 2.7), and `R2` cannot exist before that code does. It is analysis code under SPEC §6.1.
- **The `rescaled` condition is not built either,** and the C2 reads need it.

**The list of reads is settled.** The owner approved P4-PROPOSAL §5's decisions 1 to 17 as recommended (LOG, lines 583-602), including Δ9's `rescaled` and decision 12's "descope C5 to P4b", which takes C3's AG-S-boed half too. "C1 to C5" in INBOX c81749a was the control room's loose wording, not a reversal (INBOX c65a614). So this amendment covers C1, C2, C3 (AG-o against AG-c) and C4. SPEC §7's P4 row still says C1 to C5; a SPEC change is a stop point and is not made here.

**Extensions beyond the accepted text and plan, stated as such** (red team finding 11):

1. A read also changes if its `reportable` state changes. The accepted words name sign and CI only.
2. New roots for groups A, B and the hunt, where the plan said 2.2 and 2.9 carry unchanged. Rerunning the same stream would repeat the pilot's answer. The cost, stated now: on PR 2.9's figures H1 stands with probability about 0.97 × (0.45 to 0.94), so 0.44 to 0.91, and a fail on group B is a live possibility and not a surprise.
3. Group E, close to a row PR 2.9 deleted, now selected so that the reason for that deletion does not apply (section 2.9).
4. The rehearsal moves off the pilot store, forced by F5.
5. S5, the reads file, and `docs/v2/FREEZE-6.1.txt`, which are new artefacts.
6. The ceiling is set before the row count is fixed, against the plan's "once P4's row count is fixed", from the larger table, so it holds for the 1,940 rows expected.

## 1. Question and falsifiable hypotheses

**Question.** fit/5's coordinate ψ search can stop below the best point of its own grid. How many of P4's scored rows carry such a fit, how much does the better fit move the held-out reading, and does each preregistered contrast keep its sign and its CI's relation to zero when every better fit found is substituted at once?

- **H1 (instrument).** As PR 1, under section 2.9 below.
- **H2 (count).** As PR 1: no scored row carries a miss the reference finds. Falsified if M ≥ 1.
- **H3 (size).** As PR 1: no miss found moves its row's held-out reading by more than 0.01 nats per event.
- **H4 (contrasts).** Every read of section 2.7 keeps its sign, its CI's relation to zero and its reportable state. Falsified if any does not. A read not reportable on both tables is no evidence either way, and is named (section 2.7).

H2 and H3 can be falsified but never confirmed. The pilot falsified both, so no one should expect M = 0. "Restate the bound" as PR 1: a lower bound on M, the largest |Δ| found, and no bound on misses not found.

## 2. Design

### 2.1 Which units

The population is every cell the frozen P4 report reads from the P4 store, classified by the script as PR 2.1 classifies, with the same classes: coordinate path (G > 512), exhaustive (G ≤ 512), no ψ search (B-np, B-lib out-of-grammar member), no submission. A fit is (truth, seed, stored structure text), as PR 2.1. All rows are classified and none sampled.

**Changed from PR 2.1: no hand census.** The pilot's census was read before its freeze. Here the script classifies, and prints counts only. It prints no score, `oracle_*` value, `certified` value or contrast before the end.

**Tier 1 and tier 2, replacing PR 8's definition.** Tier 1 is every fit on a row returned by `contrast_rows(plan, truths)`, LLM rows included. That is a pure function frozen with the contrast code. It reads no score and no cell, and it covers every truth before any drop, gap references and the rows the §6.4 controls read included. So tier 1 cannot depend on an outcome, and a truth kept only in the corrected table still has its rows in tier 1. Tier 2 is every other checkable fit in the population.

**What the store must hold, replacing PR's "0 missing".** Expected rows are the planned units from the frozen plan. A planned unit with neither a reading nor a counted void or failure is **missing**, and that includes a non-void session with no score cell. The script refuses to start while anything is missing, with no label: the campaign is not finished. Void and failed units are counted, listed, and are absent rows under section 2.7's pair rule. They do not give `BYTE_FAIL`.

Fits on charged efficiency curves stay out, as PR 2.1, and the report says so.

### 2.2 to 2.5 The reference, the miss rule, held-out size, selection effects

As PR 2.2, 2.3, 2.4 and 2.5, on `FIT_VERSION` fit/5. Changes:

- **Seeding.** Root **20261111** in place of 20261104, with `c = f"{truth}|{seed}|{text}"` as before, where `truth` is the test split's truth id. `c = f"t2|..."`, `f"hunt|..."` and `f"s2|..."` as PR 2.2. This stream has not been run on anything, and it stays so until the launch. Tests and the rehearsal use the pilot test's roots 20261107 (`TEST_ROOT`) and 20261108 (`TEST_LOG_ROOT`), and the test's disjointness assertion carries over.
- **Caps.** K = 48 and the 8,000 and 3,000 point caps stay as PR 2.2. Their stated reason, the 78-hour stop, does not bind at 172 hours. They are kept so that R1 here is the instrument the controls measured, not because of cost.
- **Selection effects.** The `rescaled` arms: as the LLM arms, not covered.
- **Byte check.** As PR 2.4, on every coordinate row, against the P4 store's own cell. A mismatch stops the run.

### 2.6 LLM rows

PR 2.6's first and fourth points do not hold here, and are replaced.

1. LLM rows **are** read by the outcome: C1 to C4 read them.
2. As PR 2.6: no truth-ψ rescue.
3. As PR 2.6, and it weighs more: sessions cannot be replayed under a better fit, the agent saw fit/5's numbers, and P4's claims are about those sessions. Substitution corrects a score, not what the agent did after seeing fit/5's number. The report says this beside every contrast whose rows include a substituted LLM row.
4. No control covers an LLM arm (SPEC §6.4). So the label says what the found misses do to a contrast, and it is not evidence that an arm's fits are sound. The per-arm counts are given without comparison, as PR 4's exploratory table.

### 2.7 What "the contrasts survive" means

**The interface the contrast code must meet.** Pure functions, frozen with the §6.1 commit:

```python
contrast_rows(plan, truths)                                    # tuple[RowKey, ...]; no score read
reads = read_contrasts(table, controls, 20261110, 10_000)      # Mapping[str, ContrastRead]
ContrastRead(point: float, low: float, high: float, reportable: bool, n: int, rows: tuple[RowKey, ...])
```

`table` is a table of rows after `analysis.derive`. `controls` maps controls 4 and 6, which are scripted runs outside the table, to their pass state, and it is the same value in both calls. `read_contrasts` applies controls 1 to 3 and their drop rule to `table` itself. Read `i` of the sorted read names draws from `default_rng(SeedSequence([20261110, i]))`, so reads do not share a generator. Two calls resample identically when `n` is equal; when a truth is dropped from one table the resamples differ, by about 7e-6 at the lower end on the reviewer's example, against a Monte Carlo spread of 0.00017. Row keys must be unique over the plan, budget and arm included, and a test asserts it (the pilot's (truth, seed, system) has no budget, so sweep arms need the system name to carry it).

**Which control gates which read.** Controls 1, 2 and 3 gate every read. Control 4, the naming null, gates the C2 reads. A read is `reportable` only if its gating controls pass on that table and `n` is at least 2. Otherwise `point`, `low` and `high` are `nan`.

**The pair rule, which the contrast code must implement** (finding 4). The bootstrap unit is the truth (P4-PROPOSAL §4). For one system on one truth, the value is the mean over seeds of the row values, after the following.

- A row that scored `-inf`, or has no submission, is a real outcome. It takes the **floor** of its (truth, seed): the lowest finite `per_event` among all rows of that truth and seed in the table, floored before `derive` computes gaps. So the worst outcomes count against a system, and no pair is dropped for them. The floor is recomputed on the corrected table, so a row that crosses the boundary under substitution is handled the same way.
- A void or failed row is not an outcome of the system: it is absent. A truth with no row for one side of a read leaves that read, and the count is printed.
- With no finite row on a (truth, seed), its rows are absent.

An exception from `read_contrasts` is an instrument fault and gives `BYTE_FAIL`.

**The reads.** One per fit-based statement of SPEC §6.2 as the owner's decisions stand: `C1`; `C2-AG-c-time`, `C2-AG-c-names`, `C2-AG-o-time`, `C2-AG-o-names` (Δ9: named against rescaled, rescaled against anon); `C3-free` (AG-o-S against AG-c-S at equal E); `C4` (about 20 truths, the out-of-dictionary half). C6 is exploratory and has no label, as accepted. C5 and C3's AG-S-boed half are P4b's under decision 12, and P4b would need its own amendment. A read with no rows is not reportable and is named.

**The substitution.**

```python
corrected = {row key: per_event}          # rows with a reading miss only
rows = [replace(r, per_event=corrected.get(key(r), r.per_event)) for r in table.rows]
table2 = replace(table, rows=tuple(analysis.derive(rows)))
stored = read_contrasts(table, controls, 20261110, 10_000)
fixed = read_contrasts(table2, controls, 20261110, 10_000)
```

Every system's better fit goes in at one pass, ORACLE included, so gaps and kept truths are recomputed against corrected references, as PR 2.7. `read_contrasts` on the table rebuilt by the same code with an empty `corrected` must equal `stored`, every float compared by `float.hex`, so `nan` equals `nan`. Otherwise `BYTE_FAIL`. `oracle_underfit` as PR 2.7.

**A read changes** when, between `stored` and `fixed`, any of these differs: the sign of `point`; whether `[low, high]` excludes zero; `reportable`. A point of exactly zero has its own sign. It only ever adds a change.

**A read not reportable on both tables has not survived.** It is left out of the survival statement and named on the label line as "not reportable, no statement". If no read is reportable on both tables, the label cannot be `CONTRASTS_SURVIVE_FOUND_MISSES` (section 5).

**Survival is a statement about found misses only.** All reads unchanged, at least one reportable on both tables, every tier 1 fit processed and H1 standing gives `CONTRASTS_SURVIVE_FOUND_MISSES`, with this wording:

> The reportable contrasts survive substitution of the N misses found (N counts rows a read uses). Of the T fits the reads use, E are exact over their certified grid points, U are searched and unvalidated, X are exhaustive but uncertified and were not checked, and A are outside the check. Nothing is said about misses on the U fits that the search did not find, or about what any agent would have done under a better fit.

The label line gives those counts per read, the control state per stratum as PR 2.7, and group E's caught count with its denominator. The report prints both intervals in full, so a read that moved within its class can be seen.

**If a read changes**, the label is `CONTRASTS_CHANGE`, whether or not R1's control passed, because a substituted fit is real, certified and better. The report names the read, its rows and both reads. **The stored numbers stay the record, both are reported, and the owner decides what P4 can claim.**

**What stays unknown either way:** as PR 2.7, with "the 140 searched fits" read as P4's searched count.

### 2.8 The store is not touched, and the contrasts stay unread

As PR 2.8, on the P4 store. Changes follow, because on P4 this is the first unblinding, which the pilot's check was not.

- **The reads file.** `stored`, `fixed`, the S2 read and the label are written once, in one step, to `reads.json`, on a terminal end only, and to no other file. `decision.json` holds no score, no read and no label until then, and `refuse()` writes the refusal alone. There is **no provisional label**, so PR 5's provisional paragraph and PR 9's "at once" for the equality check do not carry: `read_contrasts` is not called, by any path, before that step. A failed equality gives `BYTE_FAIL`, and `reads.json` then carries the label and no read.
- **A terminal end is a choice made once.** `--finalise` on a run that could still resume refuses unless given `--end`, and it says so first. Once `reads.json` exists, every resume is refused. So nobody reads the tables and then chooses whether to go on.
- **P4's report refuses to run without `reads.json`,** prints its contrasts from that file's `stored`, and runs no bootstrap of its own, so it cannot show an interval that contradicts the label.
- **A stronger reference after the reads.** H1's result is known before tier 1, so an amendment for `CONTROL_FAIL` is best written then. One written after `reads.json` exists is written with the contrasts in hand, and the report says so. The first label stands, as section 2.10.
- **Procedure.** No session or helper computes or reads a contrast from the P4 store before `reads.json` exists, and every helper's brief says so. Coverage, wall time and row counts during the campaign are allowed, and print no score. `journal.jsonl` as PR 9, point 4. This part is procedural, and the code above does not make it impossible for someone holding the store.

### 2.9 Positive control (SPEC §6.4), first

As PR 2.9: groups A, D, C and B in that order, the same catch rule, the same pass rule (at least 5 of 6 in A, B caught, D caught), R1 alone and blind. Changes:

- **Fresh streams.** Group A, B and D's R1 runs use root 20261111, and the hunt uses root **20261112**, with the same four sub-strata, the same case rule and the same 6 CPU hour cap. The pilot's result is not carried over: a control passes or fails on this run. Extension 2 above gives the odds.
- **Group B no longer reads a store.** After F5 the pilot store cannot be read from the merged head (LOG 2026-11-05). Group B stays ORACLE dev-015 s3, run fresh through `Oracle(...).run(data)` on the dev split. Its known better value and its R0 readings are committed constants, taken as hex from the pilot check's `units.jsonl`. R0 must reproduce them bit for bit, otherwise `BYTE_FAIL`. This is the one check across 900e66a and the freeze head. It has not been run yet, and it is run once, R0 only, before `F2`: if it fails, this section changes first.
- **Group E, added, no pass threshold.** Every ORACLE unit of the test split with stored `oracle_psi_source == "truth"`: the search lost to a certified truth-ψ fit, which `systems.py` keeps only when certified, so PR 2.9's reason for deleting the old row does not apply. Each is run blind, as group B. Its rate goes on the label line, with its denominator. It runs in tier 1's order, not first, because reading that field is reading a P4 row. The script selects the cases and prints only their count, and they are the only P4-scale cases with a known better value.

"If H1 fails, the run continues" and "what the controls do not cover", as PR 2.9. The group C limits stand, and P4's logs are not shorter than the pilot's.

### 2.10 Code

PR 2.10 carries over whole except where this section changes it. That includes: a change to the script after `R2` is an amendment, needs the control room's approval, is a new frozen version and writes to a new output directory; the first records stay in the report beside any new ones; **the first run's label stands, and a later label is reported beside it and replaces it only by the owner's logged decision**; `R2` is pushed before the launch; a fresh start in a directory that holds records is refused.

- **Tests first** (CLAUDE.md, SPEC §6.3), as PR 2.10's list where it applies, plus: each guard below refuses when it should, including a repointed `FREEZE-6.1.txt` and a store whose witness does not match; `contrast_rows` reads no cell; the pair rule on `-inf`, no-submission, void and a row crossing the boundary; `read_contrasts` on a toy table reproduces a hand-computed paired CI; a planted shift flips each clause of "a read changes"; a read not reportable on both tables is named and cannot produce the survival wording; `nan` compares equal by hex; a control that fails only on the corrected table gives `CONTRASTS_CHANGE`; nothing writes a read before the terminal step, on every path (`refuse`, a stage's end, `--finalise`, SIGTERM); a resume is refused once `reads.json` exists; SIGTERM between `_stop_reason()` and the `terminating` check cannot end a stage as finished (LOG 2026-11-05). No test touches a P4 unit. Toy tables only.
- **Script:** a new file `scripts/p4_search_misses.py`, copying what it needs, so the pilot's frozen file stays byte-identical. Its own test `tests/campaign/test_p4_search_misses.py`.
- **Fit code.** `git diff 900e66a HEAD -- src/sciagent/glm/fit.py src/sciagent/glm/fit_solve.py src/sciagent/glm/fit_bounds.py src/sciagent/glm/likelihood.py src/sciagent/glm/features.py src/sciagent/glm/grids.py` lists one changed line, the refusal text at `features.py:417` (`p4-rehearsal` 6273833), and `FIT_VERSION` is `fit/5`. The script holds that one-line diff's sha256 as a constant and refuses any other. `src/sciagent/core/reductions.py` differs from 900e66a by 86 lines, and the commit messages say results are bit-identical whenever `fsum` answers. It is pinned by its blob hash at `S`. Group B's bit check is the evidence across that difference, on one unit. Other `glm` files differ and may yet differ (`designs.py`, `interventions.py`, `simulate.py`, refusal text in `grammar.py` and `syntax.py`).
- **The chain of commits, no tuning window.** `F2` freezes this file. `R2` adds the script, its test and the LOG lines recording their hashes, and precedes `S`. `S` is the §6.1 commit, and it holds the test split's hash at `docs/v2/freeze/test-split.sha256`. `T` adds `docs/v2/FREEZE-6.1.txt`, one line naming `S`, and is pushed before the campaign starts. The campaign runs at `T` and the script runs in a clean detached worktree at `T`, never at the head of v2, so later commits cannot touch it. At start the script refuses unless:
  - HEAD is `T`: `git log --format=%H -- docs/v2/FREEZE-6.1.txt` shows exactly one commit, it is HEAD, its first parent is `S`, and its diff is that file alone;
  - `PREREG_SHA256` and `F2_COMMIT` match, and the sha256 of the script and of its test appear in `git show S:docs/v2/LOG.md`, not in the working LOG. `decision.json` records `R2` as that LOG names it;
  - the store's `fingerprint.jsonl` has one `establish` line and no `reuse` line, and its commit is `T`. The store was begun before any data existed, so this ties `S` to the campaign: repointing the file after the fact gives a different commit and fails here;
  - the fit guard above is clean, the tree is clean, and the three BLAS variables are 1.
- **The splits.** Two hashes, each over the sorted `sha256sum` lines with paths relative to the split's parent's root. `--split` for the dev split, with PR 2.10's constant, for group B. `--test-split` outside the repository, against `git show S:docs/v2/freeze/test-split.sha256`. A mismatch stops the run.
- **The machine.** The store must have been filled on dm-devbox's machine class (CLAUDE.md), and the run happens there. Every row's byte check would catch a mismatch.
- **Output:** `/home/ubuntu/research/records/sciagent/p4-search-misses-F2/`, outside the repository, because it carries test-split readings; `s3://delaymargin-results-482913/sciagent/p4/search-misses-F2/` at the end. Record formats as PR 2.10, plus `reads.json`.

**Rehearsal, before `R2`.** The script runs with `--rehearsal` on a small store made at that head: three dev truths, two seeds, baseline systems at reduced budget and scripted sessions, no LLM call. The mode changes exactly these: the commit-chain and split guards do not run, the roots are 20261107 and 20261108, no control runs on a frozen root, the hunt is 5 cases per sub-stratum, a planted shift is added to `corrected` to show a read flip, output goes under `.cache/drafts/`, and the label is prefixed `REHEARSAL`. It refuses `--test-split`, the records directory, and any store whose fingerprint commit is `T`, so it cannot run on the real thing. It never reads the pilot store. The bytes rehearsed are the bytes of `R2`; a fix means rehearsing again. Exploratory, may not be cited as a P4 result, ceiling 8 CPU hours and $0, LEDGER row.

## 3. Primary analyses

| H | Statistic | Threshold | Falsified when |
|---|---|---|---|
| H1 | Group A caught, of 6; group B caught; group D caught | At least 5 of 6, yes, yes | Any part below that |
| H2 | M = rows carrying a reading miss, over the coordinate rows (also as distinct fits) | 0 | M ≥ 1 |
| H3 | max over miss rows of \|Δ\|, a move to or from `-inf` counting as above | 0.01 nats per event | Above 0.01 |
| H4 | Reads changed, section 2.7 | 0 | Any |

## 4. Secondary and exploratory analyses

- **S1 to S4** as PR 4. S2's second table adds its rows to `corrected`, and `read_contrasts` runs on it as `fixed2`. If any read differs from `fixed`, the label line says so in those words, whatever the label. S4's "§7.1 statistic" is replaced by each read's stored and corrected point and interval. S2's `fixed2` is written to `reads.json` with the rest.
- **S5, the unsubstituted LLM caveat, sized.** For each read, the share of its rows that are substituted LLM rows. Counts only.
- **Exploratory**, labelled, as PR 4. No difference between systems in miss counts is claimed.

## 5. Decision rules

**This section alone sets the label.** One label to `decision.json`, by the first rule that applies. Exit codes as PR 5.

1. `BYTE_FAIL` (3): any byte check, a split hash, the empty-`corrected` equality, an exception from `read_contrasts`, group B's bit check or the store manifest failed, in any stage. A start-up guard or a missing unit refuses the start and gives no label.
2. `CONTRASTS_CHANGE` (2): any read changed, on whatever has been found. It does not depend on H1, and it outranks every shortfall. If tier 1 did not finish, `decision.json` adds `partial: true`.
3. `INCOMPLETE` (5): no read changed and tier 1 fell short, as PR 5.
4. `CONTROL_FAIL` (4): no read changed and H1 is falsified.
5. `CONTROL_FAIL` (4) too if no read is reportable on both tables, with the reason given.
6. `CONTRASTS_SURVIVE_FOUND_MISSES` (0): no read changed, H1 stands, and at least one read is reportable on both tables.

Beside the label as PR 5, with the per-read line in place of the per-gate line. A stop after tier 1 finished does not alter the label. The label is computed once, at the terminal step.

| Label | Next |
|---|---|
| `CONTRASTS_SURVIVE_FOUND_MISSES` | P4's report is generated from the stored table, and carries section 2.7's wording and the per-read line |
| `CONTRASTS_CHANGE` | Both reads are reported, the stored numbers stay the record, and the owner decides what P4 can claim. With `partial: true` nothing is applied until the owner has decided |
| `CONTROL_FAIL` | The stored contrasts are not put forward as checked. Exact class reported. A frozen amendment with a stronger reference, or the owner's own decision to go on with the gap stated |
| `INCOMPLETE` | Same as `CONTROL_FAIL`. Resume rules as PR 5 |
| `BYTE_FAIL` | Same, and it is an instrument fault: root cause first, by amendment, before any reading |

Under every label P4's stored contrasts remain what was computed, and what P4 can claim is the owner's decision.

## 6. Exclusions and failure handling

As PR 6, with these classes on P4's rows, and with section 2.7's pair rule in place of PR 6's "handled by `derive` and `verdict`", since `verdict` is not on this path. No row is excluded for its outcome. A row `read_contrasts` drops under §6.4's rule is still classified and checked, because `contrast_rows` takes every truth before any drop.

## 7. Sample size

A census, fixed by the store. P4-PROPOSAL §4 gives 1,520 scored sessions and 740 baseline units, 2,260 rows. Under decision 12, the 80 AG-S-boed sessions and the 240 policy rows go, so 1,320 and 620, 1,940 rows. Tier 1 by that table, if `contrast_rows` returns what SPEC §6.2 and §6.4 name: AG-c-S and AG-o-S in three conditions 480, and B-sym, B-sparse, ORACLE, B-lib, planted-hint and B-rand at 80 each, and B-sym@10F 40: 1,000 rows. The rule is the function's output, not this count. Coverage statements as PR 7, and no recall bound is quoted.

**Power.** This check has none to speak of on a contrast: the reads pair over 40 truths, C4 over about 20, and the label compares two intervals, each from one fixed resampling. A read near zero can flip on a small move. That is why both intervals are printed.

## 8. Cost ceiling and wall clock

**Ceiling: 187 CPU hours over all units of this freeze, $0.** No LLM call, no instance beyond dm-devbox, S3 a few MB. Dispatch stops at **172 CPU hours**, and at most 2 jobs in flight at 3 CPU hours each take the run to 178, enforced as PR 8. The rehearsal adds 8, and polling, the per-job margin and a hand `--finalise` under 1. It costs machine time, and no money.

**Arithmetic.** The pilot check used 25.41 CPU hours on 670 rows, 0.0379 per row, so 2,260 rows is about 86 CPU hours, 1,940 about 74, and tier 1's 1,000 rows about 38. The stop is twice the larger figure, because P4's mix differs from the pilot's (62 of 240 LLM rows were on the coordinate path there, 205 of 430 baseline rows) and controls that do not grow are inside the 0.0379. A guide, not a bound: the pilot's own estimate was 55 to 106 and it used 25.41. The stop is enforced and not assumed, and at the stop the label follows section 5. The red team's own projection from the pilot's stage costs is about 46 to 97 CPU hours. If §6.1's plan has more than 2,260 rows, this ceiling needs an amendment before the freeze.

**Machine and wall clock.** As PR 8: 2 workers, `nice` 10, launch at `MemAvailable` ≥ 8 GiB. At 0.81 of a core per worker, 172 hours is about 106 wall hours and 86 hours about 53. `RuntimeMaxSec` 120 hours. The check adds 2 to 5 days after the campaign, before anything is read.

**Order, fixed and outcome-neutral.** Controls A, D, C, B; tier 1 with group E; S2; tier 2. Inside a stage by `sha256(f"{truth}|{seed}|{text}")`.

## 9. Early-stop conditions

As PR 9, except that the equality check is no start-up check here: it runs at the terminal step (section 2.8), and it stops nothing early. There is no stop on outcome.

## 10. Reported whatever the outcome

As PR 10, in one note under `docs/v2/`, red-teamed, with the stored and corrected reads from `reads.json` in place of the verdict lines, S5, group E's rate, and section 2.6's third point in words. **Change from PR 10:** per-miss rows name test-split truths and readings, and AUTOPILOT forbids test-split records in the repository, so that table stays in the records directory and on S3, and the note gives aggregates and the path. It sits beside the pilot's note and replaces nothing.

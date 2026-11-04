# Pre-registration: search misses on the pilot's own units (fit/5, P3)

**Status: DRAFT, not frozen.** Written in lane session 27 under INBOX 2026-11-04 08:30 UTC (control room, e7c6431). Freeze commit: `F` (set at the freeze; every `F` below means that commit). One red-team round comes before the freeze.

**What has been touched so far.** No search, refit or held-out evaluation has been run on any pilot unit. The census below read structures, statuses, grid sizes and recorded timing, and printed aggregates only. No score, no `oracle_*` field and no per-unit line was printed. Feasibility was measured on T2 cases and on synthetic logs, neither of which is a pilot unit.

**Sources of numbers in this draft** (dm-devbox, 2026-11-04; uncommitted, under `.cache/drafts/prereg-s27/`): `count.py`, `count2.py`, `count3.py`, `count4.py` (census: `OPENBLAS_NUM_THREADS=1 nice -n 10 .venv/bin/python .cache/drafts/prereg-s27/count.py`, and so on), `t2_ref.py` (T2 cases: `.venv/bin/python .cache/drafts/prereg-s27/t2_ref.py <which> <link> <seed> <horizon> <exhaustive> <starts>`), `t4_time.py` (identity timing, synthetic log).

## 1. Question and falsifiable hypotheses

**Question.** fit/5's coordinate ψ search can stop below the best point of its own grid. How many of the pilot's scored units carry such a fit, how much does the better fit move the held-out reading, and does the computed GO stand when every better fit found is substituted at once?

- **H1 (instrument).** The reference search catches every known miss. Falsified if any control of section 2.9 fails.
- **H2 (count).** No scored unit's reading carries a miss the reference finds. Falsified if M ≥ 1 (section 3).
- **H3 (size).** No miss found moves its unit's held-out reading by more than 0.01 nats per event, in either direction. Falsified if any does.
- **H4 (verdict).** `analysis.verdict` over the corrected readings is still `go`. Falsified if the outcome is anything else.

H2 and H3 can be falsified but never confirmed, because the reference search is incomplete on most large grids (section 2.2). No prediction is made for H2, H3 or H4.

## 2. Design

### 2.1 Which units

The population is every cell `analysis.load_rows` reads, `sessions="stored"`, from `.cache/pilot-merged`: 670 rows, 430 baseline and 240 LLM, with 0 missing, 0 failed and 0 void (`count.py`). Every row is put in exactly one class, by its stored structure and the ψ grid size G, where G is `_Slots.size`, the product of its slots' grid lengths.

| Class | Rows | Why |
|---|---:|---|
| Coordinate path, G > 512 | 267 (205 baseline, 62 LLM) | The rows the check runs on. They are 255 distinct fits |
| Exhaustive, G ≤ 512 | 318 (143 baseline, 175 LLM) | Checked by construction, below |
| No ψ search: B-np | 60 | Wiener-Hopf, no grammar fit |
| No ψ search: B-lib, out-of-grammar member won | 22 | The library model's own fitter, not fit/5's ψ search. Its optimiser is outside this question |
| No submission | 3 (AG-o-S/named) | No fit exists |

Coordinate rows by system: ORACLE 42, planted-hint 35, B-rand 44, B-sym 27, B-sym@10F 6, B-sparse 51, B-lib 0, AG-c-S/anon 18, AG-c-S/named 13, AG-o-S/anon 18, AG-o-S/named 13.

**Decision: all 670 are classified and none is sampled.** Reason: the instruction is every pilot unit, and the honest cost fits (section 8). A fit is identified by (truth, seed, stored structure text), so two rows with the same text on the same data share one search. Text, not `structure_hash`, because feature order changes fit/5's search path. In this store `parse(text)` equals its canonical form on all 585 rows with a structure, and the truths are canonical (20 of 20), so the text reproduces the form that was fitted (`count3.py`). Rows that share a fit share R1 and R2, but each row is compared with its own stored fit: ORACLE's may be the truth-ψ fit, and a sibling row's is the search fit.

**Exhaustive units cannot miss.** From `src/sciagent/glm/fit.py` (unchanged since 900e66a): `_search` (lines 851-857) takes the branch `profile.slots.size <= profile.config.psi_full_grid_max` and passes the whole product grid to `_Profile.best` (lines 723-731), which evaluates every point and returns the highest score, ties to the lowest index tuple. `psi_full_grid_max` is 512 in every pilot cell: `FitConfig()` is the default, and the script checks the `full=512` field in each cell's key. So for G ≤ 512 the chosen ψ is the arg max over the full grid.

One gap: `score` is the inner solve's objective, so an uncertified solve could understate a point. `fit` then reports `certified = False`, because that flag covers every evaluated point. So a row counts as checked by construction only if its `reading["certified"]` is 1. Rows at 0 are counted apart as "exhaustive, uncertified" and are not called checked. That count is read after the freeze.

**Fits the report reads that are not covered.** The efficiency curves carry other fits: 5,164 charged LLM fits (2,117 distinct on exhaustive grids and 922 on coordinate grids, `count2.py`), and up to 2,400 each for B-rand, B-sym and planted-hint and 4,000 for B-sym@10F. They are left out. `analysis.verdict` reads `gap`, `gap_closed`, `exact` and the ORACLE flags, and no curve, and the cost would be many times the whole check. PILOT.md's curve tables therefore stay unchecked, and the report says so.

### 2.2 The reference search

**Fixed and deterministic.** For a fit with structure s on its observational log:

- **R0, the stored fit.** `fit(s, obs, channels, config=FitConfig())`, the public call the pilot made. For ORACLE, `Oracle(...).run(data)`, so the kept fit is the stored one (search, or truth-ψ when that won). The stored cells keep no ψ or θ, so this rerun is the only way to get them.
- **R1, multi-start.** K = 32 starts drawn uniformly from the grid, each followed by fit/5's own `_ascend` with fit/5's own blocks, sharing one `_Profile` so a point is solved once. At most 8,000 new inner solves per fit: the solve cap is deterministic, and a fit that reaches it is flagged `capped`.
- **R2, exhaustive.** If G ≤ 14,400, every remaining point of the product grid. Otherwise nothing.

**Seeding.** `np.random.default_rng(np.random.SeedSequence([20261104, h]))`, with `h` the first 16 hex digits of `sha256(f"{truth}|{seed}|{text}")` as an integer. No Python `hash`, no wall time, no dependence on worker count or order.

**The chosen point** is the highest-scoring **certified** point of R0 to R2, ties to the lowest index tuple. It is then refitted by the public `fit(s, obs, channels, config=FitConfig(), psi=point)`, which by the module docstring and `test_fixed_psi_fit_is_the_search_fit_at_that_point` gives the search's θ at that point. A higher but uncertified point is not used (SPEC §2.2, and the rule ORACLE's truth-ψ check already follows); it is counted as `uncertified_better`.

**Blind.** The reference takes no truth ψ, no generating ψ and no coefficients. Its function has no such parameter, and a test asserts that. ORACLE's R0 can contain the truth-ψ point because that is the stored fit; R1 and R2 cannot see it.

**Why this search.** T2 case (0, softplus, 103648458), G = 14,400: fit/5 stopped at -247.1402 after 770 points, and the first random start already reached the generating point's -245.8153. T2 case (2, exp, 1499614403), G = 24,000: fit/5 stopped at -284.6127 after 402 points; the first start reached -275.0416, which beats the generating point (-275.6120) by a further 0.5704 nats, so T2's 9.0007 was itself a lower bound on that miss (9.5711). Reference rather than exhaustive alone, because the coordinate grids reach 2.1 × 10¹¹ points and the median is 21,600 (`count.py`). 115 of the 255 fits have G ≤ 14,400 and sum to 435,774 points; G ≤ 100,000 would be 168 fits and 2,677,206 points, too many for what it adds.

**What "no miss found" means.** Two different statements, always reported apart.

- *R2 fits (G ≤ 14,400):* every point of the grid was evaluated, so no certified grid point beats the stored fit beyond the tolerance. This is exact about the certified points of the grid and silent on uncertified ones, whose number is reported per fit. It matters on the identity link: on a misspecified synthetic log 14 of 164 random points were uncertified (`t5_time.py`).
- *R1-only fits (G > 14,400, 140 of 255):* the stored fit was not beaten beyond tolerance by the best of 32 seeded ascents. It is not a proof of anything. It shows only that this search found nothing, and a miss may remain.

So M is a lower bound on the true number of misses and every statement about reach is "at least". Control coverage (section 7) says how much that lower bound can be trusted.

### 2.3 What counts as a miss

A row **carries a miss** when the chosen point's training log L exceeds the row's stored fit by more than

`tol = max(3e-7, gap_stored + gap_reference)` nats

where each gap is the absolute duality gap of that point's inner solve (`Solution.gap`). "Stored fit" means the fit behind the row's reading, so for ORACLE it is the kept fit.

Reason. 3e-7 is T2's tolerance (`1e-9 * 300` in the test), kept as the floor so that one rule covers T2 and the pilot. A certified fit may have a gap up to 1e-8 · |log L|, about 3e-5 at pilot scale, so a fixed 3e-7 alone could count solver noise. The certificate gives the right scale: `L*(ψ) ≤ L(ψ) + gap`, so a difference above the sum of the two gaps is real. The gap is usually far smaller (1.5e-11 at a Newton solve, `t4_time.py`), and then the floor decides. The tolerance decides only whether a better fit exists, not whether it matters. §7.1's +0.01 per event is not the tolerance: it is a held-out quantity, 4.53 nats on 1,902 events is only 0.0024 per event, and an in-sample gap cannot be read as a held-out shift (LOG 2026-11-04, withdrawn stress test). So size is measured on held-out data (section 2.4) and 0.01 is H3's threshold there. A better point inside the tolerance is a `near_tie`: counted, not substituted.

**ORACLE has two counts.** A *reading miss* is measured against the kept fit, and it is what M counts and what is substituted. A *search miss* is measured against `oracle_search_ll` alone. ORACLE dev-015 s3 is a search miss rescued by truth ψ. It is a reading miss only if the reference beats truth ψ too.

### 2.4 Held-out size

For a row with a miss: `Δ = corrected per_event - stored per_event`, signed. Corrected means the same `held_out_dataset(truth.spec, truth.id, seed)`, the same `GLMModel(...).held_out_per_event([held])`, with only the fit changed.

**Byte check, in the script, for every coordinate row.** The R0 fit, scored by that same code, must give `per_event.hex()` equal to the cell's `reading["per_event"]`. For ORACLE rows, `oracle_search_ll`, `oracle_truth_psi_ll`, `oracle_psi_source` and `oracle_underfit` must match too. A mismatch stops the run (section 9). Since the cells keep no ψ, this is also the only evidence that R0 is the stored fit.

The training gain and Δ are reported side by side and never converted one to the other. A better fit can score worse on held-out data, so Δ may be negative, and it may be ±inf (an identity-link fit that gives a held-out event zero intensity). That is kept as a result.

### 2.5 Selection effects

Substitution changes the fit of the structure that was submitted. It does not rerun the choice of that structure. Decided per system:

| System | Step that used fit/5 | Handling |
|---|---|---|
| ORACLE | None (one structure) | Fully covered by substitution |
| B-lib | BIC over 5 members | **Cannot move.** The 3 grammar members have G = 1, 6 and 5, all exhaustive, and the other 2 use their own fitters (`count2.py`) |
| B-np | None | Not applicable |
| B-sparse | Path: fixed ψ, no ψ search. Final fit: covered. `_choose_link`: fit/5 on the training window for each of 3 links | Final fit is primary. The link choice is secondary S2, which reruns it with reference fits |
| B-rand | BIC over 40 proposals, drawn with no fit result | **Not covered.** It could be redone in principle, but B-rand's recorded CPU was 36.84 hours, and the reference is about 7 times fit/5's points, so about 260 CPU hours (section 8) |
| B-sym, B-sym@10F, planted-hint | BIC selection and the GP's fitness at every generation | **Not covered, and cannot be by substitution.** A better fit changes the population and every later proposal, so it is a different run |
| LLM arms | The agent read fit/5's numbers during its session | **Not covered** (section 2.6) |

Stated plainly: for B-rand, B-sym, B-sym@10F, planted-hint and the LLM arms, the corrected reading answers "given the structure this system submitted, what does a better fit of it score?". It does not answer "what would it have submitted?". The direction of the omitted effect is unknown for scores. For recovery, `exact` depends on the structure only, so **substitution can never move a recovery share**, and the too-easy clause on B-rand recovery cannot react to this check at all. B-lib's recovery cannot move by any route, as the table shows.

### 2.6 LLM units

Nothing differs in the fit: `run_score_job` calls `fit(structure, data.observational, channels, config=fit_config)` on the canonical submitted structure, the same call as a baseline, and the framework writes every number. The rule is the same and the rows are in the census. Four things differ around it.

1. LLM rows do not enter `analysis.verdict` (`rows = [r for r in table.rows if not r.llm]`), so they cannot change the outcome. They change the LLM tables only.
2. They have no truth-ψ rescue.
3. Sessions cannot be replayed under a better fit: the agent saw fit/5's numbers, some on experimental data, and that may have shaped its submission. This is not covered.
4. No control covers an LLM arm (SPEC §6.4), so misses on LLM rows are counted and sized, and no comparison is made between arms or against baselines.

### 2.7 What "the computed GO survives" means

```python
corrected = {row key: per_event}          # rows with a reading miss only
rows = [replace(r, per_event=corrected.get(key(r), r.per_event)) for r in table.rows]
table2 = replace(table, rows=tuple(analysis.derive(rows)))
v = analysis.verdict(table2, np.random.default_rng(20261002), resamples=10_000)
```

Every system's better fit is substituted in one pass, ORACLE included, so `derive` recomputes gaps, gaps closed and T4 flags against the corrected references. B-lib has no coordinate row, so its reference never changes. The generator seed and resample count are those of `pilot_report.py` and the PILOT.md command. The same code with an empty `corrected` must return a `Verdict` equal to `analysis.verdict` on the stored table, with outcome `go`; otherwise the run stops. `oracle_underfit` is recomputed for a substituted ORACLE row with `oracle_underfit(L_reference, oracle_true_params_ll)`. A better fit can only clear a flag, and the script asserts that none appears.

**The GO survives** if and only if `v.outcome == "go"` and every tier 1 fit of section 8 was checked. A survival statement is always worded as "survives the misses found, under substitution". It is never "there are no misses".

**If the outcome changes,** the report names the clause that moved, the rows behind it, and the stored and corrected value of each gate. It says that the stored verdict stands in the record, that this is an added check beside it, and which consequence P4-PROPOSAL §2.4 would attach to that clause (a control failure means fix the instrument first, a difficulty flip means a re-pilot), without applying it. The owner decides.

**What stays unknown either way:** misses R1 did not find on the 140 fits above 14,400 points; everything in the not-covered rows of section 2.5; the curves; and sampling noise, since each reading is one held-out log per (truth, seed).

### 2.8 The store and the §7.1 rule are not touched

`.cache/pilot-merged` is opened with `ResultStore(root, read_only=True)` only. The script takes a sha256 manifest of the store's files before and after, and a difference is a failure. All output goes outside the store. No cell is written, `PILOT.md`'s generated sections are not regenerated from corrected numbers, and §7.1 and P4-PROPOSAL §2.4 are not reinterpreted. This is a check beside the computed verdict. The go/no-go remains the owner's.

### 2.9 Positive control (SPEC §6.4), first

The reference runs on these before any other pilot unit. T2 cases come first, then dev-015 s3.

| # | Case | Known | Role |
|---|---|---|---|
| 1 | T2 (0, softplus, 103648458) | 1.3249 nats, G = 14,400 | Used to design R1 |
| 2 | T2 (2, exp, 1499614403) | 9.0007 nats, G = 24,000 | Used to design R1 |
| 3-6 | T2 (0, softplus, 1074), (0, softplus, 3273957339), (0, softplus, 358866363), (0, softplus, 2006407976) | 0.00397, 0.2719, 0.5943, 2.0819 nats | Validation: K and the cap were written down at 08:18Z, before these ran |
| 7 | ORACLE dev-015 s3 | stored search -781.9771, truth ψ -777.4489, G = 20,736 | The one pilot-scale case. Never run before the freeze |
| 8+ | Every other ORACLE unit whose stored `oracle_search_ll` is below `oracle_truth_psi_ll` by more than 3e-7 | Unknown until the freeze | Read after the freeze, added automatically |

**Pass, for each case:** run blind with R1 only (no R2, whatever G is), the chosen training log L is at least the known better value minus the tolerance, and it exceeds fit/5's value by more than the tolerance. The known better value is fit/5's plus T2's gap for T2 cases, and `oracle_truth_psi_ll` for ORACLE units. R1 alone, because R1 is what the 140 large fits rely on, and an R2 pass would say nothing about them.

**All must pass.** If one fails, no other pilot unit is run. Any misses already found stand as lower bounds, "no miss found" has no meaning, and the outcome is `CONTROL_FAIL`, a stop point for the owner. A stronger reference needs an amendment to this document, frozen before it is run. The search is not tuned and rerun on the failing case under this freeze.

Case 7 passing also needs its R0 byte check (section 2.4), so the first pilot unit run proves both halves. Cases 1 and 2 were looked at to choose R1, so they show only that it works on what it was built for. Cases 3 onward are the independent part.

**Already measured on T2 (not pilot units, `t2_ref.py`).** All six T2 cases were caught at the first of the 32 starts, so R1 alone passes them today. The script reruns them at the freeze. On two cases R1 went past the generating point: 9.5711 against 9.0007 on case 2, and 2.6685 against 2.0819 on (0, softplus, 2006407976). A catch on the first start also says that these misses have wide basins, which need not hold on larger grids.

**What the controls do not cover.** Five of the six T2 cases are one stratum (BIG[0], softplus). There is no known miss on the identity link, and T2 has no identity case, so H1 says nothing about the 73 of 255 coordinate fits on that link. Only S1 and the R2 fits speak to them.

### 2.10 Code

- **Tests first** (CLAUDE.md, SPEC §6.3): `tests/campaign/test_pilot_search_misses.py` is written and seen to fail before the script. No test touches a pilot unit. They cover at least: the exhaustive class against brute-force `fit(psi=...)` and the 512/513 boundary; the byte check (equal passes, one ulp off fails); the miss rule (near tie, uncertified better point, tolerance from gaps); reference blind by signature; R1 deterministic and independent of worker count and order; seeds from sha256; empty `corrected` returns an equal `Verdict`; a planted shift on a toy table flips the outcome where it should; append-only resume; the ceiling stop; the store manifest check; and a refusal to read a pilot store without the frozen hash. T2 controls 1 and 2 are slow-tier tests.
- **Script:** `scripts/pilot_search_misses.py`, importing `_Profile`, `_ascend`, `_Budget` and `_search_blocks` from `sciagent.glm.fit`. No change under `src/`, so `FIT_VERSION` and every address stay as they are.
- **Fit code.** fit/5 as at 900e66a. `git diff 900e66a v2 -- src/sciagent/glm` is empty (0 lines, run this session). So is the wider set the numbers depend on: between 900e66a and v2, `src/` differs only in `campaign/audit.py` and `campaign/merge.py`, `scripts/` only in `pilot_report.py` and `store_merge.py`, and `pyproject.toml`, `uv.lock` and `data/` not at all.
- **Where it runs:** a clean detached worktree `.claude/worktrees/misses` at `F`. At start the script refuses unless `git diff --quiet 900e66a HEAD -- src/sciagent/glm src/sciagent/scoring src/sciagent/systems src/sciagent/campaign/analysis.py src/sciagent/campaign/execute.py src/sciagent/campaign/plan.py src/environments data uv.lock` is clean, the tree is clean, `OPENBLAS_NUM_THREADS`, `OMP_NUM_THREADS` and `MKL_NUM_THREADS` are 1, and `--prereg-sha` equals the sha256 of this file at `F`. The last condition is what enforces "rule first" in code.
- **Output:** `/home/ubuntu/research/records/sciagent/search-misses-F/` (outside the repo tree), copied at the end to `s3://delaymargin-results-482913/sciagent/p3-linux/search-misses-F/`. Records (`units.jsonl`, floats as `float.hex`, sorted, byte-identical on a rerun), timing kept apart in `timing.jsonl`, and `decision.json`.

## 3. Primary analyses

One per hypothesis. There is no significance test: this is a count over a census, and the thresholds are fixed here.

| H | Statistic | Threshold | Falsified when |
|---|---|---|---|
| H1 | Controls passed, of those listed | All | Any fails |
| H2 | M = rows carrying a reading miss, over the 267 coordinate rows (also given as distinct fits) | 0 | M ≥ 1 |
| H3 | max over miss rows of \|Δ\|, non-finite counting as above | 0.01 nats per event | Above 0.01 |
| H4 | `v.outcome` from section 2.7 | `"go"` | Anything else |

0.01 is `gonogo.BSYM_TOLERANCE` and `audit.GAP_THRESHOLD`, the scale §7.1's reading works at. H3 and H4 are vacuously unfalsified at M = 0, and the report says so.

## 4. Secondary and exploratory analyses

**Secondary** (fixed now, reported, cannot change a primary label):

- **S1, R1's recall at pilot scale.** On fits where R2 ran, the share of misses R2 found that R1 alone had found. This is the only pilot-scale measure of how much R1 misses, and it reads R1's result as recorded before R2 ran.
- **S2, B-sparse's link.** Rerun `_choose_link` on the training window with R0 and then with the reference (R0 and R1) for all three links on B-sparse's coordinate rows, using `split_dataset(obs, TRAIN_FRACTION)`. R0's choice must equal the cell's `sparse.link`. Where the reference's choice differs, fit that link on the full log with the reference and score it. A second verdict, `v2`, adds those rows to `corrected`, and is labelled as such.
- **S3, shape of the found misses.** Training gain in nats and per event, Δ, sign of Δ, near ties, `uncertified_better`, `capped` and search misses on ORACLE, as counts and quantiles.
- **S4, consequences on miss rows.** Change in `gap` and `gap_closed`, T4 flags that change, each §7.1 statistic stored and corrected beside its limit, and the count of corrected gaps above +0.01 (the `hunt_gap` gate) on the corrected table. `similarity` is recomputed by `score_submission` on miss rows only.

**Exploratory** (labelled so, no decision reads them): which shapes of structure miss (link, number of features, G, sharing of `period` slots); whether misses fall on the same (truth, seed, structure) across systems. Per-system tallies are shown as counts only. No difference between systems is claimed, and no control supports one.

## 5. Decision rules

`scripts/pilot_search_misses.py` writes one label to `decision.json`, by the first rule that applies. Exit code in brackets.

1. `BYTE_FAIL` (3): any byte check, the baseline `Verdict` equality or the store manifest failed.
2. `CONTROL_FAIL` (4): any control failed.
3. `INCOMPLETE` (5): any tier 1 fit lacks R0 and R1, or lacks R2 where due, or is `reference_failed`. `v` is still computed on what was found and is labelled partial. No survival statement is made.
4. `GO_CHANGES` (2): `v.outcome != "go"`.
5. `GO_SURVIVES` (0): `v.outcome == "go"`.

Beside the label: H2 and H3 as `falsified` true or false, M, max |Δ|, unchecked and capped counts, and the coverage line of section 10. A tier 2 or S2 shortfall does not alter the label, because neither is an input of `v`; it is reported as a shortfall.

## 6. Exclusions and failure handling

Fixed in advance. No row is excluded for its outcome.

- **By class:** B-np (60), B-lib out-of-grammar (22) and no-submission (3) are outside the check, section 2.1. A void or failed row, of which there are 0 today, would be handled as `load_rows` handles it, and counted.
- **Exhaustive, `certified` = 0:** counted apart, not called checked.
- **`SciAgentError`, `ValueError` or `ArithmeticError` in R1 or R2** (`UNIT_FAILURES`, as in `execute.py`): the fit is `reference_failed`, its stored reading stays in the corrected table, and it counts as unchecked. Any other exception is a bug and stops the run.
- **Stored fit uncertified at R0:** recorded. It is compared and substituted like any other.
- **Worker killed** (out of memory, or the per-job limit): the fit is retried once on resume, and a second loss is `reference_failed`.
- **Per-job CPU limit:** 3 CPU hours, by `RLIMIT_CPU`. A job that reaches it is `reference_failed`, and is reported under that reason.
- **`capped`:** counted as checked by R1 with the cap stated, and treated as R1-only even if G ≤ 14,400 until R2 has run.
- **Non-finite corrected reading:** kept. `-inf` is handled by `derive` and `verdict` as written, including ORACLE at `-inf` failing control 1. `nan` is `reference_failed`.

## 7. Sample size

This is a census, so there is no sampling error in M, and n is fixed by the store: 267 rows, 255 distinct fits, 196 of them in tier 1 and 59 only on LLM rows. A sample would not do, because the verdict's gates turn on few units. Too-easy needs 14 more of the 30 out-of-dictionary B-sparse units to close (PILOT.md), and 26 of those 30 are on the coordinate path (`count3.py`). C3 reads at most 10 B-sym@10F units, 6 of them coordinate, so one unit can move its median.

What the numbers can and cannot support:

- **Controls.** With c independent controls all passed, the one-sided 95% lower bound on R1's recall for this kind of miss is 0.05^(1/c) (Clopper-Pearson): 0.55 at c = 5, cases 3 to 7. That is weak, it treats the cases as a random sample of misses, which they are not, and it is why S1 exists.
- **M = 0 on 255 fits** would give a 95% upper bound of 1.2% on the rate of misses this reference detects, if the fits were exchangeable. They are not (many share a truth and seed), so that figure is not reported as a rate.
- **Exhaustive coverage.** 115 of 255 fits (45%) are decided exactly, and 65 of the 196 in tier 1.

## 8. Cost ceiling and wall clock

**Ceiling: 72 CPU hours, $0.** No LLM call, no usage credit, no instance beyond the always-on dm-devbox; the S3 copy is a few MB. The ceiling holds by construction: the run is one user unit with `CPUQuota=200%` and `RuntimeMaxSec=129600`, so 2 cores × 36 hours = 72 CPU hours is a true upper bound whatever any fit does (the `cpu` and `memory` controllers are delegated to the user manager on dm-devbox, checked this session). The script also sums its workers' CPU and stops cleanly at 70 CPU hours, closing the records. At either stop the unit ends, records stay, and the label follows section 5.

```sh
systemd-run --user --unit=sci-search-misses --nice=10 -p CPUQuota=200% -p RuntimeMaxSec=129600 -p MemoryMax=5G \
  --working-directory=<worktree> uv run python scripts/pilot_search_misses.py --workers 2 --prereg-sha <sha256> ...
```

**Machine.** At most 2 pool workers, `nice` 10, one worker per fit (`FitConfig.workers` stays 1), BLAS threads 1. A job starts only when MemAvailable ≥ 8 GiB: AUTOPILOT's 6 GB floor plus the about 2 GB a pilot worker peaks at (QUEUE). Below that it waits. MemAvailable was 5.2 to 7.1 GB this morning with the replay units up, so the run starts when at least one has ended. At their rates so far (AG-c 96 and AG-o 72 of 120 in 6.25 hours), about 1.6 and 4.2 hours from 08:22Z. Those are extrapolations.

**Order, fixed and outcome-neutral.** Controls; then tier 1 (the 196 fits on baseline rows, the only rows `v` reads): R0 and R1 on all, then R2; then tier 2 (the 59 fits only on LLM rows): the same; then S2. Inside a stage, by `sha256(f"{truth}|{seed}|{text}")`. So every fit gets R1 before any fit's R2 in its tier, and the label is known at the end of tier 1 and does not wait for the rest.

**Inputs.**

| Quantity | Value | Source |
|---|---:|---|
| Fits: exp, softplus, identity | 47, 135, 73 | `count4.py` |
| Sum of one sweep over fits (points) | 5,413 + 16,607 + 6,600 = 28,620 | `count4.py` |
| Grid points of fits with G ≤ 14,400 | 58,878 + 181,020 + 195,876 = 435,774 | `count4.py` |
| New points per R1 start | 0.60 of a sweep at most in the cases measured: 4,607 points for 32 starts at sweep 240; 0.44 on (2, exp, 1499614403) | `t2_ref.py` |
| CPU per point, exp and softplus | 0.041 to 0.044 s in R1, 0.055 to 0.064 s over a whole fit/5, at 1,395 to 1,420 events. Taken as 0.09 s at 2,000 events (× 1.4) | `t2_ref.py`, horizon 2,000 |
| CPU per point, identity, well specified | 0.025 s at 1,865 events; ORACLE's identity units took 19.9 s median in all, so about 0.03 s | `t4_time.py`, `count4.py` |
| CPU per point, identity, misspecified | 0.313 s mean at 1,420 events, Clarabel on every point, so 0.44 s at 2,000 | `t5_time.py` |
| R0, CPU per fit | 55.3 s, B-rand's recorded mean over 40 fits, scoring included | `.cache/pilot-merged/timing.jsonl` |

**Expected cost.** R1 points = 32 × 0.6 × sweeps = 19.2 × 28,620 = 549,504 (exp 103,930, softplus 318,854, identity 126,720).

| Part | Arithmetic | CPU hours |
|---|---|---:|
| exp and softplus, R1 and R2 | (103,930 + 318,854 + 58,878 + 181,020) = 662,682 × 0.09 s | 16.6 |
| identity, R1 and R2 | (126,720 + 195,876) = 322,596 × 0.03 to 0.44 s | 2.7 to 39.4 |
| R0 | 255 × 55.3 s | 3.9 |
| **Primary, all 255 fits** | | **23.2 to 59.9** |
| of which tier 1 (label known) | 550,860 × 0.09 s, 242,714 × 0.03 to 0.44 s, 196 × 55.3 s | 18.8 to 46.4 |
| S2 | 51 rows × (3 × 55.3 s + 2,150 points × (0.09 + 0.09 + 0.03 to 0.44 s)) | 8.7 to 21.2 |
| **All** | | **32 to 81** |

The width is the identity link: whether the 73 submitted identity fits behave like ORACLE's or like a badly misspecified log is not known, and it could not be measured without running on pilot units.

**Worst case with nothing to stop it.** Every fit reaching its cap: (182 × 8,000 + 239,898) × 0.09 s + (73 × 8,000 + 195,876) × 0.44 s + 3.9 h = 42.4 + 95.3 + 3.9 = 141.6 CPU hours, and S2 at its cap adds about 70. No measured case came near the cap (4,607 of 8,000), but this is why the ceiling is enforced and not assumed. Expected primary cost fits under 72, and tier 1 fits even at the misspecified rate, so the honest cost for every unit is affordable and **the scope is not reduced**. S2 could be cut short, and the report would say by how much.

**Wall clock.** The one recent reference, the pinned T2 run, got 2,133 CPU seconds in about 2,640 s on this machine under load, so 0.81 of a core. With 2 workers that is 1.62 cores: tier 1 in 12 to 29 hours, all primary in 14 to 37 hours (the 36-hour limit could bind first), after the memory gate opens. Controls take under half an hour.

## 9. Early-stop conditions

The run stops early only for these. **There is no stop on outcome:** a miss, a large Δ or a changed verdict never ends the run.

1. A control fails: stop, `CONTROL_FAIL`.
2. A byte check, the baseline `Verdict` equality, the start-up guards of section 2.10 or the store manifest fails: stop at once, `BYTE_FAIL`.
3. The CPU ceiling or `RuntimeMaxSec` is reached: stop, closing the records, section 8.
4. The owner or control room writes `STOP` in the output directory: stop after the jobs in flight. No reading is made of what the run had found to justify a stop.

MemAvailable below the floor pauses dispatch and does not stop anything.

## 10. Reported whatever the outcome

In one note under `docs/v2/`, with commands, and red-teamed before it goes out as a result:

- The label, and H1 to H4 each stated as falsified or not.
- Control results, case by case, with each training value.
- The class table of section 2.1 with final counts, including exhaustive rows at `certified` = 0.
- **Coverage line:** rows and fits checked exactly (R2 or exhaustive), checked by R1 only, unchecked, capped, and not applicable.
- M in rows and fits, per system as counts, each miss row with its training gain, Δ, stored and corrected reading and class. ORACLE's search misses apart. If there are none, the table says none.
- `analysis.verdict` on corrected readings: outcome, each gate's statistic stored and corrected, kept and dropped truths, `reasons`, `inconclusive`.
- S1 to S4 and the label `v2`, and the exploratory table marked as such.
- The not-covered list of section 2.5, and the unknowns of section 2.7, in words, whatever M is.
- CPU hours and wall time used, LEDGER row, S3 path, freeze commit, and any departure from this document with both versions, recorded in `docs/v2/LOG.md` as a stop point.

Numbers go out reviewed or labelled unreviewed. Stored readings, the stored verdict and PILOT.md's generated sections stay as they are. A corrected reading is never presented as the pilot's reading.

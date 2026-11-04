# Pre-registration: search misses on the pilot's own units (fit/5, P3)

**Status: DRAFT, not frozen.** Written in lane session 27 under INBOX 2026-11-04 08:30 UTC (control room, e7c6431). Round 1 of the red team (128f236): NOT READY, seven blocking findings (`docs/v2/redteam/2026-11-04_pilot-search-misses.md`). Round 2 (b6e98db): NOT READY, five blocking findings (`docs/v2/redteam/2026-11-04_pilot-search-misses-round2.md`). Round 3 (d117fc0, narrow): READY WITH FIXES, one blocking (`docs/v2/redteam/2026-11-04_pilot-search-misses-round3.md`). This is the session 28 revision after round 3. `F` is the commit that freezes this file. `R` is the later commit that adds the script and its test (section 2.10). Every `F` and `R` below means those commits.

**What has been touched so far.** This check has run no search, refit or held-out evaluation on a pilot unit. Earlier work did touch the pilot, and the lane knows these things:

- **dev-015 s3 is not blind.** The stored ORACLE search value there is below the stored truth-ψ value by 4.53 nats (sessions 26 and 27, from stored cells). A session 27 red-team round also compared in-sample and held-out sizes on that unit (LOG 2026-11-04: ratio 1.6), which implies a held-out evaluation of a refit search fit. So it was run before this rule, by that review, and its size was seen. Here it is a control with a known answer. It is a finding only under section 2.3, if R1 beats truth ψ too.
- **The census** read structures, statuses, grid sizes and recorded timing, and printed aggregates, plus dev-015's own structure and G (`count3.py`). No score, `oracle_*` value or `certified` value was printed. Round 1's `census.py` did the same and also loaded stored rows in memory; session 28's `count5.py` printed link, tier and sweep aggregates.
- **The withdrawn stress test** (session 27) computed how far each §7.1 gate could move, from stored readings, so the lane knows those margins (LOG 2026-11-04). Round 2's review read the same census as round 1, and committed text carrying stored values.
- **T2 and synthetic logs** carry the feasibility numbers; neither is a pilot unit. The six T2 cases have been run several times, by the draft and by the red team, so nothing below calls them independent.

**Sources of numbers in this draft** (dm-devbox, 2026-11-04; uncommitted): `.cache/drafts/prereg-s27/` `count.py` to `count4.py` (census: `OPENBLAS_NUM_THREADS=1 nice -n 10 .venv/bin/python <script>`), `t4_time.py` (identity timing, synthetic log), and `.cache/drafts/prereg-s28/count5.py` and `count6.py` (R1-only sweeps), `m.out` and `cost3.py` (points per start, capped cost), `.cache/drafts/prereg-s28/spike/` (synthetic strata, memory and CPU per point at about 2,300 events; `c.table`, `d.table`). The per-start rates are round 1's blind rerun (`/tmp/rt-s27/blind.py`, `basin.py`, copied to `.cache/drafts/prereg-s27/redteam-s27/`), as printed in the red-team report. `t2_ref.py` is **not** a source for catch rates: it put the generating point in the profile (round 1, finding 1).

## 1. Question and falsifiable hypotheses

**Question.** fit/5's coordinate ψ search can stop below the best point of its own grid. How many of the pilot's scored units carry such a fit, how much does the better fit move the held-out reading, and does the computed GO stand when every better fit found is substituted at once?

- **H1 (instrument).** R1 catches known misses at the rate fixed in section 2.9. Falsified if that section's pass rule fails.
- **H2 (count).** No scored unit's reading carries a miss the reference finds. Falsified if M ≥ 1 (section 3).
- **H3 (size).** No miss found moves its unit's held-out reading by more than 0.01 nats per event, in either direction. Falsified if any does.
- **H4 (verdict).** `analysis.verdict` over the corrected readings is still `go`. Falsified if the outcome is anything else.

H2 and H3 can be falsified but never confirmed, because the reference search is incomplete on most large grids (section 2.2). No prediction is made for H2, H3 or H4.

**What "restate the bound" means here (INBOX 08:30).** The check gives a lower bound on M and the largest |Δ| among the misses found. On fits with an exact check, that count is complete for certified grid points. On the rest it is a count of what one seeded search found, and **no bound of any kind is given on misses it did not find**. It does not restate D3 as a new worst-case figure for fit/5.

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
- **R1, multi-start.** Up to K = 48 starts drawn uniformly from the grid, each followed by fit/5's own `_ascend` with fit/5's own blocks, sharing one **fresh** `_Profile`, so a point is solved once. R1's profile starts empty and holds only points R1 itself reached: not R0's point, not fit/5's path, not ORACLE's truth ψ. At most 8,000 new inner solves per fit on exp and softplus and 3,000 on identity: the caps are deterministic, a fit that reaches one is flagged `capped`, and its number of completed starts is recorded.
- **R2, exhaustive.** If G ≤ 14,400, every remaining point of the product grid, on the same profile, after R1's result is written to the record. Otherwise nothing.

**K and the caps, against the measured rates.** Round 1's blind rates per start on the six T2 cases are 9/30, 47/96, 4/56, 4/40, 1/77 and 33/51. 48 starts give a chance of no catch of 0.029 at 4/56, 0.006 at 4/40 and 0.53 at 1/77, against 0.093, 0.034 and 0.66 at 32 starts. 229 starts would be needed for 5% at 1/77, and that is unaffordable (section 8). So R1 **is not** a search that reliably finds a miss with a basin that small, and no reading of this check may say so. **The budget is points, not starts.** The caps were set so that controls, group C and tier 1 stay under the 78-hour stop at the rates of section 8 however many sweeps a start costs, which round 1 and 2 showed cannot be read off T2. At the measured 1.1 sweeps per start, 52 of 107 exp and softplus and 22 of 24 identity tier 1 searched fits reach their cap. At a constant 1.1, completed starts would be 35, 48 and 48 at the quartiles on exp and softplus (fewest 19), and 18, 28 and 34 on identity (fewest 9). A start costs more early on, so the fewest are nearer 12 to 16 and 5 to 6. The identity cap is the tighter because identity's rate may be four times higher and it has the widest cost range. So on many fits R1 is well short of 48 starts, and the record gives the number for each fit.

**Seeding.** `np.random.default_rng(np.random.SeedSequence([20261104, h]))`, where `h` is the first 16 hex digits, as an integer, of `sha256(c)`. For a pilot fit, `c = f"{truth}|{seed}|{text}"`. For a T2 case, `c = f"t2|{which}|{link}|{seed}"`. For a hunt case (section 2.9), `c = f"hunt|{stratum}|{i}"`. No Python `hash`, no wall time, no dependence on worker count or order. For S2, `c = f"s2|{truth}|{seed}|{text}"`. This stream has not been run on anything. It runs once, at the launch, and no other stream decides any control. The script's slow tests ran on T2 cases 1 and 2 under a test root, 20261107, before the freeze, to check the code. Those runs are not read as catches, nothing was chosen from them, and they are not extended to other cases.

**The chosen point** is the highest-scoring **certified** point of R0 to R2, ties to the lowest index tuple. It is then refitted by the public `fit(s, obs, channels, config=FitConfig(), psi=point)`, which by the module docstring and `test_fixed_psi_fit_is_the_search_fit_at_that_point` gives the search's θ at that point. A higher but uncertified point is not used (SPEC §2.2, and the rule ORACLE's truth-ψ check already follows); it is counted as `uncertified_better`. R0 is in the chosen point so a reading is only ever replaced by something better. **R1's own best is recorded separately**, and every control and S1 read that value and never the chosen point.

**Blind.** The reference takes no truth ψ, no generating ψ and no coefficients. A signature check is not enough, because the leak in round 1's script passed through shared state: a test therefore asserts that after a reference run every point in the profile was reached by that run's own starts and ascents (or R2's enumeration), by replaying the seeded starts on a second empty profile and comparing the point sets. Known values for controls are computed on a separate profile, in a separate function, after the reference result is recorded.

**Why this search.** Fit/5 stops at a local optimum of coordinate moves, and T2 shows that better points can exist on grids of 14,400 and 24,000 points. Random restarts of the same ascent are the cheapest search that does not depend on where fit/5 stopped. On round 1's rerun of the draft's own stream, R1 reached the generating value or better on all six T2 cases within 32 starts, and went past it on two (gains 9.5711 against 9.0007, and 2.6685 against 2.0819). On T2 case (0, softplus, 103648458), an exhaustive run found R1's best to be the global maximum, and only 3 of 14,400 points beat fit/5, so basins are narrow, not wide. Reference rather than exhaustive alone, because the coordinate grids reach 2.1 × 10¹¹ points and the median is 21,600 (`count.py`). 115 of the 255 fits have G ≤ 14,400 and sum to 435,774 points; G ≤ 100,000 would be 168 fits and 2,677,206 points, too many for what it adds.

**Two evidence classes, always reported apart.** Every fit that is checked is in exactly one.

- **Exact.** Exhaustive by construction (G ≤ 512, `certified` = 1) or R2 complete (G ≤ 14,400). No certified grid point beats the stored fit beyond the tolerance, unless the fit is listed as a miss. This is exact about the certified points of the grid and silent on uncertified ones, whose number is reported per fit. That matters on the identity link: on a misspecified synthetic log 14 of 164 random points were uncertified (`t5_time.py`).
- **Searched, unvalidated.** R1 only (G > 14,400, 140 of 255 fits, 131 of 196 in tier 1; or any fit with R2 not finished). The stored fit was not beaten beyond tolerance by the best of up to 48 seeded ascents, within the point cap. This proves nothing. R1's catch rate is known only for the controls of section 2.9, and 118 of the 196 tier 1 fits have grids larger than any T2 or pilot control. "Searched" is what the label says for these, whatever the controls do.

So M is a lower bound on the true number of misses and every statement about reach is "at least".

### 2.3 What counts as a miss

A row **carries a miss** when the chosen point's training log L exceeds the row's stored fit by more than

`tol = max(3e-7, gap_stored + gap_reference)` nats

where each gap is the absolute duality gap of that point's inner solve (`Solution.gap`). "Stored fit" means the fit behind the row's reading, so for ORACLE it is the kept fit.

Reason. 3e-7 is T2's tolerance (`1e-9 * 300` in the test), kept as the floor so that one rule covers T2 and the pilot. A certified fit may have a gap up to 1e-8 · |log L|, about 3e-5 at pilot scale, so a fixed 3e-7 alone could count solver noise. The certificate gives the right scale: `L*(ψ) ≤ L(ψ) + gap`, so a difference above the sum of the two gaps is real. The gap is usually far smaller (1.5e-11 at a Newton solve, `t4_time.py`), and then the floor decides. The tolerance decides only whether a better fit exists, not whether it matters. It does not cover quadrature: L is exact only up to `quadrature_error` (SPEC §2.2), which differs by ψ. So both fits' `quadrature_error` is recorded on every miss, and a miss whose gain is at or below their sum is listed `within_quadrature`: it stays in M and is substituted, and the report says H2 rests on it if it is the only kind. §7.1's +0.01 per event is not the tolerance: it is a held-out quantity, 4.53 nats on 1,902 events is only 0.0024 per event, and an in-sample gap cannot be read as a held-out shift (LOG 2026-11-04, withdrawn stress test). So size is measured on held-out data (section 2.4) and 0.01 is H3's threshold there. A better point inside the tolerance is a `near_tie`: counted, not substituted.

**A stored fit that is uncertified, or whose gap is not finite.** A solve can return a finite primal with an infinite gap (`fit_solve.py:685`, `:847`), and `score` still reads the primal. This can happen on ORACLE, B-sparse and LLM rows, not on B-rand, B-sym, planted-hint or B-lib, which reject uncertified fits. For such a row `gap_stored` is dropped: `tol = max(3e-7, gap_reference)`, and the actual L values are compared. A row that exceeds it is a miss, is counted in M and substituted like any other, and is also listed in the class `stored_uncertified`. The chosen point is always certified, so `gap_reference` is finite.

**ORACLE has two counts.** A *reading miss* is measured against the kept fit, and it is what M counts and what is substituted. A *search miss* is measured against `oracle_search_ll` alone. ORACLE dev-015 s3 is a search miss rescued by truth ψ. It is a reading miss only if the reference beats truth ψ too.

### 2.4 Held-out size

For a row with a miss: `Δ = corrected per_event - stored per_event`, signed. Corrected means the same `held_out_dataset(truth.spec, truth.id, seed)`, the same `GLMModel(...).held_out_per_event([held])`, with only the fit changed.

**Byte check, in the script, for every coordinate row.** The R0 fit, scored by that same code, must give `per_event.hex()` equal to the cell's `reading["per_event"]`. For ORACLE rows, `oracle_search_ll`, `oracle_truth_psi_ll`, `oracle_psi_source` and `oracle_underfit` must match too. A mismatch stops the run (section 9). Since the cells keep no ψ, this is also the only evidence that R0 is the stored fit.

The training gain and Δ are reported side by side and never converted one to the other. A better fit can score worse on held-out data, so Δ may be negative. A stored `-inf` that stays `-inf` has Δ = 0, by definition. A move from `-inf` to finite, or from finite to `-inf`, has no finite Δ: it is kept as a result, listed apart, and counts as above 0.01 for H3.

### 2.5 Selection effects

Substitution changes the fit of the structure that was submitted. It does not rerun the choice of that structure. Decided per system:

| System | Step that used fit/5 | Handling |
|---|---|---|
| ORACLE | None (one structure) | Fully covered by substitution |
| B-lib | BIC over 5 members | **Cannot move.** The 3 grammar members have G = 1, 6 and 5, all exhaustive, and the other 2 use their own fitters (`count2.py`) |
| B-np | None | Not applicable |
| B-sparse | Path: fixed ψ, no ψ search. Final fit: covered. `_choose_link`: fit/5 on the training window for each of 3 links | Final fit is primary. The link choice is secondary S2, which reruns it with reference fits |
| B-rand | BIC over 40 proposals, drawn with no fit result | **Not covered.** It could be redone in principle, but B-rand's recorded CPU was 36.84 hours and the reference is several times fit/5's points, so hundreds of CPU hours (section 8) |
| B-sym, B-sym@10F, planted-hint | BIC selection and the GP's fitness at every generation | **Not covered, and cannot be by substitution.** A better fit changes the population and every later proposal, so it is a different run |
| LLM arms | The agent read fit/5's numbers during its session | **Not covered** (section 2.6) |

Stated plainly: for B-rand, B-sym, B-sym@10F, planted-hint and the LLM arms, the corrected reading answers "given the structure this system submitted, what does a better fit of it score?". It does not answer "what would it have submitted?". The direction of the omitted effect is unknown for scores. For recovery, `exact` depends on the structure only, so substitution moves no system's `exact` flag. A recovery share can move only through which truths are kept: a substituted ORACLE reading can change `kept` (`analysis.py:601`), and recovery is computed over kept truths (`analysis.py:685-689`). That is the only route by which the too-easy clause on recovery can react to this check, for B-rand or B-lib.

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

**Survival is a statement about found misses only.** `v.outcome == "go"` with every tier 1 fit processed and H1 standing gives the label `GO_SURVIVES_FOUND_MISSES`, and its wording is fixed:

> The computed GO survives substitution of the N misses found (N counts baseline rows, the ones the verdict reads). Of the T fits the verdict reads, E are exact over their certified grid points, U are searched and unvalidated, X are exhaustive but uncertified and were not checked, and A are outside the check (B-np, B-lib's out-of-grammar members). Nothing is said about misses on the U fits that the search did not find.

The label line also gives, per §7.1 gate (too easy, too hard, C1, C2, C3), how many of the rows that gate reads are exact, searched, exhaustive uncertified and not applicable (ORACLE counts for every gate and B-lib for too easy, as gap references; too hard reads all baseline rows), and the control state of section 2.9 for each stratum of the searched fits. A searched fit's stratum is fixed by link and G: exp or softplus with G ≤ 24,000 (groups A and B); exp or softplus with 24,000 < G ≤ 3,456,000 (`large-exp`, `large-softplus`); identity with G ≤ 24,000 (`id-24000`); identity above that up to 3,456,000 (`id-large`); and any fit above 3,456,000, which has **no control**. Round 1's census says many inputs of the too-easy gate and the ORACLE reference will be searched rather than exact (25 of 30 B-sparse out-of-dictionary units, 27 of 60 ORACLE units), so this line is the survival statement's real content. It is never "there are no misses", and it is not a claim that the GO is confirmed.

**If the outcome changes,** the label is `GO_CHANGES`. A substituted fit is a real, certified, better fit, so this label stands whether or not R1's control passed. The report names the clause that moved, the rows behind it, and the stored and corrected value of each gate, and states the consequence P4-PROPOSAL §2.4 fixes for that clause: a control failure means fix the instrument first; C3 specifically means B-sym is under-tuned and the cure is the dev tuning the LOG already requires (2026-10-02); a difficulty flip means a re-pilot. **Under `GO_CHANGES` the computed GO is not put forward to the owner as GO.** The stored verdict stays in the record as what was computed. Keeping it anyway is the owner's decision, logged as an override.

**What stays unknown either way:** misses R1 did not find on the 140 searched fits; everything in the not-covered rows of section 2.5; the curves; and sampling noise, since each reading is one held-out log per (truth, seed).

### 2.8 The store and the §7.1 rule are not touched

`.cache/pilot-merged` is opened with `ResultStore(root, read_only=True)` only. The script takes a sha256 manifest of the store's files before and after, and a difference is a failure. All output goes outside the store. No cell is written, `PILOT.md`'s generated sections are not regenerated from corrected numbers, and §7.1 and P4-PROPOSAL §2.4 are not reinterpreted. This is a check beside the computed verdict. The go/no-go remains the owner's.

### 2.9 Positive control (SPEC §6.4), first

The controls run before any other pilot unit, in the order A, D, C, B, so that group B is the first pilot unit read. A control asks one thing: does **R1 alone**, blind, on a fresh profile, under the frozen stream, reach a better value that is already known? R0 and the chosen point play no part. In groups A to C a case is **caught** when R1's own best is at least the known better value minus the tolerance, and exceeds fit/5's value by more than the tolerance.

| Group | Case | Known better value | Role |
|---|---|---|---|
| A | T2 (0, softplus, 103648458), (2, exp, 1499614403), (0, softplus, 1074), (0, softplus, 3273957339), (0, softplus, 358866363), (0, softplus, 2006407976) | The generating point's value, on a separate profile | Known cases, G = 14,400 and 24,000, horizon 300 |
| B | ORACLE dev-015 s3 | `oracle_truth_psi_ll` | The one pilot-scale case: `exp`, G = 20,736 |
| C | Hunt cases (below) | The generating point's value, on a separate profile | Identity link, and grids of 24,000 and 3,456,000 points |
| D | T2 (0, softplus, 103648458), R2 alone on an empty profile, no R1 | Same as A | Enumeration reaches the known value. Group D is the one control that does not test R1 |

**Pass rule for H1, fixed now.** H1 stands if at least 5 of the 6 group A cases are caught, group B is caught, and group D is caught. The cap stops T2's G = 14,400 cases near 43 starts if a start costs 0.782 of a sweep, T2's own figure at 32 starts (30 starts, and 0.90 below, at 1.1). On round 1's rates, group A then passes with probability 0.97, and all six with 0.41, so "all must pass" would mostly measure the 1/77 case. H1 as a whole depends on group B, a single unit with a small sweep (60, against R1-only quartiles of 90, 144 and 188) whose rate is unknown: from 0.45 if it is like the 1/77 case to 0.94 if it is like 4/56. So H1 may fail on one unit, and that is accepted: it is the only pilot-scale case. Group C has no pass threshold, because how many cases it yields is not known: its catch count is reported, per stratum, as a stated rate with its denominator, and a stratum with no found loss says "no known miss, no control".

**Nothing here is an independent validation.** All six group A cases informed the design. What the frozen run adds is the fixed stream, group B, whose R1 result nobody has seen, and group C. No Clopper-Pearson figure is quoted for group A.

**Group B reads the store for one known value.** Its R0 byte check (section 2.4) runs too, so the first pilot unit proves both halves. The old row "every other ORACLE unit below its truth ψ" is deleted: `systems.py:318-320` keeps a higher truth-ψ fit whenever it is certified, so any such unit has an uncertified truth-ψ point that R1 may not select. Those units are counted in S3.

**The hunt, group C.** Before any pilot unit, on fixed-seed synthetic logs, in four sub-strata of 150 cases each, chosen to sit where no other control does.

| Sub-stratum | Structure | Link | G |
|---|---|---|---:|
| `id-24000` | `BIG[2]` of `tests/glm/test_fit_search.py` | identity | 24,000 |
| `id-large` | `LARGE` | identity | 3,456,000 |
| `large-exp` | `LARGE` | exp | 3,456,000 |
| `large-softplus` | `LARGE` | softplus | 3,456,000 |

`LARGE = (Gate(EXP_K, PhaseWindow()), Gate(SIZE_K, PhaseWindow()), Gate(POWER_K, PhaseWindow()))`. Case j of sub-stratum s (s = 0 to 3 in the table's order, j = 0 to 149; `i = 1000 * s + j` and `stratum` is its name in the seeding rule) uses `rng = default_rng(SeedSequence([20261106, 1000 * s + j]))`, and in order: `_random_point(structure, rng)`, the coefficients, then `simulate(structure, generating, coefficients, CHANNELS, marks, 300.0, rng)`. Coefficients for exp and softplus are `_random_coefficients`. For identity they are `Coefficients(0.5, ...)`: a bare or gated `Periodic` gets `(0.2, -0.15)`, and every other column `uniform(0.1, 0.4)`, drawn in feature order. Cases run round-robin (j outer, s inner), so a cap cuts the sub-strata evenly. A `SimulationError` counts the case as `sim_failed`, and it is skipped. R1 on found losses runs as jobs after the fit/5 jobs, in index order.

For each case fit/5 runs, then `fit(s, obs, CHANNELS, psi=generating)`. A case is a **found loss** if the fixed-ψ fit is certified and beats fit/5's L by more than the tolerance. fit/5's own `certified` flag is not a condition, because it is false when any visited point was uncertified. A found loss joins group C and R1 is run on it, for the first 25 found losses of each sub-stratum in index order; later ones are counted but not run. None is dropped for size. The whole of group C, R1 included, stops dispatching at **6 CPU hours** of finished jobs, and it reports how many cases it completed. Jobs in flight finish, so it can overrun by those. An uncertified generating point that beats fit/5 is counted as `uncertified_loss` and is not a found loss.

Stream 20261106 has not been run. A spike on another stream (session 28, `.cache/drafts/prereg-s28/spike/`) measured stability and cost on 40 seeds and fit/5 on 5 seeds per sub-stratum, `id-large` excepted, and saw one loss, of 4.27 nats, on `LARGE` exp at 1 of 5, and 0 of 20 on identity with `BIG` members. So `id-24000` and `id-large` **may yield nothing**. The generating point is a weak target at about 200 events: fit/5 beat it by 0.27 to 10.2 nats in 17 of those 20 cases. An empty sub-stratum is reported as "no known miss, no control", and the identity link then stays with no control at all on grids above 14,400 points.

Group C is the only evidence on the identity link and on grids above 24,000 points, and it stops at 3,456,000: 51 of the 131 tier 1 searched fits have larger grids, and no control reaches them. Its limits: it is synthetic, at horizon 300, and its identity logs are well specified, which is the easy case (about 1 uncertified point in 10,000 in the spike, against 14 of 164 on a misspecified log). Its rates may be too high or too low for pilot logs by an unknown amount.

**If H1 fails, the run continues.** R1's misses are still real and the exact class does not depend on R1, so stopping would discard the part that is sound. The outcome is `CONTROL_FAIL` (section 5) unless the verdict moved. No survival statement is made for searched fits, and a stronger reference needs an amendment frozen before it is run. The search is not tuned and rerun on a failing case under this freeze.

**What the controls do not cover.** Five of group A's six cases are one stratum (BIG[0], softplus). Nothing in A or B covers the identity link (73 of the 255 fits) or a grid above 24,000 points. S1 and the exact fits speak to the first at G ≤ 14,400, and group C to both, if it finds anything.

### 2.10 Code

- **Tests first** (CLAUDE.md, SPEC §6.3): `tests/campaign/test_pilot_search_misses.py` is written and seen to fail before the script. No test touches a pilot unit. They cover at least: the exhaustive class against brute-force `fit(psi=...)` and the 512/513 boundary; the byte check (equal passes, one ulp off fails); the miss rule (near tie, uncertified better point, tolerance from gaps, an infinite stored gap); the profile holds only the reference's own points (section 2.2); R1's own best is recorded apart from the chosen point, and a control cannot pass on R0; R1 deterministic and independent of worker count and order; seeds from sha256 for all three kinds of case; empty `corrected` returns an equal `Verdict`; a planted shift on a toy table flips the outcome where it should; Δ on `-inf` rows; append-only resume; the CPU stop and the refusal to resume over it; one process per job; `--finalise` from records left by a kill; the store manifest check; a split hash mismatch and a missing cell both stop; every guard of this section refuses when it should; and the label rules of section 5 on planted records. T2 controls 1 and 2 are slow-tier tests, on the test root 20261107 and never the frozen stream.
- **Script:** `scripts/pilot_search_misses.py`, importing `_Profile`, `_ascend`, `_Budget` and `_search_blocks` from `sciagent.glm.fit`. No change under `src/`, so `FIT_VERSION` and every address stay as they are.
- **Fit code.** fit/5 as at 900e66a. `git diff 900e66a v2 -- src/sciagent/glm` is empty (0 lines). Between 900e66a and v2, `src/` differs only in `campaign/audit.py` and `campaign/merge.py`, `scripts/` only in `pilot_report.py`, `store_merge.py` and five new `scripts/p3_report/` files, and `pyproject.toml` and `uv.lock` not at all.
- **The freeze has two commits, and no tuning window.** `F` freezes this file. The script and its test are not in the tree at `F`; they are drafted on a side branch from session 28, against toy data only, and no version of them has read a pilot unit. `R` is a later commit on v2 that adds exactly those two files. At start the script refuses unless:
  - its committed constants `PREREG_SHA256` and `F_COMMIT` match this file's sha256 in the tree and an ancestor of HEAD, and `decision.json` records `F`, `R` and that hash. Both are in code, not typed by the operator, and the digest is also in the LOG's freeze entry;
  - the sha256 of the script and of its test are the ones a LOG entry records at `R`, written and pushed before the launch. At the first start the script writes its own hash to `cpu.json`, and any later start or resume with a different hash, or a fresh start in a directory that already holds records, is refused;
  - `git diff --name-only F HEAD` lists, outside `docs/`, exactly `scripts/pilot_search_misses.py` and `tests/campaign/test_pilot_search_misses.py`, and does not list this file;
  - `git diff --quiet 900e66a HEAD -- src/sciagent/glm src/sciagent/core src/sciagent/scoring src/sciagent/systems src/sciagent/scenarios src/sciagent/campaign/analysis.py src/sciagent/campaign/execute.py src/sciagent/campaign/plan.py src/environments scripts/pilot.py pyproject.toml uv.lock` is clean;
  - the tree is clean, and `OPENBLAS_NUM_THREADS`, `OMP_NUM_THREADS` and `MKL_NUM_THREADS` are 1.

  No pilot unit is read between `F` and the launch: the census scripts are not run again, and the script's test uses toy tables only. **A change to the script after `R` is an amendment:** it needs the control room's approval before it runs, it is a new frozen version under this document's own steps, it writes to a new output directory, and the first set of records stays in the report beside the new one. There is no other way to run different code. The first run's label stands in the record; a later label is reported beside it and replaces it only by the owner's logged decision.
- **The split.** Git does not track `data/`, and a clean worktree has none. So `--split` is passed as the absolute path of the main checkout's `data/v2/truths/dev`, and the script hashes it: `sha256` over the sorted lines of `sha256sum` for every file, with paths relative to the repository root, must equal `31c18f10ca7c8a4ad6b599de5436bb15426bd02c67f3eb44fb0e93cb512ba577` (`find data/v2/truths/dev -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum`, run in the main checkout). The hash is a constant in the script. A mismatch stops the run, and so does any cell of the 670 missing, since addresses carry `truth_digest` (`plan.py:377`).
- **Where it runs:** a clean detached worktree `.claude/worktrees/misses` at `R`.
- **Output:** `/home/ubuntu/research/records/sciagent/search-misses-F/` (outside the repo tree), copied at the end to `s3://delaymargin-results-482913/sciagent/p3-linux/search-misses-F/`. Records (`journal.jsonl` appended during the run; `units.jsonl` derived from it at the end, floats as `float.hex`, sorted, byte-identical on a rerun unless a clock-based stop fired, which are group C's cap, `cpu_cut`, the job limit and the 78-hour stop; the record names which), timing kept apart in `timing.jsonl`, and `decision.json`.

## 3. Primary analyses

One per hypothesis. There is no significance test: this is a count over a census, and the thresholds are fixed here.

| H | Statistic | Threshold | Falsified when |
|---|---|---|---|
| H1 | Group A caught, of 6; group B caught; group D caught | At least 5 of 6, yes, yes | Any part below that |
| H2 | M = rows carrying a reading miss, over the 267 coordinate rows (also given as distinct fits) | 0 | M ≥ 1 |
| H3 | max over miss rows of \|Δ\|, a move to or from `-inf` counting as above (section 2.4) | 0.01 nats per event | Above 0.01 |
| H4 | `v.outcome` from section 2.7 | `"go"` | Anything else |

0.01 is `gonogo.BSYM_TOLERANCE` and `audit.GAP_THRESHOLD`, the scale §7.1's reading works at. H3 and H4 are vacuously unfalsified at M = 0, and the report says so.

## 4. Secondary and exploratory analyses

**Secondary** (fixed now, reported, cannot change a primary label):

- **S1, R1's recall at pilot scale.** On fits where R2 ran, the share of misses R2 found that R1 alone had found, counted in distinct fits, read from R1's result as recorded before R2 ran. This is the only pilot-scale measure of how much R1 misses, and at best an optimistic one for larger grids. **Floor, fixed now:** with fewer than 5 misses in the denominator, `s1_status` is `not_estimable`; with 5 or more and recall below 0.8, it is `below_floor`; otherwise `at_floor`. `s1_status` is printed in the label line. Below the floor, the label line adds: "R1 missed more than one in five of the misses on grids where the answer is known."
- **S2, B-sparse's link.** Rerun `_choose_link` on the training window with R0 and then with the reference at K = 16 and a cap of 2,000 new solves per link (R0 and R1, cheaper than the primary and so weaker) for all three links on B-sparse's coordinate rows, using `split_dataset(obs, TRAIN_FRACTION)`; the 26 out-of-dictionary rows first. R0's choice must equal the cell's `sparse.link`; a row where it does not is reported and left out of S2, and does not touch the label. A link that R0 scored `-inf` stays `-inf` under the reference, because `_choose_link` reads the fit-level flag and a single-point refit passes more easily, so no link can win for that reason alone. Where the reference's choice differs, fit that link on the full log with the reference and score it. A second verdict, `v2`, adds those rows to `corrected`, and is labelled as such. S2 runs before tier 2, because it feeds a §7.1 gate and tier 2 does not. If `v2.outcome` differs from `v.outcome`, the label line must say so, in those words, whatever the label.
- **S3, shape of the found misses.** Training gain in nats and per event, Δ, sign of Δ, near ties, `uncertified_better` (printed beside M on every report of the identity link), `capped`, `stored_uncertified`, and search misses on ORACLE, including units where stored `oracle_search_ll` is below `oracle_truth_psi_ll`, as counts and quantiles.
- **S4, consequences on miss rows.** Change in `gap` and `gap_closed`, T4 flags that change, each §7.1 statistic stored and corrected beside its limit, and the count of corrected gaps above +0.01 (the `hunt_gap` gate) on the corrected table. `similarity` is recomputed by `score_submission` on miss rows only.

**Exploratory** (labelled so, no decision reads them): which shapes of structure miss (link, number of features, G, sharing of `period` slots); whether misses fall on the same (truth, seed, structure) across systems. Per-system tallies are shown as counts only. No difference between systems is claimed, and no control supports one.

## 5. Decision rules

**This section alone sets the label; any other section that mentions one defers to it.** `scripts/pilot_search_misses.py` writes one label to `decision.json`, by the first rule that applies. Exit code in brackets.

1. `BYTE_FAIL` (3): any byte check, a start-up guard, the split hash, a missing cell, the baseline `Verdict` equality or the store manifest failed, in any stage. If R0 does not reproduce the store, nothing else can be read.
2. `GO_CHANGES` (2): `v.outcome != "go"`, on whatever has been found. Found misses are real, so this does not depend on H1, and it outranks every shortfall, so no stop can bury a flip. If tier 1 did not finish, `decision.json` adds `partial: true`.
3. `INCOMPLETE` (5): `v.outcome == "go"` and tier 1 fell short: any tier 1 fit lacks its R0 and R1 or is `reference_failed`, or a CPU stop, wall limit, `STOP` or bug ended the run before tier 1 finished. `v` is computed on what was found and is labelled partial. No survival statement is made.
4. `CONTROL_FAIL` (4): `v.outcome == "go"` and H1 is falsified. The exact class is reported as it stands. No statement is made about searched fits, and the survival wording of section 2.7 is not used.
5. `GO_SURVIVES_FOUND_MISSES` (0): `v.outcome == "go"` and H1 stands. The wording of section 2.7 is used, with its per-gate line.


Beside the label: H2 and H3 as `falsified` true or false, M, max |Δ|, exact, searched, unchecked and capped counts, `s1_status`, group C's catch count per stratum, the `v2` line if it differs, and the coverage line of section 10. A stop of any kind after tier 1 finished, S2 and tier 2 included, does not alter the label, because neither is an input of `v`; it is reported as a shortfall. The label at the end of tier 1 is **provisional** until the run ends, because a byte failure later still gives `BYTE_FAIL`. A run ended by `STOP` is not resumed: going on takes an amendment.

**What each label means for the go/no-go (the 08:30 instruction; P4-PROPOSAL §2.4).**

| Label | Did the computed GO survive? | Next |
|---|---|---|
| `GO_SURVIVES_FOUND_MISSES` | It survived the misses found, in section 2.7's wording | The go/no-go goes to the owner with that wording and the per-gate line. No longer held for this check |
| `GO_CHANGES` | No, on the misses found so far | Not put forward as GO. §2.4's consequence for the clause is stated. The owner decides, and keeping GO is a logged override. With `partial: true` the flip may yet reverse, so §2.4's consequence is stated and not applied until the owner has decided |
| `CONTROL_FAIL` | Open | The computed GO is not put forward as final. The exact class is reported. The way on is a frozen amendment with a stronger reference, or the owner's own decision to go on with the gap stated, logged as an override |
| `INCOMPLETE` | Open | Same as `CONTROL_FAIL`. A run that died by SIGKILL, a reboot or the out-of-memory killer, below the 78-hour stop and with wall time left, may be resumed. A run at the 78-hour stop, at the wall limit or ended by `STOP` may not; the script refuses, and going on takes an amendment |
| `BYTE_FAIL` | Open | Same, and the failure is an instrument fault: root cause first, by amendment, before any reading |

## 6. Exclusions and failure handling

Fixed in advance. No row is excluded for its outcome.

- **By class:** B-np (60), B-lib out-of-grammar (22) and no-submission (3) are outside the check, section 2.1. A void or failed row, of which there are 0 today, would be handled as `load_rows` handles it, and counted.
- **Exhaustive, `certified` = 0:** counted apart, not called checked.
- **`SciAgentError`, `ValueError` or `ArithmeticError` in R1 or R2** (`UNIT_FAILURES`, as in `execute.py`): the fit is `reference_failed`, its stored reading stays in the corrected table, and it counts as unchecked. Any other exception is a bug: it stops the run, and the label follows section 5. A unit failure in R0 is `BYTE_FAIL`, since the stored cell holds a fit that R0 must reproduce.
- **Stored fit uncertified at R0, or with a non-finite gap:** recorded, compared by actual L under section 2.3, and substituted like any other. Rows in this state are listed in `stored_uncertified`.
- **Worker lost** (killed by the out-of-memory killer, or any exit without a result): the job is retried once, in a new process, after the stage's other jobs. A second loss before R1's result is written is `reference_failed` with reason `worker_lost`.
- **Per-job CPU limit:** 3 CPU hours, by `RLIMIT_CPU`, in a process that serves one job only. A job that reaches it before R1's result is written is `reference_failed` at once, reason `cpu_limit`, and is not retried, because the same job would reach it again. A job is one fit: R0, then R1, then R2 where due, in that order, in one process. S2's rows and hunt cases are jobs too, under the same limit. Each stage's result is appended as it finishes, so a job that dies in R2 keeps its R1 result: the fit is searched, not exact, and any miss R1 found stands; it does not count as `reference_failed`. To keep the limit from losing work, R1 checks its CPU between starts and R2 between chunks, and at 2.5 CPU hours stops, flagged `cpu_cut` (searched). That check reads a clock.
- **`capped`:** searched, not exact, with the cap stated. A capped fit with G ≤ 14,400 still goes on to R2, and is exact if R2 finishes.
- **Non-finite corrected reading:** kept. `-inf` is handled by `derive` and `verdict` as written, including ORACLE at `-inf` failing control 1. `nan` is `reference_failed`.

## 7. Sample size

This is a census, so there is no sampling error in M, and n is fixed by the store: 267 rows, 255 distinct fits, 196 of them in tier 1 and 59 only on LLM rows. A sample would not do, because the verdict's gates turn on few units. Too-easy needs 14 more of the 30 out-of-dictionary B-sparse units to close (PILOT.md), and 26 of those 30 are on the coordinate path (`count3.py`). C3 reads at most 10 B-sym@10F units, 6 of them coordinate, so one unit can move its median.

What the numbers can and cannot support:

- **Controls.** No recall bound is quoted from them. Group A's cases are not a random sample of misses, they informed the design, and they are mostly one stratum. The measured per-start rates run from 1/77 to 33/51, and the 1/77 case has a 95% range for P(no catch) in 32 starts of 0.097 to 0.990 (round 1). The measured rates are the honest summary, and they are why searched fits stay unvalidated. S1 and group C are the only other evidence.
- **M = 0 on 255 fits** would give a 95% upper bound of 1.2% on the rate of misses this reference detects, if the fits were exchangeable. They are not (many share a truth and seed), so that figure is not reported as a rate.
- **Exhaustive coverage.** 115 of 255 fits (45%) are decided exactly, and 65 of the 196 in tier 1.

## 8. Cost ceiling and wall clock

**Ceiling: 84 CPU hours in total over all units of this freeze, $0.** No LLM call, no usage credit, no instance beyond the always-on dm-devbox; the S3 copy is a few MB. Raised from the first draft's 72 so that tier 1 fits at the misspecified identity rate; it costs no money, only machine time.

How it holds. The kernel's count is the record: the driver reads `usage_usec` from the unit's `cpu.stat` once a minute, adds it to the total of earlier units kept in `cpu.json` in the output directory (written and fsynced at each reading), and at **78 CPU hours** stops dispatching and lets jobs in flight run. Each of the at most 2 jobs in flight is bound by its own 3 CPU hour limit, so the total cannot pass 78 + 2 × 3 = 84 by more than the one-minute polling allows, about 2 CPU minutes, even if the driver hangs. A resume reads `cpu.json` and refuses at or above 78. The unit's `RuntimeMaxSec` is a backstop on time, not on CPU: 60 hours in total, and a resume is launched with 60 hours less the time already spent (the script prints the launch line). `--finalise` needs no live driver: it reads `journal.jsonl`, which is appended and fsynced per finished job, and the stored table (read-only, with `--split` and `--store`, under the same guards), then writes the sorted `units.jsonl` and the label. It runs on the driver's clean stop, and by hand after SIGKILL or a wall limit, and it uses under a minute of CPU.

```sh
systemd-run --user --unit=sci-search-misses --nice=10 -p CPUQuota=200% -p RuntimeMaxSec=<remaining> -p MemoryMax=5G -p OOMPolicy=continue \
  -E OPENBLAS_NUM_THREADS=1 -E OMP_NUM_THREADS=1 -E MKL_NUM_THREADS=1 \
  --working-directory=<worktree> uv run python scripts/pilot_search_misses.py --workers 2 --split <absolute path> --store <absolute path>
```

`OOMPolicy=continue` keeps the unit up when one worker is killed, so section 6's retry happens in the same run.


**Machine.** At most 2 workers, `nice` 10, one process per job (`FitConfig.workers` stays 1), BLAS threads 1. **The unit is launched only when `MemAvailable` ≥ 8 GiB**, so its time limit does not run while it waits: AUTOPILOT's 6 GB floor plus the about 2 GB a pilot worker peaks at (QUEUE). The second worker starts only while `MemAvailable` ≥ 8 GiB with the first running; below that, dispatch pauses. That needs most of the running units gone, since round 1 measured 6.81 GiB available with about 3.98 GiB in the four units, page cache included. Per worker, the spike measured a peak of 729 MB at 4,043 solved points on a 2,318-event log, and the block cache's 1 GiB limit puts the top near 1.3 GB; R2 takes one profile to 14,400 points, which was not measured. The 2 GB allowance stays.

**Order, fixed and outcome-neutral.** Controls and the hunt; then tier 1 (the 196 fits on baseline rows, the only rows `v` reads); then S2; then tier 2 (the 59 fits only on LLM rows). Inside a stage, by `sha256(f"{truth}|{seed}|{text}")`. The label is provisional at the end of tier 1 (section 5).

**Inputs.**

| Quantity | Value | Source |
|---|---:|---|
| Fits: exp, softplus, identity | 47, 135, 73 | `count4.py` |
| Sweeps of the 140 R1-only fits, tier 1 | exp 3,879, softplus 12,459, identity 2,880 | `count5.py` |
| Sweeps of the R1-only fits, tier 2 | exp 116, softplus 809, identity 137 | `count5.py` |
| Sum of one sweep over fits (points) | 5,413 + 16,607 + 6,600 = 28,620 | `count4.py` |
| Grid points of fits with G ≤ 14,400, tier 1 | exp and softplus 179,148, identity 147,636 | `count5.py` |
| Same, all fits | 239,898 and 195,876, of 435,774 | `count5.py` |
| New points per R1 start, fresh profile | 0.34 to 1.10 sweeps per start over 48 starts: seven synthetic cases, G from 24,000 to 3,456,000, horizon 300, one log per shape, root 20261199. Taken as 1.1. It runs higher over few starts (1.65 to 1.86 at 9 starts on `LARGE`) and falls as starts overlap. Pilot logs are longer and were not measured | `.cache/drafts/prereg-s28/m.out`, round 2's `ascent.py` |
| CPU per point, exp and softplus | 0.041 to 0.044 s in R1, 0.055 to 0.064 s over a whole fit/5, at 1,395 to 1,420 events. Taken as 0.123 s, measured on a 2,318-event synthetic log over 4,043 points, near the largest ORACLE log in the census (2,327 events) | `t2_ref.py`, horizon 2,000; spike `d.table`; round 1 census |
| CPU per point, identity, well specified | 0.039 s at 2,269 events over 2,052 points; 0.025 s at 1,865 events. Taken as 0.04 s | spike `d.table`, `t4_time.py` |
| CPU per point, identity, misspecified | 0.313 s mean at 1,420 events, Clarabel on every point, so 0.512 s at 2,327 | `t5_time.py` |
| R0, CPU per fit | 55.3 s, B-rand's recorded mean over 40 fits, scoring included | `.cache/pilot-merged/timing.jsonl` |

**Expected cost.** On fits with G ≤ 14,400, R1's points are part of the grid, so R1 and R2 together cost at most G. Only the R1-only fits add R1 points: `min(1.1 × 48 × sweep, cap)` for each fit, summed (`.cache/drafts/prereg-s28/cost3.py`, from `count6.py`): 696,368 on exp and softplus and 71,702 on identity in tier 1.

| Part | Arithmetic | CPU hours |
|---|---|---:|
| Controls | Groups A and D at horizon 300, B on its own log, under half an hour | 0.5 |
| Group C | Capped. Spike: about 5 to 33 s per case at median, so 600 cases near 3 hours, plus R1 on found losses at up to 8,000 or 3,000 points each | at most 6 |
| Tier 1, exp and softplus | (696,368 + 179,148) points × 0.123 s | 29.9 |
| Tier 1, identity | (71,702 + 147,636) points × 0.04 to 0.512 s | 2.4 to 31.2 |
| Tier 1, R0 | 196 × 55.3 s | 3.0 |
| **Tier 1 (provisional label)** | | **35.4 to 64.1** |
| S2 | 51 rows × (3 × 55.3 s + 2,000 points × (0.123 + 0.123 + 0.04 to 0.512 s)), at S2's cap | 10.5 to 23.8 |
| Tier 2 | all fits (40.4 to 75.9 hours) less tier 1 | 5.0 to 11.8 |
| **All** | | **55 to 106** |

The width is the identity link: whether the 73 submitted identity fits behave like ORACLE's or like a badly misspecified log is not known, and it could not be measured without running on pilot units. The high end of "all" is above the 78-hour stop, so S2 and tier 2 can be cut short, and the report says by how much. The caps protect tier 1 against the cost of a start, and not against the cost of a point: with every searched fit at its cap, controls, group C and tier 1 come to (107 × 8,000 + 179,148) × 0.123 s + (24 × 3,000 + 147,636) × 0.512 s + 3.0 + 6.5 = 76.1 CPU hours, under 78. That holds at these rates, with a margin of 5%: it reaches 78 at 0.130 s per point with identity at the top rate. The rates are not bounds: 0.123 s comes from a 4-column structure, 106 of the 131 tier 1 searched fits have more columns, and one synthetic shape in `m.out` scales to 0.33 s at 2,327 events; on the other side, recorded ORACLE units peak at 92.6 s, and most logs are smaller than the largest. `m.out` has no identity case. If the rates are worse, the 78-hour stop falls inside tier 1 and the label follows section 5.


**Worst case with nothing to stop it.** Every R1-only fit at its cap and identity at the top rate: (115 × 8,000 + 239,898) × 0.123 s + (25 × 3,000 + 195,876) × 0.512 s + 3.9 h, about 40 + 39 + 4 = 82 CPU hours, or 88.6 with controls and group C, before S2. The ceiling is enforced and not assumed, and at the stop the label follows section 5.

**Wall clock.** The one recent reference, the pinned T2 run, got 2,133 CPU seconds in about 2,640 s on this machine under load, so 0.81 of a core. With 2 workers that is 1.62 cores: tier 1 over by 24 to 44 hours into the run and the 78-hour stop at 48 hours, inside the 60-hour limit. With one worker for the whole run, 60 hours gives 49 CPU hours, which would leave the top of the tier 1 range unfinished; that is a further reason to launch only when the gate is open. Controls take under half an hour, and group C at most about 4 hours.

## 9. Early-stop conditions

The run stops early only for these. **There is no stop on outcome:** a miss, a large Δ, a changed verdict or a failed R1 control never ends the run.

1. A byte check, the baseline `Verdict` equality, the start-up guards of section 2.10, the split hash, a missing cell or the store manifest fails: stop at once, `BYTE_FAIL`.
2. The 78-hour CPU stop or the wall limit is reached: stop, closing the records, section 8.
3. A bug stops the run (section 6).
4. The owner or control room writes `STOP` in the output directory: stop after the jobs in flight. The file should say who stopped the run and why, and the driver copies it into `decision.json`; an empty file still stops it, and the report then says no reason was given. The label follows section 5, so a stop can neither keep a favourable partial result nor bury a flip. `journal.jsonl` is readable during a run and that cannot be prevented: nobody reads it before the run ends, and if anyone does, the note says so.

MemAvailable below the floor pauses dispatch and does not stop anything.

## 10. Reported whatever the outcome

In one note under `docs/v2/`, with commands, and red-teamed before it goes out as a result:

- The label, and H1 to H4 each stated as falsified or not.
- Control results, case by case, with fit/5's value, the known better value and R1's own best, the stratum rates of group C with denominators, and the hunt's completed count.
- The class table of section 2.1 with final counts, including exhaustive rows at `certified` = 0.
- **Coverage line:** rows and fits checked exactly (R2 or exhaustive), checked by R1 only, unchecked, capped, and not applicable.
- M in rows and fits, per system as counts, each miss row with its training gain, Δ, stored and corrected reading and class. ORACLE's search misses apart. If there are none, the table says none.
- `analysis.verdict` on corrected readings: outcome, each gate's statistic stored and corrected, kept and dropped truths, `reasons`, `inconclusive`.
- S1 to S4 and the label `v2`, and the exploratory table marked as such.
- The not-covered list of section 2.5, and the unknowns of section 2.7, in words, whatever M is.
- CPU hours and wall time used, LEDGER row, S3 path, `F` and `R`, and any departure from this document with both versions, recorded in `docs/v2/LOG.md` as a stop point.

Numbers go out reviewed or labelled unreviewed. Stored readings, the stored verdict and PILOT.md's generated sections stay as they are. A corrected reading is never presented as the pilot's reading.

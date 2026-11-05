# Red team: pilot search misses, run records and section 10 note (session 31)

Reviewed: `docs/v2/PILOT-MISSES.md` at 95e4341 (sha256 `a3e62c6c…`, 398 lines). HEAD moved from 1ea5c35 to 95e4341 during the review; the regenerated part still matches this text. Also reviewed: the run records, the worktree at R, and `.cache/pilot-merged`, read only.

## Verdict: ready with fixes

**The label stands.** `GO_SURVIVES_FOUND_MISSES` follows from the records under sections 3 to 5, and every number I recomputed holds. No finding changes a number, the label or the verdict.

Five findings are blocking, and all five are text. The go/no-go should not go to the owner until they are fixed, because the problems sit in the lines the owner will read for coverage.

Commands below use these settings, run inside `.claude/worktrees/misses`:

```sh
RT=/home/ubuntu/lanes/sciagent/.cache/drafts/s31/rt
REC=/home/ubuntu/research/records/sciagent/search-misses-3226f9b9521fe3a8c1d2445d1654cd1609b1eb2d
RUN="env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 nice -n 10 .venv/bin/python"
```

## The five questions

**1. Does the label follow? Yes.**

| Section 5 rule | Result |
|---|---|
| `BYTE_FAIL` | 0 `byte_fail` records; store manifest identical (1,157 files); split hash equal; empty substitution equals the stored verdict |
| `GO_CHANGES` | `v.outcome` is `go`, from `analysis.verdict` and from my own gate arithmetic |
| `INCOMPLETE` | 196 of 196 tier 1 fits done, 0 `reference_failed`, `run_end: complete` |
| `CONTROL_FAIL` | H1 stands: group A 5 of 6, group B caught, group D caught |

```
$ $RUN $RT/b_verdict.py
stored outcome go corrected go
recompute == decision True True
OWN wins stored 18 / corrected 18;  OWN med 0.024488052663725595 / 0.024767803287368533
OWN ood stored (0.2333333333333333, 10) / corrected same;  OWN c3med 0.0 / 0.0
scipy wilcoxon exact stored 4.76837158203125e-06 corrected 4.76837158203125e-06
v2 go sparse_ood 0.2333333333333333
$ $RUN $RT/e_replay.py
manifest files 1157 diff now []
decide() replay bytes equal decision.json: True 11857cb0572b56e4 11857cb0572b56e4
```

**2. Are M, coverage, controls and H1 to H4 right? Yes.**
- M is 25 rows on 25 fits, 23 of them baseline. The miss rule recomputes with 0 mismatches, and the smallest gain is 0.256 nats against a tolerance of 3e-7 on every row.
- H3 is falsified by 3 rows: dev-012 s3 B-sparse +0.02198, dev-015 s3 planted-hint +0.01670, and dev-019 s3 AG-o-S/anon +0.01050. The last two are in the exact class, so H3's fall does not rest on searched fits.
- Group A to D tables and all four group C rows recompute from `units.jsonl`.
- All 25 rows of the miss table parse equal to `units.jsonl`. The generator's output is byte-identical to the note above "Added by hand", and its 82 tests pass.
- Counts reconcile, but only once labelled (finding 6).

| | Exact | Searched | Unchecked | Not applicable | Total |
|---|---:|---:|---:|---:|---:|
| Rows | 441 (317 + 124) | 143 | 1 | 85 | 670 |
| Fits | 310 (195 + 115) | 140 | 1 | | |
| Baseline fits | 184 (119 + 65) | 131 | 0 | 82 | 397 |

**3. Do the words overclaim? In places, yes (findings 1 to 3).** The note may report what was found and the controls' raw results. It can say nothing quantitative about misses not found. The fixed sentence is correct, but the strata heading, the per-gate line, `uncertified_better` and the bare max |Δ| line all read stronger than that sentence.

**4. Could the 276 warnings affect a miss row, Δ or the label? No.** They cannot touch any miss row, any Δ or the label. They can only hide misses on identity fits, which M's lower-bound status already allows. Evidence is in finding 4.

**5. Departures?** The launch line, guards, stage order and budget all match the prereg. The departures are reporting gaps: items required by sections 2.2, 4, 6 and 10 are missing from the note, and the LOG has no stop-point entry (finding 5).

## Findings, ranked

### 1. BLOCKING: the strata table says more than the controls can

The note heads its table "the control that speaks for them". The prereg says the opposite: "Searched" is what the label says "whatever the controls do", and "Nothing here is an independent validation".

- Group C's R1 cases have 158 to 357 events; pilot fits have 1,797 to 2,327.
- 22 of the 23 R1 runs were capped, and `id-large` got only 4 to 6 starts.
- 13 of the 25 misses and the largest Δ sit in the 51 no-control fits, of which 38 are capped, with as few as 6 starts.
- R1 alone fell short of fit/5's own value on 5 fits. One is dev-008 s2, by 6.9 nats with all 48 starts and no cap, in the stratum shown as "4 of 4 caught".
- `H3: max |Δ|` carries no "among misses found". The prereg's own limit is missing: this is no new worst-case figure for fit/5.

```
$ python3 -I $RT/c_controls.py
caught [('id-24000', 0), ('id-large', 5), ('large-exp', 7), ('large-softplus', 4)] of [('id-24000', 1), ('id-large', 7), ('large-exp', 11), ('large-softplus', 4)] total 16 23
('id-24000', 45, False, 0.9471, 0.0, False, 48, 2737, 158, 0)
  # stratum, j, caught, known gain, R1 gain, capped, starts, points, events, uncertified
$ python3 -I $RT/n_r1.py
2 large-softplus {'R1 equal fit/5': 41, 'fits': 47, 'capped': 7, 'R1 above fit/5': 5, 'R1 below fit/5': 1}
5 no_control {'R1 equal fit/5': 35, 'fits': 51, 'capped': 38, 'R1 above fit/5': 13, 'R1 below fit/5': 3}
worst R1 shortfalls [(-6.901, 'dev-008', 2, '2 large-softplus', 48, False), (-3.487, 'dev-019', 2, '5 no_control', 9, True), (-2.29, 'dev-017', 3, '5 no_control', 20, True), (-0.838, 'dev-019', 1, '4 id-large', 9, True), (-0.25, 'dev-012', 2, '5 no_control', 14, True)]
```

**Fix:**
- Rename the heading to "Searched tier 1 fits by stratum, and that stratum's control result".
- Add the event range, the caps, and "these rates are not recall for the fits beside them".
- Write "largest |Δ| among the misses found".
- Add the prereg's no-worst-case line.

S1 at 4 of 4 is `not_estimable`. R2 beat R1 on 0 of 115 fits, which says nothing above G = 14,400.

### 2. BLOCKING: the per-gate line hides the too-easy gate's real coverage

`too_easy: exact 105, searched 83` mixes in B-lib and B-rand recovery rows, whose `exact` flag substitution cannot move. The statistic that can move is B-sparse out of dictionary. On those 30 rows, 5 are exact and 22 have no control.

```
$ $RUN $RT/i_more.py
OOD B-sparse rows by state [('exact', 1), ('exhaustive', 4), ('id-large', 1), ('large-softplus', 2), ('no_control', 22)] closing 7 of 30
too_easy searched rows by stratum [('groups_a_b', 3), ('id-24000', 3), ('id-large', 11), ('large-exp', 5), ('large-softplus', 30), ('no_control', 31)] capped 30
$ $RUN $RT/k_margin.py
closing 7 needed for 0.7 share: 21 rows; 14 more
14th smallest needed shift (0.02266126556535364, 'dev-013', 3) rows with need<=0.02198 13
B-sparse deltas sorted [-0.00442, -0.00302, -0.00216, -0.00138, -0.00106, -0.00101, 0.00031, 0.00035, 0.00149, 0.00179, 0.02198]
```

The found max of 0.02198 is above the C1 (0.0217), C2 (0.0148) and C3 (0.0100) figures of the withdrawn stress test, and just under its 0.0227. Those figures assume a shift on every row in one direction, and the found Δs have mixed signs, so this is not a bound and not a threat estimate.

**Fix:** add the 30-row split beside the too-easy count. Do not describe the gates as far from their limits on the strength of this check.

### 3. BLOCKING: `uncertified_better: 0` reads as a check, but it cannot fire on failed solves

On the two tier 1 identity fits I enumerated, every uncertified point is a solver failure with L = -inf and gap = inf, so its score can never exceed the chosen point. Section 2.2 also says the uncertified count "is reported per fit", and the note has no such list.

```
$ $RUN $RT/r_enum.py
('dev-010', 1, 'ORACLE') G 2304 solved 2304 cpu 56s py warnings 63 record n_uncertified 1
  status [(('newton', 'optimal', True), 2250), (('newton+clarabel', 'cut_rounds', False), 1), (('newton+clarabel', 'optimal', True), 50), (('newton+clarabel', 'optimal_inaccurate', True), 3)]
  unc L -inf gap inf cut_rounds  (L+gap)-chosen nan
('dev-012', 2, 'B-sym') G 3600 solved 3600 cpu 88s py warnings 240 record n_uncertified 20
  status [(('newton', 'optimal', True), 3558), (('newton+clarabel', 'cut_rounds', False), 15), (('newton+clarabel', 'optimal', True), 22), (('newton+clarabel', 'solver_error', False), 5)]
$ $RUN $RT/s_failed.py        # diagnostic, outside the rule
('dev-010', 1, 'ORACLE') failed points 1 ...  retry outcomes [(('cut_rounds', False), 1)]
  best certified grid neighbour of each failed point, minus chosen: max -0.3639921254008982
('dev-012', 2, 'B-sym') failed points 20 ...  retry outcomes [(('cut_rounds', False), 15), (('solver_error', False), 5)]
  best certified grid neighbour of each failed point, minus chosen: max -509.8877779760478
```

A retry through Clarabel also fails. B-sym's failed points sit in a poor region. ORACLE dev-010 s1's failed point has a certified neighbour only 0.36 nats below the chosen L, so a better fit there cannot be ruled out.

The list the note needs (70 points on 17 identity fits, all rows `system, G: count`):

| Class | Fits |
|---|---|
| Exact, 46 points on 10 fits | dev-012 s2 B-sym 3,600: 20; dev-013 s2 AG-o-S/anon 12,000: 10; dev-001 s1 B-rand 13,824: 8; dev-010 s2 B-rand 14,400: 2; dev-003 s1 AG-o-S/anon 1,440: 1; dev-017 s1 AG-c-S/named 720: 1; dev-017 s1 AG-o-S/anon 4,800: 1; dev-018 s2 B-sparse 576: 1; dev-010 s1 ORACLE 2,304: 1; dev-013 s2 AG-c-S/anon 600: 1 |
| Searched, 24 points on 7 fits | dev-019 s1 B-rand 345,600: 8; dev-011 s2 planted-hint 20,736: 5; dev-010 s1 B-rand 16,588,800: 5; dev-008 s3 B-rand 115,200: 2; dev-011 s2 B-sym 41,472: 2; dev-003 s1 AG-c-S/named 21,600: 1; dev-019 s1 B-sym 172,800: 1 |

**Fix:** add this list, and say `uncertified_better` counts only points with a score, so failed solves are not covered by it.

### 4. BLOCKING: the warnings hand line is out of date and its count misleads

"Their effect on any row is not yet known" can now be answered. The number 276 also counts processes, not solves.

```
$ journalctl --user -u sci-search-misses --no-pager -o short-iso | grep 'Solution may be inaccurate' | sed -E 's/.*uv\[([0-9]+)\].*/\1/' | sort | uniq -c | awk '{print $1}' | sort -n | uniq -c
    276 1
$ python3 -I $RT/q_win.py
Counter({'hunt': 194, 's2': 45, 'tier1': 28, 'tier2': 9}) 276
$ $RUN $RT/f_rows.py          # all 25 miss rows, fixed psi, stored and chosen point
dev-011 2 B-sym identity L=True True pe=True True d=True stored[newton+clarabel/optimal cert=True gap=8.06e-10 warn=0] chosen[newton+clarabel/optimal cert=True gap=5.11e-09 warn=0]
dev-002 1 B-rand identity L=True True pe=True True d=True stored[newton/optimal cert=True gap=1.16e-11 warn=0] chosen[newton/optimal cert=True gap=1.13e-11 warn=0]
ALL EQUAL True
$ python3 -I $RT/o_misc.py
max stored gap 1.5239666344175064e-07 max chosen gap 1.2859435053891296e-08 max sum 1.524166690749464e-07
$ $RUN $RT/l_sens.py
drop identity-link rows substituted 23 outcome go C2 median 0.024488052663725595 sparse 0.2333333333333333
```

What this shows:
- **Source.** `fit_solve.py:591` is Clarabel. With `inner_solver="newton"`, exp and softplus return before reaching it, so only identity fits can warn.
- **Count.** Each PID warned once, which is Python's once-per-location filter. The true count is far higher and cannot be recovered: with `always`, the two fits above gave 63 and 240 warnings.
- **Meaning.** A warning does not mean a bad final solve; dev-012 s2 gave 240 warnings and no final `optimal_inaccurate`. The 3 final `optimal_inaccurate` solves on dev-010 s1 were all certified, with gap at most 2.6e-7.
- **Rows and Δ.** All 25 stored and chosen solves reproduce L, gap and held-out bytes, with status `optimal` and 0 warnings. The largest sum of gaps on any row is 1.5e-7, against a smallest gain of 0.256 nats.
- **Label.** H1 reads no identity case, and dropping both identity substitutions still gives `go`.
- **What remains.** Failed solves can hide a better point on identity fits (finding 3). That only keeps M a lower bound.

**Fix:** replace the hand line with these points.

### 5. BLOCKING: required content is missing from the note, and the LOG has no stop point

All values below come from `$RUN $RT/b_verdict.py`, `$RUN $RT/i_more.py` and `python3 -I $RT/a_units.py`.

| Item | Rule | Value to add |
|---|---|---|
| Class split | §10, §2.1 | Coordinate 205 baseline + 62 LLM; exhaustive certified 143 + 174; exhaustive uncertified 0 + 1, which is dev-005 s3 AG-o-S/named (identity, G = 180, reading -inf) |
| Capped rows | §10 | 53 rows (52 baseline) on 52 fits; 51 tier 1 searched fits, plus dev-013 s2 AG-o-S/anon, G = 12,000, exact after R2 |
| Not-applicable fits | §10 | 82 fits (60 B-np, 22 B-lib); 3 rows have no fit |
| ORACLE search misses, apart | §10 | dev-015 s3: search gain 5.63357, reading gain 1.10531, truth ψ kept, Δ -0.00096; dev-009 s3: 0.31382, ψ source search, Δ +0.00124 |
| `stored_uncertified` listed | §6 | dev-003 s1 AG-c-S/named, searched, call none; fit-level flag false, its own point certified |
| S2 rows | §10, §4 | dev-019 s2 exp to softplus, -0.86227 to -0.87085, Δ -0.00858, out of dictionary; dev-016 s2 softplus to exp, -0.78476 to -0.78992, Δ -0.00516, in dictionary |
| S4 limits | §4 | too-easy share ≥ 0.7; too-hard every system < 0.1; C1 margin > SE, zero underfit flags, no -inf; C2 p < 0.05 and CI low > 0; C3 median ≥ -0.01 |

```
$ grep -n "2026-11-05" docs/v2/LOG.md | head     # no output
```

Section 10 requires any departure in `docs/v2/LOG.md` as a stop point. The LOG has no entry for this run's result.

### 6. Not blocking: counts reconcile only once labelled

- The stratum table sums to 131 beside "Searched 143 rows, 140 fits". It is tier 1 only; the 9 tier 2 fits are `groups_a_b` +2, `id-24000` +1, `large-softplus` +6.
- The label line's 310 / 140 / 1 cover all fits, and the statement's 184 / 131 / 0 cover baseline fits. Neither says so.
- "Capped 52" includes one exact fit.

### 7. Not blocking: C2's second condition and C1's margins are not shown

C2 passes only if the CI's lower end is above 0. Neither `decision.json` nor the note carries that.

```
stored C2 ... ci 0.014792505887358457 0.030626581033161644 passed True
corrected C2 ... ci 0.0134072923559961 0.03036450265337871 passed True
stored C1 thinnest [(0.02173559760251412, 'dev-002', ...)]     corrected: identical
```

### 8. Not blocking: S2 shows a better training fit can be explosive

On dev-019 s2 the reference's exp fit scored about -3e11 on validation, against about -471 for R0's, and that is what flipped the link. The note says only "differs 2". Add a line, because "better fit" is not the same as "better model".

```
S2 differs ('dev-019', 2, 'B-sparse') ... r0 [['exp', '-0x1.d695c1b6ce5e1p+8', True], ...] ref [['exp', '-0x1.138c5fc151e6ap+38', True], ...]
```

### 9. Not blocking: context missing beside two tables

- The note should carry the prereg's line that dev-015 s3 is not blind, since H1 depends on group B.
- §5's per-system table should carry the no-comparison sentence. Exposure differs by system: 10 of B-sparse's 11 misses are at G > 3,456,000.

### 10. Not blocking: the LOG's "before any number exists" is true only for pilot units

The generator was committed at 12:17:04Z. By then groups A and D were finished and the hunt was running, but the first pilot unit came at about 13:25Z, and no session read those records. No effect on the note.

## Checked and sound

- **Hashes.** F, R, prereg, script and test all match the LOG, as does the split (`31c18f10…`). The worktree is clean at 5e5a169.
- **Guards replay clean.** R reached origin/v2 at 11:46:40Z, before launch.
- **Launch.** `--nice=10 -p CPUQuota=200% -p RuntimeMaxSec=216000 -p MemoryMax=5G -p OOMPolicy=continue`, BLAS variables at 1, 2 workers (session transcript). `MemAvailable` 10.9 GiB (JOURNAL).
- **Stage order.** `group_a 6, group_d 1, hunt 623, group_b 1, tier1 195, s2 51, tier2 59`.
- **Budget.** Ceiling 84, stop at 78, both unreached. Clock stops all zero, 0 retries, one `run_start`.
- **Records.** `units.jsonl` equals the journal's job records, sorted. `-primal` agrees with L to 4.5e-13 on all 794 values, and all 115 exact fits have `n_points` equal to G.
- **Hand lines.** CPU, wall time, LEDGER and S3 are correct. All six data files in S3 hash equal to local, and `driver.lock` is empty at both ends.
- **`journal.jsonl`.** A search of every session transcript touched since the day before launch found no command naming a record file's contents during the run. Your hand line can say so.
- **Store use between F and launch.** Only `ls -la` and path greps.

**Spot-checks, by my own computation:**
- R0 rerun: dev-012 s3 B-sparse (largest Δ), dev-015 s3 planted-hint, dev-019 s3 AG-o-S/anon and dev-002 s1 B-rand all byte-equal on point, L, gap and reading.
- ORACLE dev-015 s3 rerun: truth ψ kept, byte-equal.
- Enumeration on a fresh profile: the record's chosen point is the certified arg max on dev-019 s3 (G = 1,200) and dev-015 s3 planted-hint (G = 10,368).

```
('dev-019', 3, 'AG-o-S/anon') ... argmax [4, 2, 2, 0, 2] record chosen [4, 2, 2, 0, 2] eq True  certified points above stored 6
('dev-015', 3, 'planted-hint') ... argmax [5, 0, 1, 0, 4, 5] record chosen [5, 0, 1, 0, 4, 5] eq True  certified points above stored 13
```

## What I could not check

- **R1, R2 and hunt reruns.** Not rerun on the reserved roots, as instructed, so `units.jsonl` byte identity on a rerun is not shown. R1 is supported by byte-exact refits of its results and 29 fast script tests at R. dev-019 s2's explosive fit is taken from the record.
- **Exhaustive class.** The 317 rows rest on the code argument. Neither the run nor I reran them.
- **The other 49 uncertified points.** I enumerated only 21 of 70, and could not show whether any failed point hides a better fit.
- **Warning-to-job mapping.** It rests on a dispatch simulation, which left 35 of 276 unplaced. Stage counts are from clock windows, so a job near a boundary could shift them. True inaccurate-solve counts are not recoverable.
- **Unit properties.** The unit is gone, so launch flags come from the transcript. Reads outside Claude sessions cannot be excluded.
- **Enumeration method.** I stopped a slow brute force over public `fit` and used `_Profile` instead. Public `fit` is pinned only at stored and chosen points.
- **Out of scope.** Curves and selection effects, per the prereg. I read PILOT.md only where cited here.

**Process notes.**
- The guard hook blocked one of my commands, an inline edit of a scratch script that named a `.claude/` path. Nothing in it ran, and I rewrote the scratch file plainly.
- Short read-only checks overlapped the background job, so two nice'd processes ran briefly.
- I edited no tracked files, and nothing was written to the records directory or the store.

Scratch scripts and outputs are in `/home/ubuntu/lanes/sciagent/.cache/drafts/s31/rt/`.

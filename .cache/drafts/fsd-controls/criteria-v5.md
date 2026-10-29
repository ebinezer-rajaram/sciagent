# Positive controls for the at-stop rule (FSD, truthFSD, telling): preregistered criteria, v5

Theorist, 2026-10-29. Self-contained: no earlier version is needed to read or implement it. Supersedes v1–v4 (`criteria-v1.md` … `criteria-v4.md`; v4 sha256 42828142…). Fixes `redteam-s17d.md` items 1–4, settles 5, 7, 8 and 10, and defers A2 (item 6). Item 9 (JOURNAL wording) is the coordinator's. No control stream has been drawn. Evidence, all in $D: `probe*-out.txt`, `power2-out.txt`, `power3-out.txt`, `a2band-out.txt`, `bench-out.txt`, `cost5-out.txt`, `random-picks.txt` (sha256 33f80c78…).

## 0. Definitions

**Paths.** $R = /home/ubuntu/lanes/sciagent (branch v2); $D = $R/.cache/drafts/fsd-controls; $WT = $R/.claude/worktrees/fsd-dist at bff61ecba3daed876e55896e329000704fb9626b (origin/p4-deltas), read-only. `controls.py` imports only: the standard library, numpy, `sciagent` and `environments` from $WT/src (checked at start through each module's `__file__`), and modules in $D. Nothing from elsewhere in $R. The parity fixtures are copied into $D/parity/ (§7).

**Menus.** `design_menu(CHANNELS)` gives 8 designs, indexed: burst 0, inject_sign_high 1 (e1), inject_sign_low 2, inject_size_high 3, inject_size_low 4, clamp_release 5, silence_release 6, censor_marked_burst 7. `diagnostic_menu` on the observational log of `World(truth, s, truncate=True)` gives 35 diagnostics. A diagnostic's label is `DiagnosticChoice.label` (name plus channel arguments); mean_rate is d = 0. Two labels match iff they are equal (`telling.observed`).

**A run.** `run_experiment_truncating(structure, psi, coef, CHANNELS, mark_sampler, design.experiment, rng, max_events=POLICY_MAX_EVENTS)`, with the cap 15,000. A run has one of four outcomes:
- complete: τ is None, and the values are read on the whole dataset;
- truncated: τ is set, and the values are read on `clip_to_analysis(ds, τ)`;
- cut0: `clip_to_analysis` raises ViewError. This counts as truncated (not stopped), with all 35 values None;
- stopped: `policies.STOPPED` is raised, and there are no values.

Values come from `diagnostic_value`. Any other `catalogue.DiagnosticError`, or a non-finite value, is stored as None and counted in the run's `none_other` and `nonfinite` fields. `hellinger2` raises on non-finite values (battery.py:359-360), so none ever reaches it.

**Tables.** He²(e, d) = `battery.hellinger2(side-0 values, side-1 values)` under a simulation rule:
- (a) a truncated or stopped run is None on every diagnostic;
- (b) truncated and stopped runs are dropped, and a design where either side keeps no run has every cell set to 0;
- (c) a truncated run keeps its clipped values, and a stopped run is None.

Telling's argmax is d-major (`Telling.from_table`: the first d whose column maximum strictly exceeds every earlier column's, then the first design attaining it). truthFSD's and FSD's argmax is e-major (`policies.choose`: the first (e, d) in menu order at the maximum). An *excluded* design is removed from the argmax, not zeroed. If every design is excluded, the exclude-all rule applies: there is no pick and no d\*, the unit scores a miss for that arm and stays in every comparison (a concordant miss in `policy_separation`, controls_p4.py:360-362), and no exception is raised. `Telling.from_table` is never called on such a table.

**K-rules (telling, side 0 truth / 1 member, R = 32; truthFSD, comparator = member, reference = truth, R = 16).**
- K0: telling (b), truthFSD (a).
- K1: both (c).
- K2z: both (c), and mean_rate's cell is set to 0 on any design where either side has a truncated run.
- K3a: both (c); each arm excludes a design where truth or member has at least 1 truncated run among *its own* replicates.
- K3h: as K3a, with "more than half" in place of "at least 1".
- K4: as K3a, counting truncated or stopped runs.
- K5: (c); X = the designs where truth or member has at least 1 truncated or stopped run among telling's 32. Telling and truthFSD both exclude exactly X; truthFSD's own runs never decide exclusion, and it keeps its own truncated runs on designs outside X.
- K5 is shared but depends on telling's R (P(exclude) = 1 − (1 − π)^32). Telling's d\* is identical under K4 and K5.

**F-rules (FSD: parametric vs reference, R = 16, e-major).**
- Fa: (a).
- Fc: (c).
- Fz: (c), with mean_rate set to 0 where either side truncates.
- Fx: (c), and a design is excluded where either side has at least 1 truncated or stopped run among its 16.

**Streams.** Controls C ∈ {"ctlA1", "ctlB"}.
- Telling (test): `scorer_seed(TELLING, C, s, side, e, r)`, r < 32.
- truthFSD and FSD (test): `policy_seed(s, k, idx, r)`, decisions k = 0..7, idx = PARAMETRIC_INDEX (member or parametric) or REFERENCE_INDEX (truth or reference), r < 16. There is no design index, so replicates share random numbers across designs (CRN).
- Random (fixed now, `random-picks.txt`): `policy_seed(s, k, RANDOM_INDEX, 0)`; e, d = divmod(rng.integers(8·35), 35).
- Calibration, rung j: per-design set `scorer_seed(TELLING, C+"-cal-r"+j, 0, side, e, r)` and CRN set `scorer_seed(TELLING, C+"-crn-r"+j, 0, side, r)`, r < 256; bootstrap generator `generator(scorer_seed(TELLING, C+"-boot-r"+j, 0))`. B's reference check: `scorer_seed(TELLING, "ctlB-ref-r"+j, 0, 0, 1, r)`, r < 256.
- Seeds: A1 101–115, B 301–315.

**Ground-truth position.** telling.py:14-24: a design where a side stops on every run "says the design is telling but not which diagnostic is". Here this is extended to truncation. The SPEC §4.2 choice is the owner's.

**Calibration (per rung).**
- E_cal = the designs where any calibration run of either side truncates, cuts at 0 or stops.
- Subsamples are without replacement, drawn from the rung's bootstrap generator in a fixed order: D\*₀ selection, p₃₂, p₁₆, p_F (B only), then the 99% clause. There are 1,000 of each.
- D\*₀ is chosen on per-design indices 0–127: 32-of-128 subsamples, d-major over designs outside E_cal, taking the fewest labels by decreasing frequency that reach a total of at least 0.95.
- Held out (feeding P2, P3 and P3-B): p₃₂(ℓ) from per-design indices 128–255 (32-of-128); p₁₆(ℓ) from CRN indices 128–255 (16-of-128, the same indices for every design, e-major); h(ℓ) = 1 − (1 − p₁₆(ℓ))^8; h = Σ_ℓ p₃₂(ℓ)·h(ℓ).
- The 99% clause: in at least 990 of 1,000 16-vs-16 subsamples (CRN indices 128–255), no cell outside the planned truncated rows is exactly 1.0.
- These are plug-in estimates. A held-out estimate of at least 0.95 occurs with P about 0.40 when the true coverage is 0.90 (128-run pool; redteam-s17d 5). The powers below are at the estimate, not guarantees.

**Preconditions.**
- P1: the truncation pattern is exactly as built in both sets (256/256 truncated where planned; elsewhere no truncation, cut0 or stop; the member never).
- P2: \|D\*₀\| ≤ 2; every label in D\*₀ carries the planted channel (`[sign]`; `[size]` on B's size rung); mean_rate ∉ D\*₀; held-out coverage Σ_{ℓ∈D\*₀} p₃₂(ℓ) ≥ 0.95.
- P3 (A1): h ≥ 0.95; `pass_ii(15, p₃₂, p₁₆)` ≥ 0.95; `pass_iii(101..115, p₃₂, h(·))` ≥ 0.95 (`power2.py`: exact, clustered, given random's fixed picks); and the 99% clause.

**Rungs and their reading (redteam-s17d 3, 7).**
- Every rung of A1 and of B is calibrated, and every rung's P1, P2 and P3 (or P3-B) values go into `D0-<C>.json`.
- The test uses the first rung, in ladder order, that passes all its preconditions.
- A rung that fails P1 or P2 is unbuildable. The control is VOID only if every rung is unbuildable.
- A rung that passes P1 and P2 but fails P3 (A1) or P3-B (B) is a **measured result**:
  - in A1, an R = 16 shortfall: truthFSD at R = 16 would not reproduce telling's label at the required rate;
  - in B, a cost of Fx.
- If no rung passes everything but at least one is buildable, the test is not run, and the buildable rungs' P3 or P3-B values are the control's result.

## 1. Control A1 (known d\*)

**Build.**
- Truth T_A: `link=identity; Excite(ExpK, One, sign=+) + Excite(ExpK, One, all)` (dev-002's structure), exp_rate 1.0 on both kernels, (θ₊, θ_all) = (0.5, 0.6), θ₀ = 0.15. Branching is 0.85 observational, 1.10 under e1, 0.60 under inject_sign_low.
- Member M_A: library HAWKES `link=identity; Excite(ExpK, One, all)`, θ = 0.85, θ₀ = 0.15, exp_rate 1.0. It is fixed, and serves as telling's member and truthFSD's comparator.
- Ladder: θ₊ = 0.5, 0.4, 0.6, with θ_all = 0.85 − θ₊/2.
- Planned truncation: the truth on e1 only.
- Probes (probe-only ids):
  - T_A truncates on e1 in 88/88 runs and nowhere else (τ 833–967 over probe3's 64);
  - the top uncapped cell is rate_after_mark_slope[sign] (He² 0.990, R = 8);
  - at θ₊ = 0.6 the top cell is spectral_peak_frequency (`probe-out.txt`, `probe3-out.txt`).

**Random arm's fixed picks, seeds 101–115** (seeds whose 8 picks include the label): mark_mean[sign] 104, 106, 112, 114; mark_skewness[sign] 102, 105, 109, 111; mark_autocorrelation[sign] 115; mark_gap_correlation[sign] 101, 102, 104, 105, 112, 113; next_gap_after_large_mark[sign] 102, 104, 106, 113; rate_after_mark_slope[sign] 106, 107, 108, 110, 114; mark_intensity_correlation[sign] none; mark_cross_correlation[sign,size] 101, 103, 105, 112; mean_rate none.

**Predictions.**
- K0 fails: truthFSD's atom row on e1 gives (e1, mean_rate) at every decision.
- K1 fails (i): telling gives (e1, mean_rate), forced because cap/τ ≥ 7.5. K1 passes (iii) on the cap.
- K2z fails (i), probably with (e1, mark_mean[sign]).
- K3a, K3h, K4 and K5 pass and cannot be told apart.

**Criteria** (a rule passes A1 iff all three hold; evaluated on the test streams of the tested rung):
- (i) telling's label ∈ D\*₀ on ≥ 12 of 15 seeds;
- (ii) truthFSD's label = telling's on ≥ 84 of 120 (seed, decision) pairs;
- (iii) `policy_separation(truthFSD, random)` passes (one-sided exact McNemar, α 0.05).

**Fix 4.** Any failure of (i), (ii) or (iii), including (iii) alone, fails A1 for that rule. There is no rerun and no extension, and the report gives D0's pass_ii and pass_iii beside the result. At the P3 floor each of (ii) and (iii) fails with P ≤ 0.05. If the three were independent, all three would pass with P ≥ 0.9945 × 0.95 × 0.95 = 0.8975; by the union bound, P ≥ 0.8945 regardless.

## 2. Control B (the dev-005 shape)

**Build (rungs 0–2).**
- Truth T_B: `link=identity; Excite(ExpK, One, sign=-) + Excite(ExpK, One, all)`, (θ₋, θ_all) = (0.5, 0.6) at rung 0, then θ₋ = 0.4 and 0.6 with θ_all = 0.85 − θ₋/2; θ₀ = 0.15. It truncates on inject_sign_low (e2) only.
- Member M_B = M_A, which is also FSD's parametric side.
- Reference Ref_B: T_A at θ₊ = 0.5 on e1, and the rung's truth on every other design. It truncates on e1 (the artefact: neither truth nor member does there) and on e2 (as the truth does).
- Ref check: T_A must truncate on e1 in 256/256 runs (`ctlB-ref-r0`); otherwise θ₊ = 0.6 (`ctlB-ref-r1`); otherwise B is VOID.

**Rung 3 (size-gated).**
- T_Bs = `link=identity; Excite(ExpK, One, all) + Excite(ExpK, Mark(size), all)` (canonical order), θ = (0.35, 0.5), θ₀ = 0.15. Branching is 0.85 observational, 1.35 under inject_size_high (e3).
- Ref = T_A on e1 and T_Bs elsewhere. Here "e2" below reads "e3", and P2's channel is `[size]`.
- Probe: it truncates 8/8 on e3 only; its top cells are `[size]`, with burst first (He² 0.978) and e1's best only sixth (`probe4-out.txt`).

**Why the artefact stays on e1.**
- It must come before the real truncation in menu order, or Fc would pick the real design by menu order.
- The only earlier design is burst. Under the identity link, a burst's 10 forced events are immigrants (10/(1 − n) expected events), so no subcritical linear model truncates on burst alone.

**P3-B.** p_F(ℓ) is Fx's per-decision label law (CRN indices 128–255, designs outside {e1, e2}); h_F = Σ_ℓ p₃₂(ℓ)·(1 − (1 − p_F(ℓ))^8). P3-B requires h_F ≥ 0.95, `pass_iii(301..315, p₃₂, h_F(·))` ≥ 0.95, and the 99% clause.

**Random arm's fixed picks, seeds 301–315:**
- `[sign]` labels: mark_mean[sign] none; mark_skewness[sign] 303, 305, 312, 315; mark_autocorrelation[sign] 301, 302, 310, 315; mark_gap_correlation[sign] 313; next_gap_after_large_mark[sign] 301, 308, 314; rate_after_mark_slope[sign] 302, 303, 309, 310; mark_intensity_correlation[sign] 312; mark_cross_correlation[sign,size] 315.
- mean_rate: 302, 306, 308, 315.
- `[size]` labels: mark_mean 304, 306, 307; mark_dispersion 301, 303, 307; mark_skewness 302, 308, 309, 314; mark_autocorrelation 309, 311, 312; mark_gap_correlation 301, 304, 311, 312, 315; next_gap_after_large_mark 305, 307, 308, 310, 313; rate_after_mark_slope 309, 314; mark_intensity_correlation 306, 309, 311.

**Predictions.**
- FSD: Fa and Fc pick (e1, mean_rate). Fz picks (e1, mark_mean[sign]), probably. Fx avoids e1 and e2.
- Telling: K0, K3a, K3h, K4 and K5 give a label in D\*₀; K1 gives (e2, mean_rate); K2z gives (e2, mark_mean[sign]), probably.

**Criteria** (per pair of FSD rule and telling rule; the pair passes iff all three hold):
- B1: FSD's design ≠ e1 on ≥ 108 of 120 pairs;
- B2: no unit is credited with d\* only through picks on e1;
- B3: FSD is credited on ≥ 12 of 15 units, and `policy_separation(FSD, random)` passes.
- Predicted: Fx with the telling of K0, K3a, K3h, K4 or K5 passes. Fa, Fc and Fz fail B1. Fa or Fc with K1 fails B2. Fx with K1 fails B3.

**Outcome table (redteam-s17d 7), using §0's rung reading.**
- A θ₋ rung that passes P1 and P2 and fails P3-B: Fx misses telling's label when the artefact sits on the truth's top design.
- The size rung passing P1 and P2 and failing P3-B: Fx misses it even with the artefact off the top design (a stronger cost).
- Mixed failures: the P3-B failures of buildable rungs are reported; unbuildable rungs are listed.
- A B1–B3 pass on the size rung holds only for truths whose best design is not the artefact design.

## 3. What is construction, what is gated, what is measured

**By construction.** These restate the §4.2 position, and none is evidence for option 2:
- A1: K1 and K2z failing (i) (D\*₀ excludes E_cal, and P2 bans mean_rate); K0 failing (the atom row); the exclusion rules passing (i).
- B: B1 for Fx (its own exclusion) and for Fc (the forcing fact); B2 for K1 (label-only matching).
- The stop case: on a design where every truth run stops, K3a's row is all 1.0 (battery.py:361-374), so `from_table` returns mean_rate. K4 and K5 exclude the design.

**Gated.** These passes mostly restate calibration:
- A1's (ii) and (iii), and B3. P3 and P3-B are their own success probabilities, so a pass tests how calibration transfers to the test streams.

**Measured.**
- The per-rung P3 and P3-B values in D0: the R = 16 shortfall and the cost of Fx.
- On the test streams, whether the calibrated rates transfer: (i)–(iii) and B1–B3.

None of this can favour option 2 over option 1.

## 4. A2 (K4 against K5) is deferred

A2 is not part of this preregistration. It drew no verdict, it had no power calculation, and it would cost about 2.85 CPU h (`cost4-out.txt`). If the owner picks option 2 and K4 against K5 matters, a later amendment preregisters it, starting from `criteria-v4.md` §1 and `probe4`/`probe5`. Until then:
- telling's d\* is identical under K4 and K5;
- they differ only in truthFSD, so only in control 5.

## 5. Procedure: sequence, gate, deviations (redteam-s17d 1, 2)

**Sequence.**
1. The coordinator commits v5 as the design record.
2. The engineer writes `controls.py`, `test_controls.py` and `parity/` in $D to §7. `controls.py` is never committed before F, because the gate requires F to be the only commit that ever touched it. The tests pass, and their output is `test_controls-out.txt`.
3. A code red team (a fresh reviewer) checks `controls.py` and its tests against v5. Its fixes go in before F; if a fix needs this file to change, that is v6 and a design red team.
4. F, one commit (`git add -f`), holds every file in $D except the run outputs: `__pycache__/`, `*-err.txt`, `cpu-total.txt`, `cpu-total.lock`, `cpu-ledger.csv`, `STOP`, `D0-*.json`, `runs-*.jsonl`, `score-*.*`. F's message carries `criteria.md sha256: <h>` and `controls.py sha256: <h>`.
5. F is pushed (`git push origin v2`). The coordinator then adds a `docs/v2/LEDGER.csv` row with F's sha, the 7.6 CPU h ceiling and $0, and creates `cpu-total.txt` containing `0.0`.
6. calibrate writes `D0-<C>.json`. D is a commit containing only that file, and it is pushed.
7. test writes `runs-<C>.jsonl`.
8. score writes `score-<C>.json` and `score-<C>.txt`.

**Gate** (every phase; any failure raises a typed error naming the check).
- `--freeze-commit F` is given explicitly; test and score also take `--d0-commit D`.
- The sha256 of `criteria.md` and `controls.py` **on disk** equal the lines in F's message.
- `git -C $R diff --quiet F -- <frozen>` (working tree against F), where `<frozen>` = `git -C $R ls-tree -r --name-only F -- $D`.
- `git -C $R log --format=%H -- $D/controls.py` prints exactly F, and `git -C $R log --format=%H F..HEAD -- <frozen>` is empty.
- `git -C $R status --porcelain --ignored -- $D` lists only `!!` entries matching the run-output patterns in step 4.
- `git -C $WT rev-parse HEAD` = bff61ecba3daed876e55896e329000704fb9626b, and `git -C $WT status --porcelain --ignored -- src` lists nothing but `!!` entries ending in `__pycache__/`. Those exist today from earlier imports (src/sciagent/, src/environments/ and src/environments/pointproc/, checked 08:29 UTC). Run with `PYTHONDONTWRITEBYTECODE=1`.
- test and score also need:
  - F a strict ancestor of D (`merge-base --is-ancestor F D`, F ≠ D);
  - `git -C $R show --name-only --format= D` = exactly `$D/D0-<C>.json`;
  - `git -C $R diff --quiet D -- $D/D0-<C>.json`;
  - `git -C $R log --format=%H D..HEAD -- $D/D0-<C>.json` empty.
- The environment: numpy and scipy versions and the sha256 of `$WT/uv.lock` are recorded in D0, and test and score refuse if they differ.
- D0 and the score files record F, D (in score), the sha256 of `criteria.md`, `controls.py`, `uv.lock` and (in score) `runs-<C>.jsonl`, $WT's HEAD, and the library versions.

**Deviations.**
- After F, any change to a frozen file, including a `controls.py` bug fix, needs a new version of this file, a red team and a new F′, recorded in LEDGER.csv.
- A control with no test-stream draw yet restarts from calibrate under F′.
- A control that has any test data keeps its original-F result as the preregistered one (or INCOMPLETE). Anything run under F′ is reported as an exploratory deviation, never as the preregistered result.

## 6. CPU: ledger, ceiling, stop (redteam-s17d 8)

**Estimate** (`cost5-out.txt`; medians of per-run CPU, so an estimate, not a bound): expected A1 2.57 + B 3.32 = 5.89 CPU h; worst 2.81 + 3.78 = 6.58 CPU h. **Ceiling 7.6 CPU h** (worst + 15%). The stop rule below enforces it, with an overshoot of at most one step plus 30 CPU s per process.

**Ledger.**
- `cpu-total.txt` holds one decimal number of CPU seconds. It is created as `0.0` at step 5 and never reset or deleted. If it is missing or unparseable, the process refuses to start; there is no silent reset.
- Update: take an exclusive `fcntl.flock` on `cpu-total.lock`; read the total; add this process's `getrusage(RUSAGE_SELF)` plus `RUSAGE_CHILDREN` (user + sys) since its last update; write to a temporary file and `os.replace` it; release the lock. Then append an audit line to `cpu-ledger.csv` (phase, control, unit, delta, total, pid, UTC time), which is never re-read.
- Updates happen after each run, at least every 30 CPU s inside non-simulation loops, and at exit.

**Stop.**
- Before each step, read the total. At or above the ceiling, write `STOP` (holding the total and the time).
- Every process checks `STOP` before each step. On STOP it updates the ledger and exits with status 3 (INCOMPLETE).
- score is charged but exempt from STOP (about 0.003 CPU h per control, `cost5.py`). It runs only for a control whose test phase finished; otherwise that control is INCOMPLETE.
- Resuming after STOP needs a new LEDGER.csv row raising the ceiling, by the coordinator. A resumed test redraws nothing already stored: runs are keyed and the streams are fixed.

## 7. Implementation contract (`controls.py`)

**CLI.** `controls.py --worktree $WT --freeze-commit F [--d0-commit D] --control {A1,B} --phase {calibrate,test,score} --cpu-ceiling-h 7.6`. Seeds, R, ids, rungs and thresholds are constants from this file, not flags.

**Outputs.**
- JSON is canonical: `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)` plus `"\n"`. Floats are Python's shortest round-trip repr, and non-finite values are never written (they become None).
- `runs-<C>.jsonl` has one line per (seed, arm ∈ {telling, decision k}, design, side, replicate): outcome, τ, n_events, the 35 values, none_other and nonfinite.
- Resumable: a torn last line is cut, and stored keys are skipped.
- `score-<C>.txt` is a fixed-order rendering of `score-<C>.json`.

**Determinism.**
- Before F: a real-simulator unit on the probe-only id `ctl-det` is run twice, and the outputs are compared with `cmp`.
- After test: score reruns seed 101's (or 301's) telling runs on design 0 and compares them byte for byte with the stored lines.

**Parity.** `$D/parity/` holds copies, with source sha256s in a manifest, of:
- the `fsd-trunc/trunc-s1-d0.jsonl` lines and `.truths.jsonl` records for dev-002, dev-005, dev-006 and dev-015;
- the `fsd-rule/candidates.json` picks for them.
`controls.py`'s K0–K4 must reproduce those picks with 0 mismatches.

**Stub tests** (`test_controls.py`), on stub tables and stub run lists:
- K0–K5 and Fa, Fc, Fz, Fx exactly as §0, including: K5's X shared and truthFSD keeping its own truncated runs outside X; Fx skipping a design on 1 truncation or stop in 16; Fz zeroing only where a side truncates; K3a against K4 on an all-stopped design.
- Exclude-all for every arm, telling included; cut0 and non-finite values.
- Each gate refusal in §5, in a scratch git repo.
- Ledger: no lost update with two processes; loops charged; a missing total refused; STOP firing.
- Canonical JSON bytes.
- X never reaching `FSDPolicy`, the tool layer or any record an agent can read (invariant 2).

## Not proved or not covered

- He²(mean_rate) = 1.0 on truncated rows is measured, and proved only for the exact He².
- The K2z and Fz labels at R = 32, and the size rung beyond R = 8, are unprobed.
- The calibration powers are plug-in estimates (§0).
- Neither control uses a real Wiener–Hopf reference, a fitted B-lib member or a B-sym top fit.
- Only one cap, horizon and environment are covered.

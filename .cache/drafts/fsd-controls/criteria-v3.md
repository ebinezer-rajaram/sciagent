# Positive controls for the at-stop rule (FSD, truthFSD, telling): design and preset criteria, v3

Theorist, 2026-10-29. Design only: no control stream has been drawn. Supersedes `criteria-v2.md` (sha256 178e1147…; fixes from `redteam-s17b.md`, N1–N8) and `criteria-v1.md` (21a01721…; `redteam-s17.md`). Evidence: `probe*-out.txt` (probe1–3 223 CPU s, probe4 135 CPU s), `power-out.txt`, `power2-out.txt`, `power3-out.txt`, `a2band-out.txt`, `cost3-out.txt` and `random-picks.txt` (sha256 33f80c78…), all in this directory. Code cited is worktree `fsd-dist` (bff61ec), read-only.

## 0. Common ground

**Rules.** K0, K1, K2z, K3a, K3h, K4 as in v1. FSD rules Fa, Fc, Fz, Fx as in v1 (Fc is FSD under K1, K3a, K3h, K4, K5; Fz is FSD under K2z).

**K5, restated (fix 2).** (c); X(truth, seed) = the designs where the truth or the member truncates or stops on at least 1 of *telling's own* 32 replicates; truthFSD skips exactly X and has no exclusion of its own. K5 is **shared, and it depends on telling's R**: P(exclude) = 1 − (1 − π)^32, so the 1-of-R cliff remains, at telling's R. Telling's d\* is **identical under K4 and K5** (both exclude on telling's 32 runs): the two differ only in truthFSD, so only in control 5 and in no C5 rate. Under (c), truthFSD keeps its own truncated runs on designs outside X; A2 counts them. Chosen over a fixed set of framework-side calibration runs because (1) no finite-sample exclusion is R-independent: R_X dedicated runs move the cliff to π ≈ 1/R_X, they do not remove it; (2) that variant changes telling's d\* and the C5 rate, and leaves truncated runs inside kept designs for telling as well; (3) it costs 2 × 8 × R_X runs per (truth, seed), 0.16 CPU h per unit at R_X = 256 (`cost-out.txt`, A1 calibration). Consequence for the owner: option 2's "computed once, independently of R" (JOURNAL.md:1517-1518) should read "computed once from telling's replicates and shared".

**Rules not yet covered by code.** K5, Fx and Fz are not in `candidates.py` (its KEYS, :202, are K0, K1, K2a, K2z, K3h, K3a, K4). The stub-table tests in §5 come before any test phase.

**Harness.** As v1: each run once via `run_experiment_truncating` (cap 15,000; stops `policies.STOPPED`), read on `view.clip_to_analysis`, rules applied afterwards with `hellinger2`, `choose` (e-major), `Telling.from_table` (d-major); hit = equal label (`telling.observed`); random = `RandomPolicy`'s draw; control 5 = `controls_p4.policy_separation`. **ViewError** (a run cut at time 0): counts as truncated, with all 35 diagnostics None (as `measure_trunc.py`).

**Streams and ids (fix 3).** C ∈ {ctlA1, ctlA2, ctlB}; rung j = 0, 1, 2.
- Telling, test: `scorer_seed(TELLING, C, s, side, e, r)`, r < 32. The id does not depend on the rung: test streams are drawn only after the rung is fixed.
- truthFSD and FSD, test: `policy_seed(s, k, PARAMETRIC_INDEX | REFERENCE_INDEX, r)`, r < 16, k = 0..7. There is no design index (policies.py:665-666), so replicate r shares random numbers across designs (CRN).
- Random: `policy_seed(s, k, RANDOM_INDEX, 0)`, no truth id: its picks are fixed now (`random-picks.txt`).
- Calibration, rung j: per-design set `scorer_seed(TELLING, C+"-cal-r"+j, 0, side, e, r)` and CRN set `scorer_seed(TELLING, C+"-crn-r"+j, 0, side, r)`, r < 256; bootstrap `generator(scorer_seed(TELLING, C+"-boot-r"+j, 0))`.
- B's reference check: `scorer_seed(TELLING, "ctlB-ref-r"+j, 0, 0, 1, r)`, r < 256. A2: search step at branching v `scorer_seed(TELLING, f"ctlA2-pi-{v:.5f}", 0, 0, 1, r)`, r < 512; full calibration ids `ctlA2-cal`, `ctlA2-crn`, `ctlA2-boot`; Δ bootstrap `generator(scorer_seed(TELLING, "ctlA2-dboot", 0))`.
- Menu: `World(truth, s, truncate=True)`. Seeds: A1 101–115 (101–110 kept from v1), B 301–315 (301–310 kept), A2 201–224.

**Ground-truth position.** As v1 (telling.py:14-24, extended to truncation). The owner's SPEC §4.2 choice is not made here.

**Calibration (A1, B).** E_cal = designs where any calibration run of either side truncates or stops. p₃₂(ℓ): share of 1,000 subsamples of 32 of the 256 per-design-set replicates whose d-major argmax over designs ∉ E_cal has label ℓ. p₁₆(ℓ): the same from the CRN set, 16 indices shared by every design (CRN kept), e-major. D\*₀ = fewest labels by decreasing p₃₂ with Σp₃₂ ≥ 0.95. h(ℓ) = 1 − (1 − p₁₆(ℓ))^8 (decisions use independent streams); h = Σ_ℓ p₃₂(ℓ)·h(ℓ).

**Preconditions.**
- P1: the truncation pattern is exactly as built (256/256 where planned, 0 elsewhere, 0 stops), in both stream sets.
- P2: \|D\*₀\| ≤ 2; every label in D\*₀ is a `[sign]` diagnostic; mean_rate ∉ D\*₀.
- P3 (fix 4, N4; A1 in full; B uses only its 99% clause, plus P3-B in §2): h ≥ 0.95; `pass_ii(N, p₃₂, p₁₆)` ≥ 0.95 and `pass_iii(seeds, p₃₂, h(·))` ≥ 0.95 (`power2.py`, frozen with this file; exact, clustered, given random's fixed picks). (i) then passes with P ≥ 0.9945 (Σp₃₂ ≥ 0.95, `a2band-out.txt`), so each criterion passes with P ≥ 0.95 at the floor; at least 99% of 16-vs-16 subsamples have no cell exactly 1.0 outside the planned truncated rows.
- A failure moves to the next rung; all rungs failing makes the control **VOID** (a finding about He² resolution, not a verdict on any rule).

**Freeze and gate (fix 6, N5).** The coordinator freezes by one git commit (the freeze commit, F) of this directory's design files: `criteria.md`, `power2.py`, `probe.py` (power2.py imports it to derive the 35 menu labels from `diagnostic_menu`; no gitignored measurement file is read), `randpicks.py`, `random-picks.txt`, `power2-out.txt`, `power3.py`, `power3-out.txt`, `a2band.py`, `a2band-out.txt`, `cost3.py`, `cost3-out.txt`, `probe2.py`, `probe3.py`, `probe4.py` and their `probe*-out.txt`, `redteam-s17.md`, `redteam-s17b.md`, `criteria-v1.md`, `criteria-v2.md`, and the files earlier versions cite (`power.py`, `power-out.txt`, `cost.py`, `cost-out.txt`, `cost2.py`, `cost2-out.txt`). In short: every file in this directory except `__pycache__/`, `*-err.txt`, `cpu-ledger.csv`, `STOP` and `D0-*.json`. `.cache/` is gitignored (.gitignore:15): use `git add -f`. The frozen sha256 of `criteria.md` is written in F's message. The calibrate phase writes `D0-<C>.json` (rung, E_cal, D\*₀, p₃₂, p₁₆, p_F, π̂, Δ_pred, every precondition value), committed separately in a commit D. The test phase takes `--criteria-sha256 <frozen hash>` and refuses unless sha256(`criteria.md`) equals it, `criteria.md` and `D0-<C>.json` are unchanged at HEAD (`git diff --quiet HEAD --`), and F is a strict ancestor of D (`git merge-base --is-ancestor F D`, F ≠ D).

**Edge cases (fix 8).**
- Test-time P1 breach (a truncation or stop where calibration had none, or a run that does not truncate where calibration had 256/256): scored as observed under every rule and listed by seed. The control is VOID if breaches touch more than 2 seeds.
- Exclude-all (N6; one rule for every rule and arm): an arm whose rule excludes every design makes no pick at that decision and observes nothing; telling with every design excluded has no d\*. The unit stays in every comparison and scores a miss for each affected arm (a concordant miss in `policy_separation`, so the pairing it enforces, controls_p4.py:360-362, holds). No exception is raised. As built this cannot happen: at most 2 of 8 designs are excluded.
- B's reference model on e1 is fixed by B's own rule below, never by A1's rung.

## 1. Control A

**A1 build.** As v1: T_A = dev-002's structure, `link=identity; Excite(ExpK, One, sign=+) + Excite(ExpK, One, all)`, exp_rate 1.0, (θ₊, θ_all) = (0.5, 0.6), θ₀ = 0.15 (branching 0.85 observational, 1.10 under inject_sign_high = e1); M_A = library HAWKES, θ = 0.85, θ₀ = 0.15; ladder θ₊ = 0.5, 0.4, 0.6 with θ_all = 0.85 − θ₊/2. N = 15: at N = 10 and the fixed random picks, (iii)'s worst power over the `[sign]` labels is 0.788 at h = 0.95; at N = 15 it is 0.985 (0.905 at h = 0.90) (`power2-out.txt`).

**Random arm's fixed picks, seeds 101–115** (`random-picks.txt`): mark_mean[sign] 104, 106, 112, 114; mark_skewness[sign] 102, 105, 109, 111; mark_autocorrelation[sign] 115; mark_gap_correlation[sign] 101, 102, 104, 105, 112, 113; next_gap_after_large_mark[sign] 102, 104, 106, 113; rate_after_mark_slope[sign] 106, 107, 108, 110, 114; mark_intensity_correlation[sign] none; mark_cross_correlation[sign,size] 101, 103, 105, 112; mean_rate none.

**A1 predictions** (unchanged from v1): K0 FAIL ((ii) ≈ 0, (iii) b = 0); K1 FAIL ((i) 0/15; it passes (iii) on the cap, since random never hits mean_rate here); K2z FAIL ((i), probable); K3a, K3h, K4, K5 PASS and indistinguishable.

**A1 criteria** (a rule passes iff all three hold):
- (i) telling's label ∈ D\*₀ on ≥ 12/15 seeds (P = 0.9945 at p = 0.95; `a2band-out.txt`);
- (ii) truthFSD's label = telling's on ≥ 84 of 120 (seed, decision) pairs (clustered: 0.969 at p₃₂ .95/.05 with p₁₆ .84/.12, 0.836 in the worst P2-compatible case; hence P3's formula; `power3-out.txt`);
- (iii) `policy_separation(truthFSD, random)` passes.
- K0 caveat as v1: if a test e1 truth run does not truncate (0 of 88 in the probes), it is a P1 breach, and (b) keeps that run.

**A2: K4 against K5, v3 (N1, N2, N8).**
- Family (N1, red team's suggestion with the bisection range moved by the probe): T_A's structure with θ₊ = 0.5 fixed; θ_all bisected on [0.44, 0.49], so the inject_sign_high branching v = 0.5 + θ_all lies in [0.94, 0.99]; θ₀ = 0.75 − θ_all (observational branching θ_all + 0.25, rate about 1); member = HAWKES with θ = θ_all + 0.25 and the same θ₀. The range is moved down from the suggested [0.47, 0.50] because the probe at θ_all = 0.49 truncated on e1 in 5/48 runs, above the band (`probe4-out.txt`, id `probe-A2b`).
- Bisection: at most 6 steps of 512 truth runs on e1 (id `ctlA2-pi-<θ_all:.5f>`); stop at the first θ_all with π̂ ∈ [0.015, 0.030]; step up if below, down if above. The full calibration at that θ_all (256 per side, both stream sets) must give π̂ ∈ [0.0075, 0.040] on its 512 e1 truth runs (P outside = 0.051 at π = 0.015, 0.096 at π = 0.030; `cost3-out.txt`).
- What the probe shows (honestly against N1's request): a₀ ≥ 0.6 is **not** plausible for this family. At θ_all = 0.49, R = 48, the top off-e1 cells are rate_after_mark_slope[sign] (burst 0.882, inject_sign_low 0.880), and a crude a₀ from overlapping subsamples is 0.395, with truthFSD's e-major pick split between spectral_peak_frequency (0.455) and rate_after_mark_slope[sign] (0.415). So v2's P2-A2 (a₀ ≥ 0.6) is dropped. Low a₀ dilutes Δ but cannot bias it: on pairs where K4's and K5's truthFSD exclusions agree, their tables and labels are identical, so Δ accrues only on E4 and E5 pairs.
- Preconditions: P1-A2: in the full calibration nothing truncates or stops off e1, and the member never does. P2-A2 (replaces v2's): Δ_pred (below) has |Δ_pred| ≥ 0.10, and the preset reading is decisive (favours K4 or K5) in ≥ 80% of the simulated tests.
- Δ_pred (N2), computed before any test stream and written to `D0-A2.json`: 200 simulated tests, each with 24 seeds × 8 decisions built by subsampling the full calibration (per seed: 32 per side per design from the per-design set for telling; per decision: 16 shared indices from the CRN set for truthFSD), scored by exactly the rules and reading below; Δ_pred = mean simulated Δ, with the shares of simulated readings favouring K5, K4 and neither. Generator `generator(scorer_seed(TELLING, "ctlA2-sim", 0))`.
- Predicted sign, stated now: Δ > 0. On E4 pairs (telling excludes e1, truthFSD's 16 do not truncate) K4 keeps e1, where the near-critical runs dominate, and leaves telling's off-e1 label; K5 excludes e1 and agrees with probability about a₀. On E5 pairs (telling keeps e1, truthFSD's 16 truncate) telling's label is on e1, K5 keeps the truncated runs and stays on e1, K4 drops e1 and leaves it. **A2's reading is limited to this regime**: a planted truth whose partially truncated design dominates its own row. It is not a general ranking of K4 and K5.
- VOID if: the bisection finds no θ_all; the full-calibration π̂ leaves [0.0075, 0.040]; P1-A2 or P2-A2 fails; fewer than 5 seeds have telling keeping e1, or fewer than 5 excluding it (P = 0.020, 0.002 and 0.023 at π = 0.015, 0.02 and 0.03; `a2band-out.txt`); P1 breaches touch more than 2 seeds.
- Test: seeds 201–224 (N = 24, 192 pairs). This replaces v1's 5 seeds (P(no seed keeps e1) = 0.34 at π = 0.05, redteam-s17.md:18).
- Measured on the same stored runs, for K4 and K5 (K3h and K1 reported too): L_K = pairs where truthFSD's label ≠ telling's (telling's label is the same under both). Exposures E4 and E5 as defined above, with L_K within each and the number of truncated runs K5 keeps. Expected per pair at π = 0.02: E4 0.345, E5 0.145, about 66 and 28 of 192 (`a2band-out.txt`).
- Preset reading (N8): Δ = (L_K4 − L_K5)/192. Interval: nonparametric percentile bootstrap over seeds (resample the 24 seeds with replacement, each seed's 8 decisions together), 2,000 resamples, generator `generator(scorer_seed(TELLING, "ctlA2-dboot", 0))`, 90% interval = `numpy.quantile(Δ*, [0.05, 0.95], method="linear")`.
  - K5 is favoured iff Δ ≥ 0.05 and the interval's lower end is **strictly** > 0.
  - K4 is favoured iff Δ ≤ −0.05 and the upper end is strictly < 0.
  - Otherwise no preference; plumbing decides.
  - The observed Δ is also reported against Δ_pred (inside or outside the interval).

**Stops, K3a against K4/K5:** as v1, by proof (battery.py:361-374, telling.py:88-95); the stub test is in §5.

## 2. Control B (the dev-005 shape)

**Build.** As v1: T_B = `link=identity; Excite(ExpK, One, sign=-) + Excite(ExpK, One, all)`, (θ₋, θ_all) = (0.5, 0.6), θ₀ = 0.15, truncating on inject_sign_low (e2) only; M_B = M_A; FSD parametric = M_B; Ref_B = T_A on inject_sign_high (e1), T_B elsewhere. Ladder θ₋ = 0.5, 0.4, 0.6, then the size-gated rung below. **Ref_B's e1 model** is T_A at θ₊ = 0.5 whatever A1's rung. It must truncate in 256/256 runs (`ctlB-ref-r0`); otherwise θ₊ = 0.6 (`ctlB-ref-r1`); otherwise B is VOID.

**Why the artefact stays on e1 (fix 5).**
- It must come before the real cap (e2) in menu order, or Fc picks e2 by menu order, which is dev-005's luck.
- The only earlier design is burst. Under the identity link a burst's 10 forced events are immigrants, adding 10/(1 − n) expected events, so no subcritical linear model truncates on burst alone.
- e1 is T_B's top design (probe2: e1 mark_gap_correlation[sign] He² 0.918; Fx's next cell is clamp_release rate_after_mark_slope[sign], 0.846). So the precondition is on the **match probability**:
  - p_F(ℓ) is Fx's per-decision label law (CRN set, designs ∉ {e1, e2}).
  - h_F = Σ_ℓ p₃₂(ℓ)·(1 − (1 − p_F(ℓ))^8).
  - P3-B: h_F ≥ 0.95 and `pass_iii(301–315, p₃₂, h_F(·))` ≥ 0.95 (N4).
  - If P3-B fails on the three θ₋ rungs, B moves to the **size-gated rung (N3)** before any VOID: T_Bs = `link=identity; Excite(ExpK, One, all) + Excite(ExpK, Mark(size), all)` (canonical order, probe4), θ = (0.35, 0.5), θ₀ = 0.15, exp_rate 1.0 (branching 0.85 observational, 1.35 under inject_size_high = e3); member M_A; Ref_Bs = T_A on e1, T_Bs elsewhere; ids `ctlB-cal-r3`, `ctlB-crn-r3`, `ctlB-boot-r3`. The artefact stays on e1 (index 1) and precedes the real cap e3 (index 3). For this rung P2's channel is `[size]`, and "e2" in B's predictions and criteria reads "e3". Probe (R = 8, `probe-Bs`): T_Bs truncates 8/8 on inject_size_high only, the member never; the top cells are `[size]` with burst first (mark_gap_correlation[size] He² 0.978) and e1's best only sixth (0.878) (`probe4-out.txt`). B3 power at N = 15 for a `[size]` label: worst 0.997 at h = 0.95 (`power2-out.txt`).
  - If P3-B also fails on the size rung, B is VOID, not a fail of Fx.

**Random arm's fixed picks, seeds 301–315:** mark_mean[sign] none; mark_skewness[sign] 303, 305, 312, 315; mark_autocorrelation[sign] 301, 302, 310, 315; mark_gap_correlation[sign] 313; next_gap_after_large_mark[sign] 301, 308, 314; rate_after_mark_slope[sign] 302, 303, 309, 310; mark_intensity_correlation[sign] 312; mark_cross_correlation[sign,size] 315; mean_rate 302, 306, 308, 315. Size rung, `[size]` labels (`power2-out.txt`): mark_mean[size] 304, 306, 307; mark_dispersion[size] 301, 303, 307; mark_skewness[size] 302, 308, 309, 314; mark_autocorrelation[size] 309, 311, 312; mark_gap_correlation[size] 301, 304, 311, 312, 315; next_gap_after_large_mark[size] 305, 307, 308, 310, 313; rate_after_mark_slope[size] 309, 314; mark_intensity_correlation[size] 306, 309, 311; mark_cross_correlation[sign,size] 315.

**Predictions** (unchanged from v1): Fa and Fc pick (e1, mean_rate); Fz picks (e1, mark_mean[sign]), probably; Fx avoids e1 and e2. Telling: K0, K3\*, K4, K5 give a label in D\*₀(B); K1 gives (e2, mean_rate); K2z gives (e2, mark_mean[sign]), probably.

**Criteria** (a pair (FSD rule, telling rule) passes iff all three hold):
- B1: FSD's design ≠ e1 on ≥ 108 of 120 pairs;
- B2: no unit is credited with d\* only through picks on e1;
- B3: FSD is credited on ≥ 12/15 units (P = 0.9945 at h_F = 0.95), and `policy_separation(FSD, random)` passes (worst `[sign]` label at h = 0.95: 1.000; `power2-out.txt`).
- Predicted outcomes: Fx with K0's telling, K3\*, K4 or K5 PASS. Fa, Fc and Fz fail B1. (Fa or Fc) with K1 fails B2. Fx with K1 fails B3.

## 3. Construction or evidence (fix 10)

**By construction.** These restate the §4.2 position and are **not evidence for option 2**:
- A1's K1 and K2z failures on (i): D\*₀ excludes E_cal, and P2 bans mean_rate.
- A1's K0 failure on (ii)/(iii): the atom row is proved.
- The exclusion rules passing (i), up to noise.
- B1 for Fx (its own exclusion) and for Fc (the forcing fact).
- B2's K1 failure: label-only matching.
- The stop case: proved.

**Measured.**
- Whether the exclusion rules recover the planted label at R = 32, and truthFSD agrees at R = 16 under CRN ((i), (ii)).
- Whether control 5 passes at real R given random's fixed picks ((iii)).
- A2's Δ between K4 and K5: its magnitude against Δ_pred. Its sign is predicted in advance (§1), so A2 is a measurement of K4's cost in one regime, not a two-sided test.
- B3: whether Fx finds telling's label when the artefact sits on T_B's top design.

None of A1, A2 or B can favour option 2 over option 1. They can show that option 2 is implementable at real R, and what it costs.

## 4. Cost, ceiling, stop rule, commands

From `cost3-out.txt` (adds B's size rung and the Δ_pred simulation allowance to v2's `cost2-out.txt`):

| | A1 | B | A2 | total |
|---|---|---|---|---|
| expected (rung 0, 3 bisection steps) | 1.84 | 2.26 | 2.85 | 6.96 CPU h |
| worst (every ladder to its last rung incl. B's size rung, each truncated run 0.97 s, 6 steps) | 2.77 | 3.72 | 3.08 | 9.56 CPU h |

v1 left out the ladder recalibration, the CRN set and the 0.97 s runs. **Ceiling: 11.0 CPU h** (worst case + 15%; v2's 9.5 left 4% headroom, redteam-s17b N7). Before launch the coordinator adds a `docs/v2/LEDGER.csv` row: dm-devbox, $0 marginal, ceiling 11.0 CPU h.

**Order.** A1 and B in parallel (2 processes, nice 10, `tmux -L sciagent`), then A2.

**Stop rule (fix 7, N7).** One shared ledger, `cpu-ledger.csv` in this directory. Every process appends one line per run (control, phase, seed, decision or telling, design, side, replicate, cpu_s, pid) under an exclusive `fcntl.flock` on the file, and before each run re-reads the whole file under the same lock and sums cpu_s. When the sum reaches 11.0 h, that process writes `STOP`; every process checks `STOP` before each run, finishes its current run and exits. Controls whose test phase is complete are scored. The rest are INCOMPLETE (not VOID, not pass or fail), and are not extended without the coordinator. A2 runs last, so A2 is cut first.

**Commands** (contract as v1; `controls.py` is written next round):

```sh
WT=/home/ubuntu/lanes/sciagent/.claude/worktrees/fsd-dist; D=/home/ubuntu/lanes/sciagent/.cache/drafts/fsd-controls
P="env OPENBLAS_NUM_THREADS=1 nice -n 10 uv run --project $WT --no-sync python -I $D/controls.py --worktree $WT --cpu-ceiling-h 11.0 --ledger $D/cpu-ledger.csv --criteria-sha256 <hash in F's message>"
$P --control A1 --phase calibrate     # writes D0-A1.json and stops; the coordinator commits it
$P --control A1 --phase test          # seeds 101-115; the N5 gate
$P --control A1 --phase score > $D/score-A1.txt
```

B is the same with seeds 301–315. A2's calibrate phase is the bisection plus the full calibration; its seeds are 201–224.

## 5. Engineer hand-off (fix 7): stub-table tests, before any test phase

These are scratch tests for `controls.py` now, and become test-first instrument tests for src later.
- **K5.**
  - X is computed from stub telling runs (truncated and stopped shares).
  - Telling's and truthFSD's argmaxes skip exactly X.
  - truthFSD ignores its own truncations when excluding, but keeps its own truncated runs on designs outside X.
  - X never reaches `FSDPolicy`, the tool layer or any record an agent can read (invariant 2; red team item 12).
- **Fx.**
  - A design where the parametric or the reference side truncates or stops on 1 of 16 runs is skipped.
  - If every design is excluded: no pick, nothing observed, the unit kept and scored a miss (the exclude-all rule, §0). No exception, so `policy_separation`'s pairing holds.
- **Ledger.** Two processes appending concurrently: no torn line, and the sum each reads includes the other's runs.
- **Fz.** mean_rate is zeroed only on cells where a side truncates; every other cell is unchanged.
- **K3a against K4.** On an all-stopped design, K3a returns (e, mean_rate), and K4 and K5 skip it.
- **Parity.** `controls.py`'s K0–K4 on `candidates.py`'s stored tables give 0 mismatches. The control outputs become the regression fixture for the src version.

## 6. Reading the outcome (fixed now)

**A1.**
- If K0 fails (ii)/(iii), K1 and K2z fail (i), and the exclusion rules pass, the instrument resolves the planted label under the exclusion position at real R. Per §3, this is not evidence for that position.
- If an exclusion rule fails (i) or (ii) with P1–P3 met, no candidate is ready. He² resolution comes first (the (He², D) ordering from the ties draft), and the thresholds are not changed.

**A2.**
- Δ favours K5, or favours K4, by the preset rule; otherwise plumbing decides. The verdict holds only in A2's regime (a partially truncated design that dominates its row) and is read against Δ_pred.
- A large E5 disagreement under K5 means truthFSD's kept truncated runs matter. A possible response is to exclude per run rather than per design. That rule is **untested**, needs its own control, and is not chosen on A2's result.

**B.**
- If Fx passes B1–B3 while Fa, Fc and Fz fail B1, FSD needs its own exclusion, whatever the telling rule.
- If (Fa or Fc) with K1 fails B2, K1 needs design-aware matching before it can be adopted.
- A VOID B (P3-B) means the artefact design is the truth's best design; it says nothing about Fx.

**Throughout.** VOID and INCOMPLETE favour nothing. Unpredicted patterns are reported, and no rule is chosen on them.

## Not proved or not covered

As in v1, plus:
- the CRN effect on p₁₆ and p_F is known only once calibration runs;
- Δ's power comes only from the calibration simulation (P2-A2), not from a closed form; a₀ for the A2 family is low (crude 0.395, probe4), so off-e1 agreement is noisy;
- B may still be VOID if the size rung also fails P3-B; the size rung was probed at R = 8 only;
- the K2z and Fz labels at R = 32 and T_B's saturated labels on e2 are unprobed.

# Positive controls for the at-stop rule (FSD, truthFSD, telling): design and preset criteria, v2

Theorist, 2026-10-29. Design only: no control stream has been drawn. Supersedes `criteria-v1.md` (sha256 21a01721…), revised for every fix in `redteam-s17.md`. Evidence: `probe*-out.txt` (223 CPU s), `power-out.txt`, `power2-out.txt`, `power3-out.txt`, `a2band-out.txt`, `cost2-out.txt` and `random-picks.txt` (sha256 33f80c78…), all in this directory. Code cited is worktree `fsd-dist` (bff61ec), read-only.

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
- P3 (fix 4; A1 in full; B uses only its 99% clause, plus P3-B in §2): h ≥ 0.95; `pass_ii(N, p₃₂, p₁₆)` ≥ 0.9 and `pass_iii(seeds, p₃₂, h(·))` ≥ 0.9 (`power2.py`, frozen with this file; exact, clustered, given random's fixed picks); at least 99% of 16-vs-16 subsamples have no cell exactly 1.0 outside the planned truncated rows.
- A failure moves to the next rung; all rungs failing makes the control **VOID** (a finding about He² resolution, not a verdict on any rule).

**Freeze and gate (fix 6).** The coordinator freezes this file by a git commit, with `power2.py`, `random-picks.txt` and the probe outputs. `.cache/` is gitignored (.gitignore:15), so that commit needs `git add -f` or a tracked copy. The calibrate phase writes `D0-<C>.json` (rung, E_cal, D\*₀, p₃₂, p₁₆, p_F, π̂, every precondition value), which is committed separately. The test phase refuses unless `criteria.md` and `D0-<C>.json` are committed and unchanged at HEAD (`git ls-files --error-unmatch`, `git diff --quiet HEAD --`).

**Edge cases (fix 8).**
- Test-time P1 breach (a truncation or stop where calibration had none, or a run that does not truncate where calibration had 256/256): scored as observed under every rule and listed by seed. The control is VOID if breaches touch more than 2 seeds.
- A rule that excludes every design at a unit scores a miss there. As built this cannot happen: at most 2 of 8 designs are excluded.
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

**A2: K4 against K5, redesigned (fix 1).**
- Build: the T_A family with θ_all = 0.85 − θ₊/2. The inject_sign_high branching v = 0.85 + θ₊/2 is set by bisection on [0.980, 1.000]: at most 6 steps of 512 truth runs on e1; stop at the first v with π̂ ∈ [0.015, 0.030]; step up if below, down if above. The probe gave 0/12 truncations at 0.98 and 6/12 at 1.00.
- Preconditions:
  - P1-A2: in a full calibration at v (256 per side, both stream sets), nothing truncates or stops off e1, and the member never does.
  - P2-A2: a₀ = Σ_ℓ p₁₆(ℓ)·p₃₂(ℓ) with e1 excluded in both arms ≥ 0.6.
- VOID if: the bisection finds no v; P1-A2 or P2-A2 fails; fewer than 5 seeds have telling keeping e1, or fewer than 5 have it excluding e1 (P = 0.020, 0.002 and 0.023 at π = 0.015, 0.02 and 0.03); P1 breaches touch more than 2 seeds.
- Test: seeds 201–224 (N = 24, 192 pairs). This replaces v1's 5 seeds, where P(no seed keeps e1) was 0.34 at π = 0.05 (redteam-s17.md:18).
- Measured on the same stored runs, for K4 and K5 (K3h and K1 reported too): L_K = pairs where truthFSD's label ≠ telling's (telling's label is the same under both). Exposures:
  - E4: telling excludes e1 and truthFSD's own 16 runs never truncate, so K4 keeps e1 in truthFSD;
  - E5: telling keeps e1 and truthFSD's 16 include a truncation, which K5 keeps under (c). The number of truncated runs kept is reported.
  - Expected per pair at π = 0.02: E4 0.345 and E5 0.145, about 66 and 28 of 192 (`a2band-out.txt`). L_K is reported within each.
- Preset reading: Δ = (L_K4 − L_K5)/192, with a 90% seed-cluster bootstrap interval (2,000 resamples of the 24 seeds).
  - K5 is favoured iff Δ ≥ 0.05 and the interval lies above 0.
  - K4 is favoured iff Δ ≤ −0.05 and the interval lies below 0.
  - Otherwise no preference; plumbing decides.
- Either rule can now lose: K4 on E4 pairs where a kept e1 wins truthFSD's argmax; K5 on E5 pairs where kept truncated runs (mean_rate ≥ cap/τ) pull truthFSD to e1. No power is computed for Δ: the effect sizes are unknown.

**Stops, K3a against K4/K5:** as v1, by proof (battery.py:361-374, telling.py:88-95); the stub test is in §5.

## 2. Control B (the dev-005 shape)

**Build.** As v1: T_B = `link=identity; Excite(ExpK, One, sign=-) + Excite(ExpK, One, all)`, (θ₋, θ_all) = (0.5, 0.6), θ₀ = 0.15, truncating on inject_sign_low (e2) only; M_B = M_A; FSD parametric = M_B; Ref_B = T_A on inject_sign_high (e1), T_B elsewhere. Ladder θ₋ = 0.5, 0.4, 0.6. **Ref_B's e1 model** is T_A at θ₊ = 0.5 whatever A1's rung. It must truncate in 256/256 runs (`ctlB-ref-r0`); otherwise θ₊ = 0.6 (`ctlB-ref-r1`); otherwise B is VOID.

**Why the artefact stays on e1 (fix 5).**
- It must come before the real cap (e2) in menu order, or Fc picks e2 by menu order, which is dev-005's luck.
- The only earlier design is burst. Under the identity link a burst's 10 forced events are immigrants, adding 10/(1 − n) expected events, so no subcritical linear model truncates on burst alone.
- e1 is T_B's top design (probe2: e1 mark_gap_correlation[sign] He² 0.918; Fx's next cell is clamp_release rate_after_mark_slope[sign], 0.846). So the precondition is on the **match probability**:
  - p_F(ℓ) is Fx's per-decision label law (CRN set, designs ∉ {e1, e2}).
  - h_F = Σ_ℓ p₃₂(ℓ)·(1 − (1 − p_F(ℓ))^8).
  - P3-B: h_F ≥ 0.95 and `pass_iii(301–315, p₃₂, h_F(·))` ≥ 0.9.
  - If P3-B fails on every rung, B is VOID, not a fail of Fx; that outcome is reported as "the artefact hides the design that shows T_B best".

**Random arm's fixed picks, seeds 301–315:** mark_mean[sign] none; mark_skewness[sign] 303, 305, 312, 315; mark_autocorrelation[sign] 301, 302, 310, 315; mark_gap_correlation[sign] 313; next_gap_after_large_mark[sign] 301, 308, 314; rate_after_mark_slope[sign] 302, 303, 309, 310; mark_intensity_correlation[sign] 312; mark_cross_correlation[sign,size] 315; mean_rate 302, 306, 308, 315.

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
- A2's Δ between K4 and K5.
- B3: whether Fx finds telling's label when the artefact sits on T_B's top design.

None of A1, A2 or B can favour option 2 over option 1. They can show that option 2 is implementable at real R, and what it costs.

## 4. Cost, ceiling, stop rule, commands

From `cost2-out.txt`:

| | A1 | B | A2 | total |
|---|---|---|---|---|
| expected (rung 0, 3 bisection steps) | 1.84 | 2.26 | 2.75 | 6.86 CPU h |
| worst (every ladder to its last rung, each truncated run 0.97 s, 6 steps) | 2.77 | 3.36 | 2.98 | 9.10 CPU h |

v1 left out the ladder recalibration, the CRN set and the 0.97 s runs. **Ceiling: 9.5 CPU h.**

**Order.** A1 and B in parallel (2 processes, nice 10, `tmux -L sciagent`), then A2.

**Stop rule (fix 7).** `controls.py` logs process CPU per run. When the sum over all controls reaches 9.5 h, it writes `STOP`, finishes the current run and exits. Controls whose test phase is complete are scored. The rest are INCOMPLETE (not VOID, not pass or fail), and are not extended without the coordinator. A2 runs last, so A2 is cut first.

**Commands** (contract as v1; `controls.py` is written next round):

```sh
WT=/home/ubuntu/lanes/sciagent/.claude/worktrees/fsd-dist; D=/home/ubuntu/lanes/sciagent/.cache/drafts/fsd-controls
P="env OPENBLAS_NUM_THREADS=1 nice -n 10 uv run --project $WT --no-sync python -I $D/controls.py --worktree $WT --cpu-ceiling-h 9.5"
$P --control A1 --phase calibrate     # writes D0-A1.json and stops; the coordinator commits it
$P --control A1 --phase test          # seeds 101-115; refuses unless criteria.md and D0-A1.json are committed, unchanged
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
  - If every design is excluded, a typed error is raised. The controls never reach this case.
- **Fz.** mean_rate is zeroed only on cells where a side truncates; every other cell is unchanged.
- **K3a against K4.** On an all-stopped design, K3a returns (e, mean_rate), and K4 and K5 skip it.
- **Parity.** `controls.py`'s K0–K4 on `candidates.py`'s stored tables give 0 mismatches. The control outputs become the regression fixture for the src version.

## 6. Reading the outcome (fixed now)

**A1.**
- If K0 fails (ii)/(iii), K1 and K2z fail (i), and the exclusion rules pass, the instrument resolves the planted label under the exclusion position at real R. Per §3, this is not evidence for that position.
- If an exclusion rule fails (i) or (ii) with P1–P3 met, no candidate is ready. He² resolution comes first (the (He², D) ordering from the ties draft), and the thresholds are not changed.

**A2.**
- Δ favours K5, or favours K4, by the preset rule; otherwise plumbing decides.
- A large E5 disagreement under K5 means truthFSD's kept truncated runs matter. The response is to exclude per run, not to re-threshold.

**B.**
- If Fx passes B1–B3 while Fa, Fc and Fz fail B1, FSD needs its own exclusion, whatever the telling rule.
- If (Fa or Fc) with K1 fails B2, K1 needs design-aware matching before it can be adopted.
- A VOID B (P3-B) means the artefact design is the truth's best design; it says nothing about Fx.

**Throughout.** VOID and INCOMPLETE favour nothing. Unpredicted patterns are reported, and no rule is chosen on them.

## Not proved or not covered

As in v1, plus:
- the CRN effect on p₁₆ and p_F is known only once calibration runs;
- Δ has no power calculation;
- B may be VOID by construction of the menu order;
- the K2z and Fz labels at R = 32 and T_B's saturated labels on e2 are unprobed.

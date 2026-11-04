# Positive controls for the at-stop rule (FSD, truthFSD, telling): design and preset criteria, v4

v4 (2026-10-29) fixes `redteam-s17c.md` B1–B5 and answers its items 6–12 (last section). It supersedes `criteria-v3.md` (sha256 0123093f…). New evidence: `probe5-out.txt` (96.6 CPU s), `bench-out.txt`, `cost4-out.txt`.

Theorist, 2026-10-29. Design only: no control stream has been drawn. Supersedes `criteria-v2.md` (sha256 178e1147…; fixes from `redteam-s17b.md`, N1–N8) and `criteria-v1.md` (21a01721…; `redteam-s17.md`). Evidence: `probe*-out.txt` (probe1–3 223 CPU s, probe4 135 CPU s), `power-out.txt`, `power2-out.txt`, `power3-out.txt`, `a2band-out.txt`, `cost3-out.txt` and `random-picks.txt` (sha256 33f80c78…), all in this directory. Code cited is worktree `fsd-dist` (bff61ec), read-only.

## 0. Common ground

**Rules.** K0, K1, K2z, K3a, K3h, K4 as in v1. FSD rules Fa, Fc, Fz, Fx as in v1 (Fc is FSD under K1, K3a, K3h, K4, K5; Fz is FSD under K2z).

**K5, restated (fix 2).** (c); X(truth, seed) = the designs where the truth or the member truncates or stops on at least 1 of *telling's own* 32 replicates; truthFSD skips exactly X and has no exclusion of its own. K5 is **shared, and it depends on telling's R**: P(exclude) = 1 − (1 − π)^32, so the 1-of-R cliff remains, at telling's R. Telling's d\* is **identical under K4 and K5** (both exclude on telling's 32 runs): the two differ only in truthFSD, so only in control 5 and in no C5 rate. Under (c), truthFSD keeps its own truncated runs on designs outside X; A2 counts them. Chosen over a fixed set of framework-side calibration runs because (1) no finite-sample exclusion is R-independent: R_X dedicated runs move the cliff to π ≈ 1/R_X, they do not remove it; (2) that variant changes telling's d\* and the C5 rate, and leaves truncated runs inside kept designs for telling as well; (3) it costs 2 × 8 × R_X runs per (truth, seed), 0.16 CPU h per unit at R_X = 256 (`cost-out.txt`, A1 calibration). Consequence for the owner: option 2's "computed once, independently of R" (JOURNAL.md:1517-1518) should read "computed once from telling's replicates and shared".

**Rules not yet covered by code.** K5, Fx and Fz are not in `candidates.py` (its KEYS, :202, are K0, K1, K2a, K2z, K3h, K3a, K4). The stub-table tests in §5 must pass before F, and so before any calibrate phase (B1).

**Harness.** As v1: each run once via `run_experiment_truncating` (cap 15,000; stops `policies.STOPPED`), read on `view.clip_to_analysis`, rules applied afterwards with `hellinger2`, `choose` (e-major), `Telling.from_table` (d-major); hit = equal label (`telling.observed`); random = `RandomPolicy`'s draw; control 5 = `controls_p4.policy_separation`. **ViewError** (a run cut at time 0): counts as truncated, with all 35 diagnostics None (as `measure_trunc.py`).

**Streams and ids (fix 3).** C ∈ {ctlA1, ctlA2, ctlB}; rung j = 0, 1, 2.
- Telling, test: `scorer_seed(TELLING, C, s, side, e, r)`, r < 32. The id does not depend on the rung: test streams are drawn only after the rung is fixed.
- truthFSD and FSD, test: `policy_seed(s, k, PARAMETRIC_INDEX | REFERENCE_INDEX, r)`, r < 16, k = 0..7. There is no design index (policies.py:665-666), so replicate r shares random numbers across designs (CRN).
- Random: `policy_seed(s, k, RANDOM_INDEX, 0)`, no truth id: its picks are fixed now (`random-picks.txt`).
- Calibration, rung j: per-design set `scorer_seed(TELLING, C+"-cal-r"+j, 0, side, e, r)` and CRN set `scorer_seed(TELLING, C+"-crn-r"+j, 0, side, r)`, r < 256; bootstrap `generator(scorer_seed(TELLING, C+"-boot-r"+j, 0))`.
- B's reference check: `scorer_seed(TELLING, "ctlB-ref-r"+j, 0, 0, 1, r)`, r < 256. A2 bisection (B5): one id, `scorer_seed(TELLING, "ctlA2-pi", 0, i, r)`, step i = 0..5, r < 512; full calibration ids `ctlA2-cal`, `ctlA2-crn`, `ctlA2-boot`; Δ bootstrap `generator(scorer_seed(TELLING, "ctlA2-dboot", 0))`.
- Menu: `World(truth, s, truncate=True)`. Seeds: A1 101–115 (101–110 kept from v1), B 301–315 (301–310 kept), A2 201–224.

**Ground-truth position.** As v1 (telling.py:14-24, extended to truncation). The owner's SPEC §4.2 choice is not made here.

**Calibration (A1, B).** E_cal = designs where any calibration run of either side truncates or stops. p₃₂(ℓ): share of 1,000 subsamples of 32 of the 256 per-design-set replicates whose d-major argmax over designs ∉ E_cal has label ℓ. p₁₆(ℓ): the same from the CRN set, 16 indices shared by every design (CRN kept), e-major. D\*₀ = fewest labels by decreasing p₃₂ with Σp₃₂ ≥ 0.95, chosen on replicate indices 0–127 (subsamples of 32 of 128), with the coverage Σ_{ℓ∈D\*₀} p₃₂ that P2/P3 use then measured on indices 128–255, so it is not in-sample (redteam-s17c 8). h(ℓ) = 1 − (1 − p₁₆(ℓ))^8 (decisions use independent streams); h = Σ_ℓ p₃₂(ℓ)·h(ℓ).

**Preconditions.**
- P1: the truncation pattern is exactly as built (256/256 where planned, 0 elsewhere, 0 stops), in both stream sets.
- P2: \|D\*₀\| ≤ 2; every label in D\*₀ is a `[sign]` diagnostic; mean_rate ∉ D\*₀.
- P3 (fix 4, N4; A1 in full; B uses only its 99% clause, plus P3-B in §2): h ≥ 0.95; `pass_ii(N, p₃₂, p₁₆)` ≥ 0.95 and `pass_iii(seeds, p₃₂, h(·))` ≥ 0.95 (`power2.py`, frozen with this file; exact, clustered, given random's fixed picks). (i) then passes with P ≥ 0.9945 (Σp₃₂ ≥ 0.95, `a2band-out.txt`), so each criterion passes with P ≥ 0.95 at the floor; at least 99% of 16-vs-16 subsamples have no cell exactly 1.0 outside the planned truncated rows.
- A failure moves to the next rung; all rungs failing makes the control **VOID** (a finding about He² resolution, not a verdict on any rule).

**Sequence (B1).**
1. Now: the coordinator commits this v4 as the design record. This commit is not the freeze.
2. Next engineer session: `controls.py` and `test_controls.py` are written in this directory to §5's contract, with no src change. The tests pass under the worktree interpreter, and their output is saved as `test_controls-out.txt`. Any change this file needs goes back to a red team as v5 first.
3. Freeze commit F (coordinator, `git add -f`, since `.cache/` is gitignored, .gitignore:15). It holds every file in this directory except `__pycache__/`, `*-err.txt`, `cpu-ledger.csv`, `cpu-total.txt`, `STOP`, `D0-*.json`, `runs-*.jsonl` and `score-*.txt`. That includes `criteria.md`, `controls.py`, `test_controls.py`, `test_controls-out.txt`, `power2.py` and `probe.py` (power2.py derives the 35 menu labels through it from `diagnostic_menu`). F's message carries the lines `criteria.md sha256: <h>` and `controls.py sha256: <h>`.
4. calibrate writes `D0-<C>.json` (rung or rungs, E_cal, D\*₀, p₃₂, p₁₆, p_F, π̂, Δ_pred, every precondition value), committed alone in a commit D.
5. test writes `runs-<C>.jsonl`.
6. score writes `score-<C>.txt`.

**Gate, in every phase (calibrate, test and score; B1).**
- F is passed explicitly (`--freeze-commit F`). The two hashes are read from F's message and must equal the sha256 of `criteria.md` and `controls.py` at HEAD.
- `git diff --quiet F HEAD -- <frozen files>`, where the frozen files are listed from F itself: `git ls-tree -r --name-only F -- .cache/drafts/fsd-controls/`.
- The worktree's HEAD is bff61ecba3daed876e55896e329000704fb9626b, and `git -C $WT status --porcelain --untracked-files=no` is empty.
- test and score also need D: F is a strict ancestor of D (`git merge-base --is-ancestor F D`, F ≠ D), and `git diff --quiet D HEAD -- D0-<C>.json`. score records the sha256 of `runs-<C>.jsonl`.
- `D0-<C>.json` and `score-<C>.txt` both record F, the sha256 of `controls.py` and the worktree's HEAD.

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

**A2: K4's cost against K5, v4 (B4, B5; N1, N2, N8).**
- Family (as v3): T_A's structure with θ₊ = 0.5 fixed and θ_all ∈ [0.44, 0.49], so the inject_sign_high branching is 0.5 + θ_all; θ₀ = 0.75 − θ_all; the member is HAWKES with θ = θ_all + 0.25 and the same θ₀.
- Bisection (B5): one stream id, `scorer_seed(TELLING, "ctlA2-pi", 0, i, r)`, step i = 0..5, r < 512, truth only, e1 only.
  - lo, hi = 0.44, 0.49; step i tests θ_i = (lo + hi)/2, so **the first point is θ_all = 0.465**.
  - π̂ < 0.015 sets lo = θ_i; π̂ > 0.030 sets hi = θ_i; π̂ in [0.015, 0.030] stops. No stop after 6 steps is VOID.
  - The full calibration at θ_i must give π̂ ∈ [0.0075, 0.040] on its 512 e1 truth runs (P outside = 0.051 at π = 0.015, 0.096 at 0.030; `cost3-out.txt`).
- Probes:
  - probe4 (θ_all 0.49, R 48): 5/48 truncated on e1; crude a₀ 0.395.
  - probe5 (θ_all 0.465, R 32, id `probe-A2c`): 0/32 truncated on e1. e1's row tops at rate_after_mark_slope[sign] He² 1.0000 and mark_intensity_correlation[sign] 0.9989, against 0.9564 for the best cell off e1 (inject_sign_low mark_gap_correlation[sign]). e1's truth runs have a median of 4,244 events, the member's 1,981.
  - So e1 dominates its row, and with a `[sign]` label, not mean_rate. Low a₀ dilutes Δ but cannot bias it: where K4's and K5's exclusions agree, their labels are identical.
- Preconditions: P1-A2 only (in the full calibration nothing truncates or stops off e1, and the member never does). v3's P2-A2 (|Δ_pred| ≥ 0.10, decisive in ≥ 80%) is **removed**: with no verdict it has nothing to gate, and its 80% was in-sample (redteam-s17c 8).
- Δ_pred (N2): as v3, 200 simulated tests from calibration subsamples (generator `ctlA2-sim`), written to `D0-A2.json` before any test stream. It is a reference, labelled in-sample, because every simulated test reuses the same few truncated e1 runs. Measured cost 0.09 CPU h (`bench-out.txt`, `cost4-out.txt`).
- **The sign of Δ is not predicted.** When telling keeps e1, its label there is rate_after_mark_slope[sign] (probe5).
  - On E5 pairs (telling keeps e1, truthFSD's 16 runs truncate), K5's kept truncated runs can push truthFSD to (e1, mean_rate): mean_rate is d = 0 and is forced towards 1. K4 drops e1.
  - On E4 pairs (telling excludes e1, truthFSD's 16 do not truncate), K4 keeps a dominant e1 whose label can still match telling's off-e1 label, because matching ignores the design.
  - Either rule can therefore lose. This also answers redteam-s17c's alternative: in this regime telling's e1 label is not mean_rate.
- Scope: this regime only, a planted truth whose partially truncated design dominates its own row.
- VOID: the bisection fails; the full-calibration π̂ leaves its band; P1-A2 fails; at test time, fewer than 5 seeds on either side of telling's exclusion of e1, or P1 breaches on more than 2 seeds.
  - VOID risk: 0.002–0.023 inside the bisection band; 0.17–0.18 at π = 0.01 or 0.04 (`a2band-out.txt`); 0.39 at 0.0075 (redteam-s17c 7).
  - Item 12: score computes and writes the test-time VOIDs before computing Δ. If any fires, it writes VOID and computes no Δ.
- Test: seeds 201–224 (N = 24, 192 pairs).
- Measured on the same stored runs (unchanged from v3): L_K for K4 and K5 (K3h and K1 reported too); E4 and E5 with L_K within each; the number of truncated runs K5 keeps. Expected per pair at π = 0.02: E4 0.345 and E5 0.145 (`a2band-out.txt`).
- Report (B4, N8):
  - Δ = (L_K4 − L_K5)/192, read as **K4's cost in control-5 agreement relative to K5, in this regime**.
  - Its 90% interval: percentile bootstrap over seeds (the 24 seeds resampled with replacement, each seed's 8 decisions together), 2,000 resamples, generator `ctlA2-dboot`, `numpy.quantile(Δ*, [0.05, 0.95], method="linear")`.
  - Δ_pred is reported beside it. **A2 draws no verdict for K4 or K5.**

**Stops, K3a against K4/K5:** as v1, by proof (battery.py:361-374, telling.py:88-95); the stub test is in §5.

## 2. Control B (the dev-005 shape)

**Build.** As v1: T_B = `link=identity; Excite(ExpK, One, sign=-) + Excite(ExpK, One, all)`, (θ₋, θ_all) = (0.5, 0.6), θ₀ = 0.15, truncating on inject_sign_low (e2) only; M_B = M_A; FSD parametric = M_B; Ref_B = T_A on inject_sign_high (e1), T_B elsewhere. **B calibrates all four rungs** (θ₋ = 0.5, 0.4, 0.6, then the size-gated rung), whatever the earlier ones give. It writes every rung's P1, P2 and P3-B values (h_F, pass_iii) into `D0-B.json` (B3). The test uses the first rung in that order that passes P1, P2 and P3-B. **Ref_B's e1 model** is T_A at θ₊ = 0.5 whatever A1's rung. It must truncate in 256/256 runs (`ctlB-ref-r0`); otherwise θ₊ = 0.6 (`ctlB-ref-r1`); otherwise B is VOID.

**Why the artefact stays on e1 (fix 5).**
- It must come before the real cap (e2) in menu order, or Fc picks e2 by menu order, which is dev-005's luck.
- The only earlier design is burst. Under the identity link a burst's 10 forced events are immigrants, adding 10/(1 − n) expected events, so no subcritical linear model truncates on burst alone.
- e1 is T_B's top design (probe2: e1 mark_gap_correlation[sign] He² 0.918; Fx's next cell is clamp_release rate_after_mark_slope[sign], 0.846). So the precondition is on the **match probability**:
  - p_F(ℓ) is Fx's per-decision label law (CRN set, designs ∉ {e1, e2}).
  - h_F = Σ_ℓ p₃₂(ℓ)·(1 − (1 − p_F(ℓ))^8).
  - P3-B: h_F ≥ 0.95 and `pass_iii(301–315, p₃₂, h_F(·))` ≥ 0.95 (N4).
  - **Size-gated rung (N3):** T_Bs = `link=identity; Excite(ExpK, One, all) + Excite(ExpK, Mark(size), all)` (canonical order, probe4), θ = (0.35, 0.5), θ₀ = 0.15, exp_rate 1.0 (branching 0.85 observational, 1.35 under inject_size_high = e3); member M_A; Ref_Bs = T_A on e1, T_Bs elsewhere; ids `ctlB-cal-r3`, `ctlB-crn-r3`, `ctlB-boot-r3`. The artefact stays on e1 (index 1) and precedes the real cap e3 (index 3). For this rung P2's channel is `[size]`, and "e2" in B's predictions and criteria reads "e3". Probe (R = 8, `probe-Bs`): T_Bs truncates 8/8 on inject_size_high only, the member never; the top cells are `[size]` with burst first (mark_gap_correlation[size] He² 0.978) and e1's best only sixth (0.878) (`probe4-out.txt`). B3 power at N = 15 for a `[size]` label: worst 0.997 at h = 0.95 (`power2-out.txt`).
  - **Reading P3-B (B3).**
    - A θ₋ rung that fails P3-B is a measured **cost of Fx**: with the artefact on the truth's top design, Fx does not recover telling's label. Its h_F is reported.
    - If every rung fails P3-B, B's test is not run; that finding is B's result, not VOID.
    - If the size rung is the one tested, a B1–B3 pass holds only for truths whose best design is not the artefact design.
    - B is VOID only if every rung fails P1 or P2, or if Ref_B's check fails.

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
- B3 (moved here, B3): a B3 pass is gated by P3-B, which is B3's own per-unit success probability (h_F), so it mostly restates P3-B.

**Measured.**
- Whether the exclusion rules recover the planted label at R = 32, and truthFSD agrees at R = 16 under CRN ((i), (ii)).
- Whether control 5 passes at real R given random's fixed picks ((iii)).
- A2's Δ: K4's cost in control-5 agreement relative to K5, in one regime, against Δ_pred. Its sign is not predicted (§1); no verdict.
- B's per-rung h_F in `D0-B.json`: whether Fx recovers telling's label when the artefact sits on the truth's top design (θ₋ rungs) or off it (size rung).

None of A1, A2 or B can favour option 2 over option 1. They can show that option 2 is implementable at real R, and what it costs.

## 4. Cost, ceiling, stop rule, commands

From `cost4-out.txt`. v4 adds: B calibrating all four rungs; the bootstraps; the Δ_pred simulation, measured rather than guessed (0.090 h, from 10.7 ms per 8 × 35 He² table at R = 32 and 7.1 ms at R = 16, `bench-out.txt`); and scoring.

| | A1 | B | A2 | total |
|---|---|---|---|---|
| expected (rung 0, 3 bisection steps) | 1.85 | 3.29 | 2.85 | 7.99 CPU h |
| worst (every ladder to its last rung, each truncated run 0.97 s, 6 steps) | 2.78 | 3.75 | 3.08 | 9.60 CPU h |

**Ceiling: 11.0 CPU h** (worst case + 14.6%). Before launch the coordinator adds a `docs/v2/LEDGER.csv` row: dm-devbox, $0 marginal, ceiling 11.0 CPU h.

**Order.** A1 and B in parallel (2 processes, nice 10, `tmux -L sciagent`), then A2.

**Stop rule (B2, N7).**
- **What counts.** CPU is whole-process: `resource.getrusage(RUSAGE_SELF)` plus `RUSAGE_CHILDREN` (git calls), user + sys. So it covers simulation, diagnostics, subsampling, bootstraps, the Δ_pred simulation, scoring and startup.
- **Updates.** Each process adds its CPU delta since its last update to `cpu-total.txt`, a single number of seconds. The update locks the file with an exclusive `fcntl.flock`, reads it, adds, writes and unlocks: O(1). Updates happen after every simulated run, and at least every 30 CPU s inside non-simulation loops.
- **Audit.** Each update also appends one audit line to `cpu-ledger.csv` (control, phase, unit, delta, total, pid). That file is never re-read during the run.
- **Stop.** Before each run or loop step, a process reads `cpu-total.txt`. At 11.0 h it writes `STOP`. Every process checks `STOP` at the same points, finishes its current step and exits. Controls whose test phase is complete are scored. The rest are INCOMPLETE (not VOID, not pass or fail), and are not extended without the coordinator. A2 runs last, so A2 is cut first.

**Commands** (contract as v1; `controls.py` is written next round):

```sh
WT=/home/ubuntu/lanes/sciagent/.claude/worktrees/fsd-dist; D=/home/ubuntu/lanes/sciagent/.cache/drafts/fsd-controls
P="env OPENBLAS_NUM_THREADS=1 nice -n 10 uv run --project $WT --no-sync python -I $D/controls.py --worktree $WT --freeze-commit <F> --cpu-ceiling-h 11.0"
$P --control A1 --phase calibrate     # gate; writes D0-A1.json and stops; the coordinator commits it (commit D)
$P --control A1 --phase test          # gate + D; seeds 101-115; writes runs-A1.jsonl
$P --control A1 --phase score > $D/score-A1.txt
```

B is the same with seeds 301–315. A2's calibrate phase is the bisection plus the full calibration; its seeds are 201–224.

## 5. Engineer hand-off: `controls.py` and `test_controls.py`, both in F (B1)

These tests must pass before F, and so before any calibrate phase (redteam-s17c 1e). They are scratch tests now, and become test-first instrument tests for src later. Besides the items below, they cover:
- **Gate.** It refuses in each of: a file differing from F; a hash mismatch with F's message; a worktree HEAD other than bff61ec, or a dirty worktree; D not strictly after F; D0 changed since D.
- **Telling exclude-all (redteam-s17c 10).** With every design excluded there is no d\*: `Telling.from_table` is never called on an all-excluded table, which would return (0, 0). The unit scores a miss and stays paired.
- **Determinism.** A stub control run twice gives byte-identical `D0`, runs and score files.
- **K5.**
  - X is computed from stub telling runs (truncated and stopped shares).
  - Telling's and truthFSD's argmaxes skip exactly X.
  - truthFSD ignores its own truncations when excluding, but keeps its own truncated runs on designs outside X.
  - X never reaches `FSDPolicy`, the tool layer or any record an agent can read (invariant 2; red team item 12).
- **Fx.**
  - A design where the parametric or the reference side truncates or stops on 1 of 16 runs is skipped.
  - If every design is excluded: no pick, nothing observed, the unit kept and scored a miss (the exclude-all rule, §0). No exception, so `policy_separation`'s pairing holds.
- **Ledger (B2).** Two processes updating `cpu-total.txt` concurrently lose no update. A non-simulation loop (stub bootstrap) is charged. `STOP` fires at a stub ceiling, and both processes exit.
- **Fz.** mean_rate is zeroed only on cells where a side truncates; every other cell is unchanged.
- **K3a against K4.** On an all-stopped design, K3a returns (e, mean_rate), and K4 and K5 skip it.
- **Parity.** `controls.py`'s K0–K4 on `candidates.py`'s stored tables give 0 mismatches. The control outputs become the regression fixture for the src version.

## 6. Reading the outcome (fixed now)

**A1.**
- If K0 fails (ii)/(iii), K1 and K2z fail (i), and the exclusion rules pass, the instrument resolves the planted label under the exclusion position at real R. Per §3, this is not evidence for that position.
- If an exclusion rule fails (i) or (ii) with P1–P3 met, no candidate is ready. He² resolution comes first (the (He², D) ordering from the ties draft), and the thresholds are not changed.
- If (iii) alone fails, with (i) and (ii) passing (redteam-s17c 9), control 5 is underpowered at N = 15. That is not "no candidate is ready". The joint floor is about 0.90 per rule (0.9945 × 0.95 × 0.95), so one such failure is expected about one time in ten.

**A2.**
- Δ and its interval are reported as K4's cost in control-5 agreement relative to K5, in A2's regime only, beside Δ_pred. No verdict for K4 or K5 is drawn (B4). The choice between them stays with the owner and with plumbing.
- A large E5 disagreement under K5 means truthFSD's kept truncated runs matter. A possible response is to exclude per run rather than per design. That rule is **untested**, needs its own control, and is not chosen on A2's result.

**B.**
- If Fx passes B1–B3 while Fa, Fc and Fz fail B1, FSD needs its own exclusion, whatever the telling rule.
- If (Fa or Fc) with K1 fails B2, K1 needs design-aware matching before it can be adopted.
- P3-B is read as in §2: a θ₋-rung failure is a cost of Fx, and a size-rung pass is limited to truths whose best design is not the artefact design.

**Throughout.** VOID and INCOMPLETE favour nothing. Unpredicted patterns are reported, and no rule is chosen on them.

## Not proved or not covered

As in v1, plus:
- the CRN effect on p₁₆ and p_F is known only once calibration runs;
- Δ has no power calculation (A2 now reports and draws no verdict); a₀ for the A2 family is low (crude 0.395, probe4), so off-e1 agreement is noisy; e1's dominance was probed once, at θ_all = 0.465 with R = 32 (probe5);
- B's size rung was probed at R = 8 only;
- the K2z and Fz labels at R = 32, and T_B's saturated labels on e2, are unprobed.

## Answers to redteam-s17c (non-blocking items 6–12)

6. Probed: `probe5-out.txt` (θ_all 0.465, R = 32, 96.6 CPU s). e1 dominates its row (rate_after_mark_slope[sign] He² 1.0000, against 0.9564 off e1), and with a `[sign]` label, so v3's predicted sign is withdrawn (§1).
7. Accepted and stated in §1. A2 VOID risk reaches 0.17–0.18 at π = 0.01 or 0.04, and 0.39 at 0.0075. A2 runs last and is the cheapest control to lose.
8. Fixed. D\*₀ is chosen on calibration indices 0–127 and its coverage measured on 128–255 (§0). Δ_pred is a reference labelled in-sample, and no longer gates anything.
9. Accepted. The joint floor is about 0.90. A failure of (iii) alone now has its own reading (§6).
10. Fixed: the telling exclude-all stub is in §5.
11. The coordinator corrects the JOURNAL wording (:1518).
12. Fixed: score computes and writes A2's test-time VOIDs before it computes Δ (§1).

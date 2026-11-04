# Positive controls for the at-stop rule (FSD, truthFSD, telling): design and preset criteria

Theorist, 2026-10-29. Design only: no control stream has been drawn. Probe evidence comes from `probe.py`, `probe2.py` and `probe3.py` (outputs in `probe*-out.txt`, 223 CPU s in total). The probes used truth ids `probe-*`, which no control stream uses. Exact arithmetic is in `power.py` → `power-out.txt`. Code cited is worktree `fsd-dist` (bff61ec), read-only.

## 0. Common ground

- **Telling and truthFSD rules.**
  - K0: truthFSD (a), telling (b).
  - K1: (c).
  - K2z: (c), with mean_rate zeroed where a side truncates.
  - K3a, K3h: (c), excluding a design where truth or member truncates on any replicate (K3a) or on more than half of them (K3h), counted on each arm's own replicates.
  - K4: K3a, with stops counted as well as truncations.
  - **K5 (new; this is the requested R-independent shared exclusion).** (c). X(truth, seed) is the set of designs where truth or member truncates or stops on at least 1 of telling's 32 scorer replicates (telling.py:135). It is computed once and handed to truthFSD framework-side, the way the truth is (policy_controls.py docstring). Both argmaxes skip exactly X, so neither arm's R decides exclusion. It is viable, but it is an instrument change (tests first).
- **FSD rules.**
  - Fa: (a).
  - Fc: (c), keeping every design. This is FSD under K1, K3a, K3h, K4 and K5 as drafted.
  - Fz: Fc with mean_rate zeroed where a side truncates (FSD under K2z).
  - Fx: Fc, excluding any design where FSD's own parametric or reference side truncates or stops on any of its 16 replicates.
- **Harness.** No src edit. It follows `fsd-trunc/measure_trunc.py`:
  - Each run is simulated once by `run_experiment_truncating` (cap `POLICY_MAX_EVENTS` = 15,000; stops are `policies.STOPPED`) and read on `view.clip_to_analysis`, with all 35 menu diagnostics stored.
  - Rules are applied afterwards with `battery.hellinger2`, `policies.choose` (e-major) and `Telling.from_table` (d-major). Rule code is ported from `fsd-rule/candidates.py`, which the red team reproduced with 0 mismatches.
  - A hit means the labels are equal (name plus channel arguments, as `telling.observed`).
  - Random is `RandomPolicy`'s draw (policies.py:968-972). The control-5 verdict is `controls_p4.policy_separation`.
- **Streams.**
  - telling: `scorer_seed(TELLING, id, s, side, e, r)`, r < 32.
  - truthFSD and FSD: `policy_seed(s, k, PARAMETRIC_INDEX | REFERENCE_INDEX, r)`, r < 16, decisions k = 0..7 (`fsd_table` with an injected comparator and reference).
  - Menu: `diagnostic_menu` on the observational log of `World(truth, s, truncate=True)`.
  - Calibration: `scorer_seed(TELLING, id+"-cal", 0, side, e, r)`, r < 256. Bootstrap: `generator(scorer_seed(TELLING, id+"-boot", 0))`.
  - Seed ranges are disjoint between controls, because policy streams carry no truth id (seeds.py docstring).
- **Ground-truth position.** telling.py:14-24 says a design where a side stops on every run "says the design is telling but not which diagnostic is". Both controls apply this to truncation too. Under the opposite position (the explosion *is* the telling fact), K1 passes A1. Choosing between the two positions is a SPEC §4.2 decision for the owner; this control does not make it.
- **Planted d\* (D\*₀), fixed by calibration before the test.**
  - E_cal is the set of designs where any of 256 calibration runs of either side truncates or stops.
  - p_R(ℓ) is the share of 1,000 subsamples (R per side, without replacement) whose argmax over designs outside E_cal has label ℓ. The argmax is d-major at R = 32 and e-major at R = 16.
  - D\*₀ is the smallest set of labels, taken by decreasing p₃₂, with Σp₃₂ ≥ 0.95.
- **Preconditions.**
  - P1: truncation happens exactly as built (256/256 where planned, 0 elsewhere, 0 stops).
  - P2: \|D\*₀\| ≤ 2; every label in D\*₀ is a `[sign]` diagnostic; mean_rate ∉ D\*₀.
  - P3: a := Σ_ℓ p₁₆(ℓ)·p₃₂(ℓ) ≥ 0.8, and at least 99% of 16-vs-16 subsamples have no cell exactly 1.0 outside the planned capped rows.
  - If a precondition fails, move to the next rung of the build's ladder. If every rung fails, the control is **VOID**: that is a finding about He² resolution at R = 32, not a pass or a fail for any rule.
  - `D0-<control>.json` and its sha256 are appended here before any test stream is drawn.

## 1. Control A (known d\*): built so that K0, K1 and the exclusion rules answer differently

**A1 build.**
- Truth T_A has dev-002's structure, `link=identity; Excite(ExpK, One, sign=+) + Excite(ExpK, One, all)`, with exp_rate 1.0 on both kernels and θ = (θ₊, θ_all) = (0.5, 0.6), θ₀ = 0.15.
  - Branching: 0.85 observational, 1.10 under inject_sign_high (e1), 0.60 under inject_sign_low.
- Member M_A is the library HAWKES structure with θ = 0.85, θ₀ = 0.15 and exp_rate 1.0. It is fixed, and serves both as telling's member and as truthFSD's comparator. It stands in for B-lib's pick: B-lib picks hawkes on dev-002 (`fsd-trunc/*.truths.jsonl`).
- Ladder: θ₊ = 0.5, then 0.4, then 0.6, with θ_all = 0.85 − θ₊/2.
- Probe evidence:
  - T_A truncates on e1 in 8/8, 16/16 and 64/64 probe runs (τ 833–967 over probe3's 64) and nowhere else; M_A never truncates.
  - The top uncapped cell is rate_after_mark_slope[sign]: He² 0.990, D = −ln ρ = 4.63, with the next cell at D 2.23. The top 6 cells are all `[sign]` (R = 8).
  - At θ₊ = 0.6 the top cell is spectral_peak_frequency (D 5.73). This is why P2 is needed.

**A1 predictions** (seeds 101–110):

| rule | telling d\* | truthFSD pick (every decision) | (i) | (ii) | (iii) | verdict |
|---|---|---|---|---|---|---|
| K0 | in D\*₀: (b) zeroes the e1 row | (e1, mean_rate): atom row, He² = 1 exactly | pass | ≈ 0 | fail (b = 0) | FAIL |
| K1 | (e1, mean_rate), forced (cap/τ ≥ 7.5) | (e1, mean_rate) | 0/10 | ≈ 80/80 | pass | FAIL |
| K2z | (e1, mark_mean[sign]), probable (probe2: next exact 1.0) | same | ≈ 0/10 | high | pass | FAIL |
| K3a, K3h, K4, K5 | in D\*₀ (e1 excluded) | in D\*₀ | pass | ≥ a | pass | PASS; A1 cannot tell these four apart |

**A1 criteria** (fixed now; a rule passes iff (i), (ii) and (iii) all hold):
- (i) Telling's label is in D\*₀ on at least 8/10 seeds. P = 0.989 if p = 0.95.
- (ii) truthFSD's label equals telling's on at least 56 of 80 (seed, decision) pairs. P = 0.989 if a = 0.8.
- (iii) `policy_separation(truthFSD, random)` passes. This is control 5 at real R: truthFSD at R = 16 on policy streams over 8 decisions, against telling at R = 32 on scorer streams, with N = 10 units.
  - P(pass) is 0.985 when the per-unit hit rate is ≥ 0.99, and 0.005 when it is no better than random's 0.207. At least 5 discordant units are needed.
- K1 is predicted to pass (iii) on the cap. That is the demonstration that control 5 alone cannot catch a cap-forced d\*.
- The K0 caveat: if any test-stream e1 truth run does not truncate (0 of 88 in the probes), (b) keeps it and e1 is not zeroed (the 1-of-R cliff). That case is scored as observed.

**A2, partial cap (K4 against K5).**
- Build: T_A with θ₊ = 0.27 + 0.005j for j = 0..5, θ_all = 0.85 − θ₊/2. Take the first rung where 256 calibration runs on e1 give π̂ ∈ [0.02, 0.08]. The probe gave 0/12 truncations at branching 0.98 and 6/12 at 1.00. Seeds 201–205.
- A flip is a pair where truthFSD's and telling's excluded sets differ *and* their labels differ. A rule passes A2 iff it has ≤ 2 flips out of 40.
- Predictions:
  - K5: 0 flips (proved: X is shared).
  - K4: exclusion disagreement is 0.463 per decision at π = 0.05 (0.53 at 0.03). K4 fails if a kept e1 wins truthFSD's argmax in at least about 11% of mismatched decisions. This is not predicted; A2 measures it.
  - K3h keeps e1 in both arms, so it has 0 flips, but its d\* may sit on a partially truncated design. That is reported, not judged, because a partial-cap ground truth is undefined.
- Control 5's "ever in 8 decisions" read-out is blind to all of this: truthFSD excludes e1 at least once with probability 0.9986 at π = 0.05.

**Stops, K3a against K4/K5: proved, no CPU.**
- If every truth replicate of design e stops, K3a keeps e with p = 1, q = 0. The overlap is then 0 on all 35 cells (battery.py:361-374), so from_table returns mean_rate (d = 0). K4 and K5 skip e.
- This needs a stub-simulator unit test, which goes to the engineer. The measured instance is dev-015 (`fsd-rule/draft.md` §2).

## 2. Control B (the dev-005 shape: the reference truncates where truth and member do not)

**Build.**
- Truth T_B: `link=identity; Excite(ExpK, One, sign=-) + Excite(ExpK, One, all)` with θ = (0.5, 0.6), θ₀ = 0.15 and exp_rate 1.0. It truncates only under inject_sign_low (e2). Ladder: θ₋ = 0.5, then 0.4, then 0.6.
- Member M_B = M_A.
- FSD's parametric side is M_B (fsd-trunc's B-lib stand-in).
- Reference Ref_B is design-switching: T_A on inject_sign_high (e1, menu index 1) and T_B on every other design.
  - So Ref_B truncates on e2, as the truth does (a real falsification).
  - It also truncates on e1, where neither truth nor member does. This is dev-005's artefact: WH truncated 16/16 there, the truth 0/4.
  - Off e1, FSD's table has truthFSD's law. A planted reference gives a known answer; real WH does not.
- Probe evidence:
  - T_B truncates 8/8 on e2 (smallest τ 846) and nowhere else. Its top cells are `[sign]` (He² 0.918, D 2.50).
  - T_A truncates 16/16 on e1.
  - The labels exactly 1.0 on that row are mean_rate, mark_mean[sign], log_gap_cv, burstiness, uniformity_ks and gap_exponential_ks.
- Preconditions: P1–P3 for T_B against M_B, plus two more:
  - p_F (the chance that Fx's e-major pick at R = 16 over designs other than e1 and e2 lands in D\*₀(B)) is at least 0.5;
  - no cell on burst (row 0, the only row before e1) is exactly 1.0 in at least 99% of subsamples. This stops menu order from passing a rule by luck, as it did at dev-005.

**Predictions** (seeds 301–310):
- FSD:
  - Fa picks (e1, mean_rate): the e1 and e2 rows are atoms, and e1 comes first.
  - Fc picks (e1, mean_rate): forced 1.0 at d = 0.
  - Fz picks (e1, mark_mean[sign]), probably.
  - Fx never picks e1 or e2; its label is in D\*₀(B) with probability p_F.
- Telling:
  - K0, K3a, K3h, K4 and K5 give a label in D\*₀(B).
  - K1 gives (e2, mean_rate).
  - K2z gives (e2, mark_mean[sign]), probably.

**Criteria**, per (FSD rule, telling rule); a pair passes iff B1, B2 and B3 all hold:
- B1: FSD's design is not e1 on at least 72/80 pairs.
- B2: no unit is credited with d\* *only* through picks on e1.
- B3: FSD is credited on at least 8/10 units, and `policy_separation(FSD, random)` passes.

**Predicted outcomes:**
- Fx with K0's telling, K3\*, K4 or K5: PASS.
- Fa, Fc and Fz fail B1 with any telling rule.
- (Fa or Fc) with K1 also fails B2 on 10/10 units: FSD's mean_rate on e1 matches d\* = mean_rate on e2 by label alone.
- Fz with K2z fails B2 if both land on mark_mean[sign].
- Fx with K1 fails B3, because Fx never picks mean_rate.
- So B separates Fx from Fa, Fc and Fz, and separates K1 (with label-only matching) from K0, K4 and K5.
- B1 is fixed by construction for Fx and by the forcing fact for Fc. The empirical content is in B2 and B3.

## 3. Cost, seeds, R, commands

| control | test seeds | R | runs | CPU h (estimate) |
|---|---|---|---|---|
| A1 | 101–110 | telling 32; truthFSD 16 × 8 decisions | 4,096 (calibration) + 10 × 2,560 | 0.16 + 1.01 |
| A2 | 201–205 | as A1 | up to 6 × 256 (π ladder, e1 truth only) + 5 × 2,560 | ≤ 0.23 + 0.46 |
| B | 301–310 | telling 32; FSD 16 × 8 decisions | 4,096 + 10 × 2,560 | 0.16 + 1.23 |

- Per-run CPU comes from the probes' medians, including the 35 diagnostics: 0.07–0.17 s for an uncapped run, 0.69–0.97 s for a truncated run, 0.53 s near criticality. The arithmetic is `cost.py` → `cost-out.txt`.
- Total 3.25 CPU h, or 4.23 with a 30% margin; ceiling **4.5 CPU h**. Wall time about 2–2.5 h on 2 processes at nice 10 in `tmux -L sciagent`. No AWS launch; $0 marginal on dm-devbox.
- `controls.py` does not exist yet; it is written next round. Its contract:
  - one line per (control, seed, decision or telling, design, side, replicate), resumable, as `measure_trunc.py`;
  - a self-check that runs below the cap are byte-identical to `run_experiment`.

```sh
WT=/home/ubuntu/lanes/sciagent/.claude/worktrees/fsd-dist; D=/home/ubuntu/lanes/sciagent/.cache/drafts/fsd-controls
P="env OPENBLAS_NUM_THREADS=1 nice -n 10 uv run --project $WT --no-sync python -I $D/controls.py --worktree $WT"
$P --control A1 --phase calibrate --reps 256   # writes D0-A1.json, then stops; append its sha256 to this file
$P --control A1 --phase test --seeds 101-110   # refuses unless that sha256 is in criteria.md
$P --control A1 --phase score > $D/score-A1.txt
```

Run B the same way (seeds 301–310) in a second window, then A2 (seeds 201–205; its calibrate phase is the π ladder).

## 4. Reading the outcome (fixed now)

**A1.**
- If K0 fails (ii)/(iii), K1 and K2z fail (i), and the exclusion rules pass, that favours exclusion over K0 and K1 under the docstring's position. It does not separate K3a, K3h, K4 and K5.
- If K1 passes (iii), that is not evidence for K1; it shows only that control 5 cannot see a cap-forced d\*.
- If an exclusion rule fails (i) with P1–P3 met, no candidate is ready. The defect is He² resolution, so the (He², D) ordering from the ties draft comes first and the rule question reopens. The thresholds are not changed.

**A2.**
- K5 with 0 flips and K4 with more than 2 favours K5.
- If K4 has 2 or fewer flips, R-dependence did not matter at the measured π, and K4 against K5 is decided on plumbing cost alone.

**B.**
- If Fx passes B1–B3 while Fa, Fc and Fz fail B1, FSD needs its own exclusion whatever the telling rule.
- If (Fa or Fc) with K1 fails B2, K1 is rejected for as long as `observed()` ignores the design. If the owner prefers K1's position, design-aware matching becomes a precondition for adopting it.
- If Fx with K1 fails B3 while Fx with K5 passes, that favours K5 again.

**Throughout.**
- A VOID control favours nothing.
- Any other pattern is reported as unpredicted, and no rule is chosen on it.
- Fx's cost to the held-out gap (C5's first read-out) is measured by neither control.

## Not proved or not covered

- He²(mean_rate) = 1.0 on truncated rows is measured (every probe), and proved only for the exact He².
- The K2z and Fz labels come from one R = 16 probe.
- The controls use no real WH, no fitted B-lib member and no B-sym top fit: they test rules, not estimators.
- The partial-cap ground truth is a SPEC §4.2 question.
- The stop case rests on the proof alone.
- Only one cap, horizon and environment are covered, and only one planted mechanism (sign gating).

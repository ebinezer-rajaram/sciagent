# Positive controls for the at-stop rule (FSD, truthFSD, telling): preregistered criteria, v8

v8 (2026-10-29, theorist) is v7 (`criteria-v7.md`, sha256 9c15e47e…, commit ecb909a) with `redteam-s18.md`'s four blocking fixes, its cheap non-blocking fixes, and the engineer's seven v7 gaps (coordinator 09:20Z). Following the owner (LOG 2026-10-29, d550bc4), there are no veto windows: v7 had no veto wait, and v8 adds none. There is one review, joint with the controls.py code review. §0's rules, §1 and §2's controls, thresholds and seeds are unchanged; the one addition there is a third reading for B rungs (E2). New evidence is in $D/v8-checks/: `breach_model.py`, `run_big-out.txt`, `pvoid-out.txt`, and the scratch-git scripts `lib8.sh`, `scen8.sh`, `s2diag.sh`, `refined.sh`, `s8.sh`, and `lib8c.sh` with `refined-c.sh` (v8's exact content-compare C7). These were written for /tmp/fsd-v8-check and /tmp/fsd-v8-theory; copy them there before rerunning. They use local bare repositories only.

**Changelog v7 → v8 (each item and its disposition).**
- **s18 blocking 1 (merge topology, deleted LEDGER rows): adopted, with changes (§5).**
  - The chain is every non-merge commit that ever changed controls.py, as the red team proposed.
  - The rows count if they were ever added, read with `--full-history --no-merges`.
  - The test-data rule is the red team's.
  - Changed after the scratch check: the "nothing after F touches frozen files" check compares content (F = O = HEAD = working tree), not history. The history form refused an honest merge (scen8 S2).
  - D-candidates are restricted to non-merge commits whose tree holds the file.
  - Added: the force-push assumption is now an owner precondition (H1) plus a detection check on GitHub's activity log (H2).
- **s18 blocking 2 (D0 seen without a push): adopted (calibrate pushes D itself, by plumbing), with changes (§5 "D").**
  - Up to 20 attempts, not 1.
  - Success means D is an ancestor of a refetched tip, so a push whose reply was lost still counts.
  - The gate is rerun just before D is built.
  - Nothing reaches stdout, stderr or the CPU ledger before the push either.
  - calibrate refuses if a D already exists, and the restore recipe is given.
  - The two alternatives are rejected with reasons in §5.
- **s18 blocking 3 (failed fetch; racing fetches): adopted, with changes.**
  - The fetch goes into a private ref, `refs/fsd-controls/v2`, which the lane never touches, with `--no-write-fetch-head`.
  - O is held in memory.
  - A flock on `git.lock` serialises the two processes.
  - A fetch failure exits 10.
- **s18 blocking 4 (an honest re-freeze cannot rerun): adopted.**
  - F's tree holds no run output.
  - A re-freeze first removes tracked D0 and score files and archives the untracked outputs.
  - Added: runs files carry an {F, D} header, so test never resumes F's runs under F′.
- **s18 non-blocking 1 (exit codes): adopted.**
  - `result` is authoritative; any other exit code is a crash.
  - Every git or gh failure exits 10.
  - B's reference-check VOID exits 12.
  - calibrate exits 0, 12 or 13.
  - score exits the terminal code, 15 taking precedence over 14. This changes v7, where score exited 0 on an untested control; it now exits 12 or 13.
- **s18 non-blocking 2 (breach VOID unpriced): priced (§0).** A reduced branching model gives these per-run miss rates:
  - 7.5e-5 at θ = 0.5;
  - 1.05e-2 at θ = 0.4;
  - ≤ 1.5e-5 at θ = 0.6.

  P(VOID by breaches | tested) is about 7e-4 (A1) and 3.7e-3 (B) on rung 0. The θ = 0.4 rung fails P1 with P ≈ 0.996, so no truncation probe is needed before freeze. v4's "a VOID writes its evidence and no rule results" is restored, and L77 is corrected.
- **s18 non-blocking 3 (ceiling and resume): adopted.**
  - There is no `--cpu-ceiling-h` flag; the ceiling is read from LEDGER rows.
  - A resume is a `fsd-controls ceiling <c> CPU h` row.
  - cpu-total.txt is created once, before F₀.
- **s18 non-blocking 4 (stale version numbers): fixed.** §5 no longer names a version.
- **s18 non-blocking 5: all adopted.**
  - Internal functions refuse the real control ids without the gate's token.
  - Score files are committed, and runs files are copied to S3.
  - Every F changes controls.py, which holds `CRITERIA_SHA256`.
  - D has a single parent.
  - The LEDGER column is `item`.
  - The stub-test list matches the contract.
  - The contract is updated.
- **Engineer E1 (`python -I` ignores PYTHONDONTWRITEBYTECODE and PYTHONPYCACHEPREFIX): adopted as the engineer built it (§7).** controls.py sets `sys.dont_write_bytecode` and `sys.pycache_prefix` before any import from $D or $WT, and the gate requires `sys.flags.isolated`.
- **E2 (a B rung with h, h_F ≥ 0.95 failing only pass_iii_F fits no reading): closed.** It is now a **separation shortfall** (§0), which ranks no rule.
- **E3 (resume rows): settled with s18 non-blocking 3.** A freeze sha may appear in more than one row, and is no longer refused. Ceiling rows never contain `freeze`.
- **E4 (calibration runs kept in `runs-<C>-cal.jsonl` and resumed): rejected.** That file is as revealing as D0 and would reopen blocking 2. calibrate keeps everything in memory and is not resumable; a STOP or crash before the push loses at most about 1.1 (A1) or 1.6 (B) CPU h.
- **E5: adopted.** `cpu-total.txt.tmp` and `git.lock` are run outputs.
- **E6: adopted as built.**
  - `"status"` is "preregistered" or "exploratory deviation"; the outcome is `"result": {"code", "name"}`.
  - calibrate exits 0, 12 or 13; 15 beats 14.
  - A missing cpu-total exits 10.
  - score on an unfinished test exits 11.
- **E7 (excluded designs removed from the argmax; candidates.py zeroed them; parity is 72/72 either way): confirmed (removal).** Parity cannot tell the two apart, so a stub test must (§7).

Earlier versions: v7 `criteria-v7.md` (9c15e47e…); v6 `criteria-v6.md` (3e30503f…); v5 (64fcabe2…); v4 (42828142…). The red-team record is `redteam-s17*.md` and `redteam-s18.md`. No control stream has been drawn. Evidence, all in $D: `probe*-out.txt`, `power2-out.txt`, `power3-out.txt`, `a2band-out.txt`, `bench-out.txt`, `cost5-out.txt`, `random-picks.txt` (sha256 33f80c78…), `v8-checks/`.

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
- Held out (feeding P2, P3 and P3-B): p₃₂(ℓ) from per-design indices 128–255 (32-of-128); p₁₆(ℓ) from CRN indices 128–255 (16-of-128, the same indices for every design, e-major over the designs outside E_cal); h(ℓ) = 1 − (1 − p₁₆(ℓ))^8; h = Σ_ℓ p₃₂(ℓ)·h(ℓ).
- The 99% clause: in at least 990 of 1,000 16-vs-16 subsamples (CRN indices 128–255), no cell outside the planned truncated rows is exactly 1.0.
- These are plug-in estimates. A held-out estimate of at least 0.95 occurs with P about 0.40 when the true coverage is 0.90 (128-run pool; redteam-s17d 5). The powers below are at the estimate, not guarantees.

**Preconditions.**
- P1: the truncation pattern is exactly as built in both sets (256/256 truncated where planned; elsewhere no truncation, cut0 or stop; the member never).
- P2: \|D\*₀\| ≤ 2; every label in D\*₀ carries the planted channel (`[sign]`; `[size]` on B's size rung); mean_rate ∉ D\*₀; held-out coverage Σ_{ℓ∈D\*₀} p₃₂(ℓ) ≥ 0.95.
- P3 (A1): h ≥ 0.95; `pass_ii(15, p₃₂, p₁₆)` ≥ 0.95; `pass_iii(101..115, p₃₂, h(·))` ≥ 0.95 (`power2.py`: exact, clustered, given random's fixed picks); and the 99% clause.

**Rungs and their reading (redteam-s17d 3, 7).**
- Every rung of A1 and of B is calibrated, and every rung's P1, P2 and P3 (or P3-B) values go into `D0-<C>.json`.
- The test uses the first rung, in ladder order, that passes all its preconditions.
- A rung is **unbuildable** if it fails P1, P2 or the 99% clause (N3).
- Calibration makes a control VOID (12) only if every rung is unbuildable or, for B, the reference check fails at both θ₊ = 0.5 and 0.6. Test and score can VOID it later (14, 15).
- A buildable rung that fails the rest of P3 (A1: h, pass_ii or pass_iii) is a measured **R = 16 shortfall**: truthFSD at R = 16 would not reproduce telling's label at the required rate.
- A buildable B rung that fails P3-B is read through B's own h (from p₁₆, as in A1), in this order:
  - if h < 0.95, it is an R = 16 shortfall, as in A1;
  - otherwise, if h_F < 0.95, it is a **cost of Fx's exclusion**;
  - otherwise (h, h_F ≥ 0.95, so pass_iii_F < 0.95), it is a **separation shortfall**. Fx keeps telling's label at the required per-decision rate, but B3's McNemar test against random's fixed picks has no power at 15 seeds. It ranks no rule. (v8, E2.)
- If no rung passes everything but at least one is buildable, the test is not run, and the buildable rungs' values are the control's result.
- **Result and exit codes (every phase).**
  - Every output has two fields:
    - `"status"`: "preregistered" or "exploratory deviation" (§5);
    - `"result"`: {"code", "name"}.
  - `result` is authoritative; exit codes only signal it.
  - The codes:
    - 0 done;
    - 10 gate refusal: any failed check, any failed git, gh, fetch or push, and a missing or unparseable cpu-total.txt;
    - 11 STOP or INCOMPLETE (this includes score on a control whose test has not finished);
    - 12 VOID at construction (above);
    - 13 not tested: no rung passes, at least one is buildable;
    - 14 VOID by test-time P1 breaches;
    - 15 VOID by a determinism mismatch.
  - Any other exit code means the process crashed: Python's 1 and 2, uv's 2, or a signal's 128 + n. A crash's partial outputs are not results. The phase is rerun (test resumes; calibrate starts over, §5). A crash that needs a code change needs F′.
  - calibrate exits 0, 12 or 13, after D is on origin/v2 and D0 is written (§5).
  - test exits 12 or 13 at once, drawing nothing, if that is D0's result.
  - score exits with the control's terminal code (0, 12, 13, 14 or 15), after writing its files.
    - It checks determinism first and breaches second, so 15 takes precedence over 14.
    - On 14 or 15, the score files hold only the result and its evidence: the mismatching lines, and the breaches by seed. No rule is scored (restored from v4).
    - On 12 or 13, they hold D0's per-rung values.
- **Test-time P1 breach** (restored from v4). This is a truncation, cut0 or stop where calibration had none, or a run that does not truncate where calibration had 256/256.
  - It is scored as observed under every rule and listed by seed in the score files.
  - If breaches touch more than 2 of the 15 seeds, the control is VOID (14).
- **The breach rule's price (s18 non-blocking 2).** The model is a reduced one (`v8-checks/breach_model.py`), not the simulator.
  - **What calibration alone can show.** 512 clean planned runs per rung bound the per-run miss rate only to ≤ 5.8e-3 (95%). Keeping P(VOID) ≤ 0.05 needs ≤ 3.7e-4 (A1: 160 planned runs per seed).
  - **The model.** It is a linear Hawkes process: μ = 0.15, kernel m·e^{−t}, every event sign + in the intervention window [800, 1200) of the 2,000 horizon, cap 15,000, simulated exactly and seeded.
    - It reproduces probe3's earliest τ: 832 against 833 at θ₊ = 0.5, and 824 against 831 at θ₊ = 0.6.
    - Its per-run miss rates are:
      - 7.5e-5 at in-window branching 1.10 (θ = 0.5; 15 in 200,000);
      - 1.05e-2 at 1.05 (θ = 0.4; 526 in 50,000);
      - none in 200,000 at 1.15 (θ = 0.6; ≤ 1.5e-5).
    - The same rates hold for B's θ₋ rungs (`run_big-out.txt`).
  - **What follows (`pvoid-out.txt`).**
    - θ = 0.5: P1 passes with P ≈ 0.96 (A1) and 0.94 (B, with the reference check). P(VOID by breaches | tested) ≈ 7e-4 (A1) and 3.7e-3 (B).
    - θ = 0.4: P1 fails with P ≈ 0.996, so this rung is almost always unbuildable; it wastes about 0.35 CPU h of calibration per control. Being built and then VOIDed has P ≤ 0.0045.
    - θ = 0.6: P(VOID | tested) ≤ 7e-4.
  - **Decision.** No truncation probe is needed before freeze. Dropping the θ = 0.4 rung would change §1 and §2 (not a blocking fix), so it is left to the coordinator.
  - **Unpriced.** Stops (none has been observed), cut0 on the planned design, and the model's late tail. probe3's latest τ at θ₊ = 0.6 (1052 in 64 runs) lies beyond the model's 99th percentile (963), so the model may understate late truncation.

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
- A buildable θ₋ rung that fails P3-B with h ≥ 0.95 > h_F: Fx misses telling's label when the artefact sits on the truth's top design (a cost of Fx's exclusion). With h < 0.95, it is an R = 16 shortfall.
- A buildable size rung that fails P3-B is read the same way, through h. There the artefact is off the top design, so the most likely reading is plain R = 16 resolution; no stronger cost is claimed.
- A buildable rung (θ₋ or size) with h ≥ 0.95 and h_F ≥ 0.95 that fails P3-B, so pass_iii_F alone fails, is a separation shortfall (§0, v8). It ranks no rule.
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


## 5. Procedure: sequence, gate, deviations (redteam-s17d 1, 2; s17f 1; s18 1–4)

**What the gate guarantees.**
- The documented sequence is the only one that passes. Every shortcut red teams s17f and s18 found is either refused or labelled exploratory.
- Every D0 that controls.py shows is already on origin/v2.
- The label is computed from origin/v2's history, which branch protection (H1) and the activity check (H2) keep append-only.
- It is not a security boundary. The stream seeds are in this file, so anyone can draw the streams with their own code, read a process's memory or watch its CPU time. Those are outside the guarantee.

**Notation.**
- $P = `.cache/drafts/fsd-controls` (the path as git prints it; `git -C $R` resolves it from $R).
- URL = `https://github.com/ebinezer-rajaram/sciagent.git`.
- O = the sha of v2 on GitHub, as this process fetched it (below).
- **Rows ever added** = the `+` lines (not `+++`) of `git -C $R log --full-history --no-merges -p --format= O -- docs/v2/LEDGER.csv`, read as CSV. A row's `item` is its second field. Editing or deleting a row later changes nothing.

**Owner precondition (owner action; not yet done; nothing runs until it is).**
- The owner turns on GitHub branch protection for v2:
  - no force pushes;
  - no deletion;
  - enforced for administrators. The machine's gh token has admin rights, so without enforcement it could still force-push.
- The command is below; the equivalent is a rule for v2 in Settings → Branches with "Do not allow bypassing the above settings" ticked. Direct pushes to v2, the lane's and calibrate's, stay allowed.

  ```sh
  gh api -X PUT repos/ebinezer-rajaram/sciagent/branches/v2/protection --input - <<'EOF'
  {"required_status_checks":null,"enforce_admins":true,"required_pull_request_reviews":null,"restrictions":null,"allow_force_pushes":false,"allow_deletions":false}
  EOF
  ```
- State on 2026-10-29 at 09:18Z:
  - `gh api repos/ebinezer-rajaram/sciagent/branches/v2 --jq .protected` printed `false`;
  - `…/branches/v2/protection` returned 404 "Branch not protected";
  - the activity log held 0 force pushes and 0 branch deletions in 86 v2 activities, back to the branch's creation on 2026-10-02.
- F₀ may be committed before the owner acts, but every phase exits 10 at H1 until then.

**Sequence.**
1. The coordinator commits this file as the design record. This is not a freeze.
2. The engineer writes `controls.py`, `ctl_gate.py`, `test_controls.py` and `parity/` in $D to §7.
   - `controls.py` is never committed before F₀, because every commit that ever touched it must be a freeze (C1–C3).
   - The tests pass, and their output is `test_controls-out.txt`.
3. One review, joint with the controls.py code review (owner, LOG 2026-10-29). Its fixes go in before F₀ and are listed in this file's changelog. There is no further design round.
4. Before F₀ only, the coordinator creates `$D/cpu-total.txt` containing `0.0`. This is never repeated (§6).
5. F (F₀, or a later F′ per Deviations) is one non-merge commit (`git add -f`).
   - It changes controls.py and holds every file in $D except the run outputs: `__pycache__/`, `.ruff_cache/`, `*-err.txt`, `cpu-total.txt`, `cpu-total.txt.tmp`, `cpu-total.lock`, `cpu-ledger.csv`, `git.lock`, `STOP`, `D0-*.json`, `runs-*.jsonl`, `score-*.*`.
   - F's tree under $P holds no run-output path.
   - F's message carries `criteria.md sha256: <h>` and `controls.py sha256: <h>`.
   - controls.py holds the constants `VERSION` (this file's version) and `CRITERIA_SHA256` (this file's hash), so every new version of this file changes controls.py and makes a new freeze.
6. F is pushed (`git push origin v2`, plain). The coordinator then pushes a LEDGER row:
   - `item` = `fsd-controls freeze <F's full sha> ceiling <c> CPU h`, where c is the cumulative ceiling (7.6 for F₀);
   - hours 0 and usd 0. The CPU used gets its own row afterwards.
   - There is no veto wait: calibrate may start once the row is on origin/v2 (owner, LOG 2026-10-29, d550bc4).
7. calibrate computes D0 in memory, pushes D itself, and only then writes D0 (below).
8. test writes `runs-<C>.jsonl`.
9. score writes `score-<C>.json` and `score-<C>.txt`. The coordinator then:
   - commits the two score files (`git add -f`, nothing else) and pushes them;
   - copies `runs-<C>.jsonl` to `s3://delaymargin-results-482913/sciagent/fsd-controls/<F short sha>/` and adds the usual LEDGER row. The score files hold the runs file's sha256.

**Fetch (every phase, first; s18 3).**
- Under an exclusive `fcntl.flock` on `$D/git.lock`, controls.py runs `git -C $R fetch --no-tags --no-write-fetch-head URL +refs/heads/v2:refs/fsd-controls/v2`, with a 120 s timeout.
  - Both controls.py processes take the lock; the lane's git commands do not.
  - In calibrate, the lock is also held while D is built and pushed.
- A nonzero exit or a timeout refuses (10).
- O = `git -C $R rev-parse refs/fsd-controls/v2`, read under the lock and held in memory.
- Every check below uses the sha O. Nothing reads `origin/v2`, `FETCH_HEAD` or the configured remote, so a hand-moved ref or a re-pointed remote changes nothing (scratch S7).

**History (every phase; owner precondition and detection).**
- H1: `gh api repos/ebinezer-rajaram/sciagent/branches/v2/protection --jq '[.allow_force_pushes.enabled,.allow_deletions.enabled,.enforce_admins.enabled]'` prints `[false,false,true]`.
- H2: `gh api 'repos/ebinezer-rajaram/sciagent/activity?ref=refs/heads/v2&activity_type=force_push&per_page=100' --jq length` prints `0`, and so does the same call with `activity_type=branch_deletion`.
  - This is the detection layer. It holds even if protection was off for a while, since an admin token can switch protection off.
- Any `gh` failure refuses (10). H1's and H2's outputs go into D0 and the score files.

**Freeze chain (every phase; s18 1).**
- C1: the freezes F₀, F₁, …, F_k are `git -C $R rev-list --full-history --no-merges --topo-order --reverse O -- $P/controls.py`: every non-merge commit on origin/v2 that ever changed controls.py, oldest first. There is at least one.
- C2: each F_j is an ancestor of F_{j+1} (`git -C $R merge-base --is-ancestor`).
- C3: each F_j is named by a row ever added whose `item` contains `fsd-controls freeze <F_j full sha>`.
  - A sha may be named by more than one row.
  - Rows naming any other sha are listed in the outputs and otherwise ignored.
- C4: `--freeze-commit F` equals F_k, the newest freeze.
- C5: the sha256 of criteria.md and controls.py on disk equal the lines in F's message, and criteria.md's hash equals controls.py's `CRITERIA_SHA256`.
- C6: `<frozen>` = `git -C $R ls-tree -r --name-only F -- $P`. No path in it matches a run-output pattern.
- C7: the frozen files are identical in F, O, HEAD and the working tree:
  - `git -C $R diff --quiet F O -- <frozen>`;
  - `git -C $R diff --quiet F HEAD -- <frozen>`;
  - `git -C $R diff --quiet F -- <frozen>`.

  This compares content, not history. A history form refuses an honest merge of F (scratch S2). A change and revert in between is harmless, because C1–C3 catch any commit that touches controls.py.
- C8: `git -C $R status --porcelain --ignored -- $P` lists only `!!` entries matching the run-output patterns. Tracked, unmodified D0 or score files (after a pull) are not listed.
- C9: $WT and the runtime:
  - `git -C $WT rev-parse HEAD` = bff61ecba3daed876e55896e329000704fb9626b;
  - `git -C $WT status --porcelain --ignored -- src` lists nothing but `!!` entries ending in `__pycache__/`. Those exist today from earlier imports: 21 directories (redteam-s17e);
  - the runtime checks in §7 (isolated mode, bytecode, thread variables).
- Any git command that fails refuses (10).

**Test-data status (every phase; s18 1).**
- Under F = F_k, control C has test data from an earlier freeze iff some commit X in `git -C $R rev-list --full-history O -- $P/D0-<C>.json` is not a descendant of F (`git -C $R merge-base --is-ancestor F X` fails).
- If so, every output of every phase under F carries `"status": "exploratory deviation"`; otherwise `"status": "preregistered"`.
- The score files list every such X with its descent flag, so the status can be recomputed from origin/v2.
- Why it is sound:
  - Under the D rule below, a D0 becomes visible only once it is on origin/v2.
  - calibrate builds D on a tip it has just checked, at which F is the newest freeze. So a D made under F descends from F and never counts.
  - A D made under an earlier F_j cannot descend from F: calibrate under F_j would have failed C4 at its last fetch.
  - Merges and deletions are counted by the same rule. A deletion follows an addition, which already counts, so it never changes the status.
- Scratch checks (`scen8.sh`; `refined-c.sh` with v8's exact C7):

  | case | result |
  |---|---|
  | linear re-freeze after a pushed D | exploratory |
  | F′ made on a branch and merged | exploratory |
  | F′ branched from before F₀ | refused (C2) |
  | calibrate killed before its push, then rerun under the same F | preregistered |
  | calibrate killed before its push, then a re-freeze | preregistered |

**D, the D0 commit (calibrate; s18 2).**
- **calibrate refuses (10) at start if a D under F exists.** A D under F is a non-merge commit X in `git -C $R rev-list --full-history --no-merges O -- $P/D0-<C>.json` that descends from F and whose tree holds the file (`git -C $R cat-file -e X:$P/D0-<C>.json`). calibrate prints X, and the coordinator restores D0 with `git -C $R show X:$P/D0-<C>.json > $D/D0-<C>.json` (scratch S9).
- **Nothing is shown before D is on origin/v2.**
  - calibrate holds every run, outcome, rung value and result in memory. None of them reaches a file, stdout or stderr until D is on origin/v2.
  - Its progress lines carry counts only. Its CPU-ledger lines carry no outcome (§6).
  - There is no calibration runs file, and calibrate is not resumable (E4).
- **The D build.** When the computation ends, under `git.lock`:
  1. Fetch (as above), and rerun every gate check at the new O. Any failure exits 10, with nothing written.
  2. Build D without touching HEAD, the index or the working tree. With `GIT_INDEX_FILE` a temporary file outside $R:
     - `read-tree O`;
     - `hash-object -w --stdin` (D0's bytes);
     - `update-index --add --cacheinfo 100644,<blob>,$P/D0-<C>.json`;
     - `write-tree`;
     - `commit-tree <tree> -p O`, with first line `fsd-controls D0 <C> under <F full sha>` and a line `D0-<C>.json sha256: <h>`.
  3. `git -C $R push URL <D>:refs/heads/v2`, plain (never `+`, never `--force`), with a 120 s timeout.
  4. Fetch again. The push succeeded iff D is an ancestor of the new O. This also catches a push the server accepted but whose reply was lost.
  5. Otherwise, release the lock, wait 30 s, and repeat from 1, at most 20 times.
     - A rejected non-fast-forward push is the normal case when the lane or the other control pushed first.
     - D is rebuilt on the new tip and never conflicts, since no other commit touches D0-<C>.json (scratch S6: rejected once, then pushed with one parent and only D0 in its diff).
     - After 20 failures, calibrate exits 10 with nothing written.
- **After a successful push**, calibrate writes D0 to $D (a temporary file, then `os.replace`), prints D's sha and exits with D0's result (0, 12 or 13).
- **Interruptions.**
  - A calibrate that crashes, is killed or hits STOP before step 4 succeeds leaves no value anywhere. Its CPU stays charged.
  - Rerunning calibrate under the same F is a clean restart. D0 records no time and not O, so its bytes repeat; only CPU is lost, at most about 1.1 CPU h for A1 and 1.6 for B (cost5's worst calibration).
  - If the push landed but D0 was not written, the rerun refuses and prints D's sha; restore D0 as above.
- **The lane and git.** On the lane's next `git pull --rebase`, D arrives, and git overwrites an untracked D0-<C>.json without notice (scratch S8). That is harmless, because the file then holds D's bytes. The gate never relies on git to protect D0.
- **Why not the alternatives.**
  - *Encrypt D0, or withhold it until a manual push.* The key or the file must be on this machine for the push to happen later. It is then as readable as plain D0, and a manual push is v7's hole.
  - *Count every calibrate start recorded on origin.* calibrate is controls.py's first real-simulator run, so a crash there is the likeliest event, and its fix needs F′. Every such honest restart would then be labelled exploratory although no value was seen. Pushing D first makes "seen" equal "on origin/v2" exactly.
  - *Costs of the choice.* controls.py gets a push path (git credentials and the network), and a crash before the push loses calibrate's CPU.
  - *Residual.* A crash's traceback can show where calibrate stopped (for example a rung index), but not a value. The F′ review sees the crash.

**test and score also need** (`--d0-commit D`):
- the D-candidates under F are exactly {D};
- D has one parent, `git -C $R show --name-only --format= D` prints exactly `$P/D0-<C>.json`, and D's first line names F;
- the sha256 of `$D/D0-<C>.json` on disk equals that of `git -C $R cat-file blob D:$P/D0-<C>.json`.
  - This is a byte compare. `git diff --quiet D -- <file>` cannot be used, because D0 stays untracked locally until D is pulled, and it then exits 1 even on identical bytes (scratch S10).
- D0's recorded F, its criteria.md and controls.py sha256, its numpy and scipy versions and its uv.lock sha256 equal the current ones.

**Records.**
- D0 records:
  - F;
  - the chain with each freeze's rows, and every other `fsd-controls` row;
  - H1 and H2;
  - the status, with every X and its descent flag;
  - the sha256 of criteria.md, controls.py and `$WT/uv.lock`;
  - $WT's HEAD;
  - the numpy and scipy versions.
- D0 records no time and not O. It cannot record D, whose sha depends on D0's bytes.
- The score files record the same, plus O, D and the sha256 of `runs-<C>.jsonl`.

**Deviations.**
- After F, any change to a frozen file, including a controls.py bug fix, needs a new version of this file, its review and a new freeze F′ (Sequence 5–6).
- Before F′ is pushed:
  - score every control whose test finished under F, because F′ makes F unusable (C4);
  - in a commit before F′, `git rm` any tracked `$P/D0-*.json` and `$P/score-*.*`;
  - move the untracked `D0-*`, `runs-*` and `score-*` files to `$R/.cache/fsd-controls-archive/<F short sha>/`.

  `cpu-total.txt`, `cpu-ledger.csv`, the lock files and `STOP` stay where they are.
- `runs-<C>.jsonl` starts with a header line {control, F, D}. test resumes only a file whose header matches, and refuses (10) otherwise.
- A control without test data from an earlier freeze restarts from calibrate under F′ and is preregistered.
- A control with test data keeps its earlier result as the preregistered one (or INCOMPLETE). Everything controls.py writes for it under F′ carries `"status": "exploratory deviation"`, and none of it is ever reported as the preregistered result.

## 6. CPU: ledger, ceiling, stop (redteam-s17d 8; s18 non-blocking 3)

**Estimate** (`cost5-out.txt`; medians of per-run CPU, so an estimate, not a bound): expected A1 2.57 + B 3.32 = 5.89 CPU h; worst 2.81 + 3.78 = 6.58 CPU h. **F₀'s ceiling is 7.6 CPU h** (worst + 15%). The stop rule below enforces it, with an overshoot of at most one step plus 30 CPU s per process.

**Ceiling.**
- There is no CLI flag. The ceiling in force is the largest c over rows ever added (§5) whose `item` is either:
  - `fsd-controls freeze <sha> ceiling <c> CPU h`, or
  - `fsd-controls ceiling <c> CPU h`.
- Each phase reads it after its fetch. It is cumulative over every freeze.

**Ledger.**
- `cpu-total.txt` holds one decimal number of CPU seconds. It is created as `0.0` once, before F₀ (§5 Sequence 4); it is carried across freezes and never reset or deleted. If it is missing or unparseable, the process refuses (10).
- Update:
  - take an exclusive `fcntl.flock` on `cpu-total.lock`;
  - read the total;
  - add this process's `getrusage(RUSAGE_SELF)` plus `RUSAGE_CHILDREN` (user + sys) since its last update;
  - write `cpu-total.txt.tmp` and `os.replace` it;
  - release the lock;
  - then append an audit line to `cpu-ledger.csv` (phase, control, unit, delta, total, pid, UTC time), which is never re-read.
- When updates happen:
  - test and score: after each run, at least every 30 CPU s inside non-simulation loops, and at exit;
  - calibrate: only every 30 CPU s and at exit, with `unit` a step counter. Per-run CPU would show which runs truncated before D is pushed.

**Stop.**
- Before each step, read the total. At or above the ceiling, write `STOP` (holding the total and the time).
- Every process checks `STOP` before each step. On STOP it updates the ledger and exits 11.
- A calibrate stopped this way writes nothing (§5).
- score is charged but exempt from STOP (about 0.003 CPU h per control, `cost5.py`). On a control whose test has not finished, it exits 11 (INCOMPLETE).
- **Resume.** The coordinator pushes a row `fsd-controls ceiling <c> CPU h`, with c above the current total, and then removes `STOP`; no process removes it.
  - A ceiling row never contains `freeze`, so it cannot affect the chain.
  - A resumed test redraws nothing already stored: runs are keyed, the streams are fixed and the header matches.
  - A resumed calibrate starts over.

## 7. Implementation contract (`controls.py`, `ctl_gate.py`)

**CLI.** `controls.py --worktree $WT --freeze-commit F [--d0-commit D] --control {A1,B} --phase {calibrate,test,score}`. Seeds, R, ids, rungs, thresholds, URL and the ceiling rule are constants or rules from this file, not flags.

**Runtime (E1).**
- `python -I` ignores every `PYTHON*` variable. So the first statements of controls.py, before any import from $D or $WT:
  - set `sys.dont_write_bytecode = True`;
  - set `sys.pycache_prefix = os.environ["PYTHONPYCACHEPREFIX"]`.
- Python then reads and writes bytecode only under that prefix, never in a `__pycache__/` beside a source file.
- check_runtime refuses (10) unless:
  - `sys.flags.isolated == 1`;
  - the prefix is set, exists and lies outside $R;
  - `OPENBLAS_NUM_THREADS`, `OMP_NUM_THREADS` and `MKL_NUM_THREADS` are `1`;
  - every imported `sciagent` or `environments` module's `__file__` lies under $WT/src.

**Outputs.**
- JSON is canonical: `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)` plus `"\n"`. Floats are Python's shortest round-trip repr, and non-finite values are never written (they become None).
- `runs-<C>.jsonl`:
  - a header line {control, F, D};
  - then one line per (seed, arm ∈ {telling, decision k}, design, side, replicate): outcome, τ, n_events, the 35 values, none_other and nonfinite.
- Resumable (test only): a torn last line is cut, stored keys are skipped, and a mismatched header refuses.
- `score-<C>.txt` is a fixed-order rendering of `score-<C>.json`.

**Determinism.**
- Before F: a real-simulator unit on the probe-only id `ctl-det` is run twice, and the outputs are compared with `cmp`.
- After test: score reruns seed 101's (A1) or 301's (B) telling runs on the truth's truncating design (A1: e1; B: e2, or e3 on the size rung), so that the truncation path is exercised. It compares them byte for byte with the stored lines. A mismatch makes the control VOID (15, "nondeterministic").
- The stub tests and the pre-F `ctl-det` run call internal functions directly, without the gate.
  - The functions that draw runs refuse the ids "ctlA1" and "ctlB", and every id derived from them, unless they are passed the token that only a passed gate constructs.
  - This guards against accidental draws of the real streams in tests and debugging. It is not a security boundary (§5).
  - No CLI flag or environment variable bypasses the gate.

**Parity.**
- `$D/parity/` holds copies, with source sha256s in a manifest, of:
  - the `fsd-trunc/trunc-s1-d0.jsonl` lines and `.truths.jsonl` records for dev-002, dev-005, dev-006 and dev-015;
  - the `fsd-rule/candidates.json` picks for them.
- `controls.py`'s K0–K4 must reproduce those picks with 0 mismatches.
- Exclusion removes a design from the argmax (§0). candidates.py zeroed it instead, and parity is 72/72 either way (E7), so parity does not test this point; a stub test does.

**Stub tests** (`test_controls.py`), on stub tables and stub run lists:
- **Rules.**
  - K0–K5 and Fa, Fc, Fz, Fx exactly as §0, including:
    - K5's X shared, and truthFSD keeping its own truncated runs outside X;
    - Fx skipping a design on 1 truncation or stop in 16;
    - Fz zeroing only where a side truncates;
    - K3a against K4 on an all-stopped design.
  - Removal, not zeroing: a table whose remaining cells are all 0, where zeroing would pick the excluded design by menu order.
  - Exclude-all for every arm, telling included; cut0 and non-finite values.
- **Readings and codes.**
  - The rung reading, including the separation shortfall.
  - P1 breaches.
  - Every result code, with score's 15 taking precedence over 14, and the evidence-only score files on 14 and 15.
- **Gate and D, in a scratch git repo with a local bare origin.**
  - Each gate refusal in §5: the fetch failure, C1–C9, the D checks, and H1 and H2 with the gh reader injected.
  - The status in s18's S1–S3 and in the S4–S5 restarts.
  - The D build and push against a concurrent pusher.
  - Nothing in a file, stdout or stderr after a calibrate killed before its push.
  - The runs-header refusal, and the ceiling read from rows.
- **Ledger.** No lost update with two processes; loops charged; a missing total refused (10); STOP firing; calibrate's coarse updates.
- **Other.**
  - Canonical JSON bytes.
  - The internal functions refusing the real ids without the token.
  - X never reaching `FSDPolicy`, the tool layer or any record an agent can read (invariant 2).

**Run command.** Runs go in `tmux -L sciagent`, with at most 2 processes, one per control. The process needs git push credentials for calibrate, and `gh` for H1 and H2:

```sh
R=/home/ubuntu/lanes/sciagent; D=$R/.cache/drafts/fsd-controls; WT=$R/.claude/worktrees/fsd-dist
env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPYCACHEPREFIX=$(mktemp -d) \
  nice -n 10 uv run --project $WT --no-sync python -I $D/controls.py --worktree $WT --freeze-commit <F> \
  [--d0-commit <D>] --control {A1,B} --phase {calibrate,test,score}
```

## 8. Reading the outcome (fixed now; redteam-s17e N1)

**A1** (on the tested rung).
- If K0 fails, K1 and K2z fail (i), and the exclusion rules (K3a, K3h, K4, K5) pass, then the instrument resolves the planted label under the exclusion position at real R. Per §3, this is not evidence for that position.
- If an exclusion rule fails A1 (any of (i)–(iii)), no candidate is ready. He² resolution comes first (the (He², D) ordering from the ties draft), and the thresholds are not changed.
- If A1 is not tested (13: an R = 16 shortfall on every buildable rung), control 5 cannot be read at R = 16 on this truth. That ranks no rule.

**B** (on the tested rung, for each pair of FSD rule and telling rule).
- If Fx passes B1–B3 while Fa, Fc and Fz fail B1, FSD needs its own exclusion, whatever the telling rule. B1 holds by construction (§3), Fa's B1 failure follows almost directly from the atom row, and B3 is gated by P3-B. **The evidence is the per-rung h_F.**
- If (Fa or Fc) with K1 fails B2, K1 needs design-aware matching before it can be adopted.
- A θ₋-rung cost of Fx's exclusion (h ≥ 0.95 > h_F) is reported beside any B result. It means Fx loses telling's label when the artefact hides the truth's top design.
- A separation shortfall (§0) is reported and ranks no rule.

**Throughout.**
- VOID (12, 14, 15) and INCOMPLETE (11) favour nothing.
- Unpredicted patterns are reported, and no rule is chosen on them.
- No control here favours option 2 over option 1 (§3).
- Results produced under a later freeze for a control that already has test data are exploratory (§5).

## Not proved or not covered

- He²(mean_rate) = 1.0 on truncated rows is measured, and proved only for the exact He².
- The K2z and Fz labels at R = 32, and the size rung beyond R = 8, are unprobed.
- The calibration powers are plug-in estimates (§0).
- The breach price comes from a reduced model, not the simulator. Its late-τ tail may be light (§0), and stops and cut0 are unpriced.
- Neither control uses a real Wiener–Hopf reference, a fitted B-lib member or a B-sym top fit.
- Only one cap, horizon and environment are covered.
- **The gate.**
  - It is not a security boundary (§5).
  - H2 relies on GitHub's activity log, whose retention is not documented. It held every v2 activity back to 2026-10-02 on 2026-10-29.
  - H1 is the owner's to switch on, and has not been.
  - The v8 gate rules were checked only in scratch repos with local bare origins (`v8-checks/`). The hash-in-message, status, $WT, runtime, H1 and H2 checks were not transcribed there. Nothing was tested against GitHub, and the protection command above has not been run.

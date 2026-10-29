# Red team of fsd-controls/criteria.md v4 (sha256 42828142…), session 17d, 2026-10-29
Verdict: READY WITH FIXES. Do not freeze v4 as written. It has 4 BLOCKING items, and each is a text or procedure change that needs no new runs. v5 needs a re-check of those 4 items only. The spec is not yet complete enough for an engineer (gaps below).

Reproduced (nice 10, about 14 CPU s, outputs in /tmp/rt17d.EYnk): I reran power, power2, power3, randpicks, a2band, cost, cost2, cost3 and cost4, and `cmp` reports IDENTICAL for all 9. bench gives 10.9/7.1 ms (stored 10.7/7.1). random-picks.txt sha256 is 33f80c78…, and the seed lists at :59 and :116 match its "hits on" lines. fsd-dist HEAD is bff61ecba3da…, `status --porcelain` is empty, and the venv imports sciagent and environments from $WT/src. No draft script uses a test id or a test seed: grep for ctlA1/ctlA2/ctlB and range(101|201|301) finds none.
Status of redteam-s17c's BLOCKING items:
- B1 partly resolved (holes in 1 and 2).
- B2 resolved: an O(1) running total, whole-process getrusage, and Δ_pred is now measured.
- B3 resolved for B but not carried to A1 (3).
- B4 resolved by the red team's own option 1; what remains is in 6.
- B5 resolved: `grep ctlA2-pi` finds only :22 and :71, one id, and the first point (0.465) is stated.

Findings, ranked
1. BLOCKING. The gate compares commits, not the files that actually run.
   - `git diff --quiet F HEAD` (:45) ignores the working tree. So an uncommitted edit to power2.py (its pass_ii/pass_iii decide P3) or to probe.py passes. "sha256 … at HEAD" (:44) could mean the committed blob or the file on disk.
   - $D is gitignored, so a new untracked module there is invisible to the gate. Once controls.py puts $D on sys.path, such a module would shadow an import.
   - Scratch repo (/tmp/rt17d-git.*): after editing the committed power2.py and adding fractions.py, `git diff --quiet $F HEAD -- power2.py` → exit 0, but `git diff --quiet $F -- power2.py` → exit 1. `status --porcelain` shows only ` M power2.py`; only `--ignored` shows `!! fractions.py`.
   - Fix: hash the files on disk; use `git diff --quiet F -- <frozen>`; require `git status --porcelain --ignored -- $D` to list only the allowed outputs; apply the same `--ignored` check to $WT/src (empty today).
2. BLOCKING. F is whatever the operator types, so a re-freeze after the data are seen passes every check.
   - The path: edit, commit F2, rerun calibrate, commit D2, then test and score. Every check at :44-47 passes, because each is relative to the F supplied.
   - v4 also has no rule for a controls.py bug found after F, which makes this path likely rather than adversarial.
   - F and D would land on the unattended lane's branch: `git -C $R status --branch` gives `## v2...origin/v2`.
   - Fix: F must be the only commit that ever touched controls.py (`git log --format=%H -- $D/controls.py` = F), and `git log F..HEAD -- <frozen>` must be empty. Push F and record its sha in the LEDGER row before calibrate. State a deviation rule: a fix after F goes through v5 and a red team, and once a control has test data its result is reported as an exploratory deviation.
3. BLOCKING. A1 repeats the B3 error that v4 fixed for B.
   - P3 gates A1 on h, pass_ii and pass_iii ≥ 0.95. These are (ii)'s and (iii)'s own success probabilities under the exclusion rules.
   - Yet a P3 failure moves A1 to the next rung, and all rungs failing is "VOID … not a verdict on any rule" (:33). An A1 VOID would hide exactly what §3 lists as measured (truthFSD agreeing at R = 16).
   - Fix, as for B: write every rung's P3 values to D0-A1.json. Read a failure as a measured R = 16 shortfall, and VOID only on P1 or P2. Move A1's (ii)/(iii) passes to §3's gated list: a pass tests transfer from calibration to the test streams, which is JOURNAL :1520's own description.
4. BLOCKING. The (iii)-only reading (:200) is an escape hatch that applies after the data are seen.
   - The rule's status is left open.
   - It contradicts P3, which certified pass_iii ≥ 0.95 at N = 15.
   - It treats (iii) differently from (ii), which has the same 0.95 floor but still reads "no candidate is ready".
   - Its arithmetic is wrong: `python3 -c` gives 0.9945 × 0.95 × 0.95 = 0.8975, which is the chance that all three criteria pass. A (iii)-only failure at the floor has P ≤ 0.05, one in twenty, not one in ten.
   - Fix: the rule fails A1, there is no rerun or extension, and the report gives D0's pass_iii beside the result.
5. Non-blocking, but must be settled before F. The split-half D*₀ (:27) measures held-out coverage and then gates nothing.
   - (i)'s P ≥ 0.9945 is stated as a consequence, not as a precondition.
   - It is not said which p₃₂ (indices 0–255, 0–127 or 128–255) feeds h, pass_ii and pass_iii.
   - Toy check (/tmp/rt17d-scr/subsamp.py, 7 CPU s): an estimate ≥ 0.95 when the true coverage is 0.90 has P 0.400 from a 128-run pool (0.245 from 256). When the true coverage is 0.80 it has P 0.128 (0.022). So "P ≥ 0.95 at the floor" (:32) is power at the plug-in estimate, not a guarantee.
6. Non-blocking. A2 costs 2.85 of the expected 7.99 CPU h and draws no verdict.
   - :203 leaves the K4/K5 choice to the owner after Δ is seen, with no rule. That is a verdict made after the fact.
   - No control shows that Δ can separate the two rules in either direction. There is no power calculation, VOID risk is 0.39 at π̂ = 0.0075, and "large" at :204 is undefined.
   - L_K and Δ_pred are defined only "as v3", and v3's Δ_pred includes the verdict shares that v4 removed (criteria-v3.md:59).
   - Fix: say Δ is not an input to the K4/K5 choice, or preset a rule, or defer A2.
7. Non-blocking. B's outcome table is incomplete.
   - Some rungs failing P1 or P2 while the rest fail P3-B fits neither rule at :112 or :114.
   - A size-rung failure of P3-B has no reading.
   - "A θ₋ rung that fails P3-B is a cost of Fx" should require that rung to pass P1 and P2.
   - §6's "FSD needs its own exclusion" rests mostly on items that §3 lists as by construction.
8. Non-blocking. The ceiling.
   - The stop rule makes 11.0 h a real cap, with an overshoot of at most about one step plus 30 CPU s per process.
   - The "worst" row uses median per-run costs, so it is an estimate, not a bound.
   - Unspecified: what happens if cpu-total.txt is missing (a silent reset to 0 voids the cap), and whether score runs after STOP.
   - cost4 omits the split-half subsamples, about 0.04 h. That is immaterial.
9. Non-blocking. The JOURNAL claims more than the evidence. `grep -n` shows :1518 still says "independently of R", and :1525-1527 still recommends option 2 "provided the controls pass", while v4 §3 says no control can favour option 2 over option 1.
10. Non-blocking. Determinism and environment.
    - Byte-identity is tested only on a stub. Add a rerun of one real test seed and compare with `cmp`.
    - `--no-sync` leaves the venv unchecked (numpy 2.5.1, scipy 1.18.0 now). Record the versions and the uv.lock hash in D0.
    - probe.py:84-86 records a ViewError as stop "cut0", but v4 counts it as truncated only, so reusing probe.run would breach P1's "0 stops".
    - hellinger2 raises on a non-finite value (battery.py:359-360), and v4 does not say how that is handled.

Can an engineer implement controls.py and its stub tests from this? No. These decisions are still open:
- which p₃₂ feeds h, pass_ii and pass_iii, and whether held-out coverage is gated (5);
- how D is identified (there is no `--d0-commit` flag, :168-171) and whether "committed alone" is checked;
- blob or disk hashing (1);
- cpu-total.txt's starting value, a missing file, resets, score after STOP, and resuming after STOP (8);
- B's mixed failures and the size-rung reading (7), and A1's P3 reading (3);
- cut0 and non-finite values (10);
- the subsample count and pool for P3's 99% clause;
- what Δ_pred contains now that the verdict is gone;
- a canonical JSON and float format for byte-identical outputs;
- a ban on imports from outside $D and $WT (the parity tables live in the unfrozen fsd-rule/candidates.py).

The K-rules, the harness and L_K are defined only "as v1" or "as v3", so the engineer has to read three versions. Nobody checks controls.py against this file before F, so add a code red team before F.

Sample size: A1's N = 15 holds (worst (iii) power at h = 0.95 is 0.985). B's N = 15 holds (1.000 for the `[sign]` labels, 0.997 for `[size]`). A2's N = 24 rests only on seed balance (VOID risk 0.002–0.023 inside the band). All of these are plug-in estimates (5).

Not checked: controls.py does not exist, so I ran no byte rerun and no gate test. I ran no control and no probe, so the probes' He² values are not recomputed. I took the controls_p4.py citations from s17c. The toy in 5 is not He² on this menu. The K2z/Fz labels at R = 32 and the size rung beyond R = 8 remain unprobed.

Files:
- /home/ubuntu/lanes/sciagent/.cache/drafts/fsd-controls/criteria.md (also probe.py, power2.py, cost4.py)
- /home/ubuntu/lanes/sciagent/docs/v2/JOURNAL.md
- /home/ubuntu/lanes/sciagent/.claude/worktrees/fsd-dist/src/sciagent/scoring/battery.py
- Scratch: /tmp/rt17d.EYnk, /tmp/rt17d-scr/subsamp.py, /tmp/rt17d-git.*

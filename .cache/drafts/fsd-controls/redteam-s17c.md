# Red team of fsd-controls/criteria.md v3 (sha256 0123093f…), session 17c, 2026-10-29
Verdict: NOT READY to freeze. There are 5 BLOCKING items. All can be fixed in the text or the procedure, with no new runs. The arithmetic and the code citations hold.
Reproduced (nice 10, about 10 CPU s): reruns of power, power2, power3, randpicks, a2band, cost, cost2 and cost3 are byte-identical to the stored outputs (`cmp`: all IDENTICAL). random-picks.txt sha256 is 33f80c78…, and the seed picks at :44 and :86 match its "hits on" lines. fsd-dist is at bff61ec = origin/p4-deltas, clean.
Cited code checks out: policies.py:665-666 (no design index), :968-972 (random), controls_p4.py:296-307 (one-sided exact McNemar, α 0.05) and :360-362 (unpaired units refused), telling.py:88-95. truthFSD uses a fixed comparator, so its 8 decisions are iid given the seed and h(ℓ) = 1−(1−p₁₆)^8 is right.

BLOCKING
1. Nothing stops the analysis being tuned after the fact: the analysis code is not pinned. `ls $D/controls.py` gives "No such file", and controls.py is not in the freeze commit F. The gate (:33) runs only in the test phase and checks only criteria.md and D0.
   (a) Calibrate is not gated. D0 can come from an edited power2.py (its pass_ii/pass_iii are what P3 evaluates) or an edited controls.py and still satisfy "F strict ancestor of D".
   (b) Score is not gated (:136). The scorer can change after the test data are seen.
   (c) The operator types the frozen hash, and F itself is never passed to the gate.
   (d) Nothing checks that the worktree is at bff61ec and clean.
   (e) The §5 stub tests are due only "before any test phase" (:11). But A2's calibrate phase already runs K4/K5 inside Δ_pred.
   Fix: commit controls.py and its passing stub tests before any calibrate phase. Gate all three phases on `git diff --quiet F HEAD -- <frozen files + controls.py>`, with F passed explicitly and the hash read from F. Check the worktree HEAD and a clean tree. Record F, the controls.py hash and the worktree HEAD in D0 and in the score output.
2. The 11.0 CPU h ceiling is not an upper bound. The ledger sums only per-run cpu_s, and the stop rule re-reads the whole file under an exclusive lock before every run (:127).
   - Run counts, using cost3's structure: 164,608 expected and 207,360 worst.
   - Microbenchmark (/tmp scratch): one re-read of 100k ledger lines takes 88.2 ms CPU (0.88 µs per line). The re-reads total about n²/2 lines, i.e. 3.32 to 5.27 CPU h that never enter the ledger.
   - Late in the run the lock is held about 100 ms per 0.2 s run, which serialises A1 and B.
   - He² subsampling, scoring and the Δ_pred simulation are also uncounted (Δ_pred is a guessed 0.1 h, `SIM_DELTA` in cost3.py).
   Fix: keep a running total, reading only appended bytes or a separate total file. Log whole-process CPU (getrusage), not per-run time.
3. One exclusion depends on the outcome (control B). P3-B's h_F is B3's own per-unit success probability.
   - Failing it on the three θ₋ rungs is a measured miss: Fx loses telling's label when the artefact sits on the truth's top design. Yet :84 and :170 call it a VOID that "says nothing about Fx". The ladder then moves to a truth where Fx can pass.
   - §3 (:110) still lists B3 as "Measured".
   Fix: put every rung's P3-B in D0. Read a θ₋-rung failure as a cost of Fx. Limit a size-rung pass to truths whose best design is not the artefact design. Move B3 to §3's construction/gated list.
4. A2's primary analysis does not answer K4 against K5. §3 (:109) says A2 is "not a two-sided test", but :65-67 and §6 (:164) still turn Δ into "favours K5 / favours K4 / plumbing decides".
   - K5 hands truthFSD telling's own X, so higher agreement with telling is built in (control 5 is nearly circular, redteam-s16 #4).
   - The sign is predicted in advance (:60).
   - P2-A2 lets the test run only when the simulation already says it is decisive in at least 80% of simulated tests (:58).
   - No planted regime could produce "favours K4", so that direction has no §6.4 positive control.
   Fix: drop the verdict wording and report Δ as K4's cost in control-5 agreement in this regime, against Δ_pred. Or add a regime where K5's kept truncated runs should lose (telling's e1 label is not mean_rate).
5. One seed is left open. `grep -n ctlA2-pi criteria.md` shows :20 `f"ctlA2-pi-{v:.5f}"` (v = 0.5 + θ_all) and :56 `ctlA2-pi-<θ_all:.5f>`. These are two different streams, so they can give two different θ_all. The first bisection point is also unstated.

Non-blocking
6. A2's mechanism is unprobed for the v3 family. probe4.py:51 (`keep = [e ... if e != 1]`) leaves e1 out of the table, so nothing shows that "e1 dominates its row" (:60). Meanwhile the off-e1 cells are strong (0.882, probe4-out.txt). N1's "probably VOID" has been moved into P2-A2, not retired; it costs about 1 CPU h of calibration to find out.
7. A2's VOID risk is quoted only inside the bisection band (0.002–0.023). The full calibration accepts π̂ in [0.0075, 0.040], where P(fewer than 5 seeds on a side) is 0.393 at π = 0.0075, 0.169 at 0.01 and 0.181 at 0.04 (a2band's `void()`, run in my scratch script).
8. Two power figures are in-sample, so they are optimistic.
   - P2-A2's 80%: each simulated test draws 24 × 32 = 768 telling runs per design from 256, reusing the same ~5 truncated e1 runs every time.
   - D*₀ is picked on the same subsamples it is scored on, so the in-sample Σp₃₂ ≥ 0.95 overstates coverage.
9. N4 is met as worded (0.95 each), but the joint floor is 0.9945 × 0.95 × 0.95 = 0.8975 (union bound 0.8945). So :161's "no candidate is ready" can fire on a roughly 10% chance per rule. A failure of (iii) alone has no assigned reading.
10. N6 is covered for Fx, but there is no stub for telling's exclude-all case: `Telling.from_table` returns (0, 0) on an all-zero table (redteam-s16 #2).
11. `grep -n "independently of R" docs/v2/JOURNAL.md` gives :1518. The owner is still deciding on the wording that v3 (:9) says is wrong.
12. State that A2's test-data VOIDs (seed balance, P1 breaches) are computed and written before Δ is computed or printed.

Earlier points
- redteam-s17: 1 partly (see 4); 2 fixed here but not in the JOURNAL (11); 3 and 4 fixed and reproduced; 5 fixed but see 3; 6 fixed except 5; 7 partly (1); 8 fixed except the (iii)-only reading (9); 9 listed, not written, and due too late (1e); 10 fixed except B3 (3); 11 partly (2); 12 fixed.
- redteam-s17b: N1 moved, not retired (6); N2 fixed but see 4; N3 fixed but see 3; N4 met as worded (9); N5 partly (labels now come from diagnostic_menu through probe.py, verified; the gate has the holes in 1); N6 partly (10); N7 partly (2); N8 fixed.

Sample size
- A1's N = 15 is not a lucky pick. Worst (iii) power at h = 0.95 for N = 12…18 is .913, .894, .954, .985, .996, .999, 1.000 (pass_iii, scratch script).
- B's N = 15 holds: 1.000 for the [sign] labels and 0.997 for [size].
- A2's N = 24 is justified only by seed balance. Δ's power rests on the in-sample simulation (8).

Not checked
- controls.py does not exist, so I could not rerun a unit and compare bytes for determinism.
- The ledger figure is a csv-parsing microbenchmark, not a measurement of real code.
- e1's He² in the v3 A2 family.
- The K2z and Fz labels at R = 32, and the size rung beyond R = 8.
- hellinger2's handling of all-None sides (no -inf path seen, but not traced).
- No control was run.

Files
- /home/ubuntu/lanes/sciagent/.cache/drafts/fsd-controls/criteria.md (also probe4.py, power2.py, cost3.py, a2band.py)
- /home/ubuntu/lanes/sciagent/docs/v2/JOURNAL.md
- /home/ubuntu/lanes/sciagent/.claude/worktrees/fsd-dist/src/sciagent/scoring/controls_p4.py
- /home/ubuntu/lanes/sciagent/.claude/worktrees/fsd-dist/src/sciagent/scoring/telling.py
- /home/ubuntu/lanes/sciagent/.claude/worktrees/fsd-dist/src/sciagent/systems/v2/policies.py
- Scratch (not in the repo): /tmp/rt17c-scr/chk.py, /tmp/rt17c-scr/ledger.py and the rerun outputs in /tmp/rt17c.SwaC

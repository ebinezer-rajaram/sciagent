# Red team of fsd-controls/criteria.md (sha256 21a01721…), session 17, 2026-10-29. Verdict: NOT READY to freeze.
(Saved by the lane from the red-team agent's reply; the agent was read-only.)
A1 and B match the code, and the probes reproduce. But A2 can't fail K5, K5 isn't R-independent, the power
figures are too high, B's planted artefact sits on T_B's own top design, and the stream ids are not pinned.
Reproduced: probe2 rerun (nice 10, 34 CPU s) is identical to probe2-out.txt once CPU timings are removed (both
sha256 423df3c4…). power.py and cost.py reruns are byte-identical. fsd-dist = bff61ec = origin/p4-deltas tip, clean.

Per question: Q1 FAILS. Q2 HOLDS WITH CAVEAT for A1 and B, FAILS for A2. Q3 FAILS.
Q4 FAILS as stated. Q5 HOLDS WITH CAVEAT. Q6 HOLDS WITH CAVEAT.

Ranked problems
1. (Q3) A2 can't fail K5. A flip needs both arms' excluded sets to differ (:73). K5's sets are equal by
   construction, so "0 flips, proved" (:75) is true by definition and tells us nothing. §4 (:159-160) has
   no outcome that favours K4. This is not a positive control. The mismatch K5 claims to remove is still
   there, on designs K5 keeps: when telling's 32 runs show no cap on e1, truthFSD's own 16 still cap with
   P = 1-(1-pi)^16, and under (c) those runs are kept. That is 0.108 per decision at pi=0.05 and 0.146 at
   0.03, so about 4-6 of A2's 40 decisions (python3 arithmetic). A2 does not count them. Telling excludes
   once per seed, so A2 has only 5 independent draws: at pi=0.05, P(no seed keeps e1) = 0.34.
2. (Q3) K5 is shared but not R-independent. X = "any of telling's 32" (:13), so P(exclude) = 1-(1-pi)^32.
   Telling's R decides X, and the 1-of-R cliff that was the objection to option 3 is still there. The
   statement "neither arm's R decides" (:13) is false for telling.
   Telling uses its own 32 replicates under K4 and under K5, so its d* is identical under both. The two
   rules differ only in truthFSD, i.e. in control 5, and in no reported C5 rate. The draft should say so.
3. (Q4) The random arm's picks are already fixed. Its draw uses policy_seed(seed, k, RANDOM_INDEX, 0) with no
   truth id (policies.py:971), so its picks for the frozen seeds can be computed now. Computed (python -I
   on fsd-dist):
   A1 seeds hit rate_after_mark_slope[sign] on 106,107,108,110 and mark_gap_correlation[sign] on
   101,102,104,105 (4/10 either way). B seeds: 4/10 on rate_after_mark_slope[sign], 3/10 on mean_rate.
   Given those fixed hits, P((iii) passes) is 0.959 at h=0.99, 0.788 at 0.95 and 0.581 at 0.90, not 0.985.
   The fixed hits work against passing, so there is no sign the seeds were cherry-picked. Keep the seeds:
   disclose the picks, recompute power given them, and raise N if the power is too low.
4. (Q4) No precondition bounds h. P3 bounds a, and a=0.8 allows h=0.80 (P=0.705, power-out.txt:7).
   (ii) "P=0.989" treats the 80 pairs as iid, but they cluster on one telling d* per seed. Exact
   clustered values: 0.942 (p32 .95/.05, p16 .84/.12) and 0.879 worst case (p32 .8/.2, p16 1/0).
   p16 comes from scorer streams that are independent per design. truthFSD's policy_seed has no design
   index (policies.py:665-666), so its replicates share random numbers across designs.
5. (Q2) B's poisoned design is T_B's top design. T_B's top uncapped cell is inject_sign_high
   mark_gap_correlation[sign], He2 0.918 (probe2-out.txt:20). That is e1, the design Ref_B poisons and
   Fx must skip. Fx's next cell is clamp_release rate_after_mark_slope[sign] (0.846).
   p_F (:99) measures whether Fx's pick is in D*0(B), not whether it matches telling's d*. So the
   predicted Fx PASS (:119) may come out VOID or unpredicted. Precondition on the match probability
   h_F instead, and compute power for B3.
6. (Q1) The stream ids are never fixed. Telling and calibration streams use scorer_seed(TELLING, id, ...)
   (:25, :28), and `id` (per control and per ladder rung) is chosen after the freeze. That leaves a seed choice open.
7. (Q1) The gate changes the frozen file. The D0 sha256 is "appended here" (:40, :144-145), which changes
   the frozen sha256. The check that refuses lives in controls.py, which has not been written.
   Fix: freeze by committing criteria.md, then commit D0-*.json separately before the test phase.
8. (Q1) Unassigned outcomes:
   - A2 has no VOID if no rung gives pi-hat in [0.02, 0.08], and no stated preconditions (P1 can't hold).
   - Which rung of T_A Ref_B uses if A1 moves down its ladder (:48, :90).
   - A clip_to_analysis ViewError ("cut0", probe.py:84-86).
   - Test-stream P1 breaches other than the K0 one (:69).
9. (Q1/Q6) K5, Fx and Fz are not in candidates.py (KEYS: K0, K1, K2a, K2z, K3h, K3a, K4;
   candidates.py:202). So ":21 ported, 0 mismatches" covers none of the new rules. Stub-table tests are
   needed before the test phase.
10. (Q2) Some separations hold by construction, not by measurement. A1's K1/K2z FAILs and B's B1/B2
   outcomes follow from D*0 excluding E_cal, P2 banning mean_rate, Fx's own exclusion and label-only
   matching. They restate the §4.2 position; they are not evidence for it.
   Nothing separates K3a/K3h/K4/K5 except A2 and the stop proof. The stop proof is correct
   (battery.py:361-374 gives 1.0 for p=1, q=0; from_table is d-major with strict >, telling.py:88-95).
   The rest of A1's table checks out against choose (policies.py:694-702), truthFSD
   (policy_controls.py:61-69) and the shared B-lib member (execute.py:440-443).
11. (Q5) The ceiling holds but leaves out two items. Ladder recalibration is not costed (+0.16 h per rung;
   A1 and B to rung 3 gives 3.90 h). Truncated runs at theta+ = 0.6 take 0.97 s (A1 test 1.12 h, not 1.01).
   Both still fit under 4.5 h. There is no stop rule for reaching the ceiling.
12. (Q6) No invariant is broken. No src edit; streams are seeded; X goes only to truthFSD, on the framework
   side. If K5 is adopted, add a code-enforced test that X never reaches FSD or the tool layer
   (invariant 2), and use the control outputs as the regression fixture for the src version.

Owner question: as framed, A1 and A2 pass option 2 almost by construction. Don't present them as evidence
for option 2.
Not checked: dev-002's structure (:45; no split file found); K2z/Fz labels at R=32; T_B's saturated
labels on e2 (probe2 lists only T_A's); whether A2's ladder reaches pi in [0.02, 0.08]; controls.py
(it doesn't exist). Commands run: probe2 rerun with diff; power.py and cost.py reruns; inline
python -I for the random picks, clustered power and K5 mismatch; git merge-base/branch checks on bff61ec.

Fixes listed by the red team:
1. Replace A2's flip count with label disagreement per rule on the same streams; preset a threshold on the
   K4-K5 difference; count K5's capped runs on designs it keeps.
2. Restate K5 as "shared, depends on telling's R", or fix X on a set number of framework-side calibration runs.
3. Name the scorer truth ids for each control and each ladder rung.
4. Put the random arm's fixed picks in the criteria, recompute (ii) and (iii) power for clustering and for
   those picks, add a precondition on h, raise N if power is short. Keep the seeds.
5. Make B's precondition the match probability h_F with power for B3, or move Ref_B's artefact off T_B's top design.
6. Freeze by git commit, commit D0 separately. Add the missing VOID and edge-case rules.
7. Stub-table tests for K5, Fx and Fz. A stop rule for the cost ceiling.

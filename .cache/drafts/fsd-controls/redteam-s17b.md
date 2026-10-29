# Re-review of fsd-controls/criteria.md v2 (sha256 178e1147…), session 17b, 2026-10-29. Verdict: READY AFTER LISTED FIXES.
(Saved by the lane from the red-team agent's reply; the agent was read-only.)
A1 and B can be frozen once the small text fixes below are in. A2 is now a fair two-sided test, but its
planted truth family will probably make it VOID at calibration, and if it isn't VOID, the sign of Δ can
be predicted in advance. Change the A2 family before freezing.
Reproduced (nice 10, ~40 CPU s): power2, power3, randpicks, a2band, cost2 byte-identical to stored outputs
(cmp); random-picks.txt sha256 33f80c78… as cited; seed picks at :44, :86 match; dev-002 structure confirmed
in data/v2/truths/dev/truths.json.

Points from redteam-s17.md: 1 fixed (new N1, N2); 2 fixed (JOURNAL.md:1517-1518 wording flagged for the
owner); 3 fixed; 4 fixed (see N4); 5 partly (P3-B stops a false fail but B may now be VOID; see N3);
6 fixed; 7 partly (gate does not pin 178e…; N5); 8 fixed (new N6); 9 fixed as hand-off (tests not
written); 10 fixed (§3, :112); 11 fixed (cost2 6.86/9.10 h reproduces; stop rule :127); 12 fixed.
Fixes 1-7: 1 fixed, 2 fixed, 3 fixed, 4 fixed, 5 partly, 6 partly (N5, N6), 7 fixed.

New problems, most serious first
N1. A2 probably VOID at calibration. Probe probe.pair at v = 0.99 (θ₊ 0.28, θ_all 0.71, member hawkes
    0.85), R = 16, probe-only id rt17-A2v099, 34.7 CPU s: off e1 every cell is weak and the top is flat
    (He² 0.438, 0.378, 0.363, 0.339, 0.337, 0.333 over 4 labels; A1 0.990), so a₀ ≥ 0.6 (P2-A2, :58) is
    unlikely. Cause: v = 0.85 + θ₊/2 forces θ₊ to ~0.26-0.30, weakening the sign gating. Fix: keep θ₊ = 0.5,
    bisect θ_all on [0.47, 0.50] (v = θ₊ + θ_all), member θ = θ_all + 0.25, scale θ₀ to keep the
    observational rate.
N2. Δ's sign predictable from calibration. Same probe: e1 dominates (He²(mean_rate) 0.68 with 1/16
    truncated; median 3,582 events vs member 2,014). When telling keeps e1 its d* is on e1; on E5 pairs K5's
    kept truncated runs pull truthFSD to e1 (agree) while K4 drops e1 (disagree): Δ > 0 for this regime, and
    :66-68 generalises it. Fix: compute the predicted Δ from calibration subsamples (32 per-design, 16 CRN)
    into D0-A2.json before testing; limit the reading to this regime.
N3. B likely VOID. The artefact must precede e2, so it sits on e1, but e1 is T_B's top design only because
    T_B is gated on sign=-. Fix: a ladder rung before VOID with a size-gated truth that truncates on
    inject_size_high (artefact stays on e1, truth's best design moves). Otherwise the dev-005 shape is
    uncovered again.
N4. P3 floor too low: at the floor (ii) and (iii) each pass w.p. 0.9, so an exclusion rule fails A1 by
    chance w.p. up to ~1 − 0.9945 × 0.9 × 0.9 ≈ 0.19 and :160 would say "no candidate is ready". Fix: joint
    P(i, ii, iii) ≥ 0.9, or 0.95 each.
N5. power2.py:28-33 reads labels from gitignored .cache/drafts/fsd-trunc/trunc-s1-d0.jsonl.truths.jsonl;
    the freeze commit must include it or derive labels from diagnostic_menu. The gate (:33) should check
    sha256 178e… (the frozen hash) and that D0's commit comes after the freeze commit.
N6. Exclude-all contradiction: :37 "scores a miss" vs :151 Fx raises a typed error; an error drops the unit
    and policy_separation refuses unpaired units (controls_p4.py:360-362). Pick one.
N7. Stop rule needs a shared CPU ledger read by both parallel processes (none specified); worst case 9.10 h
    from per-run medians leaves 4% headroom, so it may fire (A2 INCOMPLETE as written). No LEDGER.csv entry.
N8. Minor: :65 bootstrap interval type, quantile convention, strictness of "above 0" not fixed; :55-57 the
    full calibration's π̂ not required to stay in band; :164 "exclude per run" untested, needs its own control.
Not checked: a₀ at R = 32/16 (one seed at R = 16 on probe streams only); B's h_F (needs CRN calibration);
controls.py (does not exist); bisection π̂ near band edges.

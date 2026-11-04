Reviewed: `docs/v2/prereg/2026-11-04_pilot-search-misses.md` at d117fc0 (round 3, narrow: round 2's blocking findings, sections 2.2, 5, 6 and 8). Saved verbatim, session 28.

## Verdict: READY WITH FIXES. Do not freeze d117fc0 as it stands.

There is one BLOCKING finding, and it needs text edits only: no new measurement and no new design. Four of round 2's five blocking findings are fixed. Finding 1 is only partly fixed, and one sentence added in this revision makes it worse. After the edits, a diff read of the changed lines should be enough.

| Round 2 finding | Status | Evidence |
|---|---|---|
| 1. Label rules | **Partly fixed** | §5 is now sound if read alone, but §9, §6 and §2.7 still contradict it (finding 1 below) |
| 2. Cost basis | Fixed | See the notes under this table |
| 3. Consequences | Fixed | All five labels have a row in the §5 table; two small gaps (findings 2 and 3 below) |
| 4. Pinning R | Fixed | All four asks are at lines 192 to 198: LOG hash at `R`, `F_COMMIT`, hash in `cpu.json` with refusal, and approval, new directory and both record sets. One residual (finding 4 below) |
| 5. T2 slow tests | Fixed | Lines 61 and 188 use root 20261107. QUEUE line 312 now reads "Do not run the frozen seed stream on T2 more than once". Wording point in finding 10 below |

Notes on finding 2:
- **Measurement:** seven fresh-profile cases at four grid sizes, on root 20261199 (`ascent.py`).
- **Cost:** it now uses `min(1.1 × 48 × sweep, cap)` with a cap per link.
- **The cap is hard:** `_ascend` raises `_Budget` before a block that would pass the limit (`fit.py:831`).
- **Reasoning:** the old "most the ceiling carries" rationale for K is gone.

**Your questions**
- **Can the labels still be gamed?** Yes, by choosing between clauses (finding 1). §5 read alone is sound: no stop can bury a flip or produce a survival label.
- **Is every consequence stated?** Yes, with gaps for `GO_CHANGES` with `partial: true`, the resume exception and which label governs after an amendment.
- **Do the sections agree?** §2.2 and §8 agree on K, caps and rates. §5 and §6 do not (findings 1 and 5). §5 and §8 do not (findings 1 and 2). §2.2 and §6 have one gap (finding 7).
- **Does §8's arithmetic hold?** Yes. Every computed figure reproduces; the stale inputs are in finding 8.

## Findings

### 1. BLOCKING. Three clauses outside §5 give a different label from §5, and one is new

| Line | Text | Conflict |
|---|---|---|
| 335 (§9) | "A stop is always `INCOMPLETE`, whatever had been found" | Rule 2 at line 232 says "no stop can bury a flip". Line 238 says a stop after tier 1 "does not alter the label" |
| 256 (§6) | "Any other exception is a bug: it stops the run, and the label is `INCOMPLETE`." | Same conflict. A bug in S2 or tier 2 after a go gives two labels, and so does a bug after a flip |
| 134 (§2.7) | go "with every tier 1 fit processed gives the label `GO_SURVIVES_FOUND_MISSES`" | Rule 4 gives `CONTROL_FAIL` when H1 fails, so this one favours the GO |

```text
sec9.3 now:      A stop is always `INCOMPLETE`, whatever had been found, so stopping cannot...
sec9.3 b6e98db:  A stop is always `INCOMPLETE`, whatever had been found, so stopping cannot...
sec6 now:        Any other exception is a bug: it stops the run, and the label is `INCOMPLETE`.
sec6 b6e98db:    Any other exception is a bug and stops the run.
```

- **§9:** this is the sentence round 2 quoted, unchanged.
- **§6:** the clause was added in this revision, so the fixes did open a new blocking problem, of the same kind.
- **§2.7:** the wording predates this revision, and round 2 did not flag it.
- **The stop list:** §9's "stops early only for these" also leaves out the bug stop.
- **Softer versions:** lines 322 and 327 say the label "is `INCOMPLETE`", which is true only if `v` has not flipped.

Fix:
- Say at the top of §5 that it alone sets the label.
- Line 335: "the label follows section 5".
- Line 256: same wording.
- Line 134: add "and H1 stands".
- Lines 322 and 327: "follows section 5".
- Add the bug stop to §9's list.

### 2. Non-blocking. The resume exception in §5's table can never apply
Line 247 lets "a CPU or wall stop" resume "if the ceiling allows".
- The CPU stop fires at 78, and a resume "refuses at or above 78" (line 277).
- The wall stop fires at 60 hours in total, and a resume gets 60 hours less the time spent, which is 0.
- "Ceiling" in §8 means 84, so the phrase can be read as allowing a resume between 78 and 84.
- The real resume cases, SIGKILL or a reboot, are not named.
- The ban on resuming after `STOP` is in neither the guards nor the test list.

Fix: name the real resume cases, and have the script refuse a resume when `STOP` exists.

### 3. Non-blocking. `GO_CHANGES` with `partial: true` has no row of its own
The table answers "No". But Δ can take either sign, and ORACLE corrections not yet processed can move the gaps, so a partial flip can reverse. This is safe for the GO, but a stop timed on a passing flip could lock in §2.4's consequence.

Fix: say "not on the misses found so far", and state whether §2.4's consequence applies before tier 1 finishes.

### 4. Non-blocking. Nothing says which label governs after an amendment
Line 198 keeps both record sets but does not rank them. A `GO_CHANGES`, then a "bug" amendment, then a `GO_SURVIVES` is left open. One sentence closes this, the last post-data route in the text.

### 5. Non-blocking. §6's bullets conflict on `reference_failed`
- "A job that reaches it is `reference_failed` at once" and "A second loss is `reference_failed`" both sit against "a job that dies in R2... does not count as `reference_failed`".
- Rule 3 makes that difference a label difference.
- It can be read as the specific overriding the general, and the 2.5-hour `cpu_cut` makes it rare.

Fix: add "before R1's result is written" to the first two.

### 6. Non-blocking. Line 290 still says the label "is known at the end of tier 1 and does not wait for the rest"
Lines 238 and 317 call it provisional, and a `BYTE_FAIL` can still come later.

### 7. Non-blocking. Line 72's definition of searched is too narrow
It lists "`capped` with R2 not finished". A fit that is not capped, but is `cpu_cut` or died in R2, is in neither class, although §6 calls it searched.

Fix: "or R2 not finished".

### 8. Non-blocking. Stale inputs in §8 and nearby; none changes a conclusion
- **Line 313:** "about 10,200 points" is 0.782 × 48 × 272 = 10,210, the old m. It is above both caps.
- **Line 288:** d117fc0 changed "16,000" to "8,000 points was not measured", but R2 takes one profile to 14,400 points. Using `d.table`'s slope that is about 1.19 GB, so the 2 GB allowance stands.
- **Line 159:** "near 43 starts" is 8,000 / (0.782 × 240) = 42.6, the old m on T2's sweep.
  - At §2.2's 1.1 it would be 30 starts, with a pass chance of 0.90.
  - T2's own decay supports about 43 or more, so 0.97 is defensible, but the basis should be stated.
- **Line 59:** completed starts assume a constant m. `m.out` reaches 8,000 points on `LARGE` at 20 to 26 starts (formula 26.7), and 3,000 at 6 to 7 (formula 10). By hand interpolation, "fewest 19" is nearer 12 to 16, and "fewest 9" nearer 5 to 6.
- **Line 325:** the worst case of 82 hours leaves out the 6.5 hours of controls and group C, so it is 88.6 before S2.
- **Line 327:** tier 1 ends 24 to 44 hours into the run, not 22 to 40.

### 9. Non-blocking. The bound of 76.1 hours has a 5% margin, and `m.out` holds a rate the draft does not use
- With identity at the top rate, the bound reaches 78 at 0.1296 s per point, against the 0.123 s used.
- `LARGE` softplus scales cleanly with events: 5.16e-5 s per point per event in `m.out`, against 5.30e-5 in `d.table`.
- `MID144000` softplus runs at 1.43e-4, about 2.8 times that. Scaled to 2,327 events it gives 0.33 s, and exp gives 0.147 s.
- At 0.2 s, tier 1 plus controls and group C comes to 60.6 to 89.3 hours.
- Caveats: this is one short log per shape, and applying the largest log's rate to every fit overstates cost.
- §8 already says the rates are not bounds and falls back to `INCOMPLETE`, so this is not blocking. "Protected by the caps" covers m only, not the rate.
- `m.out` has no identity case, and line 302 doesn't say so. Cost barely depends on it: 21 to 24 of 24 identity fits are capped across m of 0.85 to 1.65.

### 10. Non-blocking. Line 61's "both were caught" has no record
- 20261107 appears 0 times in LOG and QUEUE at HEAD.
- The side branch's test names are `test_t2_control_1_with_r2_reaches_the_known_value_by_enumeration` and `test_t2_control_2_is_caught_by_r1_alone`. If those are the tests meant, only one is an R1 catch.
- Round 2 asked that these runs never be read as catches.

Fix: drop the clause, or cite a log.

## Arithmetic reproduced
`cost3.py` output, cross-checked by my own script from `sweeps.json`:

```text
n 140; tier 1: exp 25, softplus 82, identity 24; tier 2: 1, 7, 1
sum sweep tier 1: 3879, 12459, 2880; tier 2: 116, 809, 137
8000 3000 1.1 capped ne 52 of 107 id 22 of 24 pts 696368 71702 tier1 35.4 64.1 +6.5 70.6
bound any m 76.1; all fits 40.4 75.9; S2 10.455 23.83
starts ne q [35, 48, 48] min 19; starts id q [18, 28, 34] min 9
K 43 all 0.408 >=5 0.97; worst 39.6 + 38.5 + 3.9 = 82.07
m at 48 starts: .834 .868 .34 .343 1.088 1.095 .52
t1 G>24000 118, G>3456000 51; strata 8/5/41/9/17/51 = 131
```

Also reproduced:
- `d.table`: 0.1229 s at 2,318 events and 4,043 points; 0.0387 s at 2,269 events and 2,052 points; 729 MB.
- H1 range: 0.452 to 0.942.
- Wall figures: 48.1 hours and 48.6 CPU hours.

## Commands run
- `git rev-parse`, `git status --short`, `git log`
- `git diff --word-diff=porcelain b6e98db d117fc0 -- <prereg>`, and `git diff --word-diff=porcelain 0077c81 d117fc0 -- <prereg>`
- `python3 -I .cache/drafts/prereg-s28/cost3.py`, and `python3 -I /tmp/rt3/chk.py` (my script, outside the repo; reads `sweeps.json` and `m.out` only), plus one inline arithmetic snippet
- `grep`, `sed` and `git show b6e98db:<prereg>` on the prereg, `fit.py`, `tests/glm/test_fit_search.py`, QUEUE and `edit4.py`
- `git grep` on named files in docs, plus `TEST_ROOT` and T2 test names in `1faf074:tests/campaign/test_pilot_search_misses.py`
- `git diff --stat d117fc0 HEAD`, `stat`, `sha256sum`

**Environment:**
- HEAD moved to 0b82245 during this review (LOG, QUEUE and LEDGER only). The prereg is unchanged, with sha256 prefix `006fa2404cb83e99` at d117fc0 and at HEAD.
- `git status --short` shows 0 lines, and nothing in the repository was changed.

**Limits:**
- No search, refit or held-out evaluation ran, and nothing ran on pilot units or the reserved roots.
- The store was not loaded, and `decay.py` was read but not run.
- `review-s27` was not opened: my only grep under `.cache/drafts` was a non-recursive glob of `prereg-s28`'s top-level files.

## Not checked
- **The script and its test.** I saw only the `git grep` lines above, so I don't know which label logic is coded.
- **Whether the test-root run happened.** I can't confirm it or its result.
- **Store-derived inputs.** I took these as given: 55.3 s, 92.6 s, the 179,148 and 147,636 grid sums, 106 of 131, and the identity 0.512 s rate.
- **`sweeps.json` itself.** It was not rebuilt, because that needs the store. The split sha256 and the store fingerprint were not rerun either.
- **Launch and guards.** The `systemd-run` line and all guards are unrun.
- **m and rates beyond what was measured.** Neither is measured on real pilot logs. m is not measured on identity or above 3,456,000 points.
- **Sections outside the narrow scope.** §2.2, §5, §6 and §8 were read in full. Of §2.7, §2.9, §2.10 and §9, I read only the parts that touch labels, stops, caps and the five findings. No positive control has run yet, so none is claimed.

## Files
- `/home/ubuntu/lanes/sciagent/docs/v2/prereg/2026-11-04_pilot-search-misses.md`
- `/home/ubuntu/lanes/sciagent/docs/v2/redteam/2026-11-04_pilot-search-misses-round2.md`
- `/home/ubuntu/lanes/sciagent/.cache/drafts/prereg-s28/m.out`
- `/home/ubuntu/lanes/sciagent/.cache/drafts/prereg-s28/cost3.py`
- `/home/ubuntu/lanes/sciagent/.cache/drafts/prereg-s28/sweeps.json`
- `/home/ubuntu/lanes/sciagent/.cache/drafts/prereg-s28/spike/d.table`
- `/home/ubuntu/lanes/sciagent/src/sciagent/glm/fit.py`
- `/tmp/rt3/chk.py`

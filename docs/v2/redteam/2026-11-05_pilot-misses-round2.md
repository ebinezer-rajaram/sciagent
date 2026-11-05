# Review of the pilot miss note and PILOT.md edits: ready with fixes

Three findings are blocking and seven are not. All are text. No number, label or share changes, and `GO_SURVIVES_FOUND_MISSES` stands on everything I could recompute.

**Reviewed state:** HEAD moved from fcea1c3 to e04d68c during the review, because the lead committed the tree. `git diff docs/v2/PILOT.md` is therefore empty now. The committed edit has the same size as the diff I read (3 lines in, 3 out). File hashes were stable from the pin to the end:
- `PILOT-MISSES.md`: `6de675dd…`
- `PILOT.md`: `93e26521…`

## Blocking

**1. PILOT.md decision 1 carries only half of the per-gate line (Part B).** Section 5 sends the go/no-go "with that wording and the per-gate line". Section 2.7 defines that line as four counts per gate plus each stratum's control state, and calls it "the survival statement's real content". Line 164 gives exact and searched only.

```
$ inline check of PILOT.md line 164
fixed wording verbatim in decision 1, N filled in: True
stratum or control text in decision 1: []      # searched for strata, 'not applicable', 5 of 6, 0 of 1, 5 of 7, 7 of 11, 4 of 4
decision.json gates: too_easy n/a 22; too_hard n/a 82; C1 n/a 22; C2 0; C3 0; exhaustive_uncertified 0 on all five
```

Suggested addition: "Not applicable: too easy 22, too hard 82, C1 22, C2 0, C3 0; exhaustive uncertified 0 on every gate. Searched tier 1 fits by stratum, with that stratum's control result (not recall for those fits): groups A and B 8 (group A 5 of 6, group B caught), id-24000 5 (0 of 1), id-large 17 (5 of 7), large-exp 9 (7 of 11), large-softplus 41 (4 of 4), above 3,456,000 51 (no control)."

**2. The note labels margin minus SE as C1's margin (Part A, finding 7).** Line 406 says "C1's thinnest margin is 0.02173559760251412 at dev-002". That value is margin minus SE. The margin itself is 0.0266, which is what PILOT.md line 96 says, so the two documents disagree on the same statistic.

```
$ $RUN $RT/b_verdict.py      # sorted by c.margin - c.se; tuple is (margin-se, truth, margin, se)
stored C1 thinnest [(0.02173559760251412, 'dev-002', 0.026617620464606067, 0.004882022862091946), ...
```

Fix: "C1's smallest margin is 0.0266 at dev-002 (SE 0.0049, so 0.0217 above the SE), stored and corrected alike."

**3. Line 390 overclaims the red team's coverage.** It says "label and every number recomputed". The report says "every number I recomputed holds" and then lists what it could not check. The LOG has it right ("every number it checked"). Fix: "the label, and every number it recomputed held".

## Not blocking

**4. PILOT.md never says H2 and H3 fell.** A search for H2, H3 and H4 in PILOT.md returned nothing. Decision 2 gives 0.02198 without saying it is twice H3's 0.01 threshold, or that 3 rows exceed it. Two of those three are exact-class, so H3's fall does not rest on searched fits.

```
$ a_check.py
|delta|>0.01 [('dev-015',3,'planted-hint',0.0167,'exact',10368), ('dev-019',3,'AG-o-S/anon',0.0105,'exact',1200), ('dev-012',3,'B-sparse',0.02198,'searched',663552000)]
```

**5. "One statistic substitution can move" goes further than the prereg allows.** This wording is in PILOT.md line 164 and note line 404. Section 2.5, repeated in the note's section 8, gives a second route: recovery shares can move through `kept`. Nothing moved in this run (20 kept, stored and corrected). "The statistic substitution moves directly" would be correct.

**6. Note lines 404 and 405 pair 0.02198 with rows it did not come from.** That Δ is dev-012 s3, an in-dictionary truth, so it is not one of the 30 rows. The statistic also reads ORACLE on the same 30 units, and those rows are not split out. This errs on the cautious side, but it is imprecise.

```
$ a3.py
B-sparse OOD rows 30 {exhaustive 4, no_control 22, large-softplus 2, R2 1, id-large 1} found misses 7: -0.00442 +0.00149 -0.00138 +0.00179 -0.00302 +0.00035 -0.00216
ORACLE OOD rows 30 {R2 9, large-softplus 12, id-large 3, groups_a_b 3, no_control 3}      # 9 exact, 21 searched
B-sparse in-dictionary misses [dev-008 s2, dev-008 s3, dev-012 s1, dev-012 s3 +0.02198]
```

**7. PILOT.md line 84 is stale, and decision 2 points to it.** "dev-015 s3, the one pilot-scale point" is now out of date, since 25 rows have a measured Δ. "How many carry a miss is unknown" can now read "at least 7 of the 30 found, none changing the count".

**8. The note's `G: count` list misleads on searched fits (line 418).** Those counts are over the points R1 visited, not over G. For example, dev-010 s1 B-rand's 5 are out of 2,884 points, not 16,588,800. The range across the 7 fits is 1,221 to 2,994 points.

**9. Fit certification wording, all minor:**
- The section's 265 is never reconciled with line 84's 267 (267 less 1 truth-ψ refit, less 1 uncertified).
- "Does not say ψ is the best point of its grid" is too broad. For a certified exhaustive fit it does say that, and line 84 relies on it.
- The go/no-go shares table has no LLM rows, so a mark can never appear there.
- Hand line 120 prints the arms' shares unmarked.

**10. Citations and a leftover.** Note line 394 cites "prereg, opening section", but the text is in section 1. Label line 23 keeps `max|delta|=`, which is acceptable because it is the frozen record string.

## Part A: findings 1 to 9 of the earlier review

| Finding | State |
|---|---|
| 1 Strata heading, limits | Answered |
| 2 Too-easy split | Answered (see 5 and 6) |
| 3 `uncertified_better` | Answered; the code agrees (`v.score > floor`, and R2's points are included at line 1361) |
| 4 Warnings | Answered |
| 5 Missing content | Answered; the LOG stop point exists at e04d68c (line 2009) but did not at fcea1c3 |
| 6 Reconciliation | Answered |
| 7 C2 and C1 | C2 answered, C1 wrong (blocking 2) |
| 8 S2 line | Answered |
| 9 Context lines | Answered |

Recomputed from `units.jsonl` and `decision.json` with my own arithmetic, no sciagent import, all matching the note:
- **Group C:** 158 to 357 events, 22 of 23 capped, id-large 4 to 6 starts.
- **Pilot fits:** 1,797 to 2,327 events.
- **No-control fits:** 51, of which 38 capped, minimum 6 starts, 13 misses.
- **R1 shortfalls:** 5 fits, worst -6.901 on dev-008 s2 (48 starts, uncapped, G = 57,600).
- **R2 over R1:** 0 of 115.
- **Uncertified:** 70 points on 17 fits, all identity. All 17 rows of the note's table match.
- **Capped:** 53 rows, 52 baseline, 52 fits.
- **Reconciliation counts:** 195, 119, 115, 65, 124; tier 2 strata +2, +1, +6.
- **Other rows:** ORACLE search misses, `stored_uncertified`, S2 rows, largest gap sum 1.524e-7, smallest gain 0.256.

Other checks that pass:
- **Close count:** 7 of 30, and the 14th shift 0.02266, recomputed from raw per-event values.
- **Hand lines:** CPU 25.41 h; unit line `1d 1h 24min 28.795s`; start and end times; 276 warnings; LEDGER; S3 at 04:17:28 with 7 files and matching sizes.
- **Provenance:** prereg sha `ea71cdea…` equals `decision.json`; split sha `31c18f10…`; F and R are commits; `decision.json`, `units.jsonl` and `timing.jsonl` hashes match the note.
- **Generator:** the rerun differs from the note above "Added by hand" only by the status line and one blank line.
- **Tests:** `test_miss_note.py` and `test_pilot_report_certified.py` show no failures in the last lines of output. The summary line was cut off.

## Part B

- **Fixed wording:** verbatim, with N filled in.
- **Gate values:** all ten match `decision.json`.
- **Decision 1's hold wording:** "releases the control room's hold" matches section 5's "No longer held for this check".
- **The "It is not the decision" line:** fine.
- **Decision 2:** "lower bound ... no bound at all" matches section 1, and 13 of the 25 in the 51 fits is correct.

The only excess claim is finding 5. The only rule shortfall is finding 1.

## Part C: shares confirmed by a route with no shared code

My route was raw `ledger.sqlite` and `details/*.json`, with G from a text tokeniser and grid lengths read from `grids.py`. It imports neither `psi_search_of` nor `_Slots`.

```
$ c3.py
text G vs units.jsonl grid_size on 255 fits: mismatches 0
coordinate rows 267 (205+62), distinct fits 255, all in units; exhaustive 318 (143+175); B-np 60, B-lib 22, no structure 3
fit_config strings: 1 (full=512); env fit/5 on 910 of 910; psi_search in details 0, in llm 0
ORACLE 41/60 (42 on grid, 1 truth-psi)  B-lib 0/38  B-rand 44/60  B-sym 27/60  B-sparse 51/60  planted 35/60  B-sym@10F 6/10
AG-c-S/named 12/60  AG-o-S/named 13/57  AG-c-S/anon 18/60  AG-o-S/anon 18/60  all 265/585; certified 583/585
```

- All 12 systems, both shares and the unit counts equal the PILOT.md table.
- Per-system coordinate rows equal the prereg's section 2.1 list.
- 267, 318 and 85 on line 84 hold.
- No G falls near the limit: the nearest values are 500 and 576.

**Line 86 edit:** correct.
- Both rows are flagged in the per-system table and in both group tables. dev-003 and dev-005 are far and out, which are the marked columns.
- The -0.1462 and 0.25 recompute from raw cells.
- The three no-submission rows have no field.

**SPEC §2.2 wording:** it matches. The quote is exact, certification covers every evaluated point, and the two shares sit side by side. "Coordinate-optimal" labels the search path, not a tested property. On all 25 miss rows, stored and chosen points differ in at least 2 slots, which is consistent but not proof.

**PILOT.md outside the notes block:** byte-identical on regeneration with `--b10f-truths 10`, and the store hash is unchanged (`dc0a9f3c…`).

## What I could not check

- **No search reruns, as instructed.** So there was no `fit` call on a pilot unit, and no R0, R1, R2 or hunt rerun. `psi_search` was not observed on a real pilot fit. It rests on the one-line branch at `fit.py:851`, my grid sizes, and the run's recorded byte checks. The 317 exhaustive rows rest on the same code argument.
- **Copied from the earlier report, not re-derived:**
  - the 63 and 240 warning counts
  - the 0.36-nat neighbour
  - the Clarabel retries
  - "no warning" on the 25 miss rows' solves
  - the transcript search
  - the launch flags
- **S3:** I checked the listing and sizes, not hashes.
- **The other 49 uncertified points:** still not enumerated, as the note says.

## Process notes

- **One process was not fully kept.** Some read-only checks ran in parallel, so two nice'd processes overlapped briefly.
- **Two of my own errors, corrected:** `a2.py` used the wrong dictionary key, so `a3.py` replaces its out-of-dictionary lines. My first report regenerations lacked the plan flag.
- I edited no tracked files.

Scratch files are in `/home/ubuntu/lanes/sciagent/.cache/drafts/s31/rt2/`.

Files reviewed:
- `/home/ubuntu/lanes/sciagent/docs/v2/PILOT-MISSES.md`
- `/home/ubuntu/lanes/sciagent/docs/v2/PILOT.md`

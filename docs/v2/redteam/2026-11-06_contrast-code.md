# Review of P4's contrast code at 84390bb

**Verdict: READY WITH FIXES.** I found no read with a wrong sign or CI. The fixed order, floors, seed matching, generators and gates all match the text. Findings 1 to 3 should be fixed before the merge to v2, and findings 4 to 8 are text fixes needed before `F2`. Findings 9 to 16 don't block.

Reviewed: HEAD `84390bb0c47d17b81648ab0fd9f6b4cf921ac6de`, `contrasts.py` sha256 `ac0b9783…a814`. The stat shows three files. I changed no repository file: `git status --short | wc -l` gives 0, and scratch is under `/tmp/rt-s35`.

Unless noted, each script was run from the worktree as:
`UV_PROJECT_ENVIRONMENT=/tmp/rt-s35/venv PYTHONDONTWRITEBYTECODE=1 nice -n 15 uv run --frozen python /tmp/rt-s35/scripts/<name>.py`

## Blocking

### 1. A truth control 1 cannot check leaves every read, and nothing reports it [merge and F2]
**Where:** `contrasts.py:46-48` and `:349`; `analysis.py:757-761` and `:771-775`.

- With P4's two seeds, one B-lib row at `-inf` or one failed B-lib row leaves one paired seed. The truth is then neither checked nor dropped, and it leaves all seven reads.
- No text sets this rule. SPEC §6.4 drops a truth only if ORACLE does not beat B-lib. The `analysis.py` docstring says the truth "is checked on its other seeds", and on two seeds there are none.
- `ContrastRead` and `FloorRead` carry neither `dropped` nor the unchecked truths, yet `P4-PROPOSAL.md:219` says "the drop is reported".
- `reportable=False` also can't separate a failed gate from `n < 2`, and prereg line 186 wants "the reason given". The script would have to rerun step 1 outside the frozen module.

`probes.py` output:
```
base                     C1: ... n=8 floored=0 no_common=0
B-lib -inf on (t3,2)     C1: ... n=7 floored=0 no_common=0
B-lib failed on (t3,2)   C1: ... n=7 floored=0 no_common=0
verdict.kept ('t1','t2','t4','t5','t6','t7','t8') dropped () controls_pass True
same on 3 seeds: n = 8
```

**Fix:** add a public result holding `kept`, `dropped`, `unchecked` and `controls_pass`, drawn on stream 1000.

**Wording for §2.7:**
> A truth is dropped if ORACLE does not beat B-lib. A truth with fewer than 2 seeds on which ORACLE and B-lib both have finite gaps cannot be checked and is not kept; on P4's two seeds one B-lib at `-inf` or one failed B-lib row does this. Both kinds leave every read, and both lists are printed beside the reads, on both tables.

**Question for the control room:** should a B-lib at `-inf` on one of two seeds remove the truth? The code says yes, and no text does. `PILOT.md:102` lists no such B-lib row on the pilot.

### 2. The tests leave the pair rule's main path unpinned [merge]
**Where:** `tests/campaign/test_contrasts.py`.

Eight rule-breaking mutants survive all 63 tests. The code itself is right on each, so this is a gap in the guard, not a wrong number. Each mutant runs in a pilot-like shape, as the table shows. The pilot facts come from documents, not the store.

| Mutant | Line | Why it matters |
|---|---|---|
| Baselines never floored | `contrasts.py:262-268` | The only floored rows on C1's sides on the pilot were B-sym's two `-inf` rows (`PILOT.md:102` and its table) |
| B-lib sets no floor; references set no floor; ORACLE sets no floor (3 mutants) | `:276-279` | B-lib held 54 of 60 floors on the pilot (round 2, finding 4); in the tests' world B-lib is never lowest |
| S6 absent on the first side only | `:383-384` | No test floors a `second` side in S6 |
| Every session counts as void | `:342` | Only 1 of 63 tests has a session, and it is void; real tables hold a session per LLM unit |
| Uniqueness only over contrast rows | `:246-251` | §2.7 says unique "over the plan", and only contrast-system collisions are tested |
| `no_common_seed` counts C4's in-dictionary truths | `:376-388` | The printed count would be wrong, and no test asserts C4's |

The full run was `cd /tmp/rt-s35/wt && nice -n 15 /tmp/rt-s35/venv/bin/python /tmp/rt-s35/scripts/mut.py`:
```
23 caught of 36 ; survived: [...13 names...]
```
- The other five survivors are listed under finding 14.
- `mut2.py` gave `31 mutants; 31 caught; survived []` on the engineer's list.

**Fix:** seven short tests are at `/tmp/rt-s35/wt/tests/campaign/test_rt_extra.py`. `mut3.py` shows they pass on the commit and kill all eight:
```
unmutated: 7 passed
killed   every session counts as void <- ['test_a_live_sessions_row_stays_in']
killed   baselines never floored <- ['test_a_baseline_side_takes_the_floor', 'test_s6_leaves_out_a_floored_second_side']
killed   B-lib sets no floor <- ['test_b_lib_sets_a_floor']
```
Two sample cases, on the test file's own world:
- B-sym `-inf` on (t1,1) gives C1 point +1/144.
- B-rand -0.5 and AG-c `-inf` on (t1,1) gives floor -0.6875 and point -1/288.

### 3. `rows` does not hold every row a read used [merge and F2]
**Where:** `contrasts.py:69-70` and `:391-396`; prereg lines 113 and 117, S5.

The docstring says `rows` is "every row a read used" and "B-lib is not read". That is false: a floored row's value comes from the row that holds the floor, usually B-lib. So a read can change sign with no substituted row in its `rows`. `N`, `T`, S5 and the report's "its rows" would then misstate it.

`probes2.py` output, substituting only `(t1,1,B-rand)`:
```
stored C1 +0.003418 [-0.005371,+0.007812] n=8
fixed  C1 -0.047363 [-0.157715,+0.007812] n=8
rows equal True ; substituted key in rows of any read: False
```
This is an accounting fault, not an arithmetic one, and the size is a toy's. Round 2 found no floor moved on the pilot. But B-rand and agent rows held 6 of 60 floors there, so it can happen.

**Fix:** have `floor_rows` return the rows that set each floor, and add them to `rows`.

**Wording for §2.7:**
> `rows` holds both sides and the ORACLE on each matched (truth, seed), and for each floored row the row or rows that set its floor. N, T and S5 count `rows`. Rows that act only through `kept` or a control are not in `rows`; a read can change through them, and the report then names them.

If you keep the code as it is, fix the docstring and keep only that last sentence.

### 4. §2.1 contradicts §2.7 on failed units [F2]
**Where:** prereg line 46 says "Void and failed units ... are absent rows". §2.7 floors a fit failure, and `_takes_floor` does so (`contrasts.py:266-267`).

**Failing case:** `hand.py` table, t3 s2. Absent gives a different d from the coded -1/32.

**Wording:**
> Void and failed units are counted and listed. Under section 2.7's pair rule a void, or a failure of any cause but "the submitted structure could not be fitted", is absent; that failure takes the floor. None gives `BYTE_FAIL`.

### 5. §6 says `verdict` is not on this path, and it is [F2]
**Where:** prereg line 203; `contrasts.py:337`.

**Wording:**
> ...in place of PR 6's "handled by `derive` and `verdict`": `verdict` gives controls 1 to 3 and the drop on the unfloored table, and the pair rule gives the reads.

### 6. The embargo and `BYTE_FAIL` name only `read_contrasts`, and S6 needs a second entry point [F2]
**Where:** prereg lines 70-76, 91, 125 and 182; `contrasts.py:423`.

`read_floors` computes every read again, plus the rows-left-out read. It is not in the interface, the embargo, `reads.json` or `BYTE_FAIL`. As written, a script could call it before the terminal step and break no sentence of the text.

**Wording:**
- Lines 91, 125 and 182: replace "`read_contrasts`" with "`read_contrasts` or `read_floors`".
- Line 125: "`stored`, `fixed`, the S2 read and S6's `read_floors` output on both tables are written once...".
- Interface block: list `read_floors`, `FloorRead`, `ScriptedControls` and `same_read`.

### 7. The text does not say which condition C3-free and C4 read [F2]
**Where:** prereg line 93; SPEC §6.2 says "named condition" for C1 only; `contrasts.py:187-188`.

The code chose named, and `test_each_read_compares_the_systems_the_text_names` pins a choice the text does not make.

**Wording:**
> `C3-free` (AG-o-S against AG-c-S, named condition, at equal E); `C4` (AG-c-S, named condition, against B-sparse, about 20 truths, the out-of-dictionary half).

If named is not what was meant, this changes the read.

### 8. "Only rows of `contrast_rows` enter" cannot be implemented as written [F2]
**Where:** prereg line 78; `contrasts.py:303`.

`read_contrasts` has no plan argument, so it filters by system name and relies on `load_rows`. By reading, not run: the terminal assertion covers `rows` only, so a stray row of a contrast system could set a floor or feed a control unseen.

**Wording:**
> Only rows of the systems `contrast_rows` names enter. A table from `load_rows` holds planned units only, so these are its rows; the terminal step asserts that each table's rows of those systems are a subset of `contrast_rows`.

The script should make that wider assertion, not only the one on each read's `rows`.

## Non-blocking

### 9. One finite row outside the read can decide it
This is the accepted rule, not a code fault, so it is for the control room. `probes.py`:
```
floor at -0.75 (B-rand)        C1: +0.006836 [-0.010742,+0.015625]  without +0.015625
B-rand finite -7.3 on (t1,1)   C1: -0.402539 [-1.238867,+0.015625]  without +0.015625
```
`gonogo.py:46` records an explosive B-rand on the pilot. S6 shows the effect but does not bound it. Round 2's option of pinning the floor to B-lib was not taken, and taking it now would be a second choice made after the look.

### 10. A baseline that produces no model is absent, while an agent that produces none is floored
This follows the text. It is round 2's finding 4 on the baseline side (`search.py:624`, "no proposal produced a certified fit"). `probes.py`: B-sym failed gives C1 +0.015625, and AG-c with no submission gives +0.006836. The pilot had 0 failed baselines. State this as deliberate, or add that message to the floored class.

### 11. Gates are not stratum-specific, and `n = 2` is reportable
Controls pass on all truths while 12 of C4's 20 truths show planted level with B-rand (`probes2.py`: `controls_pass True ... ties 12`, `C4 reportable True`). At `n = 2` the 95% interval is just [d1, d2]. The report should say both.

### 12. The stale-gap check is too broad
`contrasts.py:311` compares whole rows. A `nan` in a curve, which no read uses, raises "stale gaps" on a table that is `derive`'s output, and that gives `BYTE_FAIL` (`probes.py`). `likelihood.py:134` rules out `nan` for that path, so this is unlikely. Compare `gap` and `gap_closed` only.

### 13. Read 0's stream equals the bare root
`probes.py` on root 4242: `[R,0]==[R]: True  [R,0]==R: True`. So C1's generator equals `default_rng(root)`. Nothing uses `20261110` at this commit (grep). The script must not do so.

### 14. Test quality
- `test_contrast_rows_take_every_truth_before_any_drop` (lines 1040-1058) builds `rows` and never uses them.
- The first assertion of `test_row_keys_are_unique...` cannot fail.
- The stub record says 60 failed, then 61 (`/tmp/s35-contrasts/stub-run2.txt`), not 63. `test_every_truth_dropped_leaves_seven_empty_reads_and_no_exception` and `test_two_tables_with_equal_n_resample_the_same_truths` were never seen failing.
- There is no cross-process hash-seed test for the reads; the engineer's `bytes.py` was a one-off.
- Other survivors, all low: `startswith` changed to `in`, `np.mean` in `_mean`, and the ORACLE missing from `rows` on one-seed truths. Two more are equivalent: `message` on ok rows, and the gap filter.

### 15. The 1,000 rows of §7 hold only on a stand-in plan
No real P4 plan exists at this commit. The test's sweep carries its budget through `llm_letter`. `B-sym@F=80` cannot be expressed: `CampaignError: unknown baseline systems`, and a twin named `B-sym` is refused. `contrast_rows` also depends on truth order if `control_truths` is set, so pass the campaign's order.

### 16. Small wording
- Prereg line 78, "Two calls resample identically": the indices match, but at equal `n` with a different kept set they map to other truths.
- LOG 2026-11-05 says "the contrast's rows" and the prereg says `contrast_rows`; these are different domains.
- Extension 7's second pair quotes the rejected all-rows floor, not the adopted rule.

## What held
- **Tests and checks:** 63 passed; ruff clean; `tests/test_invariants.py` and `test_campaign_analysis.py` 977 passed; `test_address_constants.py` 85 passed.
- **Reads by hand** (`hand.py`, exact fractions, root 4242): C1 -1/128 with CI, C4 with CI, C3-free -0.02734375, and S6 with 5 floored rows and without-point 0.04296875 all match. The table holds `-inf`, a fit failure, an absent failure, no submission, void rows, and a floor set by a non-side row.
- **Differential** (`diff.py 400`): a separate exact-fraction implementation of §2.7 against the code on random toy tables. 2,800 reads gave `mismatched reads 0`, covering rows, `n`, S6, `no_common_seed` and `kept`. `controls_pass` was taken from `verdict`, so that half is not independent.
- **R = 10,000 on P4 shape:** the code equals plain numpy to every printed digit.
- **Gates** (`gates.py`): ORACLE `-inf`, ORACLE failed, underfit flagged, underfit unrecorded, planted level with B-rand, B-sym@10F low, and no B-sym@10F rows each give 0 of 7 reportable and `nan`. Control 4 failing leaves exactly C1, C3-free and C4.
- **Determinism** (`bytes.py`): five runs across hash seeds and row permutations gave one sha256, `75ce8672…e888`.
- **`contrast_rows`:** it reads only `.id` and gave 840 rows today, as expected.

## What I could not check
- `load_rows`, `row_from_cell` on real cells, and any store, by instruction. The pilot figures above (54 of 60, the -inf rows, the 0 failures) come from documents and were not recomputed.
- `scripts/p4_search_misses.py` does not exist, so the terminal assertion, embargo, `reads.json`, label logic and "a read changes" are untested.
- The `rescaled` rows and the C2 reads on real arms. The real plan, as in finding 15.
- How control 4's result maps to `ScriptedControls`; I tested only the bool.
- The frozen roots: I used toy roots only (4242, 5, 77, 11) and drew no `ctl*` stream. The dev split was not needed.
- mypy, the slow tier and the full fast tier were not rerun. Everything ran on this machine with Python 3.12 only.

Scripts are in `/tmp/rt-s35/scripts/`.

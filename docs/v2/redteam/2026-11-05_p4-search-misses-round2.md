Saved by the lead from the reviewer's reply; the reviewer has no write access. Reviewed: `docs/v2/prereg/2026-11-05_p4-search-misses.md` at 5fd121f, code at `p4-rehearsal` 6273833.

# Red-team review, round 2: P4 search-miss amendment (5fd121f)

**Verdict: NOT READY, but close.** There are 6 blocking and 7 non-blocking findings. Findings R1-1, R1-3, R1-5, R1-7 and R1-8 hold as fixed. R1-2, R1-4 and R1-9 do not fully hold, and the fixes opened two new problems: `--rehearsal` is a working off switch for the embargo, and the floor decides the outcome on pilot rows. All six are text fixes, but findings 4 and 5 need the control room to accept a rule. I suggest the lead checks the fixes against this list and sends only a redesigned floor to a third pass.

**Pinned:** the amendment at 5fd121f has sha256 `4156e073b94778d1…`, as you stated. I read code at `p4-rehearsal` 6273833. That branch has moved on to d01eb48, and I did not review that head or 9047463.

## Round 1 findings

| R1 | Status | Note |
|---|---|---|
| 1 Guard moved after data | **Holds** | Repointing now fails. The residual route needs a forged sidecar (finding 8), and a mid-run fix cannot get through (finding 1) |
| 2 Embargo | **Partly** | Provisional writes, `refuse()` and kill-then-resume are closed. `bug` and row `BYTE_FAIL` ends are not (finding 2) |
| 3 Tier 1 | **Holds** | `contrast_rows` reads no score and covers every truth before any drop (see 12) |
| 4 `-inf` and pairs | **Partly** | There is now a rule, but it has faults (findings 4, 5) |
| 5 Not reportable on both tables | **Holds** | Excluded from survival, and the label cannot be survive if none is reportable |
| 7 Missing cell | **Holds** | Void and failed units are counted, and missing gives no label (see 13) |
| 8 First label stands | **Holds** | Restated in §2.10, but the blanket carry brings conflicts (finding 9) |
| 9 Rehearsal | **Partly** | Roots and ceiling are fixed, and the guard answer opened finding 3 |

## Blocking findings

### 1. BLOCKING: a campaign that needs a code fix cannot get through, and the text gives no route
It fails closed, so it is safe against gaming, but P4 would be stranded.
- A fix means a new commit, and the store's guard refuses that commit without `--reuse-from`, which appends a `reuse` line.
- The script then refuses for good ("one `establish` line and no `reuse` line"), with no label. P4's report refuses without `reads.json`, so P4 has no report at all.
- A new frozen version cannot pass the chain as written: HEAD must be `T`, the FREEZE file must have one commit, and the store's commit must be `T`. PR 2.10's amendment route is therefore not expressible for a fix to the script either.
- LOG 3aa1736 sets a same-day rule for when `--reuse-from` is allowed, so a lane following the LOG walks into this and finds out only when the check starts.

The pilot's stores each have one `establish` line and no `reuse` line, so a clean run is possible. P4 is about three times larger, has three models and an unbuilt `rescaled`.

**Fix:**
- State that a code fix during the campaign is a stop point.
- Give the lawful routes: a new store at a new `T`, or a control-room amendment frozen before further units, naming the commit, confined to paths outside the fit and scoring stack, with the guard accepting exactly that `reuse` line.
- Make the launcher refuse `--reuse-from` on the P4 store without that amendment.
- Say how an amended script runs: a `T2` that differs from `T` only in script, test and `docs/`, with the store witness staying `T`.

### 2. BLOCKING: reads are still written on ends nobody chose, and anyone can choose `--end`
**Unchosen ends.** `RESUMABLE_ENDS` is `killed` and `terminated`, and the pilot test confirms `bug` and `byte_fail` refuse a resume. So both are terminal, and §2.8 writes `stored` and `fixed` on a terminal end. Only a failed equality withholds reads. That has two effects:
- A row `BYTE_FAIL` publishes reads, against §5's "before any reading".
- A `bug` in tier 1 unblinds P4, and the script fix it needs is then written with the contrasts in hand.

**`--end`.** Nothing says who may use it. A lane session can kill the driver and pass `--end`, and this satisfies "before `reads.json` exists" by making the file early. PR 9 reserves `STOP` for the owner or control room.

**Fix:** write no reads on `bug` or any `BYTE_FAIL`, and give `--end` the same authority and note as `STOP`. The §2.10 test list should cover both ends.

### 3. BLOCKING: `--rehearsal` is an off switch for the embargo
- The mode stays in the frozen bytes, so it exists at `T`.
- It skips the split guard, and its `--test-split` refusal is by name, so the test split can be passed as `--split`.
- The store refusal is a blacklist ("fingerprint commit is `T`"), so a copy of the P4 store without `fingerprint.jsonl` passes. No forgery is needed.
- "The mode changes exactly these" lists no plan or truth override. So either it cannot read a reduced-budget store, or it reads P4's addresses on that copy.
- It then calls `read_contrasts` and writes real `stored` reads under `.cache/drafts/`. The planted shift touches only `corrected`, and the label of record is untouched.

**Fix:** use a positive allowlist. Check `--split` against the dev constant, require every truth's `split` to be `dev`, require the store to be under `.cache/drafts/`, refuse if `docs/v2/FREEZE-6.1.txt` exists in the tree, and test each. List the plan override in the mode.

### 4. BLOCKING: the floor's domain and order are not fixed
I applied the rule to the 670 pilot dev rows. This is exploratory, a plain mean bootstrap on 20 truths, not the contract code (`/tmp/rt2/floor.py`).
```
floor holder, all rows: B-np 50, B-lib 10
floor holder, contrast rows: B-lib 54, B-rand 3, AG-c-S/named 2, AG-o-S/anon 1
floors that differ all-rows vs contrast-rows: 50 of 60
floors moved by the 25 substitutions: 0
```
- **Domain.** "All rows... in the table" makes the floor mostly B-np's reading, and B-np is not in `contrast_rows`. What `table` holds is not pinned, for example Haiku, Opus or sweep rows, and that changes the number.
- **Order.** `table` arrives after `derive`, but flooring must happen before `derive`, so `read_contrasts` has to re-derive. If flooring precedes the controls, an ORACLE at `-inf` becomes finite and `oracle_inf_units` stops seeing it, so control 1's upper-bound half is disabled. Control 2's rank rule changes the same way.
- **Failed is not always "not an outcome".** `run_score_job` raises `CampaignError("the submitted structure could not be fitted")`, which is caught as a unit failure, gives status 0 and makes the row absent. So an unfittable submission vanishes while a non-submission is penalised.
- **Pairs.** The value is a mean over each side's own seeds, so one absent seed compares different worlds.

**Fix:**
- Pin the domain to `contrast_rows`, or to a fixed reference such as B-lib, which cannot be substituted.
- State that controls read the unfloored table, and that ORACLE and B-lib are never floored.
- Split `failed` by cause.
- Match seeds within a pair.

### 5. BLOCKING, needs control-room acceptance: the floor is a new P4 primary-analysis rule, unflagged, and it decides results on pilot rows
`read_contrasts` is P4's own contrast code, so the floor defines C1 to C4 and not just the miss check. It is not in the extension list.
```
AG-c-S/named - B-sym
  floor (all rows) +0.00691 [-0.00939, 0.02358]
  floor (contrast) +0.00480 [-0.01029, 0.01957]
  absent           -0.00080 [-0.01384, 0.01018]    sign flips
AG-o-S/named - AG-o-S/anon
  floor (all rows) -0.01482 [-0.03161, -0.00145]
  absent           -0.00543 [-0.01480, 0.00384]    CI relation flips
```
On your two questions:
- **Outcome-neutral?** The rule is blind to arm, but its level comes from other systems' outcomes, and its effect is not neutral. It helped AG-c against B-sym and hurt AG-o, on 7 rows of 670.
- **Under substitution?** It behaves well on the pilot. No floor moved, and reads shifted by at most 0.00022. That is because B-np and B-lib hold the floors, and neither has a coordinate row.

**Fix:** add it as an extension and get acceptance. Print each read's floored-row count and its read with those rows absent, as a secondary. `bootstrap_median_ci` already handles `-inf` if the control room would rather avoid a floor.

### 6. BLOCKING: control 5's gate on C3 was dropped silently
§2.7 says controls 1 to 3 gate every read and control 4 gates C2, and §2.10 never mentions control 5. LOG 2026-10-09 and P4-PROPOSAL §4 say control 5 gates C3. Under decision 12 its inputs go with the policy rows, so `C3-free` keeps a preregistered gate with no data, and the text releases it without saying so. Dropping it may be right, since `C3-free` uses no policy, but c65a614 does not cover it. The `controls` argument also carries control 6, which gates no read.

**Fix:** add it to the extension list, with one control-room line.

## Non-blocking findings

**7. `--out` is not pinned.** A fresh start in a new directory is a resume around "every resume is refused", with a fresh CPU ledger. The first label still stands, so the harm is limited. Fix the path in the script.

**8. The chain's residual route is a forged sidecar.** On a toy repo, a fork from before `T` with a new `T'` passes every check but the store witness. Editing one line of `fingerprint.jsonl` passes that too:
```
one commit, it is HEAD: ok / first parent is S: ok / diff is that file alone: ok
hash in S's LOG: ok / store witness is T: ok
rule=after the numbers
```
The manifest cannot catch this, because it is taken at the run's own start. It takes deliberate falsification, so this is not blocking. Requiring `T` on `origin/v2`'s first-parent chain and comparing the sidecar with a copy taken at launch would close it.

**9. The blanket carry conflicts with new text.**
- §5's "`INCOMPLETE`... Resume rules as PR 5" contradicts §2.8, because that label exists only once `reads.json` does.
- PR 9 point 1 still gives `BYTE_FAIL` for start-up guards, against §5's "no label".
- §5 lists a split hash as `BYTE_FAIL`, but it is a start-up guard.
- §2.8 says the label goes to "no other file", while §5 says "One label to `decision.json`".
- §2.10 does not say its guard list replaces PR's, whose `git diff F HEAD` guard would fail.

**10. The 7e-6 figure is misquoted.** That was one draw of a neighbouring read's shift under generator coupling, not the effect of dropping a truth. When `n` changes, the resample matrix is new, so expect about √2 × 0.00017, or 0.00024, plus the real effect.

**11. Two fit pins are weak.** `git diff` output depends on abbreviation length:
```
--abbrev=7  3bc96fc7…   --abbrev=12  a1edcef7…   --full-index  5ab0f61d…
```
Auto-abbreviation can grow with the repository, so a hash of the plain diff could cause a false refusal. Use `--full-index`. "Pinned by its blob hash at `S`" is also vacuous, because `T`'s tree equals `S`'s. It needs a constant in the script.

**12. `contrast_rows` and `rows` can drift apart.** Assert at the terminal step that every read's `rows` is a subset of `contrast_rows`, on both tables. Control 2's bootstrap generator is also unspecified.

**13. Smaller points.**
- "H1's result is known before tier 1" conflicts with nobody reading `journal.jsonl`, so a small controls file is needed.
- "The split's parent's root" is still not an exact recipe.
- One stuck tier 2 unit, for example Opus, blocks the whole check, with no way for the owner to abandon it.

## Non-blocking answers from round 1

| R1 | Answer |
|---|---|
| 10 | Right fix (a generator per read), wrong number (10) |
| 11 | Right, except the floor and control 5 (5, 6) |
| 12 | Moot under decision 12 |
| 13 | Right in substance, weak pins (11) |
| 14 | Right: 1,320 + 620 = 1,940, and tier 1's 1,000 sums |
| 15 | Right, through the preamble's mapping |
| 16 | Right: 172 + 6 + 8 + under 1 = 187 |
| 17 | Right: `oracle_psi_source == "truth"` matches `systems.py` |
| 18 | Right, except control 5 |
| 19 | Right |
| 20 | Right |
| 21 | Right: 20261110 to 12 appear nowhere in code or docs at 5fd121f or 6273833. The one drafts hit is float digits |
| 22 | Right, except the split recipe (13) |

**6273833:** it does change refusal text only. The six-file diff is the one line at `features.py:417`, and the `grammar.py`, `syntax.py`, `wiener_hopf.py` and `catalogue.py` edits are strings, plus a docstring in `catalogue.py`.

## What I could not check
- **Group B's R0 at the freeze head:** still not run. The amendment makes it a precondition of `F2`.
- **`bug`, `--end` and `--rehearsal` paths:** read from text and the pilot code, and none of them exists yet.
- **Floor results:** these are dev rows at 20 truths with three seeds. P4's rates and sizes may differ.
- **Limits kept:** no test-split record read and no ctl* stream drawn. The one read of a ctl-named file was a text match that printed one short fragment. No repository file changed, and scratch files are under `/tmp/rt2`.

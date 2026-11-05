Saved by the lead from the reviewer's reply; the reviewer has no write access. Reviewed: `docs/v2/prereg/2026-11-05_p4-search-misses.md` at 71997c2, against `docs/v2/prereg/2026-11-04_pilot-search-misses.md`, `scripts/pilot_search_misses.py`, and `p4-rehearsal` a9e43a6.

# Red-team review: P4 search-miss amendment (71997c2)

**Verdict: NOT READY.** There are 9 blocking and 13 non-blocking findings. The worst is that the §6.1 guard can be moved after the contrasts are seen, and I demonstrated this on a toy repository. Every fix is a text change before `F2`, and no P4 unit exists, so nothing is lost. Finding 6 needs a control-room or owner decision, not a lane decision. Findings 1 to 3 change the design, so I recommend a narrow second round. That is the control room's call, because it asked for one round.

**Inputs pinned**
- The amendment at 71997c2 has sha256 `8d3df8f9ad6c2dfa0472c96a5824139695be6452af60ed0c7355355307af1cad`.
- 71997c2 is the head of `v2`, and I read `p4-rehearsal` at a9e43a6.
- `scripts/pilot_search_misses.py` is byte-equal to `R` (5e5a169) at both commits (`cmp`).
- The dev split hash recomputes to `31c18f10…a577`, which matches the script's constant.

## Blocking findings

### 1. BLOCKING: the guard tying the run to `S` can be moved after the data are seen
- `S` is read from `docs/v2/FREEZE-6.1.txt`, and that path is under `docs/`, which the guard exempts.
- Nothing requires that file to have one commit, requires its parent to be `S`, or requires `S` to predate the campaign.
- `read_contrasts` is not in the hashed script, so only `S` protects it.
- The pilot's hash check is a substring test on HEAD's LOG (`if file_sha not in log`, `run_guards`), not "the LOG in `R2`".

I built the guard as worded on a toy repo in `/tmp/rt-guard`. I changed the analysis code after "the campaign", then repointed the file:
```
ancestor: ok
diff outside docs: empty
script hash in S tree recorded: ok
dirty files: 0
--- what runs:
def read_contrasts(): return 'rule chosen after the numbers'
```
Also, "the hash in the §6.1 commit" names no path. If `R2` precedes `S`, the script cannot hold that hash as a constant.

**Fix:**
- Require `git log` on the FREEZE file to show exactly one commit, whose first parent is `S`.
- Read the LOG and the split hash through `git show S:<path>`, and name that path.
- Bind `S` to the campaign with the store's own witness, `fingerprint.jsonl`: one `establish` line, no `reuse` line, and a commit whose tree equals `S` outside `docs/`.

### 2. BLOCKING: the embargo ends when `decision.json` exists, and the copied code writes that file early
§2.8 forbids reading a contrast "before that file exists". By reading the code (not run), three paths write it early:
- `after_stage` calls `write_label(provisional=True)` at the end of tier 1, with stored and corrected values. PR 5's provisional paragraph carries through "Beside the label as PR 5".
- `refuse()` writes the file on any failed start-up guard, so a dirty tree lifts the embargo by the letter.
- `--finalise` after a kill writes the full reads, and `main` then prints the resume line. `check_start` never looks at `decision.json`.

So an operator can read both tables and then choose whether to resume. That was harmless on the pilot, where the verdict was already known. On P4 it is the first unblinding. The "frozen amendment with a stronger reference" under `CONTROL_FAIL` would likewise be written with the contrasts in hand.

There is also a conflict inside the text. PR 9 says the equality failure stops "at once", and the pilot runs `check_baseline_verdict` at start. §2.8 says computation happens only at the end, while the §2.10 test bans only printing and writing.

Enforcement is procedural, and on the same day a helper drew an unapproved stream (INBOX, "unapproved rung-0 draw").

**Fix:**
- Put the reads in a separate file, written once, on a terminal end only.
- Refuse a resume once that file exists, and drop the provisional label.
- Move the equality check to the end in §9.
- Make the P4 report refuse to run without that file.

### 3. BLOCKING: tier 1 is undefined, and the only rule offered cannot be evaluated
```
grep -n -o -E ".{60}[Tt]ier [12].{50}" amendment   # 6 uses, no definition
```
- PR 8's definition (baseline rows) therefore stands under the preamble, and the census code agrees (`2 if all(r.llm ...)`). The accepted "LLM rows move into tier 1" is never stated.
- §7 says "the rule is the code's `rows`", but `rows` comes only from `read_contrasts`, which §2.8 allows only at the end. The run order needs tier 1 first.
- `rows` is taken after the drop rule, so tier 1 would depend on outcomes.
- §6 says dropped rows are still checked, but `rows` omits them.
- §7 counts control rows, but `rows` is defined as "every row the read used, gap references included".
- A truth kept only in `fixed` has rows that were never tier 1.

**Fix:** a pure `contrast_rows(plan, truths)` that reads no score and covers all truths before any drop, including gap references and control rows.

### 4. BLOCKING: `-inf`, no-submission and void pairs have no rule, and PR 6 does not carry
PR 6 says `-inf` "is handled by `derive` and `verdict`", but `verdict` is not on this path.
```
paired_bootstrap_ci with -inf -> ScoringError paired samples must be finite
P(no -inf in 80 B-sym rows at the pilot rate 2/60)= 0.0664
```
- PILOT.md lines 102-103 list 4 `-inf` units (B-sym twice, B-rand, AG-o-S/named) and 3 no-submissions.
- `analysis.py`'s summaries leave `-inf` out of means.
- At the pilot rate, C1 has about a 93% chance of meeting a `-inf`. Then either the run crashes at its last step, for which §5 has no label, or the worst outcomes are silently dropped, which is not outcome-neutral.
- A substitution can also move a row across the boundary.

**Fix:** fix in the text how a pair is treated when one side is `-inf`, absent or void, how seeds combine within a truth, and which label an exception gives.

### 5. BLOCKING: a read not reportable on both tables counts as unchanged
The third clause compares `reportable` states only. If a control fails on both tables, or a read has no rows, no read "changed" and the run prints "The contrasts survive". That is a survival statement on a comparison with no passing control (SPEC §6.4, CLAUDE.md).

The equality check has a related fault. If non-reportable reads carry `nan`, "field by field" equality gives a false `BYTE_FAIL`:
```
two not-reportable reads with nan fields equal? False
```
**Fix:** exclude non-reportable reads from survival and name them on the label line, define their field values, and compare by `float.hex`.

### 6. BLOCKING, needs a decision: the read list does not match the owner's approved P4 design
```
sed -n '583,602p' LOG:  "approved ... decisions 1-17 as recommended ...
  add `rescaled` on Sonnet only; descope C5 to P4b; add controls 4-6"
git grep -E "P4b|descop" (both commits): no reversal found
```
Decision 12 says that move takes C3's AG-S-boed half too. I grepped for a reversal but did not read the whole LOG. INBOX c81749a and SPEC §7 still say C1 to C5, so either that is the reversal or it is a slip. Nobody has said which, and the amendment does not mention it. The consequences:
- If Δ9 stands, C2 is four reads (two paired differences per arm), not two, and 160 `rescaled` rows are missing from tier 1. `controls_p4.py` says `rescaled` is "not built yet".
- If decision 12 stands, `C3-boed`, `C5-gap`, 240 tier 1 rows and 320 census rows do not exist. Those reads then fall through finding 5.
- Control 5 gates C3 and C5, FSD control A1 failed, and option 2's `Telling` change returned to the owner (LOG, line 1839). I did not establish control 5's current state.

**Fix:** get the decision, list the actual reads, and say what happens to a read with no rows.

### 7. BLOCKING: "a missing cell stops the run" cannot be met as the code means it
`check_table` fails on any `table.missing` and on `len(table.rows) != EXPECTED_ROWS`. `load_rows` drops void sessions' rows, and a session without a score cell is not counted as missing. PR relied on a hand census with 0 missing, 0 failed and 0 void, and the amendment removes that census.

Across 1,520 sessions, one unscored or infrastructure-void session would give `BYTE_FAIL`, "instrument fault... by amendment". That forces a rule to be reinterpreted after the data.

**Fix:** define the expected rows from the plan, minus counted voids, and define "missing" for an unscored session.

### 8. BLOCKING: "the first run's label stands" is not carried
The preamble carries only sections that say "as PR x", and the amendment's §2.10 has no such blanket statement.
```
grep -n -E "first run|label stands|new output directory|fresh start|holds records|pushed|provisional|finalise|resume|Resume"
147: | `INCOMPLETE` | Same as `CONTROL_FAIL`. Resume rules as PR 5 |
```
Lost from PR 2.10:
- a script change after `R` is an amendment;
- a new output directory, with the first records kept in the report;
- the first label stands, and only the owner's logged decision replaces it;
- `R` is pushed before launch;
- the refusal of a fresh start in a directory that holds records.

This is the anti-reroll rule.

**Fix:** restate these in §2.10, or carry PR 2.10 wholesale except where changed.

### 9. BLOCKING: the rehearsal cannot run as written, and it can spend a frozen control stream
- **Guards.** Before the freeze there is no FREEZE file, and `run_guards` would refuse. So the rehearsed bytes are not the frozen bytes, or the frozen script has an off switch. The text says neither.
- **Root.** No root is named. A dev store containing ORACLE dev-015 s3 at root 20261108 runs the exact stream group B uses (`reference_rng(fit_case(...), root)`). That contradicts "has not been run on anything", and a bad result invites a new root.
- **Ceiling.** The rehearsal has 5 CPU hours, but the hunt alone is capped at 6, one B-sym@10F unit is 6,541 s, and control 1 needs two seeds.

**Fix:** use test roots only, run no control on frozen roots, list exactly what the rehearsal mode changes, and add a planted shift on the dev store.

## Non-blocking findings

**10. The "same truths in the same order" claim fails when `n` changes, and one generator couples all reads.**
```
read2 low with read1 n=40: 0.0024407  with read1 n=39: 0.0024337  shift -7.0e-06
MC spread of low end over 20 seeds: sd 0.00017
two-seed control 1 differs from min(d)>0 in 0 of 200000 draws
```
With two seeds, control 1 reduces to ORACLE beating B-lib on both seeds. 11 of the 25 pilot misses had Δ < 0, including ORACLE dev-015 s3, so a dropped truth is plausible. Use a child generator per read.

**11. Only the third clause is flagged as an extension.** These are also beyond the accepted text or the plan, and are not stated as extensions:
- Group E, which is close to a row PR 2.9 deleted.
- New roots for A, B and the hunt, although the plan said 2.2 and 2.9 carry unchanged. On PR 2.9's figures, H1 now stands with probability about 0.97 × (0.45 to 0.94), or 0.44 to 0.91. State this now, before anyone is tempted to explain away a failure.
- Dropping C5's second half.
- The rehearsal move, which F5 forced.
- S5, and the FREEZE file as a new artefact.
- A ceiling set before the row count is fixed, against the plan's "once P4's row count is fixed".
- "The control room's decision... sends them onward", against the accepted "the owner decides".

**12. "Substitution cannot touch" C5's rate is wrong.** The rate can move through which truths are kept, the route PR 2.5 itself names. `C5-gap` also has two bases (B-sym and AG-c-S) inside §7's 160 rows, but only one read.

**13. The fit guard omits a changed dependency.** `git diff --stat 900e66a a9e43a6 -- src/sciagent/core` shows `core/reductions.py | 86`, which `fit.py`, `fit_solve.py`, `likelihood.py` and `features.py` all use. PR's guard covered `src/sciagent/core`. The commit messages say results are bit-identical whenever `fsum` answers, so the risk is low. But "other `glm` files differ" understates it, and row byte checks cannot see drift from 900e66a. Pin this file too, and state the difference.

**14. §7's numbers are off.**
- C4 pairs about 20 truths, not 40 (share 0.5).
- The 1,080 omits `rescaled`.
- It also double-counts 40 rows if AG-S-boed is the EIG arm, as `plan.py:399` says.
- The caps' stated reason, the 78-hour stop, no longer binds, so "as PR 2.2" carries a stale justification.

**15. Stale limits come through by reference.** "As PR 9" carries "78-hour CPU stop", and PR 5 carries the 78-hour stop and "60 hours", while §8 says 172 and 120. The preamble says that text is unchanged.

**16. 178 is close to a true bound but is not one.** It excludes the rehearsal's 5 hours, which would make 183. It also excludes about 2 CPU minutes of polling, the 60 s hard margin per job, and a hand `--finalise`.

**17. Group E's selection needs gaps that cells do not store.** The tolerance cannot be computed from stored fields. The stored equivalent is `oracle_psi_source == "truth"`, which is a strict inequality. Group E's rate should also sit on the label line.

**18. The interface does not fit P4's tables.** `read_contrasts(table, ...)` cannot see controls 4 and 6, which are scripted runs outside `Table`. The map from control to read is unstated. The pilot's row key is (truth, seed, system), which carries no budget or model, so sweep arms would collide.

**19. Nothing requires P4's report to print `stored`.** A report with another seed could show an interval that contradicts the label.

**20. "Head of v2" is fragile.**
- Any non-docs commit during a multi-day campaign gives `BYTE_FAIL`, so run at a fixed commit.
- `run_guards` records HEAD as `R`, which will no longer be `R2`.
- The store must also have been filled on dm-devbox's machine class, and the text does not require that.

**21. Root 20261108 is the pilot test's `TEST_LOG_ROOT`** (test file line 80), so "Tests use 20261107" is incomplete. The R1 stream itself was not run, because the second seed word differs. The disjointness assert at line 90 would catch a straight copy.

**22. Small text errors.**
- Group D uses no dev split (`plan_stages`).
- "Same recipe" needs a path prefix, because `SPLIT_RELATIVE` is dev-specific.
- §10's limit on test-split records conflicts with PR 10's per-miss table.
- "Two commits" is really four: `F2`, `R2`, `S` and the FREEZE commit.

## Your questions, answered
| Question | Answer |
|---|---|
| Gameable after seeing data | Yes: findings 1, 2, 8 |
| Exclusions outcome-neutral | Not shown: findings 3, 4, 7 |
| Sample size justified | The census is fine; its counts and power text are not (6, 14) |
| Primary analysis answers the question | In shape yes, but the read list and survival rule need fixing (5, 6, 10) |
| Cost a true upper bound | Nearly; it needs a sum and a stated margin (16) |
| By-reference failures | Findings 2, 3, 4, 8, 14, 15 |
| Guards and ordering | They do not hold as written (1, 9, 20) |
| Within what was accepted | Core yes; extensions are unflagged (6, 11) |

## What held
- 25 miss rows on 25 fits, and max |Δ| `0x1.682163a1b8ab0p-6` = 0.02198.
- Group A 5 of 6, groups B and D caught.
- 91,468,211,483 µs = 25.41 CPU hours, and 12.83 wall hours.
- 1,520 + 740 = 2,260, and the 1,080 sum is correct arithmetically.
- 0.0379 per row, and 106 and 53 wall hours, all check.
- The six-file fit guard is clean at a9e43a6, and no contrast code exists there.
- `simulate.py` and `world.py` keep their defaults, and the observational log is never truncated.
- Pilot stage costs are 14.40 h tier 1, 7.04 h S2, 1.05 h tier 2 and 2.30 h hunt. My rough P4 projection is about 46 CPU hours, or about 97 if the LLM coordinate share doubles at baseline cost. So 172 is ample in expectation, and the pilot's 1.98 cores beat the assumed 1.62.
- The substitution direction, the label order and "no stop on outcome" carry correctly, apart from the findings above.
- No rule was loosened after the pilot results were seen, and they are disclosed.

## What I could not check
- **Group B's bit-for-bit R0 at a9e43a6: not run.** My setup command to extract the tree into `/tmp` was denied, and I did not retry by another route. The hex constants exist in `units.jsonl`, but whether they reproduce is unverified. An engineer should run this one unit before `F2`.
- **Determinism:** I reran no unit and compared no bytes.
- **`--finalise` then resume:** this comes from reading the code, not from running it.
- **`read_contrasts`, the P4 report and `R2`:** none exists, so everything about them is a review of intent.
- **Control 5's status and the -inf rate on test truths:** both unknown. The 93% uses the pilot's dev rate.
- **Test-split records and ctl\* streams:** none read or drawn. I read only dev-side pilot records.
- **Repository:** no file changed. Scratch files remain under `/tmp` (`rt-guard`, `rt-a9`, `rt_*`, `spec_rt.md`).

Files reviewed, all under `/home/ubuntu/lanes/sciagent/`:
- `docs/v2/prereg/2026-11-05_p4-search-misses.md`
- `docs/v2/prereg/2026-11-04_pilot-search-misses.md`
- `docs/v2/P4-MISS-PLAN.md`
- `docs/v2/INBOX.md`
- `docs/v2/LOG.md`
- `docs/SPEC.md`
- `docs/v2/P4-PROPOSAL.md`
- `scripts/pilot_search_misses.py`

Pilot records: `/home/ubuntu/research/records/sciagent/search-misses-3226f9b9521fe3a8c1d2445d1654cd1609b1eb2d/`

Reviewed: `docs/v2/prereg/2026-11-05_p4-search-misses.md` and `src/sciagent/campaign/contrasts.py` with the files below, at 9e61238. Saved verbatim from the red-team agent, lane session 36, 2026-11-06.

# Red-team review, round 3: P4 search-miss amendment at 9e61238

**Verdict: READY WITH FIXES.** There are 3 blocking and 6 non-blocking findings. All nine are text or test fixes that belong before `F2`.

No read came out wrong. The contrast code matches §2.7 on 4,900 reads against an independent implementation, apart from one rounding point at an exact tie (closing list, item 8). The post-review fixes hold. The blocking problems are in the text around the code:
- which ends unblind;
- a rehearsal that cannot do what it says;
- a control gate with no source.

Finding 2 changes a number, the rehearsal's truth count, and possibly §8's ceiling. So the lead should check that one against this list, and I see no need for a fourth full round.

**Pinned**
- Commit `9e61238b4951c5d43cc55ee09233efb388351e46`.
- Amendment sha256 `8c37b5a84dd90049…7187`, `contrasts.py` `305af81d9df2ff22…9be`, `test_contrasts.py` `3dc332e34bb9d7fc…934`.
- 84390bb, dd7ca91, 8e2bd69 and 05bf8f8 are all ancestors of 9e61238. Between dd7ca91 and 9e61238 only a comment changes under `src/`, `tests/` and `scripts/`.

**File key.** Line numbers are at 9e61238. Each path below is under `/home/ubuntu/lanes/sciagent/`:
- `prereg` is `docs/v2/prereg/2026-11-05_p4-search-misses.md`
- `PR` is `docs/v2/prereg/2026-11-04_pilot-search-misses.md`
- `contrasts` is `src/sciagent/campaign/contrasts.py`
- `tests` is `tests/campaign/test_contrasts.py`
- `psm` is `scripts/pilot_search_misses.py`

Scripts are in `/tmp/rt-s36/scripts/`. Each ran from the worktree `/tmp/rt-s36/wt` as `UV_PROJECT_ENVIRONMENT=/tmp/rt-s36/venv PYTHONDONTWRITEBYTECODE=1 nice -n 15 uv run --frozen python /tmp/rt-s36/scripts/<name>.py`.

## Round 2's blocking findings

| R2 | Status | Note |
|---|---|---|
| 1 No route for a code fix | **Partly** | prereg:164 states the stop point, both routes, the launcher refusal and `T2`. It still fails closed, but neither route passes the guard as worded without a new script (finding 4) |
| 2 Unchosen ends, anyone can `--end` | **Partly** | `bug`, `BYTE_FAIL` and `--end`'s authority hold (prereg:136-137, 193). The fix opened a gap on three other ends (finding 1) |
| 3 `--rehearsal` as an off switch | **Holds** | The positive allowlist (prereg:171) closes the route round 2 found. What is left needs a forged truth file (finding 8). The mode has a separate fault (finding 2) |
| 4 Floor domain and order | **Holds** | The domain is `contrast_rows`, controls read the unfloored table, failures split by cause, and seeds are matched. Text and code agree (differential below) |
| 5 Floor as a new primary rule | **Holds** | Extension 7 is accepted and disclosed with its numbers, and S6 exists in code. Not reopened: I found nothing broken in practice |
| 6 Control 5 dropped silently | **Holds** | Extension 8. The code has no control 5 gate |

Round 2's non-blocking findings 7 to 13 are all answered in the text. The one residue is the route wording in finding 4.

## Blocking findings

### 1. BLOCKING: reads are barred on three terminal ends that §5 needs a label for

**Where:** prereg:136, 137, 193-200, 207, 224, 226. PR:233 and PR:277. psm:245, 2161, 2188-2190.

The pilot script's ends are `complete`, `stop`, `cpu_stop`, `wall_limit`, `killed`, `terminated`, `bug` and `byte_fail`, and only `killed` and `terminated` can resume. The text then says four things that do not fit together:
- prereg:136 says reads are written "only on a `complete` end or an authorised `--end`".
- prereg:137 needs `--end` only for a run "that could still resume", so `cpu_stop`, `wall_limit` and `stop` do not need it.
- prereg:207 says `INCOMPLETE` "exists only after `--end` or the stop limits", and prereg:200 and 226 say a label follows a stop. Rule 3's "no read changed" needs the reads.
- PR:277, carried over, finalises on the driver's clean stop.

So at the 172-hour stop, §2.8 gives no reads and no label, while §5 gives a label. The likeliest place for that stop is tier 2, after tier 1 is done. If §2.8 wins, a label exists only if someone chooses `--end`, and that choice would be made with `controls.json` in hand.

`bug` has the same fault. PR:233 puts a `bug` before tier 1's end in `INCOMPLETE`, but rule 3's premise cannot be evaluated when no read is computed. §5 "alone sets the label" and is silent on it.

**Confirmed by** reading the text against psm. Nothing can be run, because the script does not exist.

**Fix:**
> This happens on a `complete`, `cpu_stop`, `wall_limit` or `stop` end, or an authorised `--end`.

Add to §5:
> `bug` gives no label: `decision.json` carries the end alone, rules 2 to 6 are not evaluated, and the run goes on only by `T2`.

Remove `bug` from rule 3's carry.

### 2. BLOCKING: the rehearsal cannot show a read flip, and as written it draws the reads' root on dev rows

**Where:** prereg:169, 56, 85, 171, 224. `src/sciagent/scoring/gonogo.py:113` and `:341`.

**Three truths cannot pass control 2.** The exact one-sided signed-rank test needs p < 0.05, and its smallest p on n truths is 2^-n. So on three truths `controls_pass` is False whatever the data, and every read is `nan`. "A planted shift ... to show a read flip" then shows nothing, and the label is `CONTROL_FAIL` through rule 5. `--rehearsal` cannot force a gate, because the mode "changes exactly these".

`rehearsal_n.py`, with planted-hint winning on every truth:
```
truths=3 kept=3 control2 p=0.1250 wins=3/3 passed=False controls_pass=False C1 reportable=False point=nan
truths=4 kept=4 control2 p=0.0625 wins=4/4 passed=False controls_pass=False C1 reportable=False point=nan
truths=5 kept=5 control2 p=0.0312 wins=5/5 passed=True  controls_pass=True  C1 reportable=True
```
Five truths need 5 wins of 5, and the pilot had 18 of 20. C4 also needs at least 2 kept out-of-dictionary truths.

**The reads' root is not replaced.** prereg:169 maps "the roots" to 20261107 and 20261108, which replace 20261111 and 20261112. prereg:85 and 112-113 give the literal 20261110, and nothing in the list replaces it. So either the rehearsal draws 20261110 and its stream 1000 on dev rows, or the mode changes something the list does not name. I read this from the text and did not run it, as instructed.

**The ceiling needs another look.** By P4-PROPOSAL's fit/4 figures (`docs/v2/P4-PROPOSAL.md:202`), six truths × two seeds × 2,303 s is 7.7 CPU hours, and one B-sym@10F unit at 6,541 s adds 1.8. That is 9.5 hours at full budget against a ceiling of 8. A reduced budget lowers the cost but may fail controls 2 or 3. I cannot tell without running it. prereg:171 also says "a fix means rehearsing again", and nothing says whether 8 covers repeats, so §8's 187 is true only for one rehearsal.

**Fix:**
- Require at least 6 kept dev truths, at least 2 of them out of dictionary.
- Say the rehearsal has shown nothing unless at least one read is reportable on both tables.
- Add a third test root for the reads to the list and to the disjointness assertion.
- Say whether 8 hours is a total, and re-derive 8 and 187.

### 3. BLOCKING: control 4's pass state has no source

**Where:** prereg:83, 85. contrasts:217-222, 417. `src/sciagent/scoring/controls_p4.py:253`.

`ScriptedControls.naming_null` is a bare bool that gates four of the seven reads. The text says only "a scripted run outside the table". At 9e61238 there is no run:
```
$ grep -rn 'ScriptedControls(\|naming_null(' src scripts tests | grep -v test_contrasts.py
src/sciagent/scoring/controls_p4.py:253:def naming_null(
tests/investigation/test_controls_p4_sessions.py:127: ...   (seven more lines, all this test file)
```
Nothing under `scripts/` calls `naming_null`, and the caller must pass the gaps itself (`controls_p4.py`, docstring). The text does not say:
- which (truth, seed) or how many;
- which record holds the result;
- how the script gets the value;
- what a missing result means.

SPEC §6.4 and CLAUDE.md forbid a comparison without its control, so a default of True is a C2 read with no control behind it. The contrast review listed this mapping as unchecked. This round finds it is not defined.

**Fix, one sentence in §2.7:** name the run and its record, read the value from that record inside the script with no option to set it, and make a missing or unreadable record False. List the runner as an open dependency.

## Non-blocking findings

### 4. Neither route after a mid-campaign fix passes the guard as worded (prereg:160, 162, 164)

"A new store at a new `T`" makes a second commit on the FREEZE file, or leaves `origin/v2`'s chain. "The guard accepts exactly that `reuse` line" cannot be true of frozen bytes. `guard_toy.sh`, on a toy repo:
```
at T:                    commits on FREEZE file: 1 ; it is HEAD: yes ; ... on origin/v2 first parent chain: yes
route 1 on v2:           commits on FREEZE file: 2 ; it is HEAD: no ...
route 1 by a side branch: commits on FREEZE file: 1 ; ... on origin/v2 first parent chain: no
```
This fails closed, and a new frozen version can carry a new guard, so nothing is gameable or stranded. **Fix:** say that either route needs a new frozen script whose guard names the new commits.

### 5. P4's report is a requirement nobody is assigned (prereg:138, 156)

- `grep -c reads.json scripts/pilot_report.py` gives 0.
- That script is what P4's report is being built on: it already takes `--arm-set p4-sonnet` and holds the softplus table.
- It runs on any `--root`, calls `analysis.verdict` with a bootstrap (`scripts/pilot_report.py:880`) and prints per-arm gap means (`:1112`).
- §2.10's test list does not mention it, and no queue item says so.

So nothing yet enforces the fix for round 1's finding 2. **Fix:** name the file, add the refusal to the §6.1 work and the test list, and make `pilot_report.py` itself refuse a store with non-dev truths and no `reads.json`, so it does not remain an open route beside a new report.

### 6. The dependency list says closed, but other inputs are open (prereg:12, 46, 48, 164)

- `--reuse-from` on the P4 store is not refused: `scripts/pilot.py:337` and `:426` pass it straight through. This fails closed.
- "The frozen plan" does not exist. `scripts/pilot.py` has no model, letter or budget option (`build_parser`, line 302), and `_p4_shaped` in tests is a stand-in.
- `contrast_rows(plan, truths)` and "missing" are functions of that plan, and only rehearsal mode says where a plan comes from.

**Fix:** list these as open, and say where the frozen plan lives and that it is not an operator's input.

`.claude/worktrees/p4-plan` holds uncommitted edits to `contrasts.py`, `plan.py`, `execute.py` and `versions.py`, and a new `p4plan.py`. I read only its `contrasts.py` diff. Whatever lands needs its own check against §2.7.

### 7. One agent-conditioned void is absent while its near twin is floored (prereg:48, 93)

- A `hard_timeout` ("a tool call kept running past the wall time", `src/sciagent/campaign/llm.py:529-538`) is stored void, so `load_rows` drops it (`src/sciagent/campaign/analysis.py:359`).
- A `wall_time` end with no submission is floored. All 3 of the pilot's were AG-o-S/named (`docs/v2/PILOT.md:78`, `:104`).
- So the same event, running out of time, counts against the system or vanishes depending on whether a tool thread overran by 600 s.
- The exposure falls on C3-free and the AG-o C2 reads. `docs/v2/LOG.md:1511` flagged this as "known and not addressed", and the floor's acceptance did not cover it. The pilot lists no such void.

**Fix:** print `hard_timeout` voids per arm beside S6, or state the choice as deliberate.

### 8. The rehearsal's truths are not tied to `--split` (prereg:169, 171)

Truths come from `--rehearsal-plan`, while `--split` is only hashed. So "every truth's `split` must be `dev`" reads a file nothing authenticates. This needs deliberate forgery, as round 2's finding 8 did. **Fix:** one clause, requiring each truth to be a record of `--split`.

### 9. Two rules are still unpinned by tests (tests)

47 mutants of `contrasts.py` on a `/tmp` copy gave 44 killed, 3 survived. No mutant touched a guard (`mut.py`).
```
SURVIVED floor excludes B-sym@10F           the text's domain includes it; no test has it lowest
SURVIVED read_floors no_common from without  S6's count is unpinned
SURVIVED setters unsorted                    harmless: rows are sorted afterwards
```
The contrast review's eight mutants all die now: seven in `mut2.py`, and the uniqueness mutant in `mut.py`. **Fix:** two short tests.

## The brief's questions

| Question | Answer |
|---|---|
| Can the rules be gamed after seeing data? | Not by any new route I found. Finding 1 leaves one choice open at the stop limits, and finding 3 leaves one input open. The embargo stays procedural, as prereg:140 says |
| Are the exclusions outcome-neutral? | In the rule, yes, with control 1's drops as preregistered. Exception: finding 7. Accepted items stand |
| Is the sample size justified? | Yes for the census. No for the rehearsal (finding 2) |
| Does the primary analysis answer the question? | Yes, and the code does what §2.7 says |
| Is the ceiling a true upper bound? | 178 is enforced. 187 holds only for one rehearsal (finding 2). The contrast functions add about 1 CPU second at the terminal step |
| Can anything be tuned after the fact? | Not found, once findings 1 and 3 are closed |

## What held

- **Differential** (`diff36.py`): a separate exact-fraction implementation with its own controls 1 to 3, over 700 random toy tables and 4,900 reads, 1,644 of them reportable. It gave 0 mismatches on point, interval, `reportable`, `n`, `rows` (floor setters included), S6, `no_common_seed`, kept, dropped, unchecked, `controls_pass` and `why_not`. The first run, with exact ties allowed, gave 721 mismatches, and removing those ties gave 0 (closing list, item 8).
- **Interface:** every signature and field in prereg:75-80 exists as written. Streams, gates and the fixed order match.
- **Determinism** (`det.py`): four processes at hash seeds 0, 1, 12345 and 7, the last with shuffled rows, on a P4-shaped toy at 10,000 resamples, gave one hash, `4beea26d5d14ba38`.
- **Group B, R0 only, at 9e61238** (`groupb_r0.py`): all five fields and the kept fit are bit-identical (`-0x1.4051a13cc7023p-1`, and so on), `VERDICT REPRODUCED`, 8.5 s CPU.
- **Fit guard:** the `--full-index` diff is the one line at `features.py:417`, with sha256 `5ab0f61d…`. `reductions.py` differs by 86 lines, and `FIT_VERSION` is fit/5.
- **Splits and frozen files:** the dev split recomputes to `31c18f10…a577`, and psm is byte-identical to `R`. `grep 2026111[012]` finds nothing in `src`, `scripts` or `tests`.
- **Pilot records:** 91,468,211,483 µs is 25.41 hours, M is 25 rows on 25 fits, and max |Δ| is `0x1.682163a1b8ab0p-6`.
- **Arithmetic:** 0.0379; 86, 74 and 38; 172 + 6 + 8 + under 1; 1,320 + 620; 0.44 to 0.91; 1,000 rows, on the stand-in plan.
- **Checks:** the 98 contrast tests exit 0, `tests/test_invariants.py` passes, ruff is clean, and mypy reports no issues in 316 files. LEDGER rows 41 and 43 record the fast and slow tiers at dd7ca91. I did not rerun those.

## Closing wording list

1. prereg:5, "no contrast has been computed on anything": round 2 computed contrast-like reads on pilot dev rows (extension 7). Say P4 data.
2. prereg:83, "`read_floors` computes every read again": it computes the left-out read and the counts.
3. prereg:85, "Two calls resample identically": the indices match, and at equal `n` with a different kept set they map to other truths. Contrast review item 16, not yet taken.
4. prereg:207: `INCOMPLETE` also arises on a `complete` end with a `reference_failed` tier 1 fit.
5. prereg:56, "the test split's truth id": group B's is dev-015.
6. Extension list: C3-free and C4 reading the named condition is the lead's choice (LOG 2026-11-06). Add it as an extension.
7. Extension 7's second pair is the rejected all-rows floor, and the LOG of 2026-11-05 says "the contrast's rows". Mark both.
8. Control 1 at an exact tie: "does not beat" should drop the truth, but on two seeds `margin > se` passes by rounding in 17 of 39 cases of d = (0, k/16) (`tie.py`). It needs B-lib bit-equal to ORACLE on a seed, so I expect it to be out of reach here. The pilot's smallest ratio was 3.47.
9. §2.10's tests should add that a second `--finalise` cannot rewrite `reads.json` or the label, and say which file lands first.
10. A read that loses a seed to an absent row prints nothing per read, because only whole truths are counted.

## What I could not check

- `scripts/p4_search_misses.py` does not exist. So every end, the label logic, `--end`, `--rehearsal`, `reads.json` and the terminal assertion are reviews of intent.
- The real P4 plan, and `load_rows` on P4 cells. I read the failed-fit path through `execute.py:669`, `:193`, `driver.py:420` and `analysis.py:274`, and did not run it on a real failing fit.
- Whether controls 2 and 3 can pass on dev truths at a reduced budget.
- Extension 7's figures are round 2's. I did not recompute them, because the pilot store does not read at this head.
- `hard_timeout` rates at P4, R1 on any stream, and the odds of H1.
- The slow tier and the full fast tier. CPU was not kernel-counted, and I estimate it at well under 1 hour.

**Limits kept and things to know**
- No test split was touched, and streams 20261110 to 20261112 were not drawn. Toy roots only. The dev split was used only for its hash and group B's R0.
- No guard was bypassed. The R0 script is a copy of session 34's with `DEFAULT_SPLIT` pointed at the main checkout.
- One block occurred: the harness refused a foreground `sleep` used to wait on the mutant run. I did not retry it and read the log with plain commands instead. No guard on the work was involved.
- No repository file changed: `git status --short` in the worktree gives 0. The detached worktree remains at `/tmp/rt-s36/wt` and is listed in the repository's worktree list.
- `v2` moved during this review and `origin/v2` now holds c1ba9f6, a child of 9e61238 changing only `JOURNAL.md`, `QUEUE.md` and a new `SPEC-TEXT-P4.md`, none of which I reviewed. So "not pushed yet" no longer holds. Nothing here depends on it.

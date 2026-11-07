Reviewed: `docs/v2/prereg/2026-11-06_bsym-tuning.md`, third text, sha256 `d2c0842f…`; `scripts/bsym_tune.py` and its test at c1c4e65; `docs/v2/SPEC-TEXT-P4-C.md`. Saved verbatim from the red-team agent, lane session 37, 2026-11-07.

# Red team round 3: B-sym tuning pre-registration, and R2's share

Reviewed at v2 head: text sha256 `d2c0842f…`, the script and test file at c1c4e65, and `SPEC-TEXT-P4-C.md`. Head moved to 7f36dd7 during the review. That was a checkpoint commit and none of these files changed.

**One command was blocked, so two checks did not run (see "What I could not check").**

## Job 1 verdict: READY WITH FIXES

There are 2 blocking findings, both text-only, and 10 non-blocking. The script already does what the fixed text would say. After pasting, only the pasted lines need a re-read, not a fourth round.

### BLOCKING

**1. Sections 5 and 6 contradict each other after `final.json` (round 2's finding 7, reopened by the margin split).**
- Section 5 says "`final.json` names the setting that stands, and nothing after it changes that".
- Section 6 says any bug found after an outcome is read means "`c0` stands".
- A bug in the margin step meets both sentences, so a person must choose between them with statistics in hand.
- The `c0` branch also needs no evidence. Only the amendment route asks for a failing test, so calling something a bug is enough to remove a winner.
- The code follows section 5: `final_setting` ignores margin ends, and `check_start` refuses the resume. The text should give way.
- This answers the engineer's question: yes, state both.

Paste:
- §5: "`final.json` names the setting that stands, and only section 6's bug rule changes that."
- §6: open the bold rule with "A fault in stage 1 or in the passes, found after any outcome is read and shown by a failing test, stops every further step…". Then add: "A claimed bug with no failing test changes nothing. A fault in the margin step alone stops that step: no `margin.json` is written, a resume is refused, `final.json` stands, and the note says so."
- §2, "Where it lands": replace "Both records" with "`selection.json` and `final.json`", and add "`margin.json` goes in the same directory, and no test reads it."

**2. "Nobody decides anything after the statistics exist" is not yet true of stage 2.**
- Stage 2 is launched by hand after `selection.json` is readable.
- Declining to launch, not resuming, writing STOP, or making the §6.1 commit first each ends in `c0`.
- The new "Procedural" paragraph states that consequence but no duty to avoid it.
- §9's read ban ends at `selection.json`, so `stage2/` rows can be read while a pass runs, with STOP to hand.
- This can only ever move the result towards `c0`, so no winner can be kept this way. It is still an opt-out taken with the winner known, against section 1's aim. Round 2's finding 1 named this path.

Paste:
- End of "Procedural": "The lane launches stage 2 on any `selection.json` naming a winner other than `c0`, resumes it after every kill, and makes the §6.1 commit only once `final.json` exists or stage 2 has ended in a bug. Doing otherwise is a breach, which the note reports. `c0` still stands, so a breach cannot keep a winner."
- §9: "no session reads a store before that stage's record exists: `selection.json` for stage 1, `final.json` for the passes, `margin.json` for the margin units. A `STOP` file is written only for a machine fault, named in the journal first. Both parts are procedural."

### Non-blocking

**3. A STOP latches only once `run_end` is journalled.** This is from reading the code, because the run was blocked.
- If a SIGTERM arrives during `_drain`, the run ends `terminated`, and `_stop_reason` re-reads the file on resume, so removing STOP would let the pass carry on. A drain can last 14 hours in stage 2.
- `test_a_stop_cannot_be_undone_by_removing_the_file` only covers a `run_end stop` already written.
- Fix: journal the stop at first sight, and have `journal_end` latch on that record, test first.

**4. A stage 1 STOP can keep a partial result.** At 54 to 59 complete worlds the label is `SELECTED`, while stage 2's unfinished pass fails. Exploiting this needs a breach of §9.
- Paste: "A stage 1 run ended by a `STOP` file with fewer than 60 complete worlds is `INCOMPLETE`." A machine need can use a kill and resume instead. This needs one code line and a test.

**5. "One run of each" holds per checkout only.** `STORE_DIR` sits under `pilot.ROOT`, and `check_same_commit` compares a commit, not a path. A second worktree at the same commit starts with a fresh latch, CPU count and STOP state.
- Paste: "The LOG names the worktree's path with the commit, and no other checkout runs the launch." Better still, have `launch_guards` check that path against the LOG.

**6. `margin.json` holds a mean built from worlds never run, and "a CPU limit" is wrong.** Using `c0` against itself:
```
all 60        mean 0.0        floored 4   finished True
30 of 60 run  mean -0.00839   floored 33  finished False
nothing run   mean -0.01438   floored 62  finished False
one unit killed by RLIMIT     mean 0.0  floored 5  finished True
```
- Fix: write the mean as null unless finished.
- §2: "with `finished` false and no mean after a stop or the CPU stop". The killed-unit line shows a per-unit limit leaves `finished` true.
- A null mean also stops a STOP from keeping a favourable partial margin.

**7. Control 3 does not gate on the mid truths.** With `c0`'s six stored 10×F gaps and all four mid units at `cpu_limit`, `read_pass` gives median -0.00233 and passes. A fifth `-inf` makes the median `-inf` and fails. "If a winner fails on one" also misdescribes a median.
- Paste, after "So control 3 reads mid truths, which P3's did not": "It is a median of ten, so four of the ten can fail and it still passes."

**8. "Detached worktree at the script's commit" cannot launch.** `launch_guards` needs that commit named in `LOG.md`, and a commit cannot contain its own hash. It fails closed, before anything runs.
- Paste: "at the commit whose LOG names the script's commit".

**9. `selection.json`'s `stands` field names the winner before stage 2 has run.** That contradicts section 5 for any later reader. Keep the winner in `winner` and write `c0` in `stands`, or rename the field.

**10. Small wording.**
- Script lines 32 to 34 ("Commit them after stage 2") contradict "commits it… from the main checkout".
- §6's "refuses to start once `final.json` exists" should say "the passes refuse", because the same command resumes the margin step.
- Add to §6: "The margin step resumes by the same command and refuses once `margin.json` exists."
- §5's constant paths are relative to the run's worktree, so say so.

**11. `MemoryMax=5G` and `CPUQuota=200%` appear only in the script.** An out-of-memory kill can become a floor or a `-inf`, so §8 should name them. Stored peaks are 2.05 to 2.07 GiB here (the text cites 2.18), so two workers fit with little to spare.

**12. §8's 83.8 needs an assumption about `weak` that the text doesn't state.** The stated inputs give 75.8 + (1.1 + 1.2) × 5.42 = 88.3, which makes the chain 88.3, 94.3, 99.7. All are inside 110, so nothing turns on it.

### Round 2's findings

| # | Status | Evidence |
|---|---|---|
| 1 | Answered in text and code | What remains is finding 2 above |
| 2 | Answered | P3 `c0` through `read_pass`: 18-0-2, p 4.77e-06, CI [0.0148, 0.0306], control 3 median 0.0. All 20 truths kept. Strata list correct. Stream is 12 + p |
| 3 | Answered | Order and limits match. 110+4, 120+28, 114+148=262 |
| 4 | Answered | Store, seed and call match `_run_system`; slow test passes |
| 5 | Answered | Means 0.00963, 0.01066, 0.00765; three seeds 0.00931 [0.00472, 0.01433] on my private stream. Four non-finite reads, as round 2 found |
| 6 | Answered | Seed, landing words, `check_plan` test |
| 7 | Answered for stage 1 and the passes | Reopened by the margin split: finding 1 above |
| 8 to 10, 12, 13 | Answered | `NO_PROPOSAL` is matched with `in` |
| 11 | Answered except `margin.json` | Finding 1 above |

### Script against text: where they matched

- **Checks that matched:** rule order, floors, H1, streams, limits, `c0b`'s store, the pass rule and the fallback.
- **Pins:** split `31c18f10…` and `truths.json` `604acec5…` match. My export from a store copy reproduces the table's pin `1766167…` byte for byte.
- **P3 rows:** budgets are 40 and 400. ORACLE flags are False on 60 of 60.
- **Stops:** a stop after 1, 2 or 53 worlds gives `INCOMPLETE`, and 54 gives `SELECTED`.
- **Positive control:** H1 gates stage 1's comparison, and stage 2's read passes on P3's own `c0` rows, except on the mid truths, which have no control (the text says so).
- **Tests:** at c1c4e65, 301 fast and 9 slow passed, exit 0 for both.
- **Undrawn:** no `.cache/drafts/bsym*` directory exists. Seeds 4 to 6 and the frozen roots stayed undrawn; my scripts used root 7777.

## Job 2: keep the number, replace its reasons, add C4's own limit

**Figures that hold** (`/tmp/rt3-bsym/scripts/r2.py`, `table.py`, `store_check.py`):
- ORACLE minus B-lib is positive on 60 of 60 worlds, smallest 0.01696.
- All three seed pairs keep 20 truths, run through `oracle_beats_lib`.
- The one-in-four chance is exact at two seeds (simulated 0.2511), for independent differences symmetric about zero.
- 0.586, 0.178 and 0.028 are correct, but only as Bin(40, 0.075).

**Reasons that do not hold:**
- **Rule of three.** The worlds are not independent: variance between truths is 0.974 of the total. So 60 worlds are about 20 observations, and 0 of 20 bounds a truth at 13.9%, not 10%.
- **"Says the test population differs from dev".** 5 of 40 against 0 of 20 gives Fisher p = 0.12, and it takes 7 of 40 to reach 0.048. At the cited 9.75% rate, the limit fires only 35% of the time.
- **"Would fire on one or two noisy truths".** More than 2 needs three truths. A seed-noise model on dev expects 0.06 of 40 (worst truth dev-002, 1.6%), and at a 1% leave rate more than 2 fires 0.7% of the time. Noise is not the argument against a stricter limit.
- **"If 10% of truths truly had no difference".** Read as exactly 4 of 40, more than 4 has chance 0 and more than 2 has 0.74. It needs "each truth independently".
- **Denominator.** C4 reads only the 20 out-of-dictionary truths, so 4 of 40 could be 20% of C4 with no line printed.

**Why 4 of 40 still stands.** It works as a limit on scope, not as a test. Control 1 reads ORACLE and B-lib, and neither is a side of C1 to C4. A drop narrows what a read speaks for but cannot directly favour an arm. A false alarm would cost 1,320 sessions their confirmatory status, while the kept truths still give a fair paired read. If the control room wants a detector instead, more than 2 of 40 is the number: 0.59 at a 10% share, 0.007 at a 1% leave rate.

**Paste over the control 1 addition:**

> On two seeds control 1 passes a truth exactly when ORACLE beats B-lib on both, so a truth with no real difference passes one time in four if its two differences are independent and symmetric about zero, and a real one can fail on one noisy seed. If more than 10% of the test truths leave the reads, as dropped or unchecked, that is more than 4 of 40, the reads are reported as not confirmatory. If more than 2 of the 20 out-of-dictionary truths leave, C4 is so reported, whatever the total.

**Paste over "Why 10%":**

> Why this number, set before any P4 data. It is a limit on scope and not a test: how much of the split may leave before a read stops speaking for it is a judgment. Control 1 reads ORACLE and B-lib, and neither is a side of C1 to C4, so a drop cannot directly favour an arm, though it can lean the reads away from truths near the library: on dev the five narrowest margins are all in-dictionary, near or mid. On P3's dev rows ORACLE minus B-lib is positive on all 60 worlds, smallest 0.017, and no truth drops on any pair of seeds. Those worlds are about 20 observations, so dev bounds a truth's chance of leaving only at about 14%. As a detector the limit is weak: if each truth independently has no real difference with chance 10%, it fires with chance 0.18, at 20% 0.74, at 30% 0.96. More than 2 of 40 would detect more (0.59 at 10%). We do not take it, because three truths leaving does not stop a read speaking for the split, and the count is printed either way. The test split is not sampled and nothing is known of it. The reads are still printed, and the label of the miss check does not change.

## What I could not check

- **Blocked command.** The research-team guard refused one command: "Inline code that deletes or moves files… and mentions results". It held two things, and neither ran:
  - the synthetic STOP-undo demonstration, so finding 3 is read from code and not shown by a run;
  - a byte rerun of real P3 units through `UnitJob`. I did not resubmit either, in whole or in part. So ORACLE, B-rand and planted-hint bytes at current code were not compared with the store. Stage 2's use of stored rows still rests on round 2's two B-sym units and the slow test's repeatability.
- **Not run.** No real `systemd-run` or cgroup launch, and no candidate on any world. Candidate costs, `-inf` rates, mid-truth cost at 10×F and candidate memory all remain unknown.
- **Freeze path.** The constants are empty, so the frozen path has only run in tests. The branch still holds an older copy of the text, so `PREREG_SHA256` must be set from the pasted text after a merge.
- **R2's dev figures.** They use 20 truths under a normal seed-noise model, and I don't know the test split's stratum shares. The margins by half and stratum may not carry over.

Spend was about 0.1 CPU hour, $0, no LLM calls. My scratch files are in `/tmp/rt3-bsym/`, and the detached worktree `/tmp/rt3-bsym/wt` is still registered in the repo. I left it because I may not delete there, so `git worktree prune` will be needed once it is gone.

Files reviewed:
- `/home/ubuntu/lanes/sciagent/docs/v2/prereg/2026-11-06_bsym-tuning.md`
- `/home/ubuntu/lanes/sciagent/docs/v2/SPEC-TEXT-P4-C.md`
- `/tmp/rt3-bsym/wt/scripts/bsym_tune.py`
- `/tmp/rt3-bsym/wt/tests/campaign/test_bsym_tune.py`

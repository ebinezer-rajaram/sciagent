Reviewed: `docs/v2/prereg/2026-11-06_bsym-tuning.md` from a71a182. Saved verbatim from the red-team agent, lane session 36, 2026-11-06.

# Red team: B-sym tuning pre-registration (draft)

**Verdict: NOT READY.** There are 6 blocking and 3 non-blocking findings. Nothing has run, so nothing is spoiled and every fix is free of bias today. Findings 1, 3, 4 and 5 change the candidates, the controls, add a stage and change the rules, so the fixed text needs one narrow second round.

**Reviewed:** `docs/v2/prereg/2026-11-06_bsym-tuning.md`, sha256 `7056fb05…`, from a71a182. The hash was unchanged at the end of the review.
**Split:** `truths.json` sha256 `604acec5…`, matching PILOT.md.
**Code run:** e5e205f.

HEAD moved to 4502eb1 during the review (12 commits). `search.py`, `plan.py`, `execute.py` and `contrasts.py` did not change, and `p4plan.py` changed only in its docstring. The SPEC rulings add B-sym's tuned setting to §6.1 and number §6.4. Neither changes a finding.

## Blocking

### 1. The candidate list does not reach where B-sym fails

This is the purpose of the exercise, and it cannot be fixed after the freeze.

```
p3b.py, p3c.py on .cache/pilot-merged (read-only):
truth softplus, submitted softplus: B-sym 6/33, B-rand 19/33, planted-hint 30/33
B-sym minus B-rand, by truth, floored: mean 0.0023, CI -0.0124 to 0.0156, 14/20
Product+Gate nodes per structure: B-sym submissions 0.37, truths 1.35

propose.py (stub fitter, BIC from a hash, reads no world, 300 runs):
library seeds: identity null, identity Hawkes, exp Periodic (3; none softplus)
name     nonid/40  softplus/40  depth3/40
c0         10.8      6.1         1.14
c1..c6   9.0-12.3   4.2-8.3    0.74-1.60
c7         16.4      9.8         0.88
CHANGE_LINK 6.0 (not listed)   19.0  11.5  0.71
offspring 2 alone (not listed) 12.8   6.9  2.30

real.py, c0 with a shifted search seed, softplus fits of 40 on softplus truths: 5, 1, 17, 0
```

- At default, the GP has not been shown to beat a proposer that reads no fit result.
- With 3 seeds, 37 fits remain, which is about 4.6 generations at offspring 8. `offspring` is only ever varied together with `population`.
- `c1` to `c6` barely move what is proposed. Nothing raises the growth operators, though 10 of 20 truths are depth 3.
- Leverage: P3's mean gaps are -0.0528 (B-sym) and -0.0526 (AG-c-S/named). This is sizing, not a comparison, since PILOT.md says no control covers it. A small shift in B-sym could move C1's sign.
- The agents' one tuning round aimed at link choice, and took correct links on softplus truths from 3/11 to 8/11 (STATUS-2026-10-05). `c7` is the only analogue here.
- Caution: the within-truth gain from a right link is thin, +0.010 on 4 truths. I am not claiming these additions would win, only that they are the diagnosed failures.

**Fix:**
- Add `CHANGE_LINK` 6.0, `offspring` 2 alone, and doubled growth weights (`ADD_FEATURE`, `MULTIPLY`, `WRAP_GATE`). Each adds about 3.6 CPU hours at P3's mean.
- Decide in writing whether B-sym gets a softplus seed. It lies outside `GPConfig` and moves `sym_seeds`. The fairness argument in `search.py`, "the agent also knows the library", applies equally to links.
- Say that one-at-a-time changes cannot combine two gains.

### 2. "At least the agents' effort" is false on the draft's own counting

| Agent sessions on dev | Count | Source |
|---|---|---|
| Windows P3 | 240 | `git show 183da42:docs/v2/PILOT.md` lines 23-26; `TRANSCRIPTS.md:5` |
| Δ6 comparison | 40 | STATUS-2026-10-05 |
| Linux smoke | 4 | `LEDGER.csv:4`; `.cache/smoke/timing.jsonl` |
| Linux P3 | 240 | PILOT.md |
| **Total** | **524** | the draft says 280 |

- B-sym gets 360 new units, or 420 with P3's 60. Counting every default B-sym unit on dev the same way (133) gives 493, which is still short.
- The Windows 240 are not "from before this record was kept". They are the evidence Δ6 was built on (P4-PROPOSAL's Δ6 row).
- P4.0 would add 60 more (`P4-PROPOSAL.md:174`). It is absent from the queue's list of what `S` needs, so I could not tell whether it still runs.
- Counting only runs used to choose a prompt gives 280, and 360 clears that. But that basis must then drop the Linux 240, so the row has to be rewritten either way.
- The candidate count (8 against 3) stands.

**Fix:** State one basis and correct the row. Three seeds clears 524 on new units alone: 540 units, about 49 CPU hours at P3's mean.

### 3. `weak` can fail only through a bug, and no control works at the scale of the decision

```
propose.py: weak fits mean 29.3, min 26; softplus 0; features outside the library 0
            with crossover 0 as well: 4 fits
real.py, weak, 7 worlds of seeds 1-3 in sha256 order:
  fits 30,21,25,27,20,24,18 (all 60 P3 c0 units used 40); mean CPU 52 s
  c0 minus weak mean +0.0429, 6 of 7 positive; weak minus B-lib mean +0.0054, tied on 2
p3b.py: B-sym minus B-lib mean 0.0662, CI 0.0496 to 0.0836, 20 of 20 truths positive
```

- `weak` is not at the agents' budget, because it runs out of proposals. Its only source of novelty is crossover, which it keeps at 0.2.
- H1's expected effect is about ten times the standard error of a candidate difference (0.005, finding 7). Passing shows the pipeline runs. It does not show the instrument can separate `c0` to `c7`, which is what SPEC §6.4 and CLAUDE.md ask for.
- If H1 fails, the likely cause is a script bug, yet rule 1 reads that as an instrument result.

**Fix:** Keep `weak` as a labelled smoke test whose failure means a bug stop, and add two controls.
- **A graded positive control, free from `c0`'s stored curve.** `c0` at 40 fits minus 30 fits passes on P3 for every 2-seed pair (means 0.0077 to 0.0107, all intervals above zero). At 20 fits the means are 0.019 to 0.023. It is nested, so it is lenient.
- **A null: `c0b`, which is `c0` with a shifted search seed.** That is 40 units, about 3.6 CPU hours, never selectable, with its rank reported. It measures the noise floor directly.

### 4. The winner reaches the test split unchecked where it gates everything

- Sharing the setting with B-sym@10F and planted-hint is right, because control 3's inference needs the same GP. The fault is that nothing checks the winner before the freeze.
- `contrasts.py`: "Controls 1, 2 and 3 gate every read". A setting tuned at F = 40 that misses -0.01 at 10×F voids C1 to C4 after 1,320 sessions.
- P4-PROPOSAL:140 names dev tuning as the cure for that failure, and it cannot be reused once test data exist.
- P3's control 3 covered 10 truths at seed 1, with no mid truth.
- Control 2 is low risk (18 of 20, CI 0.0148 to 0.0306), but it has no evidence under a new setting either.

**Fix:** Add a stage before `S`, used only if the winner is not `c0`, with its rule fixed now. On failure, fall to the next-ranked setting or to `c0`.

| Unit under the winner | Units | CPU hours |
|---|---|---|
| B-sym, seeds 1 to 3 | 60 | 5.4 |
| planted-hint, seeds 1 to 3 | 60 | 10.5 |
| B-sym@10F, dev-000 to dev-009, seed 1, plus mid truths | 10+ | 27.8 for the first 10 |

- Read these against the stored B-rand and ORACLE rows. A `c0` rerun at HEAD matched the store on `per_event`, structure, curve and every reading on dev-007 s3 (one unit).
- This also gives an honest margin on worlds that selection never used.
- The ban on seeds 1 to 3 is about reading them, which no longer applies once the winner is fixed by rule.

### 5. Three routes to re-decide after seeing the data

- **Rules 1 and 2 end in "the control room decides", after every statistic exists.** One failed world trips rule 2, even a simulation failure common to all nine settings. A stop can then keep a favourable partial result, which the 2026-11-04 prereg barred in terms ("can neither keep a favourable partial result nor bury a flip").
- **§6: "A bug in the script is fixed by amendment, and the store restarts."** There is no time limit, including after the result is read.
- **§11 refuses seeds 1 to 3 only.** Seeds 6 and 7 stay open for a rerun, with no one-run latch, and only the printing of progress lines enforces "nothing is read".

**Fix:**
- Fix the fallback now: any label but `SELECTED` means `c0` stands. Alternatively, write a complete-case rule with its threshold.
- Add a resume rule for a reboot or an out-of-memory kill.
- Allow exactly the planned seeds, and refuse to start if the store has cells or `selection.json` exists.
- A bug found after any outcome is read keeps the old result beside the new one.

### 6. 100 CPU hours is not an upper bound

```
timing.py on .cache/pilot-merged/timing.jsonl:
B-sym@10F slowest 21,442 s = 5.96 h (PILOT.md's timing table agrees); draft says 3.2 h
draft's bound with the true figure: 90 + 2 x 5.96 = 101.9, over the ceiling
B-rand, same 40 fits: max 12,971 s = 3.6 h
B-sym: mean 325 s, max 1,537 s; 360 units = 32.5 CPU h
```

- The 3.2 h is the Windows fit/4 figure (`P4-PROPOSAL.md:116`).
- The premise is not a bound either, since a 40-fit B-rand unit exceeded it.
- Nothing limits a unit, and "the stop is enforced" names no mechanism. The pilot miss prereg used a kernel count and a 3 CPU-hour job limit (its line 277).
- A restart may reset the count.
- The arithmetic itself checks: 41.4, 60.6, 37.7, 55.6, 96.4.
- The guide's inputs are stale, in the safe direction. 414 s is Windows wall time at 5 to 6 workers, and 1,219, 195 and 405 s have no committed source.

**Fix:**
- Copy that mechanism, with a per-unit limit L such that 90 + 2L is at most 100. L = 2 h is 4.7 times B-sym's slowest unit and gives 94.
- State that a killed unit takes the floor, and that the ceiling spans restarts.
- With findings 1 to 4 taken, 13 settings (the 8 listed, 3 more, `weak`, `c0b`) cost about 47 CPU hours at 2 seeds or 70 at 3, plus about 44 for the second stage. That is 91 to 114, so re-set the ceiling per stage.

## Non-blocking

### 7. §7 gives no number, and its "set by the effort table" rests on finding 2

Paired world differences for `c0` under a shifted seed have rms 0.0335 (6 worlds). P3's upper bound is 0.039. So a candidate difference has a standard error of about 0.005 to 0.006 on 40 worlds. Assuming independent candidates (`final.py`):

| True lead of the best candidate | Picked, 2 seeds | Picked, 3 seeds |
|---|---|---|
| 0 (all equal) | `c0` kept 12.5%; chance lead over `c0` about 0.005 to 0.006 | same; lead about 0.004 to 0.005 |
| 0.005 | 43-50% | 52-60% |
| 0.01 | 79-87% | 89-95% |
| 0.02 | over 99% | 100% |

- I would keep the no-threshold rule. But "costs little" holds only once finding 4 exists.
- "20 truths are all that dev has" is a choice: the split cost 2,466 CPU s to sample. Whether a larger draw is still "dev only" is the control room's call.

### 8. The fair-rival claim outruns the evidence

- Selecting on seeds 4 and 5 is sound for C1's validity: the setting is fixed before fresh test data.
- It says nothing about strength on test. These are new seeds of the same 20 truths, and P4 has no default row.
- Dev holds softplus 11/20 and exp 2/20 against a prior of 0.25 each, and I cannot compare it with a test split that does not exist.
- Word §1 as "best of a fixed list by a pre-registered dev rule". Optionally run `c0` on test as a labelled secondary kept out of `contrast_rows`: 80 units, about 7 CPU hours, 2,020 rows, under the amendment's 2,260.

### 9. Text a script will trip on

- §4's closed-gap share needs ORACLE and B-lib, but §3 runs no ORACLE. Adding both costs about 0.4 CPU hours.
- An interval wholly below zero is not falsified by §1 but does not stand by §3. Rule 1 against rule 2 precedence at a partial stop is also unstated.
- §6's "could not be fitted" is a different path's string (`execute.py:501, 681`). B-sym's own total failure is "no proposal produced a certified fit" (`search.py:624`), which §6 sends to "any other". `UNIT_FAILURES` are recorded on first occurrence (driver docstring), so "retried once" reaches only crashes.
- §2 omits P4b. `PolicySearch` also takes `config.gp` (`plan.py:724`), and SPEC §6.1 now freezes P4b separately.
- No test ties `p4_plan`'s `gp` to `selection.json`.
- H2 holds with probability 7/8 if all candidates are equal, so it is not a hypothesis.

## Checked and sound

- **Floor:** it behaves.
  - A floored row costs a candidate's mean 0.0012 to 0.0016, against a standard error of about 0.005.
  - P3 had 2 such rows in 60, both identity submissions on softplus truths.
  - `weak` plays the part B-lib plays in P4, where it set 54 of 60 floors.
  - Two harmless quirks: a lone finite row earns no credit, and a world where `weak` is `-inf` has a softer floor.
- **Primary statistic:** the mean over truths matches C1's paired mean, and ORACLE does cancel.
- **Addresses:** `gp` is in the address text, so the nine settings cannot collide.
- **Determinism:** two runs of `c0` at HEAD gave identical bytes, sha256 `21f917da…`.
- **Exclusions and seeds:** outcome-neutral as written, apart from finding 5. The nine result stores I counted hold no seed 4 or 5, and root 20261113 appears only in this draft.

## What I could not check

- **Held-out performance of `c1` to `c7`:** not run, by instruction. The proposal table shows what they propose, not what would win.
- **Their real cost:** not measured.
- **Noise figure:** it rests on 6 worlds. In 5 of the 6 the shifted seed beat the stored `c0`, too few to read. A seventh run (dev-015 s2) was still going after about 800 s when my CPU limit killed it, against 192 s stored, so it is censored.
- **Source of the 1,219, 195 and 405 s figures:** not in any committed record but this draft.
- **Windows counts:** they come from committed reports, as that store is not on this machine.
- **`.cache/drafts` scripts:** only grepped for reuse of seeds 4 and 5, not read.

## How this ran

- $0, no LLM calls, under 1 CPU hour (`ulimit -t 3000` on the real units).
- Real units, all on seeds 1 to 3: `c0` twice, `weak` on 7 worlds, shifted-seed `c0` on 6 plus the killed one.
- Not touched: seeds 4 and 5, root 20261113, any control id, any guard. No block was hit.
- **Deviation from your run form:** the first store reads used `python3 -I` from the repo instead of the `uv run` form, without `nice`. They used stdlib only, opened SQLite read-only, wrote nothing to the repo, and did not overlap the bounded run.
- With foreground sleep blocked and no Monitor tool, I waited on the run by following its log with `tail -f` under a timeout.
- The worktree is removed and the repo is clean. Scripts and `real.jsonl` are in `/tmp/rt-s36c/`. To rerun `propose.py` or `real.py`, first run `git worktree add --detach /tmp/rt-s36c/wt e5e205f`.

# Pre-registration: tuning B-sym on dev before the §6.1 freeze

**Status: DRAFT, third text, not frozen, not reviewed.** Written in lane session 36 on the control room's instruction (INBOX 2026-11-06, "B-sym: tune, do not waive"): tune on dev only, CPU only, write the effort budget first, at least the effort the agent arms got, in the same units, and freeze the tuned settings in the §6.1 commit. SPEC §9's risk table and LOG 2026-10-02 required this in P3, and it was not done (LOG 2026-11-06). The first text (a71a182) was reviewed not ready, 6 blocking (`docs/v2/redteam/2026-11-06_bsym-tuning.md`). The second (sha256 `e5d47486…`) was reviewed not ready, 7 blocking (`docs/v2/redteam/2026-11-07_bsym-tuning-round2.md`). This text answers it and needs a narrow third round, on stage 2, sections 5 and 6 and the script together, before any freeze.

**What has been touched.** No candidate below has been run on seeds 4 to 6. P3's B-sym numbers on default settings are known, on seeds 1 to 3. The first round's reviewer ran `c0`, a weak setting and a shifted-seed `c0` on a few worlds of seeds 1 to 3, and a stub proposer that reads no world, and its figures informed this list. The second round's reviewer ran `c0`, `c9`, `c10`, `c11` and a softplus Hawkes variant on two worlds of seeds 1 to 3, reading CPU, fit counts, link counts and training BIC and no held-out value, and those figures set `c11` and stage 2's limits. The script's engineer ran all 14 settings at 4 fits on dev-000 seed 1 under another root, read its printed label only, and that says nothing about tuning. So the candidate list is not blind to how the default fails on those seeds. These figures are sizing, not results. No test split exists.

## 1. Question

Which of a fixed list of B-sym settings is best by a pre-registered dev rule, at the agents' budget? The aim is the strongest rival this rule can find for C1, and the rule favours the best setting and not the default. It makes no claim about strength on the test split: selection uses other seeds of the same 20 truths, and P4 has no default row.

- **H1 (the instrument separates at the scale of the decision).** Section 3's graded control has an interval wholly above zero. Falsified otherwise, including an interval wholly below zero.

## 2. Design

**Effort budget, in the agents' units.** One basis: every session or unit run on dev, and every candidate compared.

| | Candidates | Dev units |
|---|---|---|
| Agent prompt, on the record | 3 texts drafted (`v1`, `link-neutral`, `link-balanced`), 2 compared on dev | 524 sessions: Windows P3 240, the Δ6 comparison 40, Linux smoke 4, Linux P3 240. P4.0's 60 would make 584, if it runs |
| B-sym, here | 12 compared, plus 2 not selectable | 720 units on the 12, 840 with the other 2, on new seeds alone, before any stage 2 |

So B-sym gets at least the agents' effort on both counts, whether or not P4.0 runs. Not counted on either side: reading of transcripts, red-team rounds and design argument, which the prompt had far more of and which have no common unit here.

**Candidates.** Each is the default with the one change named, so **two gains cannot combine**, and that limit is accepted. `max_attempts` and `max_features` stay: they are guards. The list follows the diagnosed failures (round 1, finding 1): B-sym submitted softplus on 6 of 33 softplus-truth units, its structures are shallower than the truths', and 37 fits after the seeds are about 4.6 generations at offspring 8.

| Name | Change from the default |
|---|---|
| `c0` | none: population 8, offspring 8, tournament 3, crossover 0.2, `DEFAULT_MUTATION_WEIGHTS`, the library's seeds |
| `c1` | population 4, offspring 4 |
| `c2` | population 16 |
| `c3` | tournament 2 |
| `c4` | tournament 5 |
| `c5` | crossover 0.0 |
| `c6` | crossover 0.5 |
| `c7` | `CHANGE_LINK` weight 3.0 |
| `c8` | `CHANGE_LINK` weight 6.0 |
| `c9` | offspring 2 |
| `c10` | `ADD_FEATURE`, `MULTIPLY` and `WRAP_GATE` weights doubled |
| `c11` | the library's seeds, then `link=softplus; Excite(ExpK, One, all)`, last |

**The softplus seed, in writing.** `search.py`'s fairness argument for library seeds, "the agent also knows the library", applies to links too: the agent is told the links exist. So it is a candidate and not a default: it wins only by the same rule. It is a Hawkes seed and not a null, because a softplus null has one rate, ties the identity null on BIC and is almost never selected as a parent (round 2, finding 6). It sits outside `GPConfig` and moves `sym_seeds`: B-sym, B-sym@10F, B-sym@F=80, planted-hint and the policy arms, and not ORACLE, B-lib, B-np, B-rand or B-sparse. If it stands, the note says so.

**Not selectable.**

| Name | Setting | Role |
|---|---|---|
| `weak` | every mutation weight 0 but `REMOVE_FEATURE` at 1.0 | smoke test only. It runs out of proposals, 18 to 30 fits of 40 on seven real units, so it is not at the budget and says nothing of separation. Its failure to lose to `c0` means a bug stop |
| `c0b` | `c0` with search seed `(search_seed(truth, seed) + 1) % 2**63` | the null: it measures the noise floor directly, and its rank among the 12 is reported. Its cells go in their own store, `.cache/drafts/bsym-tune/c0b/`, because `c0b` has `c0`'s address. Its job calls `BSym` with that seed, then scores as `run_baseline_job` does, and a test holds it to that path's reading and detail |

Both are in the floor's domain on purpose, as B-lib is in P4's.

**Stage 1 units.** All 20 dev truths at seeds 4, 5 and 6, 60 worlds, B-sym at P4's budget (`fits` 40), each of the 14 settings on every world. One process per unit, as the miss check's `job_main`, calling `run_baseline_job`, with cells in `_record`'s shape, so that `RLIMIT_CPU` is per unit. Candidates share a world's data, so comparisons are paired.

**Stage 2, only if the winner is not `c0`.** Stage 1's question is F = 40, and controls 1 to 3 gate every read, so a setting that fails control 3 at 10×F would void C1 to C4 after 1,320 sessions, and dev tuning is the cure only before test data exist. A pass runs under one setting, on worlds selection did not use, in this order, by sorted truth and seed: B-sym@10F at seed 1 on 10 truths, then planted-hint on all 20 truths at seeds 1 to 3 (60 units). The 10 truths, by sorted id within each stratum: the first three near, the first three far and all four mid, dev-001, 002, 004; dev-000, 003, 005; dev-010, 015, 016, 019. So control 3 reads mid truths, which P3's did not. Six of the ten have a P3 row under `c0` and the four mid truths have none, so if a winner fails on one, `c0`, which stands instead, was never checked there.

**The read.** Rows are `ScoreRow`s with gap = `per_event` minus the stored ORACLE's, unfloored. A unit killed, failed or without a reading has gap `-inf`, and a unit never run means the pass did not finish. Control 2 is `planted_beats_rand` against B-rand's stored rows on all 20 dev truths, since P3's control 1 kept all 20 (`oracle_beats_lib` on the table; any other count is a bug), with `default_rng(SeedSequence([20261113, 12 + p]))`, 10,000 resamples, p = 1 for the first pass and 2 for the second. Control 3 is `bsym_approaches_oracle` on the 10 truths. A pass passes when both `.passed` are true. The stored rows' addresses moved with F5, so they come from a table of ORACLE, B-lib, B-rand and B-sym rows exported once at 9db1a46, under seeds 1,2,3 and `control_truths=10`, before stage 1 launches, committed at `docs/v2/bsym-tuning/p3-rows.json`, with its sha256 in the script.

- The pass passes: that setting stands, and `final.json` names it.
- It fails: the next-ranked candidate by section 5's order, if it ranks above `c0`, takes one pass. If that fails too, or none ranks above `c0`, `c0` stands.
- After the passes, B-sym at seeds 1 to 3 (60 units) runs for the setting that stands, if it is not `c0`, inside the same limits. It is a step of its own after `final.json`, and writes `margin.json`, with `finished` false after a stop or a CPU limit. No rule reads it: it gives the margin against `c0`'s stored rows on worlds selection did not use. If it does not finish, the note says so.

**Where it lands.** `p4_plan` holds `gp`, as a named constant `TUNED_GP` that P4b's plan must also import, in the §6.1 commit. If `c11` stands, `pointproc_environment` appends that structure to `sym_seeds`, never to `B_LIB_LIBRARY`, in the same commit. Both records are committed under `docs/v2/bsym-tuning/`, so a test at `T` can tie `TUNED_GP` and `sym_seeds` to them. `GPConfig`'s own defaults do not change, so `gp` moves no pilot address, and `sym_seeds` would move only the P4 systems named above. It applies wherever `config.gp` is read: B-sym, B-sym@10F, B-sym@F=80, planted-hint, and P4b's `PolicySearch`.

## 3. Primary analysis

Per world, the held-out `per_event` reading of each setting. The difference between two settings' gaps on a world equals the difference of these readings, since ORACLE's term cancels, so no ORACLE unit runs in stage 1. Any row with no finite reading, whatever the cause, takes the lowest finite reading on that world among the 14 settings, unless all 14 failed. This is stage 1's own rule and is wider than P4's pair rule, which floors only the failure it names. A truth's value is the mean over its seeds.

- **Selection statistic:** each candidate's mean over truths.
- **H1, graded control:** `c0` at 40 fits minus `c0` at 30 fits, from `c0`'s own curve, paired by truth, bootstrap over truths through `paired_bootstrap_ci`, truths sorted, 10,000 resamples, 95% percentile interval, from `default_rng(SeedSequence([20261113, 0]))`, on complete worlds. `c0`'s reading at 30 fits is the last curve point with `fits_used` ≤ 30, read with `float()`, and at 40 likewise. A `-inf` at 30 or at 40 takes that world's floor, the same number at both ends. A curve with no point within 30 fits takes the floor at 30. A world where `c0` has no curve leaves H1 and is counted, and if every world leaves, H1 has no interval and counts as falsified. On P3's seeds, read this way, it passes for every 2-seed pair, means 0.0077 to 0.0107, and on 3 seeds 0.0093 [0.0047, 0.0141]. It is nested, so it is lenient, and that is stated wherever it is cited.

## 4. Secondary, labelled exploratory

Each candidate's mean difference from `c0` with its paired interval, candidate `i` of the 12 on stream `[20261113, 1 + i]`, `c0b`'s rank, `-inf` counts, CPU per setting, results by dictionary half. No claim is made from these. The winner's margin is inflated by selection: if all 12 are equal, `c0` is kept 1 time in 12 and the chance lead is about 0.0055 to 0.0063 on 60 worlds. That does not touch P4, whose data are fresh.

## 5. Decision rules

The script applies these in order, the first that fits, and writes `selection.json`. **Any label but `SELECTED` means `c0` stands.** Nobody decides anything after the statistics exist.

1. `BUG_STOP`: `weak` did not lose to `c0` on the mean, on complete worlds. `c0` stands, and section 6's bug rule applies. With no complete world, rule 2 applies.
2. `INCOMPLETE`: fewer than 54 of the 60 worlds are complete. A world is complete when all 14 settings have a row or a floor. A failure common to all 14 drops that world. `c0` stands.
3. `NOT_SEPARATED`: H1 is falsified. `c0` stands.
4. `SELECTED <name>`: otherwise, the candidate of `c0` to `c11` with the highest mean, on complete worlds. An exact tie goes to the lower number, so `c0` wins ties. Then stage 2. `SELECTED c0` means tuned and the default kept, and is reported in those words.

There is no threshold for leaving the default. Choosing on a noisy lead costs little only because stage 2 exists, and requiring significance would favour the default.

**The records.** Stage 1 writes `selection.json`, stage 2 writes `final.json` when its passes end, and the margin step writes `margin.json`, at constant paths under `.cache/drafts/bsym-tune/` with no option. `final.json` names the setting that stands, and nothing after it changes that. If the label is not `SELECTED <name>`, or is `SELECTED c0`, `selection.json` is final and stage 2 does not run. **A pass not finished at the CPU stop or at a `STOP` file has failed**, and the script writes that. So nobody can keep a stage 1 result by stopping stage 2.

**Procedural, and stated as such.** With no `final.json` at the §6.1 commit, `c0` stands: the script cannot see that commit, so the lane applies this, and `check_plan`'s test reads a missing record as `c0`. The run has its own detached worktree at the script's commit, whose HEAD never moves. The lane copies each record to S3 and commits it under `docs/v2/bsym-tuning/` from the main checkout, so doing so cannot move the commit stage 2 checks.

## 6. Exclusions and failure handling

No world or candidate is excluded for its outcome. A unit past its CPU limit is killed, journalled as `cpu_limit` so that a resume does not rerun it, and takes the floor. A crash of the process, a reboot or an out-of-memory kill resumes by the same command, on the same store, and the CPU count spans restarts. A unit whose worker is lost twice takes the floor. The script allows exactly seeds 4, 5 and 6 in stage 1, refuses to start on a store holding cells of any other run, and stage 1 refuses to start once `selection.json` exists. Stage 2 starts only from a `selection.json` naming a winner other than `c0`, at the same commit, on its own store `.cache/drafts/bsym-tune/stage2/` and its own CPU count. It resumes as stage 1 does, and refuses to start once `final.json` exists. So there is one run of each. A bug found before any outcome is read is fixed by amendment. **A bug found after any outcome is read stops every further step, and `c0` stands, unless an amendment frozen before the §6.1 commit names the fault, shows it with a failing test, and names a new output directory on the same seeds. The rerun's label then applies by section 5, whatever it says, and the first result stays beside it.**

## 7. Sample size

20 truths is the dev split. Drawing more is a question for the control room, not taken here. Three seeds give 60 worlds per setting, and that is what clears the effort table. Paired world differences for `c0` under a shifted seed have rms about 0.034 (6 worlds; P3's upper bound 0.039), so a candidate difference has a standard error of about 0.004 to 0.005. By the reviewer's calculation, assuming independent candidates and at 12 candidates, a true lead of 0.01 is picked 86% to 92% of the time, and of 0.005 44% to 51%.

## 8. Cost ceiling and wall clock

**Stage 1 ceiling: 114 CPU hours, $0. Stage 2 ceiling: 148 CPU hours, $0. Total 262.** No LLM call, no AWS launch, dm-devbox only. The second text's 200 did not fit the candidates added for round 1: at `c9` and `c10`'s measured 2.0 to 2.5 times `c0`, one pass passes its stop, and the unfinished-pass rule would have sent those winners to `c0` on cost, against section 1. The ratio rests on 3 units at 40 fits and is unmeasured at 400.

**The mechanism,** as the pilot miss check's: CPU is counted from the kernel per systemd unit, by the cgroup's `cpu.stat`, kept in a file that spans restarts, and each process runs under `RLIMIT_CPU`. Stage 1: limit L = 2 hours, 4.7 times B-sym's slowest P3 unit (1,537 s). Dispatch stops at 110, and 2 units in flight give 114. Stage 2: L = 14 hours, against 13.1 for dev-008's 5.96 at 2.2 times, though dev-008 is not among the 10. Dispatch stops at 120, and 2 in flight give 148.

**Arithmetic.** Stage 1: 840 units at P3's B-sym mean of 325 s is 75.8 CPU hours, and `weak` costs less. With `c9` and `c10` at about twice that and the rest at P3's, 83.8; with `c1` as `c9`, 89.8; with `c7` and `c8` at 1.5 times, an assumption, 95.2, all inside 110. Stage 2, one pass: planted-hint 10.5, B-sym@10F 27.8 on P3's ten truths, so 38.3 and 84 at 2.2 times. Mid truths' 10×F cost is unknown. Two passes at 2.2 times do not fit under 120, and a winner that costs that much gets its first pass only. These are guides from P3's timings on other seeds. The limits are what bind. A stop leaves section 5 to apply.

**Machine.** 2 workers, `nice` 10, its own `systemd-run --user` unit (no LLM, so not braked), launch with at least 8 GiB available, the mechanism's gate. P3's peak worker was 2.18 GiB. At two cores, 76 CPU hours is at least 38 wall hours and 110 at least 55, and the stage 2 ceiling at least 74. I have no measured figure for the cores it will really get.

**Order, fixed and outcome-neutral.** Stage 1: worlds by `sha256(f"{truth}|{seed}")`, all 14 settings on a world before the next, so a stop leaves complete worlds. Stage 2: as its section says.

## 9. Early-stop conditions

The CPU stop, a `STOP` file, or a bug. No stop on outcome. Nothing is read before the end: the script prints counts and CPU only, and no session reads the store before `selection.json` exists. That last part is procedural.

## 10. Reported whatever the outcome

A note under `docs/v2/`, red-teamed: the label, every setting's statistic, H1's interval, `c0b`'s rank, stage 2's result, `-inf` and failure counts, CPU hours, and the effort table's actual figures. P3's write-up carries the untuned deviation whatever this finds. If the result is not `c0`, the note says P3's B-sym and P4's differ, so the two are not comparable on that row, and P4 has no default row unless a separate decision adds one.

## 11. Code

`scripts/bsym_tune.py` with `tests/campaign/test_bsym_tune.py`, test first: the settings are constants; `c0b`'s seed and its store; the floor, at fit 30 as well; the graded control's bootstrap on a hand-computed toy; each label and the order they apply; the tie rule; the fallback to the next rank; a killed unit staying killed across a resume, and taking the floor; a unit lost twice; stage 2's read and its pass rule, a pass unfinished at a stop, and the second pass's stream; no outcome printed before the end; a store under `.cache/drafts/bsym-tune/`; refusals of any truth not in the dev split, of any seed but 4, 5 and 6 in stage 1, of a store holding other cells, of a stage 1 start once `selection.json` exists, of a stage 2 start at another commit, with no winner, or once `final.json` exists; the CPU count across a restart; a test tying `TUNED_GP` and `sym_seeds` to the committed records. Seed root 20261113 is new. Tests use another root. The script's commit is named in the LOG before launch. A change after that is an amendment.

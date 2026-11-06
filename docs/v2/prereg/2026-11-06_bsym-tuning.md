# Pre-registration: tuning B-sym on dev before the §6.1 freeze

**Status: DRAFT, second text, not frozen, not reviewed.** Written in lane session 36 on the control room's instruction (INBOX 2026-11-06, "B-sym: tune, do not waive"): tune on dev only, CPU only, write the effort budget first, at least the effort the agent arms got, in the same units, and freeze the tuned settings in the §6.1 commit. SPEC §9's risk table and LOG 2026-10-02 required this in P3, and it was not done (LOG 2026-11-06). The first text (a71a182) was reviewed not ready, 6 blocking (`docs/v2/redteam/2026-11-06_bsym-tuning.md`). This text answers it and needs one narrow second round before any freeze.

**What has been touched.** No candidate below has been run on seeds 4 to 6. P3's B-sym numbers on default settings are known, on seeds 1 to 3. The first round's reviewer ran `c0`, a weak setting and a shifted-seed `c0` on a few worlds of seeds 1 to 3, and a stub proposer that reads no world, and its figures informed this list. So the candidate list is not blind to how the default fails on those seeds. Its figures are sizing, not results. No test split exists.

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
| `c11` | the library's seeds plus a softplus null structure |

**The softplus seed, in writing.** `search.py`'s fairness argument for library seeds, "the agent also knows the library", applies to links too: the agent is told the links exist. So it is a candidate and not a default: it wins only by the same rule. It sits outside `GPConfig` and moves `sym_seeds`, so if it wins, `sym_seeds` changes for every system that reads them in P4, and the note says so.

**Not selectable.**

| Name | Setting | Role |
|---|---|---|
| `weak` | every mutation weight 0 but `REMOVE_FEATURE` at 1.0 | smoke test only. It runs out of proposals, about 29 fits of 40, so it is not at the budget and says nothing of separation. Its failure to lose to `c0` means a bug stop |
| `c0b` | `c0` with a shifted search seed | the null: it measures the noise floor directly, and its rank among the 12 is reported |

**Stage 1 units.** All 20 dev truths at seeds 4, 5 and 6, 60 worlds, B-sym at P4's budget (`fits` 40), each of the 14 settings on every world, by the framework's normal path. Candidates share a world's data, so comparisons are paired.

**Stage 2, only if the winner is not `c0`.** Stage 1's question is F = 40, and controls 1 to 3 gate every read, so a setting that fails control 3 at 10×F would void C1 to C4 after 1,320 sessions, and dev tuning is the cure only before test data exist. Under the winner, on worlds selection did not use: B-sym at seeds 1 to 3 (60 units), planted-hint at seeds 1 to 3 (60), and B-sym@10F on dev-000 to dev-009 at seed 1 (10). Read against B-rand's and ORACLE's stored P3 rows, by control 2's rule and control 3's rule as `scoring/gonogo.py` has them. Their addresses moved with F5, so those rows come from a table exported once at 9db1a46, where the store reads, with its sha256 in the script.

- Both pass: the winner stands, and the margin on these worlds is reported.
- Either fails: the next-ranked of `c1` to `c11` above `c0` takes stage 2, once. If it fails too, or no other candidate ranks above `c0`, `c0` stands.

**Where it lands.** The result goes into `p4_plan` as the config's `gp`, and `sym_seeds` if `c11`, in the §6.1 commit. `GPConfig`'s own defaults do not change, so no pilot address moves. It applies wherever `config.gp` is read: B-sym, B-sym@10F, B-sym@F=80, planted-hint, and P4b's `PolicySearch`.

## 3. Primary analysis

Per world, the held-out `per_event` reading of each setting. The difference between two settings' gaps on a world equals the difference of these readings, since ORACLE's term cancels, so no ORACLE unit runs in stage 1. A row that scored `-inf`, has no submission, or ended "no proposal produced a certified fit" (`search.py`) takes the lowest finite reading on that world among the 14 settings, as P4's pair rule does. A truth's value is the mean over its seeds.

- **Selection statistic:** each candidate's mean over truths.
- **H1, graded control:** `c0` at 40 fits minus `c0` at 30 fits, from `c0`'s own curve, paired by truth, bootstrap over truths, 10,000 resamples, 95% percentile interval, from `default_rng(SeedSequence([20261113, 0]))`. On P3's seeds this passes for every 2-seed pair, means 0.0077 to 0.0107. It is nested, so it is lenient, and that is stated wherever it is cited.

## 4. Secondary, labelled exploratory

Each candidate's mean difference from `c0` with its paired interval, `c0b`'s rank, `-inf` counts, CPU per setting, results by dictionary half. No claim is made from these. The winner's margin is inflated by selection: if all 12 are equal, `c0` is kept 1 time in 12 and the chance lead is about the standard error, about 0.004 to 0.005 on 60 worlds. That does not touch P4, whose data are fresh.

## 5. Decision rules

The script applies these in order, the first that fits, and writes `selection.json`. **Any label but `SELECTED` means `c0` stands.** Nobody decides anything after the statistics exist.

1. `BUG_STOP`: `weak` did not lose to `c0` on the mean. `c0` stands, and section 6's bug rule applies.
2. `INCOMPLETE`: fewer than 54 of the 60 worlds are complete. A world is complete when all 14 settings have a row or a floor. A failure common to all 14 drops that world. `c0` stands.
3. `NOT_SEPARATED`: H1 is falsified. `c0` stands.
4. `SELECTED <name>`: otherwise, the candidate of `c0` to `c11` with the highest mean, on complete worlds. An exact tie goes to the lower number, so `c0` wins ties. Then stage 2. `SELECTED c0` means tuned and the default kept, and is reported in those words.

There is no threshold for leaving the default. Choosing on a noisy lead costs little only because stage 2 exists, and requiring significance would favour the default.

## 6. Exclusions and failure handling

No world or candidate is excluded for its outcome. A unit past its CPU limit is killed and takes the floor. A crash of the process, a reboot or an out-of-memory kill resumes by the same command, on the same store, and the CPU count spans restarts. The script allows exactly seeds 4, 5 and 6 in stage 1, refuses to start on a store holding cells of any other run, and refuses to start at all once `selection.json` exists. So there is one run. A bug found before any outcome is read is fixed by amendment. **A bug found after is fixed by amendment too, and the first result stays beside the new one, and replaces it only by the owner's logged decision.**

## 7. Sample size

20 truths is the dev split. Drawing more is a question for the control room, not taken here. Three seeds give 60 worlds per setting, and that is what clears the effort table. Paired world differences for `c0` under a shifted seed have rms about 0.034 (6 worlds; P3's upper bound 0.039), so a candidate difference has a standard error of about 0.004 to 0.005. By the reviewer's calculation, assuming independent candidates and at 8 candidates, a true lead of 0.01 is picked about 9 times in 10, and of 0.005 about half the time. Power against the 11 others is lower and was not recomputed.

## 8. Cost ceiling and wall clock

**Stage 1 ceiling: 100 CPU hours, $0. Stage 2 ceiling: 100 CPU hours, $0. Total 200.** No LLM call, no AWS launch, dm-devbox only.

**The mechanism,** as the pilot miss check's: CPU is counted from the kernel per unit, kept in a file that spans restarts, and each unit runs under `RLIMIT_CPU`. Stage 1: limit L = 2 hours, 4.7 times B-sym's slowest P3 unit (1,537 s). Dispatch stops at 90, and 2 units in flight give 94. Stage 2: L = 8 hours, against B-sym@10F's slowest, 5.96 hours. Dispatch stops at 84, and 2 in flight give 100.

**Arithmetic.** Stage 1: 840 units at P3's B-sym mean of 325 s is 75.8 CPU hours, and `weak` costs less. Stage 2, one pass: B-sym 5.4, planted-hint 10.5, B-sym@10F 27.8, so 43.7, and two passes 87.4. These are guides from P3's timings on other seeds, and candidates' real costs are not measured. The limits are what bind. A stop leaves section 5 to apply.

**Machine.** 2 workers, `nice` 10, its own `systemd-run --user` unit (no LLM, so not braked), launch with at least 6 GB available. At 0.81 of a core each, 76 hours is about 47 wall hours and 90 about 56.

**Order, fixed and outcome-neutral.** Worlds by `sha256(f"{truth}|{seed}")`, all 14 settings on a world before the next, so a stop leaves complete worlds.

## 9. Early-stop conditions

The CPU stop, a `STOP` file, or a bug. No stop on outcome. Nothing is read before the end: the script prints counts and CPU only, and no session reads the store before `selection.json` exists. That last part is procedural.

## 10. Reported whatever the outcome

A note under `docs/v2/`, red-teamed: the label, every setting's statistic, H1's interval, `c0b`'s rank, stage 2's result, `-inf` and failure counts, CPU hours, and the effort table's actual figures. P3's write-up carries the untuned deviation whatever this finds. If the result is not `c0`, the note says P3's B-sym and P4's differ, so the two are not comparable on that row, and P4 has no default row unless a separate decision adds one.

## 11. Code

`scripts/bsym_tune.py` with `tests/campaign/test_bsym_tune.py`, test first: the settings are constants; the floor; the graded control's bootstrap on a hand-computed toy; each label and the order they apply; the tie rule; the fallback to the next rank; no outcome printed before the end; a store under `.cache/drafts/bsym-tune/`; refusals of any truth not in the dev split, of any seed but 4, 5 and 6 in stage 1, of a store holding other cells, and of a start once `selection.json` exists; the CPU count across a restart; a test tying `p4_plan`'s `gp` to `selection.json`. Seed root 20261113 is new. Tests use another root. The script's commit is named in the LOG before launch. A change after that is an amendment.

# Pre-registration: tuning B-sym on dev before the §6.1 freeze

**Status: DRAFT, not frozen, NOT READY.** Red team on a71a182: not ready, 6 blocking (`docs/v2/redteam/2026-11-06_bsym-tuning.md`), not yet fixed, and the text below stands as reviewed. Nothing has run. Written in lane session 36 on the control room's instruction (INBOX 2026-11-06, "B-sym: tune, do not waive"): tune on dev only, CPU only, write the effort budget first, at least the effort the agent arms got, in the same units, and freeze the tuned settings in the §6.1 commit. SPEC §9's risk table and LOG 2026-10-02 required this in P3, and it was not done (LOG 2026-11-06).

**What has been touched.** No candidate below has been run. P3's B-sym numbers on default settings are known (`docs/v2/PILOT.md`), and so are its seeds 1 to 3, so this uses other seeds. No test split exists.

## 1. Question and hypotheses

**Question.** Among a fixed list of B-sym settings, which gives the best held-out reading on dev at the agents' budget? The aim is a fair rival for C1 and C3's control, so the rule favours the strongest setting and not the default.

- **H1 (the instrument can separate).** The default beats a deliberately weak setting on dev. Falsified if the paired interval in section 3 does not exclude zero. If so, this comparison cannot tell settings apart, and nothing is selected.
- **H2 (tuning matters).** Some candidate has a higher mean held-out reading than the default. It is reported, and no decision rests on its size.

## 2. Design

**Effort budget, in the agents' units.** The units are candidate settings compared on dev and dev units run.

| | Candidates | Dev units |
|---|---|---|
| Agent prompt, on the record | 3 texts drafted (`v1`, `link-neutral`, `link-balanced`), 2 of them compared on dev | 40 sessions in the comparison reported in STATUS-2026-10-05 (LOG 2026-10-27), 240 sessions in P3 on `v1`: 280 |
| B-sym, here | 8 compared, plus 1 weak control | 320 units on the 8, 40 on the control, 360, plus P3's 60 on the default |

So B-sym gets at least the agents' effort on both counts. Not counted on either side: human or agent reading of transcripts, red-team rounds, and design argument, which the prompt had far more of and which have no common unit here. The prompt's link-balanced text was never run. The agents' effort is what the record shows, and it may be an undercount from before this record was kept.

**Candidates.** Each is `GPConfig` with the one change named, and `max_attempts` and `max_features` stay as they are. They are guards, not search settings.

| Name | Change from the default |
|---|---|
| `c0` | none: population 8, offspring 8, tournament 3, crossover 0.2, `DEFAULT_MUTATION_WEIGHTS` |
| `c1` | population 4, offspring 4 |
| `c2` | population 16 |
| `c3` | tournament 2 |
| `c4` | tournament 5 |
| `c5` | crossover 0.0 |
| `c6` | crossover 0.5 |
| `c7` | `CHANGE_LINK` weight 3.0 |
| `weak` | every mutation weight 0 but `REMOVE_FEATURE` at 1.0. The control only: it can never be selected |

**Units.** All 20 dev truths at seeds 4 and 5, 40 worlds, B-sym at P4's budget (`fits` 40, as `p4_plan`'s Sonnet config), each candidate on every world, scored by the framework's normal path. Candidates share a world's data, so comparisons are paired. Seeds 1 to 3 are not used, because their default results are known and were read.

**Where it lands.** The winner goes into `p4_plan` as the config's `gp`, in the §6.1 commit. `GPConfig`'s own defaults do not change, so no pilot address moves. It then applies wherever P4 reads `config.gp`: B-sym, B-sym@10F, B-sym@F=80 and planted-hint. P4's control 3 tests it at 10×F on the test split. Control 2's planted-hint shares it, which is why that control is rerun under it there and nothing from P3 carries over.

## 3. Primary analysis

Per world, the held-out `per_event` reading of each candidate. The difference between two candidates' gaps on a world equals the difference of these readings, since ORACLE's term cancels, so no ORACLE unit runs. A row that scored `-inf`, has no submission or could not be fitted takes the lowest finite reading on that world among the nine settings, as P4's pair rule does. A world on which no setting has a finite reading leaves the analysis, and is counted. A truth's value is the mean over its seeds.

- **Selection statistic:** each candidate's mean over truths.
- **H1:** `c0` minus `weak`, paired by truth, bootstrap over truths, 10,000 resamples, 95% percentile interval, from `default_rng(SeedSequence([20261113, 0]))`. H1 stands if the lower end is above zero.

## 4. Secondary, labelled exploratory

Each candidate's mean difference from `c0` with its paired interval; the share of closed gaps at the pilot's threshold; `-inf` counts; wall time per candidate. Results by dictionary half. No claim is made from these, and on 20 truths the winner's margin is expected to be inflated by selection. That does not touch P4, whose data are fresh.

## 5. Decision rules

The script applies these and writes `selection.json`.

1. `NOT_SEPARATED`: H1 is falsified. Nothing is selected, the default stays, and the control room decides whether tuning is done.
2. `INCOMPLETE`: fewer than 40 worlds finished with all nine settings, at any stop. Nothing is selected, and the control room decides.
3. `SELECTED <name>`: otherwise, the candidate of `c0` to `c7` with the highest mean. An exact tie goes to the lower number, so `c0` wins ties. `SELECTED c0` means tuned and the default kept, and is reported in those words.

There is no threshold for leaving the default. The purpose is the strongest rival and the test data are fresh, so choosing on a noisy lead costs little, and requiring significance would favour the default for no reason.

## 6. Exclusions and failure handling

No world or candidate is excluded for its outcome. A unit that raises is retried once, then counts as failed: a failure with cause "could not be fitted" takes the floor, and any other leaves that world unfinished, so rule 2 applies. A bug in the script is fixed by amendment, and the store restarts.

## 7. Sample size

20 truths are all that dev has. Two seeds give 40 worlds per candidate, twice the 20 worlds of the agents' own comparison, and the total is set by the effort table. Power to separate adjacent candidates is low and is not claimed. H1's control has a large expected effect, and if it does not show, rule 1 says so.

## 8. Cost ceiling and wall clock

**Ceiling: 100 CPU hours, $0.** No LLM call, no AWS launch, dm-devbox only. Dispatch stops at 90, with at most 2 units in flight. P3's slowest B-sym@10F unit took 3.2 hours at ten times this budget, so taking that as a bound on one unit here gives 96.4 at most, and the rest is margin.

**Arithmetic.** 360 units. P3's B-sym mean was 6.9 worker-hours over 60 units at fit/4, 414 s, which gives 41 CPU hours. The determinism run's three fit/5 units took 1,219, 195 and 405 s, mean 606 s, which gives 61. `weak` should cost less, and this is a guide, not a bound. The stop is enforced. At 2 workers and 0.81 of a core each, 61 hours is about 38 wall hours and 90 about 56.

**Machine.** 2 workers, `nice` 10, its own `systemd-run --user` unit (no LLM, so not braked), launch with at least 6 GB available.

**Order, fixed and outcome-neutral.** Worlds by `sha256(f"{truth}|{seed}")`, all nine settings on a world before the next, so a stop leaves complete worlds.

## 9. Early-stop conditions

The CPU stop above, a `STOP` file, or a bug. No stop on outcome. Nothing is read before the end: progress lines print counts and wall times only.

## 10. Reported whatever the outcome

A note under `docs/v2/`, red-teamed: the label, every candidate's statistic, H1's interval, `-inf` and failure counts, CPU hours, and the effort table's actual figures. P3's write-up carries the untuned deviation whatever this finds. If `SELECTED` names anything but `c0`, the note says P3's B-sym and P4's differ, so the two are not comparable on that row.

## 11. Code

`scripts/bsym_tune.py` with `tests/campaign/test_bsym_tune.py`, test first: the candidates are constants; the floor; the bootstrap on a hand-computed toy; each label; the tie rule; no outcome printed before the end; a store under `.cache/drafts/bsym-tune/`; a refusal of any truth not in the dev split and of seeds 1 to 3. Seed root 20261113 is new. Tests use another root. The script's commit is named in the LOG before launch. A change after that is an amendment.

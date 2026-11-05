# Plan: the frozen miss check on P4's fits

**Status: plan, its decision taken (lane session 32, 2026-11-05).** The control room accepted the proposal below as an amendment, with one red-team round (INBOX c81749a; LOG 2026-11-05). Nothing here is frozen, no code is written and no P4 unit exists. It answers the queue item "Apply the frozen miss check to P4's fits before P4's verdict is read" (INBOX 0577e79; LOG 2026-11-05). Section numbers mean `docs/v2/prereg/2026-11-04_pilot-search-misses.md` (`F` = 3226f9b).

**In one line:** the rule for finding and sizing a miss carries over to P4 unchanged, but "same rules" cannot hold for the parts that say what a miss does to the verdict, because P4 has no §7.1 verdict and its contrasts read the LLM rows the pilot rule set aside. The control room decided how (status above).

## What carries over unchanged

Section 2.2's reference search, 2.3's miss rule, 2.4's held-out size, 2.5's selection effects, 2.9's controls and 6's failure handling. Their basis holds: `git diff 900e66a 8fde280 -- src/sciagent/glm` (`p4-rehearsal`) touches `designs.py`, `interventions.py` and `simulate.py`, and not `fit.py`, and `FIT_VERSION` is still `fit/5` there. `run_score_job` still calls `fit(structure, data.observational, data.channels, config=fit_config)`. Group B's control (dev-015 s3) stays a dev unit, so the controls still need the dev split and its hash.

## What cannot carry over

| Pilot rule | Why it fails on P4 | Proposed replacement |
|---|---|---|
| Guard: `git diff F HEAD` outside `docs/` lists only the script and its test (2.10) | At `p4-rehearsal` 8fde280, `F..HEAD` lists 571 files outside `docs/` | Base the guard on the new freeze commit |
| Guard: `GUARDED_PATHS` equal to `900e66a` | 16 guarded files differ at `p4-rehearsal` 8fde280, 3,881 lines added, including `analysis.py`, `execute.py` and `plan.py` | Same list against the §6.1 commit, plus a separate check that `src/sciagent/glm/fit.py` is unchanged from `900e66a` |
| `SPLIT_SHA256`, dev split only | The population is the test split | Two hashes: dev for controls, test for the population, the test records staying outside the repository |
| 2.1's hand census (670 rows) | The census was read before the freeze, and on P4 it must not expose a score | The script classifies rows itself, and nothing prints a score before the label |
| 2.6 item 1: LLM rows "cannot change the outcome" | SPEC §6.2's C1 to C4 and C6 all read LLM rows | LLM rows are read by the outcome |
| 2.6 item 4: no comparison between arms | Comparing arms is what P4 is for | Say what a miss does to each contrast, with no control covering an LLM arm stated as a caveat |
| Tier 1 = fits on baseline rows, tier 2 = LLM-only (section 8) | Tier 1 is meant to be the fits the outcome reads | Tier 1 = every fit a preregistered contrast reads |
| 2.7: substitute into `analysis.verdict`; 5: `GO_SURVIVES_FOUND_MISSES`, `GO_CHANGES` | §7.1's go/no-go is a P3 rule; P4 reports contrasts with paired bootstrap CIs | Substitute into the frozen P4 analysis code. Proposal below |
| 84 CPU hour ceiling | Set for 255 distinct fits | A new ceiling once P4's row count is fixed |

**Size.** P4-PROPOSAL's table gives 1,520 scored sessions and 740 baseline units (580 + 120 + 40), so about 2,260 rows against the pilot's 670. Scaling the pilot's 25.41 CPU hours by rows gives about 86 CPU hours, about 43 hours at 2 workers. This is a rough guide only: it counts controls that would not grow, and P4's mix differs (on the pilot 62 of 240 LLM rows were on the coordinate path, against 205 of 430 baseline rows). Fits on charged efficiency curves stay out, as in 2.1.

## An ordering constraint the queue did not show

SPEC §6.1 freezes "the analysis code" in one commit before the test campaign. The miss check runs before the verdict is read and can change what is reported, so it is analysis code. **The amended script and rule must therefore be frozen in, or before, the §6.1 commit**, not written after P4 runs. If it were written after the fits exist, its rules would be chosen with the population in hand, which is what section 2.10's two-commit freeze exists to prevent.

The run must also keep the contrasts unread: the script's equality step (2.7) computes the stored contrasts, so it writes the stored and corrected tables together at the end, and no session reads either first.

## The outcome (accepted)

Same shape as section 5, with two labels renamed and one clause defined. For each preregistered contrast C1 to C5, compare the stored and corrected read: the sign of the point estimate, and whether the 95% CI excludes zero. C6 is exploratory and is reported without a label.

- `CONTRASTS_SURVIVE_FOUND_MISSES`: no contrast changes on either, tier 1 complete, H1 standing. Wording as 2.7, with its counts per contrast in place of per gate.
- `CONTRASTS_CHANGE`: any contrast changes. The report names it, its rows, and both reads. The stored numbers stay the record, both are reported, and the owner decides what P4 can claim.
- `BYTE_FAIL`, `INCOMPLETE` and `CONTROL_FAIL`: as section 5, with "the computed GO" replaced by "the contrasts".

Caveat to carry: 2.6 item 3 weighs more here. Sessions cannot be replayed under a better fit, and P4's claims are about those sessions, so substitution corrects a score, not what the agent did after seeing fit/5's number.

## Steps, after a GO and the p4-deltas merge

1. Amendment under the `preregister` skill and 2.10's own steps: new document, new frozen version, new output directory, one red-team round. The pilot's records and label stay as they are.
2. Script changes test first (SPEC §6.3): guards, two split hashes, tiers, the contrast substitution, and the SIGTERM race below. Toy tables only.
3. Rehearse on the pilot store, on rows and contrasts of the dev split. Exploratory, and it may not be cited as a P4 result.
4. Freeze with or before the §6.1 commit, which is itself a stop point.
5. After the P4 campaign, run it before any contrast is read.

## A defect to fix in the amended script

`_run_stage` reads `_stop_reason()` and then `self.terminating`. A SIGTERM landing between the two kills the jobs and returns `None`, which means "stage finished", so on the last stage the run reports `complete` with a job killed and no journal line for it (session 32, reproduced by forcing the order, `.cache/drafts/s32/sigterm_race.py`). It also makes `test_sigterm_at_sixty_hours_is_the_wall_limit` fail now and then in the fast tier. The pilot's run was not affected: systemd logged no stop, the unit's result is `success`, and `shortfall` is 0 on tier 1, tier 2 and S2. A change to the frozen script is an amendment, so it is left alone until this one.

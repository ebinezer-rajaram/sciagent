# SPEC text for P4, part C: the rest of what §6.1 needs (proposed words, stop point)

**Status: DRAFT, lane session 37, 2026-11-07, one round run, nothing applied. `docs/SPEC.md` is unchanged. Do not apply this text as it stands: the round found 13 blocking, and the section "After the round" at the end says which words replace these.** This is the list "text still owed before §6.1" at the end of `docs/v2/SPEC-TEXT-P4.md`. The control room's rule stands: one red-team round, and any wording that changes what the paper claims comes back. Most of this writes down what P4-PROPOSAL §5's decisions approved (LOG 2026-10-03) and what the code on v2 does. Each entry says which it is. Items marked **claim** change what the paper can say and are put as rulings.

## C1. §2.2, ψ search (Δ1): **claim**

The text says "A proposal can lose only because it is a worse structure, never because the optimiser stalled". The frozen miss check on the pilot found 25 miss rows on 25 fits, largest |Δ| found 0.02198 (`docs/v2/PILOT-MISSES.md`), so the sentence is false under fit/5. The rest of the amended paragraph describes fit/4's search.

**Recommendation.** Replace "Above that, ψ is profiled coordinate-wise from the best exhaustive sub-grid. The fit is then flagged "certified in θ, coordinate-optimal in ψ", and the share of such fits is reported beside the certified share. A proposal can lose only because it is a worse structure, never because the optimiser stalled." with:

> *(Amended for P4.)* Above that, fit/5 runs block-coordinate ascent over ψ from a fixed set of starts, with a swap check between like slots. The fit is the best point evaluated, and it is flagged "certified in θ, block-coordinate-optimal in ψ", not globally optimal on the grid. The share of such fits is reported beside the certified share. So such a fit can stop below the best point of its own grid, and a proposal can lose to that as well as to being a worse structure. The size of this is measured, not assumed away: the frozen miss check (§6.1) is run on every fit a P4 read uses, a lower bound on the misses and the largest shift found are reported, and no bound is claimed on misses not found. ORACLE also θ-refits at the truth's own ψ and keeps that fit when it is certified and better on training data, and a kept fit below the true parameters' training log-likelihood is flagged `oracle_underfit`.

Also, "So every reported fit is the global optimum on that grid" earlier in the paragraph needs "up to the amendment below". §6.3 no. 2 stands as written: nothing in it claims ψ optimality.

## C2. §4.0, `run_experiment` (Δ2, Δ3): what the code does

In the table, `run_experiment`'s "Returns" cell gains: "A design may use generators (`times: {start, step, n}`; mark patterns `constant`, `alternate[a,b]`, `repeat[...]`), expanded to arrays before validation. AG-o may name a JSON file it wrote in `/work`."

After the table:

> **Truncation.** A run that reaches the event cap (15,000) is cut at that event, at τ, and flagged. It is charged as an experiment and its data are kept. The N-th event is a stopping time, so the likelihood on [0, τ) is the standard one, and fits read it so. Agent-side analysis reads the log clipped to [0, τ), or to the start of an excluded window that holds τ, so the time of an unseen event is never shown. The runaway guards still stop a run.

Instrument note: Δ2's test is an addition to §6.3 no. 1, proposed as "and on a log truncated at the N-th event".

## C3. §4.0, `belief`, and §4.4 (Δ4, test 10b): what the code does

New row in the tools table:

> | `belief(favoured, rivals, confidence)` | records the favoured structure, one or more rivals and a confidence in (0, 1) | unbudgeted, required | both |

`predict`'s row: "records a committed prediction *before* the experiment runs (§4.4); its `hypothesis` is a structure only, and a free `note` is recorded and never shown back". `submit`'s row gains "and a stop reason".

Below the tiers:

> **Beliefs are enforced.** `run_experiment` and `submit` are refused, uncharged, without a fresh belief, one recorded since the last experiment. The refused call reads no design and draws nothing.

§4.4's opening changes from "Before any experiment the agent may — and the prompt asks it to — commit a prediction through `predict`" to:

> Before each experiment and at submission the agent must record a belief (§4.0), and the tool layer enforces it. It may also commit a prediction through `predict`, and the prompt asks for one under a rival. Predictions stay optional, so a measure that needs them is reported with its denominator.

Then "From the record:" becomes "From the framework-recorded timeline:". Falsification-seeking's "favoured and runner-up hypotheses" is read from the belief's `favoured` and `rivals`.

§6.3 gains, under no. 10:

> 10b. The belief timeline is written by the framework, and a scripted agent with a known timeline gives known §4.4 values.

**Checked against the docstring only:** `scoring/behaviour.py`'s falsification-seeking matches the bullet (favoured and runner-up intervals disjoint on some statistic; touching endpoints overlap). It adds an "unpredicted" class, and its share is over experiments run. I read the definitions and not the code of each measure.

## C4. §4.0, budget line (Δ12): what the code does

After the defaults line:

> Every tool result, errors and refusals included, ends with one line giving the experiments and fits left.

## C5. §6.1, session key (Δ7): what the code does

Add to §6.1:

> A session's key holds the digest of its rendered surface: the system prompt, the opening prompt and the tool schemas, per arm and condition, so a prompt edit cannot resume an old session. The surfaces are pinned in the same commit.

The second sentence is true only once the 10 sweep surfaces are in `P4_SURFACES` (queue, "P4 cannot launch yet").

## C6. §9, voids (Δ8): what the code does, and one wider line

Today's row: "transcripts are scanned for out-of-bounds access, and any hit voids the run and is reported". Add a paragraph under the table, or a row:

> **Void policy.** A run is void only if a foreign tool executed, a foreign tool use has no recorded denial, a server tool was used, the scan has an out-of-bounds hit, or the sandbox failed as infrastructure. A foreign name the CLI refused, with nothing run, is counted as an unknown tool attempt and does not void the run. Analysis drops voids and counts them in every table.

The infrastructure clause and the "no recorded denial" clause go beyond Δ8's wording, both on the strict side (`harness/voids.py`).

## C7. §6.4, numeric rules for controls 1 to 3 (Δ10, decision 7, and the unchecked-truth rule): **claim for the last one**

Each replaces the matching sentence, keeping the proof sentence that follows it.

> 1. ORACLE beats B-lib on every test truth, as the paired mean difference of gaps over seeds above its paired standard error, with zero `oracle_underfit` flags, a flag never recorded counting as a failure. A truth that fails is not identifiable and is dropped by the preregistered rule, and the drop is reported. A truth with fewer than 2 seeds on which both gaps are finite cannot be checked. It leaves the reads, and is printed beside them on both tables (set 2026-11-06, before any P4 data).
>
> 2. A **planted-hint agent**, scripted to propose the truth's top-level production first, beats B-rand: per truth, the difference of mean gaps, with `-inf` ranked lowest, gives a one-sided exact Wilcoxon signed-rank p below 0.05, and the percentile bootstrap CI of its median has a lower end above 0. The win count and median are reported, and the mean only beside them.
>
> 3. B-sym at 10× F approaches ORACLE: the median over kept truths of its per-truth mean gap is at least -0.01 nats per event, `-inf` ranking lowest.

**Ruling asked.** Control 1 today reads "on every test truth". The unchecked rule, already decided (INBOX 2026-11-06), lets a truth leave without passing or failing, so "every" narrows. The words above say so.

**Gap closed (Δ10's last clause).** The code flags it (`campaign/analysis.py`, "Gap closed within noise": a seed whose ORACLE minus B-lib difference is within control 1's paired standard error). §4.3 could carry one sentence, and nothing read by a contrast depends on it, so I leave it out unless the round objects.

## C8. §6.1, the miss check

> The frozen miss check (`docs/v2/prereg/2026-11-05_p4-search-misses.md`) is run on P4's fits before any P4 contrast is read. Its label, the corrected contrasts and both tables are reported, and the first run's label stands.

Sits beside the §6.1 sentence on P4b, and is what C1's "(§6.1)" refers to.

## C9. §4.2, parked option 2: **ruling**

§4.2's "Exploding designs" paragraph reads as adopted. LOG 2026-10-29 parked it after control A1 failed under every rule, with today's `Telling` rule kept, and "whether to mark it as parked" was noted for later. A1 stays failed as scored, and the reading of check (b) stays exploratory (LOG 2026-11-05).

**Recommendation.** Keep the paragraph as the record and add at its head:

> *(Parked 2026-10-29.)* Control A1 failed under this rule, so it was not adopted, and the telling diagnostic is chosen as above, over all designs. This matters to P4b, not P4. The rule below returns only with a new freeze.

This belongs to P4b's freeze, so it could wait for that commit. §6.1 lists the contrasts in 6.2 and P4b's are frozen separately, so I recommend applying it now, since the text is otherwise wrong.

## C10. Housekeeping on applying

- `scoring/controls_p4.py`'s docstring says the controls are "preregistered in `docs/v2/LOG.md`, not in SPEC (choice 18)", and gates "C3/C5": out of date since 2026-11-06. `code_fingerprint` is the commit and a clean tree, so any commit changes it: make this edit before `S`, not after.
- §6.1 needs nothing for B-sym beyond "B-sym's tuned setting", already there. If the tuning selects `c11`, §4.1's B-sym row is unaffected, but the seeds move (prereg section 2).
- Unsettled, not SPEC text: whether P4.0's 60 sessions still run. If they do, the tuning prereg's effort table has already allowed for 584.

## What this does not cover

P4b's own pre-registration and FSD's event cap: each is its own queue item. Named minus anon's code is done (`campaign/secondaries.py`). fit/6 stays a SPEC item for after P4. §6.2 and §7 were applied on 2026-11-06.

## After the round

`docs/v2/redteam/2026-11-07_spec-text-p4-c.md`: ready with fixes, 13 blocking, 16 non-blocking, each with replacement words. **Those words replace this file's wherever they differ**, with the line wraps of `docs/SPEC.md` matched on applying (finding 29). I checked findings 3 and 8 against the code and the stored score, and did not check the rest.

**Taken as the reviewer gives them, since they make a sentence true and change no claim:** findings 5 (C5), 7 (C2), 12 (§2.2's sums), 14, 15, 17, 18, 19, 22, 23, 24, 25, 26, 27.

**Rulings for the control room, because they change what the paper claims.** My recommendation on each is the reviewer's words.

| Ruling | Finding | What it decides |
|---|---|---|
| R1 | 1, 13 | §2.2 and §1 say the miss check sizes a loss in the submitted fit's own score only, and nothing about selection or what would have been submitted |
| R2 | 2 | Control 1 as the code has it, and **no limit on how many truths drop**: say so, or set a limit before any P4 data. On two seeds a truth with no real difference passes one time in four |
| R3 | 3 | **Failed units in controls 2 and 3.** Today they leave the control, so stand-in rows with 7 of 12 B-sym@10F units failed passed where the same units at `-inf` failed. I recommend the code fix: a failed unit ranks as `-inf`, test first, before `S`. This was a declared departure for the pilot (`campaign/analysis.py`'s docstring) and was never decided for P4 |
| R4 | 4 | Controls 2 and 3's proof sentences narrow: no control tests the efficiency curve, and control 3 speaks for at least half the kept truths |
| R5 | 8 | §4.2's parking words, and with them a fact for P4b: **today's telling rule scored p = 1 on control 5's own test** on A1's planted truth (`score-ctlA1.txt`: K0 (iii) p=1; K5 p=0.03516). So control 5, which gates C5 and C3's AG-S-boed half, has no passing evidence under the rule that would run |
| R6 | 9, 10, 11 | What `predict` and `fit` really offer, and that experiments never reach the scored fit, which bears on how C3 and the budget sweep are read |
| R7 | 16, 20, 21, 28 | Milder: belief entries declared as a measurement intervention; ORACLE an upper bound on training likelihood only; what "scanned" means; Haiku 4.5 beside Sonnet and Opus 5.5 declared under Q5 |

**R5's K0 reading, confirmed by a run** (session 38, 2026-11-07; INBOX 2026-11-07 asked for it before P4b's freeze). Recomputed from the stored `runs-ctlA1.jsonl` with no draw, through today's `scoring/telling.py`, `policies.choose`, `analysis.telling_observed` and `controls_p4.policy_separation`: (i) 12/15, (ii) 0/120, (iii) p = 1, every He² cell bitwise equal to the frozen `controls.py` and the stored score (`.cache/drafts/s38/k0/`). So when R5's words are applied, "today's telling rule" is no longer marked as read from the code. It covers A1's stored rung-2 streams only and stays exploratory (criteria §8).

**R2's share, picked by the lane and the reviewer** (INBOX 2026-11-07; `docs/v2/redteam/2026-11-07_bsym-tuning-round3.md`, job 2). The reviewer kept the lane's number, replaced its reasons, which did not hold, and added C4's own limit. Add to control 1, after its last sentence:

> On two seeds control 1 passes a truth exactly when ORACLE beats B-lib on both, so a truth with no real difference passes one time in four if its two differences are independent and symmetric about zero, and a real one can fail on one noisy seed. If more than 10% of the test truths leave the reads, as dropped or unchecked, that is more than 4 of 40, the reads are reported as not confirmatory. If more than 2 of the 20 out-of-dictionary truths leave, C4 is so reported, whatever the total.

Why this number, set before any P4 data. It is a limit on scope and not a test: how much of the split may leave before a read stops speaking for it is a judgment. Control 1 reads ORACLE and B-lib, and neither is a side of C1 to C4, so a drop cannot directly favour an arm, though it can lean the reads away from truths near the library: on dev the five narrowest margins are all in-dictionary, near or mid. On P3's dev rows (`docs/v2/bsym-tuning/p3-rows.json`) ORACLE minus B-lib is positive on all 60 worlds, smallest 0.017, and no truth drops on any pair of seeds. Those worlds are about 20 observations, so dev bounds a truth's chance of leaving only at about 14%. As a detector the limit is weak: if each truth independently has no real difference with chance 10%, it fires with chance 0.18, at 20% 0.74, at 30% 0.96. More than 2 of 40 would detect more (0.59 at 10%). We do not take it, because three truths leaving does not stop a read speaking for the split, and the count is printed either way. The test split is not sampled and nothing is known of it. The reads are still printed, and the label of the miss check does not change.

**For information (finding 6):** the void policy's "only" in decision 6 does not hold, since the harness also voids an aborted or hard-timeout run. A `hard_timeout` is absent from the reads while a `wall_time` end with no submission takes the floor.

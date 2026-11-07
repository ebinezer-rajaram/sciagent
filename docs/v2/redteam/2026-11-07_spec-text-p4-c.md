Reviewed: `docs/v2/SPEC-TEXT-P4-C.md` at 833e1e9. Saved verbatim from the red-team agent, lane session 37, 2026-11-07.

# Red team: `docs/v2/SPEC-TEXT-P4-C.md`, one round, at 833e1e9

**Verdict: ready with fixes. Do not apply it as drafted.** There are 13 blocking findings, all with replacement words. Finding 3 also offers a code fix. Nine need the control room (marked **CLAIM**), and finding 6 goes there for information.

- **Q1, truth against the code:** C2, C5, C6, C7 and C9 each hold a false or misleading sentence. C1, C3, C4, C8 and C10 hold where tested, apart from small corrections.
- **Q2, overclaiming:** yes, in C1's replacement sentence, in C7 control 1, and in the two proof sentences C7 keeps.
- **Q3, gaps:** every item on the owed list is addressed. Five SPEC sentences that are false or out of date are not on it (findings 9 to 13).
- **Q4, C9:** the date, the rule kept, A1's scored failure and check (b) staying exploratory are right. Three other things are not (finding 8).

Paths:
- TEXT = `/home/ubuntu/lanes/sciagent/docs/v2/SPEC-TEXT-P4-C.md`
- SPEC = `/home/ubuntu/lanes/sciagent/docs/SPEC.md`
- Code = `/home/ubuntu/lanes/sciagent/src/sciagent/`
- Check scripts = `/tmp/rt-s37c/`, run with `uv run --no-sync python`

## Blocking

**1. C1 (TEXT:11): "The size of this is measured, not assumed away" claims more than the miss check does. CLAIM**
- **Text:** a proposal can lose to a stalled search, and that size is measured "on every fit a P4 read uses".
- **Evidence:** a proposal mostly loses at selection, and PILOT-MISSES:331-337 marks every selection step **Not covered**: B-rand's BIC, B-sym's GP fitness and BIC, and what LLM arms read. PILOT-MISSES:337 says the check answers "what does a better fit of it score?", not "what would it have submitted?".
- **Reach:** prereg §2.1 leaves curve fits out, and prereg §2.7 has classes U, X and A unchecked. In the pilot, 131 of 397 fits were searched and unvalidated, and 82 were outside the check. A stop can also leave tier 1 short (label `INCOMPLETE`).
- **Replacement:**

> *(Amended for P4.)* Above that, fit/5 runs block-coordinate ascent over ψ from starts set by a fixed rule, with a swap check between like slots. The fit is the best point evaluated: certified in θ, block-coordinate-optimal in ψ, not globally optimal on the grid. The share of such fits is reported beside the certified share. So such a fit can stop below the best point of its own grid, and a proposal can lose to that as well as to being a worse structure, both in its own score and earlier, when a system or an agent chooses among fits. The frozen miss check (§6.1) sizes the first only. For the submitted fit of each row a P4 read uses, as far as its stop limits allow, it reports a lower bound on the misses, the largest held-out shift found and the reads with those misses substituted. It claims no bound on misses not found, and says nothing about fits inside a search, a selection or a curve, or about what any system or agent would have submitted under a better fit. ORACLE alone also θ-refits at the truth's own ψ and keeps that fit when it is certified and better on training data, and a kept fit below the true parameters' training log-likelihood is flagged `oracle_underfit`. The flag does not detect a ψ search miss.

- **Last sentence:** the pilot had zero flags and two ORACLE misses (PILOT-MISSES:220, :431).

**2. C7 control 1 (TEXT:75): one rule omitted, the pass condition misdescribed, "not identifiable" too strong. CLAIM**
- **Omitted rule:** an ORACLE at `-inf` fails the whole control (`campaign/analysis.py:767`, `:849`; LOG 2026-10-27 (c); PILOT.md:212 prints it). TEXT's "It leaves the reads" is false for that case. With a floor-free `-inf` on one seed, that truth is `unchecked` and every read goes.
- **"Every":** a failing truth never fails the control. The drop rule already removes it, and no limit exists on how many are dropped.
- **Two seeds:** at n = 2 the paired SE is |d1−d2|/2, so "above its paired SE" holds exactly when ORACLE beats B-lib on both seeds. A truth with no real difference passes one time in four, and a real one can fail on one noisy seed.
- **Evidence:** `check_controls.py`, stand-in rows, 12 truths:

```
n=2 rule == both-positive on 20000 draws: True
null pass rate at n=2 (mean diff 0): 0.25045
null pass rate at n=3 (the pilot's seeds): 0.2135
B ORACLE -inf on one (truth,seed): controls_pass=False kept=11 dropped=0 unchecked=1
C B-lib -inf on one (truth,seed): controls_pass=True kept=11 dropped=0 unchecked=1
F 6 of 12 truths lose ORACLE-vs-B-lib on one seed: controls_pass=True kept=6 dropped=6
```

- **Replacement:**

> 1. For each test truth, ORACLE should beat B-lib: the paired mean difference of gaps over seeds above its paired standard error, which on P4's two seeds means ORACLE above B-lib on both. A truth that fails is not shown identifiable, is dropped by the preregistered rule, and the drop is reported. A truth with fewer than 2 seeds on which both gaps are finite cannot be checked. It leaves the reads, and is printed beside them on both tables (set 2026-11-06, before any P4 data). The control passes only if at least one truth is kept, no `oracle_underfit` flag is raised, a flag never recorded counting as a failure, and ORACLE scores no `-inf` on any truth and seed.

- **Ruling asked:** no share of drops fails the control today. Either say so, or set a limit before any P4 data.

**3. C7 controls 2 and 3 (TEXT:77, :79): a failed unit vanishes, so a crash can pass a control that `-inf` would fail. CLAIM**
- **Text:** "the median over kept truths".
- **Code:** `score_rows` drops any row with no gap (`analysis.py:694-697`), and `covered` keeps only truths with a row (`:820-822`). `Verdict.inconclusive` says nothing about it.
- **Evidence:** the same seven units, in two states:

```
D 7 of 12 B-sym@10F at -inf: controls_pass=False bsym_median=-inf
E the same 7 B-sym@10F units FAILED: controls_pass=True bsym_median=-0.0005
```

- **Inconsistency:** the B-sym third text ranks a failed unit as `-inf` for these same controls (`docs/v2/prereg/2026-11-06_bsym-tuning.md:56`).
- **Fix, preferred:** rank a failed unit as `-inf` in controls 2 and 3. That is a code change, test first, before `S`. Words: "with `-inf`, and any failed unit, ranking lowest".
- **Fix, words only:** "over kept truths with a scored row; a failed unit leaves the control and is counted beside it."
- **Also:** say "per kept truth" and "95%" in control 2. The code reads kept truths (`analysis.py:825`) at level 0.95 (`scoring/stats.py:58`).

**4. C7 "keeping the proof sentence" (TEXT:73): both proofs outrun the numeric rules. CLAIM**
- **Control 2:** SPEC:481-482 says it proves "the efficiency curve can separate". `planted_beats_rand` reads the final gap only, and no control reads a curve. Prereg §2.1 and PILOT-MISSES:341 also leave curves unchecked.
- **Control 3:** SPEC:483-484 says the space is searchable and a B-sym loss "is about efficiency". A median leaves up to half the truths arbitrarily far from ORACLE.
- **Replacement, control 2:** "This shows the held-out gap can separate a good proposer from a bad one. No control tests the efficiency curve."
- **Replacement, control 3:** "This shows a 10×F search gets within 0.01 nats per event of ORACLE on at least half the kept truths, so on those truths a B-sym loss at budget F is about efficiency."

**5. C5 (TEXT:59): the opening prompt is not in the key.**
- **Code:** `surface_digest` hashes `rendered_config` (`campaign/plan.py:859`), which is "without the opening prompt" (`investigation/runner.py:453`). `session_text` adds no opening text either. Δ7 asked only for the system prompt and tool schemas, so the words go beyond the decision too.
- **Evidence:** `check_keys3.py`, `check_surface.py`:

```
['system_prompt', 'tool:belief', 'tool:diagnostic', 'tool:fit', 'tool:nonparam_kernels', 'tool:notebook', 'tool:predict', 'tool:python', 'tool:run_experiment', 'tool:submit']
opening_prompt in surface: False
'Begin the investigation' anywhere in surface: False
```

- **Replacement:**

> A session's key holds the digest of its rendered surface: the system prompt and the tool schemas, per arm, condition, budget and prompt variant, so an edit to either cannot resume an old session. The opening prompt and the text of tool results are outside the digest, and the store's code fingerprint guards them. The surfaces are pinned in the same commit.

**6. C6 (TEXT:67): "void only if" is false. To the control room for information: this goes past decision 6's "only".**
- **Missing void classes:**
  - `aborted` is stored void (`campaign/llm.py:229-238`).
  - `hard_timeout` is stored void (`campaign/llm.py:529-538`), and `classify` keeps that flag (`harness/voids.py:206-213`).
  - A policy fault and a sandbox cleanup fault are infrastructure reasons too (`investigation/tools.py:548`, `runner.py:671-672`).
  - An unknown reason stays void (`harness/voids.py:20-21`).
- **Why it matters:** a `hard_timeout` is absent from the reads while a `wall_time` end with no submission takes the floor (prereg §2.7, S6).
- **"Every table":** today `void_counts` prints once per report (`scripts/pilot_report.py:949`, `scripts/p4_search_misses.py:4288`).
- **Replacement:**

> **Void policy.** A completed run is void only if a foreign tool executed, a foreign tool use has no recorded denial, a server tool was used, the `python` scan has an out-of-bounds hit, or an infrastructure fault was recorded (the sandbox, its cleanup or a policy's decision). A reason the policy does not know stays void. A run that did not complete is void when the harness aborted it or killed it at the hard timeout. A foreign name the CLI refused, with nothing run, is counted as an unknown tool attempt and does not void the run. Analysis drops voids and reports their count per system.

**7. C2 (TEXT:21): "[0, τ)" is false, twice.**
- **Code:** the N-th event at τ is kept and counted (`glm/interventions.py:916-923`, `investigation/view.py:334`, `times <= end`). The stopping-time likelihood needs it. The wording came from Δ2.
- **Evidence:** `check_trunc.py`, cap 300:

```
tau == last event time: True
events counted in the likelihood (tools._counted): 300
events strictly before tau: 299 | at or before tau: 300
clipped: n 300 horizon==tau True last==tau True
```

- **Replacement:**

> **Truncation.** A run that reaches the event cap (15,000, forced events included) is cut at that event, at τ, and flagged. It is charged as an experiment and its data are kept. The N-th event is a stopping time, so the likelihood on [0, τ], that event included, is the standard one, and fits read it so. Agent-side analysis reads the log clipped to [0, τ], or to the start of an excluded window that holds τ, so the time of an unseen event is never shown. The runaway guards still stop a run, which is charged and leaves no dataset.

**8. C9 (TEXT:97): the parking words imply the kept rule is sound, and they misstate it. CLAIM, ruling**
- **The kept rule failed too, and worse.** K0 is telling rule (b) with truthFSD (a). That matches today's `scoring/telling.py:14-24`, `:150` and the policies' atom on undefined values, and `policy_controls.py` has no exclusion. Stored score, `.cache/drafts/fsd-controls/score-ctlA1.txt`:

```
K0: pass=False (i) 12/15 (ii) 0/120 (iii) p=1 passed=False
K5: pass=False (i) 12/15 (ii) 78/120 (iii) p=0.03516 passed=True
```

- **Why it matters:** (iii) is `policy_separation`, control 5's own test. So P4b's control 5 under today's code scored p = 1 on A1's planted truth.
- **"Over all designs" is false.** Stopped replicates are dropped, and a design with one side fully stopped scores 0.
- **"Returns only with a new freeze" is incomplete.** Criteria §8 puts He² resolution first, and results under a later freeze for a control with test data are exploratory. DEVIATION-2026-11-05 adds that any later use of ctlA1 rung 0 carries its note and that rung 0 is not rerun.
- **Replacement:**

> *(Parked 2026-10-29.)* Control A1 failed under every rule tried, this one and today's alike, so the change did not proceed. The telling diagnostic stays as the code has it: chosen from non-stopped replicates, a design where either side keeps none scoring 0. That rule has not passed A1 either: with truthFSD as it stands it failed criteria (ii) and (iii), and (iii) is control 5's test. This matters to P4b, not P4. The rule below returns only under a new freeze, after the He² resolution of criteria §8, on a control with no test data, since results for A1 under a later freeze are exploratory. Any later use of ctlA1 rung 0 carries the deviation note of 2026-11-05, and rung 0 is not rerun.

**9. Not on the list: SPEC:376-379 and :277, what a prediction is. CLAIM**
- **Text:** "or for AG-o a named sandbox function recorded with its code". C3 edits the sentence before this one and leaves it.
- **Code:** the schema is a catalogue enum for both tiers (`investigation/tools.py:1312`, `:991`). `git grep "sandbox function"` hits SPEC only.
- **Replacement:** "A prediction is a catalogue diagnostic with its arguments, the experiment it applies to, the structure it follows from, a central interval and its probability level."
- **Table row:** `predict(experiment, diagnostic, args, hypothesis, interval, level, note)`.

**10. Not on the list: SPEC:185-186 and :276, predictive p-values. CLAIM**
- **Code:** `fit` takes `structure` and `data_ids` only, and its text has no p-value. `git grep predictive_pvalues` finds the definition at `glm/fit.py:1239` and `tests/glm/test_fit.py`, with no production caller.
- **Replacement, §2.2:** "The time-rescaling theorem (a KS test on rescaled inter-arrivals). Predictive p-values exist as an instrument, and no tool reports them."
- **Replacement, §4.0 row:** "certified fit: log-lik, BIC, AIC, rescaling-KS, fitted parameters".
- **Stale docstring:** `diagnostics/catalogue.py:5` says the same.

**11. Not on the list: SPEC:297-300, "from data the sandbox never saw", and §4.3's silence. CLAIM**
- **Code:** the certified fit of the submission reads the observational log (`campaign/execute.py:661`, `:678`, `:597`), which the sandbox holds (`investigation/tools.py:415-416`). The same line shows experiments never reach the scored fit, and the SPEC does not say so. That bears on C3 and on the budget sweep's reading.
- **Replacement, §4.0:** "Scores and the certified fit of the submitted structure are computed by the framework after `submit`. That fit reads the observational log only, which the sandbox holds. The held-out data never reach the sandbox."
- **Add to §4.3 no. 1:** "The submitted structure is refitted on the observational log alone, as ORACLE is, so experiments inform the structure and never its scored parameters."

**12. Not on the list: SPEC:143-144, direct O(n²) sums "fine at n ≤ 5,000".**
- **Code:** `glm/features.py:21-30` uses O(n) recursions for exponential and integer-shape gamma kernels, and a near field plus exponential sum for Lomax. C2's cap is 15,000.
- **Replacement:** "exponential and integer-shape gamma kernels by O(n) recursions, power-law kernels by direct sums over a near field and a sum of exponentials over the far field, whose error bound is in `glm/features.py`, with direct sums kept for tests."

**13. Not on the list: SPEC:79, §1's table. CLAIM**
- **Text:** the fitter "Removes 'the proposal lost because its fit hit a local optimum'". This is the same claim C1 withdraws, and it stays standing.
- **Replacement:** `Removes "the proposal lost because its fit hit a local optimum" in θ for every fit, and in ψ on grids of at most 512 points. Above that a ψ miss stays possible and is sized on submitted fits only (§2.2)`.
- **Stale docstring:** `glm/fit.py:1-2` needs the same edit.

## Non-blocking

14. **C3 belief row (TEXT:29):** "one or more rivals" should be "one to three distinct rivals, the first the runner-up" (`MAX_RIVALS`, `tools.py:221`).
15. **C3 runner-up (TEXT:41):** falsification reads `rivals[0]` only (`scoring/behaviour.py:112-114`), while the prompt asks for a prediction "under a rival" (`prompts.py:503`). A prediction under the second rival cannot count as refuting. Say "the first of its `rivals`", and declare it, since the surfaces are pinned.
16. **C3, decision 8 said "declare it". CLAIM, mild.** Add: "This is a measurement intervention, the same in both tiers."
17. **C3 and 10b (TEXT:41, :45):**
    - The measures are not all timeline reads. `stopping()` has no production caller: `git grep` finds only `controls_p4.py:614`, on assigned gaps. Add to 10b: "stopping's gaps are assigned, so it tests the reading of the favoured path, not the scorer".
    - "k turns" is k = 1 on the experiment clock (`behaviour.py:75`).
    - `submit`'s signature should be `submit(structure, report, stop_reason, note)`.
18. **C4 (TEXT:53):** a name the CLI refused never reaches the layer, and a refusal on a closed layer has no footer (`harness/tools.py:252-253`). Use "Every result of a framework tool". Read in the code, not run.
19. **C1 smaller points:**
    - "Flagged" is loose, since no cell stores `psi_search` and the report recomputes it (`pilot_report.py:547-548`).
    - Starts are data-dependent, hence "fixed rule" above.
    - The truth-ψ rescue is ORACLE's alone (PILOT.md:103). A system submitting the true structure gets no rescue.
20. **SPEC:321, ORACLE "upper bound". CLAIM.** That is true on training data at most. Pilot rows sit above ORACLE on held-out data (+0.00167, +0.00235, PILOT.md:148, :151). Use "reference, and an upper bound on training log-likelihood only". The flag also rarely fires by construction, since the truth-ψ refit starts above the true parameters.
21. **SPEC:607. CLAIM, mild.** "Transcripts are scanned" is really ten regex patterns over `python` call code only (`runner.py:116-127`, `:514-522`).
22. **SPEC:449-451, floor sentence:** code excludes voids, other failures, ORACLE and B-lib (`contrasts.py:58-63`). Add "(a void or other failure is absent; ORACLE and B-lib never take one)".
23. **§6.4 heading says "the contrast it gates"**, but controls 1 to 3 name none. Add "gate every read of C1 to C4" (`contrasts.py:84`).
24. **C8 (TEXT:87):** add "and a later label replaces it only by the owner's logged decision" (prereg §2.10).
25. **C2 instrument note (TEXT:23):** Δ2 also promised fitter recovery on truncated logs. That test exists (`tests/glm/test_likelihood_truncation.py:176`), so add it to §6.3 no. 2.
26. **C10 (TEXT:103-105):**
    - Bullet 1 omits `controls_p4.py:3` and the new `fit.py:1-2`, `catalogue.py:5` and `pilot_report.py:547`, `:564` wording. P4's report builds on that last file.
    - Bullet 2 is right but incomplete: `c11` also moves B-sym@10F, B-sym@F=80, planted-hint and the policy arms, so §6.1's "B-sym's tuned setting" does not name everything frozen. The third text is also unreviewed.
    - Bullet 3 is right.
27. **TEXT:109:** "Named minus anon's code" is stale (QUEUE:340 closed, `campaign/secondaries.py`). fit/6, on the owed list as "after P4", should be named here.
28. **Decision 16. CLAIM.** "Declare it either way" has no trace in SPEC. `MODELS` pins Haiku 4.5 beside Sonnet and Opus 5.5 (`harness/live.py:127-131`), and Q5 reads as pure scaling.
29. **Literal replacement:** C1, C3's opening and control 1's quotes match SPEC only across line wraps (`check_quotes.py`: normalised True, literal False).

## What holds
- **C1's figures:** `decision.json` gives M_rows 25, M_fits 25, max |Δ| 0.021980616852384116. All three sha256s match PILOT-MISSES:13-15.
- **C1's search and ORACLE sentences:** `glm/fit.py:851-946` and `systems/v2/systems.py:284-331`. P4 always passes ψ and coefficients (`execute.py:256-261`).
- **§6.3 no. 2 stands as C1 says:** its optimiser check is at the chosen ψ (`tests/glm/test_fit.py:9`).
- **C2:** generators and `/work` files.
- **C3:** belief gate, `predict`, `submit`, and the falsification reading.
- **C4:** the footer mechanism.
- **C7:** the statistics of controls 2 and 3, and the unchecked rule, including both tables in `reads.json`.
- **C10:** `code_fingerprint`.
- **LOG's choice 18 line:** done at LOG:2648.
- **Tests:** 116 passed, exit 0 (`pytest.out`), covering the belief, timeline, truncation, freeze, void and layer files.

## Could not check
- **No P4 data exist.** No split sha256, run config or byte rerun was possible, and no control has passed on P4 data. So the §6.4 check is structural, and my control scenarios use made-up stand-in rows.
- **Pilot store not read.** I cannot say how many pilot truths would drop on two seeds. PILOT.md:115 gives a smallest margin/SE of 5.3 on three.
- **A1:** read from the stored score, not recomputed from `runs-ctlA1.jsonl`. No control stream was drawn and no guard touched. Calling K0 "today's code" rests on reading, not a run.
- **Slow tests:** 3 deselected, including fitter recovery on truncated logs.
- **No live session:** the CLI denial path and result-size behaviour are from code and docstrings.
- **Not reviewed:**
  - `campaign/secondaries.py`.
  - `investigation/timeline.py` beyond its tests.
  - §6.3 nos. 3 to 6, 8 and 9.
  - §4.1's rows other than ORACLE.
  - The 28 catalogue diagnostics against v1's 20.

## Incidents
Nothing was blocked. `git status --short` is empty and HEAD is 833e1e9. No LLM calls, $0, CPU a few minutes by estimate. Scratch files stay in `/tmp/rt-s37c/`.

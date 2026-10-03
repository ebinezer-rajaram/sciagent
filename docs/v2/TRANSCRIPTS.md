# P3 pilot: what the LLM agents did (transcript analysis)

2026-10-03. Local report only. Covers the 240 P3 pilot LLM sessions (AG-c-S and
AG-o-S, named and anon, 20 dev truths, seeds 1-3, model `claude-sonnet-5-5`).

**This report describes behaviour only.** It makes no claim that one system
outperforms another: SPEC §6.4 controls are under audit (`PILOT-AUDIT.md`).
Where a count is split by arm or condition, that is so the behaviour can be
seen, not to rank the arms. With 60 sessions per cell, most differences between
cells fall within seed-to-seed variation.

## Method

- **Records.** Each session was located through its ledger cell's detail file
  (`.cache/pilot/details/<digest>.json`, the `record_dir`/`record` fields), so
  each session is the one that was scored. Two extra files in one record
  directory are early retries and were ignored. Scores (`exact`, `distance`,
  `per_event`) come from the matching `llm-score` cell. Gap closed is computed
  against the same (truth, seed) ORACLE and B-lib cells.
- **Features**, extracted by script for all 240 sessions: the tool-call
  sequence; every charged fit (structure in native DSL, as the campaign read it,
  plus data ids and BIC); every experiment (kind, horizon, outcome); sandbox
  calls (code-keyword purpose tags, exit codes); predictions; rejected and
  foreign tool calls; the submission; and the report. Structural distances for
  non-submitted structures come from `sciagent.scoring.structure.structural_recovery`.
  The scripts are scratch files and are not in the repo (`extract.py`,
  `analyse.py`, `analyse2.py` and `render.py` in the session scratchpad).
- **Read in full (8):** AG-o/named dev-008 s3, AG-o/named dev-019 s1,
  AG-o/anon dev-013 s2 and AG-o/anon dev-008 s3 (the four listed in PILOT.md);
  AG-o/named dev-005 s1 (far, out of dictionary, 6 experiments);
  AG-c/named dev-017 s3 (far, out of dictionary, no experiments);
  AG-c/named dev-001 s1 (near, out of dictionary, exploding experiments); and
  the void AG-o/named dev-015 s3, read around its foreign call.
  About 15 more reports were read where a check flagged them.
- **The records do not show the model's reasoning.** All 2,925 `thinking`
  blocks are empty: the content is redacted and only the signature is kept.
  `notebook` was never called (0 of 240 sessions). The only first-person
  reasoning is the 346 short text blocks and the final reports. Statements
  about *why* an agent did something are inferences from its actions and those
  texts.

## 1. Search: what agents try

**Every session starts in the same place.** The first fit is always an
identity-link model. 103 of 240 sessions open with
`link=identity; Excite(ExpK, One, all)`, and a further 24 add `+ Periodic`.
From there the search is greedy and additive. Agents add or swap one feature,
mark function, kernel or source split at a time, keep the BIC winner, and
batch 2-3 variants per turn. They fit a median of 17 distinct structures.

| explored (sessions, of 240) | count |
|---|---|
| identity / exp / softplus link tried | 240 / 121 / 131 |
| only one link tried (always identity) | 97 (40%) |
| ExpK / PowerK / GammaK | 239 / 232 / 180 |
| sign-split source | 234 |
| Periodic / Trend | 240 / 200 |
| Gate (any) / LastMarkAbove / PhaseWindow | 135 / 119 / 39 |
| Product | 14 |
| max nesting depth tried: 1 / 2 / 3 | 104 / 131 / 5 |

**Links.** Eleven truths use softplus (132 sessions). In 40 of those sessions
softplus was never fitted, and 31 never left identity. The submitted link
matched the truth's link in 150 of 240 sessions. Submitted links were identity
139, softplus 64, exp 37. On the 7 identity truths every session tried
identity, which is what they start with.

**Beyond the dictionary: almost never.** All 10 out-of-dictionary truths need a
depth-3 feature, such as `Gate(Gate(Periodic, …), PhaseWindow)` or
`Product(Periodic, Gate(Excite, …))`. Only 5 of 120 sessions on those truths
ever fitted a depth-3 structure. All 5 were AG-o, on dev-003 (3) and dev-013 (2),
and in the dev-013 case the structures came out of the agent's own sandbox
search (§3). `Product` appears in 14 of 240 sessions. Submissions were
depth 1 in 201 sessions, depth 2 in 36 and depth 3 in 3.

**When the truth was fitted.** The exact truth was fitted in 41 sessions and
submitted in 32. Eight of the 9 sessions that fitted it without submitting it
are dev-018 (`Excite(GammaK, Mark(size), all) + Periodic`). There, BIC
preferred dropping the Periodic term by 2-15 units, on both `obs` and a plain
experiment run. The agents followed BIC correctly. That truth's periodic term
does not pay for itself at this sample size. In 170 of 240 sessions some
fitted structure was structurally closer to the truth than the one submitted.
Selection is by BIC, not distance, so this is expected rather than a failure
in itself.

## 2. Experiments

**Overall use.** 145 of 240 sessions (60%) ran at least one experiment. The
distribution was 0: 95, 1: 64, 2: 38, 3: 23, 4: 12, 5: 4, 6: 3, 7: 1, and no
session used all 8. The median number left unused is 7. No session ran an
experiment before its first fit; the median user ran its first one after 12
fits.

**What kind of experiments.** Of the 302 experiments that ran:

| kind | n | note |
|---|---|---|
| `inject_marks` (one channel set everywhere or in a window) | 210 | 54 (26%) blew up: "stopped and produced no dataset" (more than 15k or 50k events) |
| `force_events` (incl. 7 composed with inject) | 61 | 0 blew up |
| plain run (`compose` with no parts), often at maximum horizon | 30 | used as "more data" |
| `clamp_rate` | 1 | |
| `censor` | 0 | |

Blow-ups follow one pattern: the agent sets the exciting mark high on every
event, which makes the process supercritical. Forty sessions lost at least one
experiment this way, and 2 lost every experiment they ran. AG-c/named dev-001 s1
lost two in a row before finding a sub-critical value. Interventions on time
(clamping, censoring), which would separate an exogenous periodic rate from
self-excitation, were essentially never used.

**Informative, and do they change the model?**
- Users fit on experiment data (median 12 fits on `e*`). Seven users never fit
  their experiment's data at all.
- Among users, the submission differs from the obs-only BIC leader they had
  before the first experiment in 113 of 145 sessions (78%). Non-users submit
  their obs BIC leader in 89 of 95 (94%). Measured by structural distance to
  the truth, the users' final submission is closer than their pre-experiment
  leader in 76 sessions, the same in 36, and farther in 33. This is
  **confounded**: users also keep searching (median 27 fits against 12 for
  non-users). A change after an experiment is not evidence that the experiment
  caused it. 111 of 145 users submit the BIC leader on their widest joint
  dataset, so experiments act mainly as extra data for BIC.
- In the transcripts read, the targeted experiments were informative and
  the agents read them correctly with their own counts:
  - dev-008 named s3 forced large and small events alternately and saw a
    roughly 4x response after the large ones.
  - dev-005 s1 forced pairs of events and found that the excitation after a
    large mark is fast.
  - dev-001 s1 read its explosions at size 2.2 and 2.5, against a sub-critical
    run at 1.9, as evidence for a threshold. It switched from `Pow(size)` to
    `Above(size)`, which is the truth's first term.

  The plain long runs (dev-013 s2 and dev-008 anon s3, at horizon 64000 in
  anonymised time units) were used only to sharpen periodograms and as joint
  fitting data.
- **Predictions are almost unused.** Ten sessions committed 10 predictions,
  and 11 more `predict` calls were rejected because the `hypothesis` field held
  commentary after the structure (for example `"…, sign forced -1: lambda=…"`).
  Nine of the 302 experiments carried a prediction. Every prediction followed
  from the agent's favoured structure with no rival, so a hit could not
  separate it from alternatives. dev-005 s1 calls the exp link "confirmed" by
  such a hit; the truth uses softplus.

**Why about 40% use none.** It is not because the tools failed: none of the 95
no-experiment sessions made even a failed `run_experiment` call. 94 of the 95
reports say plainly that no experiments were run, usually as a caveat ("rests
on the observational data alone"). None argues that an experiment would have
been uninformative.

The behaviour fits a stopping rule that never reaches the point where an
experiment would be designed. The agent fits a simple model, tries a handful of
one-feature variants, sees that none beats BIC (and often that the KS test
passes), and submits. These sessions use a median of 12 fits. dev-019 named s1
is the clearest case: 7 fits, then "Nothing beats the simple model," then
submit.

Use rises when the observational fits are visibly unresolved. By stratum it was
near 49%, mid 50% and far 74%. By truth it ranged from 1 of 12 sessions
(dev-001, dev-004, dev-006) to 12 of 12 (dev-003, dev-014).

**Rejected calls.** 155 `run_experiment` calls were rejected (not charged), in
51 sessions, with a maximum of 13 in one session. 134 of them are
`force_events` mark arrays: about 114 of the wrong length and 20 over the
500-event limit. A typical message is "marks['size'] has 212 values
for 200 times". The agent generates the list in Python, or by hand, and then
re-types it into the tool call, and the counts drift. dev-005 s1 failed five
times and finally worked around it by composing many 16-element parts.

## 3. Sandbox (AG-o)

- **Use.** 63 of 120 AG-o sessions called `python` (32 named, 31 anon): a
  median of 3 calls per user, 30 at most, 286 calls in all. 11 exited with an
  error and 3 hit the time limit. AG-c never tried to call it.
- **What it is used for** (keyword tags, one call can carry several): reading
  `/data` 199, folding, histograms and periodograms 135, writing `/work` 57,
  generating experiment designs 50, the agent's own likelihood or optimisation
  26, simulation 8, statsmodels 6. In practice it serves experiments.
  59 of 63 users ran experiments, against 16 of 57 AG-o non-users, and 89 of
  AG-o's 90 rejected experiment calls are in user sessions, because users
  attempt the more complex `force_events` designs.
- **Does it help?** It enables behaviour the closed arm does not show.
  - dev-013 anon s2 is the only route to depth 3 seen in this pilot. The agent
    built a binned-likelihood surrogate over phase-folded counts, searched
    gated and product periodic forms in the sandbox, and then fitted the
    winners with the framework. It recovered the truth's
    `Gate(Gate(Periodic, PhaseWindow), PhaseWindow)` feature, but under the exp
    link where the truth uses softplus.
  - dev-008 anon s1 used its own likelihood to argue that the framework's
    uncertified, coordinate-optimal fit sat in a poor optimum: "about 600
    log-lik units" on its own calculation. I have not verified that claim.

  Whether this improves scores is not assessed here (§6.4). No sandbox number
  reaches scoring, because the framework refits the submitted structure
  (invariant 2 holds).
- **The 9 voids ("foreign tool: python") are a tool-name slip, not misuse.**
  In each void, the agent once called a tool named bare `python` instead of
  `mcp__lab__python`. The harness answered "No such tool available," nothing
  ran, and the session used `mcp__lab__python` normally (1-5 calls per
  session). The code was benign: design generation, reading data, and in
  dev-015 s3 the probe `print(1)`. All 9 are on seeds 2-3. The void rule
  counts a rejected, unexecuted call as a hermeticity breach. That is a policy
  choice to revisit, not evidence of an escape attempt.

## 4. Stopping

Fits used per session:
- Quartiles were 13 / 18.5 / 31; only 6 of 240 sessions reached 40.
- The median number of fits after the last improvement in obs BIC is 8, so
  agents stop after a short plateau.
- Turns have a median of 27 and a maximum of 77 (the limit is 100), and the
  wall time and turn limits never bind.

Of the texts written just before `submit` (96 sessions; 144 submit straight
after tool calls), the typical one is "Nothing else helps. Submitting the
simple model." or "Fit 27 is best by BIC, so I'm submitting that structure."
None mentions the remaining budget.

Stopping is therefore local and driven by BIC: the agent's one-step
neighbourhood stops improving. This is reinforced by the prompt, which says "a
simpler structure that is right beats a larger one that merely fits" and
"submit when further evidence is unlikely to change your answer," and there is
no cost to stopping early. Reports often cite simplicity as the reason to stop.

## 5. Named vs anonymised

The anonymised condition changes three things at once:
- the channel names (`m1` = size, `m2` = sign, listed in swapped order);
- the diagnostic names (`d01`, …);
- the time scale (×8: horizon 16000, `exp_rate` grid 0.03125-1, periods 40-800).

Any behavioural difference cannot be attributed to naming alone.

| behaviour | named (120) | anon (120) |
|---|---|---|
| median fits used, AG-c / AG-o | 15 / 16.5 | 21.5 / 23 |
| ≥1 experiment, AG-c / AG-o | 30 / 32 | 40 / 43 |
| sessions trying Gate / LastMarkAbove / Above / ExpOf | 52 / 45 / 64 / 46 | 83 / 74 / 93 / 69 |
| max depth tried ≥ 2 | 53 | 83 |
| median distinct structures | 15 | 19 |
| median diagnostics calls | 5 | 3 |

Paired by (truth, arm, seed), the anonymised session used more fits in 80 of
120 pairs, fewer in 32, and the same number in 8. The named agents lean on
domain priors (sign-split sources first, `Mark(sign)`), but the anonymised
agents try the `m2` splits about as often (120 against 114 sessions). Honesty
and report style showed no visible difference.

## 6. Do the reports match what was done?

On the whole, yes.
- **Experiments.** 94 of 95 no-experiment sessions say they ran none. A regex
  flagged 16 count mismatches, and all of them were false alarms on manual
  reading ("none of the 8 experiments", "one experiment exploded").
- **Variants said to be tried.** Each claim was checked against the session's
  fits:
  - 1 unsupported claim: AG-o/anon dev-000 s1 says "Gamma beats exponential and
    power-law kernels", but it fitted no PowerK and made no sandbox call.
  - 1 claim supported only by the sandbox: AG-o/anon dev-008 s1 says the
    softplus link was beaten, and that comparison was made with its own
    likelihood.
  - 10 reports dismiss a trend without ever fitting `Trend`, apparently on the
    basis of a diagnostic.
- **Numbers quoted.** In the transcripts read, quoted values match the
  framework output (for example dev-008 named s3: LL -2474, BIC 5048,
  KS p 0.92).
- **Volunteered limits.** Reports often say what was not tested ("I also did
  not test gated features"), 87 of 240 use uncertainty language, and 15 point
  out that a fit was uncertified or coordinate-optimal. 190 of 5,114 charged
  fits were in fact uncertified.
- **The main overstatement** is "confirmed" applied to a confirmatory
  prediction (§2).

## 7. Recurring failure modes

1. **Identity anchoring.** 40% of sessions never try a non-identity link, and
   31 sessions on softplus truths never leave identity.
2. **Additive, shallow search.** `Product` is tried in 14 of 240 sessions and
   depth 3 in 5. The parts of out-of-dictionary truths that need nesting are
   essentially never proposed. AG-c/named dev-017 s3, for example, fits 15
   additive variants and submits a 2-feature model against a 4-feature truth
   with Gate and Product, without trying either.
3. **Early, local stopping** (§4), with 7 of 8 experiments typically unused.
4. **Experiments as more data**, not contrasts: plain long runs, and
   mark-injection blow-ups that waste the budget (54 of 302, all
   `inject_marks`). Time-domain interventions go unused.
5. **Malformed tool calls**:
   - 134 `force_events` mark-array errors (about 114 of the wrong length);
   - 11 `predict` calls with commentary in `hypothesis`;
   - 31 diagnostics missing a required `channel`;
   - 22 schema-validation failures: 13 truncated or invalid JSON, 8
     `run_experiment` inputs that fit no schema (such as an extra
     `intervention_note` key), and 1 `data_id` in place of `data_ids`;
   - 9 bare `python` calls.
6. **No recorded belief state.** The notebook is unused, thinking is redacted,
   and predictions are rare and confirmatory. Revision, falsification-seeking
   and stopping (SPEC §4.4) cannot be computed from these records.
7. **BIC under weak identifiability** (dev-018). The truth is fitted and
   correctly rejected by BIC, and no agent designs an experiment that would
   make its periodic term identifiable.

## Implications for the P4 redesign

- **Make experiments matter.**
  - Return the truncated dataset, or a clamped run, when a run hits the event
    cap, instead of nothing.
  - Let `run_experiment` take structured generators (start/step/n, mark
    patterns) or, in AG-o, a `/work` JSON path, so the agent does not re-type
    arrays.
  - Consider a free framework tool that, given two of the agent's fitted
    rivals and a proposed intervention, reports how far apart their predicted
    diagnostics are. The framework would compute the numbers, which keeps
    invariant 2, and it gives agents a reason to design contrasts.
- **Belief timeline.** Elicit it explicitly, because the record cannot recover
  it otherwise. Require a structured `belief` entry (favoured structure,
  named rivals, confidence) before each experiment and at submit. Separate the
  `predict` structure field from free text, and ask for a prediction under at
  least one rival.
- **Stopping and search breadth.** The prompt's simplicity framing and the
  examples (identity first, depth ≤ 2) plausibly anchor the search. Test
  variants of the prompt (link-neutral ordering, a nested example) before
  attributing shallow search to the model. Record why the agent stopped.
- **Void policy.** Decide whether a rejected, unexecuted foreign call voids a
  run, and log the decision in LOG.md.
- **Instrument follow-ups (outside behaviour).**
  - Agents report the coordinate search getting stuck on gated or periodic
    structures. Check whether scoring's refit of the submission has the same
    problem.
  - dev-018's periodic term is not BIC-identifiable at n ≈ 2000.
- **Anonymisation.** If P4 means to isolate the effect of naming, vary the
  time scale separately from the channel and diagnostic names.

# Backlog

Per SPEC §13: the design is frozen. New ideas enter here with a rationale and a
note on which frozen decision they would touch. Architecture changes only on a
demonstrated contradiction — a case where two frozen decisions cannot both be
satisfied, documented with the failing test.

This is not the build backlog. The ordered build backlog is SPEC §11, and its
live cursor comes from `scripts/status.py`. Decisions already taken go in
`docs/DECISIONS.md`.

Entry format:

```
## Short title

**Idea.** What it is.
**Rationale.** Why it might be worth doing.
**Touches.** Which frozen decision it would change, or "none".
```

---

## Known backlog at freeze

Recorded in SPEC §13, restated here so this file is not empty: full
likelihood-free engine, two-step-lookahead BOED, remaining variants and
baselines, the 104-scenario benchmark, grammar sensitivity analysis (R5), the
Rust market environment, real-data grounding, identifiability work,
multi-provider evaluation, independent human study.

---

## Learned experiment-selection policy (RL over the operation set)

**Idea.** A system variant whose experiment selection is a policy learned by
reinforcement learning over the §4.4 operation set, in place of one-step-greedy
BOED. State is the hypothesis graph plus registered results; actions are the
typed operations; reward is a scalarisation of the §8 dimensions. It enters the
matrix as an additional arm beside V1 and V7, not as a replacement for either.

**Rationale.** R3 asks whether there is headroom above two-step-lookahead BOED
on collective-value sequences. A learned policy is the direct instrument for
answering that: if it cannot beat lookahead after training on DEV, the headroom
is small and R3 has an answer worth having. The machinery RL usually has to be
retrofitted with is already present — bit-exact determinism gives replayable
episodes, ground truth is an edit set rather than a label so return is
computable without a human, and the append-only content-addressed registry is
already a trajectory store.

Three frictions, none fatal:

- §8 forbids collapsing D1–D6 into a single number, and RL needs a scalar. The
  prohibition is on *reporting*, so a training-only scalarisation is
  admissible, but it becomes a versioned inspectable artefact with consequences
  for every learned policy, on the same footing as the prefix code, and must be
  justified in the write-up rather than tuned.
- Fitting and reporting on the same pool. Train on DEV, report on HOLDOUT and
  TEST. Note that A14 does not catch this: reusing DEV is not a partition
  crossing, so it stays a discipline question rather than something the
  analyser can enforce.
- Determinism. Policy weights must enter the content hash alongside the env,
  config, metric and data versions, and action sampling must run through the
  explicitly passed seeded generators.

It does not touch F7. Reward is framework-computed and the policy still emits
only structure, so no agent-reachable path writes a number.

**Touches.** F5, which gives experiment selection within a fixed space to
conventional methods. Also §9's matrix, which gains an arm, and F13, since the
preregistered primary contrast is not about this and must not be restated
around it.

## RL fine-tuning of the hypothesis proposal layer

**Idea.** Fine-tune the LLM proposal layer of V7 by reinforcement learning
against framework-computed scores on generated scenarios, rather than using it
zero-shot or few-shot.

**Rationale.** Recorded mainly to state the objection, because the idea is
otherwise the obvious next step after the learned-selection entry above and
will keep resurfacing.

R1 asks whether the LLM is reasoning or retrieving, and names retrieval as the
most likely outcome. Training the proposal layer on scenarios drawn from our
own edit grammar manufactures a retriever over that grammar and forecloses the
question on the slice: a trained V7 beating B4 would demonstrate that we
successfully trained it, not that generation beats retrieval. Any such arm
would have to be evaluated exclusively on structure outside the grammar it was
trained against, which is a much smaller evaluable surface than S1–S12, and
S11 is the only slice scenario that qualifies.

Sequencing for both entries: not possible before item 12, and belongs after
item 15. The baselines and the first experiment matrix are what establish
whether there is headroom worth learning into, and running either variant
earlier reintroduces exactly the confound between agent performance and
framework immaturity that items 2–11 exist to prevent.

**Touches.** No frozen decision directly. It compromises R2 by adding a
training axis to the memory ablation, and it makes R1 unanswerable on any
scenario whose ground truth lies inside `agent_grammar`.
## DONE (2026-08-16) — A mark-arrival cross-diagnostic, so S11 Stage A is
## detectable

**Closed.** `size_gap_correlation` joined the §4.3 catalogue and
`METRIC_VERSION` moved 1.1.0 -> 1.2.0 with it, which is the versioning event
this entry said it would be. The statistic is the first of the two the entry
named — a mark against the inter-arrival gap that follows it — chosen over the
windowed cross-correlation on measurement: 3.42 sd of separation against 2.42
for a high-versus-low mark gap ratio, with plain Hawkes reading -0.0012 against
S11's -0.134.

The user took the §4.3 judgement the entry said could not be settled by
implementation alone. `docs/DECISIONS.md` carries the pilot, the bin-edge
quantiles, and the re-measured A9. Note that SPEC §12 criterion 4 still needs
re-specifying and this did not do it: B1 holds only the null and fires on 7/12
scenarios, so "at a rate at least matching B1" is the wrong yardstick whatever
the catalogue contains. The original entry follows, unedited.


**Idea.** Add one diagnostic to the §4.3 catalogue that measures the *joint*
behaviour of the mark and arrival components — the correlation between a mark's
size and the inter-arrival gap that follows it is the natural choice, and the
lag-1 cross-correlation of sizes against subsequent counts is the windowed
version of the same thing. Every existing catalogue entry is a statistic of one
component in isolation.

**Rationale.** Measured under item 6: the posterior predictive check detects
`SIZE_EXCITATION`, scenario S11's out-of-library mechanism, in **3.0%** of 100
scenarios at alpha 0.05 — below the test's own nominal size. It detects a size-
component mixture in 100%. The difference is not the check; it is that S11's
mechanism is a Hawkes process whose marks gate the excitation, calibrated to the
same operating point as the four it hides among, and no statistic of arrivals
alone can separate the two. The numbers are in `docs/DECISIONS.md`.

This is load-bearing rather than cosmetic. SPEC §4.6 requirement 1 is "detect
inadequacy (S11 Stage A, via PPC)". SPEC §12 criterion 4 asks V7 to detect S11
at a rate at least matching B1, and B1 *is* the PPC, so the exit criterion is
currently cleared by any system that does nothing. Worse, SPEC §9's preregistered
primary contrast is explicitly "conditional on inadequacy detection", so with a
3% detection rate Stage B would be estimated on about three runs in a hundred —
the contrast the whole slice is built around would have no power for a reason
that has nothing to do with the LLM.

The obvious alternative — dropping the conditional and reporting Stage B
unconditionally — is worse, because F6 exists precisely to stop detection and
extension quality being reported combined.

**Touches.** SPEC §4.3, the frozen diagnostic catalogue for the slice. It also
changes `MetricRegistry.version` and therefore the content address of every
experiment registered against the catalogue, so it is a versioning event and not
an addition. It touches no other frozen decision: F5 keeps inadequacy *detection*
with conventional methods, and a cross-component statistic is still conventional.

**Sequencing.** Before item 9, since B1's measured Stage A rate is what items 12
and 15 are compared against, and a baseline measured under the current catalogue
would have to be re-run. It cannot be settled by implementation alone — whether
this counts as a demonstrated contradiction between §4.6 and §4.3, or as a
catalogue that was simply incomplete, is a judgement about the spec.

---

**Re-measured 2026-08-04, at item 12. The problem has inverted, and it is now
worse.** The other two entries above are closed, and neither helped here.

Combining evidence across experiments lifted B1's detection over S1-S12 from 2/12
to 7/12, and B1 now *does* fire on S11. That does not rescue criterion 4, because
B1 holds only the null and therefore fires on anything that is not the null --
S1, S5, S7, S8, S10, S11 and S12 alike. Its S11 detection is a statement about
holding a trivially inadequate space, not about out-of-library sensitivity.

Meanwhile V7, which holds the closed set, was built at item 12 and measured:
**its combined p-value on S11 is 0.5273 against an alpha of 0.05**, and A9 puts
the check's power against `SIZE_EXCITATION` with the closed set entertained at
**0.000**. Hawkes covers S11's mechanism on every arrival-only statistic.

So criterion 4 has gone from "clearable by a system that does nothing" to
**unpassable by any system holding an adequate-looking closed set**, and SPEC §9's
preregistered primary contrast -- "On S11 Stage B, conditional on inadequacy
detection" -- conditions on an event that occurred **zero times in twelve**. The
contrast is not weak; it is undefined.

This is the demonstrated contradiction SPEC §13 requires. §4.6 requirement 1 asks
that S11 Stage A be detectable via the PPC; §4.2 calibrates S11's mechanism to be
indistinguishable from Hawkes under every arrival statistic; §4.3 offers no other
kind of statistic. All three cannot hold. The failing measurement is in
`docs/DECISIONS.md` under "V7 exists, and SPEC §9's primary contrast cannot be
run", and `tests/test_hybrid.py` exercises the path.

**It cannot be fixed by restructuring V7.** Checking after every experiment
rather than at the half-budget point gives more chances at a test with no power.
0.000 is a property of the catalogue and the calibration, not of when the check
is taken.

**The decision is between two frozen documents** and is not taken here: either
§4.3 gains this diagnostic, or §9's contrast is re-specified. A third option --
having V7 propose unconditionally -- is rejected, because it would report
extension quality on runs where inadequacy was never detected, which is precisely
what F6 forbids.

## DONE (2026-08-04, item 12 prerequisite) — Combine posterior-predictive
## evidence across experiments, instead of min-p

**Closed.** Implemented as the harmonic mean p-value scaled by `1 + ln(n)`,
not Fisher: Fisher was measured and *failed*, taking the realised size to
0.130 and power against a detectable defect from 1.000 to 0.070, because the
evidence on this slice is concentrated in one experiment of five. B1's
detection over S1-S12 went from 2/12 to 7/12 and no longer degrades with
budget. Numbers and the rejected alternatives are in `docs/DECISIONS.md`.
The original entry follows, unedited.


**Idea.** Replace the posterior predictive check's multiplicity handling. It
currently reports the *minimum* per-experiment tail probability under a Sidak
correction for the number of experiments. Combine the per-experiment
probabilities into a single statistic instead — Fisher's method and the
Stouffer weighted-Z are the two obvious candidates — so that several experiments
agreeing counts as evidence rather than as several chances to be wrong.

**Rationale.** Measured under item 9, on B1, which *is* the check. B1 holds only
the null hypothesis, so on S1–S7 its hypothesis space is inadequate by
construction and the check ought to say so. Detection instead moves the wrong way
with the budget:

| budget | min per-experiment p | corrected p | detects? |
|---|---|---|---|
| 1 | 0.0134 | 0.0134 | yes |
| 2 | 0.0133 | 0.0265 | yes |
| 4 | 0.0133 | 0.0523 | no |
| 8 | 0.0133 | 0.1019 | no |
| 16 | 0.0133 | 0.1935 | no |

The per-experiment signal never changes. Only the penalty grows, and it grows
faster than the evidence it is applied to, so **a system that runs more
experiments detects less**. At the slice's standard budget of eight, B1's
detection rate over S1–S10 is 2/10, and the two it catches are explained rather
than encouraging: S8 has a per-experiment probability of 0.0030, strong enough to
survive the correction, and S10 is caught *because it is budget-starved* — two
experiments carry a far smaller penalty than eight. The scenario with the least
evidence is the only arrival-mechanism scenario B1 flags.

This compounds with the mark-arrival cross-diagnostic entry above rather than
duplicating it. That entry is about *which diagnostics exist* and reports a 3%
detection rate on S11's out-of-library mechanism; this one is about *how evidence
across experiments is combined* and depresses detection on every scenario at
every budget above two. Fixing either alone leaves the other in place, and both
land on SPEC §12 criterion 4 — "detects inadequacy on S11 at a rate at least
matching B1" — which a near-zero floor makes clearable by a system that does
nothing.

Two frictions. Fisher and Stouffer both assume independence across the combined
tests, which holds here by construction — each experiment is drawn under its own
seed, which is what already lets the engine multiply likelihoods — but it stops
holding the moment repeated measurements of one design are combined, and B1
repeats designs once its rotation exhausts the design set. And the tail
probabilities are discrete, coming from a binned outcome space, so a combined
statistic calibrated against a continuous null will be conservative; how
conservative is measurable and should be measured rather than assumed.

**Touches.** No frozen decision in SPEC. `inference/ppc.py` is item 6's and is
gated by A9, which passes today and would need re-measuring against whatever
replaces min-p — A9 states a detection criterion, so the change is inside its
remit rather than around it. `PPCResult.p_value` keeps its meaning as "the
multiplicity-corrected probability", so no caller changes. It does not touch F6:
detection stays a conventional, non-agentic judgement.

**Sequencing.** Before item 12, and ideally before any figure quoting B1's rate
is published. Item 9's baselines can be re-run cheaply — the gate is 10.8 seconds
warm — so re-measuring after a change costs almost nothing, and no scenario data
would need rebuilding.

## DONE (2026-08-04, item 12 prerequisite) — A prediction per offered design

**Closed.** `systems.base.table_predictions` derives one per design. A23's
coverage went from 90.385% to 100.000% on an identical 2288-claim population;
all 220 referrals were the predicted cause and all of them disappeared. The
original entry follows, unedited.


**Idea.** When a system proposes a hypothesis without supplying predictions,
`systems.base.table_prediction` derives exactly one, under the scenario's *first*
design. Derive one per design the scenario offers instead, so a hypothesis says
what it expects of every experiment that could be run against it rather than of
one arbitrary experiment.

**Rationale.** Measured under item 10, on A23. The verifier grades a claim that
carries no measured effect by evaluating the subject's predictions on the cited
experiments — item 5's condition algebra decides that exactly. A prediction made
under a design the system never ran bears on nothing, so the claim is *referred*:
the verifier correctly reports that it cannot decide mechanically.

All 180 of A23's referrals are this, and all of them are V1 on S5, S6 and S10 —
BOED simply does not select the first design on those three scenarios. That is
22.5% of V1's claims and 0% of every other baseline's, and it puts A23's measured
coverage at **90.5%** against a 90% threshold. One more scenario where V1 avoids
the first design takes the gate below its bar for a reason that is about which
design a prediction was attached to, not about the verifier.

A hypothesis is a statement about the whole design space, and attaching its
falsifiability to one arbitrary member of that space is the actual error. The
current behaviour also quietly weakens A16's guarantee in practice: a hypothesis
is refutable, but only by an experiment nobody may run.

Two frictions. Every prediction is validated at proposal time, so *n* designs
means *n* validations and *n* table reads per proposal — cheap, but it is inside
the loop item 9's beam search runs fifty times per scenario. And a design whose
outcome space has more than one axis has no table-derived prediction at all
(`_cell_condition` raises for it, by design, since a `Prediction` names a single
diagnostic), so the per-design derivation must skip those rather than fail.

**Touches.** No frozen decision. `systems/base.py` is item 9's and
`hypothesis/graph.py` item 5's; A16 and A18 both re-run unchanged, since more
predictions per hypothesis is more falsifiability and not less. A23's figure
must be re-measured after the change, and the current 90.5% is the baseline it
would be compared against.

**Sequencing.** Before item 12. A23 is re-measured against real agent claims
there, and doing this first means that measurement is not confounded by an
artefact of where predictions were attached.

---

## STRUCK (2026-08-16, refuted by measurement) — Relevance clause 3 fires on
## every pair, so A20 is not testing what it reads as

**Struck, not done.** The claim below is false of A20 and always was. Disabling
the `SCOPE_OVERLAP` arm of `verify/relevance.py::clauses` leaves **17 of 102**
cases unsurfaced, all of them `scope_overlap/*`; the other 85 are carried by
clauses 1, 2, 4, 5 and 6 independently. A20's cases are *constructed* and already
give each omitted record an `env_version` of `other/{variant}` — written at item
10 on 2026-08-04, five days before this entry — so the fix it asks for is
already in the tree. `docs/DECISIONS.md` records the mutation run.

What the entry gets right is the behaviour on *real slice runs*, where clause 3
does fire for every pair. That is not a defect: an experiment from the same
environment version is relevant under §7.1, and conservative evidence
completeness is what the clause is for. The original entry follows, unedited.


**Idea.** Either narrow SPEC §7.1 clause 3's environment-version axis, or make
A20's constructed cases vary scope so the other clauses are the ones deciding.
Not a code fix as it stands — the current behaviour is what §7.1 says.

**Rationale.** `scopes_overlap` returns true as soon as two scopes share an
`env_version` (`verify/relevance.py`), which is clause 3's first axis and exactly
what the specification asks for. But an investigation runs in one environment at
one version, so `EvidenceIndex.from_history` stamps every record with the single
`executor.scope()`, and `claims_from_run` gives every claim that same scope.
Clause 3 therefore fires for every (claim, record) pair on every real slice run,
which makes `uncited_relevant` equal to "every experiment not cited" and means
A20's 100-case guarantee is currently carried by one clause that cannot fail
rather than by the relevance relation as a whole.

This is not wrong and nothing is mis-adjudicated: an experiment from the same
environment version *is* relevant under §7.1, and evidence completeness is
correctly conservative. What it costs is discriminating power in the gate — A20
would still pass if clauses 1, 2, 4, 5 and 6 were all broken, and it is the only
place they are measured together. Item 10 already noted that no SPEC §5 baseline
declares `targets`, so clauses 1 and 6 never fire on slice runs either; between
the two, A20 is exercising clause 3 and a little of 2 and 5.

**Touches.** SPEC §7.1 clause 3, if the axis is narrowed — that is a frozen
decision and would need a demonstrated contradiction, which this is not. The
version that touches nothing frozen is the second: leave the relation alone and
give A20 cases whose scopes genuinely differ, so each clause is measured on its
own. Prefer that one.

**Sequencing.** Before item 15's matrix, since A20's figure is reported there,
and cheap either way — it is test data, not framework code.

---

## Model tier as a preregistered evaluation axis

**Idea.** Run the matrix's V7 arm at more than one model tier — Opus, Sonnet,
Haiku — on identical scenarios, seeds and grammar, and report the tiers side by
side as an additional axis. Not a substitution for cost reasons: the same
scenarios, the same everything else, with the tier declared in advance.

**Rationale.** The framework's central claim is a division of labour: conventional
methods own posterior updating, statistical computation, experiment selection and
inadequacy detection, and the LLM owns only hypothesis-space construction. If that
division does what it is meant to, the burden it leaves on the model is narrow —
choose which structure the residual points at — and a smaller model may stay
competitive at it. That is a direct, cheap test of the thesis rather than a
detail about vendors, and the answer is interesting in both directions: if a
cheap tier keeps up, the constraint structure is doing the work; if it does not,
the structural proposal step is where the capability is actually spent.

The machinery is already there. The tier reaches the transcript address through
`Provider.model`, so tiers cannot contaminate each other's recorded calls, and
`Provider.settings` keeps effort separate from tier — which matters, because the
two are otherwise easy to confound in a comparison.

Two cautions if it is run. Effort must be held fixed across tiers or swept
deliberately, since a cheap tier at high effort against an expensive one at low
effort measures nothing. And the tiers' rate limits differ, so a tier that has to
be recorded across several days is not thereby a slower system — wall-clock from a
recording run is not a result.

**Touches.** None. It is an additional arm beside V1 and V7, on the same footing
as the learned-policy entry above, and needs no change to a frozen decision.

**Sequencing.** After item 15's first matrix, not before. The single-tier result
is the baseline the comparison is against, and running both at once would triple
the recording cost of a matrix whose subscription rate-limit budget is not yet
measured.

---

## DONE (2026-08-17, item 15) — A resumable campaign driver, which item 15
## needed and did not have

**Done, with one departure from what this entry asked for.** The driver is
`sciagent/eval/matrix.py`; §9's 56 cells are `environments/pointproc/matrix.py`,
beside the environment rather than in the framework, since invariant 1 means
`sciagent` may not name `V7` or `S11`. Both properties this entry demanded hold
and are tested: resume means *skip what is addressed*, and seeds come from
`(scenario seed, replicate index)` rather than from iteration order. The trap it
asked to be written down — a re-run with changed inputs being a new address with
the old row still present — is `test_a_metric_version_bump_is_a_new_address`.

**The departure.** This entry said "keyed on the registry's content addresses",
which reads as *rows in `ExperimentStore`*. Cells go in a **sibling** store
instead, `registry/ledger.py::CampaignLedger`, on the same `ExperimentKey`
address and the same three append-only enforcement layers. The reason is that
`ExperimentStore.append` refuses non-finite results, and `-inf` is an ordinary
cell reading — D2 when the candidate ruled out something that happens, and
`log_score` whenever the truth got zero mass, which is B1's every run.
`docs/DECISIONS.md` carries the argument and the rejected alternative.

**Not closed by this**, and none of it is waiting on the driver: the platform
precondition, the unmeasured subscription rate limits, and the D1–D6 report
layer, which is the next entry below and stays open. The original entry follows,
unedited.


**Idea.** A driver that runs SPEC §9's matrix cell by cell, keyed on the
registry's content addresses: before running a cell, ask whether its address is
already registered, and skip it if so. Checkpointing falls out of that rather
than being bolted on. `eval/campaign.py` says in its module docstring that the
§9 matrix "is item 15's and is not here"; this is the thing that is not here.

**Rationale.** 56 cells at twenty seeds is ~1,120 investigations and days of
compute, which is longer than any session — and longer than any recording run
that has to fit inside subscription rate caps. A matrix that can only be run in
one sitting cannot be run at all. Resuming from the registry rather than from a
session's notes is also the only version that survives a session ending badly,
which `/handoff` exists because sessions do.

Two properties it must have, both from the invariants rather than from taste.
Invariant 4 makes a re-run an *append* with a new content address, never a
correction of the old row — so "resume" means "skip what is addressed", not
"overwrite what looks stale". And invariant 3 means every seed reaches the cell
through an explicitly passed generator, so the driver may not derive seeds from
iteration order.

One trap worth writing down before somebody hits it: a cell that needs re-running
because its *inputs* changed is a new address, so the old row stays and both are
in the registry. Any report over the matrix therefore has to select rows by
address rather than assume one row per cell.

**Touches.** No frozen decision. It is item 15's implementation, not a change to
what item 15 is.

## A report layer for D1–D6, since §8 forbids the obvious one

**Idea.** Render the matrix as a vector table: D1 through D6 per system per
scenario, with intervals, and no total column. Nothing renders one today —
`eval/scoring.py` computes `dimension_vector`, and the deliberate absence of a
`total` field on it is asserted by a test.

**Rationale.** The numbers are useless to a reader in the form they are
computed, and the natural rendering is the forbidden one. §8 says the six
dimensions are "reported separately, never collapsed into one number", and item
12's S11 table is the demonstration of why: Hawkes scores 1.50 on D1 — worse
than proposing nothing, which scores 1.00 — and 0.960 on D3 against a best rival
of 0.698. A mean of the six would report that as mediocre. A ranking column
would report it as a loss. Both would be wrong about the one scenario the slice
is built around.

So the report layer is where the prohibition either holds or quietly fails, and
building it deliberately is how it holds. It should also carry the two things
the matrix skill says must appear wherever these numbers are reported: which
platform every cell ran on, and that slice results are exploratory by
construction.

**Touches.** None. §8 as written; this implements the reporting discipline it
already mandates.

## Grammar sensitivity (R5) deserves promoting out of the freeze list

**Idea.** Run the ranking under coarse and fine variants of the edit grammar and
report how much it moves. It is named in SPEC §13's known-backlog-at-freeze list
as one item among ten, and this entry argues it is not one item among ten.

**Rationale.** R5 asks how much scores depend on the edit grammar and notes the
answer "could be large enough to undermine any ranking". Item 12 measured
something that makes this concrete rather than hypothetical: on S11, a plain
Hawkes proposal is 1.50 from the truth under `grammar.distance` while the *null*
is 1.00, so a system proposing the mechanism that reproduces S11's interventional
behaviour almost exactly scores worse structurally than one proposing nothing.
D1 is `grammar.distance` and nothing else, so that number is a statement about
the grammar, not about the world.

SPEC §0 already concedes the general point — the grammar "does not eliminate
author bias", it makes it "formalised, inspectable, versioned" — and says the
actual improvement is that it "can be varied in sensitivity analysis". That
sentence is a promise R5 is the only thing that keeps. With D1 reported in every
cell of the matrix, the promise is about to be load-bearing.

**Touches.** No frozen decision. It varies the grammar deliberately and reports
the spread, which is what §0 says the grammar's versioning is for. Sequencing:
after item 15, since the first matrix is the ranking whose sensitivity is being
measured, and running it earlier means measuring the sensitivity of nothing.

## A Stage A battery, because one probe only looks in one direction

**Idea.** Give Stage A more than one adequacy probe, and combine the readings by
a rule that does not dilute — a minimum-p with an explicit correction, or a
per-direction verdict reported separately — rather than folding them into one
harmonic mean.

**Rationale.** Measured 2026-08-16 at 100 scenarios per arm, alpha 0.05:

| misspecification | all templates | probe only |
|---|---|---|
| `size_excitation` (S11's mechanism) | 52% | **90%** |
| `size_mixture` (a component no closed-set hypothesis touches) | 100% | **3%** |

Scoping the check to `query:size_gap_correlation` nearly doubles power against
the mechanism the probe was built for, and costs almost all of it against a
misspecification in a direction the probe does not measure. Both effects come
from the same combination rule: `1 + ln(n)` dilution is why eight readings do
worse than one on `size_excitation`, and one reading is why the eighth is missed
on `size_mixture`.

This is already visible in the slice rather than hypothetical. V7's adequacy
check fires on S11 alone across the twelve, and specifically not on S8, whose
compound truth is outside its space and which the full-record check flags at
0.0396. S8's misspecification is a size mixture, so the 3% row is the reason.
Every Stage A detection figure in the repository is currently a statement about
the mark-arrival direction alone.

Doing nothing is defensible for the slice — §12 criterion 4 is about S11 and a
probe aimed at S11 answers it — but it stops being defensible at the 104-scenario
benchmark, where the misspecification directions are not known in advance and a
single-direction gate would report a detection rate that means nothing.

**Touches.** SPEC §4.6, which describes Stage A as a check rather than a battery,
and would need the combination rule stated. Not a change to make on the way past
some other work: the rule is the whole design, and choosing it by convenience
while shipping something else is how the `1 + ln(n)` dilution got in.

## SPEC §12 criterion 4 is incoherent, and fixing it needs a decision made cold

**Idea.** Re-specify criterion 4 — currently *"Detects inadequacy on S11 at a
rate at least matching B1"* — as a power-against-size comparison on a named
check, and decide what "a system detected inadequacy" means for a system that
never consults the check.

**Rationale.** Measured 2026-08-16. The criterion names no check, and the two
that exist disagree:

| | fires on |
|---|---|
| full-record `ppc` | B1 on 5 of 12 (S1, S5, S8, S10, S11); V7 on none |
| Stage A adequacy probe | B1 on S11 alone (0.0294); V7 on S11 alone (0.0112) |

Three separate problems, none of which the wording survives:

1. **It compares different checks.** B1 holds no proposal layer, so it never
   calls `Investigation.ppc()`; its "detection" can only be read off a run,
   while V7's is a gate it acts on. Reading one arm's gate against the other
   arm's full-record summary is not a comparison.
2. **B1's full-record rate is a multiplicity artefact.** B1 holds only the null,
   so its space is inadequate on eleven of twelve by construction, and the rate
   comes from eight experiments agreeing rather than from adequacy detection. A
   bar set there rewards firing indiscriminately.
3. **There is no false-positive term**, so it measures size rather than power.

**And the field a fixed criterion would read does not exist yet.** A
`ScenarioRun.adequacy` field was written on 2026-08-16 and withdrawn the same
day. The only place the harness can evaluate it uniformly across arms is after
`investigate` returns — which reads the *final* posterior, so a system that
successfully proposed a structure explaining the probe records as having failed
to detect, inverting the criterion. Measured on V7/S11: gated on p=0.0128,
end-of-run 0.0112. The deeper trouble is that the field conflates *is this space
adequate* (a property of space and scenario, arm-symmetric, harness-computable
before the run) with *did this system detect that it was not* (a property of
behaviour, undefined for B1). Any re-specification has to pick one and say which.

**Touches.** SPEC §12, an exit criterion, and therefore CLAUDE.md's invariant 6
in its surviving form: **this must not be decided in a session that has just
measured V7 against the candidate wording.** A draft replacement was written on
2026-08-16 and reverted unshipped for exactly that reason — an independent code
review and an invariant audit both flagged it, and they were right. The
measurements above are the input; the decision is not this session's to make,
and should be taken by someone who has not just watched V7 pass it.

## DONE (2026-08-16, before item 15) — Invariant 2 is held by convention where
## it should be held by construction

**Done as specified**, and the sequencing note below is why it went ahead of
item 15 rather than after it. `Investigation.engine` now returns
`sciagent.inference.view.EngineView`, which withholds `record`, `record_probe`,
`expand`, `ensure_structure` and `ppc`; `run_scenario` additionally reconciles
the engine's observations against the experiments the investigation charged for
and raises `EngineTamperError` on disagreement, so a system reaching an engine
by some other route is caught rather than scored. Guarded by
`TestTheEngineIsSealedAgainstTheSystem` in `tests/test_systems.py`, whose
poisoner reproduced the silent-scoring case before the fix.

Two channels named in `docs/DECISIONS.md` are **not** closed by this and remain
open: `Investigation.propose(predictions=...)`, and reconciliation of the probe
compartment.

**Idea.** Give `Investigation.engine` a read-only view instead of the live
`EmpiricalTableEngine`, so that recording evidence is a capability the harness
holds and a system cannot reach.

**Rationale.** Found by the `invariant-auditor` on 2026-08-16 and not acted on
in that session, deliberately — see **Touches**. `Investigation.engine`
(`src/sciagent/systems/base.py`) returns `self._engine` itself, and its docstring
says *"Read-only in effect: it writes the numbers."* That is a comment, and
CLAUDE.md's second invariant says in as many words: **enforce with runtime
assertions, not comments.**

The engine exposes two public mutating methods a system can therefore reach:

- `record(...)` — folds straight into `log_likelihood_total` and `posterior()`.
  A system calling it with a fabricated `DiagnosticVector` writes its own
  posterior. `_validated` checks only for a duplicate id and a matching
  template; nothing checks the result came from an execution.
- `record_probe(...)` — added 2026-08-16. Followed by
  `engine.ppc(experiments={own_id})` it bypasses `Investigation.ppc()`'s scoping
  to the framework-chosen `_stage_a` entirely, letting a system compute an
  adequacy verdict over evidence of its own choosing.

`_audit` in `campaign.py` cannot catch either, and this is the part worth
understanding: it re-derives the expected diagnosis from **the same engine
object** the system was handed. It detects a system whose *reported* numbers
disagree with the engine; it cannot detect a system that changed the engine
before returning, because then both sides agree.

**Latent, not active.** No shipped system calls either method — verified by
grep across `src/sciagent/systems/**`; every one of V1, V3, V4, V7, B1, B4 and
B5 touches the engine only through `.table`, `.observations`, `.live`, or by
passing it read-only to `boed.plan`. So this is a gap in enforcement, not a
defect in behaviour, and it long predates the session that found it: `record`
has been public and reachable since the engine existed. What 2026-08-16 did was
add a second method to the same unguarded surface, which is what drew the audit's
attention to it.

**Touches.** No frozen decision — invariant 2 already demands this and the code
merely fails to deliver it. It is here rather than done because the fix is a
capability boundary crossed by every system and every baseline, and bolting one
on at the end of an unrelated session is how the three defects that same review
caught got written in the first place. Sequencing: before item 15, since the
matrix is the first time these systems run at scale and a fabricated posterior
would be indistinguishable from a real one in the report.

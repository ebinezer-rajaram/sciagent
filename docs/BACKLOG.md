# Backlog

Per SPEC §13: the design is frozen. New ideas enter here with a rationale and a
note on which frozen decision they would touch. Architecture changes only on a
demonstrated contradiction — a case where two frozen decisions cannot both be
satisfied, documented with the failing test.

Most of this file is ideas. Some of it is build work, and the difference is one
field: **an entry carrying a `**Gate.**` is tracked by `scripts/status.py` as
build work, and everything else here is not.** SPEC §11 is still the first
backlog and still goes first; the gated entries here are the backlog that
continues it, ordered by `**Rank.**`, and the live cursor falls through to them
once every §11 item is satisfied. Decisions already taken go in
`docs/DECISIONS.md`.

Gate numbers extend SPEC §6's namespace through this file rather than forking
it — A1–A24 are declared there, A25 onward here, and `status.py` refuses a
number claimed twice. SPEC itself stays frozen: §13 puts new work in this file,
so nothing here is a reason to edit it.

Entry format. The first three fields are all an idea needs:

```
## Short title

**Idea.** What it is.
**Rationale.** Why it might be worth doing.
**Touches.** Which frozen decision it would change, or "none".
```

Adding the next two promotes it to build work, and both are then required:

```
**Gate.** `test_aNN_named_for_its_criterion` — what it must establish.
**Rank.** N          — position in the build order; no two entries share one.
**Held.** <blocker>  — optional: waiting on a decision, not on code. The
                       cursor names it and passes over it.
**Cost.** S/M/L/XL   — prose, not read by anything.
```

An entry stays in place when it lands: prefix the heading with
`DONE (date) —`, or `STRUCK (date, why) —` if it was withdrawn, and leave the
fields alone. The report marks it closed and the cursor moves past it. The two
markers differ in one machine-read way: a `DONE` entry's gate is still a
criterion and keeps its row, while a `STRUCK` one's gate line survives only as
a record of what was proposed — nothing will satisfy it, so it leaves the
namespace and its number is free again.

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

**Not closed by this**, and none of it is waiting on the driver: the unmeasured
subscription rate limits. The D1–D6 report layer was the second, and is now done
— see the entry below. The platform precondition was the third and is
**discharged**: the project is pinned to Windows, so the matrix runs there in
full and the report states it. The original entry follows, unedited.


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

## DONE (2026-08-17, item 15) — A report layer for D1–D6, since §8 forbids the
## obvious one

**Done.** `sciagent/eval/report.py`, with `scripts/report_matrix.py` as a thin
CLI in front of it. `summarise` aggregates ledger rows into `CellSummary` rows
carrying six `DimensionSummary` values; `render` prints them as one block per
cell with six dimension columns and no total column; `contrast` is §9's
preregistered question.

**The prohibition is held by three separate things, not by the docstring.** No
type in the module has a field or property whose name contains `total`,
`overall`, `rank`, `combined`, `aggregate` or `composite`, and a parametrised
test asserts that over `dataclasses.fields` and `vars` of each exported type
rather than trusting the prose. `DIMENSIONS` is a six-tuple, so a seventh column
would have to be added to it. And the rendered header is checked for the same
substrings.

**Two things this entry asked for and got, both refusals rather than defaults.**
The platform and the grammar version are required arguments, and `summarise`
raises without either — the ledger cannot supply the platform, because the
registry content-addresses with no platform term, so a default would invent an
answer. `--platform` and `--grammar` have no defaults on the CLI for the same
reason, and a test asserts that. The exploratory caveat is rendered
unconditionally.

**Three decisions the entry did not settle**, all in `docs/DECISIONS.md`:
intervals are normal 95% reusing `verify/numerical.py`'s `Z_TWO_SIDED` rather
than a bootstrap; they are **not** clipped to each dimension's support, because
clipping narrows an interval and would bias §12 criterion 5's non-overlap test
toward the claim; and non-finite readings are counted and excluded rather than
folded in, since one `-inf` on D2 would otherwise report twenty replicates as no
measurement.

**Also added:** `SPEC9_CONTRAST` in `environments/pointproc/matrix.py`, so
`Contrast.preregistered` is *derived* from a declaration rather than asserted by
whoever computes it. `Preregistration` (the type) is in the framework and the
instance is beside the environment — the same split as `Cell`/`SPEC9_CELLS`.

**Not closed by this.** No cell of the matrix has been run: the unmeasured
subscription rate limits stand, and were never waiting on this. The platform
precondition no longer stands — the project is pinned to Windows. The original
entry follows, unedited.


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

## DONE (2026-08-22, gate A29) — SPEC §12 criterion 4 is incoherent, and fixing
## it needs a decision made cold

**Discharged.** The decision was written up as `docs/OPEN-DECISIONS.md` §1 and
taken cold on 2026-08-21 as C1; gate A29 implemented it on 2026-08-22. What the
implementation then found — that C1 makes the criterion unfailable, because both
its clauses are comparisons and the probe is now arm-symmetric — is the entry at
the end of this file, not this one. This entry asked for a decision and got one.

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

**The write-up is done; the decision is not.** `docs/OPEN-DECISIONS.md` §1 sets
out the fork any fix has to take, three candidate wordings with what each would
make the criterion mean, and a recommendation. It was written 2026-08-18 by a
session that had not run V7, and it changes nothing.

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

## DONE (2026-08-18) — Finish the Stage A seed sweep, and give it somewhere
## to run

**Closed.** `scripts/stage_a_seed_sweep.py --only V4/S11 --seeds 20` ran on the
Windows desktop in **1243.2s**: **fired 20/20, 40 asks**, exactly the figure the
entry below deduced from V3. The seed sweep's 142 is now 142 measured rather
than 102 measured plus 40 derived, and the deduction's reasoning is corroborated
rather than merely sound.

**The second half is answered by the run rather than by a decision.** The entry
argues this class of work needs "a quiet machine, or a cloud session". It ran
here with VS Code, a browser and five `claude` processes resident, at **6.1 GB
free of 15.93** throughout and a 0.07 GB working set. So what defeated the three
earlier attempts was the specific 11-process / 1.0 GB condition, not the sweep's
intrinsic appetite — the cell needs headroom, not a dedicated machine, and
`--only` running one cell per process is what supplies it. No cloud session is
needed and none should be used: that route was already foreclosed, see below.

**One sentence of the entry below is stale and is left standing.** It says the
cloud route "collides with the platform precondition in `/matrix` —
cross-platform determinism is unverified". That was true when written on
2026-08-16. `40fd351` then pinned the project to Windows and `docs/DECISIONS.md`
2026-08-17 records the divergence as closed by pinning rather than by fixing, so
the collision is no longer a live tension but a settled prohibition. The original
entry follows, unedited.

**Idea.** Run `scripts/stage_a_seed_sweep.py --only V4/S11`, the one
model-bearing cell of eighteen that has never been executed, and while there,
decide where this class of run belongs. The sweep is ~300 investigations; the
three S11 cells are ~30 minutes each and the other fifteen finish in seconds.

**Rationale.** The call count that unblocked item 15's recording run — 142, in
`docs/DECISIONS.md` 2026-08-16 "the seed sweep" — is 102 measured plus 40
*deduced* for V4/S11. The deduction is sound and is argued there: `Hybrid`
consults the gate before `_extend` runs, `Memory` enters only through
`render_brief` inside `propose`, so V3 and V4 cannot fire differently on one
scenario and seed; both S12 arms fired at exactly seeds `[5, 7]` and both S8 arms
fired zero. So this is not a hole in the reasoning. It is the difference between
a number that was measured and a number that was derived, in a figure that is
about to authorise a recording run, and the file should not have to explain that
distinction twice.

The second half is the part with teeth. The sweep was attempted three times on a
15.9 GB Windows desktop with VS Code, Firefox and eleven `claude` processes
resident. Free memory reached 1.0 GB, numpy began failing 193 KiB allocations,
and one failure took the editor's own Claude Code process down with
`0xC0000409` — `majflt` in the hundreds at `cpu=0ms`, a machine thrashing rather
than computing. That is not a bug in the sweep and no amount of tuning it will
help: the work genuinely needs memory the desktop did not have. The options are
a quiet machine, or a cloud session, and the cloud route collides with the
platform precondition in `/matrix` — cross-platform determinism is unverified,
and `.cache/tables/` carries the divergence between machines. Worth settling
deliberately rather than at the moment somebody needs the answer.

**Touches.** No frozen decision. Completing a measurement and choosing where to
run it; neither changes what item 15 is.

## DONE (2026-08-22, gate A44) — Audit the proposal path's failure taxonomy,
## rather than extending it one crash at a time

**Idea.** Enumerate the exceptions reachable from `ProposalLayer.propose` and
`Investigation.propose`, decide for each whether it is a proposal *outcome* or a
fault that should stop a run, and pin the decision in tests. Then check
`Hybrid._propose_once` and `_admit` handle exactly that set.

**Rationale.** 2026-08-18 found **two** distinct escapes in one afternoon, both
at that seam, both discovered by a live campaign stopping rather than by a test:
`StructureNotMeasurableError` from the table refusing a structure (V7/S2/07), and
`InvalidEditError` from the grammar refusing a defect (V3/S11/09). Each was fixed
in its own entry in `docs/DECISIONS.md`. Neither was anticipated.

The instrument that finds them is the expensive one. Only an arm that proposes
structure outside the library can reach either, so 38 conventional cells over 760
replicates could not have; it took live LLM cells costing quota and roughly two
hours of compute. Two data points do not prove a third exists, but they do show
that the set was never enumerated and that discovery costs a stopped campaign
each time.

Two specific inputs the audit already has, from the `invariant-auditor` lenses
run on that change:

* `ProposalRecord.refused` conflates a model declining with a *transport* failure
  — `AgentSdkProvider` raises `ProviderError` for a refusal, for `max_tokens`,
  and for any failed session including an API error status, and all three break
  `_extend`. That is the same misattribution the `unmeasurable` tier was carved
  out to avoid, applied to quota state rather than to measurement limits.
* The break asymmetry is now four continuing outcomes against one breaking one,
  so `yield_fraction`'s denominator depends on which outcome arrives. Harmless at
  `max_proposals = 2`, where declining can only lower the ratio; at 3 or more a
  model unable to produce a second admission would score strictly higher by
  declining. Raising `max_proposals` is therefore a sharper change than it looks.

**One escape found and deliberately not fixed, 2026-08-18.** `CANDIDATE_FAULTS`
in `src/sciagent/inference/empirical.py` enumerates `ProgramError` members only,
but `simulate` reaches `EditGrammar.apply`, which raises `EditNotInGrammarError`
— a `GrammarError` — at `src/sciagent/core/edits.py:483` and `:487`. Nothing on
the path catches it, so it escapes `_admit` and stops a campaign. It is
unreachable today only because `agent_grammar() ⊆ edit_grammar()`, which is the
same "wiring, not a promise" argument that the sibling change in
`provider.py` explicitly rejected as a reason to leave a guard narrow.

It is left open rather than fixed because closing it needs a decision this entry
is the right place for and a one-line widening is the wrong way to take it: is a
grammar refusal *at apply time* a property of the candidate (so `"unmeasurable"`,
costing the proposal) or a divergence between two grammars that should stop the
run? The first reading widens `CANDIDATE_FAULTS` past the `ProgramError` family
its docstring scopes it to; the second adds it to the propagating class beside
transport. Decide it with the retiering below, not before.

**Partly done, 2026-08-18.** The enumeration exists and four guards found
narrower than the errors behind them are fixed — see `docs/DECISIONS.md`. What
remains is the *retiering*, and it is now known to be a scoring change rather
than a diagnostic one: `docs/OPEN-DECISIONS.md` §2 lists all twenty conditions
`"refused"` conflates, shows why adding a `ProposalRecord` field moves
`yield_fraction`'s denominator and so touches what §12 criterion 11 reads, and
recommends T3 — but recommends taking it *after* the R5 re-scoring question,
since a matrix that is re-run anyway makes the metric-version bump free.

**Decided 2026-08-21 — T3**, as this file's write-up recommended. Taken twice:
first as T2 on the premise that A40 bumps `METRIC_VERSION` and the bump is
therefore free, then corrected the same day when the A40 worktree showed the
premise false. `DIMENSION_VERSION` landed at A26 the day *after* the A40 entry
was written and exists so a dimension change need not move a column that
addresses the empirical tables; A40 re-derives on `dimensions` and `battery` and
bumps nothing. `yield_fraction` is an agency metric rather than one of §8's six,
so `DIMENSION_VERSION` would not carry it either — T2 would need the real bump,
measured at a 3m11s table rebuild on every machine, for a change touching no
estimator. **T1a survives** the correction: a condition that propagates was
never scored, so moving machine faults out of the tier moves no recorded number
and needs no bump. `max_proposals` stays at 2 until the break asymmetry is
settled in the same change, and the `EditNotInGrammarError` escape above is
decided with the retiering as this entry already asks. Both decisions and the
correction are in `docs/DECISIONS.md`.

What that leaves this entry to do is unchanged in shape and now has its answer:
keep `ProposalRecord`'s five fields, so `yield_fraction`'s denominator never
moves, and add the parallel non-scoring breakdown of causes beside it. That is
A44's gate.

**Touches.** No frozen decision. It is enumeration and test coverage over an
existing boundary. Sequencing: **not** mid-campaign, and not in a session that
has just watched a particular arm fail against a particular tier — the same
hazard the `unmeasurable` entry names and the §12 criterion 4 entry insists on.

**Constrained by A40, 2026-08-21.** This entry retiers the very catch the
constraint lands on. `SystemConfigurationError` is deliberately outside
`ProposalError` (`core/errors.py:357`) so a harness fault cannot be caught and
scored — the A40 worktree chose it there for exactly that reason. The retiering
must widen the catch by *kind*, never by moving up the hierarchy to
`ProposalError`, which would swallow `TranscriptMissError` and record a replay
miss as a refusal.

**Gate.** `test_a44_a_fault_is_not_scored_as_a_refusal` — a constructed
failure of each enumerated kind reaches exactly one cause in the parallel
non-scoring breakdown: a model declining reads as a refusal, a dead session as
transport, and a fault of the machine propagates rather than reaching
`ProposalRecord` at all; `EditNotInGrammarError` from `apply` lands where this
entry decides rather than escaping `_admit`; and `ProposalRecord`'s five fields
are untouched, pinned by the recorded campaign's `yield_fraction` coming out
bit-identical across the change. No `METRIC_VERSION` bump: that is what T3 buys
and what the gate must not quietly spend.

**Landed 2026-08-22, with one clause substituted.** The `yield_fraction` half of
the third clause cannot be evaluated: `.cache/campaign/spec9.db`'s 1,120 readings
carry sixteen keys and no proposal-outcome field, so the recorded campaign's
`yield_fraction` cannot come out bit-identical because it cannot come out at all
— A31's omission, seen from a third side. Substituted with literal fractions
under the pre-change taxonomy plus retagging invariance across every
refused-tier cause; the `METRIC_VERSION` half was recovered exactly, against the
digest all 1,120 rows carry. `docs/DECISIONS.md` (2026-08-22) records both, and
the two sub-decisions this entry deferred to the change.
**Rank.** 6
**Cost.** M. Constructed cases throughout; no live call is needed to grade it.

## Four defects in `scripts/rate_limit_pilot.py`'s reporting, found by review
## after its measurements were already taken

**Idea.** Fix four things `/code-review` found in the pilot on 2026-08-18, none
of which affects the numbers it already produced:

* `ok` gates on `cost_usd > 0.0`, so a session that reports no cost discards the
  whole measurement *after* the quota was spent. Latent rather than theoretical —
  the measured median is \$0.098, but `apiKeySource` is `"none"` and the SDK's
  costing is what would return zero.
* Rejected calls are recorded with `address=""` although the transcript *was*
  stored, so the report's "inspect these" rows cannot be joined to the saved
  corpus — which is the one thing that listing is for.
* Exit 1 on any malformed draft is indistinguishable from the provider-failure
  stop the script exists to detect. Two very different events, one status.
* `work_list`'s docstring calls the screening "cheap: a gate evaluation per
  candidate". `at_proposal_time` spends half the scenario budget through BOED,
  so a 20-call run screens ~240 half-investigations in one process — on a
  machine `docs/DECISIONS.md` records dying at around 300.

**Rationale.** Recorded rather than fixed, deliberately. The script landed on
2026-08-18 as the artefact that produced the pilot's measurements, and the reason
it landed unmodified is that rewriting it changes what those numbers were
produced by. Only statements the same day's changes made *false* were corrected,
plus the seed-grid misdescription. These four are logic, and re-testing logic
here costs live model calls, so a session that is not mid-campaign should do it
and re-run the pilot to confirm.

**Touches.** No frozen decision. A script, not the framework.

---

The entries below open with the sixteen appended 2026-08-18 by the deep
adversarial review (`docs/REVIEW-2026-08-18.md`, which carries the evidence, the
ranked order and the costed experiment designs), and continue with the ones
raised since by review of the work that discharged them. Each carries a named
acceptance criterion under **Gate.** (in the `test_aNN` form `scripts/status.py`
tracks), a **Rank.** giving its place in the build order, and an effort estimate
under its cost line. Gate numbers from A25 up extend §6's namespace through this
file, per SPEC §13; none of them grades an already-built system: every gate
below binds framework apparatus on constructed cases, and wherever a change
would move a number a recorded campaign already reported, the entry says so
and routes it through a metric-version bump decided cold.

## DONE (2026-08-26, gate A25) — Real-data grounding on the SCEDC QTM catalog

**Idea.** Ground the framework on the QTM catalog (Ross et al. 2019, Science):
1.81M template-matched Southern California events 2008–2017, two text files
(192MB + 95MB) verified byte-frozen since 2019-04-15. Snapshot and hash it as
`DATA_VERSION`; build the ingestion pipeline (magnitude cut, deterministic tie
rule, rescale to mean gap 1.0, disjoint 512-event segments keyed by seed
index) as versioned environment code beside `pointproc`; declare short-term
aftershock incompleteness as an S12-style unscored nuisance. Two preregistered
tracks, never mixed: semi-synthetic (recalibrate the reference to QTM's
operating point, author edits, simulate — full D1–D6) and found-data (the
ETAS consensus edit is the D1 surrogate; D2/D4/D6 intact, D5 observational
only, D3 only as the found battery of in-network M5+ events).

**Rationale.** The framework has only ever seen defects it wrote itself. QTM
is the one surveyed corpus that is natively a frozen citable snapshot *and*
whose century-deep consensus mechanism — ETAS magnitude-gated triggering, fit
to this catalog specifically (Moutote 2021; van den Ende & Ampuero 2020) — is
exactly one library edit: `AddDependency(size→arrival)`, `mechanisms.py:146`,
S11's out-of-library mechanism, sole detector `size_gap_correlation`. The
transfer test writes itself: does the system detect inadequacy on data where
seismology knows the truth is the S11 edit, and does Stage B propose it? R4
is answerable without interventions as rank preservation under D2. Known
risks, adversarially verified: no formal licence text (get written SCEDC
confirmation before redistributing the snapshot); template-matching false
detections cluster after large marks — the same signature as the consensus
edit — so the detector's reading must be bounded on semi-synthetic ETAS with
and without an artifact model; regional pooling needs a polygon sensitivity
arm. Runner-up if QTM falls through: Binance spot aggTrades monthly zips
(exact time/size/sign schema, checksummed immutable files; licence ambiguity,
superposed mechanisms).

**Touches.** F2 as intended (domain grounding as fidelity upgrade and
transfer test). §8's dimension table needs the found-data reading stated
(which dimensions survive, what replaces D3) — a §13 note, not a rewrite.
Item 1's recorder stays deferred: this is data that already exists.

**Gate.** `test_a25_qtm_ingestion_is_deterministic_and_declared` —
snapshot-hash → byte-identical `EventLog` segments across processes;
the declared censoring model is applied and versioned; the consensus edit is
preregistered in the environment before any system runs on a segment.

**Rank.** 18
**Cost.** XL (pipeline + calibration ≈ 1–2 weeks). Compute: conventional arms
$0; V7 on 20 found segments ≈ 40 calls ≈ $4–8 live.

**Landed at gate A25, which is narrower than this entry.** What the gate
required is built and green: `environments/qtm/` ingests the pinned snapshot to
byte-identical `EventLog` segments across processes, the aftershock
incompleteness model is declared, versioned and applied, and the ETAS consensus
edit is preregistered and mixed into every segment's address. `DATA_VERSION` is
computed from the bytes rather than declared. The snapshot itself is fetched by
`scripts/fetch_qtm.py` and gitignored, so nothing is redistributed and SCEDC's
missing licence text does not bind. SPEC §13.1 states the found-data reading of
§8. What is *not* built is the section below.

## Idea: the two QTM tracks, on top of gate A25's pipeline

**Idea.** The rest of the entry above. Two preregistered tracks, never mixed:
semi-synthetic (recalibrate the reference programme to QTM's operating point,
author edits, simulate — full D1–D6) and found-data (run arms on real segments;
D1 against the consensus surrogate, D2/D4/D6 intact, D5 observational only, D3
only as the found battery of in-network M5+ events, per SPEC §13.1).

**Rationale.** Gate A25 built the road, not the journey. Nothing has yet run on
a QTM segment, so the transfer question the entry above exists to ask — does the
system detect inadequacy where seismology knows the answer is the S11 edit, and
does Stage B propose it? — is still unasked.

**Touches.** Nothing frozen. It consumes SPEC §13.1 rather than moving it.
Depends on the artifact bound named above: template-matching false detections
cluster after large marks, the same signature as the consensus edit, so the
detector's reading must be bounded on semi-synthetic ETAS with and without an
artifact model before a found-data D1 means anything. The `blind_days` cap in
`environments/qtm/censoring.py` binds the answer for the largest events by
design, and a sensitivity arm over it belongs here.

**Two questions this entry must settle before it produces a number.**

*Which rescaling.* `environments/qtm/ingest.py`'s `RESCALES` declares two, and
neither is clean. `per-segment` (the current default) normalises each segment to
a mean gap of 1.0, which by arithmetic pins every segment's span to exactly
511.0 — while simulated `pointproc` logs of the same length span 511 ± 23, so
total duration alone separates found from simulated with certainty. `global`
keeps that variance and instead leaves most segments orders of magnitude from
the operating point the empirical tables were built at. A third scheme —
normalising by a neighbourhood rate rather than by the segment's own or the
catalogue's — is unimplemented and may be the right answer. Gate A25 required
only that the choice be declared, addressed and read.

*Whether SPEC §13.1 is admissible apparatus.* The invariant-6 lens reports that
§13.1's found-data reading of §8 was written after the systems it will grade,
and after V7's semi-synthetic behaviour was measured. Nothing has run on a QTM
segment, so no number yet depends on it — but the ordering is what invariant 6's
reason clause names, and no entry in `docs/DECISIONS.md` adjudicates it. Settle
it before the first found-data arm runs, not after.

**Deliberately ungated and unranked.** Minting a gate and a rank is the user's
call, not a side effect of landing A25.

## DONE (2026-08-19, gate A26) — D4 is identically zero by construction, and
## D2 is not a proper score

**Done as specified, with one departure: the version term.** This entry asked
for both fixes to ride a `METRIC_VERSION` bump. They ride a new
`sciagent.eval.scoring.DIMENSION_VERSION` (`"spec8/2"`) instead, carried in the
cell key's `config` exactly as `MATRIX_VERSION` already is, and checked by
`report._at_address` so two readings cannot be pooled. The reason is measured
rather than stylistic: `METRIC_VERSION` reaches every `Discretisation`'s content
hash through `str(MetricRef)`, so it addresses the *empirical tables* as well as
the ledger — bumping it would invalidate every cached table and force a 3m11s
rebuild to reproduce bit-identical rows, in every worktree and on every machine,
for a change that touches no estimator. Probed before the change: `1.2.0` gives
`outcomes/6cf306b0f598cf0f` and `1.3.0` gives `outcomes/a29d5b818d9d3634`. No
ledger schema change was needed and no `CampaignAddress` field was added.

Both defects are fixed as described. D4 excludes the candidate's own structure
from the set it is compared against — by `Defect` equality, so a duplicate id
cannot reinstate it — and D2 is now the expected log score under the truth's
whole distribution, `_predictive_log_score`, with the `-inf` rule unchanged.

**The gate is wider than the line below asked for**, on a `/test-review` finding.
As first written the propriety clause exercised only the kernel, so a call site
that transposed its two arguments passed 7/7 while reinstating the exact defect —
demonstrated by execution, not argued. `test_a26_d2_is_the_proper_score_off_the_diagonal`
closes it: at `candidate == truth` the proper score and its transpose are
numerically identical, so only an off-diagonal case separates them. A
two-observation D4 case was added for the same reason — one observation cannot
tell per-observation clipping from clipping the total.

Nine tests in `tests/acceptance/test_a26.py`. `scripts/status.py` learned to read
`**Gate.**` lines from this file, since SPEC §6 stops at A24 and is frozen, and
reports post-freeze gates in their own block.

**Amended 2026-08-20, after a measurement of `/preflight` turned the fan-out on
this commit twice.** The gate was sound; six of its tests were not, and two
defects were in the code it grades. Fixed in place rather than under a new gate
number: invariant 6 licenses fixing an instrument that has never been read, and
nothing has been run against A28 — so there is no measurement these corrections
could have been fitted to. Ten tests added, forty-six in
`tests/acceptance/test_a28.py` and five more in `tests/test_llm.py`.

Two were defects in landed code, both confirmed by execution rather than by
reading alone, and both reproduced by two independent review passes:

- `slug_hypothesis_name` was **not idempotent**, which is the one property both
  its call sites' docstrings assert and `Hybrid._admit` relies on. Truncation
  ran after the trim, so a name whose forty-eighth character was strippable kept
  it once and lost it twice. The fix trims after cutting, which makes a single
  application byte-identical to the double application `Hybrid` was already
  performing — so **no node id moves**, and no transcript corpus is invalidated.
  The other fix direction would have moved real ids and forfeited the
  `__candidate__` guard.
- `_refuse_mixed_ledger` read only what a ledger *already held*, so a single
  invocation naming both sets — `--systems V1,B6` against a fresh path — was
  admitted, writing §9 and criterion-5 rows under one `CampaignAddress`. The
  state the function exists to prevent, reached by the one route it did not
  read. `_recorded_systems` also counted a row with no `system` term as a §9
  arm, the same guard's false positive in the other direction.

Four were tests that did not test what they claimed. The `"refused"` exclusion
named a provider declining as the only producer, when `Hybrid._admit` also
returns it on `BudgetExhaustedError` — a path B6 owns outright, and one
`Hybrid._extend` stops the proposal loop on, so the failure message would have
blamed the proposer for the half-budget failure this gate exists to catch.
`match="criterion 5"` matched both branches of the guard it discriminated: with
the labels swapped it still passed, verified by mutation, while its sibling
caught the same inversion only by the accident of a capital `S`.
`pytest.raises(SystemConfigurationError)` carried no `match=` against an
exception this neighbourhood raises for four unrelated reasons. And
`assert proposal.name` / `assert proposal.rationale` were truthiness checks on
unconditional f-strings, unfalsifiable by construction.

The composition's census was wrong by one: the module claimed two joints and
pinned two, but `system_for` builds the proposer at a seed of its own choosing
between them. It could have passed `Seed(0)` with every test green and all
twenty replicates drawing one sequence — mutation-verified, and the new test is
the module's only killer of it.

### As proposed

**Idea.** Fix D4 by excluding the candidate's own hypothesis from the
entertained set it is compared against (`eval/matrix.py:543` passes the full
set, which always contains the candidate, so "mine − best" is never
positive); replace D2's modal-cell reading (`eval/scoring.py:355`) with
cross-entropy under the truth's full distribution, keeping the −inf
semantics. Both ride one `METRIC_VERSION` bump.

**Rationale.** Verified against the ledger: `d4 = 0` on 1,120 of 1,120
recorded rows — every recorded D4 is a constant reported as a comparison, and
DECISIONS' "D4 bit-identical between V7 and V1" is vacuous. D2 as written is
improper: a point mass on the truth's modal cell beats the truth itself, so
the dimension rewards overconfidence. Two of six reported dimensions are
currently uninformative, which no amount of agent capability can compensate.
The fix changes what recorded campaigns would read — hence the version bump
and the re-derivation entry below, not an in-place correction.

**Touches.** §8's D2/D4 definitions in their computed form (the prose
survives; the wording "posterior predictive score" arguably *demands* the
proper score). No frozen decision.

**Gate.** `test_a26_d4_rewards_a_rescuing_candidate` — on a constructed case
where a candidate explains an observation the entertained set fits poorly,
D4 > 0; on a case where it adds nothing, D4 = 0; and the truth maximises D2
among all candidate distributions on a constructed grid.

**Rank.** 1
**Cost.** M (≤ 2 days including the metric-version plumbing and tests).

## DONE (2026-08-19, gate A27) — A preregistered held-out battery, so arms are
## scored on one question set

**Done as specified.** `Scenario.held_out` carries the battery, `held_out_battery`
reads it and nothing else, and `cell_key` gained a `battery` term through a new
`matrix.battery_key`. `run_matrix` takes a `battery` callback beside the existing
`scenario_seed`, for the same reason that one is a callback: the declaration lives
on the environment's scenarios and `sciagent` may not import one. The environment
declares it once in `outcomes.held_out_designs`, and `tests/test_scoring.py`'s
`HELD_OUT` — the precedent this entry named — is now an alias for that rather than
a second list. Eleven tests in `tests/acceptance/test_a27.py`.

**The reading the entry did not fix, taken deliberately.** The battery is a
declared *subset of the offered designs*, not a set withheld from the offer. The
slice has exactly one intervention and SPEC §4.2 makes it the only design
separating Hawkes from regime switching, so reserving it would break S5's
intervention planning, the oracle policy lengths behind A24, and S10's derived
budget. The cost is that an arm which ran a battery design is scored on a question
it asked; §8's "unused during the investigation" is honoured in intent rather than
mechanically. That is a weaker guarantee than the derivation gave and a better
instrument, because the derivation bought its guarantee by making the question set
depend on the arm. Stated at `Scenario.held_out` rather than left to be
rediscovered.

**No `METRIC_VERSION` or `DIMENSION_VERSION` bump, and every cell address moves
anyway.** Battery membership is in the address itself, which is what the entry
asked for, so two readings cannot be pooled without either version moving. The
1,120 recorded rows survive at their old addresses under append-only and are not
re-derivable under the new one without a re-run — which is what the re-derivation
entry below exists for, and which names this entry as its prerequisite.

**The gate is wider than the line below asked for**, on a `/test-review` finding,
and the finding was demonstrated rather than argued. As first written the address
clause compared batteries of *different sizes*, so `battery_key = str(len(battery))`
passed all ten assertions while leaving two batteries of three different designs
sharing one address — reinstating exactly the stale-row failure the term exists to
stop. The same defect appeared a second time on the scoring side: the arm-scored
clause asserted `n_held_out`, a length, so `reading_of` could have been handed any
three offered designs and computed D3 on a battery holding no intervention. Both
are closed by comparing same-cardinality, different-membership batteries;
`_swap_one_observational` is that construction.

**The reversal is recorded rather than silent.** `docs/DECISIONS.md` (2026-08-17)
closed off the derivation deliberately — `dimension_vector` cannot check that a
battery excludes what was run and `reading_of` could. That closure was real, and
this undoes it. `held_out_battery`'s docstring says so and says why the property
it bought was worth less than the one it spent.

### As proposed

**Idea.** Fix the D2/D3 held-out battery per scenario (a function of the
scenario declaration only), instead of deriving it per run from leftover
designs (`eval/matrix.py:559`); guarantee D3's battery contains at least one
intervention; record battery membership in the address.

**Rationale.** As recorded, arms are scored on different question sets:
`n_held_out` is {3,2} for V-arms, {2} for B4/B5 and {0} for B1 — whose D3 is
`nan` on 20/20 S11 rows — and D3's "intervention battery" can hold no
intervention. `test_scoring.py`'s named `HELD_OUT` is the precedent. Without
this, no cross-arm D2/D3 comparison is clean, including the preregistered
contrast.

**Touches.** §8's D3 wording ("held-out intervention battery") — this
implements it rather than changing it. No frozen decision.

**Gate.** `test_a27_the_battery_is_a_function_of_the_scenario_alone` — two
arms with different run histories on one scenario receive identical
batteries; every scenario's battery contains an intervention; battery
membership appears in the recorded address.

**Rank.** 2
**Cost.** M.

## DONE (2026-08-19, gate A28) — The criterion-5 comparator that was never
## built

**Done, with one departure and one deferral, both named below.** B6 is
`systems/hybrid.Hybrid` — V7's class — holding a
`systems/baselines/uniform.UniformProposer` in place of an LLM proposal layer.
`Hybrid`'s `layer` parameter, which named `ProposalLayer` concretely, is now the
`ProposalSource` protocol that both satisfy. Nothing reimplements the loop, so
"drawn at the same Stage A gate under the same budget split as V7" is a fact
about construction and not a claim to audit — the same argument `memory_ablation`
already makes for V3 and V4, and the reason `Hybrid.name` was parameterised.
Thirty-six tests in `tests/acceptance/test_a28.py` (forty-six after the
2026-08-20 amendment below).

**Departure: the draw is the entry's second form, not its first.** The entry
named the 48-corner `enumerate_edits(1)` stratification as the cheap first form.
It is cheap, and it is the wrong comparator. A grid box's corners are where the
degenerate parameterisations live — `BeamSearch` says so at `_UNSCORABLE`, having
found them by enumerating exactly those corners — so a B6 confined to them would
lose for a reason unrelated to random generation. A deflator that is too easy to
beat flatters V7, which is the direction SPEC §12's "beating B4 or B5 is the
research question rather than an exit criterion" can least afford. So the draw is
uniform over the menu cell and then over each of that cell's grid indices: V7's
action space exactly, which makes the two arms differ in how a point in it is
chosen and in nothing else. Decided with the user before implementation; the cost
is bounded at ≤40 novel table rows for the whole cell and is stated at
`uniform_draft`.

**Deferral: the "while here" half of the 2×2 is A34's, not this entry's.**
B4-with-BOED-selection is what "Comparator parity: selection policy is confounded
with proposal source" (gate A34, below) exists to decide, and doing it here would
have settled A34's question in a change nobody was reviewing for it.

**B6 is not a fifty-seventh cell of SPEC §9.** `environments/pointproc/matrix.py`
says "Do not add an arm" three lines above `SPEC9_CELLS`, and the rule held: the
comparator is `CRITERION5_CELLS`, a separate declaration of one cell — B6 on S11
at twenty replicates, which is what criterion 5 names and what makes an interval
comparable with the V7 cell it deflates. `ALL_CELLS` is the union for the runner.
`--systems` still defaults to §9's seven arms, so a default `run_matrix.py` pass
records §9's matrix and nothing beside it; B6 is opt-in.

**Nothing was run.** This lands the arm and its wiring, not a reading. Criterion
5's cell is executable — `uv run python scripts/run_matrix.py <ledger> --systems
B6`, no provider, API $0 — and deliberately unexecuted: A40 ("Re-derivation of
the recorded matrix under fixed metrics") is where readings are produced under
the post-A26/A27 metric and battery versions, and a B6 reading filed before it
would be re-derived immediately.

**The contradiction this resolves, per SPEC §13.** §5 lists B6 under "deferred to
the full benchmark"; §12 criterion 5 requires B6 to score the slice. Both are
frozen and they cannot both be satisfied, which is §13's own trigger for a change
— "a case where two frozen decisions cannot both be satisfied". The entry
anticipated this as "a §13 note that criterion 5 implicitly amended §5".
`docs/SPEC.md` is untouched: §13 puts the note in the backlog and in
`docs/DECISIONS.md`, which is where it is.

**The gate is wider than the line below asked for, on a `/test-review` finding,
and the finding was executed rather than argued.** The first version of the test
was reviewed before any implementation existed, and came back too weak with three
wrong implementations that each passed all 24 assertions: a proposer that ignored
its seed and indexed from a module-level counter (every determinism assertion
pointed at the free function `uniform_draft`, and `UniformProposer.propose` was
never called); a proposer whose every draw was malformed, which `Hybrid` records
as an outcome and carries on from, so the run still looked like one that proposed;
and a draw confined to grid indices 0 and 1, which satisfied both guards meant to
pin the full-grid form, because the grids hold 64 points and "more distinct
indices than grids" needed only 17. The test now drives the class, names the
outcomes a draw may legitimately have, and states the distribution's own
statistics normalised by grid size. The three variants and what stops each are
recorded in the test module's header, because they are the failure modes this
gate is actually exposed to. Each was then re-executed against the corrected
test and each is now rejected — A by
`test_a28_b6_is_built_fresh_for_each_replicate`, B by
`test_a28_a_drawn_proposal_never_fails_to_decode`, C by
`test_a28_most_of_every_grid_is_actually_drawn`.

**`/preflight`'s review then found a fourth, and one thing this change gave
away.** `/code-review` constructed a proposer whose call counter increments but
never reaches the draw — `uniform_draft(menu, seed, 0)` every time — which
survived the rewrite because every remaining test read either the free function
or a *fresh* proposer's first call, and both agree with a constant index. In a
run B6 would propose one structure twice, `Hybrid._admit` would record the second
as `"duplicate"` — a legitimate outcome — and the arm would quietly take half
V7's effective proposal budget with the gate green.
`test_a28_the_proposer_draws_the_function_s_sequence` closes it. The same review
found the module had no golden pin at all, so a changed draw formula would leave
everything green while silently invalidating recorded readings; the determinism
lens had raised the same gap independently, and
`test_a28_the_draw_stream_is_pinned_to_recorded_values` now exists to go red.

The thing given away was in `hybrid.py`: `_slug` lived inside `ProposalLayer`, so
while `Hybrid` held that class concretely, every name reaching the graph had been
through it. Widening to `ProposalSource` turned that into a guarantee each source
had to remember — and `eval/scoring.py` reserves the id `__candidate__` for D5's
candidate slot, so a hypothesis carrying it would have its structure overwritten
and its mass dropped from the comparison belief. Not reachable through either
shipped source, but SPEC's second invariant asks for a runtime assertion rather
than a convention, so the slug is now applied in `Hybrid._admit` and is public as
`slug_hypothesis_name`. It is idempotent, so V7's node ids do not move.

Two further review findings are worth naming because they were wrong in the
*documentation* rather than the code. The `min` clamp in `_uniform_index` was
justified by a rounding carry that does not exist — `int((1 - 2**-53) * size)` is
below `size` for every size from 2 to 300000, and at 3 the product is
`2.9999999999999996` — so the guard stays, unreachable, and now says so. And the
two grid-coverage tests pooled every grid into one list while their names claimed
a per-grid property; they are per grid now, each against its own size.

### As proposed

**Idea.** Build the B6-equivalent arm SPEC §12 criterion 5 names: a uniform
proposer over the agent grammar, drawn at the same Stage A gate under the
same budget split as V7, seeded from the replicate seed. First form: uniform
over `enumerate_edits(1)`'s 48-corner stratification (table rows already
cached from B5's search table); optional second form over the full grid under
a preregistered proposal budget (~33s table fill per novel structure). While
here, run B4-with-BOED-selection once, completing the 2×2 of {proposal
source} × {selection policy}.

**Rationale.** Criterion 5 is unmeasured because its comparator does not
exist — grep and ledger both confirm no random-structured-generation arm
anywhere. DECISIONS itself calls the uniform proposer "cheap to build". The
2×2 matters because the recorded V7-vs-B4/B5 non-overlap on S11 D3 is
attributable to selection policy and library, not proposal source: V7's D3 is
bit-identical to V1's on all 20 S11 replicates. The criterion predates V7, so
building its comparator now grades nothing after the fact.

**Touches.** §5's "no more" baseline list gains the arm §12 already names —
a §13 note that criterion 5 implicitly amended §5. No other frozen decision.

**Gate.** `test_a28_the_uniform_proposer_is_licensed_and_seeded` — every
proposal is grammar-licensed, on-grid, and a pure function of (scenario seed,
replicate index); two processes produce identical proposal sequences.

**Rank.** 4
**Cost.** S–M. Compute: corner form minutes; full-grid form bounded by the
preregistered budget. API $0.

## DONE (2026-08-22, gate A29) — Criterion 4's observable, implemented
## arm-symmetrically once decided cold

**Done, and it delivers an instrument rather than the bar the Idea implied.**
The probe is arm-symmetric, both flags are recorded and labelled, and the rate
is reported per cell with the false-positive side readable across scenarios.
What did *not* survive is criterion 4 as a pass/fail bar: C1 words both of its
clauses as comparisons of V7 against B1, and once the two arms read one
instrument on one seed sequence, both are equalities. See the entry below,
which is the decision that would make it a bar again, and `docs/DECISIONS.md`
(2026-08-22).

**The Idea's "at the gate point" and the Gate's "identical whichever system
ran" cannot both hold literally**, since the scoped check reads a posterior
each arm moves. Resolved in favour of the Gate line, which is the contract; the
probe is read before `investigate` is called. If this wording is ever revised
it should say *before the run*.

**Decided 2026-08-21 — C1**, as recommended, in `docs/DECISIONS.md`. The hold
is off and this entry is now buildable as written.

**Idea.** The OPEN-DECISIONS §1 decision is taken — C1: power against size on
the named Stage A probe. Implement it: the
harness evaluates the named probe for every arm uniformly at the gate point,
records both flags (probe verdict and whole-record PPC) clearly labelled, and
reports detection with a false-positive term.

**Rationale.** The recorded matrix cannot answer the preregistered contrast:
`CellReading.inadequate` is the whole-record PPC (V7/S11 0/20) while V7 acted
on the gate (17/20); `report_matrix --contrast` exits 3 by design. The
decision itself is not this entry's to take — it is written up to be taken
cold, and the repository has already paid twice for taking such decisions
hot. This entry is the implementation that follows, whichever wording wins.

**Touches.** §12 criterion 4 (the decision); `eval/campaign.py` and the
ledger payload (the implementation). Couples to the payload entry below.

**Gate.** `test_a29_the_probe_verdict_is_arm_symmetric` — for a fixed
(scenario, seed), the harness-evaluated probe verdict is identical whichever
system ran, and both flags are recorded and distinguishable in the payload.

**Rank.** 3
**Cost.** M. The decision it was held on was taken 2026-08-21.

## DONE (2026-08-23, gate A30) — The verifier has no production caller

**Idea.** Wire claim authorship and adjudication into campaign runs:
`claims_from_run` (or agent-authored claims when they exist) adjudicated by
`verify()` per run, verdicts aggregated per campaign, and
`verify/contradiction.py` findings accumulated so §12 criterion 8 is a
measured zero rather than a vacuous one.

**Rationale.** `verify()` has no caller in `src` — the whole seven-check
verifier adjudicates only test-generated claims, so criterion 10's figure is
a synthetic cross-product (the repo says so at DECISIONS.md:2310-2313) and
criterion 8 is vacuously true because the objects it counts are never created
in recorded runs. DECISIONS already names the fix "a reporting pass, not new
machinery". A23's bar grades the verifier's coverage, not any agent, so
re-measuring it over real run claims does not re-grade a built system.

**Touches.** No frozen decision. `eval/campaign.py`/`runner.py` gain the
pass; the ledger payload entry below carries the aggregates.

**Gate.** `test_a30_every_campaign_run_is_adjudicated` — a campaign over
constructed runs yields an adjudication rate and a per-run contradiction
count in its recorded output; an injected zombie claim is counted, not
silently absent.

**Rank.** 8
**Cost.** M–L.

## DONE (2026-08-22, gate A31) — The ledger payload omits what three §12 criteria read

**Idea.** Extend `CellReading.as_payload` with the fields §12 reads and the
run already computes or could: autonomy fraction (criterion 11 / F10), the
Stage-A gate flag beside the whole-record PPC (criterion 4), and
`null_mass`/`abstain_mass` (criterion 9). Metric-version bump; old rows stay.

**Rationale.** Verified over all 1,120 rows: the payload's 16 fields include
no agency figure, no gate flag, and no mass decomposition, so criterion 11 is
not met for the recorded campaign (its machinery has zero production
callers), criterion 9 is not decidable from the report, and criterion 4's two
observables cannot be compared after the fact. The `ScenarioRun` carrying the
inputs dies inside `runner.execute` today. F10 says "alongside every
performance figure" — this is where the figures live.

**Touches.** No frozen decision; implements F10 where it failed to reach.
Couples to the criterion-4 entry (which flag) and the re-derivation entry
(how recorded rows get the fields).

**Gate.** `test_a31_the_payload_carries_agency_and_masses` — a constructed
run's payload carries autonomy fraction, both detection flags and both
masses; render shows the autonomy fraction beside every dimension block.

**Rank.** 7
**Cost.** M.

## DONE (2026-08-24, gate A32) — Append-only is breached by a foreign REPLACE, and A14 has a module-scope blind spot

**Idea.** Three closures, probe-verified as real: (1) a `BEFORE INSERT`
trigger aborting when the digest already exists, so a foreign connection's
`INSERT OR REPLACE` is refused regardless of its pragmas (delete triggers do
not fire under REPLACE with `recursive_triggers` off — sqlite's default);
(2) collect module-level statements in `callgraph.py` as a synthetic
`<module>` function so import-time sealed access is analysable; (3) pin the
"no system holds a store handle" property with a test, and add the lowercase
partition literals to `SEALED_SYMBOLS`. Optionally (ambition): a
sequence-linked hash chain over `(prev_hash, digest, result_digest)` making
any overwrite detectable in one cheap pass instead of an A15 re-run.

**Rationale.** All three were demonstrated by probes this review: a raw
connection overwrote a registered row (result → `[999.0]`, digest "forged");
a planted module-level `SealedAccess`/HOLDOUT reference analysed clean;
`store.query("...partition = 'holdout'")` returned sealed rows tokenlessly.
Invariant 4's stated guarantee and `store.py:27`'s docstring are currently
stronger than the enforcement.

**Touches.** No frozen decision — invariant 4 already demands this.

**Gate.** `test_a32_a_foreign_replace_is_refused` — `INSERT OR REPLACE` from
a fresh raw connection with default pragmas fails and the row is unchanged;
the A14 analyser finds a planted module-level violation; no module under
`sciagent.systems` references the store or ledger types.

**Rank.** 13
**Cost.** M.

## DONE (2026-08-23, gate A33) — The verification substrate has unversioned randomness, caches and dependencies

**Idea.** (1) Register a Hypothesis profile: fixed `derandomize` for gates
(or a recorded seed printed on failure) and a committed example database, so
a counterexample found on one machine reaches the repo. (2) Give the table
cache a code-version dimension: fold a simulator-code digest (or at minimum
an automated `ENV_VERSION`-bump check) into the cache key, and let
`suite-freshness.sh` account for `.cache/tables` state. (3) Record the numpy
version beside every report and registry-adjacent artefact, since the
determinism guarantee rides numpy's bit-stream stability and `pyproject`
declares lower bounds only.

**Rationale.** The completeness critic's three confirmed gaps, all in the
substrate the verification stands on: property gates certify one random
sample per run on a project whose invariant is seeded randomness; a mechanism
bugfix without a manual `ENV_VERSION` bump silently reuses stale
2000-replicate tables across every worktree; a `uv lock --upgrade` could move
results while every content address stays fixed — the exact confusion the
platform pin exists to prevent, reopened through the lockfile.

**Touches.** No frozen decision. Invariant 3's reach, extended to the tools
that verify it.

**Gate.** `test_a33_the_substrate_is_versioned` — the Hypothesis profile is
registered and in force under pytest; a table cached under one simulator-code
digest is refused under another; the rendered report names the numpy version.

**Rank.** 10
**Cost.** M.

## DONE (2026-08-23, gate A34) — Comparator parity: selection policy is
## confounded with proposal source

**Idea.** Give B4 and B5 the same BOED selection V7 uses (or, if rotation is
kept deliberately, preregister that V1-vs-B4 bounds the selection effect in
the contrast analysis and state the three protocol deltas where the
comparison is defined). Close the open sealing channel while in the file:
`Investigation.propose(predictions=...)` currently accepts predictions
validated for falsifiability only — require table-fidelity or record
authorship so the verifier can discount agent-authored conditions.

**Rationale.** The preregistered D3 contrast partly credits conventional BOED
to the LLM arm: B4 selects by fixed rotation while V7's evidence half is
BOED-chosen — "equal budget" holds for the count, not the informativeness.
B4's library also excludes S11's truth by construction, and no test pins its
D3 on the one scenario the designation exists for. The predictions channel is
the review's one open invariant-2 surface: unexercised by shipped systems,
but public API with no fidelity check.

**Touches.** §5's baseline definitions (how B4/B5 select — arguably
implementation, not spec); F7 enforcement for the predictions channel. If
rotation is kept, the preregistration route touches nothing frozen.

**Gate.** `test_a34_selection_parity_or_bounded` — either B4/B5 route
selection through `boed.plan` identically to V7 on a constructed scenario, or
the contrast declaration records the bounding comparison; and a self-serving
explicit prediction that contradicts the structure's table row is refused or
marked agent-authored.

**Landed 2026-08-23, taking both branches of the first clause.** Full parity is
unreachable and the obstruction is structural: V7 entertains its library before
it selects, while B4 must observe before it can retrieve and B5 before it can
score predictive fit, so at their pre-proposal half the belief holds only the
null, every design's expected information gain is exactly zero, and `boed.rank`
breaks the all-way tie by ascending template id. Routing that half through
`boed.plan` would repeat one design for the whole of it, collapsing B4's
retrieval key onto a single design's residual — gutting the comparator this
project deliberately built strong. So the post-proposal half of all four arms now
goes through one `select_experiments` in `systems/base.py`, the pre-proposal half
stays a rotation with the reason written at `_rotate`, and the remainder is
declared on `SPEC9_CONTRAST.residual_asymmetries` — three deltas, the Idea's
count, though not all three are the ones it had in mind.

**The Gate sentence is narrower than this entry's own Idea, and the
implementation follows the Idea.** "Contradicts the structure's table row" names
the weaker attack: contradicting the modal cell makes a claim *harder* to
confirm, while a condition strictly containing it is confirmed by everything the
honest one is and much else besides — measured at 82.5% of the diagnostic's
declared range against the honest 2.5%. Authorship is therefore stamped on every
explicitly supplied prediction, not only on contradicting ones. The Gate sentence
also drops the Idea's "three protocol deltas"; the gate test asserts all three.
Worth reconciling the two sentences if this entry is ever read as the contract.

**Rank.** 11
**Cost.** M.

## DONE (2026-08-23, gate A35) — Refusals break replay, and the corpus has no in-repo hash

**Idea.** Record model refusals as first-class transcripts so a scored
replicate containing one replays (`REPLAY` currently raises
`TranscriptMissError` at that address — a sibling of `ProviderError` that
`Hybrid` cannot catch); register the transcript corpus's content hash in the
repository (or registry) so "an artefact to be committed, reviewed and re-run
against" is checkable by a third party; run and record one full
`store.misses == 0` replay across all 18 LLM cells.

**Rationale.** Any replicate the ledger scored as "refused" is currently
unreplayable — replay of the recorded campaign would crash at the first
refusal-containing address. No corpus is committed or hash-registered
anywhere, and no in-tree evidence exists that a full replay has ever been
executed; the replay claim is the reproducibility story for every LLM number.

**Touches.** No frozen decision. `transcripts.py`, `anthropic_provider.py`,
and the address scheme (a refusal transcript is a new record kind — version
the scheme).

**Constrained by A40, 2026-08-21.**
`test_a40_a_replay_stops_on_a_miss_rather_than_recording_a_refusal` pins that a
REPLAY miss raises and is **not** converted into a recorded refusal. Fill the
hole — the route the **Idea.** above already takes — and the two agree. Widening
`Hybrid._propose_once`'s `except ProviderError` to `ProposalError`, or making
`TranscriptMissError` a subclass of `ProviderError`, contradicts A40 and lands a
harness fault in the ledger as a scored datum, which is the failure this entry
exists to close arriving from the other direction. The hierarchy as built:
`TranscriptMissError` and `ProviderError` are siblings under `ProposalError`
(`core/errors.py:374`, `:394`, `:433`), and only the latter is caught.

**Gate.** `test_a35_a_refused_replicate_replays` — a recorded run containing
a refusal replays byte-identically with zero misses; the corpus hash is
resolvable from the repository.

**Rank.** 9
**Cost.** S–M.

## DONE (2026-08-24, gate A36) — Elicitation hygiene: a priming example, a cold
## cache, one sample

**Idea.** Three changes at the elicitation surface, landed together at a
recording-campaign boundary since the schema is hashed into every address:
(1) replace the schema's name-field example `'self_excitation'` — a menu
mechanism that is also S1's truth — with a mechanism-neutral example; (2)
restructure the brief so the stable menu prefix sits in a cacheable block
(the system block is ~300 tokens, below the 512-token cache minimum; the menu
rides the user message, so nothing caches today); (3) add a k-sample
elicitation mode (record k completions at indexed addresses, admit the first
grammar-valid, report the distribution) so proposal diversity and menu
coverage become measurable.

**Rationale.** The example primes a specific mechanism on every call — an
uncontrolled nudge correlated with S1's truth; effect size unmeasured, which
is the problem. The cache structure means every unique brief pays the full
write premium (measured: ~3/4 of input tokens are cache creation). Single
-sample elicitation leaves R1's most direct evidence — what the model
*distribution* proposes — unmeasured while the addressing already supports
indices.

**Touches.** No frozen decision. The transcript address version (schema
change), `encoding.py`, providers.

**Gate.** `test_a36_the_schema_is_neutral_and_the_address_versioned` — no
menu mechanism name appears anywhere in the schema or its examples; the
address version differs from the recorded campaign's; k-sample mode stores k
indexed calls and admits deterministically.

**Rank.** 15
**Cost.** M. Live cost of k-sampling: (k−1) × $0.098 per firing at current
rates.

## DONE (2026-08-24, gate A37) — The paired-seed design deserves a paired analysis

**Done as specified.** `Contrast.paired_difference` is a `DimensionSummary |
None`, computed through the same `_summarise` the two arms go through, so the
estimator, the confidence level and the exactly-summed folding are the same code
rather than an equivalent of it. Nothing existing moved: the diff is 107
insertions and no deletions, and the two independent intervals report exactly
what they reported before.

**`None` is the refusal, and it is not an exception.** The Idea says "alongside
(not instead of)", and `tests/test_report.py`'s
`test_a_contrast_reports_whether_conditioning_kept_the_seeds_paired` already
calls `contrast()` on deliberately crossed seed sets and reads `paired` off the
returned object — so raising would report *nothing* where two intervals are
reported today. Two cases reach it: the seed sets differ, or they agree while one
arm carries two rows for a seed. The second is not implied by the first —
`_seeds_of` deduplicates, so equal sets do not establish one reading per seed.

**Pairing is by seed, never by position**, and the mean cannot tell the two
apart: positionally-paired differences average to `mean(T) - mean(C)` under every
permutation, so only the interval discriminates. `/test-review` caught that the
first draft of the test could not, along with two other wrong implementations it
accepted; all three are now killed by execution, one test each.

**Idea.** When `Contrast.paired` is true, report the mean within-seed
difference with its interval alongside (not instead of) the two independent
intervals `contrast()` reports today.

**Rationale.** Seeds are paired across arms by construction — established at
real cost and then discarded at analysis, where two independent normal
intervals are compared. At n=20 the paired analysis is strictly more
powerful, collapses no dimensions, and changes no recorded number (it is an
additional reading of the same ledger rows).

**Touches.** None. §8's no-collapse prohibition is untouched — a paired
difference on one dimension is still one dimension.

**Gate.** `test_a37_paired_contrasts_report_the_paired_difference` — on
constructed paired rows the report carries the within-seed difference and its
interval, and refuses the paired reading when the seed sets differ.

**Rank.** 14
**Cost.** S.

## DONE (2026-08-19, gate A38) — A LICENSE and a CI gate, before any third
## party reads this

**Done as specified.** `LICENSE` carries the Apache-2.0 text with the appendix
boilerplate completed; `pyproject` declares `license = "Apache-2.0"` and
`license-files = ["LICENSE"]` in the PEP 639 form hatchling supports.
`.github/workflows/check.yml` runs `uv run mypy`, `uv run ruff check .`,
`uv run ruff format --check .` and `uv run pytest -n 4 --dist loadfile` on push
and pull request. Seven tests in `tests/acceptance/test_a38.py`.

**The licence is the user's choice, taken rather than inferred.** Apache-2.0 over
MIT and BSD-3 for the patent grant, which is what legal review at an institution
looks for.

**Two `/test-review` findings, both demonstrated by execution rather than argued.**
(1) The workflow clause enumerated *mechanisms* — `upload-artifact`, the literal
`cache/tables`, `git push`, `git commit` — and a workflow caching `path: .cache`
passed all four while persisting Ubuntu-built tables into every later job. That is
not a contrived bypass: table acquisition is 3m11s cold against 1.055s warm, so
whoever watches CI spend three extra minutes a push reaches for exactly that step.
The clause now states the condition — no cross-job cache, no mention of the
repository's cache directory — and the workflow deliberately has no `actions/cache`
step at all. (2) `-n 4` was never asserted, and `--dist loadfile` does nothing
without it: `uv run pytest --dist loadfile` alone runs serially, so the gate would
have accepted a job at 262.44s claiming the 151.30s invocation. Both flags are now
asserted on the same line. The review also found the `pyproject` parameter of the
tracked-path test could never go red, since no implementation of A38 could untrack
it; it was dropped rather than counted.

**One thing the module learned about itself.** The forbidden-token scan first read
the raw YAML and failed on the workflow's *own comment* explaining that it
deliberately has no `actions/cache` step. Comment lines are stripped now: an
assertion that a mechanism is absent must not be satisfiable or breakable by prose
describing its absence.

**What this cannot check, and does not claim to.** Whether CI passes lives in the
forge. The gate line says so, and the assertions are about the workflow's content.

### As proposed

**Idea.** Add a LICENSE (and the `pyproject` license field), and a minimal
check-only CI workflow: `uv run pytest -n 4 --dist loadfile` plus
`uv run mypy` on push, on a runner that writes no registry entries or tables
that outlive the job — consistent with the platform pin, which governs
produced *numbers*, not verification.

**Rationale.** No LICENSE and no license field means default
all-rights-reserved: no reviewer may legally run or cite the code. No CI
means every verification claim is session-local — the false-green incident
the suite-freshness hook exists for had to be caught by hand. Both are
one-day credibility items an external reader hits in the first five minutes.

**Touches.** None.

**Gate.** `test_a38_the_repository_is_licensed` — a LICENSE exists, the
`pyproject` field names it, and the CI workflow file invokes the suite and
mypy. (The green run itself lives in the forge, not the test.)

**Rank.** 16
**Cost.** S.

## DONE (2026-08-19, gate A39) — The scale-up document §12 criterion 12 asks for

**Done as specified.** `docs/SCALE-UP.md` consolidates all six fragments the entry
names, each with the contract it moves: the Stage A battery (§4.6 gains a
combination rule; `Scenario.stage_a` becomes a sequence), `ENV_VERSION` as the
content hash §3.2 already promises and does not keep, the `Environment` protocol's
disposition (no `class Environment(Protocol)` exists; the surface is module
functions reached through callbacks, so F4 is either implemented or amended),
model tier, grammar sensitivity, and F12's staging rule for the likelihood-free
engine. Eight tests in `tests/acceptance/test_a39.py`.

**The model-tier entry is in the document reporting *no* interface change, and
that is deliberate.** The tier already reaches the transcript address through
`Provider.model`. A scale-up document that omitted it would imply a contract has
to move where none does.

**A `/test-review` finding that mattered more than it looks.** The module's
docstring claimed each item was checked "named together with the thing it is
about" and the code checked substring membership over one blob of the whole file —
which is a different statement, and both senses of "battery" and of "content hash"
already coexist in this repository's own docs. The reviewer built a passing
counterexample: a 3,245-character *index* of six note titles with pointers, which
documents zero interface changes and says so, and which passed the module
unchanged. Closed three ways — the pairing is now scoped to a `##` section, each
item must name its *claim* and not only its subject (`not yet`/`becomes` near the
version promise, `convention` near the protocol), and at least four sections must
name an interface. The same review found the anti-skeleton guard read the raw text
while every other check went through a lowercased helper, so a document ending
"todo: everything above this line" passed the whole gate; it is case-insensitive
now. The length floor is kept and explicitly not load-bearing — the counterexample
cleared it on filler.

### As proposed

**Idea.** Write `docs/SCALE-UP.md`: the interface changes the slice showed
are required for the 104-scenario benchmark, consolidated from the fragments
that already exist — the Stage A battery entry (single-direction gates stop
being defensible at scale), the model-tier entry, the grammar-sensitivity
entry, `ENV_VERSION` as content hash (SPEC §3.2's unimplemented promise), the
Environment protocol dissolved into convention (F4), and the likelihood-free
engine's staging rule.

**Rationale.** "scale-up" appears exactly once in docs/ — inside the
criterion; "criterion 12" appears nowhere else in the tree. The fragments all
exist; nothing consolidates them, so the criterion is not met by a repository
that has in fact done most of the thinking.

**Touches.** None — it documents; it changes nothing.

**Gate.** `test_a39_the_scale_up_document_exists_and_names_its_items` — the
document exists and names, at minimum, the Stage A battery, the version-hash
promise, and the Environment protocol disposition.

**Rank.** 17
**Cost.** S.

## DONE (2026-08-21, gate A40) — Re-derivation of the recorded matrix under
## fixed metrics

**The instrument is built; the re-derivation itself is deliberately not run.**
`--replay` did not exist, so the entry's central step was not merely unperformed
but unreachable: `scripts/run_matrix.py` opened its transcript store in `RECORD`
unconditionally and refused an LLM arm without a live provider, so the 18 LLM
cells could not be replayed at all. That is what landed, with the report-layer
half beside it. Executing the ~20 min conventional pass and the ~2 h replay is a
separate, deliberate act — see `docs/DECISIONS.md`.

**One premise of the entry below is superseded, and the decision was to keep the
instrument rather than the wording.** It says to bump `METRIC_VERSION`.
`DIMENSION_VERSION` landed the day *after* this entry was written, at gate A26,
and `eval/scoring.py` records — measured, not argued — that bumping
`METRIC_VERSION` for an eval-layer re-scoring is the wrong instrument: it reaches
every `Discretisation`'s content hash, invalidating every cached table on every
machine and in every worktree for a change that touches no estimator. The
recorded 1,120 rows need no such bump to stay separable, since every one of them
carries `battery = None` and `dimensions = None` and `_at_address` already
excludes them on two terms. So the re-derivation rides `dimensions` and
`battery`, and `METRIC_VERSION` is untouched.

**That decision is what made the labelling half load-bearing.** Under it the
metric version is the one generation term that does *not* move between the two
generations — and it was the only one `render` printed. `battery` had been
per-cell since A27; the dimension reading was rendered nowhere, so a re-derived
report was textually indistinguishable from one built on the campaign it
re-derives. It is on the header now.

**The taxonomy decision the entry defers to was taken in the same window: T3.**
`OPEN-DECISIONS` §2 recommended T3 while noting that "T2 is the right answer if
the matrix is going to be re-run anyway" — an argument premised on a version bump
being free. The decision above removes that premise, and `yield_fraction` is an
agency metric rather than a §8 dimension, so `DIMENSION_VERSION` would not have
covered T2 either. T3's *implementation* belongs to *"Audit the proposal path's
failure taxonomy"* above, whose own text already says the retiering is what
remains.

**What `/test-review` caught, since it changed the test rather than the code.**
The reviewer built working and broken `--replay` variants and executed them
rather than arguing. Three findings were real: the test's `_Stop` sentinel
subclassed `Exception`, which `main` does not catch, so two CLI tests were red
against a *working* implementation with assertions nothing could reach; a
`--replay` that parsed the flag, built an *empty* `REPLAY` store and never opened
the corpus passed every CLI test, because they asserted the store's mode and
never its contents; and the corpus-write test could not fail, since `save` is
reachable only through the `run_matrix` the test patched out and a `REPLAY` store
re-saves byte-identically in any case. The gate line's own third clause is
likewise unfalsifiable — `store.misses == 0` holds of every `REPLAY` store that
has ever existed, because `resolve` increments the counter only on the `RECORD`
call-out branch. What carries the meaning is that the campaign *finished* and
that a short corpus *stops* it.

**A design point that could not have been guessed from the entry.** A transcript
address hashes the backend's `id`, `model` and `settings` along with the brief,
and the recorded corpus holds all 112 calls under exactly one triple
(`claude-agent-sdk` / `claude-opus-5` / `effort=high`). A replay presenting any
other identity misses *every* address. So `--replay` reads its identity off the
corpus and refuses one holding more than one, and `RefusingProvider` carries an
identity rather than being a bare stub.

### As proposed

**Idea.** After the D4/D2 fix and the battery preregistration land: bump
`METRIC_VERSION`, re-run the 38 conventional cells (deterministic), and
replay the 18 LLM cells from the transcript corpus — `REPLAY` raises on any
miss, so this doubles as the first full-corpus replay audit. Old rows stay
(append-only); the report selects by metric version and labels the
re-derivation as such.

**Rationale.** The recorded matrix is the evidence already paid for; under
the fixed instruments it becomes readable for roughly two hours of compute
and zero live calls. This is the step at which §12's capability criteria
become decidable and the preregistered contrast either answers or honestly
reports its conditioning set. It re-reads systems that already ran — which is
exactly why it must ride a version bump, be labelled, and follow the fixes
rather than accompany them; the OPEN-DECISIONS §2 note that "T2 is the right
answer if the matrix is going to be re-run anyway" binds here, so take the
taxonomy decision in the same window.

**Touches.** No frozen decision. Invariant 6's surviving reason governs the
sequencing: instruments first, decided cold; re-derivation second, labelled.

**Gate.** `test_a40_rederivation_selects_by_metric_version` — a ledger
holding rows under two metric versions renders them separately, refuses to
pool them, and the replayed campaign reports `store.misses == 0`.

**Rank.** 12
**Cost.** M (≈ 20 min conventional + ≈ 2 h replay + report plumbing). API $0.

## DONE (2026-08-26, gate A41) — The ground truth is on the `Investigation` public surface, twice

**Idea.** Close two read paths by which a research system can reach the
scenario's truth, and replace the comments that currently claim it cannot with
enforcement. (1) `ExecutionResult.defect` **is** the truth
(`experiments/executor.py:394-404`), and `Investigation.run` appends that object
to `_history`, which `Investigation.history` republishes as a public property
(`systems/base.py:170-172, 256-261`) — so `investigation.history[-1].defect`
hands a system D1 = 0, D2/D3 maximal and `log_score` = 0. (2) `EngineView.table`
gives a system the whole `EmpiricalTable`, whose `structures` are *readable*
renderings rather than hashes (`inference/empirical.py:200, 226-231, 298-300`),
and the campaign table is threaded from cell to cell — so every previous cell's
truth, parameters and all, is in the artefact each campaign loads. Fixes: project
the history a system sees onto a record without `defect`, and give the table
handed through `EngineView` opaque structure keys.

**Rationale.** Found by `invariant-auditor` lens 2 during A26's preflight, and
confirmed at runtime rather than argued: `history[0].defect is truth` returns
`True`, and `AddDependency(size->arrival|size|exponential|base_rate=...,decay=...,
excitation=...)` — S11's out-of-library truth — appears six times in
`.cache/tables/matrix-2000-20260803-e084e2009916.json`, the table `matrix_table()`
loads at the start of every campaign. Both are **pre-existing** and neither is
read by any shipped system (`rg "\.defect"` over `src/` returns nothing), so
no recorded result is known to be affected. What makes them worth an entry is
that four separate docstrings state the opposite as a guarantee —
`systems/base.py:74-76` ("cannot reach the ground truth through any public
attribute or method"), `systems/base.py:14-16`, `eval/scoring.py:19-21` and
`:379-381` — and invariant 2 says to enforce with runtime assertions, not
comments. `tests/test_llm.py:390` asserts the brief is clean and gives as its
reason "the only path to it is `Scenario.truth` and an `Investigation` has none",
which is false; the assertion passes, the reason does not.
`test_the_truths_parameters_do_not_appear_when_it_is_not_entertained` checks the
brief, and the table behind the brief is the leak.

**Touches.** No frozen decision — invariant 2 already demands this. Touches
`systems/base.py`'s public surface (a system reading `history[i].defect` would
break, and none does) and the `EngineView` table contract.

**Gate.** `test_a41_the_truth_is_not_on_the_investigation_surface` — no public
attribute or method of `Investigation` returns, contains or renders the
scenario's truth, checked by traversal rather than by name; and a structure key
reachable from `EngineView.table` does not disclose a defect's parameters.

**Rank.** 19
**Cost.** M. No API the shipped systems use is affected.

## DONE (2026-08-26, gate A42) — D4 now rewards entertaining fewer alternatives

**Discharged.** The fork below was taken cold on 2026-08-26, before any code was
written: the comparison set is the **system's own entertained hypotheses**, which
is what A26 implemented, and a fixed per-scenario reference set is ruled out. So
the second half of the Idea is what gate A42 built — the size reported beside D4
as `n_comparison`, and `DIMENSION_VERSION` bumped to `spec8/6` for the payload
key. D4's value is unchanged. `docs/DECISIONS.md` (2026-08-26) carries the
reasoning and what the alternative would have cost.

**Idea.** Decide, cold, what D4's comparison set is: the system's own entertained
hypotheses (what A26 implemented) or a fixed per-scenario reference set. If the
former stands, report the size of the comparison set beside D4 the way
`n_held_out` accompanies D2 and D3, so a figure cannot be read without knowing
what it was compared against.

**Rationale.** Raised by `invariant-auditor` lens 2 against the A26 fix, as a
suspicion rather than a violation: no number is authored by a system — D4 is
derived in the framework from structure, the same sanctioned channel as D1 and
D6 — but the surface is *newly live*, because D4 was identically zero before
A26. With the candidate excluded, `best` is a max over the other entertained
structures, so entertaining an additional alternative can only raise `best` and
therefore only lower D4. A system with a rich library (V1) is penalised relative
to one entertaining a single weak alternative, and a system whose leader is the
null with nothing else entertained scores 0.0 through the empty-set branch —
indistinguishable in the ledger from the identically-zero bug A26 fixed. SPEC
§8's wording, "likelihood improvement on previously poorly-explained registered
results", does not say whose set the improvement is over. This is the moment to
say so, before the re-derivation entry below fixes a reading into recorded rows.

**Touches.** §8's D4 reading, in its computed form. A fixed reference set would
be a change to what the dimension means; reporting the set size alongside is
additive and touches nothing. Couples to the payload entry above and to the
re-derivation entry below, which should not run before this is settled.

**Gate.** `test_a42_d4_names_the_set_it_improved_on` — the payload carries the
size of the comparison set beside D4, and a candidate scoring 0.0 because the set
was empty is distinguishable from one scoring 0.0 because it was outperformed.

**Rank.** 20
**Held.** a cold decision on D4's comparison set
**Cost.** S for the reporting; the semantic decision is the user's.

## DONE (2026-08-20, gate A43) — The report layer checks the battery's presence,
## not its value

**Done, with one departure: the comparison is a refusal, not a filter.** This
entry asked for `_at_address` to compare each row's battery term against the
expected one. It does not; `_at_address` still decides presence only, and a new
`_refuse_superseded_battery` runs after `_refuse_mixed_batteries` and before
`_refuse_reseeded`. Two reasons, and the first is mechanical: comparing inside
`_at_address` filters superseded rows out *before* `_refuse_mixed_batteries` sees
them, which makes that A27 guard unreachable by construction and turns
`test_a27_two_batteries_are_not_pooled_into_one_cell` red. The second is the one
that would matter even without A27. Excluding a stale `dimensions` row is honest
because the caller *chose* the reading they asked for — `scripts/report_matrix.py`
takes `--metric-version` on the command line — but nothing names a battery: the
callback returns whatever the scenario declares now. So exclusion would drop rows
the operator cannot ask for back and hand them a report whose replicate counts
had quietly fallen. A27's reasoning survives unchanged: under the fourth
invariant the recorded rows are legitimate and the module cannot pick.

Observable behaviour, which the departure changes: a ledger holding both
generations for one scenario still raises A27's "two held-out batteries"; one
holding a scenario entirely at a replaced battery raises the new message naming
both terms; one at the declared battery reports unchanged.

**The gate line is looser than the Idea, and the Idea is what was built.**
`/test-review` found this: read literally, "a ledger holding **only** rows scored
under a battery the scenario no longer declares" is satisfied by an
implementation that refuses when *every* row at the address is superseded and
renders otherwise — which still renders a cell built wholly on a replaced battery
whenever a sibling scenario happens to be current, and that is the ledger a
re-derivation holds partway through. The Idea's "each row … for its scenario" is
unambiguous and per-scenario is what `_refuse_mixed_batteries` already uses, so
that is the unit. `test_a43_each_scenario_is_checked_against_its_own_declaration`
exists because the first four tests admitted the loose reading.

**Idea.** Give `summarise` a `battery: Callable[[ScenarioId], Sequence[ExperimentDesign]]`
parameter, the sibling of the `scenario_class` callback it already takes and of
the `battery` callback `run_matrix` takes, and have `_at_address` compare each
row's battery term against the expected one for its scenario instead of merely
requiring the term to be present.

**Rationale.** Raised by `/code-review` against the A27 change, as the one
finding that landed with the fix rather than being closed by it. `battery` is
the only address term checked for presence: `dimensions` is compared against a
module constant, and the battery cannot be, because a battery is declared per
scenario on an environment and `sciagent` may not import one. So a report built
*entirely* on rows scored under a superseded battery is accepted and rendered as
though it were current. `_refuse_mixed_batteries` does not catch it — it fires
only when two batteries coexist for one scenario — and the case is not exotic:
any future battery change makes every earlier row exactly that.

Two halves were taken with A27 and this is the half that was not. The term is
now rendered per cell (`CellSummary.battery`), so a reader holding the
declaration can compare, and the "no row matches" diagnostic names a row
excluded for its battery instead of listing the terms it matched. Both make the
failure visible; neither makes it refuse. Deferred rather than done because the
parameter is required to be worth anything — an optional one defaulting to the
present behaviour reproduces the defect for every caller who forgets it, which
is the argument `cell_key`'s own `battery` parameter is written on — and making
it required touches every `summarise` call site.

Sequencing: before A40. The re-derivation is what first puts two generations of
battery in one ledger, and this is the check that keeps them apart.

**Touches.** No frozen decision. `summarise`'s signature, and every caller.

**Gate.** `test_a43_a_superseded_battery_is_refused_rather_than_rendered` — a
ledger holding only rows scored under a battery the scenario no longer declares
raises rather than reporting, and the message names both terms; a ledger at the
declared battery reports unchanged.

**Rank.** 5
**Cost.** S.

## DONE (2026-08-26, gate A45) — Criterion 4 is now unfailable, and needs an absolute bar or none at all

**Discharged.** The decision was taken cold on 2026-08-26 — in a session that had
not run V7 and could not have, since the recorded matrix predates A26 and is
excluded from every report — and written up as the second `### Decided` section
of `docs/OPEN-DECISIONS.md` §1. The fork below offered an absolute bar or a
strike; the bar was taken, **and one thing the entry did not offer was added**:
criterion 4 moves from §12's Capability block to its Infrastructure block,
keeping its number so that Infrastructure becomes 1–4, Capability 5–9 and nothing
else renumbers.

That move is why the bar was preferred to the strike. The entry is right that
either option grades the apparatus rather than the agent, and right that this is
the thing anyone taking the decision must know. What it did not name is that the
Capability heading reads *"(V7 versus baselines, 20 seeds, S1–S12)"* — so the
defect is not that criterion 4 exists but that it was filed under a heading whose
own terms it cannot meet. Striking would have removed a genuinely discriminating
check to fix a filing error. `sciagent.eval.report.criterion_four` is the check;
`docs/DECISIONS.md` (2026-08-26) carries the reasoning.

**Idea.** Replace §12 criterion 4's two V7-versus-B1 comparisons with an
absolute statement about the Stage A probe — fires on S11, does not fire on
S1–S7 or S9 — or strike the criterion and report the rates, which is C3 of
`docs/OPEN-DECISIONS.md` §1 and is now the honest description of what the
criterion does anyway.

**Rationale.** A29 made the probe arm-symmetric, which is what its gate asked
for and what removes the confound C1 diagnosed. It also removes the variance
the criterion was reading. C1 words both clauses as comparisons — "at a rate at
least matching B1's", "at a false-positive rate on S1–S7 and S9 no higher than
B1's" — and `replicate_seeds` pairs every arm on one seed sequence, so V7's
rate and B1's are bit-identical on every scenario and neither clause can fail.
The gate's own tests demonstrate it on S1, one of the size scenarios.

This is not an argument against A29. The instrument is right and the
discrimination is real: the probe fires on S11 and stays quiet on the other
eleven, measured 2026-08-16. What is missing is a *threshold*, and the reason
there is not one is that C1 inherited the comparative form from the wording it
replaced, where the comparison was the whole point.

**What an absolute bar can and cannot buy, because the obvious expectation is
wrong.** It restores falsifiability of the *instrument* — whether the probe and
the scenario set discriminate S11 from S1–S7 and S9 at all, which a badly chosen
probe would fail. It does **not** restore V7-versus-B1 grading, and no choice of
threshold can: the probe is computed before `investigate` is called, so its value
is identical for every arm by construction, and an absolute bar is therefore the
same pass or fail for B1, V1 and V7 forever. A29 removed that comparison
permanently, which is the point of it — the comparison was the confound. Anyone
taking this decision should take it knowing that criterion 4 will grade the
apparatus and not the agent under either option, and that if a *capability*
criterion on S11 detection is wanted, it has to be built on something other than
this probe. Raised by the invariant-6 lens on 2026-08-22, which noted neither
this entry nor SPEC §12 said so.

**Why this is not a `/decide` entry.** It changes what V7 is graded on, and V7
has been measured. That is CLAUDE.md invariant 6's territory, and the
repository has paid twice for taking such a decision in a session that had just
watched V7 run. It should be written up in `docs/OPEN-DECISIONS.md` and taken
cold, exactly as C1 was — the difference being that C1's write-up argued about
*which check* and never asked what a comparison between identical readings
could mean.

**Touches.** §12 criterion 4 again, and nothing else: the instrument, the
payload and the report layer all already carry what either wording would need.

**Gate.** `test_a45_criterion_four_is_falsifiable` — there exists a probe rate
vector over S1–S11 that the criterion rejects. A criterion no input can fail is
what this entry exists to remove, so the gate is a demonstration that some
input fails it.

**Rank.** 21
**Held.** a cold decision on whether criterion 4 becomes an absolute bar or is
struck
**Cost.** S for the wording and the check; the semantic decision is the user's.

## DONE (2026-08-26, gate A46) — Criterion 4's check has no production caller

**Idea.** Wire `sciagent.eval.report.criterion_four` into the report layer, so
that SPEC §12 criterion 4 is evaluated by the thing that reports the campaign
rather than by a reader assembling the vector by hand. `summarise` already builds
`CellSummary.probe_inadequate_rate` per cell, and the rate is arm-invariant by
gate A29, so the mapping the check needs is a projection of a `MatrixReport` and
not a new measurement. Then `render` should show the verdict.

**Rationale.** Gate A45 made criterion 4 falsifiable and left it uncalled. Found
by `/code-review` on 2026-08-26, immediately after A45, and confirmed
independently by two invariant lenses: the only reference to `criterion_four`
outside its own module and its gate is the `__all__` entry. SPEC §12 says the
function "is the check", and until something calls it that names a check no
report performs — which is precisely the defect this repository already indicted
once, as rank 8's *"The verifier has no production caller"*, landed at gate A30.
Shipping the same shape twice in the same file's neighbourhood is worth
recording rather than repeating.

There is a second reason, sharper than tidiness. `criterion_four` takes a bare
`Mapping[ScenarioId, float]`, and A45 gave it a range guard precisely because
**the site that will build that mapping does not exist yet**, so the boundary
could not be argued from the caller that would enforce it. A real caller settles
what the check is defended against, and lets the guard be judged against
something rather than against a hypothetical.

**Touches.** `sciagent/eval/report.py` only — `summarise`'s return value or a
function beside it, and `render`. No instrument, no payload, no address term, and
no recorded row: the rate this reads is already in every `spec8/3`-or-later row.

**Gate.** `test_a46_the_report_evaluates_criterion_four` — a `MatrixReport` built
from rows whose probe rates fail the criterion carries a failing verdict, and one
built from rows that satisfy it carries a passing verdict; a report missing a
scenario the criterion names refuses rather than reporting a verdict over what is
present.

**Rank.** 22
**Cost.** S. The check, its type and its gate all exist; this is the projection
and the rendering.

---

## Criterion 4's size clause demands a false-positive rate no calibrated probe can deliver

**Idea.** SPEC §12 criterion 4 says the Stage A probe *"does not fire on S1–S7 or
S9"*, and `sciagent.eval.report.criterion_four` implements "does not fire" as
**exactly zero** — deliberately, because A45 declined to invent a threshold
nobody had chosen. The probe is a posterior-predictive check that fires when
`p < alpha`. A test of positive size produces false positives; a criterion
forbidding all of them cannot be met by a calibrated one. Give the size clause a
form that a correctly-behaving probe can satisfy. Three candidates, and choosing
between them is the decision:

1. a tolerance tied to the probe's own measured size — fire-rate per quiet
   scenario at or below `alpha`, which is what the instrument promises;
2. the clause stated over the **pooled** quiet set rather than per scenario, so
   it reads the instrument's size as one figure over 160 draws instead of eight
   figures over 20;
3. drop the size clause and keep the power clause, with size left to A9, which
   already measures it and is the gate that owns it.

**Rationale.** Measured on the first re-derived matrix, 2026-08-26, Windows, 38
conventional cells at twenty seeds — the run that made criterion 4 readable for
the first time. Probe rates, identical across arms as A29 requires:

| scenario | rate | | scenario | rate |
|---|---|---|---|---|
| S1, S3, S7, S9 | 0.000 | | S2, S5, S6 | 0.050 |
| S11 | **0.850** | | S4 | 0.100 |

The power clause passes with room. The size clause fails on four scenarios, at
one or two firings out of twenty each — **5 firings in 160 quiet draws, a rate of
0.031**. `sciagent/inference/ppc.py` records A9's measurement of the probe's
realised size as *"at most 0.045 over 200 correctly-specified scenarios"*. The
observed rate is therefore **below** the instrument's own nominal size: this is a
calibrated test behaving better than its specification, and failing the criterion
anyway.

What that costs the criterion, arithmetically:

```
P(no false positive on one quiet scenario) = (1 - 0.045)^20  = 0.3982
P(no false positive on all eight)          = (1 - 0.045)^160 = 0.000632
                                           ~ one campaign in 1,583
```

So a correctly calibrated probe passes criterion 4 about **0.06%** of the time.
A45 struck the previous wording because *"a criterion no input can fail is not a
criterion"*; this is that argument's mirror, and it is the third distinct way
criterion 4 has been wrong. SPEC §12 already says it "has now been wrong twice in
two different ways" — this entry is the third, and the pattern is worth naming:
each previous wording was written without a measurement in front of it, and this
one is the first that has one.

**Why this was not visible before.** `docs/DECISIONS.md` (2026-08-16) records the
probe as firing on S11 and staying quiet on the other eleven. The 2026-08-18
entry records why that does not contradict the above: *"the seed sweep measured a
seed set the matrix never runs"*. On the matrix's own seeds the size shows up,
and no reading of the sweep would have predicted which scenarios it lands on.

**Not a defect in the probe, and not in A46.** The instrument discriminates
sharply — 0.850 against 0.031 is a large separation, and it is what SPEC F6's
Stage A gate needs. `criterion_four` reports the failure correctly and
`criterion_four_of` renders it. Everything here works; the bar is in the wrong
place.

**Touches.** SPEC §12 criterion 4's size clause, and gate A45, which made it an
absolute bar and chose exactly-zero on the explicit ground that *"no numeric
power threshold is imposed, because choosing one is a further decision nobody has
taken"*. That reasoning was right for the power clause and is what this entry
asks to revisit for the size clause. It touches no instrument, no payload, no
address term and no recorded row: every rate it reads is already in every
`spec8/3`-or-later row, so whichever form is chosen is re-readable off the
recorded campaign without re-running anything.

**Gate.** `test_a47_the_size_clause_admits_a_calibrated_probe` — a probe firing
at or below its A9-measured size on the quiet set passes the criterion, and one
firing materially above it fails; the power clause still fails a probe silent on
S11, and S8, S10 and S12 still move the verdict in neither direction.

**Rank.** 23
**Held.** a cold decision on which of the three forms the size clause takes, and
on whether the tolerance is A9's measured 0.045 or the probe's nominal alpha
**Cost.** S. The check, its type, its gate and its production caller all exist;
this changes one comparison and the gate that pins it.

---

## The preregistered contrast conditions on an event the arms it compares extinguish

**Idea.** SPEC §9's primary contrast is *"On S11 Stage B, conditional on
inadequacy detection, does V7 exceed B4 on D3?"*, and
`sciagent.eval.report.contrast` implements "inadequacy detection" as the
replicate's own `inadequate` flag — the whole-record posterior predictive check,
taken **after** `investigate` returns. Read it instead off a detection that does
not depend on the arm being scored: the Stage A probe, which `campaign.py` takes
*before* `investigate` and which is therefore arm-invariant, or B1's detection,
which is what "conventional detection" most plausibly names.

**Rationale.** Measured on the completed matrix, 2026-08-27, S11 at twenty seeds:

| arm | Stage A probe (pre-`investigate`) | whole-record PPC (post) |
|---|---|---|
| B1 — PPC-only, no expansion | 0.850 | **1.000** |
| B4 — retrieval | 0.850 | **0.000** |
| V7 — hybrid | 0.850 | **0.000** |

`contrast` therefore refuses, and says so rather than computing anything:

> contrast unavailable: no replicate of V7 on S11 detected inadequacy, so a
> contrast conditional on inadequacy detection has no answer on this matrix.

**The conditioning event is extinguished by the thing the contrast exists to
measure.** B1 holds no proposal layer, so it leaves the space as it found it and
the whole-record check still fires on every replicate. V7 and B4 expand the
space; by the time `engine.ppc()` is read, the inadequacy it would have detected
has been explained away. Conditioning a treatment-versus-comparator contrast on a
post-treatment quantity selects exactly against the arms that succeeded, and it
does so *more* strongly the better they do. On this matrix it selects both to
zero.

**What the contrast would actually say under each candidate, stated because this
entry can check it and a reader deciding it should not have to.** Both candidate
events are already recorded, so the outcome is computable now rather than after a
decision — and withholding it while proposing the change would leave whoever
decides unable to see whether the proposal favours the arm it is about. On S11,
**V7's and B4's D3 are bit-identical on every one of the twenty seeds**, at
`0.6969628430490871`; B1's is `0.4784019635420516`, also constant. An
arm-invariant conditioning event selects the *same seed set for both arms* by
construction, so under either candidate the V7-versus-B4 paired difference on D3
is **exactly zero — a tie, not a win for the treatment arm.** No choice of
arm-invariant event can turn it into a directional advantage for V7. That is the
disclosure that matters here: this change cannot manufacture the result the
headline claim is hoping for, and it should not be adopted in the belief that it
might.

Found by the invariant-6 lens querying the ledger during review of this entry,
not by the entry's author, who had the same access and reported the detection
rates without the outcome.

**A verified-cold decision is what this should get, and it has not had one.**
Gate A45 settled criterion 4's previous wording *"in a session that had not run
V7 and could not have"* — structurally blind, and recorded as such in
`docs/OPEN-DECISIONS.md`. Both this entry and rank 23 were written the same day
their campaign completed, by a session that had just read the numbers, and they
argue a reading rather than only logging facts. `**Held.**` is what stops that
mattering, since nothing here is implemented. But the eventual pick should go
through the same blindness check A45 had rather than being taken straight off
these two entries.

**The wording supports the other reading, and this is the ambiguity worth
resolving.** §9's headline claim is *"Conditional on **conventional** detection
that the current model space is inadequate"* — "conventional" reads as *detected
by a conventional method*, which is B1 or the Stage A probe, not the treatment
arm's own post-hoc check. §9's shorter statement of the contrast drops the word,
and the implementation followed the shorter one. `report.py` already keeps the
two keys deliberately apart, and its comment on `_PROBE_INADEQUATE` names the
distinction exactly: the whole-record check *"is arm-dependent even in its
verdict"*.

Under the arm's-own reading the contrast is **unanswerable in principle** for any
arm that expands successfully, which is every arm the claim is about. Under the
conventional-detection reading it conditions on 0.850 of S11 replicates (probe)
or 1.000 (B1) and is answerable now, off rows already recorded.

**Not a defect in `contrast`.** It refuses cleanly, names the reason, and its
docstring already states that conditioning is *"a filter on replicates, not a
caption"* and that relaxing it must be explicit. `--contrast` exits 3 rather than
printing a number. Everything behaves; the question is which event the filter
should read.

**Touches.** SPEC §9's primary contrast and its headline claim — the conditioning
event only, not the treatment, the comparator, the scenario or the dimension, all
of which are preregistered and stay. B4 remains the comparator by prior
designation. It touches no instrument, no payload and no address term: both
candidate events are already in every `spec8/3`-or-later row, so whichever is
chosen is readable off the recorded campaign without re-running anything.

**Gate.** `test_a48_the_contrast_conditions_on_an_arm_invariant_event` — a
contrast on a matrix where the treatment expands successfully still has a
conditioning population; the filter admits replicates the *probe* flagged rather
than those the arm's own post-hoc check flagged; and a contrast whose
conditioning population is genuinely empty still refuses rather than reporting
over everything.

**Rank.** 24
**Held.** a cold decision on which event "inadequacy detection" names — the Stage
A probe, B1's detection, or the arm's own whole-record PPC as now — and on
whether §9's two statements of the claim should be reconciled in wording as well
as in code
**Cost.** S. One filter and its gate; no instrument moves and no row is re-run.

---

## Criterion 5 compares two extensions on a dimension that cannot see either

**Idea.** SPEC §12 criterion 5 asks whether V7 *"proposes an S11 extension
exceeding B6-equivalent random structured generation on D3, with a non-overlapping
95% interval"*. `sciagent.eval.scoring.dimension_vector` computes D3 over the
**leading** structure, so a proposed extension reaches that figure only by winning
the posterior. On the recorded campaign no arm's proposal ever does, and the
comparison is a tie to the last bit. Give criterion 5 a quantity a proposal can
move. Four candidates, and choosing between them is the decision:

1. D3 over the best *proposed* extension rather than over the posterior leader;
2. D4, which already reads the entertained set rather than the leader (A26, A42);
3. `ScenarioRun.structural_distance` — distance to the nearest entertained
   structure — which is proposal-sensitive and already in every row;
4. leave the dimension alone and record criterion 5 as answered. A tie is an
   answer, and §12 already says beating a baseline is not an exit criterion.

**Rationale.** Measured 2026-08-27 on the completed matrix, S11 at twenty seeds,
B6 paired to V7 on the same seeds, the same battery `3#2e9ef3a1660578ec` and the
same address:

```
B6/S11  d3 0.6969628430490871   d1 1.5000  escalated 1.700
V7/S11  d3 0.6969628430490871   d1 1.5000  escalated 1.300
paired difference V7 - B6 on D3: exactly 0.000000  [0.000000, 0.000000]
```

Both arms propose — V7 1.3 extensions per replicate, B6 1.7 — and D3 reports the
same plain-Hawkes library member (`d1` 1.5000) for both, and for B4, V1 and V4
besides. Five arms, one number, on all twenty seeds.

**The disclosure that decides what this entry is worth, stated here because the
entry can check it and whoever decides should not have to.** The tie is not only
an instrument artefact. `structural_distance` is a `min` over entertained
structures under the environment's grammar, with no clamp — a genuinely
proposal-sensitive quantity, recorded in every row — and on S11 it is
**1.000000 for all eight arm-cells**, B1 and V1 included, which entertain nothing
beyond the library (`escalated` 0.000) and reach it anyway. No proposal from any
arm, LLM or random or retrieval, ever landed closer to S11's truth than the best
library member. **Re-instrumenting criterion 5 cannot turn this campaign into a
pass**, and it should not be adopted in the belief that it might. What it would
buy is a criterion whose failure means something — today's means only that no
extension led — and a comparison that would separate the arms on a campaign where
one of them did better.

**This is the third instrument the completed matrix has shown to be pointed at a
quantity adjacent to the one its criterion names**, after rank 23's size clause
and rank 24's conditioning event. The pattern the rank 23 entry names holds here
too: the wording was written without a measurement in front of it.

**Not a defect in the scorer.** `dimension_vector`'s docstring is explicit that
D1, D2, D3 and D6 are functions of the leading structure and that D4 and D5 read
the entertained set; the split is deliberate and documented. Criterion 5 picked
the half that cannot see what it asks about.

**Touches.** SPEC §12 criterion 5's dimension only — not the treatment, the
comparator, the scenario or the non-overlap rule, all of which are preregistered
and stay. Under candidates 2, 3 and 4 it touches no instrument, no payload and no
address term, and is readable off the recorded campaign without re-running
anything; candidate 1 is the one that is not.

**Gate.** `test_a49_criterion_five_reads_a_quantity_a_proposal_can_move` — on a
matrix where the treatment's proposal is strictly closer to the truth than the
comparator's, the criterion separates the two arms where D3-over-the-leader
reports them equal; a matrix where neither proposal leads still yields a verdict
rather than a tie by construction; and the non-overlap rule still fails a genuine
overlap.

**Rank.** 25
**Held.** a cold decision on which of the four quantities criterion 5 reads —
and, before that, on whether it should be re-instrumented at all, given that both
proposal-sensitive quantities already recorded answer "tie"
**Cost.** M. Candidates 2, 3 and 4 are a re-read of recorded rows. Candidate 1 is
not: the payload carries `escalated` and `entertained` as counts, not the proposed
structures, so it needs the recorded runs re-scored. That costs compute and no
model calls — V7's S11 cell replays from `spec9-v3.json`, which is `transcript/3`
and the one corpus that still replays, and B6 carries no provider at all.

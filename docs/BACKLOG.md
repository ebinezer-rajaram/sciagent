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
## A mark-arrival cross-diagnostic, so S11 Stage A is detectable

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

## Relevance clause 3 fires on every pair, so A20 is not testing what it reads as

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

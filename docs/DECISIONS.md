# Decisions

Append-only. Newest entries at the bottom. Never edit or delete an entry; if a
decision is superseded, write a new one that says so and links back.

This file holds only what the repository cannot tell you itself:

- **Spec ambiguities** found and how they were resolved.
- **Approaches tried and abandoned**, with the reason they failed.
- **Measured numbers** produced by scripts that are expensive to reproduce.
- **Work left deliberately incomplete**, and what it is waiting on.

It does not hold anything derivable from the code, the tests, `git log`, or
`scripts/status.py`. If a new session could find it by running the status
script, it does not belong here.

New ideas that would touch a frozen architectural decision go in
`docs/BACKLOG.md`, per SPEC §13 — not here.

Entry format:

```
## YYYY-MM-DD — item N: short title

**Decision.** What was settled.
**Why.** The reasoning, including what the alternative would have cost.
**Closes off.** What this rules out, or what still depends on it.
```

---

## 2026-08-01 — item 0: session-state machinery

**Decision.** Build state is reported by `scripts/status.py`, derived from the
repository; durable session knowledge is appended here at the moment it is
produced, not at session end. Acceptance-gate coverage is derived from test
function names (`test_a5_...`) rather than pytest markers.

**Why.** Anything requiring a command to be typed at session end is unreliable —
sessions die by context exhaustion, `/clear`, or a closed laptop, and none of
those offer a chance to run a wrap-up. Capture therefore triggers on the event
(a decision being made), not on the clock. The naming convention was already
followed exactly by `tests/acceptance/test_a01_a05.py`, so deriving from it
required no change to the test suite and no marker registry to keep in sync.

**Measured.** Full suite is 46 tests in 23.5s, too slow for a session-start
hook. Hence two modes: default collection-only (~1.2s, reports which gates have
tests *written*) and `--run` (executes, reports verified pass/fail). The default
mode never claims a gate passes.

**Closes off.** Gate status now depends on the `test_aN_` naming convention. A
test that does not follow it is invisible to the report — it is counted, but
only in the "not named for a gate" line. Renaming a test silently changes which
criterion it is credited to.

**Also.** Rejected parsing pytest's JUnit XML for `--run` outcomes: it would have
meant either an XML parser on a generated file or a `defusedxml` dependency in a
project deliberately limited to numpy and scipy. Pytest's `-rA` short summary
gives the same per-test outcomes as plain text, so no parser exists to harden.

## 2026-08-01 — item 2: lagged dependency edges

**Decision.** `AddDependency` always creates a *lagged* edge: the target at event
`i` reads the source's values at events `< i`. `GenerativeProgram` gained a
`history_edges` field, a subset of `edges`. Acyclicity is enforced on
`edges - history_edges` only; `descendants` still walks the full `edges` set, so
SPEC §3.1's reachability contract is unchanged.

**Why.** Scenario S11's ground truth is `AddDependency(size -> arrival)`, and the
reference programme already contains `arrival -> size`. Taken as instantaneous
edges that is a two-cycle, which §3.1 forbids. The semantics rescue it — §4.5
says "rate depends on *prior* mark sizes" — but the representation had no way to
say so. Lagged edges connect distinct event indices, so the time-unrolled graph
stays acyclic. The alternative was changing S11's ground truth, which would have
cost the only out-of-library scenario in the slice.

**Closes off.** Hawkes' self-loop and S11's cross-component edge are now the same
construct differing only in source, which is deliberate: telling them apart is
what S11 tests. `descendants` is strict (paths of length >= 1), so a component
with a history dependence appears in its own descendant set; collateral for an
intervention is therefore `descendants(target) - {target}`, not `descendants`.

## 2026-08-01 — item 2: the two "change the component" edit types

**Decision.** `ChangeDistributionFamily` replaces the distributional family of a
component's output, keeping it memoryless within its structural class.
`ReparameteriseComponent` keeps the conditional law fixed and replaces a
*constant* parameter with a deterministic function of exogenous state. Periodic
arrivals are therefore a reparameterisation, not a family change: arrivals remain
conditionally Poisson, only the rate becomes a function of time.

**Why.** SPEC §3.1 lists both types and never says what separates them, but the
prefix code charges them differently and `distance` treats a cross-type
difference as larger than a within-type parameter difference, so the boundary
propagates into D1 and D6 for every scenario. Some rule had to be written down
and be auditable. "Stochastic-family change versus deterministic-parameter-
function change" maps exactly onto the §4.2 table.

**Also.** `Component.parameters` is `Mapping[str, float]` by specification, so it
cannot hold structural payloads. Everything structural lives in the `FamilyId`
string — `mixture_of_poisson_2`, `hawkes_exponential`, `poisson_periodic` — and
`family` became a *derived* field: an edit declares structure, and the grammar's
resolution table determines the family the component compiles to. Consequence: a
defect may carry at most one edit per target component, since two would leave the
compiled family ambiguous. `EditGrammar.validate_defect` enforces it.

## 2026-08-01 — item 2: parameter quantisation, and GRID_SIZE = 64

**Decision.** Every edit parameter is quantised onto a grid of `GRID_SIZE = 64`
points declared by the grammar. Grammar-valid parameters must lie exactly on the
grid; `code_length` raises rather than snapping.

**Why.** No prefix code exists over the reals, so acceptance test A4 is only well
posed once the edit space is enumerable. Quantisation is therefore part of the
frozen prior, not an implementation detail: `size` fixes the per-parameter cost
at `log2(size)` bits.

**Measured.** 32 points over the three-decade rate grids gives 25% steps. Near
the confounding operating point the best mean rate the periodic mechanism could
reach was **4.33%** from the reference; the neighbouring amplitudes gave 6.1% and
7.5%. At 64 points the same mechanism reaches **0.30%**. Widening the search from
3 to 6 grid steps at 32 points returned the *identical* answer, which placed the
residual on the grid's resolution rather than on the search.

**Closes off.** The cost is one extra bit per parameter, applied uniformly, so
relative code lengths between mechanisms are unchanged except through the
parameter counts the parsimony prior exists to charge for. Under the ground-truth
grammar: null defect 1 bit, seasonality 23, Hawkes / mixture / S11 24, regime
switching 29. Note `code_length` is **grammar-relative** — the same edit costs
less under `agent_grammar` because naming the construct costs fewer bits — so any
reported D1 or D6 figure must state which grammar produced it.

## 2026-08-01 — item 3: the mechanisms can only be matched at one window

**Decision.** The four mechanisms are calibrated to a declared reference
operating point — mean rate 1.0, inter-arrival dispersion 3.5, Fano factor at
window 2.0 — and not to a matched Fano profile, which is impossible.

**Why.** A renewal process has `Fano(W) -> cv2` from below and can never exceed
it; Hawkes and regime switching exceed it at large `W`. So the Poisson mixture's
Fano-versus-window curve cannot be made to agree with the other three everywhere,
for any parameters. This is structural, not a calibration failure. It is also
exactly what SPEC §4.2 already implies when it says no single diagnostic resolves
the mechanisms and the minimum discriminating plan is three stages.

**Measured** (60 runs of 2000 events; separability is `|mean difference| /
pooled SD`, a single-investigation effect size that does not shrink with more
seeds). Worst separability at the reference point across all six pairs: **1.90**.
Mean rate is fully neutralised at max 0.41. Discrimination appears where §4.2
says it should: Fano at W=0.5 isolates the mixture at 13-15, Fano at W=10
isolates seasonality at 9-10 as its Fano collapses to 1.29 at W ~ period. The
check costs about 15 minutes to reproduce; `scripts/confounding_check.py` prints
the full table.

**Closes off.** "Confounded" in this repository means *at the reference operating
point*, and any scenario relying on a different operating point has to re-measure.

## 2026-08-01 — item 3: calibrate by analytic rate pruning

**Decision.** `scripts/calibrate_mechanisms.py` discards grid neighbours whose
*closed-form* stationary mean rate misses the reference before any simulation
runs, then spends the whole simulation budget on dispersion and the Fano factor,
which have no closed form.

**Why.** The mean rate is a nuisance parameter, so mechanisms that disagree on it
leak their identity through it — an investigator comparing an observed rate
against the known reference rate of 1.0 gets information that has nothing to do
with the science. Every mechanism here has a closed-form rate and it agrees with
the simulated estimate to well within Monte Carlo error, so the constraint is
free.

**Tried and abandoned.** (1) Searching on simulated rate at 32 points: left a
separability of **2.55** between seasonality and the mixture on mean rate alone.
(2) Raising the grid to 64 points *while simultaneously* cutting the search
budget: results came back worse (rates spread 0.990-1.073) and the two changes
were confounded, so the run established nothing. Changing one variable at a time
would have cost one run instead of two. Pruning is also far faster — seasonality
went from 6859 candidates to 19, about a minute.

**Measured.** Final mean rates span 0.990-1.007, a 1.7% spread, down from 8.8%.

## 2026-08-01 — item 3: Hawkes versus regime switching is under-confounded

**Left incomplete.** The calibrated Hawkes and regime-switching mechanisms are
separable at **4.33** on lag-1 count autocorrelation at window 2.0 (Hawkes
0.476 +/- 0.040, regime 0.315 +/- 0.034, 2000-event runs). At the reference
operating point they are the *best*-confounded pair (rate 0.41, cv2 0.23, F2
0.41), but the profile away from it diverges.

**Why it matters.** SPEC §4.2 assigns that pair to stage 3 of the minimum plan —
intervention — and scenario S10 is built on their being non-identifiable below a
budget threshold. If a single run's autocorrelation separates them, S10 is not
non-identifiable and the three-stage plan collapses to two.

**Cause.** The calibration loss constrains only mean rate, dispersion and the
Fano factor at the reference window. Nothing penalises divergence elsewhere in
the profile. An earlier calibration had this pair at max 1.14 across *every*
statistic measured, so a matched pair demonstrably exists on the grid.

**Waiting on.** A decision to re-run `regime_switching` alone with
autocorrelation added to the loss (about ten minutes), versus keeping
autocorrelation out of S10's early diagnostic budget, which is a scenario-design
workaround for a calibration gap. Not done unilaterally because it changes a
frozen scenario parameter after the numbers were reported.

## 2026-08-02 — item 4: the two partition axes

**Decision.** `DataPartition = DEV | HOLDOUT | TEST` labels registered rows and is
the axis A14 restricts. The `exploratory | confirmatory` axis stays a property of
a `Claim` under SPEC §3.3 and does not enter the registry. A partition belonging
to neither `AGENT_REACHABLE` nor `SEALED` is a test failure, not a default.

**Why.** The spec names three vocabularies for one module: §10's comment says
"exploratory / confirmatory / holdout", A14 says "HOLDOUT or TEST", A8 says "200
DEV scenarios". Read as one axis they contradict each other, since a claim's
evidential status and a dataset's seal are independent — a confirmatory claim can
rest on DEV data during development. Two axes satisfy all four references without
amendment. The alternative, a single four-valued enum, would have forced the
registry to store a claim property it has no business knowing.

**Closes off.** `records()` with no partition argument returns AGENT_REACHABLE
rows only, never everything. An unfiltered read that silently included HOLDOUT is
exactly the failure A14 exists to prevent, and defaulting to "all" would have made
the guard depend on every caller remembering to pass an argument.

## 2026-08-02 — item 4: A14 before there is an agent

**Decision.** A14 is discharged by three assertions, not one: that
`AGENT_TOOL_SURFACE` is declared and non-empty, that the analyser finds a planted
violation in `tests/acceptance/fixtures/holdout_violator.py`, and that it clears
`fixtures/clean_tool.py`. The surface names modules that do not exist yet
(`sciagent.systems.*`, `sciagent.experiments.dsl`).

**Why.** Item 12 is the first item containing an agent, so a reachability search
over `src` today starts from no entry points and returns clean whatever the
analyser does. Passing a gate by examining nothing is worse than not having the
gate, because the report then reads "A14 verified". The negative control makes the
analyser's competence a tested property; the positive control stops a checker that
flags everything from also "passing".

**Tried and abandoned.** Resolving calls purely by simple name, with no module
preference. It is sound — it cannot miss a path — but it linked `clean_tool._lookup`
to `holdout_violator._lookup` merely because two unrelated modules used the same
private helper name, so the positive control failed. Resolution now prefers a
definition in the calling module and falls back to the whole tree only for names
the caller does not define, which mirrors how Python resolves a bare call.
Attribute calls (`store.sealed_records(...)`) are never local, so the
over-approximation that keeps the analysis sound is still where it needs to be.

**Closes off.** Item 12 must extend `AGENT_TOOL_SURFACE` when it adds the agent.
If it does not, A14 keeps passing while checking nothing real — the declaration is
now the load-bearing part, and no test can tell that a *newly written* agent module
was omitted from it.

## 2026-08-02 — item 4: three enforcement layers, and why the triggers are not redundant

**Decision.** Append-only is enforced at three levels: no mutating method on the
API, a sqlite authorizer allowlist on the connection, and aborting `BEFORE
UPDATE`/`BEFORE DELETE` triggers in the schema. `PRAGMA recursive_triggers` is on.

**Measured.** The layers do not overlap the way they appear to. Probing all seven
mutation routes: `UPDATE`, `DELETE`, `DROP TABLE`, `ALTER TABLE`, `DROP TRIGGER`
and `PRAGMA writable_schema` are all stopped by the authorizer and never reach the
triggers. `INSERT OR REPLACE` is stopped by **the trigger alone** — the authorizer
is consulted at prepare time and sees only an INSERT, while the row deletion that
REPLACE performs is a runtime event it never authorises. That deletion fires the
delete trigger only when `recursive_triggers` is on; with the pragma off, a single
supported SQL statement silently overwrites a registered result.

**Closes off.** The triggers cannot be dropped as belt-and-braces duplication of
the authorizer, and the pragma cannot be dropped as a performance nicety. The A12
fuzz arm covers `INSERT OR REPLACE` specifically for this reason.

## 2026-08-02 — item 4: EnvVersion is a version string, not yet a content hash

**Left incomplete.** SPEC §3.2 defines `EnvVersion` as a content hash over code
plus reference programme. What exists is a composed version string
(`pointproc/<grammar version>+<library version>`), assembled in the acceptance
test rather than by the environment.

**Why it matters.** The content-hash form is what makes a stale environment
detectable: an edited kernel that nobody remembered to version would currently
produce the same `EnvVersion`, so old rows and new rows would share a content
address while meaning different things. `ExperimentStore` would then raise
`RegistryConflictError` and the failure would look like irreproducibility rather
than like a missed version bump.

**Waiting on.** `core/environment.py` and the `Environment` protocol, which no
backlog item owns — §3 says "implement first", §11 never lists it. The registry
requires only that the field be a stable string, so nothing in item 4 is blocked;
whichever item first needs `Environment` should close this.

## 2026-08-02 — item 3: match autocorrelation for the Hawkes/regime pair

**Decision.** The calibration loss now carries a fourth term, count
autocorrelation at the reference window, applied to regime switching alone and
targeted at the Hawkes value. Regime switching was recalibrated against it.

**Why.** This closes the item left open on 2026-08-01. Constraining only the
mean rate, dispersion and one Fano factor left Hawkes and regime switching
separable at 4.33 standard deviations on autocorrelation, and SPEC §4.2 assigns
that pair to stage 3 of the minimum plan, so no dispersion diagnostic may
separate them. Scenario S10's non-identifiability depended on it.

The term is deliberately *not* applied to the mixture or to seasonality. Both
are meant to be separable by temporal structure — the mixture by having none,
seasonality by phase-locking — so constraining their autocorrelation would
destroy a designed discriminator rather than close a leak.

**Measured.** Hawkes versus regime switching is now separable at **0.22 at
worst across every statistic measured**, at every window: rate 0.22, cv2 0.10,
Fano 0.00 to 0.12, autocorrelation 0.08. The pair is indistinguishable by any
dispersion diagnostic, which is what leaves intervention as the only route.
Nothing else regressed: worst separability at the reference operating point is
unchanged at 1.90, and the mixture and seasonality still separate where SPEC
§4.2 says they should.

**Closes off.** Any future recalibration of regime switching must keep the
autocorrelation term, or S10 silently stops being non-identifiable. The target
is the *Hawkes* value, so recalibrating Hawkes without recalibrating regime
switching afterwards breaks the pairing.

## 2026-08-02 — item 3: what the run-length diagnostic actually measures

**Decision.** `run_length_geometric_deviation` measures temporal dependence in
the run pattern of above-average windows, and its docstring now says so. It is
*not* a measure of the latent regime's sojourn law, which is what it was
originally documented as.

**Why.** The original claim was that a near-zero value indicated memoryless
high-rate periods and therefore a Markov regime. Measurement showed the reverse
ordering, and the reason is elementary: under a homogeneous Poisson process,
whether a window exceeds the mean is an iid Bernoulli trial, so its runs are
*exactly* geometric. The undefective reference therefore scores lowest, not
highest.

**Measured** (window 1.0, eight seeds, 20000 events): reference 0.012, Poisson
mixture 0.016, seasonality 0.271, Hawkes 0.455, regime switching 0.523. The
statistic separates cleanly, but along the axis of temporal dependence rather
than regime geometry — the two independent-window cases sit at or below 0.036
and the three clustered ones at or above 0.148.

**Left incomplete.** SPEC §4.2's actual regime discriminator, the geometric
sojourn distribution of the *latent* high-rate periods, is not recoverable by
thresholding counts at their mean: Poisson noise fragments one long high-rate
period into several short runs, so the observed runs are a thinned version of
the regime's and are not geometric even when the regime's are. Recovering them
needs the regime state inferred, which belongs with the posterior engine
(item 6), not with a summary statistic over an event log.

## 2026-08-02 — item 3: the diagnostic catalogue was not covered by its gates

**Left incomplete, now closed.** Backlog item 3 reads "reference programme, four
mechanisms, diagnostics", and `scripts/status.py` reported it at 2/2 gates while
only three of SPEC §4.3's eight diagnostics existed. A1 and A2 test determinism
and edit soundness; neither touches the catalogue, so the gate count said nothing
about it.

**Decision.** All eight are now implemented in
`environments/pointproc/diagnostics.py`, with the five discriminators covered by
`tests/test_diagnostics.py`. Those tests are deliberately *not* named for an
acceptance criterion, since none applies; they appear in the status report only
in the "not named for a gate" line.

**Closes off.** A gate count is evidence about the criteria that exist, not
about a backlog item being complete. Items 7, 9, and 11 through 15 have no
A-gate at all, so the same gap will recur there and the status report already
says so on its last line.

## 2026-08-03 — item 5: A16 is decided exactly, not sampled

**Decision.** `Prediction.condition` and `refutation` are terms in a small
closed algebra (`sciagent/core/conditions.py`): comparisons and intervals over
one diagnostic, combined with and/or/not. Every term denotes a finite union of
real intervals with explicit open or closed endpoints, and satisfiability over a
diagnostic's declared range is emptiness of an intersection.

**Why.** A16 asks whether a refutation is satisfiable over the range. Sampling
answers that correctly for wide conditions and wrongly for `value == 3.0`, and
the failure is silent. The interval representation decides it, and decides it
without computing a single new float — every operation compares endpoints the
caller supplied — so the verdict cannot drift with floating point.

The price is that conditions relating two diagnostics, or involving a computed
threshold, are not representable. SPEC §3.3 gives `Prediction` one `diagnostic`
field, so nothing in the slice needs them.

**Closes off.** Item 10's verifier gets `evaluate(condition, value)` for free,
and item 8's BOED gets `witness`, which names a concrete outcome that would
refute a hypothesis rather than merely asserting one exists.

## 2026-08-03 — item 5: the condition algebra models the finite reals

**Left incomplete, deliberately.** An infinite endpoint is always open, so a
declared range of `0..inf` means "arbitrarily large" and not "possibly literally
infinite". A diagnostic returning `inf` therefore falls outside the domain, and
`satisfiable_over` could call a refutation unsatisfiable that such an observation
would in fact meet — a false rejection, which is the direction that costs a good
hypothesis rather than the direction that admits a bad one.

**Why it is sound here.** The slice's estimators raise on the inputs that would
produce an infinity rather than returning one, so no such value reaches a
condition. The alternative — modelling the extended reals — makes `[-inf, inf]`
and `(-inf, inf)` two spellings of one set and breaks the emptiness test the
whole decision procedure rests on.

**Waiting on.** An environment whose diagnostics can return an infinity. It will
fail `test_a16_infinite_values_are_outside_the_domain`, which exists so the
assumption cannot be inherited in silence.

## 2026-08-03 — item 5: A16's neighbouring checks are separate codes

**Spec ambiguity, resolved.** A16 states one criterion: reject an unsatisfiable
refutation. Three neighbouring incoherences are decided by the same interval
arithmetic at no extra cost — a refutation covering the whole range, a refutation
overlapping the condition it accompanies, and an unsatisfiable condition. The
spec does not say whether the validator should check them.

**Decision.** It checks all four, under four `RejectionCode`s. A16's own gate
asserts `UNSATISFIABLE_REFUTATION` specifically, so the criterion measures what
it claims and the extras cannot inflate it.

**Measured consequence.** `TAUTOLOGICAL_REFUTATION` never appears alone: a
refutation covering the range leaves the condition either unsatisfiable or
overlapping, so a second code always follows it. That is a property of the
codes, not a bug, and the gate asserts membership rather than equality because
of it.

## 2026-08-03 — item 5: plausibility has no write path at all

**Spec ambiguity, resolved.** A17 asks that no agent-accessible path write
`plausibility`, which admits a guarded write path — a capability token, as A14
uses for sealed partitions.

**Decision.** There is no write path, guarded or otherwise. `HypothesisGraph`
derives the whole vector from the grammar's prefix code as the normalised
`2 ** -code_length(D)` and re-derives it on every transition. No public
constructor, method or keyword accepts the number. `__post_init__` re-derives and
compares by exact equality, so a value planted with `dataclasses.replace` is
refused the next time a graph is built from those nodes.

**Why.** A guarded path is a path, and it has to stay guarded through every later
refactor. Deriving the number means the static half of A17 is a statement about a
thing that does not exist. It also discharges SPEC §0's "the prior is derived,
not fitted" mechanically rather than by discipline.

**Closes off.** Rejection does not renormalise. The prior is a statement about
structure; rejecting a hypothesis is a statement about evidence, and moving mass
between hypotheses on evidential grounds through the prior would be exactly the
leak invariant 2 exists to prevent. A rejected hypothesis keeps its prior mass
and the posterior engine (item 6) zeroes it.

**Waiting on.** Uncompiled nodes. SPEC §3.3 types `program_edit` as `Defect |
None`, "None only before compilation", but a node with no structure has no code
length and so no derived prior. `propose` requires a compiled defect, so the
`None` case is unreachable today; the prose-first proposal path that produces one
arrives with item 12 and will have to say what prior an uncompiled node carries.

## 2026-08-03 — item 5: two placements forced by the layering

**Decision.** `MetricRef` moved from `sciagent/registry/metrics.py` to
`sciagent/core/types.py`, and the condition algebra went to a new
`sciagent/core/conditions.py` rather than into `types.py`.

**Why.** SPEC §10 puts `Prediction` in `core/types.py`. A prediction names a
diagnostic and holds two conditions, and `core` cannot import from `registry` —
`registry` already imports from `core`. Both types had to move down. `registry/
metrics.py` re-exports `MetricRef`, so every existing import site is unchanged.
`conditions.py` is a module SPEC §10 does not list; the alternative was ~200
lines of interval arithmetic inside a module of value types.

**Closes off.** `core/` now has a module that is not in the §10 layout. That is a
layout sketch rather than a frozen decision, but it is the first divergence from
it and later ones should be recorded the same way.

## 2026-08-03 — item 5: the A14 call-graph analyser was blind to keyword writes

**Approach corrected.** `tests/acceptance/callgraph.py` matched restricted
symbols appearing as attributes, bare names and string constants. A17's realistic
violation is none of those: `HypothesisNode` is frozen, so an agent-authored tool
cannot assign to the attribute and would reach past the constructor with
`replace(node, plausibility=...)`, where the symbol is a keyword argument.

**Decision.** `_sealed_symbol` now also matches `ast.keyword` and `ast.arg`, so a
restricted name is caught as a keyword argument or as a parameter name. A14's
six tests are unaffected — none of its sealed symbols appears in either position.

**Closes off.** The analyser over-approximates further than before, which is the
correct direction for a safety gate and the reason its docstring already argued
for soundness over precision.

## 2026-08-03 — item 6: the likelihood is binned, and that is forced

**Decision.** `EmpiricalTableEngine` estimates `p(result | hypothesis)` as a
frequency over a declared finite partition of the diagnostic's range. Bin edges
are frozen literals chosen once from a pilot, never derived from the scenario
under investigation.

**Why.** Three constraints point the same way and only this satisfies all three.
A6 asks that the estimate sit within two Monte Carlo standard errors of an
analytically exact likelihood, which is only attainable for an estimator whose
*estimand is* that likelihood; a binned frequency qualifies and a smoothed
density does not. A7 mandates the Miller-Madow correction, which is an estimator
for the entropy of a *discrete* distribution from counts and is meaningless over
a continuum. And item 8's expected information gain needs a finite outcome space
to be a sum rather than an integral.

**Tried and abandoned.** (1) A kernel density estimate over the simulated
replicates. At its optimal bandwidth the smoothing bias is the *same order* as
the standard error, so the discrepancy A6 measures never shrinks into the
tolerance however many replicates are spent — the criterion would fail by
construction, not by implementation. (2) A multivariate Gaussian synthetic
likelihood, which handles within-experiment correlation exactly but whose
estimand is the Gaussian approximation and not the likelihood, so A6 would be
comparing against the wrong number.

**Closes off.** Every diagnostic that reaches the engine needs declared bin
edges, and they are part of what determines a likelihood. `Discretisation.version`
and `EmpiricalTable.version` are content hashes for that reason, and
`EmpiricalTable.load` refuses a stored table whose templates do not reproduce the
recorded address. Changing an edge invalidates every table built under it.

## 2026-08-03 — item 6: A6 and A7 are the same measurement, read twice

**Spec ambiguity, resolved.** A6 says the estimate is "within 2 MC standard
errors of exact, across 500 trials". A7 says the reported error has "correct
empirical coverage: across 500 repeats, true value falls within +/- 2 SE at least
93% of the time". Read literally these are one test written down twice.

**Decision.** They are separated by what each holds fixed. A6 fixes the
*standard*: agreement with a closed-form likelihood, on programmes whose sampling
distribution is exactly known. A7 fixes the *number*: 93% coverage, plus the
entropy clause. A6 additionally carries a bias check that no coverage statement
makes, and A7 carries a check that the error bar is not merely wide — coverage
bought by an error bar ten times too large would satisfy the criterion as written
and be useless.

**Why the bias check is loose.** Estimating a *log* likelihood from a finite
sample carries a Jensen term of order `-(1 - p) / (2 M p)`. It shrinks with the
replicate count but never vanishes, so a tolerance tight enough to be tested at
two standard errors *of the mean over 500 trials* would measure that term rather
than test the estimator. The threshold is half of one standard error.

**Measured** (500 independent tables, 200 replicates each, 8 equal-probability
cells; the three analytic cases are the reference programme read as a first
inter-arrival gap and as a realised mean rate, and the Poisson mixture read as a
gap, whose laws are Exponential, Gamma-derived and hyperexponential):

| case | coverage | mean deviation | mean SE | realised sd |
|---|---|---|---|---|
| homogeneous Poisson gap | 0.950 | -0.0054 | 0.1885 | 0.1946 |
| homogeneous Poisson rate | 0.948 | -0.0068 | 0.1887 | 0.1960 |
| Poisson mixture gap | 0.964 | -0.0093 | 0.1888 | 0.1754 |

The Jensen bias is 3-5% of one standard error at 200 replicates and scales as
`1/sqrt(M)` relative to it, so at the slice table's 2000 it is about 1.5%.

**Also.** The analytic bins are placed at the exact octiles of the law under
test. Equal-probability cells make the Krichevsky-Trofimov estimator exactly
unbiased — its shrinkage is towards the uniform distribution over cells, which is
then the true one — so what A6 measures is the estimator's Monte Carlo behaviour
and not the smoothing choice. Away from equiprobable cells the KT shrinkage is
`(0.5 - pK/2) / (M + K/2)`, which at 200 replicates and `p = 0.5` is 29% of the
standard error and would dominate the bias check.

**Tried and abandoned.** Keying A6's experiment templates by the metric they
measure. Two of the three cases read the *same* diagnostic under different
programmes, so they need different equal-probability edges; the metric-keyed dict
silently gave the reference gap the mixture's octiles, and the reference's draws
piled into three cells. Coverage came out at 14/500 and the realised spread at
0.83 against a reported 0.15. Nothing raised — the likelihoods were simply wrong.
Templates are keyed by case for that reason.

## 2026-08-03 — item 6: A8 cannot be run under the structural prior

**Spec ambiguity, resolved.** A8 asks for calibration over 200 DEV scenarios.
Calibration in the Bayesian sense requires the scenarios to be drawn from the
prior the posterior uses. SPEC §0 forbids precisely that: the structural
complexity prior is a statement about parsimony, "independent of how often each
defect type happens to appear in the benchmark", and conflating the two "would
have made the posterior an artefact of scenario sampling". Under that prior the
null defect costs 1 bit and every mechanism 23 or more, so a benchmark drawn from
it is 99.9999% nulls and measures nothing.

**Decision.** A8 measures the calibration of the *likelihood*, over a balanced
benchmark, through `EmpiricalTableEngine.log_likelihood_total` and a flat prior.
That is the configuration in which a miscalibration is attributable to the
estimator, which is what A8 exists to gate. The deployed structural-prior
posterior is measured over the same benchmark and reported, so the size of SPEC
§0's deliberate mismatch is a recorded number rather than a later surprise.

`log_likelihood_total` is published for this reason. It is not a write path: the
engine computes it and the acceptance test does the normalisation.

**Also, two smaller readings.** "Credible intervals" over a finite hypothesis set
are read as credible *sets* — the smallest set of hypotheses whose mass reaches
the nominal level. Expected calibration error is computed classwise, over every
(hypothesis, probability) pair, not over the top-ranked one: top-1 ECE on 200
scenarios carries about 0.09 of pure binomial noise, so A8's 0.05 threshold would
be unmeasurable that way and would fail a perfectly calibrated engine.

**Measured** (200 balanced scenarios, four experiments each, 2000-replicate
table): classwise ECE **0.0100** under the flat prior against A8's 0.05 bound,
and **0.1987** under the structural prior. The truth is the modal hypothesis in
86.0% of scenarios under the flat prior and 41.5% under the structural one.
Credible-set coverage is 0.860 at the 50% level, 0.995 at 80% and 1.000 at 90% —
over-covering, as a discrete credible set should, since the smallest set reaching
a level usually overshoots it.

**Closes off.** The 41.5% figure is not a defect. Hawkes and regime switching are
near-tied on every dispersion diagnostic by design, and their code lengths differ
by 5 bits, so the prior rather than the evidence decides between them. Any later
reading of a deployed posterior on the slice has to account for that, as D1 and
D6 figures already have to state which grammar produced them.

## 2026-08-03 — item 6: the check and the likelihood need different estimators

**Decision.** The posterior predictive check reads
`EmpiricalTable.resolved_probabilities`, in which every cell probability is
floored at `3 / M` — the rule-of-three one-sided 95% upper bound for a cell no
replicate reached — and renormalised. The likelihood keeps the unfloored
Krichevsky-Trofimov point estimate.

**Why.** A check is a question about tails, and the tails of a simulated table
are where a point estimate is least trustworthy. A cell no replicate reached is
assigned `0.5 / (M + K/2)`, which is the right point estimate and a badly wrong
statement about how surprising an observation there would be: at 2000 replicates
it is six times smaller than what the simulation budget can actually rule out.
The two estimators answer different questions and are held to different criteria
— a likelihood must be unbiased, which A6 measures, and a check must not
over-reject, which A9 measures.

**Measured** (300 correctly-specified scenarios per configuration, alpha 0.05):

| floor | false-positive rate | power, size mixture | power, size excitation |
|---|---|---|---|
| none | 0.087 | 1.000 | 0.063 |
| 1/M | 0.083 | 1.000 | 0.063 |
| 2/M | 0.067 | 1.000 | 0.050 |
| 3/M | 0.033 | 1.000 | 0.050 |

Unfloored, the realised size is 1.7x nominal — inside A9's 2x bound but with no
margin, and a 100-scenario gate against that bound would be flaky. Floored, the
check is genuinely conservative and loses nothing on a detectable defect. A
600-scenario measurement put the unfloored rate at 9.0% +/- 1.2%.

**Also.** `size_dispersion`'s bin edges above 1.25 were coarsened from seven
cells to three at the same time. No closed-set hypothesis has any mass there, so
the extra edges bought no discrimination, and they cost detection power: the tail
is the sum over every cell no more likely than the observed one, and each
unreached cell contributes the floor.

**Closes off.** Any diagnostic added to a template's outcome space now has a
power cost as well as a discrimination benefit, and the two are traded in the bin
edges. Splitting a region no hypothesis occupies is never free.

## 2026-08-03 — item 6: the PPC has no power against S11's mechanism

**Measured.** Against `SIZE_MIXTURE`, a defect in a component no closed-set
hypothesis touches, the check's detection rate is **1.000**. Against
`SIZE_EXCITATION`, scenario S11's out-of-library mechanism, it is **0.030** —
below the nominal 5% size of the test, which is to say the check cannot see it at
all. Both at alpha 0.05 over 100 scenarios, against the four slice templates.

**Why.** `SIZE_EXCITATION` is calibrated to the same operating point as the four
mechanisms it hides among (rate 0.990, cv2 3.290, F2 3.444), and structurally it
is a Hawkes process whose marks gate the excitation. Every one of the four
templates is a dispersion or correlation statistic of the arrival stream alone,
and Hawkes covers it on all four. Detection would need a diagnostic of the
*joint* behaviour of marks and arrivals — the correlation between a mark's size
and the gap that follows it is the obvious one — and SPEC §4.3's catalogue
contains no such statistic.

**Why it matters.** SPEC §4.6 requirement 1 is "detect inadequacy (S11 Stage A,
via PPC)" and SPEC §12 criterion 4 asks V7 to detect it "at a rate at least
matching B1". B1 is the PPC alone, so the bar is currently 3%, which any system
clears by doing nothing. The conditional in SPEC §9's preregistered contrast —
"conditional on inadequacy detection" — would condition on an event that occurs
three times in a hundred, leaving Stage B measured on a handful of runs.

**Left incomplete, deliberately.** No diagnostic was added. SPEC §4.3's catalogue
is frozen and this is a design question rather than an implementation one, so it
goes to `docs/BACKLOG.md` with the frozen decision it would touch. Item 6's gates
do not depend on it: A9 asks for power to be *reported* per defect type, not to
clear a threshold, and 0.030 is the honest report.

## 2026-08-03 — item 6: what is left open

**Left incomplete.** Three things.

*The engine is mutable.* Every other value type in the repository is a frozen
dataclass returning new instances. SPEC §3.5 gives `expand` and `ppc` signatures
that return a cost and a verdict rather than a new engine, and an investigation
is a growing record, so `EmpiricalTableEngine` accumulates state. It is strictly
append-only — experiments are recorded and hypotheses admitted, and no path
removes or revises either — but it is not a value.

*The prior is deliberately unnormalised.* `log_prior` returns
`-code_length * ln 2` and `posterior` normalises at the end, so the prior's
normalising constant cancels. That is what lets `expand(node)` match SPEC §3.5's
signature exactly: the engine never has to re-derive anyone else's prior, and
A10's "same posterior as including it from the start" holds by bit-equality
rather than to within Monte Carlo error. It also keeps
`sciagent.hypothesis.graph._derive_plausibility` the single place a normalised
prior is written.

*A17 will bite at item 12.* The A14/A17 call-graph analyser cannot tell a read
from a write, and `PLAUSIBILITY_SYMBOLS` over-approximates on purpose. When item
12 adds an agent that can ask for a posterior, any path from the agent tool
surface to `EmpiricalTableEngine.posterior` reaches a function that reads
`HypothesisNode.plausibility`, and A17 will flag it. Nothing in item 6 can fix
that: the resolution is item 12's, and it is the same resolution the deployed
posterior needs anyway. Recorded so it is not discovered as a mystery failure.

**Also.** `SIZE_MIXTURE` was added to `environments/pointproc/mechanisms.py` as
A9's control arm. It is calibrated in one respect only — the mean mark size is
preserved at 0.9988, so the defect does not announce itself through a nuisance
moment — and its squared coefficient of variation is 7.45 against the reference's
1.0. Scenario S8 pairs it with seasonality and will need it calibrated properly;
that belongs with item 11.

**Measured, on cost.** Building the slice's table is 5 structures x 2000
replicates x 512 events, about 10,000 executions at 13 ms, or **2 minutes 50
seconds**. It is cached under a gitignored `.cache/tables/`, keyed on a content
address over the templates, their discretisations, the replicate count and the
seed. The full suite is **2 minutes 27 seconds** with the table cached, up from
23.5 seconds before this item; the largest remaining costs are A6's 500
independent trial tables at 48 s and the rebuild that verifies the cache is
honest at 26 s.

## 2026-08-03 — item 7: three spec ambiguities in the experiment DSL

**Ambiguity 1: how an intervention reaches execution.** SPEC §4.4 lists
`ForceArrival(intervention)` as an operation, but §3.1's `GenerativeProgram`
exposes only `execute(seed, n_events)` and `Component.parameters` is float-valued
by specification, so a forcing schedule cannot ride along as a parameter. The
spec never says how the two meet.

**Resolved** by an optional keyword argument: `execute(seed, n_events, *, clamps:
Mapping[ComponentId, Mapping[int, float]] | None = None)`. A clamped component
takes its value at a clamped index from the schedule and does not draw. An
unclamped call is byte-identical to before, so A1 and the built empirical table
are untouched — verified directly rather than assumed: a worktree at the previous
commit and the current tree both compute table version
`table/689f679f38d4a4d8419468d49eb584b5` and the same cache stem.

A clamp is an argument rather than a field of the programme because it is
`do(X = x)`: an act performed on a model, not part of one. The alternative
considered was an environment-supplied "forced" family, rejected because every
distinct schedule would become a distinct `FamilyId` and the schedule would still
have to be encoded in float parameters — the workaround `Component`'s own
docstring already flags as a last resort.

**Ambiguity 2: `CompareCandidates` does not fit the executor.** Every other §4.4
operation is one execution yielding one `DiagnosticVector`. This one scores
candidate defects against each other, which is what one-step-greedy BOED does.

**Resolved** by typing it in `dsl.py` so the §4.4 operation set is complete, and
refusing it in the executor with `UnknownOperationError` naming item 8. The
comparison rule — which score, which divergence — is item 8's decision, and
inventing one here would mean revising it immediately.

**Ambiguity 3: `Prediction.under`.** SPEC §3.3 writes `under: ExperimentTemplate`,
i.e. the structure. The code has `ExperimentTemplateId`, carrying a note that
said item 7 would replace it.

**Resolved** by keeping the id, and rewriting the note to say why. A `Prediction`
is a frozen value type that gets content-addressed; embedding a whole design in
each one enlarges what is hashed and buys nothing, since `ExperimentDesign.id`
*is* the design's canonical rendering. Retyping would also ripple through
`hypothesis/graph.py`, `hypothesis/validator.py` and A16's 72 tests. This is a
deliberate divergence from the specification's literal type, recorded as one.

**Closes off.** An environment expresses an intervention as a clamp schedule or a
programme rewrite, and as nothing else. An environment needing a third mechanism
has found a contradiction worth writing down rather than a gap to fill locally.

## 2026-08-03 — item 7: a forced arrival must declare how long it observes for

**Tried and abandoned.** Reading a forced-arrival experiment over the whole
remainder of the run. It has no power at all. Post-burst mean rate after a
20-arrival burst at spacing 0.01, 200 replicates per structure:

| structure | whole-run window (492 events) | 20-event window |
|---|---|---|
| hawkes | 1.097 (sd 0.208) | 10.04 (sd 5.09) |
| regime_switching | 1.011 (sd 0.158) | 1.15 (sd 0.85) |
| poisson_mixture | 1.010 (sd 0.081) | 1.26 (sd 0.93) |
| seasonality | 1.004 (sd 0.047) | 1.04 (sd 0.30) |
| null | 1.000 (sd 0.043) | 1.05 (sd 0.23) |

**Why.** The Hawkes kernel decays at rate 0.928, so the excitation a burst
contributes is spent within about one time unit, while a 512-event run at the
reference rate spans some five hundred. Pooling over the whole run averages the
response against five hundred units of baseline and returns the baseline.

**Consequence for the type.** `ForceArrival` therefore carries an `observe`
field: how many events after the last forced one the measurement is read over. It
is a field rather than a compiler constant because the right window is a property
of the mechanism under test — too long dilutes, too short measures noise — which
makes it something an experiment *chooses*, and something item 8's BOED will
choose between. Measured at 500 replicates, AUC for Hawkes against each
alternative:

| window | vs null | vs mixture | vs regime | vs seasonality |
|---|---|---|---|---|
| 10 events | 0.998 | 0.981 | 0.994 | 0.996 |
| 20 events | 0.986 | 0.980 | 0.979 | 0.986 |

Ten events gives the higher AUC but a much worse operating point: against the
Poisson mixture only 58% of Hawkes replicates clear the mixture's 99th
percentile, because over a short window the mixture's own high-rate component
produces bursts that look like excitation. At twenty events the worst case across
all four alternatives is 88%. **Twenty is the chosen default.**

**Why this matters beyond the number.** SPEC §4.2 makes the forced arrival the
only thing separating Hawkes from regime switching, and the pair is calibrated to
be indistinguishable under every dispersion diagnostic. The integration test
measures both sides of that contrast in one place: under
`inter_arrival_dispersion` the pair sits at AUC 0.35–0.65, and under the forced
burst at 0.98. If that gap ever closes, stage 3 of §4.2's minimum discriminating
plan has no experiment, S10's non-identifiability stops being budget-bound, and
A24 would be measuring BOED over a design space containing no discriminating
design.

## 2026-08-03 — item 7: a clamped component skips its draw

**Decision.** A clamped index does not advance the clamped component's random
stream. The alternative — draw, then discard — was considered and rejected.

**Why.** Drawing and discarding would keep a clamped run and an unclamped run on
aligned streams, so the two would differ only where the clamp bites and a forced
arrival's effect could be measured pairwise, which is far more powerful than
measuring it across replicates. That alignment is unattainable here: three of the
five arrival families (Hawkes, periodic, size-excited) simulate by Ogata
thinning, and a thinned draw consumes a number of variates that depends on the
history. The streams diverge at the first post-clamp event whatever is done, so
paying for the discarded draws would buy an alignment that does not survive.

**Consequence.** Every forced-arrival effect in this repository is a
between-replicate comparison. A clamped run is *not* a counterfactual of its
unclamped twin, and reading one as such would be wrong. What is preserved, and
tested, is that clamping one component cannot perturb another's draws at all —
streams are derived by name, so that holds regardless.

## 2026-08-03 — item 7: what is left open

**Left incomplete, deliberately.** Three things.

*`Intervention` and `Estimand` are not added.* SPEC §3.3 lists both among the
interfaces to implement first, and item 7 implements neither. The executor does
derive and record `manipulated` and the collateral set — which is what an
experiment needs, and it is derived from the programme DAG rather than declared,
as §3.3 requires. But the typed estimand and its alignment with an intervention
are what the *verifier* checks, which is A21's business and therefore item 10's.
Adding the types now would mean guessing what the verifier wants from them.

*`ConditionOn` observes two covariates.* Phase and mark size. SPEC §4.2 also
names "conditioning on the inferred state" as a discriminator for regime
switching, and that is deliberately absent: the latent regime is not observable,
and conditioning on the true trace would be reading the answer off the ground
truth. Conditioning on an *inferred* state is a hypothesis-dependent analysis
rather than an experiment operation, so it belongs to whatever performs the
inference and not to the DSL.

*`ForceArrival` accepts only a prefix of the run on a time-valued component.*
Arrival values are absolute times and must ascend; whether a mid-run clamp
violates that depends on times not yet drawn, so it is not checkable at compile
time. Forcing indices `0..k-1` is the case where monotonicity holds by
construction. A mid-run intervention would need either a relative schedule
("insert an arrival `dt` after event `i`") or a two-pass execution, and no
scenario in §4.5 needs one.

**Also.** `outcomes.simulator()` is now an `Executor` with no registry attached,
so the posterior engine's table-building executions and an investigation's
registered experiments travel one code path. They are kept apart by which method
is called: `measure` executes; `run` executes *and* registers *and* charges. An
engine that registered its ten thousand internal simulations would fill the
record with experiments nobody performed and make the reported cost of an
investigation meaningless, so `run` refuses outright when no store is attached
rather than returning something that resembles a registered row.

**Measured, on cost.** `tests/test_experiments.py` runs in **8 seconds**, most of
it the forced-arrival separation measurement — 250 executions of 512 events, at a
burst intensity where Hawkes thinning is expensive. The full suite is **2 minutes
20 seconds** with the table cached, against 2 minutes 27 recorded at item 6.

## 2026-08-03 — item 8: what A24's "greedy optimum" is

**Ambiguity.** SPEC §6.6 A24 requires one-step-greedy BOED's realised
information gain to be "within 10% of the greedy optimum" and does not say what
the optimum is. Three readings were available.

**Resolved** as the *exact-EIG greedy* policy: the reference chooses, at each
step, the design maximising expected information gain computed from outcome
distributions known in closed form, while the implementation chooses from a
finite empirical table of the same distributions. What A24 then bounds is the
implementation's estimation error, which is what "the implementation is a fair
baseline rather than a straw man" is a claim about.

**Why not the other two.** A *hindsight oracle* — the design maximising the gain
actually realised on the outcome drawn — is not a target an expectation-maximiser
can be held to within 10%, so A24 would be unpassable as written. A *non-myopic
DP optimum* measures the cost of myopia, which is a property of greedy selection
rather than of this implementation, and SPEC §11 assigns exhaustive DP to item 11.

**Consequence for the test.** A24's scenarios are synthetic, because "computable
optimal policies" needs distributions that are exactly known and the slice's are
precisely what the empirical table estimates. Two devices make the number mean
something: one outcome is pre-drawn for every (step, design) pair before either
policy runs, so where the policies agree their realised gain is *identical* and
the aggregate difference carries no outcome noise; and both trajectories are
scored by exact Bayes updates, so a badly calibrated estimator cannot report a
large gain by being confidently wrong.

**Measured**, 200 scenarios, 5 hypotheses, 6 designs, 8 cells, horizon 3, table
at 200 replicates:

| policy | mean realised gain | of optimum |
|---|---|---|
| exact-EIG greedy (the reference) | 1.0082 bits | 100% |
| one-step-greedy BOED (table) | 1.0014 bits | **99.33%** |
| uniformly random selection | 0.5598 bits | 55.52% |

The two greedy policies chose the same design on 81% of steps, so the 99.33% is
not agreement by default: they diverge on nearly a fifth of decisions and it
costs almost nothing, because divergence happens where two designs are nearly
tied and the loss from picking either is nearly zero.

**On the replicate count.** A24's table is built at 200 replicates, not the
slice's 2000. The criterion bounds the selector's estimation error, so it is
measured where that error is visible; at 2000 the policies almost never diverge
and the bound would be met without testing anything.

**Closes off.** The random-selection row is asserted, not merely reported:
`test_a24_the_bound_rejects_random_selection` requires random choice to *fail*
the same 10% bound. A gate a broken implementation also passes is not a gate, and
this is what shows A24's threshold discriminates.

## 2026-08-03 — item 8: `CompareCandidates` is selection, not execution

**Decision.** SPEC §4.4's sixth operation is realised by
`sciagent.experiments.boed.compare`, which restricts the belief to the named
candidate defects and ranks the designs that would separate them. It keeps no
executor path, permanently. Item 7 left this open (see "Ambiguity 2" above) and
the refusal in `Executor` now says the reason rather than naming a future item.

**Why.** Every other §4.4 operation is one execution yielding one
`DiagnosticVector`. This one measures nothing: it asks which experiment would
tell the candidates apart, and the answer is an inference output, not a datum.
Giving it an executor path would mean inventing a divergence and a discretisation
for it, registering a row for an experiment nobody performed, and charging budget
for arithmetic — which is the same mistake the engine's table-building
simulations are kept out of the record to avoid.

**Closes off.** Candidates are matched to hypotheses by `defect_key`, so two
hypotheses proposed under different names for one structure are one candidate. A
candidate no hypothesis holds raises rather than being dropped: silently
comparing against fewer candidates than were asked for would be a wrong answer to
a question that looked answered.

## 2026-08-03 — item 8: BOED does not apply the Miller-Madow correction

**Tried and abandoned.** Correcting both terms of `H(Y) - sum_h p(h) H(Y|h)`
with Miller-Madow, which `inference/entropy.py` had been written in anticipation
of. Its module docstring said so, and has been corrected.

**Why abandoned, on principle.** The conditional term's distribution is already
the one the likelihood uses — Krichevsky-Trofimov, from
`EmpiricalTable.probabilities`. Reading it from raw frequencies instead would put
a second definition of a cell probability into the codebase, and `boed.update`
would stop agreeing with `EmpiricalTableEngine.posterior`. Raw frequencies also
assign zero to an unvisited cell, which kills a hypothesis outright on the
evidence of a finite simulation budget.

**Why abandoned, on size.** Measured over the slice's table (2000 replicates,
9 cells, 5 structures × 4 designs):

| quantity | largest over the table |
|---|---|
| KT lift of a row's entropy over plug-in | 0.032 bits |
| Miller-Madow addition to the same row | 0.0036 bits |

KT is already the larger correction by an order of magnitude and in the same
direction, so stacking both would over-correct. What survives is a bias in the
safe direction: shrinkage lifts a peaked row further than a flat one, hence the
conditional term more than the marginal, so EIG comes out **low** by at most
0.024 bits of 1.47 (1.6%), with the ranking of the slice's four designs
unchanged. Understating a design's value cannot make a useless experiment look
informative.

**Closes off.** `entropy_standard_error` was extracted from the private
`_standard_error` and made `inf`-tolerant, so an exact predictive
(`Predictive.samples = math.inf`) reports an error bar of zero rather than
dividing by an infinite sample count by accident.

## 2026-08-03 — item 8: the structural prior leaves BOED nothing to gain at step zero

**Measured, and it matters for item 9.** With no experiment recorded, the
engine's posterior *is* SPEC §0's structural prior, under which the null costs
one bit and every mechanism twenty-three or more. Over the slice's closed set
that puts 1.000000 (to six places) on the null, leaving a posterior entropy of
**0.000012 bits** and a best available expected information gain of **0.000005
bits** across all four slice designs.

**Consequence.** A BOED-only investigation's *first* design is settled by the
tiebreak — ascending template id — and not by the belief, because every design is
within a rounding error of every other. This is not a defect to be fixed: SPEC §0
fixes the prior deliberately and says so. But V1 (item 9) cannot be described as
"choosing" its opening experiment, and any comparison of V1 against a system that
opens differently is comparing tiebreaks on step one.

**Closes off.** Recorded as a property of the prior, not of the selector. The
same measurement is asserted in `tests/test_boed.py`
(`TestTheStructuralPriorLeavesLittleToGain`), so it fails loudly if the prior or
the code lengths move.

## 2026-08-03 — item 8: what is left open

**Left incomplete, deliberately.** Two things.

*Two-step lookahead is not implemented.* SPEC §13 lists it in the backlog at
freeze, and research question R3 — whether an LLM has headroom above lookahead or
merely substitutes for its depth — depends on it existing later, not now. One
step is what §5 specifies for V1.

*`greedy` accepts a caller-supplied belief.* SPEC's second invariant says no code
path reachable from an agent may set a posterior value, and `greedy`'s
`posterior` argument is such a path in principle: a system could hand it a
fabricated distribution. It is not closed by a runtime assertion, because A24
must drive the identical policy from closed-form distributions with no engine in
existence, and there is nothing at that boundary to assert against. What is
provided instead is `boed.plan`, which takes both the belief and the predictive
off the engine and is the entry point a research system is meant to use, so the
fabrication path exists only between framework functions. Closing it properly
belongs with the verifier (item 10), where "did this number come from where it
claims to" is already the subject.

**Measured, on cost.** `tests/test_boed.py` runs in **1.2 seconds** and
`tests/acceptance/test_a24.py` in **2.5 seconds**; neither simulates a
programme, which is why item 8 adds 23 tests for under four seconds where item
7 added 8 seconds for far fewer. The full suite is **2 minutes 50 seconds** with
the table cached, against 2 minutes 20 recorded at item 7.

## 2026-08-03 — item 9: what `Diagnosis.abstain_mass` means

**Ambiguity.** SPEC §3.4 lists `abstain_mass` beside `null_mass` and defines
neither. `null_mass` is unambiguous — the posterior on the empty edit set — but
"abstain" has no reading the specification fixes, and §12 criterion 9 depends on
it: "null and abstain mass exceeding any single defect's mass" on S9 and S10.

**Resolved** as `1 - max_h p(h)`: the mass the system declines to commit to its
own leading answer. Framework-derived in `systems.base.diagnose`, never supplied.

**Why.** It has to discriminate, and it does. On S10 — Hawkes against regime
switching with a starved budget — a well-behaved system splits its belief and
abstain mass is large; a system that concentrates wrongly gets a small one and
fails the criterion. Measured on the four baselines at S10: abstain 0.233 (V1),
0.000 (B1), 0.099 (B4), 0.010 (B5), against a leading mass of 0.767, 1.000,
0.901 and 0.990. The criterion separates them.

**Rejected.** "Mass on hypotheses statistically tied with the leader" needs a tie
test the specification does not define, and would make the number depend on a
significance level nothing else in §3.4 carries.

## 2026-08-03 — item 9: every system starts from the null, and proposes the rest

**Decision.** `systems.base.null_seeded_graph` builds every investigation's
starting graph with the null hypothesis and nothing else, for all four systems.
V1's closed set is *proposed* by V1 at step zero, not handed to it.

**Why.** Two reasons. B1 proposes nothing at all, and a posterior predictive
check needs something to check against — "the observations are unexplained" is
only a statement if there is something failing to explain them. And it makes the
systems comparable: SPEC §5's contrast is between what systems *do*, not between
what they were given, so a V1 that started with five hypotheses while B4 started
with one would be measuring the setup.

**Consequence.** `Diagnosis.proposed_edits` means "introduced during this
investigation", which is why V1 reports four proposals despite being the
closed-set system. SPEC §3.4's comment reads "for agent-created hypotheses"; the
narrower reading — library lookups do not count — needs a library/created
distinction nothing in the framework draws, and would make the field untestable.

## 2026-08-03 — item 9: the baselines on S1-S10, measured

**Measured**, and expensive to reproduce: 40 runs (4 systems × 10 scenarios) on
the 2000-replicate slice table. Correct = leading hypothesis holds the true
structure.

| system | correct | identified (>0.5 mass) | mean structural distance |
|---|---|---|---|
| V1 (BOED, closed set) | 6/10 | 6/10 | 0.100 |
| B1 (PPC only) | 1/10 | 1/10 | 1.111 |
| B4 (retrieval) | 6/10 | 6/10 | 0.100 |
| B5 (beam search) | 1/10 | 1/10 | 0.811 |

**V1 and B4 are indistinguishable on the closed world.** Both get S1, S3, S4, S5,
S6, S9 and both miss S2, S7, S8, S10. That is an early and unsurprising reading
on R1 — with no discriminating design available, optimal *selection* buys nothing
over nearest-neighbour retrieval, because there is nothing to select. It is not
evidence about R1's real question, which is generation.

**Both miss S2 and S7, and both miss them the same way**: the truth is regime
switching and the mass lands on Hawkes (0.861 and 0.774 under V1). SPEC §4.2
calls that pair separable only by a forced arrival, and no design offered here is
one. This is the intervention gap recorded below, appearing exactly where the
specification predicts it.

**B5 scores 1/10 and this is not a straw man.** Its candidates are grid corners
and the truths are interior points of the same grids, so exact-match mass is zero
by construction. What it does do is land in the correct structural cell on four
of nine non-null scenarios and average 0.811 edits from the truth against B1's
1.111. `ScenarioRun.structural_distance` exists to make that visible; reporting
only the proper score would have made a working search look broken.

## 2026-08-03 — item 9: S1-S10 defined here, oracle policy lengths left to item 11

**Ambiguity, and where it was split.** Item 9's gate is "Run on S1-S10" but §11
assigns the twelve scenarios to item 11. Item 11's gate names *oracle policy
lengths* — exhaustive DP where tractable — so the split taken is: S1-S10's
definitions here, because item 9's gate cannot be met without them; S11, S12 and
every oracle length remain item 11's.

**Left incomplete, deliberately.** Two things item 11 must settle.

*S10's budget is asserted, not derived.* SPEC §4.5 defines S10 by a budget "below
the discriminating threshold"; where that threshold sits is what item 11's DP
computes. Two experiments is set on the argument that §4.2's minimum plan is
three stages and the third is unavailable here. It happens to be sufficient — no
system identifies S10 — but sufficiency is not the same as being *at* the
threshold. `registry/budget.py` said this data would arrive with item 11; that
note is now partly stale and partly still true.

*S5-S7 are not separately tuned.* §4.2 already calibrates all four mechanisms to
be mutually indistinguishable under dispersion, so a confounded scenario does not
need its alternative made plausible. What separates S5-S7 from S1-S4 here is the
seed and how the result is read.

## 2026-08-03 — item 9: the observational design set caps V1 on S2, S5, S7 and S10

**Measured, and it is a ceiling rather than a defect.** `slice_designs()` holds
no `ForceArrival`, because the empirical table is not calibrated on one (item 7
recorded why). SPEC §4.2 makes the forced arrival the only discriminator of
Hawkes from regime switching. So on every scenario turning on that pair, no
system offered these designs can resolve it, and none does.

**Consequence for §12.** Criterion 6 — "a discriminating three-stage plan on at
least two of S5-S7" — is unreachable until an intervention template joins the
table. That is item 11's, which needs the full design space for its DP anyway.
Adding it is a table rebuild: five structures × four templates × 2000 replicates,
plus the new template across all of them.

**Not worked around.** V1 could have been given the intervention as an
unregistered design, but then its likelihood would be read off a table with no
row for it, and the posterior would be confidently wrong rather than merely
uninformed.

## 2026-08-03 — item 9: B1's Stage A detection is destroyed by its own multiplicity correction

**Measured.** B1 holds only the null, so on S1-S7 the hypothesis space is
inadequate by construction and the check ought to say so. It does not.

| budget | min per-experiment p | corrected p | detects? |
|---|---|---|---|
| 1 | 0.0134 | 0.0134 | yes |
| 2 | 0.0133 | 0.0265 | yes |
| 4 | 0.0133 | 0.0523 | **no** |
| 8 | 0.0133 | 0.1019 | **no** |
| 16 | 0.0133 | 0.1935 | **no** |

`inference/ppc.py` combines experiments by taking the *minimum* p-value under a
Sidak correction for the number of tests. Eight experiments all pointing the same
way are treated as eight chances to be wrong and never as accumulating evidence,
so **detection degrades monotonically as the budget grows**. The per-experiment
signal is unchanged at 0.0133 throughout; only the penalty moves.

**Detection rate over S1-S10 at the standard budget is 2/10**, and both
detections confirm the mechanism rather than contradicting it. S8 fires because
its size-distribution mixture is strong enough (per-experiment 0.0030) to survive
the same correction. S10 fires because it is *budget-starved* — two experiments,
so a small penalty — which means the scenario carrying the least evidence is the
only arrival-mechanism scenario B1 detects.

**Consequence.** SPEC §12 criterion 4 asks V7 to detect S11 "at a rate at least
matching B1". A floor of near-zero for this reason is clearable by a system that
does nothing, which is the same failure `docs/BACKLOG.md`'s mark-arrival
cross-diagnostic entry describes, arriving by a second and independent route:
that entry is about *which diagnostics exist*, this one is about *how evidence
across experiments is combined*. Fixing either alone leaves the other. Recorded
as a backlog entry rather than acted on, because `ppc.py` is item 6's and gated
by A9, which still passes.

## 2026-08-03 — item 9: what the baselines cost, and how it is kept down

**Measured.** The gate is 3 minutes 46 seconds cold and **10.8 seconds** warm.
Two caches do the work, and without either the module is about six minutes on
every run.

*B5's search table.* Scoring a candidate means simulating it. The beam visits all
48 single edits the agent grammar licenses at corner resolution; at 25 replicates
over four templates that is about 65 seconds, cached to disk by
`tests/slice_tables.py::search_table`. Candidates the environment cannot measure
are skipped there and scored `-inf` by `table_fit` — an edit space's corners hold
parameterisations whose programmes are degenerate, and a search that enumerates
corners finds them.

*The engine's own rows.* This was the larger cost and the less obvious one. When
B5 proposes a structure outside the closed set, `EmpiricalTableEngine.expand`
must simulate its row at the slice's **full 2000 replicates** — about 33 seconds,
once per scenario, ten times over. The gate now threads one growing table through
all forty runs and persists it, so each distinct proposal is simulated once per
machine. A row is a pure function of `(defect, template, seed)`, so this changes
what the gate costs and not what it concludes.

**Full suite: 2 minutes 50 seconds warm**, unchanged from item 8, because the
gate's 10.8 seconds is absorbed by the table builds already there.

## 2026-08-03 — item 9: `EmpiricalTableEngine.ensure_structure` exists to break an ordering cycle

**Decision.** A public method that fills a structure's table row without
registering a hypothesis, extracted from `expand`.

**Why.** A hypothesis needs a refutable prediction before the graph will admit
it; a table-derived prediction needs the structure's row; that row is what
`expand` would have filled. Deriving the prediction and admitting the hypothesis
each waited for the other. `ensure_structure` is the half of `expand` that does
not touch belief, so `Investigation.propose` can fill the row, derive the
prediction from it, and then admit.

**Consequence.** `Investigation.propose` takes no predictions by default and
derives them, which is why no baseline in `systems/baselines/` contains a
threshold. A system says which structure it wants entertained; the framework says
what that structure predicts and what would refute it.

## 2026-08-03 — item 9: A17 went live for the first time, and it fails

**Left incomplete, and it is a gate rather than a detail.** `AGENT_TOOL_SURFACE`
in `registry/partitions.py` was declared at item 4 as `sciagent.systems`,
`sciagent.systems.*` and `sciagent.experiments.dsl`. No module matched it until
now — `test_a16_a18.py`'s own docstring says so, and says the static half of A17
was therefore discharged by fixtures. **Item 9 creates `sciagent/systems/`, so
A17's analysis over the real tree became non-vacuous for the first time, and it
immediately reports three paths.**

All three run
`BeamSearch.investigate -> HypothesisGraph.propose -> HypothesisGraph._rebuilt`,
reaching `_derive_plausibility` and `plausibility`.

**What the analyser is actually seeing.** `callgraph.resolve` matches calls by
simple name, preferring a definition in the calling module. So:

- `systems/base.py` defines `Investigation.propose`, which *shadows*
  `HypothesisGraph.propose`. Its own genuine call to `self._graph.propose(...)`
  resolves back to itself and the path is never reported.
- `systems/baselines/beam_search.py` defines no `propose`, so
  `investigation.propose(...)` falls back to every `propose` in the tree, picks
  up the graph's, and the path appears.

The reported paths are therefore a name collision, but the *unreported* one is
real: the systems layer does call `HypothesisGraph.propose`, and base.py is clean
only by accident of shadowing. That is worse than failing, because the gate looks
green for a reason unrelated to the invariant.

**Not resolved here, deliberately.** Every available fix changes something item 9
does not own — `AGENT_TOOL_SURFACE`, `PLAUSIBILITY_SYMBOLS`, the analyser's
read-versus-write over-approximation, or A17 itself — and CLAUDE.md forbids
weakening an acceptance test to make a subsystem pass. The runtime half of A17
still holds and is tested independently: `HypothesisGraph.__post_init__` re-derives
every plausibility from the prefix code and refuses a forged one, which is what
actually stops a value being planted.

## 2026-08-03 — item 9: A17 resolved by naming the derivation boundary

**Supersedes the entry above**, which recorded A17 as left unresolved. Appended
rather than edited, per this repository's append-only rule for decisions.

**Ambiguity, and it is a real one.** A17 forbids any agent-reachable path from
touching a plausibility symbol. A research system must be able to introduce a
hypothesis, and introducing one necessarily runs the framework's derivation --
which is SPEC's second invariant being *satisfied*, not violated: the framework
writes the number. So every correct systems layer has a path into
`_derive_plausibility`, and the criterion as literally stated forbids the design
it exists to protect. That is a demonstrated contradiction in SPEC §13's sense,
with the failing test to document it.

**Resolved** by `PLAUSIBILITY_DERIVATION` in `hypothesis/graph.py`: three
functions licensed to make the write, whose *own* references the analyser
exempts. `callgraph.analyse` gained a `licensed` parameter.

**Why these three and no others.** They are exactly the functions that
syntactically touch a plausibility symbol, and the test
`test_a17_every_licensed_function_still_needs_its_licence` asserts that equality
in both directions -- so an entry that stops being needed fails the suite, and a
new writer cannot be added without either fixing it or consciously widening the
declaration. `_derive_plausibility` is deliberately *not* licensed: it reads the
grammar and the edits only, so a write appearing inside it would still fail A17.

**Why it is a boundary and not a switch.** A licensed function is still
traversed; only its own references are ignored. Two things hold that in place. A
test licenses the negative-control fixture's entry point and asserts the planted
write *one hop down* is still caught. And a mutation was run by hand: planting
`object.__setattr__(node, "plausibility", 0.99)` in B1's `investigate` fails A17
with the path reported, and removing it passes.

**The thing worth remembering.** Before the boundary existed, `systems/base.py`
was reported clean while genuinely calling `HypothesisGraph.propose` -- because
`callgraph` resolves by simple name preferring the calling module, and
`Investigation.propose` shadowed the graph's method of the same name. Only
`beam_search.py`, which defines no `propose`, surfaced the path. A gate that is
green because of a name collision is worse than one that is red, and nothing in
the suite would have caught it: `test_a17_the_surface_declaration_matches_real_modules`
is the assertion added so that A17 going vacuous is itself a failure.

## 2026-08-03 — item 10: SPEC §7.1 clause 6 is subsumed by clause 1

**Ambiguity, resolved as a redundancy in the specification.** §7.1 makes an
experiment relevant to a claim if (1) its target hypothesis is "within 2 edges"
of the claim's subject in the hypothesis graph, or (6) its target is
`AlternativeTo` or `Contradicts` the subject. Those are the only two relation
types the graph has, and a relation is one edge, so every clause 6 case lies
inside clause 1's two-edge window. Clause 6 is therefore never the sole reason
an experiment is relevant.

**Kept anyway, and asserted.** `test_a20_clause_six_is_subsumed_by_clause_one`
pins both halves: a directly-related target fires clauses 1 and 6 together, and
a two-hop target fires clause 1 alone, so clause 1 is strictly wider. Dropping
clause 6 would make the implementation disagree with a frozen document over what
is in fact a harmless redundancy; leaving it undocumented would make the next
reader think one of the two was broken.

**What would make them differ.** A relation type outside the rivalry pair --
refinement, decomposition, derivation. Clause 1 would then reach targets clause 6
does not, and the two would be genuinely independent.

## 2026-08-03 — item 10: what a `Claim` carries that SPEC §3.3 does not list

**Three additions, each forced.**

*`id`.* §3.3 gives `Claim` no identifier. A verdict has to name the claim it is
about, and `verify/contradiction.py` compares a claim against those already
accepted, which is not expressible over anonymous values. Same reason
`Prediction` acquired one at item 5.

*`subject_kind`.* §3.3 types the subject `HypothesisId | ComponentId`. Both are
`NewType`s over `str`, so at runtime they are indistinguishable, and a verifier
that must look the subject up in either the hypothesis graph or the programme
cannot tell from the value which one to ask. Resolving by lookup — "if the graph
holds it, it is a hypothesis" — is ambiguous exactly when an id is both.

*`intervention`.* §3.3 defines `Intervention` as a standalone type and does not
attach it to anything. §7.2's licensing table reads two things no experiment
record can supply: which mediators are *declared* blocked, and which assumptions
are *listed*. Both are claimant declarations, so they have to travel with the
claim; without them the causal table has nothing to read and A21 is unstatable.

**Also resolved:** an estimand's `target` and `outcome` are `ComponentId`, not
metric names. §7.2's requirements quantify over paths in the programme DAG, which
is a graph over components. What an effect was *measured* on is
`EffectEstimate.metric`, which is a separate field.

## 2026-08-03 — item 10: the strength ladder, which SPEC does not give

**Ambiguity.** §3.3 gives a claim four strengths — `suggests`, `supports`,
`establishes`, `refutes` — and nowhere says what earns each. Every figure the
verifier reports about claim quality depends on the answer.

**Resolved** as two channels, in `verify/statistical.py`. A claim carrying an
`EffectEstimate` is graded on the interval: `suggests` needs a non-zero point,
`supports` needs the interval to exclude zero, `establishes` needs that plus two
experiments in each arm, and `refutes` needs the measured direction to oppose the
preregistered one. A claim carrying no effect, about a hypothesis that made
predictions, is graded on those predictions using item 5's exact condition
algebra: the prediction's `condition` or its `refutation` evaluated at the
observed value. A claim with neither channel is **referred**, not refused.

**The one non-obvious rule.** An effect with a standard error of zero reaches
`suggests` and no further. One observation per arm gives a sample variance of
zero, hence a zero-width interval, which "excludes zero" for any non-zero point
estimate — so without this rule a single pair of runs would establish anything.

## 2026-08-03 — item 10: A23 measured at 90.5%, and why it is that close

**Measured.** 1904 claims from the item 9 baseline runs (S1–S10 × V1/B1/B4/B5),
generated by `campaign.claims_from_run` as the modality × strength cross-product
per hypothesis carrying mass. **1724 adjudicated, 90.546%**, against A23's 90%.
Verdicts: 366 accept (19.2%), 1358 reject (71.3%), 180 refer (9.5%). Building
them costs 9.3 s with the gate table warm.

**Every referral has one cause and one location.** All 180 are `statistical`
referrals, and all of them are V1 on S5, S6 and S10 — 60 each, 22.5% of V1's
claims, and 0% of B1's, B4's and B5's. A hypothesis's framework-derived
prediction is made under the scenario's *first* design (`table_prediction` in
`systems/base.py`), and on those three scenarios BOED never selects that design.
No cited experiment then bears on any prediction, the prediction channel has
nothing to evaluate, and referral is the correct answer rather than a defect.

**The margin is thin and the cause is structural**, not statistical noise: one
more scenario on which V1 avoids the first design would drop the figure below
90%. The remedy — a hypothesis carrying a prediction per offered design rather
than one — is item 6/9 machinery, so it is in `BACKLOG.md` rather than done here.
It was deliberately not done to make this gate more comfortable.

**Re-measure at item 12**, against real agent claims, which is what A23 actually
names. This figure stands in for that and should not be quoted as it.

## 2026-08-03 — item 10: what is left open

**Left incomplete, deliberately. Four things.**

*No baseline declares experiment targets.* `Investigation.run` takes an optional
`targets` argument naming the hypotheses an experiment was aimed at, which is
what §7.1 clause 1 reads. No SPEC §5 baseline passes it, so on real slice runs
clauses 1 and 6 never fire and relevance rests on clauses 2 to 5. A20's
constructed cases exercise all six. V1 could supply it for free — BOED already
knows the candidate set it is separating — and item 12's V7 should.

*A controlled direct effect is not constructible from a single slice experiment.*
It needs one component manipulated and another held fixed at once, and the DSL
gives one operation per design: `AblateComponent` holds exactly one component
fixed and manipulates nothing else. A21's pairs are therefore built as
`EvidenceRecord`s directly, which is what "100 constructed intervention/estimand
pairs" licenses. This is a limit of the §4.4 operation set, not of the licensing
rule.

*Path enumeration is exponential.* `verify/causal.py` enumerates every directed
path between two components, because §7.2's direct-effect row asks whether
*every* mediating path is blocked — a statement about paths, not about
reachability. The slice's DAG has four components. A larger environment needs a
different algorithm, and approximating it would license claims it should not.

*`boed.greedy`'s caller-supplied belief is still open.* Item 8 recorded that
closing it "belongs with the verifier (item 10)". It does not close here: A19
checks that a *claim's* figures come from the registry, which is the analogous
guarantee one level up, but nothing yet stops a research system handing `greedy`
a fabricated posterior. That path runs between framework functions only, and
closing it properly needs the agent that could abuse it — item 12.

**Measured, on cost.** `tests/acceptance/test_a19_a23.py` runs in **10.0 s**
(9.3 s of it the A23 slice runs) and `tests/test_verify.py` in **1.3 s**. The
full suite is **3 minutes 3 seconds** with the tables cached, against 2 minutes
50 recorded at item 8; item 10 adds 86 tests for about 11 seconds.

## 2026-08-04 — item 11: what an "oracle policy length" is

**Ambiguity.** SPEC §11 gives item 11 the gate "oracle policy lengths —
exhaustive DP where tractable, planning-baseline lower bound otherwise" and
defines neither the quantity nor the threshold a policy is aiming at.

**Resolved** as the minimum over adaptive policies of `E[T]`, where `T` is the
first experiment after which the posterior mass on the true structure exceeds
**0.5** — `ClosedWorldScore.identified`'s threshold, so the oracle and the
systems it judges are scored on one criterion and "the oracle needed 3, B4
needed 6" is a comparison rather than two unrelated numbers.

**Two distributions, deliberately.** An outcome is drawn from the row of the
defect the scenario *executes* (truth and nuisance together); the belief is
updated from what the hypotheses predict. On a well-specified scenario these are
the same distribution. On S12 they are not, and that is the scenario; on S11 the
truth is not in the hypothesis set at all, so the mass on it is zero forever and
the reported reach probability is zero. That is not a failure of the computation
— it is the finding that no policy over the agent's closed set identifies S11.

**Exhaustive to horizon 3.** The belief tree branches by designs times outcome
cells, about sixty children per node on the slice: 2×10⁵ nodes at three and
1.5×10⁷ at four. A path unresolved at the horizon is charged one further
experiment, the least any continuation could cost, so `expected_steps` is exact
when every path resolves and a **lower bound** otherwise.

**Measured**, on the calibrated 2000-replicate table, five designs, the closed
set entertained, agent-grammar prior:

| id | E[T] | reach | greedy | greedy finishes | floor | prior mass on truth | optimal opening |
|---|---|---|---|---|---|---|---|
| S1 | 3.008 | 0.992 | 3.151 | 1.000 | 2.13 | 2.4e-7 | phase-conditioned |
| S2 | ≥4.000 | 0.000 | 5.637 | 1.000 | 2.70 | 3.7e-9 | **forced arrival** |
| S3 | 3.027 | 0.973 | 3.137 | 1.000 | 2.08 | 2.4e-7 | autocorrelation |
| S4 | 3.017 | 0.983 | 3.526 | 1.000 | 2.19 | 1.2e-7 | phase-conditioned |
| S5 | 3.008 | 0.992 | 3.178 | 1.000 | 2.13 | 2.4e-7 | phase-conditioned |
| S6 | 3.027 | 0.973 | 3.141 | 1.000 | 2.08 | 2.4e-7 | autocorrelation |
| S7 | ≥4.000 | 0.000 | 5.618 | 1.000 | 2.70 | 3.7e-9 | **forced arrival** |
| S8 | ≥4.000 | 0.000 | — | 0.000 | inf | 0 | — |
| S9 | 0.000 | 1.000 | 0.000 | 1.000 | 0.00 | 1.0 | none |
| S10 | 3.008 | 0.992 | 3.158 | 1.000 | 2.13 | 2.4e-7 | phase-conditioned |
| S11 | ≥4.000 | 0.000 | — | 0.000 | inf | 0 | — |
| S12 | ≥4.000 | 0.000 | 8.025 | **0.334** | 2.70 | 3.7e-9 | forced arrival |

**Three things to read off it.**

*S10's budget of 2 is genuinely below the threshold.* The optimum is at least
3.008, so the scenario is non-identifiable by measurement rather than by the
assertion item 9 left behind. The number that makes it so is not the missing
intervention any more — that arrived here — but the budget, which is what SPEC
§4.5 says S10 is about.

*S8 and S11 are unreachable, not merely hard.* Neither truth is in the closed
set, so no policy over it ever crosses the threshold. S11 is designed that way;
S8's compound is a second case of the same thing and worth naming, since item 9
recorded V1 and B4 "missing" S8 without saying it was impossible for them.

*The parsimony prior sets the floor everywhere.* The prior puts 2.4e-7 on Hawkes
and 3.7e-9 on regime switching against 1.0 on the null, so every non-null
scenario needs three experiments before anything else is true of it. SPEC §0
fixes that prior deliberately; it means a system taking three experiments on S1
is at the optimum and not slow.

**Closes off.** `Scenario.nuisance` is executed and never scored, so an oracle
length is a property of `(truth, executed world, designs, prior)` and not of the
seed. S1 and S5, S2 and S7, S3 and S6 therefore have identical lengths, which
`tests/test_oracle.py` asserts rather than assumes.

## 2026-08-04 — item 11: the first evidence bound was not a bound

**Tried and abandoned.** A floor of `log2(0.5 / prior mass on truth) / log2(cells
of the widest design)`, on the argument that no experiment carries more bits than
its outcome space holds.

**Why it is wrong.** That bounds *mutual information*, which is the expected
reduction in entropy. It says nothing about the odds on one hypothesis, which a
single outcome moves by `log2(p_truth(cell) / p_rival(cell))` — unbounded when
one hypothesis nearly excludes a cell another frequents, which is exactly what a
discriminating design is built to arrange.

**Caught by measurement, not by inspection.** It returned 6.91 experiments for S2
where greedy achieved 5.64. A lower bound above an achieved value is not a bound,
and `tests/test_oracle.py::test_the_floor_never_exceeds_what_greedy_achieves`
exists so that any future version has to survive the same comparison.

**Replaced** by the prior odds against each rival divided by the largest
log-likelihood ratio any cell of any design affords against that rival, maximised
over rivals. Holding more than half the mass requires out-weighing every rival,
so a bound derived from a necessary condition is valid; it assumes every
experiment returns the single most discriminating cell available, which no world
obliges, so it is weak. It is 2.1–2.7 experiments across the slice against
optimum values of 3.0–4.0.

**Closes off.** A bound that was quietly too large would have flattered every
system measured against it, in the direction that makes an agent look closer to
optimal than it is.

## 2026-08-04 — item 11: the intervention joined the calibrated design set

**Decision.** `slice_designs()` is five designs, not four: the four
observational ones unchanged plus `force[arrival@...|20]:mean_rate`, a burst of
twenty arrivals at spacing 0.01 read over the twenty recorded events that
follow. Item 9 recorded the cost of its absence — V1 and B4 both missed S2, S7
and S10 — and left it here because item 11's dynamic programming needs a design
space containing a discriminating experiment before a policy length over it
means anything.

**Measured**, `scripts/pilot_forced_edges.py`, 300 replicates per structure:
post-burst `mean_rate` quantiles.

| structure | 1% | 10% | 50% | 90% | 99% |
|---|---|---|---|---|---|
| hawkes | 1.138 | 3.986 | 9.098 | 16.866 | 27.552 |
| null | 0.611 | 0.769 | 0.997 | 1.330 | 1.840 |
| poisson_mixture | 0.503 | 0.675 | 1.030 | 1.881 | 3.626 |
| regime_switching | 0.263 | 0.429 | 0.895 | 2.225 | 4.033 |
| seasonality | 0.599 | 0.827 | 0.907 | 1.583 | 1.765 |
| size_excitation (S11) | 0.729 | 2.676 | 6.382 | 12.764 | 20.541 |

AUC of Hawkes against the others: 0.984–0.992, and against S11's size excitation
**0.674** — the two excited mechanisms are not separated by this design either,
which is what makes S11 hard rather than merely absent.

**Edges frozen** at `(0.6, 0.9, 1.2, 1.6, 2.2, 3.2, 5.0, 8.0, 12.0, 18.0)`, on
the same principle as the other four: fine where the unexcited structures sit
(0.5–2, where a response has to be told from its absence), coarse above 3 where
only Hawkes lives. Every one of the eleven cells is occupied by some structure at
300 replicates, so none costs the posterior predictive check its rule-of-three
floor for nothing. Regime switching's distinctive low tail — 0.287 of its mass
below 0.6, against 0.007 for the null — is what the first edge is for.

**What it bought**, measured against a re-run of the oracle over the four
observational designs alone, on S7:

| design set | optimum | greedy finishes | greedy length | floor |
|---|---|---|---|---|
| five, with the intervention | ≥4.000 | 1.000 | 5.618 | 2.70 |
| four, observational only | ≥4.000 | **0.276** | 10.99 | 5.83 |

**What it cost.** The table's content address covers the templates, so adding one
invalidates every cached row: the cold build is now **4 minutes 36 seconds** for
the closed set (five structures × 2000 replicates × five templates), against 2
minutes 50 at item 6, plus about 90 seconds for each of the three worlds no
closed-set row covers (S8's compound, S11's mechanism, S12's censored regime).
B5's search table is 64 seconds more than before. The item 9 gate is **8 minutes
22 seconds cold** and the numbers below it are all re-measured.

## 2026-08-04 — item 11: the empirical table's address does not cover the compiler

**Found by breaking it.** Changing what a `ForceArrival` is read over — from the
next `observe` *indices* of the run to the first `observe` *recorded* events
after the burst — changes every forced-arrival row. Nothing noticed:
`EmpiricalTable.version` hashes the templates, their discretisations, the
replicate count and the seed, and a template says what is measured, not what is
done. The cached rows stayed readable and became silently wrong.

**Resolved** by `OPERATIONS_VERSION` in `environments/pointproc/operations.py`,
carried into `ENV_VERSION` alongside the grammar and family-library versions, and
by mixing `ENV_VERSION` into the test cache keys (`tests/slice_tables.py`), where
the environment is in scope and the table is not. A change to what an operation
means is now a cache miss.

**Why not in the framework.** `EmpiricalTable` is domain-independent and is handed
a `simulate` callable; it cannot know that the callable's meaning moved. The
registry *is* addressed correctly — `ExperimentKey` carries `env_version` — so
this was a hole in the cache alone, and the fix belongs at the layer that knows
which environment it is caching.

**Closes off.** Any future change to `operations.py`'s semantics must bump
`OPERATIONS_VERSION`. Nothing enforces that automatically; a content hash over
the module would, and is left undone deliberately — it would make every cache
key move on a comment edit, and the version string is the same promise the
grammar and library already make.

## 2026-08-04 — item 11: S12's censoring is realised at the record, not in the draw

**Ambiguity.** SPEC §4.5 gives S12 "an observation-level censoring nuisance" and
§3.1 gives no way to express one: a `GenerativeProgram` draws a value per
component per event, every drawn value must be finite, and a metric is a pure
function of an event log. There is no "not recorded".

**Resolved** by splitting declaration from realisation. The family
`identity_periodic_censored` on `obs` *declares* the observation process in two
parameters, `period` and `duty`, and draws exactly what the identity family
draws — a censored event still happened. The environment's compiler *realises*
it, as a restriction of the log composed before the operation's own, because
`CompiledOperation.restrict` is the one place in this architecture where a run
becomes a record.

**Why not a sentinel value.** Writing a marker into `obs` would put a number into
the log that every metric would have to know not to read, which contradicts SPEC
§3.2's "a metric is a function of an event log and nothing else". Nothing else
was available: an `AddDependency(arrival → obs)` would have made the censoring
depend on the *previous* arrival's time, since `apply` makes dependency edges
lagged.

**Consequence for `ForceArrival`.** Its window was "the next `observe` indices of
the run", which under censoring can select events that were never recorded — in
one candidate window it left a single event and the table build died. It is now
"the first `observe` recorded events whose time exceeds the last forced one",
which is identical on an uncensored run and is what the operation means anyway:
an investigator reads the events that reach them.

**The nuisance is executed and never scored.** `Scenario.nuisance` is a second
defect applied with the truth (`Scenario.executed`) and absent from every score,
so S12's correct diagnosis is regime switching alone — SPEC §12 criterion 7.
Folding it into the truth would have made S12 a decomposition task, which S8
already is. It is licensed by `edit_grammar` and not by `agent_grammar`, so no
system can propose it: a system that could explain the censoring away would not
be recovering from a garden path.

**Calibrated** at period 6.681, duty 0.599 — an observed stretch of 4.00
followed by a censored one of 2.68 — by `scripts/calibrate_censoring.py`. Both
halves of §4.5's description are constraints, and both are measured.

*The garden path.* Under regime switching the censored record reads as
seasonality on the two arrival-dispersion diagnostics: count autocorrelation
0.193 against seasonality's 0.199 and the truth's own 0.433, inter-arrival
dispersion 3.40 against 3.42. The power spectrum carries a peak at 0.150 — the
censoring frequency — sharp to a standard deviation of 0.001, where regime
switching alone has no peak at all (0.017, sd 0.018).

*The path can be left.* The period is deliberately not `CANDIDATE_PERIOD`
(11.559) nor a low harmonic of it, so conditioning on the phase a seasonality
hypothesis proposes does not remove the dispersion: 2.44 against genuine
seasonality's 1.07.

**What the belief actually sees**, per design, as world-weighted mean
log-likelihood in bits (differences between hypotheses are what matter; the
common constant is dropped):

| design | hawkes | null | mixture | **regime** | seasonality |
|---|---|---|---|---|---|
| count_autocorrelation_w2 | -8.61 | -8.85 | -8.05 | **-11.33** | **-2.33** |
| inter_arrival_dispersion | -2.98 | -11.96 | -3.71 | **-2.98** | -4.24 |
| phase_conditioned_dispersion | -3.03 | -11.96 | -6.43 | **-3.42** | -11.96 |
| size_dispersion | -2.73 | -2.72 | -2.72 | **-2.74** | -2.73 |
| force → mean_rate | -7.99 | -5.33 | -3.19 | **-2.27** | -4.45 |
| **sum** | -25.34 | -40.83 | -24.11 | **-22.74** | -25.70 |

The autocorrelation pays **nine bits an experiment for the wrong answer** and the
truth is last there; the phase conditioning and the intervention pay it back.
Summed over the design set the truth leads by 1.37 bits over the runner-up, so a
belief fed every design converges on it — S12 is a garden path and not a trap.

**And it costs greedy.** One-step-greedy spends most of its budget on the
highest-gain design, which here is the one carrying the spurious signal: it
identifies the truth on **33.4%** of rollouts within twelve experiments, taking
8.03 when it does, against 100% and 5.64 on S2 — the same truth without the
nuisance. V1 recovers it on the single realisation the gate runs, at 0.996 mass.

**Closes off.** The censoring is visible in the control channel too: the world's
modal `size_dispersion` cell is 0 where every hypothesis's is 2, because dropping
two events in five shortens the sample the mark statistics are estimated from.
The posterior predictive check therefore fires on S12 for V1 and B4, which is
correct — the model *is* misspecified there — but it means S12 contributes to
Stage A detection rates as well as to Stage B, and a report that treats detection
as an S11-only measurement will be wrong about it.

## 2026-08-04 — item 11: every hypothesis graph moved to the agent grammar

**Decision.** The suite builds hypothesis graphs on `agent_grammar()` and keeps
executing on `edit_grammar()`. Until now both were the environment's.

**Why it had to change.** `HypothesisGraph.propose` validates a structure against
its own grammar, so a graph carrying the ground-truth grammar would let a system
propose S11's `size → arrival` mechanism — the one structure SPEC §4.5 defines
S11 by *not* having. Out-of-library was true on paper and false in the harness.

**Consequence, and it is not small.** A hypothesis's prior is
`2**-code_length` under the graph's grammar, and the code is grammar-relative:
the agent grammar's Hawkes cell holds one dependency construct where the ground
truth's holds two, so every prior in the suite moved. That is the honest
direction — a system is charged for the structures it can express, not for the
ones the environment can — but it means item 9's posteriors are not comparable
across this change, and they were re-measured rather than adjusted.

**Also.** `ScenarioRun.structural_distance` was computed under the graph's
grammar and raised on S11, since a distance whose endpoint the grammar cannot
express is undefined. It now uses `Executor.grammar`, the environment's, which
licenses the truth by construction. The two agree wherever both can express what
is being compared: the ground metric is over grid indices and the grids are the
same objects in both grammars.

## 2026-08-04 — item 11: the baselines on S1-S12, re-measured

**Measured**, and it supersedes the item 9 table above: 48 runs (4 systems × 12
scenarios) on the five-design 2000-replicate table, hypothesis graphs on the
agent grammar. Correct = leading hypothesis holds the true structure; identified
= that and more than half the mass.

| system | correct | identified | mean structural distance | PPC fires on |
|---|---|---|---|---|
| V1 (BOED, closed set) | 9/12 | 9/12 | 0.167 | S12 |
| B1 (PPC only) | 1/12 | 1/12 | 1.000 | S8, S10 |
| B4 (retrieval) | 6/12 | 6/12 | 0.167 | S8, S12 |
| B5 (beam search) | 1/12 | 1/12 | 0.775 | S7, S10 |

**V1 and B4 have come apart, and the intervention is why.** Item 9 recorded them
as indistinguishable at 6/10 — "with no discriminating design available, optimal
selection buys nothing over nearest-neighbour retrieval, because there is nothing
to select". There is now something to select: V1 gets S2, S7 and S12, all three
of which turn on the forced arrival, and B4 gets none of them, because retrieval
keyed on a residual signature has no way to *choose* an experiment. That is a
first real reading on R1 in the direction the architecture predicts, and it is
about selection, not generation.

**V1 recovers S12 and B4 does not.** V1 ends at 0.996 on regime switching; B4
leads with 0.646 on the wrong structure and puts zero mass on the truth. The
garden path is left by choosing the experiments that leave it, which is the
capability S12 exists to test.

**Nobody gets S8 or S11**, and the oracle says nobody could: neither truth is in
the closed set, so this is a floor for item 12 rather than a failure of these
four. Every system still puts full mass on the null in S9 and none identifies
S10 — SPEC §12 criterion 9 holds, now for the budget reason rather than for want
of a discriminating design.

**B5 is unchanged at 1/12** and its mean distance improved from 0.811 to 0.775.
Its candidates are grid corners and the truths are interior points, so an
exact-match score of zero is by construction; the distance is the number that
says whether searching helped.

## 2026-08-04 — item 11: what is left open

**Left incomplete, deliberately. Four things.**

*The dynamic programme stops at three experiments.* Six of the twelve scenarios
resolve inside it and six do not, so half the table is a lower bound rather than
a value. Horizon four is 1.5×10⁷ belief nodes; the memo keyed on the rounded
belief helps — Bayes updates commute, so the distinct beliefs at depth three are
34,000 of the 2×10⁵ visited — but not by the two orders of magnitude needed.
Anything that wants exact lengths for S2, S7 and S12 needs a better search, not
a bigger machine: branch-and-bound against the evidence floor is the obvious
route and is not implemented.

*The greedy bound is a rollout, not a computation.* 2000 seeded rollouts to a
limit of twelve experiments. It is reproducible and it brackets the optimum from
above where it finishes every time, but its Monte Carlo error is not reported,
and on S12 where it finishes a third of the time the conditional mean is the
only thing available.

*S11 has no Stage B measurement.* The oracle says no policy over the closed set
identifies it, which is the scenario's premise, not its content. What the
scenario is actually for — proposing an appropriate missing mechanism — needs a
system that can propose, and that is item 12. The floor it will be measured
against is B1's detection rate, which remains near zero for the reasons item 9
recorded.

*The intervention is one design, not a family.* `ForceArrival` carries a burst
count, a spacing and an observation window, and exactly one point in that space
is in the calibrated set. Item 7 measured the window (10 events gives a higher
AUC and a worse operating point; 20 was chosen); nothing has measured the burst
count or the spacing, and a BOED that could choose between two windows would be
choosing something SPEC §4.4 says an experiment chooses.

**Measured, on cost.** The full suite is **6 minutes 23 seconds** warm, against
3 minutes 3 seconds at item 10. Item 11 adds 69 tests; almost none of the
increase is theirs. It is the five-design table: every cached row was
invalidated, the closed set rebuilds in 4m36s cold, and three further worlds
cost about 90 seconds each. Warm, `tests/test_oracle.py` is **97 seconds** — 12
dynamic programmes at about 4 seconds and 13 rollout sets at about 3 — and the
item 9 gate is 8m22s cold, unchanged warm.

**Also.** `Executor.grammar` was added, read-only, so that a distance to a truth
outside the agent's grammar is computable. It is not reachable from a research
system: an `Investigation` exposes `budget`, `designs`, `history`, `posterior`
and `ppc`, and never the executor.

## 2026-08-04 — item 12 prerequisite: the check combines evidence by harmonic mean

**Decision.** `inference/ppc.py` combines per-experiment tail probabilities by
the **harmonic mean p-value scaled by `1 + ln(n)`**, replacing the minimum
p-value under a Sidak correction. This closes the `docs/BACKLOG.md` entry
"Combine posterior-predictive evidence across experiments, instead of min-p",
which item 9 opened and sequenced before item 12.

**Why the old rule had to go.** Under min-p, evidence and penalty grow together
and the penalty wins. B1's per-experiment probability against an inadequate
hypothesis space is 0.0133 whatever the budget; the Sidak correction takes the
combined value from 0.0133 at one experiment to 0.1928 at sixteen, so **a system
that ran more experiments detected less**. The harmonic mean grows
logarithmically instead:

| budget | 1 | 2 | 4 | 8 | 16 |
|---|---|---|---|---|---|
| Sidak on min-p | 0.0133 | 0.0264 | 0.0521 | 0.1016 | 0.1928 |
| harmonic mean | 0.0133 | 0.0225 | 0.0317 | 0.0410 | 0.0502 |

Some growth is correct — `n` tests genuinely offer `n` chances — so the target
was never a flat penalty. What was wrong was growth fast enough to cross alpha
by the fourth experiment.

**Tried and abandoned: Fisher's method**, which the backlog entry named as the
first candidate. It pays two degrees of freedom per experiment whether or not
that experiment carried evidence, and on this slice the evidence is
*concentrated*: against `SIZE_MIXTURE` the median per-experiment p-value is
0.6678 while the median smallest is 0.0030, and four of the five slice templates
are uninformative about a defect in the mark component. Measured over A9's arms,
Fisher's realised size was **0.130** — above A9's `2 alpha` bound — and its power
against `SIZE_MIXTURE` collapsed from 1.000 to **0.070**. Both arms moved the
wrong way at once, which is what identified the diagnosis: min-p was not merely
penalising evidence, it was also *concentrating* it, and any replacement has to
keep the second property while fixing the first.

**Tried and abandoned: the Cauchy combination (ACAT).** For `n` identical
p-values it returns the p-value unchanged at every `n`, so it applies no
multiplicity penalty whatever — the opposite failure. It also cannot represent
the `p = 1` atom that a discrete tail regularly produces: `tan(-pi/2)` diverges
and one such experiment drives the combined value to 1 on its own.

**Measured**, over A9's arms at alpha 0.05 (100 scenarios per arm):

| combiner | realised size | power, `SIZE_MIXTURE` | power, `SIZE_EXCITATION` |
|---|---|---|---|
| Sidak on min-p | 0.050 | 1.000 | 0.060 |
| Fisher | **0.130** | 0.070 | 0.070 |
| Cauchy (ACAT) | 0.030 | 0.000 | 0.040 |
| harmonic x max(1, ln n) | 0.040 | 1.000 | 0.050 |
| **harmonic x (1 + ln n)** | **0.010** | **1.000** | **0.000** |

**Why `1 + ln(n)` and not `ln(n)`.** The factor has to be exactly 1 at `n = 1`,
where there is nothing to correct. Beyond that the two were separated by
measurement, over the full 200-scenario benchmark rather than A9's 100 so the
binomial standard error is 0.015 rather than 0.022:

| experiments combined | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| Sidak on min-p | 0.045 | 0.025 | 0.070 | 0.070 | 0.045 |
| harmonic x max(1, ln n) | 0.045 | 0.025 | **0.100** | 0.070 | 0.030 |
| harmonic x (1 + ln n) | 0.045 | 0.005 | 0.040 | 0.025 | 0.010 |

`max(1, ln n)` lands exactly on A9's `2 alpha` bound at three experiments, with
no margin. Item 6 rejected the unfloored predictive for precisely this reason —
"inside A9's 2x bound but with no margin, and a 100-scenario gate against that
bound would be flaky" — and the same call is made here.

**Closes off.** The cost is the `SIZE_EXCITATION` row falling from 0.060 to
0.000. Neither figure is detection: alpha is 0.05, so min-p's 0.060 was the
nominal floor and nothing more. S11's mechanism remains invisible to the check,
for the reason item 6 recorded — every slice template is a statistic of the
arrival stream alone, and the mechanism is calibrated to hide there. **That is
the other backlog entry, and this change does not touch it.** The two compound
rather than substitute, exactly as both entries said.

**Also.** `sidak` is gone from the module rather than left unused; the scaled
harmonic mean is `harmonic_mean_combined`, and it takes the whole vector rather
than a minimum and a count, so no caller can combine the wrong number of tests.
`PPCResult.p_value` keeps its meaning as "the multiplicity-corrected
probability", so nothing downstream changed.

## 2026-08-04 — item 12 prerequisite: B1's Stage A rate, re-measured

**Measured**, and it supersedes the 2/10 figure item 9 recorded. B1 on S1-S12
under the harmonic-mean combiner, alpha 0.05:

| id | class | experiments | smallest per-experiment p | combined | detects |
|---|---|---|---|---|---|
| S1 | single | 8 | 0.0075 | 0.0432 | **yes** |
| S2 | single | 8 | 0.0133 | 0.0563 | no |
| S3 | single | 8 | 0.0133 | 0.0797 | no |
| S4 | single | 8 | 0.0133 | 0.0825 | no |
| S5 | confounded | 8 | 0.0075 | 0.0430 | **yes** |
| S6 | confounded | 8 | 0.0133 | 0.0798 | no |
| S7 | confounded | 8 | 0.0097 | 0.0454 | **yes** |
| S8 | compound | 8 | 0.0030 | 0.0361 | **yes** |
| S9 | null | 8 | 0.1034 | 1.0000 | no |
| S10 | non-identifiable | 2 | 0.0133 | 0.0226 | **yes** |
| S11 | out-of-library | 8 | 0.0075 | 0.0431 | **yes** |
| S12 | garden path | 8 | 0.0133 | 0.0490 | **yes** |

**7/12, against 2/12 before.** S9 is the true negative and stays negative at
exactly 1.0000 — B1 holds the null and the null is S9's truth, so the one run in
the slice whose hypothesis space provably contains the answer is not flagged.
The four still missed are the ones where a single experiment of eight carries
the evidence and the other seven are uninformative, which pulls the harmonic
mean up; that is the combiner behaving as designed rather than a residue.

**Nothing about the posterior moved**, which is the check that this touched
Stage A only: V1 9/12 correct, B1 1/12, B4 6/12, B5 1/12, mean structural
distances 0.167 / 1.000 / 0.167 / 0.775 — identical to the item 11 table.

**The consequence for SPEC §12 criterion 4 is the opposite of the old one, and
it is worse.** The criterion is "detects inadequacy on S11 at a rate at least
matching B1". Before, B1's rate was near zero and any inert system cleared it.
Now B1 detects S11 — but *not because it can see S11's mechanism*. B1 holds only
the null, so it fires on anything that is not the null: S1, S5, S7, S8, S10, S11
and S12 alike. Its S11 detection is a statement about holding a trivially
inadequate space, not about out-of-library sensitivity.

V7 holds the closed set, and A9 measures the check's power against
`SIZE_EXCITATION` **with the closed set entertained** at **0.000**. Hawkes covers
S11's mechanism on every arrival-only statistic, so no system holding an adequate
closed set can match a system holding only the null. **Criterion 4 has gone from
trivially clearable to unpassable**, and the root cause is the same in both
regimes: SPEC §4.3's catalogue contains no statistic of the joint behaviour of
marks and arrivals.

**Left for the user, deliberately.** That is the third `docs/BACKLOG.md` entry,
and it touches SPEC §4.3, which is frozen. What this measurement adds is the
demonstrated contradiction SPEC §13 requires: §4.6 requirement 1 asks that S11
Stage A be detectable via the PPC, §4.2 calibrates S11's mechanism to be
indistinguishable from Hawkes under every arrival statistic, and §4.3 offers no
other kind. Both cannot hold. The decision is not taken here because it bumps
`MetricRegistry.version`, moves the content address of every registered
experiment, and re-measures every number in this file.

**Closes off.** Any future reading of criterion 4 must say which hypothesis
space the comparison was made under. A detection rate is a property of
(check, hypothesis space, catalogue), and comparing two systems holding
different spaces on it — which is exactly what §12 criterion 4 asks for —
measures the spaces at least as much as the systems.

**Measured, on cost.** Full suite **518 passed, 6 skipped in 6m16s** warm,
against 6m23s at item 11. The combiner is arithmetic on values the check already
computed, so it costs nothing.

## 2026-08-04 — item 12 prerequisite: a prediction per offered design

**Decision.** `systems.base.table_predictions` derives one prediction per design
the scenario offers, replacing `table_prediction`'s single prediction under the
scenario's *first* design. `null_seeded_graph` takes the design space rather than
one member of it. This closes the `docs/BACKLOG.md` entry "A prediction per
offered design, not one per hypothesis", which item 10 opened and sequenced
before item 12.

**Why.** A hypothesis is a statement about the whole design space, and attaching
its falsifiability to one arbitrary member of that space was the actual error.
It also quietly weakened A16 in practice: a hypothesis was refutable, but
possibly only by an experiment nobody would run.

**Measured**, like-for-like — the old rule restored by monkeypatch and re-run on
the same twelve scenarios, so the claim population is identical at 2288 and the
comparison is not confounded by item 11 having grown the scenario set from ten:

| | accept | refer | reject | adjudicated |
|---|---|---|---|---|
| one prediction, first design | 477 (20.8%) | 220 (9.6%) | 1591 (69.5%) | **90.385%** |
| one per offered design | 743 (32.5%) | 0 | 1545 (67.5%) | **100.000%** |

A23's threshold is 90%. The old figure clears it by four tenths of a percentage
point on twelve scenarios, against the 90.546% item 10 recorded on ten — so the
margin was *narrowing*, not holding.

**The referral set had moved, which is the stronger evidence.** Item 10 measured
all 180 referrals as V1 on S5, S6 and S10. On the five-design set the same rule
puts all 220 on V1 at S5, S9 and S10. Same cause — BOED does not select the first
design — different scenarios, because which design is selected first depends on
the design set, and item 11 added one. A gate whose margin depends on that is a
gate that was going to fail eventually for a reason unrelated to the verifier.

**Not only referrals moved.** Accepts rose by 266 while rejects fell by 46, so
about a fifth of the change is claims that were *rejected* under the old rule and
are accepted under this one. That is expected rather than alarming: the
statistical channel grades a claim carrying no effect by evaluating its subject's
predictions against the cited experiments, and a hypothesis that previously
offered only a prediction about an unrun design now offers one about an
experiment that happened. The claim is being judged on evidence that bears on it
instead of on evidence that does not.

**Closes off.** Every proposal now costs `n` validations and `n` table reads
rather than one. That is cheap, but it is inside the loop B5's beam runs, and the
full suite is unchanged at 6m16s so nothing needs doing about it today. A design
whose template measures several diagnostics at once is **skipped**, since
`Prediction` names one diagnostic by specification; all five slice templates are
one-dimensional, so the skip path is unexercised on this slice and is covered by
a raise if it ever removes every design.

**Re-measure at item 12 against real agent claims**, which is what A23 actually
names. Both figures above stand in for that, as item 10's did.

## 2026-08-04 — item 12: determinism forces record and replay, it does not merely suggest it

**Decision.** Every model call goes through a content-addressed transcript store
(`systems/llm/transcripts.py`). Evaluation runs in `REPLAY`, where a missing
address raises `TranscriptMissError`; `RECORD` is a separate, deliberate act that
produces an artefact to be committed.

**Why it is forced rather than convenient.** SPEC §1's third invariant is
bit-exact determinism. The usual way to approach that for a model call is
`temperature=0`, and it is **unavailable**: `claude-opus-5` rejects
`temperature`, `top_p` and `top_k` outright — a request carrying any of them is
refused with a 400. There is therefore no setting, not even a degenerate one,
that makes two calls with one prompt return one answer.

So a recorded response is not a cache of the reproducible thing. It **is** the
reproducible thing, and the model call is the process that produces it — exactly
as `EmpiricalTable` is the artefact and simulation is the process. The two are
built the same way on purpose: content-addressed, refused when the address
disagrees, never silently refreshed.

**The asymmetry between the modes is the guarantee.** A store that filled a miss
by calling out would make a run's result depend on when it happened and on who
had credentials in their environment. `REPLAY` raising is what stops an
evaluation run quietly becoming a live one.

**What the address covers**: provider id, model id, system prompt, rendered
brief, tool schema, and the index of the call within the investigation. The last
matters because a system may ask twice with an identical brief — after an
experiment that moved nothing — and those are two events that may legitimately
get different answers. It deliberately does *not* cover the scenario id or the
seed: those reach it through the brief, and two scenarios presenting an
identical brief are the same question as far as the model is concerned.

**Closes off.** `ADDRESS_VERSION` is mixed into every address, so a change to
*what* is hashed invalidates the corpus rather than silently matching against a
differently-computed key — the promise `OPERATIONS_VERSION` already makes for the
table cache. And because a brief renders a `Defect`, which is a `frozenset` with
process-dependent iteration order, `render_brief` renders it in canonical order;
`tests/transcript_child.py` is the cross-process arm that fails if that ever
stops, since an in-process loop cannot see it. The failure it prevents is a
corpus that replays on the machine that recorded it and misses everywhere else.

**Also.** No refusal fallback. The ordinary advice for this model family is to
opt into server-side `fallbacks`; it is wrong here, because a transcript's
address covers the model id, so a response served by a substitute would be stored
under an address naming a model that did not produce it. A provenance chain that
quietly lies about which model answered is worse for this framework than a run
that stops, so a refusal raises `ProviderError` and nothing is recorded.

## 2026-08-04 — item 12: the model chooses a cell and an index, never a value

**Decision.** A proposal is a choice of *structural cell* from a menu derived
from the grammar, plus, per parameter, an **index** into that parameter's
quantisation grid. Not a value.

**Why this and not free-form structure.** The grammar already enumerates every
licensed cell through `EditGrammar.structures()`, so a menu is derived rather
than authored, it is exactly as expressive as the grammar and no more, and — the
part that matters — an out-of-library structure is unproposable **because the
menu has no entry for it**, not because a validator caught it afterwards.
Measured on the slice: the agent grammar yields a 5-entry menu and none of its
entries is a dependency sourced from `size`, so SPEC §4.5's S11 mechanism cannot
be expressed. The environment grammar's menu does contain one. S11 is out of
library by SPEC §3.2's mechanical definition, in the harness and not only on
paper, and `tests/test_llm.py` asserts both halves.

**Why an index and not a value.** Three consequences that would each otherwise
need a guard. A decoded proposal is on-grid by construction, so it can never trip
`OffGridParameterError` and the prefix code is always defined on it. The model
has no way to express a magnitude at all, so no prompt wording can coax a
plausibility, a probability or a score out of it. And the JSON schema handed to
the provider contains **no `number` type anywhere** — a mechanically checkable
statement rather than a convention, asserted directly.

SPEC F7 at this boundary is therefore a property of the wire format rather than
of the prompt or of the model's compliance. The grids are still *shown*, because
a proposal made blind to what the indices mean would be a lottery rather than a
hypothesis; reading a value and choosing its index is a different act from
writing one.

**Closes off.** `core/edits.py`'s `_build` became public as `build_edit`, because
decoding a cell into an edit is now done outside that module and instantiating
the dataclasses directly would put a second copy of the option-kind-to-edit-type
mapping into the codebase — where `FamilyOption` resolving to either
`ChangeDistributionFamily` or `ReparameteriseComponent` is exactly the detail
that would drift.

**Also.** `isinstance(True, int)` is true in Python, so a payload of booleans
would decode to grid indices 0 and 1 and look well-formed. `draft_from_payload`
refuses booleans explicitly and there is a test for it.

## 2026-08-04 — item 12: V7 exists, and SPEC §9's primary contrast cannot be run

**Measured**, V7 on all twelve scenarios, closed set entertained, scripted
provider, five-design 2000-replicate table. The `fires` column is the *final*
check; `calls` is how many times the proposal layer was actually asked, which is
gated by the check taken at the half-budget point.

| id | class | final PPC p | fires | correct | proposal calls |
|---|---|---|---|---|---|
| S1 | single | 0.8509 | no | yes | 0 |
| S2 | single | 1.0000 | no | yes | 0 |
| S3 | single | 1.0000 | no | yes | 0 |
| S4 | single | 1.0000 | no | yes | 0 |
| S5 | confounded | 1.0000 | no | yes | 0 |
| S6 | confounded | 1.0000 | no | yes | 0 |
| S7 | confounded | 1.0000 | no | yes | 0 |
| S8 | compound | 1.0000 | no | no | 0 |
| S9 | null | 1.0000 | no | yes | 0 |
| S10 | non-identifiable | 0.2629 | no | no | **2** |
| S11 | **out-of-library** | **0.5273** | **no** | no | **0** |
| S12 | garden path | 0.1011 | no | yes | **2** |

**S11 is the finding.** Its combined p-value is 0.5273 against an alpha of 0.05
— not marginal, an order of magnitude away. V7 entertains the closed set, Hawkes
covers S11's mechanism on every arrival-only statistic, and A9 already measured
the check's power against `SIZE_EXCITATION` **with the closed set entertained**
at 0.000. So Stage A never fires on the one scenario the whole architecture
exists for, and **Stage B never runs**.

SPEC §9's preregistered primary contrast is "On S11 Stage B, conditional on
inadequacy detection, does V7 exceed B4 on D3?" It conditions on an event that
occurs zero times in twelve. The contrast is not weak; it is **undefined**.

**This is not fixable by restructuring the loop.** Checking after every
experiment rather than at the half-budget point would give more chances at a test
with no power; A9's 0.000 is a property of the diagnostic catalogue and the
calibration, not of when the check is taken. SPEC §4.2 calibrates S11's mechanism
to the same operating point as the four it hides among, and every statistic in
§4.3 is of the arrival stream alone.

**Where the proposals did happen is instructive.** S10 and S12 called the layer
twice each. On S10 the budget is two, so the pre-proposal phase is one
experiment and the combined p-value is just that experiment's — no multiplicity,
so the check is at its most sensitive. On S12 the censoring genuinely misfits
the closed set. Neither is S11.

**V7 is otherwise V1**, correct on 9/12 with an identical profile, which is the
architecture behaving as designed: on a scenario whose truth is in the library
the check passes, no proposal is made, and the two systems are the same system.
That is what makes the contrast clean when it *can* be run.

**Left for the user, and it is the same decision as before.** Either §4.3 gains a
mark-arrival cross-diagnostic — the `docs/BACKLOG.md` entry — or §9's contrast is
re-specified. Both touch frozen documents, and the two measurements now bracket
the problem from opposite sides: B1 detects S11 for a reason that is not about
S11, and V7 does not detect it at all.

**A third option exists and is worse.** V7 could propose unconditionally rather
than on detection. That would produce Stage B data on S11, at the cost of
reporting extension quality on runs where inadequacy was never detected — which
is precisely what SPEC F6 exists to forbid ("never reported combined"). Recorded
so that it is rejected deliberately rather than discovered later as a shortcut.

## 2026-08-04 — item 12: D1-D6, and the measurement that shows why §8 forbids collapsing them

**Decision.** `eval/scoring.py` gains `dimension_vector`, SPEC §8's six
dimensions, alongside the closed-world proper score item 9 built. D3 is one minus
the mean **Jensen-Shannon divergence**, in bits, between the candidate's and the
truth's outcome distributions over a held-out battery.

**Why Jensen-Shannon and not Kullback-Leibler.** Symmetric, so "how far is the
candidate from the truth" does not depend on which is named first — and neither
ordering is privileged when two proposed explanations are compared. And bounded
in `[0, 1]`, which is what lets divergences over several designs be averaged into
a number that means something; an unbounded divergence would let one design where
the candidate assigns near-zero to a frequent outcome dominate the battery.

**Measured on S11, and it is R7 in one table.** Every closed-set structure scored
against S11's out-of-library truth, held-out battery of the mark-size diagnostic
and the forced-arrival intervention:

| candidate | D1 distance | D2 held-out | **D3 similarity** | D6 bits |
|---|---|---|---|---|
| hawkes | 1.50 | -2.082 | **0.960** | 24.0 |
| null | **1.00** | -5.798 | 0.617 | 1.0 |
| poisson_mixture | 1.50 | -5.645 | 0.677 | 24.6 |
| regime_switching | 1.50 | -5.775 | 0.698 | 29.0 |
| seasonality | 1.50 | -5.734 | 0.629 | 23.0 |
| *the truth itself* | 0.00 | -1.958 | 1.000 | 24.0 |

**Read the Hawkes row against the null row.** On D1, Hawkes is 1.50 from the
truth and the *null* is 1.00 — so a system that proposed Hawkes on S11 scores
worse structurally than one that proposed nothing at all, and exactly the same as
one that proposed seasonality. On D3 it is 0.960 against a best rival of 0.698,
and the truth itself scores 1.000.

That is SPEC §0's "on structural recovery" correction and research question R7,
demonstrated rather than argued: S11's mechanism is a Hawkes process whose marks
gate the excitation, so a plain Hawkes reproduces its response to a forced
arrival almost exactly while being, by the edit metric, no closer than anything
else. A scalarisation of the six would have to decide how many edits of D1 a
tenth of D3 is worth, which is the open question — hence `DimensionVector` has
six fields, no `total`, and a test asserting there is no `total` to read.

**Two smaller readings.** D2 is the candidate's log2 predictive at the *truth's
modal cell*, so it asks "would this have predicted what usually happens" — a
different question from D3's "does it respond to intervention the same way", and
the Hawkes row shows they can be answered differently. D4 sums only *positive*
likelihood improvements, because §8 asks about "previously poorly-explained"
results: an experiment the entertained set already explains is not one the
candidate was meant to rescue, so failing to beat it costs nothing.

**Closes off.** An empty battery gives D2 and D3 as `nan`, not `0` — a question
that was not asked must not read as a measurement of zero. D5 does give `0.0` on
an empty design set, and that asymmetry is deliberate: there, nothing was
enabled, which is an answer. `dimension_vector` returns the grown table so a
caller can score many candidates against one battery without re-simulating; each
new structure costs a 2000-replicate row otherwise.

**`grammar` must be the environment's, not the agent's.** On an out-of-library
scenario the truth is by construction outside the agent's grammar, so a distance
or a code length computed under the agent's would raise on exactly the scenario
the vector exists for — the same reason `ScenarioRun.structural_distance` uses
`Executor.grammar`, recorded at item 11.

## 2026-08-04 — item 12: A17 went non-vacuous over a second package, and what it caught

**Decision.** `AGENT_TOOL_SURFACE` now names `sciagent.systems.llm` and
`sciagent.systems.llm.*`. Item 4's entry said in as many words that item 12 must
extend it or "A17 keeps passing while checking nothing real", and this is that.

**What it caught, immediately.** One path:
`sciagent.systems.llm.scripted.numeric_provider` — a helper I had shipped inside
the package to build a payload carrying a `plausibility`, as A17's negative
control. The analyser matches string constants, read `"plausibility"`, and
reported the module as an agent-reachable path mentioning a sealed symbol.

**Resolved by deleting it from the package**, not by licensing it. A helper whose
only purpose is to construct the thing an invariant forbids has no business being
importable by the systems that invariant constrains; the control now lives in
`tests/test_llm.py` as `smuggling_payload`, beside the two assertions it exists
for. The analyser was right and the code was wrong, which is the outcome a static
gate is for.

**Item 6's predicted bite did not happen, and the reason is worth recording.**
That entry warned: "when item 12 adds an agent that can ask for a posterior, any
path from the agent tool surface to `EmpiricalTableEngine.posterior` reaches a
function that reads `HypothesisNode.plausibility`, and A17 will flag it."
It does not, because **`inference/empirical.py` contains no reference to
`plausibility` at all**. The engine never uses the normalised prior; it uses
`log_prior`, which is `-code_length * ln 2` off the grammar, and normalises at
the end — which is the same item 6 entry's "the prior is deliberately
unnormalised", read forward. The two halves of that entry were inconsistent with
each other and the second one is right.

So no new licence was added to `PLAUSIBILITY_DERIVATION`, which still names
exactly the three functions item 9 named, and
`test_a17_every_licensed_function_still_needs_its_licence` still holds in both
directions.

**Closes off.** A17's analysis now covers two packages. The next item that adds a
module under `sciagent/systems/` inherits the surface automatically through the
`*` patterns; one that adds an agent-reachable package *outside* it must extend
the declaration again, and nothing will tell it to except this note and item 4's.

## 2026-08-04 — item 12: what is left open

**Left incomplete, deliberately. Six things.**

*There is no recorded transcript corpus.* No Anthropic credential is resolvable
in this environment — `ANTHROPIC_API_KEY` unset, no `ant` CLI — so no live call
was made and nothing was recorded. Every V7 figure above is against a **scripted
provider**, and none of it is a measurement of what a model proposes. The
machinery is built and gated so that recording is the only remaining step: a
corpus recorded under `RECORD` replays under `REPLAY` with `misses == 0`, which
`tests/test_hybrid.py` asserts end to end on S12.

*SPEC §12's capability criteria 4, 5 and 6 are not measured.* Criterion 4 is
undefined for the reason recorded above — Stage A never fires on S11, so Stage B
never runs. Criterion 5 asks V7 to exceed "B6-equivalent random structured
generation" on D3 with a non-overlapping 95% interval, and no B6 exists: SPEC §5
defers it to the full benchmark. It is now cheap to build — a provider drawing
uniformly from the structural menu is a few lines against `ScriptedProvider` —
and it should be, since D3 now exists to compare on. Criterion 6, a
discriminating three-stage plan on two of S5–S7, needs the plan *read off* a run
rather than the diagnosis, which nothing yet does.

*Criterion 8 is not measured either.* "Zero graph contradictions and zero zombie
hypotheses across all runs" — A18 makes a duplicate an error and `Hybrid._admit`
skips one rather than re-proposing, so the mechanism is there, but no run
aggregates `verify/contradiction.py` over the population. That is a reporting
pass, not new machinery.

*D2's "held out" is honoured by the caller, not checked.* `dimension_vector`
cannot see what an investigation ran — it takes a battery and a truth — so a
caller passing a design the system already used would be measuring fit and
calling it prediction. Documented at the call site; not enforceable there.

*V7's two constants are asserted, not measured.* `max_proposals = 2` and the
half-budget split before the check are taken from B4 and B5 so the three are
comparable, which is a reason to pick them and not evidence that they are right.
Nothing has measured whether a system that proposes once, or checks after every
experiment, does better.

*A23 still stands at 100% over generated claims, not agent claims.* Item 10 said
it must be re-measured "against real agent claims, which is what A23 actually
names", and this item was to have done it. It cannot, for the first reason above.
The 100.000% figure is over `claims_from_run`'s cross-product, as item 10's
90.546% was.

**Measured, on cost.** The full suite is **618 passed, 6 skipped in 6m50s**. Item 12 adds 93 tests:
`tests/test_llm.py` (50), `tests/test_hybrid.py` (23) and `tests/test_scoring.py`
(20). Almost all of the added wall-clock is table rows, not tests — a structure
V7 proposes or a candidate D2/D3 scores has no row in the calibrated table and
filling one is 2000 replicates. Both modules thread a growing table and persist
it, as the item 9 gate does, so the cost is once per machine rather than once per
session.

## 2026-08-04 — item 12: the decoder was more permissive than its own schema

**Found by its own negative control.** `draft_from_payload` read the keys it knew
and ignored the rest, so a payload carrying `plausibility` decoded cleanly — the
value was dropped, since `ProposalDraft` has no field for it, but nothing was
*refused*. Meanwhile `tool_schema` declares `additionalProperties: false` at
every level. The two ends of the boundary published different contracts, and the
looser one was the one that ran.

**Resolved** by refusing an unknown key, at both levels, against
`_PAYLOAD_KEYS` and `_EDIT_KEYS` declared beside the schema that generates them.

**Why it is worth failing on rather than tolerating.** The value could never
reach a score either way — that part was already true and is still asserted. But
"the number was refused" and "the number was silently dropped" are different
things to be able to say afterwards, and only the first is evidence about what
the model tried. A17's whole shape is that a violation should be *visible*, and a
decoder that quietly discards the one field the invariant is named for gives up
the only place that attempt was observable.

**The test that caught it was written for something else.** It asserted the
negative control was refused; it failed because the control was tolerated. The
positive control -- a conforming payload still decodes, to exactly the three
fields `ProposalDraft` declares -- was added at the same time, since a decoder
that refused everything would satisfy every negative test in that class.

## 2026-08-04 — infrastructure: the personal deny rules do not survive the move into the repository

**Decision.** The repository now carries the working defaults that used to live
only in `~/.claude/` (8dd0690), but deliberately *not* the permission rules.
Those remain uncommitted, and the version sitting in the working tree is
known-wrong rather than merely unfinished.

**Tried and abandoned.** Copying the twenty `deny` entries from
`~/.claude/settings.json` verbatim into `.claude/settings.json`. Checked against
`code.claude.com/docs/en/permissions.md` rather than assumed, three of the four
classes do not do what they read as doing:

- `Bash(curl*|*sh)` and `Bash(iwr*|*iex)` match nothing at all. `|` is a
  recognised command separator and "a rule must match each subcommand
  independently", so no subcommand ever contains the literal `|` the pattern
  requires. `iwr`/`iex` are additionally PowerShell, which has its own
  `PowerShell(...)` rule namespace that a `Bash(...)` rule is never consulted
  for — and PowerShell is this machine's primary shell.
- `Read(**/.ssh/**)`, `Read(**/.aws/**)` and the two `~/.claude/*` entries are
  anchored at the project root when they live in project settings, and "a rule
  only matches files under its anchor". They cannot reach the home-directory
  files they name. A `~/` prefix is what reaches outside.
- A `Read` deny covers `Edit` but not `Write`, so six paths were read-denied and
  still writable.

**Why it is not simply corrected.** The auto-mode classifier refuses edits to
`.claude/settings.json`, which is the right refusal — that file governs the
editing agent's own permissions. **This waits on the local machine**: on return
it gets solved directly, by approving the edit or applying it by hand. Until
then the tree holds a deny list that reads protective and partly is not, which
is worse than holding none, so it must not be committed as it stands.

**Closes off.** Nothing in 8dd0690 depends on it; that commit stands alone. But
an unattended cloud session runs with no deny list whatsoever, which is the
reason the attempt was made and the reason it is worth finishing.

## 2026-08-04 — infrastructure: what a cloud session cannot yet be trusted with

**Decision.** No cloud-produced registry entry is to be trusted until
`determinism_child.py` has been run on both platforms and its output diffed.

**Why.** Invariant 3 demands byte-identical output, and the registry is
content-addressed over (env version, config, data version, metric version, seed)
with no platform term in the tuple. Local runs are Windows; cloud runs are
Ubuntu 24.04 on x86-64. If BLAS resolution differs between them, two entries can
share a content address while holding different numbers, and nothing in the
system is positioned to report it.

**The obvious instrument is the wrong one.** A1 and A15 each rebuild their
expected value inside a single process and compare against it, so they pass on
any platform — which is exactly the failure they would be reached for. What
emits comparable evidence is `tests/acceptance/determinism_child.py`, which
writes `name sha256` lines to stdout under
`test_a1_byte_identical_across_processes`.

**Measured, on cold start.** From a fresh clone with an empty `uv` cache:
`uv sync` 19.4s, then `uv run python scripts/status.py` a further 10.9s — about
30s before the SessionStart hook prints, on a developer desktop with warm
network. A cloud VM is 4 vCPU behind a proxied PyPI, so the hook's 60s timeout
was not safe. 180s is written but uncommitted, for the reason in the preceding
entry.

**Closes off.** Work in the cloud on anything that writes no registry entry is
unaffected. Anything that does write one waits on this check.

## 2026-08-05 — item 11: the oracle named an opening design it had not chosen

**The measured numbers, because horizon 4 is expensive.** At `DEFAULT_HORIZON = 3`
the dynamic programme resolves no path on S2, S7, S8, S11 or S12 — five of the
twelve — so every design scores the `horizon + 1` floor and the search has no
preference. Run at horizon 4, S2 and S7 separate properly:

```
4.563441675303293   query:phase_conditioned_dispersion   <- optimal opening
4.612708193191079   query:count_autocorrelation_w2
4.638117321572723   force[arrival@...|20]:mean_rate      <- the intervention, 3rd
4.656071553542731   query:inter_arrival_dispersion
4.999999999999999   query:size_dispersion
```

**An assertion was true for the wrong reason, and abandoned.**
`test_the_regime_scenarios_open_with_the_intervention` claimed the optimal policy
opens S2/S7 with the forced arrival. It does not, at the first horizon where the
question is answerable. The premise it rested on is sound — on S2's world the
intervention separates Hawkes from regime switching by 4.4033 bits against at
most 0.4909 for any query — but the conclusion does not follow: Hawkes is not the
binding rival at the prior, and the truth must outrun all four to cross the
threshold. The phase-conditioned query separates *this pair* worse (0.4909) and
the *best rival* better (0.4909 against the intervention's 0.3466), so it opens.
Replaced by an assertion of the pairwise margin, which is what SPEC §4.2 actually
claims for the fifth design.

**Spec ambiguity: what "unreachable" means for `first_design`.** Its docstring
promised `None` for an unreachable truth without saying whether that meant
out-of-library (S8, S11: `truth_mass_prior == 0`) or unresolved within the
horizon. Settled as the latter, matching `identifiable`'s own definition, so all
five saturated scenarios now report `None`. The narrower reading would have left
S2, S7 and S12 naming a design the search never preferred.

**Ties are broken on a rounded key, not a tolerance.** A near-equality test is
not transitive, so the winner among three near-tied designs could depend on the
order they were offered in — the determinism invariant broken a second time, one
dict ordering away. Rounding to `VALUE_PLACES = 9` keeps the comparison a total
order. Nine places sits eight orders of magnitude below the smallest real margin
(0.049 above) and eight above float rounding noise.

**Left incomplete: the cross-platform claim is inferred, not verified.** The
failure appeared only on Ubuntu; `CLAUDE.md` records the suite green on Windows
at this commit, and downstream of the table the arithmetic is plain Python floats
in fixed order, hence IEEE-deterministic. So the *rows* must differ between the
platforms, and `environments/pointproc/diagnostics.py` computes metrics with
`np.dot` (lines 126, 129, 379, 382) against an OpenBLAS built `DYNAMIC_ARCH` —
one replicate of 2000 crossing one bin edge is enough. This has not been
demonstrated: it wants `tests/acceptance/determinism_child.py` run on both
platforms and diffed. The fixes above make the *choice of design* robust to such
a difference; they do not make the table identical, and the registry still
content-addresses with no platform term.

---

## 2026-08-09 — review: the registry's read path could write

**Decision.** `ExperimentStore`'s sqlite authorizer no longer admits `INSERT`
outright. It is granted only while `append` is running, via `_appending`, and the
connection is opened with `cached_statements=0`.

**Why.** An authorizer is per *connection*, not per caller, and `append` needs
`INSERT`, so the allowlist admitted it for every statement on that connection —
including `query`, which is documented "read-only". A hand-written `INSERT` was
therefore accepted, registering a row that skipped every check `append` makes:
the non-finite guard, the conflict check, and content addressing itself. The row
landed in the agent-reachable pool with a `digest` column unrelated to its own
content. Reproduced before the fix, refused after.

A12 was never violated — `UPDATE`, `DELETE`, `DROP`, and `INSERT OR REPLACE` are
all genuinely refused, as the entry above records. What happened is narrower and
worth naming: the fuzz corpus listed `INSERT OR REPLACE` and never a bare
`INSERT`. `INSERT OR REPLACE` is caught by the *delete trigger*, not by the
authorizer, so the corpus was testing the schema layer twice and the connection
layer not at all for the one action the allowlist opened.

**Measured, and the reason the first fix was wrong.** Scoping the grant to
`append` is not sufficient on its own. Python's sqlite3 caches prepared
statements by SQL text and **a cache hit skips the authorizer**, which runs at
prepare time — so after one successful append, re-issuing the exact text of
`append`'s own `INSERT` through `query` was authorised by the *earlier* prepare
and went through. Demonstrated directly before `cached_statements=0` was added.
That flag is a correctness requirement here, not a tuning knob, and removing it
silently reopens the hole for one specific statement.

**Closes off.** The A12 corpus now carries a bare `INSERT` and a
`WITH … INSERT` — the latter because it is what a prefix check on the statement
text would wave through, and it documents why the fix is an authorizer and not a
string check.

## 2026-08-09 — review: `ExperimentDesign.id` is not injective, deliberately

**Decision.** `id` continues to omit `n_events`, so two designs differing only in
run length render alike. The docstrings now say so, and
`Executor.simulator` refuses a repeated id as `EmpiricalTable.build` and
`boed.rank` already did.

**Why.** The tidier rule — put `n_events` in the id — is correct and costs more
than the defect. Every id would change, hence every `EmpiricalTable.version` and
every registry content address, retiring every stored table and every registered
row to close a collision no caller reaches by accident. What the id actually has
to be is unambiguous *within one design set*, and that is now enforced at all
three places such a set is assembled. `config()` covers all three fields and
stays injective, so the registry address was never at risk.

**Closes off.** The class docstring claimed `id` and `config` were both "pure
functions of the three fields". Only `config` is. Anything that comes to depend
on `id` distinguishing run lengths must change the id and accept the migration.

## 2026-08-09 — review: V7's proposal record was write-only

**Decision.** `Hybrid` keeps its `ProposalAttempt`s and publishes them as
`attempts`.

**Why.** The type existed, carried a docstring about SPEC §12 criterion 11's
autonomy fraction, and was discarded at the end of `investigate` — no slot, no
accessor, no reader anywhere in the repository including the tests. So a run
could not report how many times the model was asked, or distinguish a refusal
from a malformed draft. A `Diagnosis` cannot carry this: SPEC §3.4 has no field
for it, and the two failure outcomes admit no hypothesis, so there is nothing a
distribution could say about them.

**Closes off.** `residual_candidates` still reports duplicate proposals only, and
the reading is now written down rather than implied: an admitted proposal is in
the distribution on its own account, a refused one names no hypothesis, and a
duplicate is the model asking for a second look at a hypothesis the evidence has
not settled. Item 14's agency metrics read `attempts`, not the diagnosis.

## 2026-08-09 — review: what the determinism invariant was not checking

**Decision.** `test_no_unseeded_randomness` now covers `scripts/` and `tests/` as
well as `src/`, rejects `default_rng()` called with no seed, and detects the
stdlib `random` module by import rather than by substring.

**Why.** Three gaps, none of them live — every call site in the repository was
already correct, which is why this is a guard change and not a bug fix.
`scripts/` was unscanned and is where `calibrate_mechanisms.py` and
`calibrate_censoring.py` produce the frozen literals in `mechanisms.py`, so
unseeded randomness there would make a calibration nobody could reproduce without
anything noticing. `default_rng` sat in the allowlist unconditionally, but
`default_rng()` with no argument draws from the operating system — it is the one
member of that list that can be either, and only the call site can tell.
The substring check for `import random` matched the module name in a comment and
missed `from random import randint`; it also would have failed on the assertion's
own source once `tests/` came into scope.

**Closes off.** The suite grew by ~55 parametrised cases. Nothing was found, so
this buys future coverage rather than fixing present breakage.

**Addendum, same day.** The first version of the `default_rng` check matched only
`ast.Attribute` call targets, so `from numpy.random import default_rng` followed
by a bare `default_rng()` passed it — found by an independent check, not by the
author. The guard was a test of import style rather than of behaviour. It now
resolves a call's final identifier however it was reached, and a companion check
refuses `from numpy.random import <dist>`, `from numpy import random` and
`import numpy.random`, which the attribute walk never visits either. Nothing in
the tree used any of those forms; the point is that the invariant is now about
what a module can reach rather than how it spells it.

## 2026-08-11 — item 13: "at equal information" is a representation swap, not a nesting

**Decision.** SPEC §11 item 13's two arms *swap* memory representations rather
than nesting them. V3 (`Memory.RAW`) is shown the per-step readings and the
entertained structures unannotated; V4 (`Memory.GRAPH`) is shown those same
structures annotated with their posterior mass, and no readings. Neither brief's
content is a superset of the other's. The sections that are not memory — the
structural menu, the designs, SPEC F5's conventional Stage A verdict and the
budget — are in both arms unchanged.

**Why.** R2 (§2) says the comparison is *at equal information*, and the obvious
alternative — V4 as V3 plus the graph — fails that on its face: V4 would strictly
dominate, and the measured delta would answer "does a graph help on top of a
history" rather than "does a graph beat a history". §5 line 282 names the pair
"raw history **versus** hypothesis graph", which is the swap and not the sum.
The cost of the swap is that V4 cannot see a reading it might have reasoned from
directly; that is the ablation, not a defect in it. The user was asked and chose
the swap over the additive reading before any code was written.

The arms are `Hybrid` twice under two names rather than two classes, so "they
differ only in memory" is a fact about construction rather than a claim to audit:
`memory_ablation` shares library, grammar, store, budget and prompt by passing
the same objects to both. Its `provider` argument is a **factory** for the same
reason — a `ScriptedProvider` consumes its script, so one shared instance would
have had V4 answering the second scripted proposal while V3 answered the first,
and the ablation would have been measuring what the backend said back.

**Closes off.** `Memory.BOTH` is the default and renders exactly the six sections
item 12 recorded its corpus against, so V7's transcripts and its twelve-scenario
table stay valid; the raw arm's extra section is unreachable from `BOTH`. The
three memories address disjointly, so no arm can replay another's answer.

## 2026-08-11 — item 13: R2 is measurable on S12 alone, and not yet with a scripted provider

**Measured**, both arms on SPEC §9's ablation cell, closed set entertained,
scripted provider, shared calibrated table. `asked` is how many times the
proposal layer was reached — i.e. how many times the arm's brief was rendered.

| id | arm | asked | experiments | final PPC p | correct |
|---|---|---|---|---|---|
| S8 | V3 | **0** | 8 | 1.0000 | no |
| S8 | V4 | **0** | 8 | 1.0000 | no |
| S11 | V3 | **0** | 8 | 0.5299 | no |
| S11 | V4 | **0** | 8 | 0.5299 | no |
| S12 | V3 | **2** | 8 | 0.1009 | yes |
| S12 | V4 | **2** | 8 | 0.1009 | yes |

**Two of §9's three scenarios never reach the proposal layer at all.** SPEC F6
makes extension conditional on Stage A detection, the gate stays shut on S8 and
S11, and where it stays shut neither arm's brief is rendered and V3 and V4 are
the same system running the same trajectory. R2's delta is therefore *undefined*
on S8 and S11 — not small, undefined. This is the same finding item 12 recorded
for V7 from the other side, and for the same reason: A9 measured the check's
power against `SIZE_EXCITATION` at 0.000 with the closed set entertained, so S11
is invisible to Stage A by a property of the diagnostic catalogue.

**On S12 the delta is currently zero by construction, which is not an R2 result.**
Both arms were asked twice, both admitted both proposals, and the two runs are
identical — because `ScriptedProvider` returns fixed payloads *regardless of the
brief*. The briefs did differ: the arms' transcript addresses are disjoint. What
this establishes is that the apparatus discriminates and is deterministic, not
that structured memory is worth nothing.

**Left deliberately incomplete.** Answering R2 needs a provider that reads the
brief, over a recorded corpus, on S12 — the only cell where the question is even
askable. §9 asks for twenty seeds across three scenarios; one of the three
survives. Whether that is enough to preregister a contrast on is the user's call
and touches §9, so it is not decided here.

**One trap worth naming, because it cost a red test.**
`ScenarioRun.ppc` is the check taken *after* the whole budget is spent; the gate
that opens a proposal is the check taken at the half-budget point. They disagree
on S12 — final p 0.1009 with the final check not firing, and two proposal calls
all the same. Anything asking "did the arms have a chance to diverge" must read
the attempt count, never the run's final PPC.

## 2026-08-11 — item 13: V7's brief is byte-identical to 9a2992a, checked across worktrees

**Measured.** The default rendering of `render_brief` — no `memory` argument —
is byte-identical before and after the ablation. Eight investigation states
(S1, S8, S9, S11, S12 at two designs run with two structures entertained, plus
S12 with nothing run, S12 with four entertained and four designs, and S8 with
one design) were rendered under a detached worktree at `HEAD` (9a2992a) and
under the change, by the same probe script under two `PYTHONPATH`s. The JSON
dumps diff clean. Digest over the whole set:

```
sha256 386150e902cd80afbb15bf96259c825c9b1787940f7ebce9c5ababdd75f63fb8
S1 464e431b…  S8 b103eaad…  S9 a1e54990…  S11 48b311c0…  S12 d2c3944f…
```

**Why it is written down rather than left to a test.** `TestTheDefaultPathIsUntouched`
cannot establish this and no longer claims to. Every assertion available to a
unit test here is within one process and one version, so a rewrite of
`Memory.BOTH`'s own rendering would satisfy all of them; comparing the default
against `Memory.BOTH` is a tautology with respect to the previous commit. The
cross-worktree diff is the only thing that settles it, it costs a worktree and a
probe script, and it is exactly the kind of expensive check a later session
should not have to redo to know the corpus still resolves.

**Closes off.** Item 12's recorded transcripts and its twelve-scenario table
remain valid across this change. The check is against 9a2992a specifically; a
future change to the six default sections invalidates it and needs the same
measurement again, not a reassurance. The class docstring says so and points
here.

**Found by an independent check, not by the author.** So was the reason the
first version of `test_raw_carries_no_posterior` passed: it searched the brief
for `"posterior "` and succeeded only because `## Posterior predictive check`
capitalises the P. That heading is SPEC F5's conventional Stage A verdict and is
in *every* arm by design, so the test would have gone green on a genuine leak of
posterior mass into V3 and red on a harmless lower-casing of a heading. It now
searches for `" -- posterior "`, the exact annotation `_hypotheses_section`
writes, and a companion test asserts the check section is present in all three
arms so that "no posterior" can never be read as "no check".

## 2026-08-13 — item 14: the approval-tier boundary is lateness, not proposal count

**Ambiguity.** SPEC F10 requires "two approval tiers" and §12 criterion 11 an
autonomy fraction per investigation. Neither says where the boundary falls, and
§11's row for item 14 is three words. The metric is meaningless until someone
picks, so this is the pick.

**Decision.** Tier 1 is running one of the designs the scenario offers. Tier 2
is introducing a hypothesis whose graph node carries a non-null `proposed_at` --
one introduced after evidence was in hand. `autonomy_fraction` is tier 1 over
the two. Structures introduced before any experiment are reported separately as
`entertained` and kept out of the fraction.

**Why not the obvious reading.** "A proposal is a tier-2 act" was the first
choice and is wrong. `entertain` routes every library structure through
`Investigation.propose`, so V1's `proposed` holds its whole library and only
B1's is empty. Counting proposals reports V1 -- the system *defined* as never
extending its hypothesis space -- as less autonomous than B1 for doing the one
thing V1 does, and makes the fraction depend on how large a library the harness
handed out. Excluding the opening set also removes the perverse incentive: a
system could otherwise raise its own autonomy by entertaining more of its
library.

**Why not the rationale string.** `entertain` writes `library structure '...'`
as its rationale, so a prefix check on that string separates the opening set
from an extension in one line. It was rejected: rationale is prose the *system*
authors, so a system could raise its own autonomy fraction by writing a
different one. `proposed_at` is set by `Investigation.propose` from the
investigation's own history and is unreachable from a system. SPEC's second
invariant is the framework writing the numbers, and a number derived from
agent-authored prose is the agent writing it through one level of indirection.

**Why lateness is the right thing and not merely a safe one.** F9 already
singles out the late hypothesis: it takes no evidential penalty in likelihood
but cannot support a confirmatory claim without a prospectively registered
discriminating experiment. That is the framework's existing statement that such
a hypothesis needs something further before it counts, which is what an approval
tier is. The boundary reuses a distinction the spec already draws rather than
inventing a second one beside it.

**Closes off.** Anything measuring per-decision autonomy at finer grain than
"experiment versus late structure" needs a new datum on the run; the two
mutating operations on `Investigation` are all there is to tier today. A system
that proposes from the prior alone with zero experiments would have its
admissions counted as the opening set, and `agency_metrics` raises rather than
reporting it -- no SPEC §5 system does this, since every one spends half its
budget first.

## 2026-08-13 — item 14: "no proposal layer" and "layer never asked" are different runs

**Decision.** `ScenarioRun.attempts` is `tuple[ProposalAttempt, ...] | None`.
`None` means the system holds no proposal layer; an empty tuple means it holds
one that was never consulted. `AgencyMetrics.proposals` is `None` in the first
case and a record reading zero in the second.

**Approach abandoned.** The first implementation typed it as a plain tuple and
derived the record with `proposal_record(run.attempts) if run.attempts else
None`. That collapses the two: V7 on a scenario whose posterior predictive check
never opens F6's gate produces an empty tuple, and the metric reported it
identically to B4, which has no model to ask at all. The distinction is the one
the proposal record exists to make -- a run that asked five times and used one
differs from one that asked once, and both differ from a system with nothing to
ask -- so a representation that cannot hold it defeats the object. An empty
tuple cannot carry a capability *and* a count.

**Why it was not caught by a test first.** Both tests written at the time read
`proposals is None` on systems that genuinely have no layer, so both passed. The
missing case had no run behind it: V7 appears in the slice metrics only through
S12, where the gate does fire. `test_a_layer_that_was_never_asked_still_has_a_record`
now runs V7 on S1 end to end rather than constructing the case, because the part
that has to get this right is the harness's capture in `run_scenario` and not
the metric's arithmetic.

**Closes off.** `run_scenario` decides the capability by `isinstance(system,
Proposing)` -- structural, so any system exposing `attempts` is picked up and no
system is named. A future system holding a layer must expose the attribute even
on runs where it asks nothing, or it will be reported as having no layer.

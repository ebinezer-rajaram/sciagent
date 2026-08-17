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

## 2026-08-15 — item 1: the recorder is deferred, and the clock was allowed to run

**Decision.** SPEC §11 item 1 -- "Recorder to EC2 and S3", gated on "running, gap
detection live" -- is deliberately not built, and will not be built as part of
the slice. It has zero lines of code with items 2 through 14 complete. This entry
is the record that the omission is a choice rather than an oversight, because
nothing else in the repository can distinguish the two.

**Why.** SPEC §11 says of item 1 "**Do this first; it is the only item with a
clock**", and §13's closing line repeats it: "The next action is item 1: put the
recorder on a websocket feed. Everything else can start whenever. That cannot."
Both are correct about the clock and neither was followed. The reason the core
track proceeded anyway is F2: the two tracks are independent, and the core
research track "answers the central question with no market data, no exchange
semantics, no Rust". Every acceptance criterion A1-A24, every §5 baseline, all
twelve slice scenarios and §9's whole matrix run on `environments/pointproc`,
which is a generator. None of them has ever needed a recorded tick.

So the cost of the omission is not paid by anything currently built. It is paid
by **R4** -- "do closed-world results transfer to higher fidelity?" -- and by any
phase-6 market environment, both of which want history that exists only if
somebody was recording at the time. That history is now permanently missing for
the window 2026-08-01 to today, and each further day widens it.

**The alternative, and what it would have cost.** Building item 1 first, as
specified. It is infrastructure with no A-gate, no dependency on anything in
`sciagent/`, and no effect on any number the slice reports; doing it first would
have delayed every gate by the length of an EC2-and-S3 deployment for a payoff
that arrives at phase 6. The judgement taken -- and it is a judgement, not a
derivation -- is that a wider gap in market history is a smaller loss than a
later validated evaluation apparatus. It is recorded here so that a session
reading §13's closing line does not conclude the backlog was simply misread.

**Closes off.** R4 is unanswerable on any data predating whenever the recorder is
eventually started, and the gap is not recoverable from a vendor for a
websocket feed nobody was subscribed to. Nothing in items 2-15 waits on this.
`scripts/status.py` now reports item 1 as `deferred` rather than as an untracked
item with no A-gate, so the cursor stops reading as though it were pending work
somebody forgot.

## 2026-08-15 — infrastructure: the deny list, corrected on the local machine

**Decision.** `.claude/settings.json` now carries a deny list that was checked
against the permissions documentation rather than carried over from
`~/.claude/`. This closes the entry of 2026-08-04, "the personal deny rules do
not survive the move into the repository", which recorded the working-tree
version as known-wrong and said it "waits on the local machine".

**One finding in that entry was itself wrong, and this supersedes it.** It said
"a `Read` deny covers `Edit` but not `Write`, so six paths were read-denied and
still writable". The documentation says the opposite: a `Read` deny rule also
blocks **`Edit` and `Write`** on the same path, including creating a new file
there. What is genuinely not covered is `NotebookEdit`, and separately there is
**no `Write(...)` rule namespace at all** -- a rule written against `Write`,
`Glob` or `MultiEdit` is accepted and then never consulted, which is the worst
of both worlds. The `Edit(...)` entries kept here are therefore belt-and-braces,
not the load-bearing part.

**What the other three findings turned into.**

- `Bash(curl*|*sh)` and `Bash(iwr*|*iex)` matched nothing, as recorded, because
  `|` is one of the recognised separators (`&&`, `||`, `;`, `|`, `|&`, `&`,
  newline) and a rule must match each subcommand independently, so no subcommand
  ever contains the literal `|`. The documented replacement is to deny the
  network fetchers outright -- `Bash(curl *)`, `Bash(wget *)` -- and reach
  approved hosts through `WebFetch(domain:...)` instead. Nothing in this project
  fetches over `curl`: dependencies come through `uv`, and the one networked
  module uses the `anthropic` SDK.
- The home-directory paths are now spelled `~/.ssh/**`, not `**/.ssh/**`. A
  project-level rule anchors at the project root, so the old spelling could not
  reach the files it named; `~/` is the prefix that anchors at home. The
  project-relative spellings are kept *alongside* rather than replaced, since a
  `.ssh` or `.aws` directory inside a checkout is a different file from the one
  in `$HOME` and both should be refused.
- PowerShell has its own `PowerShell(...)` namespace, parsed from the PowerShell
  AST with its own subcommand splitting, and this machine's primary shell is
  PowerShell. Every destructive `Bash(...)` rule now has a `PowerShell(...)`
  counterpart. A `Bash(...)` rule was never going to be consulted for a
  PowerShell command, which is why the original `iwr`/`iex` pair was doubly dead.

**Measured against what it costs.** 38 deny rules against the 20 that were in the
tree. `Bash(curl *)` and `Bash(wget *)` are the only two that can plausibly
interrupt ordinary work here, and both were unused across this repository's
history.

**Two things deliberately not denied.** `Edit(docs/SPEC.md)` would be the natural
protection for a frozen document, and is omitted because the catalogue change
now planned amends SPEC §4.3 on the demonstrated contradiction §13 provides --
a rule that has to be removed to do sanctioned work trains people to remove
rules. And `Bash(rm -rf *)` in general: the two narrow forms kept are weak by
construction, since the documentation is explicit that Bash patterns are
fragile against re-spelling. They are a guard against an accident, never against
an adversary, and should not be read as more.

**Closes off.** An unattended cloud session now runs with a deny list that
reaches what it names. The reasoning lives here because JSON admits no comments,
so the file itself cannot say why any rule is shaped the way it is.

## 2026-08-15 — infrastructure: the cross-platform instrument could not see the layer it was aimed at

**Decision.** `tests/acceptance/determinism_child.py` now emits a second digest
per case, `metrics/<name>`, over every value the SPEC §4.3 catalogue computes
from the event log. The Windows baseline is recorded below so that a cloud
session can produce the Ubuntu half and diff without a local machine running.

**The instrument and the suspicion did not meet.** The entry of 2026-08-04 makes
this file the thing to run on both platforms before trusting a cloud-produced
artefact, and the entry of 2026-08-05 names the suspected cause of the
Windows/Ubuntu split as `np.dot` in `environments/pointproc/diagnostics.py`
(lines 126, 129, 379, 382) against an OpenBLAS built `DYNAMIC_ARCH`. But the
child hashed `log.to_bytes()` and nothing else, and every one of those `np.dot`
calls is **downstream of the log**. Running it on both platforms would have
produced a clean diff that said nothing whatever about the layer the registry
content-addresses over, and the clean diff would have been read as the check
passing. An instrument that cannot fail for the reason you are worried about is
worse than no instrument, because it retires the worry.

**Values are digested as IEEE doubles**, matching `ExperimentRecord.digest_of`,
not as text. A shortest-round-trip repr hides a difference in the last place,
which is exactly the size a different summation order produces.

**Measured, Windows 11, x86-64, this commit.** Eighteen executions: six
programmes (the reference, the four confounded mechanisms, `SIZE_EXCITATION`)
each run unclamped, under a forced-arrival prefix, and with a component held
fixed. Log digests are unchanged by this edit, so the first column also
establishes that adding the second changed nothing about the first.

```
reference                       c793c834…   metrics/ bb4043bd…
reference+forced                4fd50434…   metrics/ c7aace13…
reference+held                  50f4e2b3…   metrics/ 2a9bbc54…
hawkes                          d0348135…   metrics/ e70c755e…
hawkes+forced                   d22b1dcd…   metrics/ 011a3728…
hawkes+held                     d6a3019a…   metrics/ c6cb92c2…
poisson_mixture                 70477978…   metrics/ 09e8ccc9…
poisson_mixture+forced          b15c221e…   metrics/ b861ab7c…
poisson_mixture+held            fcd7ed49…   metrics/ f20ad404…
regime_switching                d00bc50d…   metrics/ 3b437f42…
regime_switching+forced         01687ad6…   metrics/ f7142948…
regime_switching+held           729531ec…   metrics/ 30d900f6…
seasonality                     41971612…   metrics/ d2877c94…
seasonality+forced              0f1f8789…   metrics/ b251e1c1…
seasonality+held                f905aa58…   metrics/ f2146b52…
size_excitation                 a0c1c91b…   metrics/ 65f46631…
size_excitation+forced          8b09e1e5…   metrics/ 5e019a1a…
size_excitation+held            984026cc…   metrics/ ffa29010…
```

Full digests come from `uv run python tests/acceptance/determinism_child.py`;
the truncations above are for reading, and the diff must be taken over the
command's own output.

**Left incomplete, and it is the same thing as before.** The Ubuntu half has not
been run. This entry moves the check from "would not have answered the question"
to "will answer it", and nothing more. `A1` gained
`test_a1_the_child_digests_the_metric_layer_too`, which asserts both layers are
present so that a later refactor cannot quietly return the instrument to its
previous reach; it deliberately asserts no *value*, because the cross-platform
claim is settled by diffing two runs and not by a constant checked into a test.

## 2026-08-15 — infrastructure: two artefact writers let the platform choose their bytes

**Decision.** `EmpiricalTable.save` and `TranscriptStore.save` pass
`newline="\n"`. `tests/test_invariants.py` grows a static check that every
`write_text` under `src/` pins it, and the repository gains a `.gitattributes`.

**Why it was invisible.** `Path.write_text` opens in text mode with
`newline=None`, which translates every `\n` to `os.linesep` on write — `\r\n`
here, `\n` in a cloud session. Both files therefore differed byte for byte
between the two halves of this project while parsing identically, and **no
round-trip test could see it**, because reading translates the line endings
back. The third invariant says byte-identical output; these are the only two
places output leaves the process as a file somebody else is meant to reproduce.

This matters more for the transcript corpus than for the table. The corpus is
not a cache — `transcripts.py` argues at length that a recorded response *is*
the reproducible artefact, because the sampling parameters that would pin a
model call are rejected by the models in question. An artefact whose bytes
depend on which machine wrote it is not one.

**Measured.** The working tree held `store.py` as CRLF and `README.md` as LF at
the same commit, which is what `core.autocrlf=true` plus editors that write LF
produces. No committed blob contains CRLF, so nothing needed renormalising and
adding `.gitattributes` produced no diff.

**Closes off.** The static check is over `src/` only: a test writing a scratch
file is not producing an artefact anybody compares, and requiring the keyword
there would be noise.

## 2026-08-15 — the cross-platform divergence is real, and it has been measured

**Measured.** S12's final posterior predictive p-value, V7 and both ablation
arms, closed set entertained, scripted provider, shared calibrated table:

| where | commit | platform | final PPC p |
|---|---|---|---|
| item 12, this file line 2126 | a380a21 | Windows | 0.1011 |
| item 13, this file line 2618 | 200d218 | **Ubuntu** | **0.1009** |
| today, at `HEAD` | 0a9afd5 | Windows | **0.101100** |
| today, at item 13's own commit | 200d218 | Windows | **0.101100** |

All three arms agree with each other exactly in both of today's runs -- V7, V3
and V4 return the identical value, which is the architecture behaving as
designed, since on S12 they run the same trajectory and differ only in a brief
the scripted provider ignores.

**The code is not the difference.** The last row is the one that settles it: a
detached worktree at 200d218, the exact commit whose entry records 0.1009, run
on Windows, gives 0.101100. The ten commits between a380a21 and 200d218 are
therefore not what moved the number, and neither is item 14. Same source, same
seed, same script, same library, two platforms, two answers.

**This is the demonstration the entry of 2026-08-05 said was missing.** That one
inferred a Windows/Ubuntu split from a failure that appeared only on Ubuntu,
named `np.dot` in `environments/pointproc/diagnostics.py` against a
`DYNAMIC_ARCH` OpenBLAS as the suspected cause, and closed with "this has not
been demonstrated". It now has been, at the level of a reported number. The
mechanism it proposed also fits: the p-value is a tail sum over binned cells, a
table row is 2000 replicates assigned to bins by those diagnostics, and "one
replicate of 2000 crossing one bin edge is enough" is exactly the size of effect
that moves 0.1011 to 0.1009.

**What follows, and it is the serious part.** The registry content-addresses over
(env version, config, data version, metric version, seed) **with no platform
term**. The fear recorded on 2026-08-04 was that "two entries can share a content
address while holding different numbers, and nothing in the system is positioned
to report it". That is no longer a fear. Any registry built partly on Windows and
partly in a cloud session is suspect, and so is any table shared between them --
including `.cache/tables/`, which is keyed on replicates, seed and design set and
not on the machine that filled it.

**Not yet established, and it is one command away.** That the divergence lives in
the metric layer specifically. Today's evidence is a downstream number; localising
it wants `tests/acceptance/determinism_child.py` -- extended in the entry above to
digest metric values and not only event logs -- run on Ubuntu and diffed against
the Windows baseline recorded there. If the `metrics/` lines differ and the log
lines do not, the diagnostics are the site and the event loop is exonerated.

**Deliberately not decided here.** Whether the content address gains a platform
term, whether tables become platform-scoped, or whether the diagnostics are made
platform-stable by taking the summations out of BLAS. All three are real options
with different costs -- the first two retire every stored artefact, the third is a
change to frozen §4.3 estimators -- and the choice wants the localisation above
first. Recorded now because the measurement is cheap to lose and expensive to
redo, and because no further cloud-produced number should be trusted until it is
settled.

## 2026-08-15 — the estimators fold exactly now, and the simulation did too

**Decision.** Every float-folding reduction that reaches a stored number goes
through the new `sciagent/core/reductions.py`, which multiplies and subtracts in
numpy and sums with `math.fsum`. `METRIC_VERSION` 1.0.0 -> 1.1.0 and
`LIBRARY_VERSION` 1.1.0 -> 1.2.0. This supersedes the option left open in the
entry above; the other two -- a platform term in the content address, and
leaving it alone -- are rejected below.

**The scope was larger than the diagnosis suggested, in the direction that
matters.** The 2026-08-05 entry named `np.dot` in `diagnostics.py`. Searching
for the *property* rather than the named call found 23 order-dependent folds in
`diagnostics.py` -- `np.mean` and `np.var` as well as `np.dot`, several of them
in metrics that are design axes -- and, not previously suspected, **two in
`components.py`**: the Hawkes intensity kernel and the size-excitation kernel.

Those two are inside the *simulation*. The intensity is what the thinning loop
compares against, so a summation order chosen by the CPU changes the next
arrival time and therefore the event log itself. Everything else in this
investigation concerned numbers computed *from* a log. This one meant the log.
It also means `determinism_child.py`'s original event-log-only digest was not
merely aimed at the wrong layer -- it was aimed at a layer that was itself
affected, and would have caught this had it ever been run on two platforms.

**Measured, and it is the confirmation that the change did what it claims.**
Re-running the child after the change, against the baseline recorded above:

| case | event log | metrics |
|---|---|---|
| `reference` (all three modes) | **unchanged** | changed |
| `poisson_mixture` (all three) | **unchanged** | changed |
| `regime_switching` (all three) | **unchanged** | changed |
| `seasonality` (all three) | **unchanged** | changed |
| `hawkes` (all three) | **changed** | changed |
| `size_excitation`, `+held` | **changed** | changed |
| `size_excitation+forced` | unchanged | changed |

Exactly the four mechanisms with no intensity kernel kept their logs, and
exactly the two with one moved. `size_excitation+forced` keeping its log is not
an anomaly: under a forced-arrival prefix the kernel's history window holds few
enough terms on that trajectory that pairwise summation and exact summation
agree bit for bit, which is what one expects of a sum with no cancellation in it.

**Why not a platform term in the content address.** It is the honest option and
it was rejected on cost. Cloud and local sessions could then never share a
calibrated table or a registered result, so every cloud session rebuilds from
cold -- and this repository is set up to be driven from a phone, which is most
of the point. It also preserves the defect rather than fixing it: an estimator
that disagrees with itself across machines stays wrong, and the address merely
stops the disagreement being visible.

**Why not leave it.** The measurement above is not of a rounding curiosity. A
metric value picks a bin, a bin picks a count, a count picks a likelihood.

**Cost, measured before committing to it.** On slice-realistic arrays (n=512)
the exactly-rounded variance costs 29.06us against numpy's 13.68us and the dot
13.13us against 1.18us -- about **1.1 seconds** added to a full cold table build
of 35 rows at 2000 replicates. The ratios look worse than the cost is because
the absolute numbers are microseconds. An earlier estimate of 25-37x was wrong:
it squared the deviations in a Python generator. Keeping the elementwise half in
numpy is what makes this cheap, and it is sound because an elementwise operation
is a set of independent correctly-rounded ops with no accumulator to reorder --
vector width cannot change it.

**What a version bump costs, which is the number to plan around.** The suite
after this change was **909 passed, 7 skipped in 31m45s**, against 875 in 6m44s
warm on the same machine immediately before it. (The final count for the change
as shipped is higher -- review and audit added tests afterwards -- but the 31m45s
is the figure to plan around, and it was measured on the run that rebuilt.) Almost none of that is the
reductions: both `ENV_VERSION` and `METRIC_VERSION` moved, so all three cached
tables -- gate, slice and search -- missed their addresses and were rebuilt from
cold. This is what any versioning event costs here and is worth knowing before
scheduling one, particularly on a 4 vCPU cloud VM where it will be longer. The
old files are left in `.cache/tables/` rather than deleted; they are addressed by
content, so they are simply never read again.

**This closes one class of divergence, not all of them, and the distinction has
to be stated because an earlier draft of this entry did not.** The *fold* is now
exact, so no summation order can matter. Nothing here makes the addends
portable.

**Transcendental functions are not correctly rounded, and this is the bigger
remaining hole.** Measured: `math.exp` disagrees with the correctly-rounded
double -- `Decimal.exp()` at 60 digits -- for **17694 of 20000** inputs across
`[-40, 0]`. Every practical `exp` is allowed that error and implementations
differ, so the same expression can round differently under the MSVC runtime and
under glibc. Both Hawkes kernels sum `np.exp(...)` terms, which means an exactly
rounded fold over inexactly rounded addends is still only as portable as the
platform's `exp`. Closing it would need a correctly-rounded math library, which
is a dependency this project does not have and should not acquire for this.

So the honest statement of what changed: the class that was *demonstrated* --
BLAS and pairwise summation choosing kernels from CPU features -- is closed, and
what remains is narrower and more testable. Nobody may conclude from this entry
that the platforms now agree.

**Left open, unchanged by this.** `np.fft.rfft`, in the two spectral metrics.
There is no exact-rounding substitute for a transform, and numpy's FFT is
pocketfft compiled in rather than a dispatching library, so it is *probably*
stable across x86-64 -- an expectation, not a measurement.

**Registered is not measured, and the distinction is what makes this deferrable.**
`spectral_peak_frequency` and `spectral_peak_prominence` are both in
`metric_registry()`, so their *names and versions* enter `MetricRegistry.version`
and therefore every experiment's content address. Their *values* enter nothing:
`_QUERY_EDGES` gives a discretisation to four metrics plus `mean_rate` for the
forced design, and a metric with no discretisation is not an axis of any design,
so no table row and no registered result holds an FFT-derived number today. The
address covers metric identity, not metric output. Promoting a spectral metric
to an axis is exactly what the planned §4.3 widening would do, and that is the
moment this stops being deferrable -- settle it with the two-platform diff first.

**One trap for whoever takes that diff.** `determinism_child.py`'s `metrics/`
lines digest *every* metric the catalogue declares, spectral ones included --
deliberately, because the point of that instrument is to see a divergence before
it reaches a stored number. So a `metrics/` mismatch between the platforms does
not by itself mean a stored number differs: it has to be attributed to a metric
first, and a difference confined to the two spectral entries is the FFT question
above rather than a live registry problem.

**Still not done: the Ubuntu half.** This entry makes the estimators
platform-stable by construction and confirms the change moved what it should on
one platform. It does not demonstrate that two platforms now agree. The baseline
above supersedes the one in the preceding entry -- those digests were taken
before this change and no longer describe this code.

**New Windows baseline**, this commit, from
`uv run python tests/acceptance/determinism_child.py`:

```
reference               c793c834…  metrics/ d7f438d9…
reference+forced        4fd50434…  metrics/ 24169cd7…
reference+held          50f4e2b3…  metrics/ 36aa5e1e…
hawkes                  b8cd586f…  metrics/ 350ce478…
hawkes+forced           3d624450…  metrics/ 44405918…
hawkes+held             e6a3cdbb…  metrics/ 9c58aa4d…
poisson_mixture         70477978…  metrics/ 619fa2cd…
poisson_mixture+forced  b15c221e…  metrics/ a78bd4ae…
poisson_mixture+held    fcd7ed49…  metrics/ acc4c3fa…
regime_switching        d00bc50d…  metrics/ 8c1f5a45…
regime_switching+forced 01687ad6…  metrics/ f1fec3fa…
regime_switching+held   729531ec…  metrics/ fdad3f46…
seasonality             41971612…  metrics/ 429a97e9…
seasonality+forced      0f1f8789…  metrics/ d4e12f0e…
seasonality+held        f905aa58…  metrics/ 42e6a5f6…
size_excitation         31e0e252…  metrics/ b08ed8cf…
size_excitation+forced  8b09e1e5…  metrics/ 25fdcf17…
size_excitation+held    6cd9de4a…  metrics/ d8b72f91…
```

**One site in `sciagent/` too, and the first version of the guard could not see
it.** `EditGrammar.distance` ended with `float(cost[rows, columns].sum())`
(`core/edits.py`) -- a bare method-style fold over the assignment costs, which
is SPEC §8's D1 and therefore reported and stored. Two things had hidden it. The
guard was scoped to `src/environments`, on the reasoning that a fold outside a
diagnostic is summarising for a human; and it keyed on the *base* of the call
being the numpy alias, so `x.sum()` -- whose base here is a subscript, not a
name -- could never match however it was scoped. The function three lines below
it already used `math.fsum` for its displacement sum, so this was a half
migration nobody had a reason to notice.

An earlier draft of this entry claimed "`sciagent/` needed no change: it already
folded with `math.fsum` throughout". That was wrong, and it was wrong because it
was inferred from a search that could not have found the counter-example. The
guard now covers the whole of `src` and both spellings, with controls that watch
it catch the subscript case specifically.

**No third version bump, and the reason is worth stating.** `distance` is
reached only by `eval/scoring.py`'s D1 and by `ScenarioRun.structural_distance`.
It does not touch `code_length`, so the structural prior is unmoved; and neither
caller writes to the registry, whose key covers env, config, data, metric and
seed but nothing about scoring. So no stored row changes and `GRAMMAR_VERSION`
stays put -- the grammar's expressible space is what that version is about, and
it has not changed. What does change is every *reported* D1, in the last places.
The figures recorded for item 12's D1-D6 table are quoted to two decimals and
are unaffected at that precision.

**Closes off.** `tests/test_invariants.py` gains
`test_metric_values_use_deterministic_reductions`, which fails on any
order-dependent float fold anywhere under `src` -- so this is a property of the
tree from now on rather than a set of call sites somebody remembered to change.
Selections are deliberately not flagged: `np.median`, `np.max` and `np.argmax`
pick from a multiset rather than accumulating over it, and no kernel can reorder
a selection into a different answer.

## 2026-08-15 — infrastructure: the suite's cost is one test, not the suite

**Measured.** Full suite, warm tables, on the developer desktop with the machine
otherwise idle: **980 passed, 7 skipped in 390.96s (6m30s)**. The slowest 25
tests account for **360.6s** of that, and a single test accounts for **146.88s**:

| seconds | test |
|---|---|
| 146.88 | `test_oracle.py::TestTheBracketIsCoherent::test_the_floor_never_exceeds_what_greedy_achieves[S1]` |
| 49.77 | `test_a06_a11.py::TestEngineInvariants::test_the_table_is_reproducible_across_builds` |
| 43.49 | `test_a06_a11.py::TestA6LikelihoodEstimation::test_a6_estimate_is_within_two_standard_errors_of_exact` |
| 17.43 | `test_a06_a11.py::TestA9PosteriorPredictiveChecks::test_a9_false_positive_rate_is_within_twice_nominal` |
| 17.13 | `test_oracle.py::TestTheInterventionEarnsItsPlace::test_the_observational_designs_alone_are_worse` |

**Why this is worth the six and a half minutes it costs to reproduce.** The
entry of item 11 recorded the suite jumping from 3m03s to 6m23s and named
`test_oracle.py` at 97 seconds *for the whole file*. It is now one
parametrisation, `[S1]` alone, at 147 seconds — 37% of the suite in one test.
Anybody reaching for a general remedy should know that first.

It rules out the obvious one. `pytest-xdist` with `-n auto` on twelve cores
cannot finish faster than its slowest single test, so the floor is ~2m30s rather
than the ~35s a naive cores-divided reading suggests. That is still a threefold
win and worth having, but it is a different decision than it looks like, and it
carries a hazard: workers would race on `.cache/tables/`, where the cold/warm
gap is 46x (the item 9 gate is 3m46s cold against 10.8s warm).

**Closes off.** Nothing yet. `pytest-xdist` was deliberately not installed —
`uv add` writes `pyproject.toml` and `uv.lock`, and a second session was
committing to both. It stays open, and the condition on adopting it is a
parallel run whose results are byte-identical to the serial one, not a
wall-clock improvement.

## 2026-08-15 — infrastructure: skill triggering cannot be measured on Windows

**Approach tried and abandoned.** `skill-creator`'s description-optimisation
loop, to measure whether the implicitly-invoked skills (`/recall`, `/handoff`)
actually fire when they should. It cannot run on this machine at all.

**Why it fails.** `scripts/run_eval.py:108` polls the `claude -p` subprocess with
`select.select([process.stdout], ...)`. On win32 `select.select` accepts only
sockets, never pipes, so every query raises `WinError 10038` before a byte is
read — and the harness scores the exception as "the skill did not trigger".
`--num-workers 1` fails identically; it is not a concurrency problem.

**This is worth recording because the failure mode lies convincingly.** It
reports plausible scores. The first run returned 4/8 and 4/7 on held-out
queries, with every negative apparently passing and every positive apparently
failing — a coherent, believable picture of two undertriggering skills, which
prompted a rewrite of both descriptions that measured no better. What settled it
was a control query reading literally *"invoke /recall and tell me the recorded
suite timings"*, which also scored 0.00. That is impossible if the harness works,
and it is the cheapest possible check. Run a control that must trigger before
believing any triggering number from this tool.

**Closes off.** The two descriptions are written to the documented
undertriggering guidance and are **unmeasured**; do not cite a triggering figure
for them. Measuring means a cloud session — Ubuntu 24.04, where `select` on a
pipe is fine — and that is the one part of this workflow that genuinely belongs
in cloud rather than local.

## 2026-08-15 — infrastructure: one working tree, two sessions, and a false green

**Work left deliberately incomplete.** `.claude/hooks/suite-freshness.sh` skips a
redundant suite run by hashing the tree and comparing against the last recorded
green. Two ways it could certify a run that never happened were found and fixed;
the root cause of the second is not fixed, and this records what would fix it.

**The second defect was observed live, not reasoned about.** A suite ran
18:00–18:07. Another session added a dependency at **18:05:56**, five minutes
in. The green recorded at 18:07:10 hashed the *new* `pyproject.toml` and
`uv.lock` and certified a tree pytest had never executed against. Nothing
noticed, because the script compared against the tree in front of it rather than
the tree the run saw. It now pins the hash with `begin` before pytest and
refuses to `record` if the tree moved.

(The first defect was ordinary and is described where it was fixed: `cd ""`
succeeds in bash, so an unset `CLAUDE_PROJECT_DIR` silently hashed zero files,
and sha256 of nothing is still 64 characters.)

**What actually fixes it, and what that waits on.** Detection is a patch over
the real problem, which is that concurrent sessions share one working tree —
the same problem `/ship` spends twenty lines of scope discipline on. A worktree
per session removes it at the root. The obstacle is measured and specific:
`tests/slice_tables.py:75` hardcodes `CACHE = <repo root>/.cache/tables`, and
`.cache/` is gitignored, so **every new worktree starts cold** — against a 46x
cold/warm gap that would make a fresh worktree's first suite run far worse than
the contention it avoids. Worktrees need that path to honour an environment
variable first, so all trees share one warm cache. That is a one-line change to
a file another session was holding, which is why it is not made here.

**Closes off.** `record` still cannot verify that pytest ran or passed; the
caller asserts it. Closing that would mean the script owning the run, which
forfeits backgrounding — the thing that makes 6m30s tolerable. Left open
deliberately, and made moot by the worktree fix rather than solved separately.

## 2026-08-15 — a second live backend, billed to a subscription

**Decision.** `sciagent/systems/llm/agent_sdk_provider.py` reaches the same model
through the Claude Agent SDK, which authenticates a spawned Claude Code process
with a subscription token instead of API credits. It is a **sibling** of
`AnthropicProvider`, not a replacement: the id is part of every call address, so
the two cannot resolve each other's calls and the choice is made *before*
recording. Most of the reasoning is in the module docstring, which is the right
place for it; what follows is only what the repository cannot tell you.

**Why now rather than later.** No transcript corpus exists yet. The provider id
is in the address, so this choice is free today and costs a full re-record of
item 15's matrix once one is on disk. That asymmetry, not the credit saving, is
what made it worth doing before the matrix rather than after.

**The external fact the whole thing rests on, with its date.** Anthropic
announced on 2026-05-14 that Agent SDK and `claude -p` usage would leave the
Pro/Max subscription pools on **2026-06-15** for a separate monthly credit
(\$100 at Max 5x, \$200 at Max 20x) billed at API rates. It was **paused on the
day it was due to take effect**; programmatic usage still draws on subscription
limits, there is no credit to claim, and Anthropic said it is reworking the plan
and will give advance notice. This is not derivable from the repo, it is
expensive to re-research, and it decides whether this backend is viable at all.
If it returns, the fallback is `AnthropicProvider`, which is why that module was
kept working rather than migrated.

**Work left deliberately incomplete, and what it waits on.** Three things, in
descending order of how much they would hurt:

1. **The hermetic option set is unproven against a real session.** Every offline
   test injects a stand-in for `query`, so what is asserted is the request that
   *would* be sent. The specific unknown is whether the CLI accepts
   `--setting-sources=` — the SDK emits that empty form for `setting_sources=[]`,
   and nothing offline can say the CLI reads it as "none" rather than as one
   unnamed source. Waiting on one live call.
2. **Subscription rate limits across a recording run are unmeasured.** Item 15 is
   ~1120 proposals; Max has 5-hour and weekly caps. Whether a matrix fits, or
   needs to be spread over days, is a pilot measurement nobody has taken.
3. **The Claude Code binary version is not in the call address.** It is part of
   what produced the artefact and not part of its identity. A corpus recorded
   through this backend is reproducible given a *comparable* binary, not any
   binary — a real gap, recorded rather than hidden, and not closable without
   changing `ADDRESS_VERSION` and every address with it.

**Closes off.** Nothing about the Messages API path, which is untouched and still
the default anywhere a script names a provider explicitly. It does *not* settle
which backend item 15 records against — that needs (1) and (2) answered first.

## 2026-08-15 — the Agent SDK backend, measured against live sessions

Closes open item (1) of the entry above. Item (2) — subscription rate caps across
a recording run — is still unmeasured, and item (3) has changed shape.

**The hermetic option set works, and here are the numbers.** A live session under
`setting_sources=[]`, `tools=[]`, `skills=None` and a bare-string `system_prompt`
reports `tools == ["StructuredOutput"]` — every built-in tool off — no project
skills, no project agents, no MCP servers, no plugins, and `apiKeySource: "none"`
confirming subscription auth. The load-bearing figure is the **593-token total
prompt**: Claude Code's own system prompt plus the sixteen bundled skill
descriptions the manifest still lists would be thousands of tokens, so the
manifest lists what the *session* knows about, not what reaches the model. A full
proposal costs roughly **$0.008–0.05**; a trivial turn measured $0.0078.

**An approach that looked right, passed a probe, and was wrong.**
`env={"CLAUDE_CODE_SIMPLE": "1"}` — what `--bare` sets — was added to close the
one residual, and reverted. Setting it *does* drop `memory_paths` from the
manifest while `apiKeySource` stays `"none"`, so an init-only probe says the two
halves of bare mode are separable. They are not: with it set, **every turn fails**
— `is_error=True` under a `success` subtype, no output, `total_cost_usd == 0`,
meaning no model call was made — while a control turn without it succeeds. The
probe was too cheap to be honest, because it broke out of the stream before the
model ran and so never exercised authentication. **A manifest that looks right is
not a turn that works**; any future probe of this backend must complete a turn.

**The residual it was meant to fix does not leak, and that is measured too.**
`memory_paths.auto` stays resolved under `setting_sources=[]`, pointing at the
per-project auto-memory directory — which is exactly where a Claude Code session
is told to write memories, so it reads as a contamination path that would arm
itself later. A canary file planted there, with a session asked to report any such
token back, came back `"none"`. Resolved in the manifest, not read into the
prompt. Re-run that canary before trusting this if the CLI's memory behaviour ever
changes.

**Item (3) has changed shape: the binary version is now observable.** The init
event carries `claude_code_version` (`2.1.233` for these measurements). The gap is
no longer that it cannot be seen — it is that `call_address` does not cover it and
`Transcript` has no field to hold it. Closing it properly means changing
`ADDRESS_VERSION` and invalidating every address, which is cheap only while no
corpus exists. Worth deciding before item 15 records, not after.

**Closes off.** Do not re-add `CLAUDE_CODE_SIMPLE`; `tests/test_llm.py` asserts
`options.env == {}` for that reason, since re-adding it would break every live
recording while the offline tests stayed green.

## 2026-08-16 — performance: where the simulation time actually was, and the one convention it cost

**Decision.** An optimisation pass over the event loop, the two defect-key
renderings and the oracle's search, taken only where the change is an
equivalence transformation. `DrawContext` is no longer `frozen`, which is a
deliberate exception to this project's rule that value types are frozen.

**Why the exception.** A `DrawContext` is constructed at exactly one site,
passed to one kernel and discarded; nothing hashes it, compares it or stores it.
It is an execution context, not a value. `frozen=True` routes all eight fields
through `object.__setattr__`, and one context is built per component per event.
`slots=True` stays, so a kernel still cannot invent an attribute; what is given
up is the error on rebinding an existing one, which no kernel does. If that
trade ever looks wrong, the measurement below is what to re-examine, not the
convention.

**Measured, per draw, and this is the part worth keeping.** These cost a
morning to isolate and are invisible in a profile that attributes ufunc time to
its caller:

| call | ns/op |
|---|---|
| `np.isfinite(x)` on a Python float | **1023.5** |
| `math.isfinite(x)` | **27.4** |
| frozen+slots dataclass, 8 fields | **1175.6** |
| plain slots dataclass, 8 fields | **275.3** |
| `FrozenDict.__getitem__` | 47.7 |
| `dict.__getitem__` | 19.9 |

`np.isfinite` on a *scalar* is the surprise: a ufunc dispatch costs forty times
the `math` predicate for the identical condition. At one guard per draw it was
about a fifth of simulation time on its own. The rule this leaves behind is
narrow and worth keeping: **inside a per-draw or per-event loop, reach for
`math`, not for a numpy ufunc on a scalar.** Nothing here argues against numpy
on arrays, which is where the elementwise half of `core/reductions.py` still
belongs.

**Measured, on the suite, warm and on identical cache addresses.** The eight
slowest tests went from 359.8s to 214.8s. The two largest movements:
`test_the_floor_never_exceeds_what_greedy_achieves[S1]` 162.22s → 95.11s, and
`test_the_table_is_reproducible_across_builds` 51.33s → 26.90s. No new file
appeared in `.cache/tables/`, which is the useful check: the content addresses
did not move, so the tables hold the same numbers.

**Tried and abandoned: a `NamedTuple` `DrawContext`.** It reached 2.25x against
1.47x for the hoisting alone, bit-exact over 300 triples. Rejected because a
`NamedTuple` field named `index` shadows `tuple.index`, which pyright flags and
which would leave a value type whose most-read attribute collides with a method
of its own base. Dropping `frozen=` from the dataclass buys effectively the same
construction cost with none of that.

**Tried and abandoned: committing golden digests as a regression test.** It was
the intended verification for this pass and it contradicts the entry of
2026-08-15 on the cross-platform instrument, which states that A1 "deliberately
asserts no *value*, because the cross-platform claim is settled by diffing two
runs and not by a constant checked into a test". A digest constant captured on
Windows would also make the *unresolved* event-loop-versus-metric-layer question
fail as if it were a regression, in exactly the cloud sessions this repository
is meant to be driven from. Evidence for this pass is instead the before/after
diff of `tests/acceptance/determinism_child.py`: all 36 lines identical, both
layers.

**The caches opened an invariant-3 hole, and closing it fixed an older one.**
Found by the `invariant-auditor` on the finished diff, not by writing it.
`structure_key` and `defect_key` gained `@lru_cache`, which identifies arguments
by `__eq__`; both *render* their key through `repr`. Those disagree in exactly
two places — `1 == 1.0` across the numeric tower and `-0.0 == 0.0` across signed
zeros. Before the caches, two equal-but-differently-rendered defects produced two
different table addresses: wrong, but deterministic. With them, the second is a
cache hit and gets the first's string, so which address a structure lands under
becomes a function of call order. That is the thing invariant 3 exists to forbid,
and it reaches a content-addressed table.

Fixed at the root rather than by dropping the caches: `sort_key` now normalises
each parameter with `float(value) + 0.0`, so equal defects cannot render
differently and the older latent bug is closed with the new one. **Measured
before making it: across all 23 grids in both grammars, 1472 values, plus the
closed set's own 13 — no non-float, no signed zero.** The normalisation is
therefore a no-op on every value the grammar can currently produce, which is why
no table address moved, and it is in the code to keep it that way rather than to
change anything today. `ParameterGrid.values` is what makes it true, building
every value through `float(...)`; `parameters(**values: float)` does not check at
runtime, and mypy's numeric tower admits an `int` silently.

**Left deliberately incomplete, with the number attached.** Parallelising the
replicate loop in `EmpiricalTable.with_structure` was prototyped and measured at
**3.52x on 10 workers at 1000 replicates**, with counts identical to the serial
build — each replicate is a pure function of `(defect, template, seed)` and a
tally is an integer count, so a fixed reduction order cannot change a row. It
was **not** taken, because it is a change to the execution model on the path
invariant 3 protects and wants its own gate rather than inheriting confidence
from this pass. Two things constrain the design if it is picked up: chunk by
replicate and not by template, or the executor's one-slot log cache is destroyed
(measured: it saves 60% of executions, 100 `simulate` calls to 40 `execute`
calls); and Windows spawn costs about 1.4s of worker startup, so it is a loss
below a few hundred replicates. On the 4 vCPU cloud VM the win is much smaller
than 3.52x, and it would make every timing in this file incomparable — see the
contention measurement in `CLAUDE.md`.

**Also left open: nothing now guards the RNG stream against a future refactor.**
A1 checks self-consistency across processes and repeats, which a refactor that
moved the stream would still satisfy. That gap is the reason goldens were
proposed at all, and it is still there.

**Closes off.** The executor's one-slot log cache was examined and is already
optimal on the slice's design set — the four `QueryDiagnostic` templates share an
operation and hit it, and the remaining misses are genuine seed changes. Do not
re-open it looking for a win.

**One caveat on the green, and it is the hazard already recorded.** Both suite
runs above shared a working tree with another session, which landed 973d79a
("a second live backend") after the second run finished. That is the situation
described on 2026-08-15 under "one working tree, two sessions, and a false
green". The **per-test** durations are comparable — every test compared lives in
`test_oracle.py`, `test_a06_a11.py` or `test_baselines_slice.py`, none of which
that work touches — but the suite wall-clock is not, and the green predates the
merge. Re-run before shipping rather than trusting it.

## 2026-08-16 — infrastructure: a worktree per local session, and what it took

**Decision.** Concurrent local sessions each get their own git worktree, created
under `.claude/worktrees/` and merged back to `main` by `/ship`. This closes the
item left open on 2026-08-15 under "one working tree, two sessions, and a false
green", which named worktrees as the root fix and the table cache as the blocker.

**Why now, and the evidence it is not theoretical.** The shared tree had already
blocked work twice in this file — a one-line cache fix left unmade "because
another session was holding the file", and `pytest-xdist` (a measured threefold
win) left uninstalled because `uv add` writes two files another session was
committing to. It happened once more *during* this change: `main` moved from
973d79a to be97cb0 mid-session, absorbing seventeen dirty files that were not
mine.

**The measured numbers, which are the whole justification.** Acquiring the slice
table in a fresh worktree:

| | cache | time |
|---|---|---|
| cold — the worktree's own, empty | `_verify/.cache/tables` | **3m11.316s** |
| warm — shared via `SCIAGENT_TABLE_CACHE` | main tree's `.cache/tables` | **1.055s** |

A factor of **181**. The 46x recorded on 2026-08-15 is not in conflict: it timed
the item 9 gate, which does more than acquire the table. Without the shared cache
every new worktree pays three minutes on first use and worktrees cost more than
they save. Also measured, because both were guessed at in planning: a worktree
`.venv` builds in **5.02s** (53 packages) and its apparent 593M is hardlinked to
uv's cache — sampled link count 3, so it is not new disk.

**Three hazards the earlier entry did not anticipate.** Each was found by probing
rather than by reasoning, and each would have failed silently:

1. **`hook_cd_project` resolved to the wrong tree.** It preferred
   `CLAUDE_PROJECT_DIR` over its script-relative fallback, so a worktree session
   with that variable pointing at the main tree pinned, recorded and checked
   `suite-freshness` against a tree the run never touched — the exact false green
   the script exists to prevent, arriving through the back door. The script's own
   location now wins, since the hooks are checked in and therefore per-worktree.
2. **`.claude/worktrees/` was not gitignored**, so every worktree's files showed
   as untracked in the parent — the noise worktrees exist to remove.
3. **`EmpiricalTable.save` was not atomic**, which only matters once trees share
   a cache. Fixed with a temp file plus `os.replace`, then measured under
   deliberate contention: 2000 reads, **0 partial**. But one *writer* died with
   `PermissionError` (WinError 5) — Windows refuses `MoveFileEx` while another
   process holds the destination open. Readers are never wrong; writers must
   wait. Hence the bounded retry; without it, concurrent suites would be flaky
   rather than corrupt.

**Closes off.** Worktrees do **not** buy parallel testing — the 30m37s contention
figure is CPU and applies across trees exactly as within one. `begin`/`record` is
kept rather than removed: it still catches your own edits during a backgrounded
run, which is what backgrounding invites. `record` still cannot verify pytest ran
or passed; the caller asserts it, and that remains open. Cross-platform
determinism is untouched — `SCIAGENT_TABLE_CACHE` is unset in cloud sessions, so
the fallback path is the previous behaviour exactly.

## 2026-08-16 — infrastructure: what shipping without the review step cost

**Work left deliberately incomplete, and an approach that failed.** `136550a` was
pushed without `/ship` step 2, which has always called for `/code-review` and the
`invariant-auditor` subagent. Running them afterwards found nine issues. Two were
confirmed by probe and meant the worktree feature did not work in the case it was
built for. Both are fixed here; this records what they were, because both were
things the commit's own verification claimed to have checked.

**Defect 1: the verification tested an invocation form nobody uses.** `136550a`
made `hook_cd_project` prefer `BASH_SOURCE` so a worktree session would resolve
to its own tree, and a probe confirmed it. But `settings.json` invokes hooks as
`bash "$CLAUDE_PROJECT_DIR/.claude/hooks/X.sh"`, so `BASH_SOURCE` is an absolute
path into the *main* tree however the session started. Measured on the same
session:

| invocation | resolved tree |
|---|---|
| `settings.json` form (the real one) | main tree |
| manual `. .claude/hooks/lib.sh` (the one probed) | worktree |

`guard-determinism.sh` would therefore have checked invariant 3 against the main
tree after an edit in a worktree and reported clean — which that hook's own
comment calls worse than no guard. The fix asks git, which is the only
participant that knows: the tree containing the edited file when a caller passes
one, else the session's cwd. Cost 0.04s, against the 0.038s `sed` already
accepted per edit.

**Defect 2: the mechanism could not reach the case it was for.** The shared table
cache was keyed on `SCIAGENT_TABLE_CACHE`, set in `.claude/settings.local.json`
— which is untracked, so no worktree checkout can contain it. Every worktree
therefore took the cold path. The original 1.055s measurement held only because
the variable had been set by hand on the command line. Now resolved from
`git rev-parse --git-common-dir`, whose parent is the main worktree; the variable
survives as an override. Verified with it unset in a fresh cold worktree: cache
resolves to the main tree, acquisition **1.084s** against 3m11s.

**Both defects share one cause, which is the reusable lesson.** Each was a probe
that confirmed the mechanism in a configuration the real system never uses. A
green probe against the wrong setup is indistinguishable from a green probe
against the right one, and neither `mypy`, `ruff` nor the suite can tell the
difference — none of them execute a hook or a worktree. The independent review
found both within minutes, which is the argument for running it *before*
committing rather than after. CLAUDE.md now records that invoking `/ship` is the
authorisation for its step-2 subagents.

**A judgement call worth recording.** `cached_table` now catches `TableError`
from `table.save` and returns the table anyway. That sits close to the rule
against suppressing errors, and the reasoning is that publishing a cache is not
part of building a table: the computation succeeded, and letting a contended
write discard a 3m11s build would trade correctness for nothing. The catch is
narrow — `TableError` is specifically what `save` raises when it gives up — and
the failure is reported on stderr rather than swallowed.

**Left open, and why it was not closed here.** The auditor flagged that
`ENV_VERSION` is composed of three hand-maintained version literals
(`grammar.py`, `components.py`, `operations.py`) rather than a hash of the
environment's source, as `outcomes.py` itself documents as an interim stand-in.
Sharing one cache across worktrees widens that exposure: two trees on different
commits now rely on those literals having been bumped. Not fixed, because
changing the addressing scheme invalidates every cached table and touches what
A6–A11 calibrate against. It waits on the environment protocol.

Also open: the full multi-process race was validated out-of-band, not in the
suite. `tests/test_empirical_io.py` covers the *mechanism* deterministically by
patching `os.replace` — retry, give-up, no leaked temporary, and an existing
table left intact by a failed save — because a real two-process race is slow and
intermittent and asserts the absence of a symptom rather than the presence of the
mechanism.

## 2026-08-16 — the main working tree does not obey .gitattributes

**A measured finding, handed to a separate investigation.** Found while checking
whether a fresh worktree could inherit the main tree's recorded green. It cannot,
and the reason is not the freshness mechanism.

`.gitattributes` sets `* text=auto eol=lf`, and its own comment gives the reason:
Windows locally, Ubuntu in cloud sessions, and SPEC's third invariant asking for
byte-identical output. A fresh worktree checkout obeys it. **The main working
tree does not.** Measured on `tests/test_invariants.py`, a file nothing in this
session touched:

| | bytes | endings |
|---|---|---|
| stored blob | 24673 | LF |
| fresh worktree checkout | 24673 | LF |
| main working tree | 25240 | CRLF |

**132 of the hashed `.py` files** differ between the main tree and a fresh
worktree on line endings alone. `core.autocrlf` is `true` at system and global
scope and unset locally, so these were checked out before the `.gitattributes`
rule landed; git renormalises only on checkout, never spontaneously, and
`git diff` reports nothing because it normalises CRLF away on the way in. That
is why this has been invisible.

**Consequences, and what is *not* claimed.** A fresh worktree cannot match a
green recorded by the main tree, so the shared green record helps within a tree
and between established worktrees but not on first use. Beyond that: source line
endings do not change Python's behaviour, and `ENV_VERSION` is composed of
declared literals rather than a hash over source, so **no numerical impact was
observed and none is asserted**. What is true is that the file written to keep
the Windows and Ubuntu halves byte-identical is not in force locally, which is
worth knowing before any cross-platform table is compared.

**Deliberately not fixed here.** Renormalising rewrites ~132 working files,
which is not a thing to do while other sessions hold them open, and it is a
larger question than the change it was found during. Left for its own
investigation. The cheap check for whoever picks it up: compare
`wc -c` of a file against `git show HEAD:<path> | wc -c`.

## 2026-08-16 — what the pre-commit review caught that the post-commit one had not

**An approach that failed, recorded because the failure repeated.** The fix for
`136550a` was itself reviewed before committing, this time. Five findings, and
one of them was the same *class* of mistake as the defects being fixed.

**The one worth remembering.** `cached_table` was given a `try/except TableError`
so a contended cache write could not discard a built table. `search_table` and
`save_gate_table` write to the same shared directory and were left unguarded --
and `save_gate_table` is reached from six test modules, so it is by far the more
likely collision. The low-traffic path was protected and the high-traffic one was
not. Both reviewers found it independently; nothing mechanical could, because
`mypy`, `ruff` and a green suite all pass either way. All three writers now go
through one `_publish` helper, so the protection cannot be applied to some
callers and forgotten at others.

**The rest, briefly.** `_cache_root` asked git for the enclosing repository
without checking the answer was *this* repository, so a tree lacking its own
`.git` would put its cache in an outer repo's root -- the same landmark check
`_hook_is_project_root` already had, missing here. A `# type: ignore[arg-type]`
had reached the new test, which CLAUDE.md forbids outright; replaced by spelling
the accepted type. `except BaseException ... raise` became `try/finally`, which
gives the same cleanup while catching nothing.

**A claim corrected rather than defended.** The comment on the shared green
record said a reader "must never see a half-written set". The temporary-plus-move
delivers that for readers, but `record` remains a read-modify-write, so two trees
recording at the same instant can lose one green. Left as is -- the loser re-runs
a suite it need not have, costing seven minutes and no correctness -- but the
comment now says what is true rather than what was intended.

**Also corrected:** the previous entry says a failed cache write is "reported on
stderr rather than swallowed". Pytest captures stderr on a passing test, so in a
green run that notice appears only under `-s`. Still not swallowed, but weaker
than the earlier wording implies.

## 2026-08-16 — /ship §3a cannot run from the worktree it mandates

**Work left deliberately incomplete.** §3a's fast-forward, and the push step
`d509ba0` added beside it, both reach the shared checkout through `git -C`. A
worktree-isolated session is refused:

```
$ git -C "C:/Users/Ebinezer/Documents/Startup/sciagent" merge --ff-only worktree-delegation-gate
Refusing to run it — a worktree-isolated session's git operations must target
its own worktree.
```

Confirmed twice on this branch: once against the original
`git -C "$(git rev-parse --git-common-dir)/.."`, and again after `d509ba0`
rewrote it to resolve `main_tree` first. That rewrite fixed the shell quoting;
it did not change what is being refused, because the refusal is on `-C` leaving
the worktree, not on how the path was built.

**Why it matters more than it looks.** `136550a` made worktree-per-session the
default, so this is not an edge case — it is every ship. It blocks at the last
step, after the suite is green and recorded, which is the most expensive place
to discover it: the work is committed and verified, and only the ref move is
left.

**What was done instead.** `ExitWorktree` to leave isolation, then run the two
commands from the main tree. That is the sanctioned exit rather than a way
around the guard, and the skill already reserves the call for the user — so the
practical shape is that a worktree ship ends by asking, which is worth knowing
before it happens rather than at the blocked step.

**Closes off.** No in-worktree form is known. `git push . HEAD:main` and
`git branch -f main` are both expected to fail independently of the harness,
since `main` is checked out in another worktree — **not tested here**, because a
partial success would move the ref while leaving the main tree's index stale.
This waits on one of two things: the harness permitting `--ff-only` against the
common dir, or §3a being rewritten to end at `ExitWorktree` and hand the last
two commands to the user rather than issuing them.

## 2026-08-16 — infrastructure: §3a discharged, by asking rather than reaching

**Decision.** The second option in the entry above, taken. §3a is now in two
halves: the FF/DIVERGED question is answered in the worktree, and then `/ship`
**stops and asks** the user to approve `ExitWorktree` with `keep`. After that the
session's working directory *is* the shared checkout, so the merge and the push
are plain `git` with no `-C` at all. This supersedes the "waits on" clause above.

**Why.** Rewriting the path expression was never going to work: the refusal is on
`-C` leaving the worktree, not on how the path was built, which `d509ba0` already
demonstrated by fixing the quoting and changing nothing. Nor could `/ship` call
`ExitWorktree` itself — the tool's own contract reserves it for the user and it
is a no-op unless the calling session created the worktree. So the only shape
left is to ask, and the honest thing is to say up front that a worktree ship ends
by asking rather than discovering it at the blocked step.

Two things were added on the way through, both absent before. The post-exit path
now checks the shared checkout is on `main` and clean *before* merging into it —
a fast-forward rewrites that tree's files, and doing it under a live session is
the hazard worktrees were introduced to prevent. And §4's base check is now
stated to run in the tree being pushed from: it reads `HEAD` and `@{u}` of
wherever it runs, so in a worktree with an upstream it answers about the worktree
branch and then authorises a push of `main` — two refs, one plausible-looking
empty list.

**Closes off.** Worktree isolation now costs exactly one question per ship. If
the harness later permits `--ff-only` against the common dir, that question
becomes removable, but nothing else in §3a would need to change.

## 2026-08-16 — infrastructure: the CRLF drift cost seconds, not a rewrite

**Decision.** Done, not deferred. The entry "the main working tree does not obey
`.gitattributes`" left this for its own investigation on the grounds that it
"rewrites ~132 working files". That estimate was wrong in the way that matters:
**`HEAD` already stores LF.** The repository was already normalised; only the
working tree was stale. So the fix is a working-tree refresh with no commit, no
blob change and nothing to review:

```sh
git status --porcelain          # must be empty
git ls-files -z | xargs -0 rm -f
git checkout .
```

Measured: 140 paths restored, 71 of which had been CRLF. Seconds. `git status`
empty before and after; `git log` unchanged.

**Why it was worth doing now rather than later.** It was the precondition for a
mechanism already shipped. `suite-freshness.sh` shares its green record between
trees on the theory that a content hash is a global fact — but the main tree
hashed `39cf6f44…` and a worktree `91a15f02…` at the same commit and both clean,
so no worktree could ever reuse the main tree's green and every new one paid a
full suite. After the refresh both trees hash `91a15f02…`, which was already
line 1 of the record, so the main tree went FRESH with **no suite run at all**.

**Closes off.** The sharing rationale in `suite-freshness.sh` now records this
dependency, because the failure mode is silent: a tree that drifts back to CRLF
reports a permanent STALE that re-running never fixes. The check is `wc -c`
against `git show HEAD:<path> | wc -c`. What is still not claimed, exactly as
before: no numerical impact was observed and none is asserted.

## 2026-08-16 — infrastructure: two hooks on one event race, because they run in parallel

**Decision.** `ruff-after-edit.sh` and `guard-determinism.sh` are no longer two
`PostToolUse` entries. A new `after-edit.sh` captures stdin once and runs them in
order, propagating the guard's exit status.

**Why.** Claude Code runs **every hook matching an event in parallel**, and offers
no ordering mechanism — no sequence field, and array order in `settings.json`
means nothing. The documented remedy for a dependency between two hooks is to
make them one hook. The dependency here was invisible and real: ruff rewrites the
edited file while the guard walks the same tree with `path.read_text()` and
`ast.parse`. A file caught mid-rewrite raises inside the test, pytest reports a
*failing test* and exits 1 — and exit 1 is exactly the status the guard is
entitled to read as a genuine violation. It would have printed "INVARIANT 3
VIOLATED" about an edit that was fine, and the accusation would not reproduce.

The guard already distinguishes 2/3/4/5 as "the guard failing, not your code",
which is what made this worth fixing rather than tolerating: the one status it
trusts was the one the race could forge.

**Closes off.** Cost of serialising is nil — the pre-filter still exits in ~40ms
on the overwhelming majority of edits, and only an edit mentioning randomness
pays both. This is a harness behaviour, not a repository fact, so it is recorded
here: nothing in the tree would tell a later session that array order in
`settings.json` is not sequencing.

## 2026-08-16 — item 15 prerequisite: the mark-arrival cross-diagnostic works, measured

**Measured**, and this is the evidence SPEC §13 asks for before a frozen
document moves. Two runs, ~15 minutes of compute together.

**First, the current state, re-taken.** The 0.000 power figure for
`SIZE_EXCITATION` in this file's item 12 entries was measured on 2026-08-04,
*before* `core/reductions.py` moved `METRIC_VERSION` to 1.1.0 and rebuilt every
table. It had to be re-taken rather than inherited, and it holds:

```
METRIC_VERSION 1.1.0, alpha 0.05, 100 scenarios per arm
realised size (correctly specified): 0.010
power[size_mixture   ] = 1.000   min p 0.0351  median p 0.0383
power[size_excitation] = 0.000   min p 0.0508  median p 0.7972
```

**The min and the median are the new information.** Not one scenario in a
hundred crosses alpha, and the best of them reaches only 0.0508 while the median
sits at 0.7972. A p-distribution that flat is an *absence of signal*, not a
threshold that a smaller alpha would rescue. Anyone tempted to fix S11 by
loosening the check should read the median first.

**Second, the candidate.** Correlation between a mark and the inter-arrival gap
that follows it — the statistic `docs/BACKLOG.md` names. 200 replicates of 512
events per structure:

| structure | mean | sd |
|---|---|---|
| null | +0.0001 | 0.0445 |
| **hawkes** | **-0.0012** | 0.0456 |
| poisson_mixture | -0.0012 | 0.0444 |
| regime_switching | -0.0020 | 0.0392 |
| seasonality | +0.0006 | 0.0462 |
| **SIZE_EXCITATION (S11)** | **-0.1342** | 0.0308 |

Worst-case separation against the closed set is **3.42 sd**. Two variants were
tried and are worse — a Spearman rank version at 3.20 sd, and a high-versus-low
mark gap ratio at 2.42 sd — so the plain Pearson correlation is kept, which is
also the cheapest and the one already named in the backlog.

**The Hawkes row is the load-bearing one.** SPEC §4.2 calibrates S11's mechanism
to the same operating point as the four it hides among, and Hawkes is what
covers it on every arrival-only statistic. Here Hawkes sits at -0.0012,
indistinguishable from the null. The statistic separates the confounded pair
rather than merely detecting that something is clustered.

**Third, indicative power.** Threshold calibrated on the pooled closed set
(2000 draws) to a one-sided 5%, then applied to S11:

```
one-sided 5% threshold: -0.0692
realised size on closed set:   0.050
power against SIZE_EXCITATION: 0.968   (today: 0.000)
  false-positive rate[hawkes] = 0.025   <- lowest of the five
```

**0.968 is an upper bound, not a prediction, and the distinction matters.** The
real posterior predictive check bins outcomes into cells and combines across
experiments by the harmonic mean of 2026-08-04; this probe thresholds a
continuous statistic from a single execution. What it establishes is that the
information is present in the mark-arrival joint behaviour and absent from every
statistic in §4.3 — not what the check will report once the statistic is binned
and given discretisation edges. Re-measure A9 after the change; do not quote
0.968 as the detection rate.

**Closes off.** It does not decide §4.3. Adding the diagnostic is a
`MetricRegistry.version` event, so it re-addresses every experiment registered
against the catalogue and rebuilds all three cached tables — the 2026-08-15
reductions entry priced that class of change at **31m45s** for the suite against
6m44s warm. What this entry removes is the excuse of not knowing whether the
change would work. SPEC §12 criterion 4 still needs re-specifying regardless:
B1 fires on 7/12 scenarios because it holds only the null, so "at a rate at
least matching B1" is the wrong yardstick whatever the catalogue contains.

## 2026-08-16 — the call address excludes the binary version, deliberately

**Decision.** `call_address` does not cover `claude_code_version`, and should
not. The CLI version is `Completion.provenance` and `Transcript.provenance`,
recorded beside the payload and never hashed into its identity. Settled in
`be97cb0` with the reasoning in the `transcripts.py` module docstring; this
entry exists because that commit left none, and without it the question still
reads as open.

**Why it is written down at all.** The 2026-08-15 entry "the Agent SDK backend,
measured against live sessions" closes with item (3) — the binary version is
observable but `call_address` does not cover it — and says "worth deciding
before item 15 records, not after". A session reading this file in order reaches
that sentence and finds nothing after it, so the natural next move is to bump
`ADDRESS_VERSION`. That move is now wrong, and expensively so.

**Why the exclusion is right, and the argument is stronger at matrix scale than
it was at one call.** Item 15 is ~1,120 investigations under subscription rate
caps, which the same entry records as unmeasured and likely to spread the
recording across days. Claude Code auto-updates. Put the binary version in the
address and a mid-recording update silently re-addresses every subsequent call:
the corpus splits in two, and replaying the first half misses. The cost is not a
tidiness argument about identity, it is a recording run that cannot be replayed
through. **`ADDRESS_VERSION` stays at `transcript/2`.**

**Closes off.** Reproducibility of a corpus is "given a comparable binary", not
"given any binary", and that gap is real and is accepted rather than hidden —
provenance is what makes it auditable after the fact. Supersedes item (3) of the
2026-08-15 entry. Item (2) of that entry, the rate caps, is still unmeasured and
is now the only one of its three left open.

## 2026-08-16 — review: A20 is not carried by clause 3, and the backlog entry is wrong

**Approach abandoned, because the defect it targets does not exist.**
`docs/BACKLOG.md`'s entry "Relevance clause 3 fires on every pair, so A20 is not
testing what it reads as" asks for A20's cases to be given genuinely differing
scopes, on the reasoning that "A20 would still pass if clauses 1, 2, 4, 5 and 6
were all broken". They already differ, and it would not.

**Measured, by mutation rather than by reading.** Disabling the
`RelevanceClause.SCOPE_OVERLAP` arm of `verify/relevance.py::clauses` and
re-running A20:

```
17 of 102 omitted experiments went unsurfaced: ['scope_overlap/0', ...]
```

All 17 are the clause's own cases. The other 85 are surfaced by clauses 1, 2, 4,
5 and 6 independently of clause 3, which is the exact inverse of the entry's
claim. Two further A20 tests fail under the mutation and both fail *only* on
`scope_overlap` cases.

**Why the entry got it wrong, which is the part worth keeping.** It reasoned
correctly about *real slice runs* — `EvidenceIndex.from_history` does stamp every
record with the single `executor.scope()`, so on a real run clause 3 does fire
for every pair and `uncited_relevant` is every uncited experiment. It then
carried that conclusion across to A20, whose cases are **constructed** and give
each omitted record an `env_version` of `other/{variant}` precisely so the other
clauses are the ones deciding. Those constructed cases and
`test_a20_four_clauses_are_independently_sufficient` were both written at item 10
on 2026-08-04, five days *before* the entry of 2026-08-09.

**Closes off.** No change to `verify/relevance.py`, no change to A20, no change
to SPEC §7.1 — the tree was already right. The observation about real slice runs
stands on its own and is not a defect: an experiment from the same environment
version *is* relevant under §7.1, and evidence completeness being conservative is
the behaviour that clause is for. The backlog entry should be struck rather than
implemented, and this is the measurement that licenses striking it.

## 2026-08-16 — the catalogue was not the whole contradiction: BOED never selects the Stage A design

**Measured**, immediately after adding `size_gap_correlation` and taking
`METRIC_VERSION` to 1.2.0. V1 (BOED-only) on all twelve scenarios at their own
budgets, closed set entertained, shared table:

| id | budget | new design run? | final PPC p | designs actually run |
|---|---|---|---|---|
| S1 | 8 | **no** | 0.8509 | countauto, force, interarrival, phasecond |
| S5 | 8 | **no** | 1.0000 | force, interarrival, phasecond |
| S10 | 2 | **no** | 0.2629 | interarrival |
| **S11** | 8 | **no** | **0.5273** | countauto, force, interarrival, phasecond |
| S12 | 8 | **no** | 0.1006 | countauto, force, interarrival, phasecond |

Zero selections in twelve scenarios. **S11's p-value is 0.5273, which is item
12's number to four decimal places.** Adding the diagnostic changed nothing where
it matters.

**A9 says 0.520 and a real run says nothing, and both are correct.**
`ppc_outcomes` calls `observe`, which runs *every* template unconditionally. A
budgeted investigation selects, and one-step greedy BOED selects by expected
information gain **about the entertained hypothesis set**. Every closed-set
member is uncoupled and reads zero on this metric, so its expected gain is
approximately zero and it is the least attractive design on the board. BOED is
not malfunctioning; it is doing exactly what it is specified to do.

**The general statement, which is the part worth keeping.** *A design that
detects inadequacy of a hypothesis space is, by construction, uninformative
within that space.* Stage A asks a question about the space; BOED optimises
inside it. SPEC F5 hands experiment selection and inadequacy detection both to
"conventional methods" and thereby reads as though they were one interest. They
are opposed, and nothing in the loop makes the selector serve the detector.

**This predates the change, and that is how it went unnoticed.**
`size_dispersion` is also never selected on any of the twelve, for the same
reason -- no closed-set hypothesis perturbs the size component. So A9's headline
100% power against `size_mixture` is measured in a regime no investigation
enters either. The gap has been in the apparatus since item 6; adding a
cross-diagnostic did not create it, it made it load-bearing.

**What this means for the work that was just done.** SPEC §4.3's amendment and
the 1.2.0 version event are necessary and are *not* sufficient. The pilot's
3.42 sd separation and A9's 0.520 both stand as measurements of the statistic.
Neither is a measurement of detection in an investigation, and no figure from
this session should be quoted as one.

**Deliberately not decided.** Four options, and the choice is the user's because
three of them touch a frozen decision:

1. Reserve budget: every investigation runs the Stage A design once, outside
   BOED's selection. Cheapest, and it must apply to every system identically or
   it biases the §9 comparison. Touches the budget semantics, not F5.
2. Give Stage A its own experiment allocation, separate from the BOED loop.
   Same idea, stated as an architecture rather than a special case.
3. Add an adequacy term to BOED's objective. Touches F5 directly and makes
   V1 no longer the "optimal selection, no representation change" baseline §5
   defines it as.
4. Accept it and report that Stage A detection needs a design BOED will not
   choose. Honest, and leaves §9's contrast conditioning on a near-zero event
   exactly as before.

**Closes off.** Nothing is reverted -- the diagnostic, the version event and the
§4.3 amendment are prerequisites for every one of the four options, and option 4
is the only one under which they buy nothing. Whoever takes this must re-measure
detection *from runs* rather than from A9, and should fix A9's own regime while
they are there, since a power figure measured over all templates does not
describe any system SPEC §5 defines.

## 2026-08-16 — the Stage A allocation, and the two things it did not fix

**Decision.** `Scenario.stage_a` is a design the *framework* runs once before the
system sees anything: measured rather than run, so nothing is registered, no
budget is charged, and it never enters the evidence index. `run_scenario` takes
it from the scenario rather than from its own signature, so no caller can supply
it to one arm and omit it for another — an asymmetry there would bias every §9
comparison invisibly. This is option 1 of the four left open in the entry above.

**Measured, V1 on all twelve, and it is a real improvement that is not enough.**
S11's final posterior predictive p-value:

| | S11 | S12 | S10 |
|---|---|---|---|
| before the catalogue change | 0.5273 | 0.1011 | 0.2629 |
| catalogue change alone | 0.5273 | 0.1006 | 0.2629 |
| **with the Stage A allocation** | **0.2090** | 0.1166 | 0.4640 |

**2.5x on S11, and still four times alpha.** The middle row is the one to read
first: adding the diagnostic to the catalogue moved S11 by nothing at all,
because BOED never selected it. The allocation is what made the diagnostic
reachable, and the remaining gap is a third thing.

**What the remaining gap is.** The check combines per-experiment probabilities
by the harmonic mean scaled by `1 + ln(n)` (2026-08-04). With the allocation
there are nine readings, of which **one** bears on adequacy and eight were chosen
by BOED to discriminate *within* the entertained set. The informative reading is
diluted, and the penalty term grew because it arrived. That entry measured the
scaling against Fisher's method and found Fisher catastrophic here, so the
combination rule is not something to change casually: any replacement needs its
own realised-size and power pass, exactly as that one had.

**Left open deliberately, and it is now a well-posed question rather than a
mystery.** Either Stage A reads its own probe alone rather than through the
combined statistic -- defensible, since an experiment chosen to separate two
hypotheses inside a space says little about whether the space is right -- or the
combination is weighted rather than uniform. Both are changes to how the check is
*used*; neither needs a new diagnostic, and the catalogue work is a prerequisite
for both.

**A latent bug this surfaced, which is independent of all of the above.**
Enabling the allocation turned eight `tests/test_agency.py` tests red, and the
cause is not the allocation. The extra reading changes the engine's state before
the system runs, so B5's beam search scores differently and proposes a *different*
structure; filling that structure's table row raises `ExecutionError: phase bin 0
holds 1 window(s)` out of `phase_conditioned_dispersion`. Isolated by setting
`stage_a=None`, which makes the tests pass again.

So `ensure_structure` can be handed a structure whose executions are degenerate
for one catalogue diagnostic, and nothing guards it. That is reachable today by
any system proposing an unlucky structure -- B5 explores fifty per scenario --
and it is luck rather than design that no arrangement had hit it before. It must
be fixed before the allocation lands, and it should be fixed on its own terms
rather than as part of this.

**Closes off.** Nothing is committed. The catalogue change (`METRIC_VERSION`
1.2.0, the diagnostic, its edges, SPEC §4.3's amendment) is green on the suites
run against it and is a prerequisite for every remaining option. The allocation
is written and correct as far as it goes, and leaves the tree red until the
`ensure_structure` fragility above is closed.

## 2026-08-16 — Stage A reads its own probe, and S11 finally fires

**Measured**, V1 on all twelve at their own budgets, with the Stage A allocation
in place. "combined" is the check over every reading, which is what ships today.
"probe" is the same check scoped to the Stage A reading alone, via the new
`experiments=` argument to `EmpiricalTableEngine.ppc`.

| id | truth in library? | combined p | **probe p** | fires at 0.05 |
|---|---|---|---|---|
| S1 | yes | 0.9287 | 0.4924 | no |
| S2 | yes | 0.7928 | 0.0595 | no |
| S3 | yes | 1.0000 | 0.8283 | no |
| S4 | yes | 1.0000 | 0.5128 | no |
| S5 | yes | 1.0000 | 0.2494 | no |
| S6 | yes | 1.0000 | 0.5138 | no |
| S7 | yes | 0.7840 | 0.0595 | no |
| S8 | yes | 1.0000 | 0.5138 | no |
| S9 | yes | 1.0000 | 0.1832 | no |
| S10 | yes | 0.4640 | 1.0000 | no |
| **S11** | **no** | 0.2090 | **0.0112** | **YES** |
| S12 | yes | 0.1166 | 0.5083 | no |

**One scenario fires and it is the right one.** Eleven in-library scenarios stay
quiet, nearest miss 0.0595. Against a starting point where S11's check read
0.5273 and detection over twelve scenarios was zero, this is the discrimination
the slice was missing.

**Why scoping is not a trick to make the number smaller.** The combination rule
scales the harmonic mean by `1 + ln(n)`, so nine readings dilute one. Eight of
those nine were selected by BOED to separate hypotheses *inside* the entertained
set, and an experiment chosen for that says close to nothing about whether the
set is the right one. The probe exists for the other question. The full-record
check is still the honest summary of fit and is what `ScenarioRun.ppc` keeps
reporting; what changes is which check opens SPEC F6's gate.

**Stated plainly, because it bears on how this should be read.** This rule was
chosen after measuring that the alternative fell short, which is the ordering
that should make a reader suspicious. Two things stand against that. It was named
as the preferred option *before* 0.2090 was measured, in the same session, on the
argument above. And it is falsifiable in the direction that would have embarrassed
it: had the probe fired on several in-library scenarios it would have been a worse
rule than the one it replaced, and the table above is where that would have shown.
It did not.

**It also relocates R2 rather than killing it, which the previous entry feared.**
Item 13's ablation runs on S8, S11 and S12, and the 2026-08-11 entry recorded
that only S12 ever reached the proposal layer. Under the combined check with the
allocation, S12's gate closed and R2 had no cell left anywhere. Under the probe,
**S11's gate opens**, so V3 and V4 both have their briefs rendered on the
out-of-library scenario the architecture exists for. That is a better cell than
S12 ever was: S12's proposals were driven by a censoring nuisance over a truth
that is *in* the closed set, which is a false positive that happened to be useful.

**Left to do, and it is threading rather than discovery.** The measurement above
computes the probe check post hoc. Nothing yet *uses* it: `Hybrid`'s half-budget
gate, `PPCOnly` and the campaign all still call `ppc()` unscoped. Threading it
needs the Stage A experiment id to reach the systems, which wants a method on
`Investigation` rather than an id every system reconstructs -- SPEC's second
invariant argues for the framework owning it. Until that lands, no system's
behaviour has changed and the two red tests in `tests/test_agency.py` still
describe V7's old S12 behaviour.

**Closes off.** `ppc(experiments=...)` raises `UnknownExperimentError` on a name
never recorded rather than checking fewer readings than asked, so a scoped check
cannot silently become a narrower one. SPEC §12 criterion 4 should be
re-specified against the table above -- power against S11 versus false positives
on S1-S10 and S12 -- and not against B1, which fires on 7/12 for reasons that are
about holding only the null.

## 2026-08-16 — the gate is threaded, and V7 reaches Stage B on S11 for the first time

**Measured**, V7 and B1 on all twelve at their own budgets, scripted provider,
shared calibrated table, with the scoped gate live. ``asked`` is how many times
the proposal layer was consulted.

| id | V7 asked | V7 correct | | id | V7 asked | V7 correct |
|---|---|---|---|---|---|---|
| S1 | 0 | yes | | S7 | 0 | yes |
| S2 | 0 | yes | | S8 | 0 | no |
| S3 | 0 | yes | | S9 | 0 | yes |
| S4 | 0 | yes | | S10 | 0 | no |
| S5 | 0 | yes | | **S11** | **2** | no |
| S6 | 0 | yes | | S12 | 0 | yes |

**Two proposals on S11 and none anywhere else.** Item 12 measured the exact
inverse -- S10 and S12 asked twice each, S11 not at all -- and recorded that
SPEC §9's preregistered contrast "conditions on an event that occurs zero times
in twelve". It now occurs on exactly the scenario it was written for. **The
primary contrast is runnable.**

**V7 is still correct on 9/12**, the same nine as before, so nothing in-library
was traded away for it. S11 stays "no" and must: its truth is outside the agent
grammar, so exact recovery is impossible by construction, and D3 rather than
correctness is what SPEC §8 says to score it on.

**How it is threaded, and why it is two files rather than five.**
``Investigation.ppc`` is the single system-facing entry point -- ``Hybrid``'s
gate, ``PPCOnly`` and ``llm/encoding`` all reach the check through it -- so
scoping it there reaches every system at once. ``Scenario.stage_a`` names the
design, ``campaign.stage_a_id`` names the reading, and ``run_scenario`` passes
that id to the investigation. ``ScenarioRun.ppc`` deliberately still calls
``engine.ppc()`` unscoped: the full-record check remains the honest summary of
how well the entertained set explains everything seen, and only the check a
system *acts on* is scoped.

``stage_a_id`` exists as a function because the reading is written in one module
and looked up in another. Two copies of the f-string would diverge silently --
``ppc(experiments=...)`` raises on an unknown name, but only after the write and
the read had already disagreed.

**Five tests moved from S12 to S11, and the move is the finding rather than an
adjustment.** ``tests/test_hybrid.py``'s gating and transcript tests and
``tests/test_agency.py``'s ``EXTENDING_SCENARIO`` all needed a scenario where the
layer is actually consulted. That was S12, whose truth is *in* the closed set;
the gate opened there because the censoring nuisance fooled a check holding no
reading that bore on adequacy. It is S11 now. Nothing was weakened to make them
pass -- they assert the same properties on a scenario where the property is the
one being tested, and `test_agency` needed a one-line change because it already
named the extending scenario exactly once.

**What this does to item 13, which the entry before last feared was dead.** R2's
ablation runs on S8, S11 and S12, and the 2026-08-11 entry recorded S12 as the
only cell ever reaching the proposal layer. It is now S11 -- a strictly better
cell, since V3 and V4 diverge there on the out-of-library scenario the memory
question is actually interesting for. R2 was never measurable on a cell that
mattered before today.

**Still not done, and none of it is discovery.** SPEC §12 criterion 4 wants
re-specifying against the numbers above rather than against B1, which fires on
nearly everything for reasons about holding only the null -- its full-record
p-values here run 0.042 to 0.123 on the eleven non-null scenarios and 1.0000 on
S9. A9 still measures power in the every-template regime that no budgeted run
enters, and should be re-measured against the probe. And every V7 figure in this
entry is a **scripted** provider: this is the apparatus working, not a
measurement of what a model proposes.

## 2026-08-16 — R7 revised: Hawkes was never interventionally equivalent to S11

**Measured**, every closed-set structure scored against S11's out-of-library
truth, held-out battery of the two mark diagnostics and the forced-arrival
intervention. This supersedes the table in "item 12: D1-D6, and the measurement
that shows why §8 forbids collapsing them" (2026-08-04), which was taken over a
battery of two designs that could not see the mark-arrival coupling.

| candidate | D1 | D2 | **D3 (2026-08-04)** | **D3 now** | D6 |
|---|---|---|---|---|---|
| *the truth itself* | 0.00 | -1.615 | 1.000 | 1.000 | 24.0 |
| **hawkes** | 1.50 | -4.516 | **0.960** | **0.697** | 24.0 |
| regime_switching | 1.50 | -6.978 | 0.698 | 0.527 | 29.0 |
| poisson_mixture | 1.50 | -6.891 | 0.677 | 0.518 | 24.6 |
| seasonality | 1.50 | -6.950 | 0.629 | 0.483 | 23.0 |
| null | **1.00** | -6.992 | 0.617 | 0.478 | 1.0 |

**R7 survives in its weak form and dies in its strong one.** The 2026-08-04
entry read Hawkes at 0.960 against a truth of 1.000 and described it as
reproducing S11's interventional response "almost exactly" -- interventionally
*equivalent* while structurally wrong. At 0.697 against 1.000 it is plainly
distinguishable from the truth. Hawkes was never equivalent; the battery could
not see the one dimension on which the two genuinely differ, which is the
mark-arrival coupling that S11's mechanism is built out of.

**What still holds, and it is the part that matters for §8.** Hawkes remains the
closest closed-set candidate by a clear margin -- 0.697 against a best rival of
0.527 -- while sitting at D1 1.50, *further* from the truth than the null's 1.00.
So a candidate that reproduces the truth's interventional behaviour better than
anything else available still scores worse structurally than proposing nothing.
That disagreement between two dimensions is what §8's prohibition on collapsing
them exists for, and no number above weakens it.

**How the battery changed, which is a defect worth naming separately.**
`HELD_OUT` in `tests/test_scoring.py` was `slice_designs()[3:]` -- a *positional*
slice. When SPEC §4.3 gained `size_gap_correlation` the slice silently grew from
two designs to three, and a reported number moved with nothing in the diff to say
so. It now names its designs. Membership of a battery that D2 and D3 are defined
over cannot be a consequence of where a design happens to sort.

**The design was kept in the battery, deliberately, and the reasoning should be
inspectable because the incentive ran the other way.** Dropping it would have
restored the 0.960 and preserved a finding this repository had already published
to itself. It was kept because the battery's own stated rationale demands it: the
comment on `HELD_OUT` says the mark diagnostic is there so that "a candidate that
got the arrival side right and the mark side wrong should not score a clean 1",
and `size_gap_correlation` is the sharpest available instance of exactly that
case. Excluding it would have meant declining to measure the thing that tests the
claim.

**Read this against the entry recording the catalogue change.** The two are the
same fact from opposite ends. A catalogue blind to the mark-arrival coupling
could not detect S11 (power 0.000) *and* could not tell Hawkes from S11's truth
(D3 0.960). One diagnostic moved both, and it should be no surprise that it did:
they were the same blindness.

**Closes off.** Item 12's D1-D6 table is superseded for D2 and D3; its D1 and D6
columns are unaffected, since neither depends on the battery. Any figure quoting
Hawkes at 0.960 on S11 is now stale, including the prose in that entry, which is
left unedited per this file's append-only rule. `test_scoring.py` asserts both
directions now -- that Hawkes still leads by 0.15, and that it is *below* 0.8 --
with the second assertion naming `HELD_OUT` so that a future battery change which
restores the artefact says so in its failure message.

## 2026-08-16 — the Stage A probe was evidence, and only B5 noticed

**The defect.** `_run_stage_a` recorded the adequacy probe through
`engine.record`, which appends to the same list `log_likelihood_total` folds
over. So the reading entered *every system's posterior*: an extra observation,
charged to no budget, absent from the run's evidence index, silently reweighting
the belief the run is scored on. Its docstring claimed "a system can neither see
that it happened nor cite it", and both halves were false — `engine.observations`
is public, and B5 ranks its beam on it.

Fixed by giving the engine a second compartment. `record_probe` writes there;
`observations`, `log_likelihood_total` and `_supporting` cannot see it; `ppc`
reaches it only when a caller names the id. Unscoped, `ppc()` is over experiments
alone, which is what makes `ScenarioRun.ppc` reconstructible from
`ScenarioRun.evidence` — a summary p-value folding in a reading absent from the
index is one nobody can check.

**Why it hid in the posterior and not in the beam.** Measured on S1, four
experiments in, closed set entertained, recording the probe the old way:

| | |
|---|---|
| largest posterior move | **0.004** |
| the term it adds to each candidate's fit | -1.79 to -1.66 nats |
| spread of that term across five structures | **0.129 nats** |

A posterior normalises, so a term the live hypotheses agree about cancels almost
exactly — five structures agreeing to within 0.13 nats move the posterior by
0.004, which no test would ever catch. B5 ranks on the *unnormalised* sum, over
the agent grammar's ~18M candidates rather than five, where nothing constrains
that term to be shared. 0.129 nats is a floor observed on the closed set, not a
measurement of the beam's spread; the beam's own spread was not measured, and the
recovery figures below are the evidence that it is much larger.

**The cost, and the only thing that reported it.**

| | before the probe | probe as evidence | probe compartmented |
|---|---|---|---|
| B5 right structural cell, of 9 | 3 | **1** | **4** |
| B5 mean distance | 0.775 | — | 0.812 |
| B1 full-record p on S1 | 0.0489 | 0.0569 | 0.0489 |

Nothing else in the suite moved. B1's two detection tests failed, but for the
second-order reason that the probe diluted their full-record check — a symptom
that looked like a threshold to adjust. The structural regression was the only
signal pointing at the cause, and it pointed the wrong way round, which is what
made it worth chasing: an observation on the mark-arrival axis should *help* a
search whose grammar contains size-coupling structures. It hurt because the
search was being scored on a reading it was never meant to see.

B5 ends at 4 of 9 rather than the 3 it started from. The extra cell is not this
fix: the propose loop now falls through to the next beam member when the top one
turns out unmeasurable at the engine's replicate count, where it used to propose
nothing at all.

**A regression guard exists now, which it did not before.**
`TestAProbeIsNotEvidence` in `tests/test_systems.py` asserts the probe moves no
posterior mass, is absent from `observations`, is absent from the unscoped check,
is reachable when named, and cannot shadow an experiment id. A future `record`
where `record_probe` belongs fails by name rather than moving a baseline's score
for reasons nobody can see.

**`ScenarioRun` now carries both checks.** `ppc` is the full record; `adequacy`
is the verdict the system itself acted on, taken from `Investigation.ppc()` so no
second copy of the scoping rule can drift. Both are needed because SPEC §12
criterion 4 compares an LLM's detection against B1's, and B1 holds no proposal
layer to gate — its detection can only be read off a run. Reading one arm's gate
against the other arm's full-record check is not a comparison.

That distinction immediately pays, on the twelve-scenario table re-measured after
the fix. On the **full-record** check B1 fires on 5 of 12 (S1, S5, S8, S10, S11)
and V7 on none. On the **adequacy** check both fire on S11 alone — B1 at 0.0294,
V7 at 0.0112. B1's apparent detection advantage is a multiplicity artefact of
holding only the null across eight experiments, not a finding about adequacy, and
§12 criterion 4 as written would have credited it.

**Unchanged by the fix**, which is the point: V7 still asks the proposal layer
twice on S11 and zero times on the other eleven, still fires the gate on S11
alone, still scores correct on 9 of 12. Everything the Stage A work bought
survived the correction.

**Closes off.** The general rule this settles: a reading about whether the
entertained set is *adequate* must not also redistribute mass *within* it, or the
adequacy verdict is partly a consequence of the belief it exists to audit. Any
future harness-side measurement — a calibration reading, a second-stage probe —
goes through `record_probe` or states why it is evidence. Still open: §12
criterion 4 wants re-specifying against the adequacy column above, and A9 still
reports power in the every-template regime no budgeted run enters.

## 2026-08-16 — A9 re-measured against the probe: the Stage A gate is directional

**Measured**, 100 scenarios per arm, closed set entertained, alpha 0.05. A9 had
only ever reported power over *every template at once* — a regime no budgeted run
enters, and since the gate was scoped, not the regime any system acts on either.
Both are now reported.

| misspecification | all templates | probe only |
|---|---|---|
| `size_excitation` (S11's own mechanism) | 52% | **90%** |
| `size_mixture` (a component no closed-set hypothesis touches) | 100% | **3%** |
| correctly specified (size, must stay under 2α) | 0% | 0% |

**Neither regime dominates, and that is the finding.** Against the mechanism the
probe was built for, scoping nearly doubles power: the combination rule scales
the harmonic mean by `1 + ln(n)`, so eight readings silent about the mark-arrival
coupling dilute the one reading that carries it. Against a size-distribution
mixture the probe does not measure, scoping costs almost everything — 100% to 3%.

**So the number SPEC §6.2 says the LLM must not be credited with is 90%, not
52%**, and it is 90% only in one direction. The earlier figure understated the
gate on its own mechanism by nearly half while overstating it everywhere else.

**What this explains, which was otherwise loose.** V7's adequacy check fires on
S11 alone across the twelve — including *not* on S8, whose compound truth is
genuinely outside its space and which B1's full-record check does flag at 0.0396.
That is not a bug in the gate; S8's misspecification is a size-distribution
mixture, and the third row above says the probe has 3% power against exactly
that. A single directional probe buys S11 and pays for it on S8.

**Both sizes stay at 0%**, well inside the 2α bound, so the scoped check is not
trading size for power — it is trading breadth for depth. The false-positive
assertion is now parameterised over both arms, because the scoped one is what
gates every proposal an investigation makes and a bound holding only over the
whole catalogue would bound nothing anyone acts on.

**A floor rather than a pin.** The assertion is `> 0.8` against a measured 0.90.
It sits above the 52% the whole catalogue manages, which is the comparison
carrying the content, and leaves room for Monte Carlo noise at n=100. The
all-templates arm keeps its own assertion on `size_mixture`, so a catalogue that
stopped covering what the probe misses fails here rather than in a campaign.

**Closes off.** SPEC §12 criterion 4 was re-specified against these numbers in
the same session and now reads as power against size — detection on S11 at a
strictly higher rate than firing where the space contains the truth, on the Stage
A check, with B1 reported alongside rather than used as the bar. What is *not*
settled is whether one probe is enough: `BACKLOG.md` carries the question of a
Stage A battery, which would touch §4.6 and is not a change to make on the way
past. Until then, every Stage A detection figure in this repository is a
statement about the mark-arrival direction and should be read as one.

## 2026-08-16 — the review caught an invariant 6 violation on the way out, and it was right

**Withdrawn before shipping**, on findings from `/code-review` and the
`invariant-auditor` lens 6 run during `/ship`. Two things written earlier today
are corrected here rather than edited, per this file's append-only rule. **The
entries above that describe them are stale in exactly these respects**, and no
figure in them moved — only what was shipped.

**1. The SPEC §12 criterion 4 rewrite is reverted.** The entry
"A9 re-measured against the probe" closes by saying criterion 4 "was
re-specified against these numbers in the same session". That sentence describes
the violation accurately enough to have been the evidence for it. An exit
criterion that grades V7, rewritten in the same sitting that measured V7 against
the gate the new wording names, is the confound CLAUDE.md's invariant 6 survives
to prevent — and it does not matter that the old wording was genuinely
incoherent, that the replacement was stricter, or that the rewrite was asked
for. The ordering is the violation.

Criterion 4 is back to its frozen text. The argument for changing it, the three
distinct ways the old wording fails, and the measurements are now in
`BACKLOG.md`, which is where SPEC §13 says a change to a frozen decision goes.
It is explicitly marked as not this session's decision to take.

**2. `ScenarioRun.adequacy` is withdrawn.** Added a few hours earlier so B1's
detection and V7's gate could be read on the same check. The review found it
evaluated after `investigate` returns, so it reads the final posterior rather
than the one the system gated on — V7 on S11 acted on p=0.0128 and the field
recorded 0.0112. The consequence is worse than the discrepancy: a system that
*successfully* proposes a structure explaining the probe ends with
`inadequate == False`, so a correct detection records as a miss, inverting the
criterion the field existed to serve.

Chasing that down surfaced the real defect, which is that the field conflates two
quantities: whether *the space* is adequate for a scenario, which is
arm-symmetric and computable by the harness before any system runs, and whether
*a system* detected that it was not, which B1 cannot have an answer to because it
never calls the check. Both are open in `BACKLOG.md`. With criterion 4 reverted
the field has no consumer, and a field whose correct semantics wait on an unmade
decision is worse than no field.

**3. A9's new assertion is withdrawn; its new measurement is kept.** The
two-regime power table stands and is the session's real finding. What came out is
the threshold placed on `size_excitation/probe` — S11's own mechanism, and
therefore the arm V7 is graded against. The test's own docstring already said
"the control arm is asserted and the hard arm is only recorded", and adding a bar
to the hard arm after measuring it inverted that discipline. The control arm's
assertion is unchanged; both regimes are reported and bounded.

**Three real defects the same review found, which were fixed rather than
withdrawn.** `EmpiricalTable.with_structure` computed `cell_of` outside its
guard, so `OutOfRangeError` escaped unwrapped while `beam_search` documented the
boundary as wrapping both and caught only the wrapped type — latent (108
wrapped, 0 out-of-range across the twelve) but it would have aborted a search
rather than costing one candidate its rank. `BeamSearch` reported the admitted
hypothesis as a residual candidate once the propose loop could fall past rank 0,
and `_audit` excludes that field, so it would have been wrong in the report and
silent everywhere else. And the Stage A design is in `slice_designs()`, so B1,
B4 and B5 rotate onto it and spend budget there while V1 and V7 select by
information gain and can decline — left as it is, flagged, and it belongs with
the criterion 4 question rather than being settled on the way past.

**Closes off.** What ships is the probe-compartment fix and the catalogue
amendment, neither of which is an acceptance bar. The §4.3 amendment stays: it
rests on a demonstrated §13 contradiction — 0.000 power made §4.6 requirement 1
unsatisfiable — and it moves every arm alike, B1 detecting S11 at 0.0294 on the
same instrument V7 reads at 0.0112. That is a different kind of change from a
criterion, and the distinction is the one worth keeping: **fix the instrument
when it cannot measure; do not touch the bar in the session that reads it.**

The general lesson is cheaper stated than learned. The author of a change is the
worst judge of whether it was written to fit what he had just measured, because
the argument for it is genuinely good either way — every finding above came with
a defensible rationale, and three of them were still wrong to ship.

## 2026-08-16 — infrastructure: what the recall fan-out actually recovers, and one finding it did not survive

**A measured number that is expensive to reproduce.** `/recall` gained a fan-out
branch for corpus-shaped questions: four `decisions-sweeper` agents over four
contiguous slices of this file, launched in one message. Probed against the
question *"everything about how long things take and about resource contention"*:

| method | entries returned |
|---|---|
| header grep, eight search terms | 4, of which **2 are false positives** on the word "second" |
| four-slice sweep | **24** |

The gap is not a matter of degree. Four of the five entries slice 2 returned are
titled `item N: what is left open` — a title that matches no topical grep in any
wording — and between them they hold the entire suite timing history, 23.5s at
item 0 through 2m50s at item 9. The header grep returns none of them. The
30m37s contention figure and the 3m11s/1.055s cache figures, both cited in
`CLAUDE.md` as settled facts, are likewise invisible to it.

Reproducing this costs four sonnet passes over 226KB, so it is recorded rather
than re-derived. The trigger written into the skill is deliberately narrow —
corpus-shaped question, or a §2 grep returning more than about eight headers —
because the narrow path remains right for "was X decided" and is the common case.

**An approach abandoned: `decisions-sweeper` could not be probed as itself.** The
agent registry is read at session start, so a newly written agent file does not
resolve in the session that writes it; all four launches were rejected against
the registry as it stood at startup. The probe above therefore ran
`general-purpose` with the sweeper's contract inlined, which tests the design and
not the registration. **Waiting on:** a session restart to confirm the agent
resolves, and to settle a question this session could not — whether a worktree
session reads `.claude/agents/` from its own tree or from the shared checkout.
`invariant-auditor` exists in both, so it does not discriminate. If the latter,
the sweeper is unusable until this branch reaches `main`.

**A finding raised by the audit and refuted, recorded so it is not raised again.**
The four-lens audit's lens 6 reported `src/sciagent/eval/agency.py` as reopening
the invariant-6 confound: a metric module added at `0a9afd5` (2026-08-13), two
days after the ablation at `200d218` and nine after the LLM layer at `a380a21`,
therefore evaluation apparatus younger than the systems it grades. The dates are
exactly right. The charge is not:

```
$ git log --format='%h %ad' --date=iso -S'| 14 | Agency metrics' -- docs/SPEC.md
7f69717 2026-08-01 20:47:28 +0100      # Initialise repo
```

`agency.py` is named in SPEC §11 row 14 and in the §14 module layout **from the
initial commit** — three days before any system existed and twelve before it was
implemented. The invariant's reason asks whether apparatus was *conceived* after
the thing it grades; here the specification predates everything and the frozen
backlog is what put the implementation at item 14. The audit further read the
2026-08-13 entry's V1/B1 passage as evidence the boundary was tuned against
observed scores. It is not: that passage argues from `entertain` routing every
library structure through `Investigation.propose` — a property of the API — and
from V1's SPEC §5 *definition* as never extending its hypothesis space. Both are
deductive, neither is a measurement.

**Closes off.** The ordering fact is real and any future lens-6 sweep will find
it again, which is why this is here rather than left to be re-litigated. What
would genuinely violate the reason clause is a gate or metric with no SPEC row
predating the system it scores; that test, not the commit dates alone, is what
lens 6 should apply. Nothing here changes the audit's standing — it also
returned three correctly clean lenses, and being wrong about a real ordering fact
in the conservative direction is the failure mode that costs least.

## 2026-08-16 — the shared cache's reader was never guarded, and four workers found it

**Found by breaking it.** `pytest-xdist` at `-n 4` failed a suite run with
`PermissionError: [Errno 13]` reading `.cache/tables/gate-2000-...json`, inside
`EmpiricalTable.load` at `empirical.py:499`. Not an ordering defect and not a
test bug: the shared table cache's **read** path had no guard at all.

**Why it was missed, which is the useful part.** The write half is elaborately
hardened — `save` writes a per-pid temporary and retries `os.replace` eight
times on `PermissionError`, and `tests/test_empirical_io.py` covers that
mechanism deterministically by patching `os.replace`. Its docstring concluded
from the out-of-band probe — two writers, two readers, 2000 clean reads —
*"Readers are never wrong; writers merely have to wait."* The second clause is
still true. The first was an extrapolation from two readers, and it fails at the
process count a `-n 4` suite reaches: Windows refuses an `open` for the window in
which `os.replace` holds the destination, and with enough readers somebody lands
in it. `load` read with a bare `path.read_text` on the strength of that sentence.

**Measured, both arms of the same harness**, four writers against six readers and
six against eight, differing only in the reader's call:

| reader | writers/readers | reads | failures |
|---|---|---|---|
| `path.read_text` (before) | 4/6 | 900 | **4** |
| `path.read_text` (before) | 6/8 | 1200 | **1** |
| `_read_text_contended` (after) | 4/6 | 900 | 0 |
| `_read_text_contended` (after) | 6/8 | 1200 | 0 |

**Decision.** `_read_text_contended` mirrors `save`'s retry on the read side,
using the same `_REPLACE_ATTEMPTS`/`_REPLACE_BACKOFF_S`, raising `TableError`
rather than letting a bare `PermissionError` reach a caller who would read it as
a missing file. `load` goes through it. Backoff is fixed, not jittered, for the
reason it is in `save`: nothing here may consult a random source (invariant 3),
and the wait affects timing only, never bytes. Three tests follow the file's
established pattern — retry, typed give-up, and one asserting `load` still routes
through the guard, which is the test that fails if the bypass ever returns.

**This was latent without xdist.** Worktrees deliberately share one
`.cache/tables`, so concurrent sessions are the same race with fewer processes.
xdist did not create it; it made it reproducible, which the 2026-08-16 entry on
the review step had recorded as impractical — *"a real two-process race is slow
and intermittent"*. It cost five parallel suite runs to see once, and about
twenty seconds to see on demand afterwards.

**Closes off.** `save`'s docstring no longer asserts readers cannot fail; it
records what the old probe did and did not establish. Any future reader of a
cached artefact should go through the same door.

**What this is *not*, since the shapes invite confusion.** `_publish` and
`_read_text_contended` are both single doors, and that much is the same lesson —
protection applied to some callers and forgotten at others is what hid this bug
and what `_publish` was introduced to prevent. Their *behaviour on failure is
deliberately opposite*, and neither is the other's equivalent. A failed write is
survivable because the computation succeeded and the table is in hand: `_publish`
swallows a `TableError` and returns it. A failed read has nothing to return, so
`_read_text_contended` raises, and the three `load` sites in `tests/slice_tables.py`
deliberately have no handler. An exhausted read means the file is held by
something that is not going away in 1.4 seconds, and continuing from that would
mean inventing a table.

## 2026-08-16 — infrastructure: pytest-xdist adopted at -n 4, and what the contention rule actually is

**Decision.** `pytest-xdist` is in the dev group and the suite runs at
**`-n 4 --dist loadfile`**. This closes the item left open on 2026-08-15, whose
stated condition was *"a parallel run whose results are byte-identical to the
serial one, not a wall-clock improvement."* That condition is met and was checked
as stated: junit outcome sets diffed per test id, 1031 tests, `1024 passed, 7
skipped` identically at `-n 4`, `-n 6` and serial, across four separate parallel
runs.

**`--dist loadfile` was added late, and the run that justified it nearly did not
happen.** With the default `load`, wall-clock ranged **135.88s to 282.96s** across
runs of the same tree — which looked like scheduling noise and was not. Tests in
a module share simulated rows through the gate table, so splitting a module
across workers makes each worker re-simulate at 2000 replicates what a sibling
already built. Per test, serial against default `load`:

| test | serial | `-n 4` |
|---|---|---|
| `test_every_in_library_scenario_is_identifiable[S5]` | **0.00s** | **120.07s** |
| `test_the_floor_never_exceeds_what_greedy_achieves[S3]` | **0.00s** | **118.14s** |
| `test_the_floor_never_exceeds_what_greedy_achieves[S1]` | 89.74s | 118.62s |

A test that costs nothing serially costing two minutes in parallel is duplicated
work, not contention, and it explains what had looked like an unrelated puzzle:
why `-n 6` (253.70s) and `-n auto` (279.16s) came out *slower* rather than merely
no faster. More workers split more modules. `--dist loadfile` keeps a module on
one worker and the duplication disappears: **151.30s**, with only one oracle test
left in the slowest eight where plain `load` showed four.

The general lesson is worth more than the flag. A wall-clock range that looks
like noise is worth one `--durations` diff against serial before it is written
off, because the shape that produced it here — a suite whose expensive work is
cached *within* a process — is invisible in a total and obvious per test.

**Measured** on the developer desktop, 12 logical processors, 16 GB, warm tables:

| invocation | wall clock | outcome |
|---|---|---|
| serial | **262.44s** | 1024 passed, 7 skipped |
| `-n 4` | **159.37s / 166.52s** | identical result sets |
| `-n 6` | **253.70s** | identical, but no better than serial |
| `-n auto` (12) | **279.16s** | **3 failed** — `MemoryError` ×3 |

**The prediction was right about the mechanism and wrong about the bound.** The
2026-08-15 entry reasoned that `-n auto` "cannot finish faster than its slowest
single test, so the floor is ~2m30s… still a threefold win". The floor argument
holds, but the binding constraint is **memory, not cores**: twelve workers on
16 GB die inside `np.fft.rfft`, and six are slow enough to be pointless. The real
figure is **1.6x**, not 3x. `-n 4` is also what `-n auto` resolves to on the
4 vCPU cloud VM, so one flag is right on both surfaces.

**Not in `addopts`, deliberately.** Worker startup takes a single file from
**1.76s to 3.41s**, and the project's own guidance is to run one file while
iterating. The flag lives in the full-suite invocation and in `suite-runner`.

**What the contention rule turns out to be.** Four read-only `decisions-sweeper`
agents alongside a `-n 4` run: **183.25s and 185.37s** against ~163s idle, about
**12–15%**, while alive for only ~38s of the run. So the operational rule both
skills already state — do not run agents alongside the suite — is confirmed, and
the premise offered for it, *"read-only and do no CPU work, so they cost
nothing"*, is not. The **30m37s** figure was never a local measurement: it comes
from `1c01fb1`'s commit message, taken on the 4 vCPU cloud VM against a **7m50s**
baseline on that same VM, and it appears nowhere in this file — a four-slice
sweep over all 3900 lines confirms it. `CLAUDE.md` had paired it with the
desktop's 6m30s idle figure from six days later, splicing two machines into a
4.7x that neither measured.

**The suite is also faster than recorded for an unrelated reason.** 262.44s
serial for 1024 tests, against 390.96s for 980 on 2026-08-15 — `35cf96a`'s
optimisation pass, which took the slowest test from 146.88s to 89.74s.

**Closes off.** Every wall-clock figure in this file before today is serial, and
the two are not comparable — this is the hazard the 2026-08-16 performance entry
raised when it declined to parallelise the replicate loop. The mitigation is that
`suite-runner` reports its invocation beside its timing, and `CLAUDE.md` now asks
the same of anyone recording one. Note the distinction that entry turns on:
parallelising `with_structure` changes the execution model on the path invariant
3 protects, and still wants its own gate. xdist distributes whole tests across
processes, each of which runs the simulation exactly as before, so it does not.

**Left open, both raised by the pre-commit review and both costs rather than
defects.** Neither is fixed here, because both want a decision rather than a
reflex at ship time.

1. **`save_gate_table` is a read-modify-write, and `-n 4` makes losing one
   routine.** `gate_table()` loads the shared file, callers grow it, and
   `save_gate_table` publishes the whole table, so two workers that both load,
   both add a row and both save leave only the later one's row. Correctness is
   untouched — the file's own comment establishes that storing a superset is safe
   because a row is a pure function of `(defect, template, seed)` — but a lost
   row is re-simulated at 2000 replicates, which the same comment calls the
   single most expensive thing in the suite. Serially all growth happens in one
   process and nothing is lost; across four workers it is a per-run event rather
   than the rare cross-session one it was. **This is not the marginal cost the
   first draft of this entry claimed** — under default `load` it was the
   dominant one, worth over 230 seconds across two tests, and `--dist loadfile`
   contains it only by keeping a module's tests together. It is contained, not
   fixed: two *modules* that grow the same rows still land on different workers.
   A merge-on-write in `_publish` for this one file is the obvious fix and has
   its own narrower race.
2. **A cold cache under four workers is not merely four times slow.**
   `slice_table()` is `lru_cache`d per process, so after a version bump up to
   four workers each build the full table concurrently — 3m11s apiece, in
   parallel, on a machine where `-n auto` already exhausted 16 GB with the tables
   *warm*. Warm the cache before a parallel run following any version bump, or
   run that first suite serially.

**One new exposure the adoption creates, checked and clean but worth naming.**
xdist workers are separate processes, so a single suite run now holds **four
`PYTHONHASHSEED` values at once**, where a serial run held one for its whole
duration. That is a genuinely new shape for invariant 3's dict/set-ordering
clause, not merely more of an old one. The lens-3 audit sampled the sites most
plausibly on a path from an unsorted `set`/`.items()` to a stored or printed
value — `verify/relevance.py`, `verify/verdict.py`, `systems/llm/transcripts.py`,
`systems/llm/agent_sdk_provider.py` — and found each either membership-only or
sorted before use, with `agent_sdk_provider.py` documenting the choice for this
exact reason. The existing discipline anticipates multi-process hash seeds; it
simply had not met xdist. The sweep was sampled rather than exhaustive across
roughly ninety sites, so this is "no counterexample found", not a proof.

## 2026-08-16 — infrastructure: a worktree reads its agents from the shared checkout

**Settled, and it is the unfavourable answer.** The 2026-08-16 entry on the
recall fan-out left this waiting: *"whether a worktree session reads
`.claude/agents/` from its own tree or from the shared checkout.
`invariant-auditor` exists in both, so it does not discriminate."* It reads from
the **shared checkout**. An agent written in a worktree cannot be used from that
worktree's session; it becomes available when its branch reaches `main`.

**What discriminated it.** `suite-runner` was written in a worktree and exists
nowhere else — launching it returned `Agent type 'suite-runner' not found`, with
`decisions-sweeper` listed as available in the same message. `decisions-sweeper`
is committed and present in both trees, which is exactly why it could not settle
this on its own.

**The other candidate cause is refuted, not merely set aside.** That entry also
recorded *"the agent registry is read at session start, so a newly written agent
file does not resolve in the session that writes it"*. The registry does refresh
mid-session: `decisions-sweeper` was absent from this session's agent list at
startup and was announced to it later, when `b3b01a5` landed on `main` — before
the worktree existed. So a session restart is not the remedy and never was; the
merge is. What remains true is the practical advice, for a different reason than
the one given.

**Closes off.** `recall/SKILL.md`'s fallback stands and is now the documented
route for any agent authored in a worktree: inline the contract into
`general-purpose` at `sonnet` and say which form was used. `suite-runner` carries
the same note. This session used that fallback for its own verification run,
which is the honest form of the precedent — the agent whose definition it added
could not run it.

## 2026-08-16 — invariant 2 is violated: a system can write its own posterior

**Found by the pre-commit audit, on a branch that did not cause it.** `/ship`'s
lens 2 reported it against unrelated work, and it is recorded here rather than
fixed in that branch because the fix is its own piece of work. It is the most
serious thing in this file.

**The violation.** `systems/base.py:141-143` exposes the live posterior engine:

```python
@property
def engine(self) -> EmpiricalTableEngine:
    """Return the posterior engine. Read-only in effect: it writes the numbers."""
    return self._engine
```

"Read-only in effect" is a **docstring**, and invariant 2 says in terms: enforce
with runtime assertions, not comments. `EmpiricalTableEngine.record`
(`inference/empirical.py:716-743`) is public and takes an arbitrary
`result: DiagnosticVector`. Its two guards check that the experiment id is fresh
and that the template matches the table. **Neither constrains the numbers.** The
posterior is a pure function of recorded observations (`log_likelihood_total`),
so a system that writes observations writes its own posterior — and therefore its
own score.

**Demonstrated by execution, not by reading.** A system that calls
`investigation.engine.record(...)` with results it chose and then concludes
honestly:

| system | abstain | leading | log score | experiments charged |
|---|---|---|---|---|
| honest | 0.000216 | 0.999784 | **-12.2043** | 1 |
| poisoner | 0.005893 | 0.994107 | **-7.4069** | 1 |

Steering pins the posterior exactly on 4 of 5 targets, and on S2 reaches
`truth_mass=1.000000, log_score=0.0000` — a perfect proper score for one charged
experiment, with `run_scenario` raising nothing.

**Why nothing catches it.** `campaign.py:235-241` re-derives the expected
diagnosis with `diagnose(scenario.id, engine, ...)` — from *the same engine*. A
system that poisons the state and then reports honestly is compared against a
derivation from its own poisoned source, so `_audit` checks a lie against itself.
The two tests that guard this (`tests/test_systems.py`'s `Liar` and `Hedger`)
both fabricate the *returned* `Diagnosis`, which is the case `_audit` does catch;
the state-side case is untested. `ClosedWorldScore`'s propriety argument holds
over *reports* and is void over *state*.

**A17 does not reach it either.** Invariant 2 names four quantities —
plausibility, a posterior value, a metric definition, a score — and only
`plausibility` has a sealed-symbol declaration (`PLAUSIBILITY_SYMBOLS`,
`hypothesis/graph.py:83-88`). `record` is outside A17's scope by construction.
Plausibility itself is genuinely enforced and is the model to copy:
`HypothesisGraph.__post_init__` re-derives the whole vector and raises
`PlausibilityWriteError` on disagreement.

**The cheapest detection, named because it is nearly free.** `campaign.py:250`
stores `experiments=len(investigation.history)` — experiments actually run —
while the posterior comes from `engine.observations`, which is real *plus*
fabricated. The two are never compared. That missing reconciliation is precisely
the absent runtime assertion.

**Also over-exposed, same root cause.** `engine.expand(h)`
(`empirical.py:907`) takes a `HypothesisNode` directly, so a system can admit a
hypothesis to the engine without `graph.propose` and its A16/A18 validation. It
ignores `h.plausibility`, so it is not a plausibility channel — it is the same
boundary leak.

**A second, lower-severity violation, traced but not executed.**
`Investigation.propose`'s `predictions` parameter (`systems/base.py:249-295`)
lets a system author the thresholds it is graded against: a `Prediction` carries
a `Condition` holding floats, `validate_prediction` checks satisfiability,
non-tautology and non-overlap but never that the numbers came from the framework,
and `verify/statistical.py:92-93` then grades the system with them. The framework
has the correct derivation in `table_prediction`, whose docstring — *"why no
baseline in this package contains a number"* — is the tell: the defence is that
in-repo systems decline to use the parameter, not that it cannot be used. No
system currently passes `predictions=`; the only caller is framework-side.

**Left open, deliberately and with the shape of the fix named.** Sealing the
engine behind a recording boundary the system cannot reach, plus the
`history`-versus-`observations` reconciliation as a runtime assertion. Whether
any recorded run has ever exercised this is **not** answerable by inspection: it
needs a registry sweep comparing each run's `experiments` count against its
engine observation count, and no artefact currently stores the latter.

## 2026-08-16 — invariant 3's fourth clause finally has a guard

**Decision.** `test_no_set_iteration_order_reaches_an_artefact` in
`tests/test_invariants.py`, with a `TestTheSetOrderGuardItself` control class,
over `src` and `scripts`.

**Why it was missing and why now.** The invariant names four things and the file
guarded three: the random source, the fold order, the line ending. *"No dict/set
iteration order dependence in anything affecting output"* was convention only,
found by lens 3 of the same pre-commit audit. The convention held — the guard was
written against a tree it reports clean over both roots, and the cross-
`PYTHONHASHSEED` child-process arms of A1 and A15 exercise it end to end for the
pipelines they run — but a clause with no static guard is one whose next
violation is found by a reader.

`pytest-xdist` is what moved it from theoretical to worth doing. A serial run
holds one `PYTHONHASHSEED` throughout, so an ordering dependence is at least
consistent within a run; four workers hold four seeds at once, so the same
dependence can make two tests in one run disagree.

**Scoped to sets, deliberately not dicts.** A dict has preserved insertion order
since 3.7, so flagging `.items()` would put noise on the commonest loop in the
codebase, and noise is how a guard gets suppressed rather than obeyed. A set's
order is a function of its members' hashes, and for `str` that is a function of
the seed. Four shapes are caught — `for` over a set, a comprehension over one,
`list()`/`tuple()` of one, `join()` of one — and `sorted()` is exempt because it
is the fix. Syntactic and therefore a floor, not a proof: a set arriving through
a parameter is invisible to it, which is the same bargain the other three guards
strike.

## 2026-08-16 — invariant 2: the engine is sealed, and what the seal does not reach

**Supersedes the "left open" clause of** *2026-08-16 — invariant 2 is violated: a
system can write its own posterior*, which named the shape of the fix and
deliberately did not build it. That entry stands; this one closes the part of it
that is now closed and is explicit about the part that is not.

**Sequenced ahead of item 15 on the backlog entry's own instruction** —
"before item 15, since the matrix is the first time these systems run at scale
and a fabricated posterior would be indistinguishable from a real one in the
report". `/next`'s cursor pointed at item 15; the backlog overrode it.

**Decision.** Two defences, not one. `Investigation.engine` returns an
`EngineView` that withholds `record`, `record_probe`, `expand`,
`ensure_structure` and `ppc`; and `run_scenario` reconciles what the engine
holds against what the run is entitled to, raising `EngineTamperError` when they
differ. The view is the boundary, the reconciliation is the runtime assertion
the invariant demands, and the second exists because the previous arrangement
*was* a boundary — an undocumented one, believed for as long as nothing checked
it.

**The reconciliation needs two clauses, and shipping with one would have left
the hole half-open.** This is the part worth recording, because the first
version had only the obvious clause and looked complete.

- *Evidence.* Engine observations against `investigation.history`. Catches a
  fabricated `record`.
- *Hypotheses.* `engine.hypotheses` against the graph's admitted nodes. Catches
  `expand`, which admits a structure to the engine **without** `graph.propose`'s
  A16/A18 validation. Mass is distributed over what the *engine* holds, so a
  hypothesis the graph never saw takes weight and scores — while the evidence
  clause passes untouched, because the system ran and charged for exactly the
  experiments it recorded.

**Found by `/ship`'s independent review, by execution, not by reading.** Against
the evidence-only version a system that ran one experiment and smuggled one
hypothesis moved its own log score from `-inf` to `-12.2` with nothing raising.
Guarded now by `test_a_smuggled_hypothesis_is_refused`, which was confirmed red
against the one-clause version before the clause was restored. The general
lesson is the one the review's existence assumes: the author of a check is the
worst judge of what it misses, and *this* check's blind spot was the second
mutating verb in a list the same author had written three lines above.

`ensure_structure` needs no clause. It fills a table row and admits nothing, and
`EmpiricalTable.with_structure` leaves every existing row untouched, so it moves
no mass — checked rather than assumed.

**Then the invariant auditor found the same mistake twice more, one level down.**
The two-clause version named *what the engine holds* and still not *what it is
worth*. Two further channels, both reachable by the same route and neither
touching an id:

- **Structure rewrite.** `_Hypothesis` is `@dataclass(slots=True)` and not
  frozen, so an existing hypothesis's `program_edit` can be reassigned in place.
  `engine_edits` reads it straight off the engine and `run_scenario` passes that
  mapping to both `closed_world_score` and `structural_distance` — so rewriting
  the leader's edit set to the scenario's truth awards an exact match while
  every id stays put. This is the sharpest of the lot: it sets the score
  directly rather than moving mass and hoping.
- **Status flip.** `posterior()` gives a rejected hypothesis exactly zero and
  renormalises the rest over themselves, so flipping a rival to `rejected`
  hands its mass to the leader.

Both are now clauses. The status clause is free of false positives *by
construction rather than by luck*: nothing in `src/sciagent/` rejects anything —
`Investigation` never calls `graph.reject` — so `engine.live == engine.hypotheses`
on every honest run, and the day that stops being true the clause fails loudly
instead of silently widening.

**Left uncovered, and named rather than implied: table replacement.** `_table`
is a plain slot that `ensure_structure` legitimately reassigns, so there is no
fixed expectation to compare against. A caller who can reach the private engine
to swap the table can reach anything else, which is the honest limit of this
whole approach: `_reconcile` is a check with a stated scope, not a containment
proof. Python has no capability boundary to offer and pretending otherwise is
how the docstring got it wrong twice.

**Two ordering defects the same audit found, neither of them about tampering.**

- `_attempts_of(system)` reads `Proposing.attempts`, a **property** — system code
  — and it sat *after* `_reconcile` in `run_scenario`'s argument list. Keyword
  arguments evaluate in source order, so `graph=` and `evidence=` were captured
  after it: a system holding its `Investigation` could add an experiment to the
  evidence index, or a node to the graph, once the check had passed. Hoisted
  above the check. The score itself was never exposed — it is evaluated earlier
  in the same list — which is why this reads as a near miss rather than a hole.
- `Investigation.propose` called `ensure_structure` only in the
  `predictions is None` branch, so the explicit-predictions path reached it via
  `expand` *after* the graph had been reassigned. A caught
  `StructureNotMeasurableError` would then leave the graph holding a node the
  engine did not — an `EngineTamperError` on an honest system. Not live (nothing
  passes `predictions=`), but it is a false positive that this change would have
  introduced, coupling the known-open `predictions=` channel to a new failure.
  `ensure_structure` is now unconditional and precedes the graph move in both
  paths, which is the ordering B5 already depends on.

**The pattern across all three rounds is worth more than any individual fix.**
Each version of the check enumerated the ways of cheating that its author could
think of, and each time an independent reader found one more by asking a
different question: first *which verbs mutate*, then *which fields are mutable*,
then *what runs after the check*. None of the three was subtle. All three were
invisible from inside the reasoning that produced the previous version.

**Why `ppc` is withheld though it mutates nothing.** Everything else on that
list writes. `ppc(experiments={...})` only reads — but it reads *evidence of the
caller's choosing*, and SPEC §4.6's Stage A is a question asked of the
framework-selected reading. Forwarding it, even unscoped, hands back the choice
that `Investigation.ppc`'s scoping exists to remove. This is the non-obvious
half of the boundary: read-only is not the criterion, *authorship* is.

**The view's docstring claimed a guarantee it cannot hold, and that was the
same review's second finding.** It said the wrapped engine "cannot be mutated
through this object"; `view._engine` is one attribute away, and the test meant
to catch that filters underscore names, so it structurally could not. The
docstring now states only what is true — no *method* here writes — and names the
reconciliation as what actually binds. Recorded because the failure mode is
precisely the one invariant 2 legislates against, committed inside the change
written to satisfy it: a comment holding a line it cannot hold. Enforcement
prose is not enforcement, including in a file about enforcement.

**Why the delegation is written out by hand.** An `EngineView` forwarding
through `__getattr__` would have been four lines and would have re-exposed every
method the engine grows afterwards — the boundary would widen silently with the
class it wraps. Explicit delegation makes admitting a capability an edit to
`view.py`. The cost is a file that must be touched when the engine gains a query,
and that cost is the feature.

**`ReadableEngine` is in `inference/view.py` and not in `inference/interface.py`,
where a protocol belongs.** `boed.plan` and `table_predictive` needed widening to
accept either the engine or a view; the protocol they need has a `table`
property returning an `EmpiricalTable`, and `inference.interface` is what
`inference.empirical` imports *from*, so putting it there is a cycle. Recorded
because the placement looks like carelessness and is not.

**What this does not reach, and what each is waiting on.**

- `Investigation.propose(predictions=...)` — a system can still author the
  thresholds `verify/statistical.py` grades it against. Left out because the fix
  changes `propose`'s signature, which SPEC §3.5 speaks to, and because no
  system passes the parameter today: the only caller is framework-side. It needs
  a decision about the signature before it needs code.
- **Probe reconciliation.** `_probes` has no public accessor, so reconciling the
  probe compartment would mean adding one — widening the surface this change
  narrows. Not done, and not merely forgotten.
- **Historical runs remain unverifiable.** The previous entry's closing point
  survives untouched: whether any *already recorded* run exercised this cannot be
  settled by inspection, because no artefact stores the engine's observation
  count. The reconciliation covers runs from here forward and says nothing about
  the registry as it stands.

**Cheap, unlike the other change queued in front of item 15.** No
`MetricRegistry.version` event, so no table rebuild: the full suite ran 443.92s
(1138 passed, 7 skipped) against the ~6m44s warm baseline, versus the 31m45s that
the mark-arrival diagnostic's re-addressing would cost. The seal can land before
the matrix without paying for it.

## 2026-08-17 — item 15: the campaign ledger is a sibling store, because a score is not an experiment

**Decision.** SPEC §9's matrix records completed cells in a new
`sciagent/registry/ledger.py::CampaignLedger`, addressed by the same
`ExperimentKey` the registry uses, and *not* in `ExperimentStore`. The sqlite
machinery both need — the authorizer allowlist, the aborting UPDATE/DELETE
triggers, the statement-cache disable, the canonical length-prefixed encoding —
moved to `sciagent/registry/backing.py` and is now shared rather than
duplicated.

**Why, and it is not a matter of taste.** `ExperimentStore.append` refuses any
non-finite result: *"a diagnostic that cannot produce a number must fail, not
register one."* That is right for an experiment and wrong for a score, and the
matrix would hit it on ordinary cells rather than on edge cases:

| field | when it is non-finite |
|---|---|
| `d2_held_out_predictive` | `-inf` when the candidate ruled out something that happens |
| `d2`, `d3` | `nan` on an empty held-out battery |
| `ClosedWorldScore.log_score` | `-inf` whenever the truth got zero mass — **B1's ordinary case**, since it holds only the null |

So the two stores disagree about what a valid payload *is*, and one class
serving both would mean relaxing a guard that is load-bearing on the other side
of it. The alternative considered and rejected was encoding the non-finite cases
as a value plus a sentinel code, which is lossless but puts an encoding trick
between the matrix and its own numbers.

**The extraction was the price, and it was worth paying.** Duplicating ~25 lines
of authorizer and trigger code across two stores would have left A12's three
enforcement layers checked on one of them and merely *resembled* on the other.
A12–A15 pass unchanged against the extracted version (26 tests), verified before
anything was built on top of it.

**Digested over `float.hex()`, not over the packed double.** Two `nan`s are then
the same reading. That is what a resumed campaign needs — `nan != nan`, so a
value comparison would make an honest rerun of a cell *raise* — and it keeps a
platform's choice of nan payload bits out of the address, which the 2026-08-15
cross-platform entry is the standing reason to care about.

**The payload is named, not a positional vector.** A fixed-width vector that
gains a field silently re-reads every stored row against the wrong names, and a
matrix is exactly the artefact where that goes unnoticed: the numbers are
expensive, so nobody recomputes one to check it. `test_ledger.py` pins that a
reading which gained a field is a *conflict*, not a match.

**Closes off.** The partition is in the cell's `config` — part of the address —
rather than a ledger column. A cell run on DEV and the same cell on TEST are two
readings and must not collide; a column would instead ask this store to
reimplement A14's sealed-read boundary, and it has no sealed-read path to
protect.

## 2026-08-17 — item 15: matrix seeds are paired across arms, and never come from iteration order

**Decision.** `replicate_seeds(scenario_seed, replicates)` derives the twenty
seeds of a cell from the **scenario's seed and the replicate index alone** —
never from the system, and never from the order the driver visits cells in. It
is a prefix stream: `replicate_seeds(s, 5) == replicate_seeds(s, 20)[:5]`.

**Why each of the three properties is load-bearing.**

- **Paired across systems.** §9's preregistered contrast asks whether V7 exceeds
  B4 on S11. With per-system seeds that comparison would be partly a comparison
  of *worlds*, at twenty draws an arm — a variance cost paid for nothing, on the
  one contrast the slice is built around.
- **Not from iteration order.** Invariant 3 routes randomness through explicitly
  passed generators, and a seed taken from a loop counter is neither. The failure
  it prevents is specific and silent: resuming a campaign that stopped halfway
  would change which world a cell ran in *while its address stayed the same*.
- **A prefix stream.** Raising a campaign's replicate count reuses the seeds
  already spent. A re-draw would, under invariant 4, strand every recorded cell
  at an address nothing asks for again — the rows would survive and be useless.

**A judgement call worth flagging, since nothing forces it.** A scenario's
Stage A probe design stays *in* the held-out battery. `_run_stage_a` measures
rather than runs it, so it never enters `run.evidence` and no system could cite
it; it is identical across arms, so it biases no comparison. But it is a design
the posterior predictive check has already seen, which is an argument for
excluding it that I did not find decisive. Recorded rather than buried.

**Closes off.** `reading_of` *derives* the held-out battery from the run's
evidence index instead of accepting it as an argument.
`scoring.dimension_vector` says it cannot check that the battery excludes what
was run — "the caller holds the history and it is the caller's to honour" — and
that is true of it and false of `reading_of`, which has the run. One way to get
a whole matrix quietly wrong, removed.

## 2026-08-17 — item 15: the driver is built and deliberately not run

**Work left incomplete, and what each part waits on.** The resumable campaign
driver exists (`sciagent/eval/matrix.py`, `environments/pointproc/matrix.py`,
1,120 replicates over §9's 56 cells) and **no cell has been run**. Three
separate things block that, and only the third is a matter of time:

1. **The platform precondition, unsettled.** The 2026-08-15 entry measured a
   real Windows/Ubuntu divergence and recorded that the registry
   content-addresses with no platform term. A matrix built partly on each would
   be internally incomparable with nothing in the registry to report it. This is
   the `/matrix` skill's stop condition and it has not moved.
2. **Subscription rate limits across a recording run, still unmeasured** — open
   item (2) of the 2026-08-15 Agent SDK entry. A concurrent session was working
   this in `.claude/worktrees/rate-limit-pilot` with uncommitted
   `scripts/rate_limit_pilot.py` and `scripts/stage_a_seed_sweep.py`; this
   session stayed off it deliberately rather than duplicating the work.
3. **The D1–D6 report layer**, which stays its own `docs/BACKLOG.md` entry. §8
   forbids collapsing the six into one number, and that entry argues the report
   layer is where the prohibition either holds or quietly fails — not something
   to improvise while shipping a driver.

**What the driver does guarantee, since it is not obvious from the diff.** An
exception from `execute` propagates with every completed cell already in the
ledger; there is no separate progress file to fall out of step with what was
recorded. `skip_recorded=False` re-executes every cell and lets the ledger's
conflict check compare each fresh reading against the stored one — invariant 3
audited at matrix scale, and the instrument that would catch the platform
divergence reaching a cell.

**Measured.** Full suite green at `-n 4 --dist loadfile`: **204.17s**, 1202
passed, 7 skipped. `mypy` clean over 116 source files. Note this is *slower* than
the 151.30s recorded on 2026-08-16 for the same invocation on this machine; the
suite has grown by several backlog items since, so the two figures are not a
regression pair.

**Closes off.** Nothing about which backend records the matrix — that still needs
(2) above answered, exactly as the 2026-08-15 entry left it.

## 2026-08-17 — item 15: what the pre-commit review and the four invariant lenses caught

**A real invariant 2 gap, in code this change did not write.** `run_scenario`
read `system=system.name` *inside* the `ScenarioRun(...)` call.
`ResearchSystem.name` is a **property**, so reading it runs system code, and
Python evaluates keyword arguments left to right — so `ppc`, `experiments`,
`graph` and `evidence` were all read *after* the system had had one more turn,
with `_reconcile` and `_audit` already passed.

This is the exact hazard `campaign.py` already documents and had already fixed
for `Proposing.attempts`, which was hoisted above `_reconcile` for it. `name`
was missed, and sits **earlier in the same call**, so its window was strictly
larger than the one that was closed.

Latent, not live: all five shipped baselines return a string literal. But item
15 is what makes the consequence durable — every field in that window becomes an
append-only ledger row, and `inadequate` is SPEC §9's *conditioning variable* for
the preregistered contrast.

**Fixed by hoisting `name = system.name` beside `attempts`, and the regression
test was checked against the unfixed code.** `test_a_system_cannot_act_from_the_
property_the_harness_reads` in `tests/test_systems.py` runs a system that spends
an experiment when asked its name. With the hoist the run is **refused**
(`InvestigationError`); with the hoist reverted the same test reports `DID NOT
RAISE` and the run completes, yielding a `ScenarioRun` whose `ppc`,
`experiments` and `evidence` describe two experiments while its diagnosis
describes one. That asymmetry is what would have reached the ledger.

**The reason it took an auditor and not the suite.** Nothing static can see it:
the bug is *evaluation order of keyword arguments*, and both the fixed and the
unfixed forms type-check, lint clean, and pass every existing test.

**Three more, fixed.**

- **`execute` took `Mapping[str, float]`**, with a docstring asserting that only
  `reading_of` should author one. That is prose holding an invariant CLAUDE.md
  says to hold with assertions. It now takes a `CellReading`, which only
  `reading_of` constructs, and `run_matrix` calls `as_payload()` itself.
- **`CampaignAddress.metric_version` was declared, not derived**, and the
  failure mode is specific to `skip_recorded=True` being the default: a metric
  change nobody reflected in the declared version does not raise — the cell is
  *skipped*, the stale reading is reported as the matrix's, and the ledger's
  conflict check cannot fire because nothing executed. `CampaignAddress.of(executor)`
  reads all four fields off the executor; `MetricRegistry.version` is a content
  hash, so a metric change moves it whether or not anybody remembered.
- **`partition` was a bare `str`** — `"dev"`, `"DEV"` and `"dev "` were three
  addresses for one campaign — and is now `DataPartition`.

**Two declined, with reasons, since both are defensible the other way.**

- **The ledger's `entries()`/`count()` return every row regardless of
  partition**, the reverse of `ExperimentStore.records()`, whose `None` means
  *agent-reachable partitions* rather than *all*. Filtering would mean
  interpreting `config`, and holding `config` as opaque text is the whole of
  what keeps the store domain-independent. Recorded as a stated limitation in
  the module docstring; selection by partition belongs to the report layer,
  which must select by address anyway.
- **`_audit` compares `mine != theirs` where `mine` is the system's own
  object.** `Diagnosis.distribution` is *annotated* `FrozenDict` but a dataclass
  does not enforce it, so a hostile `Mapping` subclass with a lying `__eq__`
  passes the audit and is then read by `closed_world_score` and — new with this
  change — by `leading_structure` and as D5's `posterior`. Pre-existing, and it
  needs deliberately hostile code. Not fixed here because the loop compares five
  fields of mixed types (two floats among three mappings), so `dict(mine) !=
  dict(theirs)` is not a uniform substitution, and surgery inside `_audit` at
  ship time is how the defects this very review exists to catch get written.
  **Open, and this is the record of it.**

**Closes off.** Four lenses were run — 2, 3, 4 and 6 — and three came back
clean. Lens 3 independently confirmed the two claims most worth doubting:
`stable_key` is invariant across `PYTHONHASHSEED` 0, 42 and unset, and two NaNs
with different payload bits (`0x7ff8…01`, `0x7ff8…02`) both render `'nan'` under
`float.hex()`, so the ledger's digest treats them as one reading by
construction. Lens 6 confirmed §9's contrast and its comparator B4 date to the
initial commit `7f69717`, three days before V7 existed at `a380a21` — the
ordering confound does not reopen.

## 2026-08-17 — item 15: §8 says both "not applicable" and "in all cases", and the code had already chosen

**The ambiguity.** SPEC §8 line 387 says that for closed-world scenarios S1–S10
"D1–D6 not applicable". Line 391, four lines later, says "Report D1 through D6 as
a vector in all cases". Taken literally the two cannot both hold for S1–S10.

**Resolved as *not the headline* rather than *not computed*, and not by choosing.**
The existing code had already settled it in two places, before the report layer
existed to expose the tension: `scoring.primary_dimension` maps all five
closed-world classes to `None` while returning a dimension name for the other
two, and `CellReading` stores a `DimensionVector` *and* a `ClosedWorldScore` for
every cell without regard to class. So the vector is computed everywhere and read
as primary nowhere in the closed world. `report.CellSummary` follows that: six
dimensions plus the proper score on every row, and `primary=None` saying which
reading is authoritative.

**Why this is worth recording rather than obvious.** The other reading — compute
the vector only for out-of-library and compound cells — is the one a fresh session
gets to from line 387 alone, and it would have made `summarise` branch on scenario
class and emit ragged rows. That is a plausible afternoon's work in the wrong
direction, and nothing in the code comments on line 387 at all.

**Closes off.** Nothing about §8's wording, which is frozen and stays as written.
If the two lines are ever reconciled in the spec, this entry is the record of
which way the implementation went first.

## 2026-08-17 — item 15: the report layer's intervals are normal, unclipped, and share `verify/`'s estimator

**Decision.** `eval/report.py` reports a normal 95% interval on each cell's mean,
reusing `verify/numerical.py`'s `Z_TWO_SIDED` and `CONFIDENCE_LEVEL` and folding
through `core/reductions.py`. It is **not** clipped to each dimension's support,
and non-finite replicates are excluded and counted rather than folded in.

**Why not a bootstrap.** SPEC §12 criterion 5 asks for "a non-overlapping 95%
interval" and names no estimator, so the choice was open. A bootstrap would need a
seeded generator threaded into a *rendering* path, which makes a quoted figure
stochastic — and `verify/numerical.py` already argues the general case: "an
estimator chosen per claim is a degree of freedom". One estimator across the
project costs a worse fit at n=20 and buys a number nobody can shop for.

**Why not clipped, which is the part that would have gone wrong quietly.** D3 is
bounded in [0, 1], and at twenty replicates an interval on a mean near the ceiling
runs past it — measured on `[0.96, 1.0, 1.0, 1.0]`, mean 0.99, standard error
0.01, upper bound ≈ 1.0096. Clipping that to 1.0 reads cleaner and is wrong in a
specific direction: it **narrows** the interval, and §12 criterion 5's test is
*non-overlap*, so clipping the leading arm makes the preregistered claim easier to
satisfy. A presentation choice would have biased the headline result. Unclipped
also leaves the bound overrun visible, which is the honest signal that twenty
seeds is thin.

**Why non-finite values are counted and excluded.** `-inf` and `nan` are ordinary
here, not corruption: D2 is `-inf` when the candidate ruled out something that
happens and `nan` on an empty held-out battery, and `log_score` is `-inf` whenever
the truth got zero mass, which is B1's every run outside S9. `math.fsum` over one
of those returns `-inf` for the whole cell and the variance then returns `nan`, so
a folded-in report would show twenty replicates as `[nan, nan]` — no measurement,
because one replicate was informative. A cell with no finite replicate gets `nan`
and no interval, never `0.0`.

**Closes off.** This is now the only interval estimator in the project, in both
`verify/` and `eval/`. A second one would need a reason recorded here.

## 2026-08-17 — item 15: what the report layer still leaves undone

**Measured.** Full suite green at `-n 4 --dist loadfile`: **136.63s**, 1272
passed, 7 skipped. `mypy` clean over 119 source files. Do not read this as a
speed-up against the 204.17s recorded for the driver commit two entries above:
that run rebuilt cold tables and this one had them warm, so the two are not a
comparable pair. The count is not comparable either — the 1202 figure in that
entry predates the review-driven tests the same commit then added, and 607 tracked
test functions is unchanged by this change, which adds a new untracked file
instead. (Both figures here were corrected during review: an earlier draft of this
entry said 138.52s/1266, which was the run *before* the audits added tests, and
gave a test-function count that the audit fixes then moved. Quoting a count in
prose is a standing invitation to this; the tail of the pytest output is the
figure that matters.)

**Work left incomplete, unchanged by this.** Still no cell of the matrix has been
run, and the two blockers that were never waiting on the report layer both stand:
the platform precondition (an Ubuntu run of `determinism_child.py` diffed against
the Windows baseline) and the unmeasured subscription rate limits. The third
blocker, the report layer, is closed — so item 15's remaining work is now
*entirely* those two, which is a narrower statement than the driver entry could
make.

**Deliberately not built, and this is the one worth flagging.** `render` emits
ASCII only, and there is no machine-readable output — no CSV, no JSON. The first
is a real constraint discovered rather than chosen: a literal `§` in printed
output comes back as a replacement character on a Windows console at cp1252, so
the section signs that are correct in a docstring are wrong in `print`, and a test
pins `text.isascii()`. The second is scope: nothing consumes a matrix yet, and a
serialisation format invented before its consumer would be the wrong one. The
ledger is already the machine-readable artefact.

**Closes off.** Nothing about which backend records the matrix — that still needs
the rate-limit question answered, exactly as the 2026-08-15 entry left it.

## 2026-08-17 — item 15: three audits, and two of my own tests could not fail

**What the audits caught, and all three findings were about claims rather than
about numbers.** Invariants 2 and 3 both hold in the report layer — no
agent-reachable path to `summarise`/`contrast`/`render`, no arithmetic crossing two
dimensions anywhere in the module, both folds through `core/reductions.py`, and
`kind = scenario_class(scenario)` correctly hoisted above the `CellSummary(...)`
argument list, which is the 2752203 evaluation-order hazard not repeated. What did
not hold was three things I had written down.

**1. `Contrast.preregistered` is a label check, not provenance — three docstrings
said otherwise.** `contrast()` compares its arguments against a `Preregistration`
the same caller supplies, so a fabricated declaration yields `preregistered=True`
for any pairing; an auditor demonstrated it on a B5-on-D1 contrast. **It cannot be
closed from inside `sciagent`**: a declaration names systems and a scenario, so
under invariant 1 it must arrive from outside, and anything from outside is
caller-supplied. Fixed by narrowing all three claims to what the flag buys — an
*accidental* mislabel, which is the realistic error since the comparator and
dimension are keyword arguments a later edit can change while the caption stays
put — and by adding a test that asserts the forgery **succeeds**, so the limit is
pinned rather than waiting to be rediscovered. The thing with authority is the
single `SPEC9_CONTRAST` instance, pinned against drift by its own test.

**2. `CellReading` is not "constructible only by `reading_of`", and this
supersedes the claim in the 2026-08-17 driver entry above.** It is a frozen
dataclass with a public `__init__`; both test modules construct one directly, and
`run_matrix` applies no runtime check to the callback's return. The accurate
narrowing — which is still a real one over the `Mapping[str, float]` it replaced —
is **"only through the whole vector"**: there is no way to supply three fields and
let the rest default, so a caller hand-rolling a payload meets real friction. A
capability boundary is not something Python offers, and `execute` is harness code
in any case. `eval/matrix.py`'s docstring now says the narrower thing and says
explicitly that the stronger claim was wrong.

**3. Two of my tests could not fail for the reasons their names gave.** Both found
by mutation testing rather than by reading, which is the part worth generalising —
I had read both and thought them fine.

- `test_no_type_carries_a_collapsed_figure` parametrised over a **hand-written**
  list of three types while `__all__` exports five, so `Contrast` and
  `Preregistration` were unchecked; giving `Contrast` a `total_score` field and an
  `overall_rank` property passed the entire file. Now derived from `__all__`, with
  a second test asserting the derivation still sees all five. Also recorded
  honestly in the module docstring: the substring check is a **tripwire, not a
  proof** — a collapsed figure named `headline` would pass it — and what actually
  holds §8 is `DIMENSIONS` being a six-tuple plus the absence of cross-dimension
  arithmetic.
- `test_the_rendered_table_names_the_primary_dimension` asserted only `"d3" in
  text.lower()`, and `d3` appears in every report's column header and legend;
  deleting the `primary dimension:` line from `render` entirely still passed. Now
  asserts the label.

**Measured, and this one cost two wrong mutations before it came out.** A third
weak test needed a subtler fix. `test_the_row_order_does_not_change_the_rendering`
used identical values per cell — no fold order-dependence reachable — and compared
`render()` output, whose `.4f` rounds away exactly the last place a bad fold moves.
Replacing it took two corrections:

- *Distinct values are not enough.* With `(0.91, 0.87, 0.94, 0.89, 0.92)` a
  naive fold gives two distinct means across the 120 orderings, but forward,
  reversed **and** rotated all land on the same one. Reversal in particular
  exercises less than it looks, because it keeps each cell's replicates
  contiguous. The test now runs all 120 permutations and compares at
  `float.hex()`.
- **CPython's builtin `sum()` is not an order-dependent fold.** It applies
  Neumaier compensation to floats, so `sum((0.91,0.87,0.94,0.92,0.89))/5` gives
  `0x1.cfdf3b645a1cbp-1` — bit-identical to `math.fsum` — while an explicit
  accumulator gives `0x1.cfdf3b645a1cap-1`. My first mutation used `sum()` and was
  a silent no-op, which read as "the test is fine". Anyone reaching for `sum()` to
  *fix* a determinism problem would get compensation by accident and never learn
  why; anyone using it to *test* for one gets a false pass. `math.fsum` is still
  the right call in `reductions.py` — it is exactly rounded, where Neumaier is only
  nearly so.

**Verified by mutation, not by reading.** All four now fail: collapsed field on
`Contrast`, stripped primary-dimension line, `high` clipped to 1.0, and an
order-dependent fold.

**Left open, deliberately.** `scoring.primary_dimension` raises a bare
`builtins.KeyError` for an unknown scenario class rather than a typed error from
`core/errors`. `ScenarioClass` is a `Literal` so mypy catches it, and fixing it
means editing `scoring.py`, which this change otherwise does not touch. Also: the
report's rendered text is LF-only and ASCII, but Windows stdout is a text stream,
so redirecting `scripts/report_matrix.py` and hashing the file gives a
platform-dependent digest. Noted in that script's docstring; not fixed, because the
ledger is the artefact and this is a view of it.

## 2026-08-17 — item 15: a seed is in a cell's address but not in the campaign address, and the report pooled two campaigns

**The defect, found by pre-commit review and measured.** `report.summarise`
selected rows by matching a whole `CampaignAddress` — matrix version, partition,
env, data and metric versions — and I recorded that as covering "every term of the
address". It does not. `cell_key` also puts the **seed** in the `ExperimentKey`,
and the seed is not on `CampaignAddress`. So a campaign re-run after its
scenario-seed table changed appends a second row per replicate at a *new digest
whose `config` is byte-identical*, and no version comparison can separate them.

Measured on a three-replicate cell: one seed set at D3 0.10 and a re-seeded one at
0.90 came back as **one cell of six replicates, point estimate 0.5000**, interval
spanning both, and nothing raised. That is worse than a wrong number — the
replicate count reads as a fuller campaign rather than a broken one, and 20 becomes
40 exactly where somebody would take it as reassurance.

**Fixed by refusing, not by picking.** `_refuse_reseeded` raises if one
`(system, scenario, replicate)` has more than one row at the address, naming both
seeds. Under the fourth invariant both rows are legitimate appends and neither
supersedes the other, so the report layer has no basis to choose; the caller has to
say which seed set it means. The alternative — threading `scenario_seed` into
`summarise` so it can compute the expected seeds — is a larger interface for a case
nobody has hit yet, and would still need this refusal underneath it.

**Why the existing test did not catch it.** `test_a_re_addressed_cell_leaves_both_rows_and_only_one_is_read`
exercises a *metric version* bump, which **is** on the address and therefore was
excluded correctly. The seed is the one term of a cell's identity that the campaign
address omits, so it was the one case the passing test did not represent. A test
covering the general hazard through the one instance that happens to be handled is
the shape to watch for.

**A spec ambiguity surfaced and deliberately not resolved.** §9's contrast is
"conditional on inadequacy detection", and `_arm` filters each arm by its own flag.
`eval/matrix.py` argues at length that seeds are paired across arms so the contrast
is not partly a comparison of worlds — and per-arm conditioning can undo exactly
that, leaving V7 on seeds {a,b,c} and B4 on {b,c,d} with equal replicate counts and
nothing visible to say so. Which reading §9 intends — each arm on its own
detections, or both restricted to the seeds where they agree — the text does not
settle, and deciding it inside the report layer would decide it by fiat. `Contrast`
now carries `treatment_seeds`, `comparator_seeds` and a `paired` property, and the
CLI prints it. **This needs answering before the contrast is quoted**, and it is not
answered here.

**Three smaller findings, fixed.** `DimensionSummary.level` was stored and rendered
by nothing, so every printed interval was an unlabelled bracket — the header now
states the level, read off a summary rather than restated so the two cannot drift.
The CLI read four fields off `SPEC9_CONTRAST` but took the conditioning from the
function default, so a declaration setting it `False` would have computed the
conditioned contrast and printed `preregistered False` — the one combination that
looks like a finding rather than a bug. And `--contrast` let
`MalformedDesignError` escape as a traceback in a state its own help text calls
legitimate; it is now a message and exit 3, distinct from the missing-ledger 2.

**One thing deleted.** A public `cells_by` helper, unused, absent from `__all__` —
and therefore outside the reach of the no-collapse tripwire that derives its type
list from `__all__`. An unchecked public convenience for slicing cells, in the
module whose job is not collapsing them, is not worth keeping for a line that reads
better at the call site.

## 2026-08-17 — item 15: a guard that checks the wrong property is worse than no guard

**All four invariant lenses clean; every finding was a claim, and the worst one
was a fix.** Lenses 2, 3, 4 and 6 ran on the report layer. Invariants 2, 3, 4 and
6's reason clause all hold — no agent-reachable path to `summarise`/`contrast`/
`render`, no arithmetic crossing two dimensions, no write path to the ledger, and
the layer computes no score `scoring.py` does not already produce. Lens 6 verified
independently that §9's contrast **and** the interval machinery predate the system
they grade: the contrast is in `7f69717` (2026-08-01), `Z_TWO_SIDED` arrived at
`91449a1` (2026-08-04 00:14), and V7 first exists at `a380a21` (2026-08-04 20:43).

**The finding worth generalising: my fix for a bare subscript checked the wrong
property, and its comment said the case was covered.** The previous audit round
had me route `_arm`'s inadequacy read through `_values` "so a bare subscript would
not raise an untyped `KeyError`". `_values` checks a field is **present**. The
conditioning filter tests `flag == 1.0`. So a *present but non-boolean* value
passed the guard and then silently failed the filter, **dropping the replicate**:
measured, one replicate at `inadequate=2.0` moved a contrast arm from `point=0.6333
n=3` to `point=0.9000 n=2` with nothing raised. The guard was worse than none,
because the comment beside it told the next reader the case was handled. Split into
`_flags`, which checks presence *and* value, now used by both `_rate` and `_arm`.

Worth recording separately: the tampered value cannot arrive through
`CellReading`, whose `inadequate` is a `bool`, so `reading(inadequate=2.0)` stores
`1.0`. Reaching the defect at all required writing the payload directly — which is
why this is a hand-built-report guard, and also why the first version of the test
for it passed against the unfixed code.

**Two overclaims of mine were false in ways a test was passing over.**

- **"ASCII, deliberately, as is every other byte `render` emits"** — false.
  `render` interpolates the platform, the grammar, and each cell's system and
  scenario with no check, so `--platform "Ubuntu § café"` produced non-ASCII output
  and a `UnicodeEncodeError` under cp1252 — exactly the failure the comment exists
  to prevent. `test_the_rendering_is_ascii` passes an ASCII platform, so it went on
  passing. Fixed by checking the *inputs*: `_refuse_non_ascii` on platform and
  grammar in `summarise`, and on system and scenario in `_coordinate`, since those
  arrive from the ledger rather than from a caller.
- **"the result is a pure function of the rows and does not depend on the order
  they arrive in"** — overstated. `cells` and the rendering are order-independent;
  the `MatrixReport` *value* is not, because `rows` retains the order given, so two
  reports over one campaign render identically and compare unequal. The docstring
  now says which is which.

**A defect I reintroduced two lines after writing the comment against it.**
`render`'s new interval-level line read `report.cells[0].dimensions[DIMENSIONS[0]].level`
— an `IndexError` on a cell-less report and a `KeyError` on one missing D1, both
untyped, in the module whose every other failure is a `MalformedDesignError`. Worse,
with two cells at different levels it printed the first one's as though it governed
the table, which is the single thing `DimensionSummary.level` exists to prevent. Now
`_level_of`, which reads every summary and refuses a disagreement.

**Also fixed, smaller.** `_refuse_reseeded`'s exception *text* named whichever of
the two seeds arrived first, so one situation produced two different messages
("seeds 0 and 900" / "seeds 900 and 0") — an order-dependent output in a function
whose docstring promises order-independence; the pair is now sorted, and the same
canonicalisation applied to `_values`'s and `_rate`'s diagnostic picks. The
`scenario_class` callback was taken entirely on trust though it decides which figure
is the headline: an unknown class raised a bare `KeyError` out of `scoring.py`, and a
*valid but wrong* one silently rendered S11 with no D3 headline. Now checked against
`SCENARIO_CLASSES`. A self-contrast (`treatment == comparator`) collapsed two arms
into one dict key and returned `overlaps=True exceeds=False paired=True` — an
answer to a question nobody asked; refused. And `Z_TWO_SIDED` was absent from
`verify/numerical.py`'s `__all__` while this layer derives every interval from it.

**A formatting trap, measured.** A `\\u2013` escape in test source does **not**
survive `ruff format` — it is normalised back to the literal character, which then
trips ruff's own ambiguous-Unicode rule. Building the string with `chr(0x2013)` is
what keeps the source ASCII and the test intact. Two rounds were spent discovering
that.

**Left as stated limitations, not fixed.** `platform` is validated for non-blankness
and ASCII but not for *truth* — a caller can label a Windows run as Ubuntu, and the
ledger has no platform term to check against, so this is not closable from here.
`DIMENSIONS` is a rebindable module global typed `tuple[str, ...]` with no length
pin; lens 2 confirmed the worst a rebind achieves is duplicating an already-reported
figure as a seventh column, never synthesising a composite, since no site combines
two payload fields. And nothing in this layer can detect an `execute` callback that
took a number from an LLM response — `summarise` cannot distinguish a fabricated
payload from a derived one, which is invariant 2's problem upstream and not this
module's.

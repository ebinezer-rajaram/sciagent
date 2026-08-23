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

## 2026-08-16 — the rate-limit pilot: what a proposal costs, and the denominator was wrong

**Measured**, twenty real proposals through `AgentSdkProvider` on
`claude-opus-5` at `effort=high`, subscription auth, `scripts/rate_limit_pilot.py`.
Twelve scenarios under `Memory.BOTH` then eight under `Memory.RAW`, so no two
calls shared a brief. **20/20 succeeded and no rate limit was reached.**

| per proposal | median | mean | min | max |
|---|---|---|---|---|
| wall-clock | **37.4s** | 41.5s | 30.2s | 63.9s |
| cost as the SDK reports it | **$0.0980** | $0.1037 | $0.0792 | $0.1408 |
| output tokens | 2,508 | 2,777 | 1,998 | 4,409 |
| brief | 4,422 chars | 4,413 | 4,082 | 4,556 |

Total 830.8s and $2.0734 for twenty. **86.7 proposals/hour** run serially.

**The cost is not a bill and must not be read as one.** `apiKeySource` is
`"none"`; `total_cost_usd` is the SDK valuing the turn at API rates. It is a
proxy for quota consumed, which is the thing the caps are denominated in, and
that is the only sense in which the dollar figures here mean anything.

**It is 2x to 12x the figure this file already carries, and the old one should
not be quoted again.** The 2026-08-15 entry records "a full proposal costs
roughly $0.008–0.05". A full proposal at high effort costs $0.098 at the median.
The gap is thinking: 1,998 to 4,409 output tokens per call, billed as output.
Whatever the earlier range was measured on, it was not this.

**The denominator in the open item is wrong, and correcting it is worth more
than the cost measurement.** The 2026-08-15 entry says "Item 15 is ~1120
proposals". 1,120 is 56 cells x 20 seeds, which is an **investigation** count.
Two facts cut it down:

* Only `Hybrid` holds a proposal layer. Outside `llm/`, which defines it,
  `ProposalLayer` appears in `hybrid.py` and in `ablation.py` -- and the
  ablation's two arms *are* `Hybrid`, under the names V3 and V4. V1, B1, B4 and
  B5 have no layer and never call a model. Of the 56 cells, the model-bearing
  ones are V7's twelve plus V3/V4's six: **18 cells, 360 investigations.**
* Of those, the entry "the gate is threaded" measures the scoped Stage A gate
  opening on **S11 and nowhere else** in twelve scenarios. The cells that ask
  are V7/S11, V3/S11 and V4/S11 -- **three**, at twenty seeds, at
  `max_proposals=2`.

| basis | calls | cost | model time |
|---|---|---|---|
| **S11 alone fires** | **120** | **$11.76** | **1.2h** |
| every model-bearing cell fires at every seed | 720 | $70.56 | 7.5h |
| the ~1120 the open item is written in | 1,120 | $109.77 | 11.6h |

**So the open item is probably answerable without measuring the cap at all.**
A recording run of 120 calls over 1.2h is not a thing a 5-hour window plausibly
refuses. That reasoning is only as good as the row it rests on, and the floor
rests on a gate measurement taken at **one seed per scenario**: S11's probe reads
0.0112 against alpha 0.05 and the nearest in-library miss is 0.0595, so seeds
can move a cell either way. What is now cheap is bounding it -- run the gate
across twenty seeds with a scripted provider and count the cells that fire. That
costs no quota at all, and it is the measurement that closes this, not a burn.

**Three observations from the run that nothing else records.**

* **Every call was served by two models.** `served_models` reads
  `claude-haiku-4-5,claude-opus-5` on all twenty.
  `_refuse_a_substitute_model` passes because it asks whether the pinned model is
  **among** those that served, not whether it was the only one -- which is the
  right rule, since Claude Code uses a small model for its own auxiliary work and
  a stricter test would refuse every real call. Worth knowing anyway: the
  module's docstring reads as the stricter promise, and haiku usage draws on the
  same subscription during a recording run.
* **The prompt is ~3,600 tokens and about three quarters of it is cache
  *creation*.** Median 2,677 created against 969 read, with `input_tokens` at 2
  to 4. The 969 is the constant head; the created part is the brief, which is
  unique per call and therefore written to a cache nothing ever reads back. A
  recording run pays the write premium on every call by construction.
* **`num_turns` was 2 or 3.** `MAX_TURNS = 4` and the constant's comment says a
  real brief "measured two turns consistently". Three happens. The headroom is
  load-bearing, not decorative.

**A version fact that vindicates a decision made hours earlier.** The spawned
binary reported `claude_code_version` **2.1.233** while `claude --version` on
PATH reported **2.1.221** in the same session. Whichever way that resolves --
auto-update mid-session, or the SDK resolving a different install -- it is
exactly the drift the entry "the call address excludes the binary version"
refused to hash into an address. Had `ADDRESS_VERSION` covered it, this pilot's
twenty transcripts would already be split across two address spaces.

**What was deliberately not done.** No registry entry, no committed corpus. The
twenty transcripts are under `.cache/`, gitignored: the brief depends on the
structural menu, `METRIC_VERSION` and the Stage A allocation, all of which moved
today, so these addresses would go stale before item 15 recorded against them.
The script drives `ProposalLayer.propose` directly rather than running V7,
because the gate opens on S11 alone and a run through `investigate` would have
priced one scenario's brief twenty times.

**Closes off.** It does **not** measure the 5-hour or weekly cap. Nothing here
says how many proposals fit in a window; it says what one costs, which is the
other half of that division. Item (2) of the 2026-08-15 entry stays open on
paper, and the cheapest route to closing it is now the scripted seed sweep above
rather than a burn.

## 2026-08-16 — the seed sweep: 142 calls, not 120, and S11 fires at every seed

**Supersedes the call count in "the rate-limit pilot: what a proposal costs, and
the denominator was wrong"**, earlier today. That entry derived **120** calls from
the gate firing on S11 alone, flagged that the derivation rested on one seed per
scenario, and named the sweep that would settle it. The sweep was run. The figure
is **142**, and the reason it moved is worth more than the number.

**Measured**, `scripts/stage_a_seed_sweep.py`, the eighteen model-bearing cells at
twenty seeds each, scripted provider, `max_proposals=2`. Seed `k` is
`scenario.seed + 1_000_000 * k`, so `k = 0` is the recorded seed and the first
column is a control on the harness: it reproduced the recorded table exactly --
V7, V3 and V4 firing on S11, all fifteen other cells shut.

| cell | fired | asks |
|---|---|---|
| V7/S11 | **20/20** | 40 |
| V3/S11 | **20/20** | 40 |
| V4/S11 | *not measured -- see below* | *40* |
| V7/S3 | 2/20 | 4 |
| V7/S12, V3/S12, V4/S12 | 2/20 each, seeds `[5, 7]` | 12 |
| V7/S5, V7/S6, V7/S9 | 1/20 each | 6 |
| V7/S1,S2,S4,S7,S8,S10; V3/S8, V4/S8 | 0/20 | 0 |
| **total** | | **142** |

**S11 fires at every seed, and that is the result the matrix depends on.** SPEC
§9's preregistered contrast conditions on detection; item 12 measured that event
occurring zero times in twelve, and the entry "the gate is threaded" got it firing
at the recorded seed. It now fires at **twenty of twenty** on both arms measured.
The contrast is not a knife-edge on one seed.

**In-library scenarios fire too, and that is the check working rather than
failing.** Six firings across 220 V7 seed-runs on scenarios whose truth is in the
library -- **2.7%**, against an alpha of 0.05. A gate with a 5% false-positive
rate that produced none at all over 220 draws would be the surprising outcome.
Nothing needs fixing; what needed correcting was the arithmetic that assumed
zero.

**V4/S11 is deduced rather than measured, and the deduction is sound.**
`Hybrid.investigate` consults `investigation.ppc()` *before* calling `_extend`,
and `Memory` enters only through `render_brief` inside `ProposalLayer.propose`,
which runs after. The gate is therefore provably independent of the memory arm,
so V3 and V4 must fire identically on one scenario and seed. Corroborated twice:
both S12 arms fired at exactly seeds `[5, 7]`, and both S8 arms fired zero. It is
recorded as a deduction anyway, because the cell was not run.

**What this does to the recording run.** At the pilot's median of $0.098 and
37.4s: **142 calls, ~$14 of quota, ~1.5h of model time.** 142 is also a *ceiling*
-- `_extend` breaks only on a refusal, a scripted provider never refuses, and a
live model refusing its first call would stop the loop at one.

**The rate caps can therefore be closed without measuring them.** A run of ~1.5h
and ~$14 is not something a 5-hour window plausibly refuses. Item (2) of the
2026-08-15 entry is answered by making the numerator small enough that the
denominator stops mattering, which is a better outcome than a burn would have
bought: a burn measures the cap on the day it is run, and Anthropic has already
changed subscription terms once (2026-05-14, paused on the day of effect).

**A machine constraint that cost two crashed runs and one crashed session.** The
sweep is ~300 investigations and the three S11 cells dominate it at ~30 minutes
each; the rest finish in seconds. Run on a 15.9 GB Windows desktop with VS Code,
Firefox and eleven `claude` processes resident, free memory reached **1.0 GB** and
numpy began failing 193 KiB allocations. One failure took the editor's Claude Code
process down with `0xC0000409`, with `majflt` in the hundreds at `cpu=0ms` -- the
machine was thrashing, not computing. **Do not run this sweep alongside a full
desktop.** Cells are independent, which is why `--only SYSTEM/SCENARIO` exists;
run the three S11 cells one process at a time.

**Closes off.** It does not measure the 5-hour or weekly cap, and after this it is
not worth measuring them: the question was whether the matrix fits, and the answer
is that it fits with an order of magnitude to spare. V4/S11 remains the one
model-bearing cell never executed. Nothing here is a live-model measurement -- the
sweep is scripted throughout, because the gate is conventional (SPEC F5) and
cannot see which backend would have answered.

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

## 2026-08-17 — infrastructure: the ship split, and two facts about the skill namespace

**Decision.** `/ship` is two skills now. `/preflight` scopes, verifies and
reviews; `/ship` commits, merges and pushes. The agent runs `/preflight` on its
own initiative at the end of `/next`; only the user invokes `/ship`. The
principle written into both, replacing "invoking `/ship` is the authorisation
for its step 2 subagents": **read-only review is authorised by the work,
irreversible action only by the user.**

**Why the guarantee is "lands nothing", not "read-only".** The first draft said
`/preflight` "is read-only and changes nothing", and used that to license
running it unprompted. The pre-commit review caught the contradiction: the same
skill tells you to fix what the review finds, and fixing edits files. Both
claims cannot hold, and the authorisation rests on the one that is false. The
line the split actually draws is *reversible* against *published*: working-tree
edits are the work already asked for, and nothing in `/preflight` touches
history. Getting this wrong would have been an overstatement doing real work.

**`verify` is a reserved name. Do not use it for a project skill.** A built-in
skill of that name exists and is marked `disable-model-invocation`, so
`Skill(verify)` returns *"cannot be used with Skill tool"* and the project file
is never consulted. It is **invisible** in the session's available-skills
listing — precisely because a skill the model cannot invoke is not listed as one
it can — so the collision cannot be found by looking. Renamed to `/preflight`.
Nothing in `.claude/settings*.json` or the global config mentions it; searching
those is what wasted the time, and the symptom is the only evidence there is.

**Skills resolve from the shared checkout, exactly as agents do.** The
2026-08-16 entry settled this for `.claude/agents/` and left skills open. They
behave identically: `Skill(preflight)` returns `Unknown skill` from the worktree
that wrote the file. The two errors are worth telling apart — *"cannot be used"*
means the name resolved to something else, *"Unknown skill"* means it did not
resolve at all. Reading the first as the second cost an hour and produced a
confident wrong diagnosis ("the harness froze its skill list at session start")
that the rename disproved in one call.

**A hazard the auto-trigger introduced, which the manual flow did not have.**
`/next` backgrounds the suite, then closes out. While `/ship` was user-invoked,
the turn boundary guaranteed the run had finished — you could not type it
sooner. Triggering `/preflight` automatically removes that guarantee and puts
five review subagents beside a live `-n 4` suite, the contention measured at
12–15% on 2026-08-16. The wait is now written down. This is the general shape of
the risk in automating a step: the protection that goes missing is the one
nobody wrote down, because nothing was enforcing it.

**Left open, and what it waits on.** Neither `/preflight` resolving nor the
auto-invocation from `/next` has been exercised, and neither can be from the
worktree that wrote them — they wait on the merge. `/ship` §0 therefore carries
an explicit `Unknown skill` branch telling the session to perform the three
steps inline rather than skip them; without it a worktree ship would lose scope,
`mypy`, `ruff`, the suite and the review at once, silently, one step before a
push.

## 2026-08-17 — infrastructure: the A-test lens, and the question that made it fire on everything

**Decision.** `/next` step 3 now reviews the A-test before the implementation is
built to it — one `evidence-checker` per gate, model omitted so it inherits the
session. Nothing else covers this: `/gate` guards *no test written* and *test
skipped*, `suite-runner` is forbidden from acting on a test it believes is
wrong, and a vacuous `test_aN_` still counts as a covered gate in
`scripts/status.py`, which derives coverage from the name.

**The agent choice moved twice during review, and the second move is the
instructive one.** It was drafted as `general-purpose`, which the review
rejected: CLAUDE.md licenses these unprompted subagents on the grounds that
review is read-only, and `general-purpose` holds `Edit` and `Write` restrained
only by prose. Swapping to `evidence-checker` was then rejected *on its own
argument* — it is `Read, Glob, Grep, Bash`, and `Bash` writes files. Checked
across the fleet: `evidence-checker` and `invariant-auditor` are both
`Read, Glob, Grep, Bash`, and `decisions-sweeper` (`Read, Grep`) is the only
agent here that is mechanically write-incapable. **So every read-only guarantee
this repository runs on — `/preflight`'s four lenses included — is contractual,
not mechanical.** `evidence-checker` stayed, for its contract fit and its
narrower toolset; what changed is that the skill no longer claims a guarantee
nothing enforces. Worth knowing before the next authorisation argument is
written on the same false premise.

**An approach that failed, and the reason it would have been worse than
nothing.** The lens first asked: *name one plausible wrong implementation this
test would pass; if you can, the test is too weak.* That makes the verdict a
function of the reviewer's imagination rather than of the test, and a competent
reviewer can always name something. Probed against A7 — the shipped test, and a
copy with its `0.93` threshold relaxed to `0.50` — it returned **TOO WEAK on
both**. A lens that fires on everything reports nothing.

Rewritten to ask for an implementation the test **accepts and the criterion
rejects**, with the arithmetic shown, and to say explicitly that an
implementation the criterion also accepts is out of scope however unsatisfying
it looks. Re-probed:

| case | verdict |
|---|---|
| A7 as shipped, judged in its module | **SOUND** |
| same, threshold `0.93` → `0.50` | **TOO WEAK** |
| assertion correct, but fails at collection | **TOO WEAK** (question 1) |

Five subagent runs, ~292k tokens, which is why this is recorded rather than
re-derived. The separating arithmetic is the agents' own closed-form work and
**was not independently checked**: the candidates that made the shipped test
look weak land near 96–100% coverage, which A7 accepts, while the threshold
change admits an SE understated by √2 at roughly 82%, which it rejects. The
repo's own measured table in the 2026-08-03 A6/A7 entry gives 0.948–0.950 for a
correct implementation; treat the third digit of anything above as the agents',
not as measured here.

**Excerpting a test changes the answer, so pass the path.** Judged as a 40-line
extract, the shipped A7 came back TOO WEAK on a denominator gap that does not
exist in the module — the guard closing it (`assert len(rows) == TRIALS`) lives
in a *sibling criterion's* test over the same `lru_cache`d fixture. The module
is the unit; the excerpt was a different, worse test.

**It will rediscover settled decisions and argue with them.** The lens reported
A6 and A7 measuring one statistic as an invariant 5 defect, having read and then
argued against the very module comment encoding the 2026-08-03 decision that
resolved it. It cannot see `docs/DECISIONS.md`, and cannot tell a defect from a
choice. The skill now says to check a finding against `/recall` before acting —
the same guard lens 6 of `invariant-auditor` already carries, for the same
reason.

**Two findings about `tests/acceptance/test_a06_a11.py`, raised and not fixed.**
The comment at `:240-243` calls `TRIAL_REPLICATES = 200` "a fifth of the slice's
replicate count"; `REPLICATES = 2000`, so it is a tenth — **verified here**. The
same comment's rationale, that a lower replicate count is "the demanding
direction" because coverage is harder when Monte Carlo error is large, was
claimed to have its sign backwards on the grounds that coverage is scale-free.
**That claim is unverified** and needs its own check. Both are commentary rather
than behaviour; the test passes either way, and neither was fixed because the
change they sit in touches no Python.
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

## 2026-08-17 — Windows is the reference platform; the divergence is closed by pinning, not by fixing

**Decision.** The project is pinned to **Windows**. Every registry entry, cached
table and reported number comes from the desktop. Cloud sessions are for writing
code, reviewing it, running the suite and shipping — they do not produce results.

This supersedes the standing instruction in the 2026-08-15 entry *"the
cross-platform divergence is real, and it has been measured"* (line ~2957): *no
further cloud-produced number should be trusted until this is settled.* That
instruction is **discharged, not satisfied**. The divergence was never localised.
No cloud-produced number is trusted because none is produced.

**Why.** That entry deliberately left three remedies open and declined to choose:
put a platform term in the content address, scope `.cache/tables/` by platform, or
run the whole matrix on one platform and say which. The first two retire every
stored artefact; the third costs nothing. The user's position is that the desktop
is the only machine that runs anything, so the second platform whose disagreement
the first two remedies would price in does not exist. Paying to make two platforms
comparable when only one produces numbers is paying for an option nobody holds.

It also removes a false blocker. `/matrix` opened with *"Stop. Check the platform
precondition first"* and `docs/BACKLOG.md` twice listed the precondition among what
stands in item 15's way. Neither was ever true in the sense that mattered — running
the matrix locally always satisfied it — but both read as work owed before a cell
could run, which is the opposite of what the pin means.

**What was not weakened, and why it looked like it might be.** The exact-fold
reductions in `core/reductions.py` and the `reductions.total` call inside the
Hawkes intensity are **kept and rejustified**, not relaxed. Their docstrings argued
entirely from portability across machines, which the pin makes moot — but invariant
3 is a same-machine requirement first: byte-identical output across processes,
across runs, and across a numpy upgrade that changes kernel selection underneath
you. Exact folds buy that on one machine and get the cross-platform class for free.
The prose now says so; the code is untouched.

`summarise()` keeps its **required** `platform` argument. Under a pin the value is
a constant, and a constant is exactly the kind of fact that stops being written
down — which is the argument for holding it by construction. A report that names
its platform stays readable if the pin is ever lifted; one that assumed it does
not. This was offered as removable and declined.

**Closes off.** `/platform-check` and `tests/acceptance/determinism_child.py` stay
in the repository, correct and dormant. They are the instrument for lifting the
pin, not a blocker on anything, and nothing routine should invoke them. Two
situations reopen this: a decision to run results on a second machine, or a
comparison of a number from one platform with a number from another. Neither is
on the horizon.

Item 15 now waits on **one** thing, not two: the unmeasured subscription rate
limits.

**A second axis, found by the review of this very entry, and left open.** The
rejustification above was first written as *"exact folds deliver byte-identical
output across processes, across runs, and across a numpy upgrade that changes a
kernel selection."* The third clause is **false**, and `reductions.py`'s own
unchanged text says why: `math.fsum` fixes the *order* addends are summed in,
never the addends themselves, and `math.exp` disagrees with the correctly-rounded
double for **17694 of 20000** inputs across `[-40, 0]`. The Hawkes kernels hand
`np.exp(...)` terms straight to `total`. A numpy upgrade is free to move those
addends exactly as a change of platform is. The sentence was corrected before
this shipped; the comment at `components.py:128` had it right all along, scoping
its claim to the order.

What the correction exposed is worth more than the error. **Nothing in the content
address names the numpy that computed the numbers.** `pyproject.toml` requires
`numpy>=2.1` and `uv.lock` pins `2.5.1`, so `uv lock --upgrade` can move it
inside the existing constraint, and `(env version, config, data version, metric
version, seed)` does not shift. `docs/DECISIONS.md` 2026-08-15 records
`METRIC_VERSION` and `LIBRARY_VERSION` being bumped **by hand** when a fold
implementation changed — there is no automatic term. So this is the 2026-08-15
shape of problem on a different axis: two entries sharing an address while
holding different numbers, with nothing positioned to notice.

**Deliberately not fixed here, and not a reason to hold the pin.** It is
orthogonal — pinning the platform never claimed to close it, and the axis was
open before this entry and is no wider after. Settling it would mean either a
numpy term in the address (retiring every stored row) or a two-version diff of
`determinism_child.py` of the kind the platform question got. Neither is this
change's business. What is recorded here is that the second axis exists, that it
is unmeasured, and that `uv lock --upgrade` is the action that would trip it.
Do not read the Windows pin as covering it.

The platform measurement itself is untouched and still stands — S12's final PPC
p-value is `0.101100` on Windows and `0.1009` on Ubuntu, from one commit and one
seed. If the pin is ever lifted, that is the entry to return to. This one says
only that nobody needs to.

## 2026-08-17 — item 15: the matrix had a driver, a ledger and a report, and no way to run a cell

**Decision.** `run_matrix` has taken an `execute: Callable[[CellTask], CellReading]`
callback since the driver landed, and nothing outside a test supplied one:
`reading_of` was called only from `tests/test_matrix.py`, and `SPEC9_CELLS` was
consumed only there. `environments/pointproc/runner.py` is that callback —
`system_for` for all seven arms, `MatrixRunner` holding the threaded table, and
`scripts/run_matrix.py` in front. Environment-side, for the reason `SPEC9_CELLS`
is: it names `V7` and `S11`.

This gap was never written down as a blocker. The 2026-08-17 driver entry lists
three, and the Windows pin entry narrows them to one — the rate limits — which is
true of the *LLM* arms and was read as true of the matrix. **38 of 56 cells need
no provider at all**, and nothing was stopping them.

**Why.** Table acquisition had to move out of `tests/` first. `_cache_root`,
`_cache_key`, `cached_table`, `slice_table` and `search_table` lived in
`tests/slice_tables.py`; a script cannot import that, since the bare
`import slice_tables` resolves only under pytest's prepend mode, `tests/` having
no `__init__.py`. They are now `environments/pointproc/tables.py`, re-exported
from where they were. The alternative — a second implementation beside the first
— is the bug this file already records twice: two ways of finding the cache, one
silently wrong, **3m11s cold against 1.055s warm**.
`test_the_tests_and_the_runner_resolve_the_same_directory` asserts the two agree,
and the anchor moved with the file (`parents[1]` from `tests/` is `parents[3]`
from `src/environments/pointproc/`; wrong is invisible except as time).

The gate table **stayed** in `tests/`: it is the suite's artefact, grown by the
suite, and nothing in `src/` reads it. The campaign got its own `matrix-*.json`
for the mirror reason — a matrix run that grew the gate table in place would
change what a later suite run starts from.

**Measured, and expensive to reproduce.** Per replicate on this desktop, warm
tables: V1, B4 and B1 all under 0.2s; **B5 is the entire cost of the matrix.**
B5 on S1, replicates 00 through 04: **33.8s, 0.6s, 77.0s, 0.6s, 94.3s** — it
alternates, because a replicate is cheap exactly when the beam's picks are
already in the threaded table. The first replicate simulates **12,000 rows**; the
same task repeated on a threading runner simulates **0**, in 0.6s. Do not
extrapolate a total from the cheap ones.

Full suite green at `-n 4 --dist loadfile`: **144.73s**, then **145.70s** after
the review fixes, both `1329 passed, 7 skipped`. `mypy` clean over 123 source
files.

**Two tests were killed by the A-test lens before any implementation existed, and
both would have passed.** Recorded because the second is the kind nobody finds
later.

1. A seed test asserted a replicate reproduces `run_scenario` on the *untouched*
   scenario, on the premise that `replicate_seeds` puts the scenario's own seed
   first. **It does not** — `eval/matrix.py:293` hashes `matrix/{seed}/{index}`,
   so S9's `20260909` becomes `3756393230493447090`. The test passed anyway,
   because its three asserted quantities happened not to move on B1/S9: measured
   over the first four replicate seeds, all four give `(1.0, 8, 0.0)`. The
   explanation first offered here — that `ppc.py`'s `1 + ln(n)` scale saturates
   the null scenario under the null hypothesis at exactly 1.0 — is **too strong**,
   and the campaign says so: over 20 seeds, `experiments` and
   `structural_distance` really are invariant, but `ppc_p_value` is 1.0 on only
   **15 of 20**, the rest falling between 0.525 and 0.597. The first four seeds
   were among the fifteen. So the test was hidden by luck as much as by
   saturation, which is a worse defect than the one originally written down, not a
   better one. Its sibling — *two replicates differ* — was still **red for a
   correct implementation** on replicates 0 and 1, which are both 1.0. A runner at
   `task.seed + 7` passed both. Replaced by
   a byte-equality test against an independently built run at `task.seed`, **on
   V1/S1**, where 7 of 16 payload fields move at `seed + 7` (on S9 only
   `ppc_p_value` moves), plus a test asserting that difference so the instrument
   cannot go vacuous unnoticed.
2. A threading test compared `table.structures` as a set under `>=`. A runner
   rebuilding from `gate_table()` every replicate re-grew the same structure and
   passed. Replaced by the simulation count above.

**An injection seam was built and then removed.** `MatrixRunner` took a
`simulate` argument so that test could count calls. The invariant-2 audit was
right that it was wider than its purpose: a caller-supplied simulator changes what
gets **scored**, not merely what gets counted, and `execute` persists the grown
table into a shared content-addressed cache whose key covers templates, replicate
count and seed — **but not the simulator**. Poisoned rows would be
byte-indistinguishable from faithful ones and read by every later campaign, and
the ledger's conflict check would not catch it either, since two passes with the
same doctored simulator agree. Counting was what was wanted, so the runner counts
itself (`MatrixRunner.simulations`). Not an invariant-2 violation as written — no
agent can reach it — which is exactly why it would have survived.

`--provider` offers the two live backends and deliberately **not** `scripted`. A
scripted provider's answers are fixtures and the ledger has no field saying so.

**The ordering question, asked and closed.** The runner threads a growing table
across replicates, so resuming a campaign visits cells in a different order than
one pass does. Invariant 3 would be violated if that moved a number. It does not:
a row is a pure function of `(seed, replicates, structure, template)`, so a table
holding more when a later cell runs changes what the cell **costs** and never what
it scores. `ExpansionCost.simulator_calls` is the one order-dependent figure, and
every call site discards it; no cost field reaches `CellReading`.

**Closes off.** Work left incomplete: the 18 LLM cells — V7 on twelve scenarios,
V3 and V4 on three each, 360 investigations — are not run. The unmeasured
subscription rate limits are the only thing in the way, and a concurrent session
holds that work. `system_for` builds those arms today and refuses without a
provider, so nothing structural is owed.

One consequence worth stating plainly: **no LLM arm has ever been run in this
tree.** The invariant-2 audit traced statically that an LLM arm's numbers can come
only from `reading_of` — the parameter channel is a grid *index*, not a value
(`encoding.py:258`), and `draft_from_payload` refuses an unknown key by name — but
traced is not measured. `tests/test_matrix_runner.py` pins a runner replicate
against an independent run for **V1 on S1 only**.

Nothing here settles which backend records the LLM cells. It does close the claim
that item 15 was blocked: the conventional two-thirds of the matrix was runnable
throughout.

## 2026-08-17 — item 15: the first 38 cells are run, and the cost estimate was wrong by eightfold

**Decision.** The conventional two-thirds of SPEC §9's matrix is recorded:
**38 cells, 760 replicates, 20 per cell, no skips**, in `.cache/campaign/spec9.db`
on the Windows desktop, at address
`pointproc/pointproc/1.1.0+1.2.0+1.1.0` / `pointproc/generated/1.0.0` /
`metrics/9b1c54c9d49f49f656c30e32d21d4a7b`, partition `dev`. **Exploratory by
construction** — §9 says slice results inform the frozen campaign and are not
reportable as confirmatory findings.

**353,724 rows simulated** — read off the run's stdout, which nothing persists;
the ledger does not carry it and it is not reproducible from the artefacts.
**20m19s** between the ledger's first and last write, which is what "about
twenty minutes" below rests on; no timer bracketed the process.

Not the first cells ever *run*: `.cache/campaign/smoke.db` holds eleven at the
same content addresses, from the smoke test twenty minutes earlier. The first
recorded in the §9 campaign ledger. That overlap turned out to be worth more than
the tidier claim — **all eleven shared addresses carry equal reading digests
across the two runs**, which is invariant 3 checked across two processes whose
tables were in different states, and it was free.

**The projection this entry's title refers to was made in conversation, not in
the entry above** — that entry says only "do not extrapolate a total from the
cheap ones", and it is worth being exact about which of the two erred. The
projection took the mean of B5's five quoted S1 timings across its 240
replicates, reached about 2.75 hours, and predicted a run needing relaunching.
The campaign took about twenty minutes, roughly eightfold less. The error was
extrapolating from the *expensive* replicates immediately after warning against
extrapolating from the cheap ones. Measured: B5's early replicates on S1 cost
33.8s to 94.3s, its final ones **0.5s median** with an occasional 7.2s. The cost
is not a per-replicate rate at all — it is the one-off price of each new
structure, and the threaded table pays it once. Threading is therefore not an
optimisation on this matrix; it is the difference between twenty minutes and a
campaign nobody runs. A future estimate should model *distinct structures*, not
replicates.

**Measured: correct rate, 20 seeds per cell.** Dot means the cell is not in §9.

```
         S1    S2    S3    S4    S5    S6    S7    S8    S9   S10   S11   S12
  V1   1.00  1.00  1.00  1.00  1.00  1.00  1.00  0.00  1.00  0.00  0.00  0.15
  B4   1.00  0.00  1.00  1.00  1.00  1.00  0.00  0.00  1.00  0.00  0.00  0.00
  B5   0.00  0.00  0.00  0.00  0.00  0.00  0.00  0.00  1.00  0.00  0.00  0.00
  B1      .     .     .     .     .     .     .     .  1.00     .  0.00     .
```

**B5 scores zero everywhere but S9, and that is expected behaviour rather than a
defect.** The support is *mechanical*, and this matters because the obvious
citation does not bear the weight: `tests/test_baselines_slice.py:196` explains
the zeros in a docstring but **asserts nothing about them** — its two assertions
are `mean_b5 < mean_b1` and `len(inside) >= 3`. The mechanism is that B5
enumerates single-edit candidates at *corner* resolution and every slice truth is
an interior point of the same grids, so exact match by proposal is impossible
rather than merely unlikely. S9 is the exception in the campaign and at the gate
alike, because the null is seeded rather than proposed.

The figure with content is structural distance: **0.917**, over the nine non-null
closed-world scenarios, averaging per scenario then across, at 20 seeds.

**Do not compare that to the 0.775 in that docstring.** It is attributed there to
*item 11*, the docstring says outright "the assertion is the comparison, not those
figures", and a Stage A probe defect has moved B5's structural recovery since it
was written; re-running the gate's own computation now gives about 0.812. Nor to
its `>= 3` bar: the gate's "inside the right cell" is a *single run's* distance
below 1.0, and a per-scenario mean over 20 seeds is a different quantity. Per
replicate the campaign puts **2.55 of 9** scenarios inside, which is at or under
that bar rather than comfortably past it — most scenarios sit at exactly 1.0 for
most seeds and the mean dips below only because a minority land near 0.44.

This was worth checking because two independent reviews flagged that `runner.py`'s
`_beam()` pairs the search table with `simulator(GRAMMAR)` while
`tests/test_baselines_slice.py:78` uses `simulator(agent_grammar())`. That
discrepancy has since been **measured directly** rather than argued from: all 48
single-edit agent-grammar candidates by 6 templates by 2 seeds, plus the 5
closed-set defects by 6 templates by 3 seeds — 666 comparisons, **zero
differences**, including identical refusals on the 22 degenerate corners. It does
not reach a number on the space the beam searches.

**Measured: inadequacy rate — the whole-record posterior predictive check.**
**This is not Stage A**, and the distinction is the one that makes SPEC §12
criterion 4 incoherent, so it is worth stating rather than assuming. The field is
`CellReading.inadequate`, which is `ScenarioRun.ppc.inadequate`, documented at
`eval/campaign.py:104` as "**Not** the verdict a system acted on where a scenario
declares a Stage A probe". It covers the run's recorded experiments and no probe.

```
         S1    S2    S3    S4    S5    S6    S7    S8    S9   S10   S11   S12
  V1   0.05  0.00  0.00  0.00  0.00  0.00  0.00  0.00  0.00  0.35  0.00  0.00
  B4   0.00  0.00  0.00  0.00  0.00  0.00  0.00  1.00  0.00  0.20  0.05  0.35
  B5   0.35  0.35  0.00  0.15  0.20  0.00  0.40  0.55  0.00  0.95  0.30  0.00
  B1      .     .     .     .     .     .     .     .  0.00     .  1.00     .
```

**`B1` on `S11` fires 20 of 20 on that check, and 0 of 20 on `S9`.** At 20 seeds
this corroborates the S11 and S9 entries of the full-record row in
`docs/BACKLOG.md`'s criterion-4 table, which was a single seed; §9 gives B1 only
those two scenarios, so the campaign says nothing about the other three that row
names.

**It does not set criterion 4's bar, and an earlier draft of this entry said it
did.** That draft read "the bar criterion 4 names is 1.00, not the 0.0294
single-seed p-value that entry quotes" — setting a full-record *firing rate*
against a Stage A *p-value*. Those are the two different checks whose
disagreement is the first of the three reasons `docs/BACKLOG.md` gives for
calling the criterion incoherent, so the sentence was a fresh instance of the
confusion it was citing. Caught by an independent check of this entry before it
was committed. Recorded because the mistake is easy and the two numbers look
comparable.

What stands: a measurement of the full-record check, at 20 seeds, on B1's two
scenarios. The re-specification is still owed, and `docs/BACKLOG.md` still says
it "must not be decided in a session that has just measured V7 against the
candidate wording" — this session measured B1's side, which is the same hazard
from the other direction, so nothing is concluded here.

**Closes off.** The 18 LLM cells still wait on the rate-limit measurement, and
`.cache/campaign/spec9.db` is a partial campaign by design — `run_matrix` skips
what is addressed, so adding them later is a resume and not a re-run. The
preregistered contrast (V7 vs B4 on S11, D3, conditional on inadequacy detection)
is **unanswerable** until they land, which is why `scripts/report_matrix.py` keeps
`--contrast` off by default.

## 2026-08-18 — item 15: the rate-limit measurement was taken on 2026-08-16 and nobody could see it

**Decision.** The two 2026-08-16 entries — "the rate-limit pilot: what a proposal
costs, and the denominator was wrong" and "the seed sweep: 142 calls, not 120,
and S11 fires at every seed" — are landed **verbatim, at their chronological
position** (now lines 5072 and 5170), together with `scripts/rate_limit_pilot.py`,
`scripts/stage_a_seed_sweep.py` and the `docs/BACKLOG.md` entry "Finish the Stage
A seed sweep, and give it somewhere to run". 173 and 33 insertions, no deletions,
byte-identical to what the stranded session wrote.

**The file now contradicts itself and that is deliberate.** At line 5221 it says
the rate caps "can therefore be closed without measuring them"; at 6215 it says
"the 18 LLM cells still wait on the rate-limit measurement". Both are what was
believed when written. Editing either would be the one thing this file forbids,
and the contradiction is the evidence for what follows.

**Why it happened, which is the part worth having.** The work was complete and
uncommitted in `.claude/worktrees/rate-limit-pilot`, on a branch 12 commits behind
`main`. Uncommitted work in a worktree is invisible to `git log`, to
`git status` in every other tree, to `scripts/status.py`, and to `/recall` —
which greps `docs/DECISIONS.md`, and the entries were sitting in that worktree's
*copy* of it. Every instrument this project uses to orient reported the blocker
as open, correctly, because none of them can see a file that was never committed.

**The record named the path and it still did not help.** Lines 5343-5347 read:
"A concurrent session was working this in `.claude/worktrees/rate-limit-pilot`
with uncommitted `scripts/rate_limit_pilot.py` and `scripts/stage_a_seed_sweep.py`;
this session stayed off it deliberately rather than duplicating the work." That
was the right call at the time. What no session then did was go back and ask
whether the concurrent work had *finished* — three later entries (5343, 5655,
6215) restate "still unmeasured" without checking a location one of them had
itself written down. The habit that would have caught it is cheap: when an entry
defers to a concurrent session by path, that path is a thing to re-check before
repeating the deferral, not a citation.

**Cost.** Two days of item 15 reported as blocked on a question already answered,
and a 38-cell campaign run and written up under that belief. Nothing was
recomputed twice and no number is wrong — the conventional cells never needed a
provider, as `main`'s own entry at 5999 works out. The cost was in what did not
get started.

**Measured, and it qualifies the pilot's cost figure rather than restating it.**
`rate_limit_pilot.py --dry-run` on today's tree gives a **4221.5-char median
brief** against the **4422** the pilot recorded on 2026-08-16. The brief moved
because the structural menu, `METRIC_VERSION` and the Stage A allocation all did,
exactly as that entry predicted when it declined to commit a corpus. So the
$0.098 median is a measurement against a 4422-char brief, and today's calls are
~4.5% shorter on input. The output tokens are the cost driver, so this does not
move the projection much — but the $0.098 should not be quoted as though it were
taken against the brief now being sent.

**Closes off.** It does not measure the 5-hour or weekly cap, and after the seed
sweep that is still not worth doing. `.cache/campaign/spec9.db` — the 38 recorded
cells, 760 rows — is copied from `.claude/worktrees/matrix-runner/` to the main
tree's `.cache/`, verified identical by row count and by a digest over every
`reading_digest` in `sequence` order, with the original left in place. It was
reachable only from inside that worktree, and `run_matrix.py` takes the ledger as
a path resolved against the working directory, so a resume from anywhere else
would have silently re-run all 38 rather than skipping them. Nothing about the
LLM cells is settled here; they are running as this is written.

## 2026-08-18 — item 15: the seed sweep measured a seed set the matrix never runs, and the LLM arm has no outcome for an unmeasurable proposal

**Supersedes the call-count projection in "the seed sweep: 142 calls, not 120,
and S11 fires at every seed"**, which was landed earlier today and is the entry
that unblocked the recording run. Its measurements stand; what does not is the
inference from them to the matrix's budget.

**Measured: the two seed sets are disjoint.** The sweep uses an arithmetic grid,
`scenario.seed + 1_000_000 * k` (`scripts/stage_a_seed_sweep.py`, `STRIDE`). The
matrix hashes: `replicate_seeds` returns
`stable_key(f"matrix/{scenario_seed}/{index}") % _SEED_MODULUS`
(`sciagent/eval/matrix.py:292-295`). Computed over 20 replicates:

| scenario | sweep seeds [:3] | matrix seeds [:3] | overlap |
|---|---|---|---|
| S2 | 20260902, 21260902, 22260902 | 5970087448106719972, … | **0 of 20** |
| S11 | 20260911, 21260911, 22260911 | 4320392470714962927, … | **0 of 20** |

So **142 is not the matrix's call count**, and the ~$14 and ~1.5h that follow
from it are not the matrix's budget. The sweep's control — "`k = 0` is the
scenario's own seed, so the first column must reproduce the recorded table" — is
sound for what it checked, item 12's single-seed runs at `scenario.seed`. The
matrix uses none of those seeds either.

**What the sweep still supports.** S11 firing 20/20 on an arbitrary 20-seed grid
is evidence the Stage A gate is not a knife-edge on one seed, which is the claim
SPEC §9's conditioned contrast actually rests on. That survives. The arithmetic
that turned a firing table into a call count does not.

**Corroborated by the run rather than by argument.** The campaign proposed on
**V7/S2 replicate 07**, and the sweep records V7/S2 firing **0/20**. A cell the
sweep says never fires, firing on the matrix's seeds, is the disjointness showing
up in behaviour within 27 replicates.

**A real gap: an unmeasurable proposal has no outcome, so it kills the
campaign.** The run stopped with
`StructureNotMeasurableError`: structure
`AddLatentVariable(arrival|two_state_markov|…)` executes, but design
`query:phase_conditioned_dispersion` cannot be measured on it — `phase bin 6
holds 1 window(s)` (`environments/pointproc/diagnostics.py:225`, raised through
`inference/empirical.py:435`).

`Hybrid._propose_once` converts `ProviderError` into `"refused"` and
`MalformedProposalError` into `"malformed"`; there is no third case, so this
escapes through `_admit` and out of `investigate`. The precedent is one file
away and points the other way: `beam_search.py:66` defines
`_UNSCORABLE = (ExecutionError, OutOfRangeError, StructureNotMeasurableError)`
and line 167 catches it, so an unmeasurable candidate costs B5 a rank rather than
the search. The comment at `empirical.py:445-452` records that this was learned
once already, when an `OutOfRangeError` escaping the same boundary aborted a beam
search.

This is reachable only by an arm that proposes structure outside the library:
V1, B1, B4 and B5 never expand the table with a new structure, which is why 38
conventional cells ran clean and why the defect surfaced on the first LLM cells
ever run in this tree.

**Work left incomplete, and what it waits on — a decision that is not mine.**
Giving the proposal taxonomy a fourth outcome would change what V7 scores: a run
that currently dies would instead record a non-admission and carry on. That is
evaluation apparatus, and CLAUDE.md's invariant 6 survives precisely as the rule
that such apparatus must not be shaped after watching the system it grades fail
against it. B5's handling is a strong precedent for what the answer should be,
and the precedent is not the same thing as the decision. **Stopped here and
asked.**

**State banked.** `.cache/campaign/spec9.db` holds **787 rows** — the 38
conventional cells' 760, plus 27 LLM replicates (V7/S1 complete at 20, V7/S2
through replicate 06). `run_matrix` skips what it holds, so this is a resume.
The live backend is validated end to end and was never the problem: one V7/S11
replicate through `AgentSdkProvider` took **113.0s** and recorded **2 calls**, at
`max_proposals=2`, into a separate `llm_smoke.db` so the smoke does not sit in
the campaign.

**Closes off.** It does not touch the 5-hour or weekly caps, which remain
unmeasured and — the one part of the superseded arithmetic that does survive —
still look far from binding. It says nothing about what the matrix's true call
count is; measuring that needs the sweep re-run over `replicate_seeds`, which
costs no quota and has not been done.

## 2026-08-18 — item 15: an unmeasurable proposal is a fifth outcome, not a crash

**Decision.** `Hybrid._admit` catches
:class:`StructureNotMeasurableError` and returns
`ProposalAttempt(..., "unmeasurable", ...)`, and `ProposalRecord` gains a fifth
required field to count it. An unmeasurable proposal now costs V7 a proposal and
the run continues, exactly as `BeamSearch._UNSCORABLE` costs B5 a candidate its
rank rather than the search. `_extend` does not break on it — only `"refused"`
breaks — so the second of `max_proposals=2` is still attempted.

**Why a fifth tier rather than reusing one.** Folding it into `"malformed"`
would blame the model for a faultless draft: the structure is licensed, on-grid
and executes. Folding it into `"refused"` would blame a provider that declined
nothing. Both would be recorded as facts about the proposal layer when the fact
is about the *pair* — this structure against this design set. `ProposalRecord`'s
existing docstring already makes exactly this complaint about `refused` carrying
two causes, so adding a third would have compounded a wart the file names.

**The taxonomy could not drift silently, and that is by prior design rather than
by care taken here.** `PROPOSAL_OUTCOMES` is derived from
`fields(ProposalRecord)`, and `proposal_record` raises
`InvestigationError` on an outcome outside it — "counting it would need a tier it
has not been given". So the new outcome could not have been silently dropped; it
would have crashed the agency metrics instead. Two existing tests
(`test_agency.py:297` and `:365`) assert `requested` equals the sum over
`PROPOSAL_OUTCOMES`, and both picked the fifth tier up with no edit.

**This is evaluation apparatus shaped after watching the system it grades fail
against it, and the user took that decision explicitly** when the alternative —
leaving item 15's LLM cells unrunnable and deciding cold later, as §12 criterion
4 and the withdrawn `ScenarioRun.adequacy` field were both handled — was put
beside it. Three things bound how far the hazard reaches, and none of them is
that the change is small:

* **The precedent predates the failure.** `_UNSCORABLE` has held this exact rule
  in `beam_search.py` since it existed, and `empirical.py`'s own comment records
  the lesson being learned once already, when an `OutOfRangeError` escaping the
  same boundary aborted a beam search. The rule was not invented to let V7 past.
* **No recorded number moves.** `CellReading` carries dimensions, score,
  `ppc_p_value`, `inadequate`, `experiments` and `structural_distance` — no
  agency metric — so the 760 conventional rows keep their readings and their
  content addresses. Nothing needed re-running.
* **It cannot flatter V7 against its comparators.** The tier is reachable only by
  an arm that expands the table with a structure outside the library, so V1, B1,
  B4 and B5 score zero there by construction, and the preregistered contrast
  (V7 vs B4 on S11, D3) reads none of these fields.

What it does change is what a V7 run *does*: a replicate that previously killed
the campaign now completes with one proposal spent and nothing admitted. That is
a different D1–D6 vector than the crash produced, which was no vector at all.

**Measured.** The failing structure was recovered from the error text by
inverting the parameter grids —
`fixed_payload(1, (32, 55, 29, 22))`, being `mult_low=0.1459`,
`mult_high=30.42`, `switch_rate=0.0504`, `p_high=0.3552` — and pinned as
`test_an_unmeasurable_proposal_leaves_a_usable_investigation`. It reproduced the
campaign stop exactly, through the same chain (`hybrid.py` -> `base.py:297` ->
`empirical.py:904` -> `:435`), failed before the fix and passes after. Full suite
green at `-n 4 --dist loadfile`: **1336 passed, 7 skipped, 150.00s**; `mypy`
clean over 125 source files; `ruff check` and `ruff format --check` clean.

**Closes off.** It says nothing about whether
`query:phase_conditioned_dispersion`'s eight-bin default is the right design —
widening the window or lowering the bin count would make more structures
measurable, and would move `METRIC_VERSION` and every address with it, which is
why it was not touched. It does not touch the diagnostic itself, so the same
structures remain unmeasurable; they are now merely survivable.

## 2026-08-18 — item 15: the proposal layer did not honour its own documented contract, and that is the second escape found by running

**Decision.** `ProposalLayer._build` re-raises `InvalidEditError` from
`EditGrammar.validate_defect` as `MalformedProposalError`. No new outcome tier:
`"malformed"` already exists, and `ProposalLayer.propose`'s docstring already
promised this exact error "when the payload does not denote a structure the
grammar licenses". The layer simply was not doing it.

**Why this one is not a taxonomy judgement.** `decode` refuses a menu index off
the menu, a parameter count that disagrees with the grids, and a grid index past
the end of its grid. What it does not check is the *pairwise* constraint, so a
draft whose edits are each licensed can still be invalid as a defect — two edits
on one target, whose compiled family would be ambiguous. `InvalidEditError` is a
`GrammarError` and its own docstring says "or conflicts with another edit in the
defect"; `MalformedProposalError` is a `ProposalError` and says "does not denote
a structure the grammar licenses". The second is the accurate description of what
happened, `_propose_once` catches it, and the contract said so before any of this
was measured. Changing which exception crosses that boundary makes the code match
a promise that predates the failure, rather than inventing a category to survive
one.

**Measured.** Stopped `V3/S11` after **9** replicates, on replicate 09. Pinned as
`test_two_edits_on_one_target_leave_a_usable_investigation`: structures 0 and 1
both target `arrival`, which is the clash the model produced. Failed before the
change through `provider.py:223`, passes after. `mypy` clean over 125 source
files; `ruff check` clean.

**The pattern is worth more than either fix, and it is not reassuring.** These are
**two distinct escapes in one afternoon**, both in the same place — a proposal
that fails in a way `Hybrid._propose_once` has no case for — and both found by a
live campaign stopping, not by a test. `StructureNotMeasurableError` came from the
*table* refusing a structure; `InvalidEditError` came from the *grammar* refusing
a defect. They share only that the LLM arm is the sole way to reach them: every
conventional arm proposes from a fixed library that was validated once, so 38
conventional cells over 760 replicates could not have found either.

What that suggests, and what this entry does **not** do: the proposal path's
failure taxonomy should be audited against the set of errors reachable from
`ProposalLayer.propose` and `Investigation.propose`, rather than extended one
crash at a time on a run that costs quota. Two data points do not establish that a
third exists; they do establish that neither was anticipated, and that the
instrument that finds them is expensive. That audit is a `docs/BACKLOG.md` entry,
not something to improvise while a campaign is mid-flight.

**Closes off.** It does not touch `decode`, which still checks what it checked;
the pairwise constraint stays where it is, in the grammar, and is merely reported
as the layer said it would be. It says nothing about whether a model proposing two
edits on one target is a prompting problem — the brief does not currently say the
targets must differ, and whether it should is a question about the brief, which
`METRIC_VERSION` does not cover but the call address does.

## 2026-08-18 — item 15: the matrix is complete at 56 cells, and V7's extension changes almost nothing it is scored on

**Decision.** SPEC §9's matrix is recorded in full: **56 cells, 1120 replicates,
20 per cell, no skips**, in `.cache/campaign/spec9.db` on the Windows desktop, at
`pointproc/pointproc/1.1.0+1.2.0+1.1.0` / `pointproc/generated/1.0.0` /
`metrics/9b1c54c9d49f49f656c30e32d21d4a7b`, partition `dev`. The 18 LLM cells —
V7 on twelve, V3 and V4 on three each — join the 38 conventional ones recorded
2026-08-17. **Exploratory by construction**, per §9.

**Measured: 112 live model calls**, against the **142** the superseded seed-sweep
arithmetic projected. The count decomposes exactly, which is the check that it is
not a coincidence: **56 firings x 2 proposals**. V7/S11, V3/S11 and V4/S11 each
fired **17 of 20**; V7 fired **5 times** across the other eleven scenarios (S2
once, S4 twice, S5 once, S6 once); V3 and V4 fired **0** on S8 and S12.

**The three arms firing identically at 17/20 is the 2026-08-16 deduction holding,
and it now covers V7 as well.** That entry argued V3 and V4 must fire alike
because `Hybrid` consults the gate before `_extend` and `Memory` enters only
inside `propose`. The same argument covers any two `Hybrid`s differing solely in
memory, so V7 belongs in it. Three arms, same 17 seeds, is that reasoning
measured rather than deduced.

**The sweep's firing table does not transfer, as the disjoint-seeds entry
predicted.** It said S11 fires 20/20 and named S3, S12, S5, S6 and S9 as the
others; the matrix fires 17/20 on S11, nothing on S3, S12 or S9, and fires on S2
and S4, which the sweep had at zero.

### The preregistered contrast has no answer, and that is the finding

```
contrast unavailable: no replicate of V7 on S11 detected inadequacy, so a
contrast conditional on inadequacy detection has no answer on this matrix.
That is a finding to report, not a number to compute
```

`report_matrix.py --contrast` exits 3. **V7/S11 records `inadequate` 0 of 20**
while V7 *acted* on the Stage A gate in 17 of those same 20 runs. Those are two
different checks — `CellReading.inadequate` is the whole-record posterior
predictive check, documented at `eval/campaign.py:104` as "**Not** the verdict a
system acted on where a scenario declares a Stage A probe" — and §9's contrast
conditions on the first while V7 is driven by the second.

This is the incoherence `docs/BACKLOG.md` records for SPEC §12 criterion 4,
reaching §9's primary contrast by the same route: the specification names
"inadequacy detection" and two checks answer to that name. **Nothing was changed
to make the contrast compute.** Switching the conditioning field is exactly the
decision that entry says must not be taken in a session that has just measured
V7, and this session has done nothing but measure V7.

### V7 is V1, to six decimals, everywhere it is scored

The result that most wants stating plainly. Comparing readings replicate by
replicate, V7 differs from V1 on **21 of 240** — precisely the 21 where it
proposed — and on S11 the differences are:

| field | largest \|V7 - V1\| over 20 replicates |
|---|---|
| `ppc_p_value` | 9.564e-03 |
| `d5_enabled_experiment_value` | 1.099e-04 |
| `leading_mass` | 8.757e-05 |

**D1, D2, D3, D4, D6, `correct`, `identified`, `truth_mass`, `log_score` and
`structural_distance` are bit-identical.** Seventeen proposals per S11 cell, and
the leading hypothesis never moved: D1 stays at **1.5000** for V1, V7, V3 and V4
alike, which is the plain-Hawkes library member that `docs/BACKLOG.md`'s grammar
sensitivity entry already flags as scoring *worse* than the null on S11.

**Correct rate, 20 seeds.** V7's row is V1's row.

```
         S1    S2    S3    S4    S5    S6    S7    S8    S9   S10   S11   S12
  V1   1.00  1.00  1.00  1.00  1.00  1.00  1.00  0.00  1.00  0.00  0.00  0.15
  V7   1.00  1.00  1.00  1.00  1.00  1.00  1.00  0.00  1.00  0.00  0.00  0.15
  B4   1.00  0.00  1.00  1.00  1.00  1.00  0.00  0.00  1.00  0.00  0.00  0.00
  B5   0.00  0.00  0.00  0.00  0.00  0.00  0.00  0.00  1.00  0.00  0.00  0.00
  B1      .     .     .     .     .     .     .     .  1.00     .  0.00     .
  V3      .     .     .     .     .     .     .  0.00     .     .  0.00  0.15
  V4      .     .     .     .     .     .     .  0.00     .     .  0.00  0.15
```

**On D3 unconditioned, V7/S11 is 0.6662 and B4/S11 is 0.5464.** Do **not** read
that as the preregistered contrast, which is conditional and has no answer here.
It is also not evidence about proposal quality: V7's 0.6662 is V1's 0.6662 to
four decimals, so what it measures is BOED with a library, not extension. V3 is
0.6652 and V4 is 0.6662 — the memory arms differ from each other by 1e-3, which is
the only number in this matrix that is about memory at all.

**Cost and duration.** 112 calls. The per-call cost was not re-measured here and
the pilot's $0.098 median was taken against a 4422-character brief where today's
is ~4222, so it should be quoted as an estimate rather than a bill. Wall clock,
summed from the runner's per-replicate lines: the three S11 cells dominate at
**34.8, 47.1 and 29.1 minutes** for V7, V3 and V4 — **110.9 minutes** between
them, for 60 of the 1120 replicates. The 15 other cells took 273 replicates, of
which 268 ran in 0.1-0.2s and five cost 90-215s apiece; those five are exactly
V7's five non-S11 firings. Cost tracks distinct structures, not replicates,
exactly as the 2026-08-17 entry concluded from the conventional cells.

**Three stoppages, none of them a rate limit.** Two were proposal-path escapes,
each recorded in its own entry above and each fixed before resuming. The third,
on V4/S11 replicate 13, was `Exception: Claude Code returned an error result:
success` — a bare exception out of the Agent SDK, past `run_matrix.py`'s
`except SciAgentError` and so printed as a traceback rather than the designed
"stopped after N replicate(s)". **It did not recur**: a plain resume completed
replicate 13 in 49.5s and the remaining six after it. Recorded as transient on
one observation, which is all the evidence there is. The checkpointing behaved as
its docstring promises through all three — every completed replicate was already
in the ledger, and no work was repeated.

**Closes off.** The subscription caps are still unmeasured and were never
approached: 112 calls over an afternoon. Nothing here re-specifies §12 criterion
4, and nothing here answers §9's contrast. What item 15 now has is the matrix
itself, and one result worth carrying into the frozen campaign: **on this slice,
the proposal layer does not move the dimensions it is scored on.** Whether that
is the grammar's distance being the wrong instrument (the R5 entry), the gate
conditioning on the wrong check (the criterion-4 entry), or V7 genuinely adding
nothing, this matrix cannot say — and those are three different follow-ups.
## 2026-08-18 — infrastructure: ad-hoc work is a first-class path, and the A-test lens is no longer `/next`'s

**Decision.** The test review that the 2026-08-17 entry above installed as
`/next` step 3 now lives in its own skill, `/test-review`, which `/next` calls
and which a direct request calls for itself. CLAUDE.md gains the ad-hoc path
explicitly: a request made in a sentence rather than a backlog row drops
`/next`'s §1 and §2 — the two sections that resolve the cursor and read the §11
row, which an ad-hoc request has nothing to resolve against — and keeps §3
entire. `/ship` §1 gains the matching commit form: ad-hoc work takes no
`(backlog item N)` suffix, because `/next` step 1.3 resolves the cursor by
grepping for exactly that string and a false one marks an item done. Nothing
about the lens itself changed — the agent choice, the accepts-and-rejects
question, the path-not-excerpt rule and the `/recall` guard all moved verbatim.

**Why.** Everything else `/next` does was already in CLAUDE.md and already
applied to any work: test first, watch it fail, `mypy` and the suite,
`suite-freshness` pinning, `/decide`, `/preflight`. Checked by grep —
`"reviewing a test, not the code"` returned exactly one file. The lens was the
sole step reachable only by invoking `/next`, so asking for something directly
silently bought the whole discipline minus the one review that cannot be run
later. The alternative was to leave it where it was and rely on remembering it
on the ad-hoc path; that is the class of thing this repository writes down
precisely because remembering does not scale.

**Read the 2026-08-17 entry as scoped, not wrong.** It settles what the lens
asks and why, and all of that stands. What it no longer settles is *where the
lens fires* — a fresh session reading it alone would conclude the lens is a
`/next` feature, and since entries are never edited, this is the forward link
that says otherwise.

**Closes off.** It rules out `/next` becoming the mandatory route to writing
code here. `/ship` never checked for `/next` and still does not — it requires
only that `/preflight` has run — so the ad-hoc path reaches a commit through
exactly the same gate.

**Left incomplete, and what it is waiting on.** `/test-review` has never been
invoked. It was authored in a worktree, and the skill namespace fact recorded on
2026-08-17 applies to it: a skill authored in a worktree is invisible to that
worktree's own session until its branch reaches `main`. So it cannot be
exercised before the merge, and its first real use is also its first test. Both
`/next` and CLAUDE.md therefore carry the `Unknown skill` fallback — open the
file and carry it out inline — because the window where that fires is the window
where the step is easiest to lose.

## 2026-08-18 — infrastructure: the CLAUDE.md trim was attempted, reviewed twice, and abandoned

**Decision.** CLAUDE.md is not being trimmed. An audit cut it from 22,696 to
21,224 characters — 6.5%, about 400 tokens a session — by removing the ten
slash-command bullets, two `Stack` lines derivable from `pyproject.toml`, and
the forensic receipts behind four rules. Two independent `/code-review` passes
over that diff returned nine and then fifteen findings. The whole trim was
reverted. What survives is one fix that was never part of it: the stale
`docs/DECISIONS.md` figures, below.

**Why — the decisive case.** Invariant 6 was compressed from eight lines to
four. The original carries **two** prohibitions, not one: *"anything that would
move evaluation apparatus after the agent that is scored by it reopens the
confound"* **and** *"a new gate written after the system it grades is not [in
bounds]"*. The first compression kept only the broad clause, which made the
headline an absolute that `c4dcee3` — the item 15 matrix, which grades V7 and
was written thirteen days after it — already violated; a session reading it
literally would have had to refuse the remaining LLM cells. The second, written
to fix that, kept only the narrow clause, which would have **permitted the SPEC
§12 criterion 4 rewrite that this file records as withdrawn on 2026-08-16 for
violating invariant 6**. An exit criterion is not an acceptance test, so "gate"
alone does not reach it. The two wordings, quoted because the trim was reverted
and exists in no commit — too broad: *"Evaluation apparatus is never written
after the system it grades."* Too narrow: *"A gate is never written after the
system it grades."* Two attempts, opposite failures, and the second was
worse than the first because it silently re-opened a violation the repository
had already paid to catch.

**Why — the general reason.** Compressing prose is safe; compressing a
prohibition changes what it prohibits, and this file's prose is densely
cross-referential in ways not visible from the line being cut. Other instances
from the same two reviews: a restored line reading *"acceptance criteria get
property-based tests"* is false — `grep -rn '@given' tests/` returns 6 against
343 `test_a*` functions; removing the cd-prefix magnitude left *"Measured across
50 transcripts; none of the prefixed commands needed it"*, a measurement with
its result deleted, which says less than no measurement; a pointer replacing the
`mypy`-takes-no-arguments explanation aimed at a section containing no `mypy`
guidance. Each was introduced by a round of fixes for the previous round's
findings. The failure mode is not carelessness about any one line — it is that
the reviews kept being right, and the edits kept generating fresh work.

**Closes off.** It rules out re-proposing a CLAUDE.md size reduction on
token-saving grounds alone. ~400 tokens a session is not worth a non-negotiable
invariant changing scope, and nothing in the audit found a way to get the first
without risking the second. If it is ever attempted again, invariant 6 is the
test case: any compression that does not preserve **both** its clauses is wrong,
and the two ways of getting it wrong are recorded above.

**What was kept, and why it is unrelated.** CLAUDE.md and `/recall` described
`docs/DECISIONS.md` with figures stale by roughly a factor of two — "~190KB
across ~90 entries ... about 50k tokens" in one place, "226KB" in another,
"over 220KB across more than 100 entries ... upwards of 55k tokens" in
`/recall`, whose fan-out slice sizing said "roughly 25 entries and 950 lines
each". Measured 2026-08-18 with this entry in place, over the git blob (LF), which
is 6,171 bytes smaller than `wc -c` on the CRLF working tree: **~370,000 bytes,
~6,180 lines, 135 entries**, most entries under 60 lines but one in ten over 80 and the longest 185,
and 16 entries carrying no scope. A session trusting the 50k figure as a budget and reading the
file whole would have spent about 90k. The slice figure was stale rather than
harmful — `/recall` anchors its fourth range at the end of the file, so a
literal four-way cut still covered everything; what 950 understated was how much
each sweeper had to read, which pushes a slice toward `Read`'s default window.
Two counting traps are worth recording, because both bit this fix: `grep -c
'^## '` returns 136, one more than the 135 real entries, since the preamble's
fenced entry-format template matches the same pattern; and the scope-less count
is 16 by the colon test, not the 17 that same template inflates it to. The
figures are approximate by that file's own instruction not to restate them as
exact.

**Left incomplete, and what it is waiting on.** Two things.

The three-file plan gate. The user settled in this session that the rule should
bind `/next` as well — a backlog row authorises the work, not the edits — but
**nothing in the repository carries that yet**, so it is an intention and not
in force: CLAUDE.md's rule is unchanged and names no skill, and
`.claude/skills/next/SKILL.md` has no plan step at all (`grep -in plan` returns
nothing). The sentence drafted for it was reverted with the trim, and had a
defect worth fixing before it returns: it read "once an item is clearly spanning
more than three files, stop", which licenses editing until the count becomes
clear, against a base rule that says "before editing anything". Landing it means
both files, not one.

Whether block-level HTML comments are stripped from CLAUDE.md before injection:
documented at `code.claude.com/docs/en/memory.md`, never measured here. It
mattered because the trim's receipts were moved into such a block, and the
saving was 6.5% if the stripping happens and 0.4% if it does not. With the trim
reverted nothing depends on it, but the next audit that reaches for the
mechanism should measure it with `/context` first rather than inherit the
assumption.

## 2026-08-18 — infrastructure: the three-file plan gate is in force, in both files

**Decision.** The rule binds `/next` as well as ad-hoc work, and it now exists in
the repository rather than as an intention. `CLAUDE.md`'s working-defaults bullet
and `.claude/skills/next/SKILL.md` §3 both carry it. This discharges the first of
the two items the CLAUDE.md-trim entry above left incomplete.

**Why.** The entry above records the wording that was drafted and reverted with
the trim, and its defect: *"once an item is clearly spanning more than three
files, stop"* licenses editing until the count becomes clear, against a base rule
that says *before editing anything*. Both files now gate on the **estimate**,
made before starting. The formulation that carries the point is that a §11
backlog row authorises the *work* and not the edits — and that step 1's A-test is
an edit like any other, which is the case a `/next` session would otherwise treat
as exempt.

**Closes off.** Nothing further. The second item that entry left open — whether
block-level HTML comments are stripped from `CLAUDE.md` before injection — is
untouched and still unmeasured.

## 2026-08-18 — item 15: the proposal path's escapes are a class, and the third stoppage was not transient

**Decision.** Four guards on the proposal path were narrower than the errors
behind them. All four are widened, each to a boundary named explicitly rather
than to the enclosing family. The code and its reasoning are in the diff; what
follows is what the diff does not carry.

**The third stoppage was this defect, not a transient.** The entry above records
item 15's V4/S11 replicate 13 dying with `Exception: Claude Code returned an
error result: success`, and concludes "recorded as transient on one observation,
which is all the evidence there is". There is more evidence now.
`claude_agent_sdk` raises **bare `Exception` from seven sites** in
`_internal/query.py` (`:441 :496 :512 :524 :563 :598 :965`), and `:965` is
`raise Exception(message.get("error", "Unknown error"))` — the shape of that
message exactly. It did not recur because a resume drew a different session, not
because the cause had cleared. **Supersedes the transient reading**; it was an
unguarded seam, and it would have recurred.

**529 is `OverloadedError`, not `InternalServerError`.** The fact that decided
where the Messages API guard goes, and the one most likely to be got wrong again.
Measured against the installed SDK via `_make_status_error_from_response`:

| status | class |
|---|---|
| 400 | `BadRequestError` |
| 401 | `AuthenticationError` |
| 404 | `NotFoundError` |
| 429 | `RateLimitError` |
| 500 | `InternalServerError` |
| 529 | `OverloadedError` |

`OverloadedError` is a **sibling** of `InternalServerError` under `APIStatusError`,
not a subclass. A guard reasoning from "5xx" therefore misses the 529 that a long
recording run is likeliest to meet. The guard is written at `AnthropicError`, the
SDK root, for that reason — and the root is both floor and ceiling there because
that SDK raises nothing bare.

**A deliberate, single exception to "never catch bare `Exception`".** The Agent
SDK guard catches it, preceded by `except SciAgentError: raise`. It cannot be
narrowed to `ClaudeSDKError`, which converts none of the seven sites above. It is
not suppression: the original is chained on `__cause__`, its type name is in the
message, and the `SciAgentError` clause makes it impossible for a framework error
to enter the broad clause at all.

The reason that clause is load-bearing is worth stating, because it is the
opposite of the obvious worry. A `ProviderError` is caught by
`Hybrid._propose_once` and recorded as `"refused"` — so a framework fault
converted here would **not** stop a campaign, it would let one continue and write
a scientific outcome for a bug. That is strictly worse than the crash the guard
exists to prevent, and it is why `DeterminismError` is excluded from
`empirical.py`'s new `CANDIDATE_FAULTS` by the same argument: an invariant-3
violation filed as `"unmeasurable"` is a campaign carrying on past a failed
determinism guarantee.

**This is an override of `.claude/rules/python.md` and of CLAUDE.md's own
working default, taken in one place and recorded so it can be reversed.** If it
is reversed, the seven sites are what has to be answered instead.

**The abandoned test design, which `/test-review` caught and was right about.**
The first draft of the transport tests asserted five and three **leaf** exception
instances while claiming to establish coverage of two hierarchies. A guard
enumerating exactly those leaves would have passed every one of them. The
docstring defending the choice had the reasoning inverted: converting a *base*
implies its subclasses, while converting subclasses implies nothing about the
base, so the draft picked the weaker direction for the very property it named.

Sharpest instance, and the reason this is recorded rather than merely fixed: the
draft hand-built `InternalServerError` with a 529 response to represent an
overloaded API. Since the SDK maps 529 to `OverloadedError`, **the scenario the
test named was the one it did not cover**, and it would have gone green against a
guard that let exactly that failure escape. Fixed by parametrising over the base
classes as well, and by adding bare `Exception` for the Agent SDK.

**A watched failure that proved nothing, for the record.** The first run of the
`CANDIDATE_FAULTS` test failed on all five cases including the control — because
`with_structure` returns early at zero cost for a structure the table already
holds, so `closed_set()["null"]` never reached the guard under test. A red from a
fixture that never entered the code under test is indistinguishable from a real
one in the summary line. The helper now asserts the candidate is off-table before
using it.

**Closes off.** It does not close `docs/BACKLOG.md`'s proposal-taxonomy entry:
`ProposalRecord.refused` still conflates a decline with a transport failure in
its **count**, and only the `detail` string now separates them. Changing the
count means adding a field, which moves `yield_fraction`'s denominator and so
touches what SPEC §12 criterion 11 reads — invariant 6 territory, deliberately
left for a cold decision.

## 2026-08-18 — the pilot's four reporting defects are fixed, and its numbers still describe the old script

**Decision.** `scripts/rate_limit_pilot.py`'s four `/code-review` findings are
fixed. Three are in the diff and need nothing here. The fourth is worth a line
because the fix is not the obvious one: `ok` no longer gates on
`cost_usd > 0.0` but on `outcome != "ProviderError"`, because served-ness is a
property of the outcome and the cost is exactly the field that cannot be trusted
here — this backend authenticates by subscription with `apiKeySource` of
`"none"`, so a zero from the SDK's costing is the case to expect. The old
predicate discarded the whole measurement *after* the quota had been spent.

**Left incomplete, and what it is waiting on.** The confirming re-run.
`docs/BACKLOG.md` says a session not mid-campaign should fix these *and* re-run
the pilot, and only the first half is done: re-running costs live calls. So the
pilot's recorded numbers — the $0.098 median in particular — describe the script
**as it was**, and the fixes are unverified against a live session. The
`--dry-run` path was exercised and still completes, which establishes no
regression and nothing about the zero-cost predicate, since a scripted backend
reports zero cost and the old code special-cased dry runs to compensate.

**Closes off.** Nothing depends on it. The exit codes now separate the two
events the script exists to tell apart — 1 for a provider failure that stopped
the run, 3 for a completed run with rejected drafts — so a caller can finally
distinguish them, which nothing could before.

## 2026-08-18 — two open decisions are written up cold, and neither is taken

**Decision.** `docs/OPEN-DECISIONS.md` is new and states two decisions this
repository has deliberately not taken: SPEC §12 criterion 4's re-specification,
and the proposal outcome taxonomy. It changes no SPEC text and no code. Both
`docs/BACKLOG.md` entries now point at it.

**Why a document rather than the decisions themselves.** Invariant 6. Both touch
apparatus that scores a system already measured, and this file records the
repository paying twice for taking such a decision warm. The write-up is the part
that *can* be done cold-safely, because it is the enumeration of options rather
than a choice among them; separating the two is what lets a later session decide
without first re-deriving the measurements.

**One finding from writing it, which is not in either §.** The two decisions are
**coupled, and in a specific order**. Retiering `"refused"` requires a metric
version bump if it moves `yield_fraction`'s denominator — but that bump is free
if the matrix is being re-scored anyway, which is exactly what R5's
distance-grammar variant would do. So the taxonomy decision should be taken
*after* the R5 re-scoring question and not before it, and taking it first would
buy a parallel reporting structure that a re-run would immediately make
redundant.

**Closes off.** It does not close either backlog entry: both remain open, with
the write-up done and the decision outstanding. It rules out re-deriving the
criterion 4 measurements, which are now in one place rather than spread across
three `docs/DECISIONS.md` entries.

## 2026-08-18 — the transport guard was wrong in its first form, and the review caught it

**Supersedes** the transport half of "the proposal path's escapes are a class",
above. The defect and the fix both matter; the entry above describes the fix
that was reviewed and rejected.

**Decision.** A transport failure raises the new
`ProviderUnavailableError`, **not** `ProviderError`. Both are `ProposalError`s
and both are `SciAgentError`s; the difference is that `Hybrid._propose_once`
catches the second and not the first.

**Why the first form was worse than the crash it replaced.** `ProviderError` is
caught and recorded as `"refused"`, so `_extend` broke, `investigate`
**completed**, and `run_matrix` checkpointed a scored reading into the ledger for
a replicate whose model was never reached. Three things follow, and none of them
followed from the crash:

- The reading is **permanent**. Invariant 4: the ledger has no update path.
- It is **unreproducible**. `TranscriptStore.resolve` stores nothing when the
  call raises, so a replay recomputes the same address, misses, and raises
  `TranscriptMissError`.
- A `--verify` pass on a healthy network re-runs the cell, gets a different
  reading, and raises `RegistryConflictError` — whose own docstring reads *"that
  is a framework bug and never a finding"*. It would have been neither.

So the first form traded "loses transcripts, records nothing" for "keeps
transcripts, records a wrong number that cannot be re-earned". Propagating gets
both halves: still a `SciAgentError`, so `run_matrix` stops cleanly with every
completed replicate already checkpointed — which is all the guard was ever for.

**The reasoning was available and I did not apply it.** The same change's own
ceiling argument — *a `ProviderError` is caught, so a fault converted here would
let a campaign continue and write a scientific outcome for a bug* — is written
into `agent_sdk_provider.py` and into the entry above, and a test was written to
pin it. It was applied to framework faults and not to transport, though a 429 is
no more a statement about a model's proposals than a `TypeError` is. Two
independent reviewers found it; neither was told to look for it.

**`MemoryError` joins the pass-through clause.** Not a `SciAgentError`, so the
typed clause missed it, and this machine is recorded dying at around 300
half-investigations in one process. It would have been scored as `"refused"`.

**Left open deliberately, and recorded in `docs/BACKLOG.md`.**
`CANDIDATE_FAULTS` still misses `EditNotInGrammarError` from `EditGrammar.apply`
(`core/edits.py:483`, `:487`). Unreachable while `agent_grammar() ⊆
edit_grammar()` — which is the wiring argument this very session rejected as a
reason to leave a guard narrow, so it is inconsistent to leave and is left
anyway: whether a grammar refusal at *apply* time is a candidate property or a
grammar divergence decides which class it takes, and that is a decision to make
with the retiering rather than by widening a tuple in passing.

**Closes off.** It makes the transport class a *third* standing of proposal
outcome, beside "recorded and scored" and "stops the run as a configuration
fault" — and `docs/OPEN-DECISIONS.md` §2 now lists T1a, moving further
conditions out of `"refused"` rather than recounting them within it, which did
not exist as an option before this fix demonstrated it.

## 2026-08-18 — a sibling class is not a subclass, and three comparisons quietly stopped matching

**Decision.** `scripts/rate_limit_pilot.py` names `ProviderUnavailableError`
explicitly everywhere it names `ProviderError`, through a `STOPPED_OUTCOMES`
constant rather than by repeating the pair.

**Why it is worth an entry.** Making the transport class a *sibling* of
`ProviderError` rather than a subclass is what makes `Hybrid` not catch it — the
whole point of the previous entry. The cost is that **every existing site keyed to
`ProviderError` silently stopped covering transport**, and the compiler cannot
see it because two of the three sites compare *strings*: `CallRecord.outcome`
holds `type(error).__name__`.

The three, all in the pilot's `report`/`main`:

| was | consequence if left |
|---|---|
| `outcome != "ProviderError"` | a stopped run counted as **billed**, inflating the cost table's denominator |
| `outcome == "ProviderError"` | the **"THE RUN STOPPED"** block never printed |
| `outcome == "ProviderError"` | exit code 0 on a run that stopped |

And the `except` clause itself, which is the sharp one: a rate cap would have
propagated out of `run_pilot`, past a `main` with no handler around it, so
`report` and `_save_transcripts` never ran and **every call already billed in
that process was lost** — in the one script whose stated purpose is to be
running when a cap bites. Found by the determinism lens, which flagged it as
tangential to its own question and was right to report it anyway.

**Closes off.** It is the general hazard of the sibling design, and the pilot was
only the first place it landed. Anything added later that keys on `ProviderError`
— a catch clause, a string comparison, a report filter — has to decide about the
transport class deliberately, and `grep -rn 'ProviderError'` is the check. The
constant exists so that the decision is recorded in one place rather than
re-derived at each site.

## 2026-08-18 — the third review round, and the enumerate-versus-family rule that was missing

**Decision.** Five more defects fixed, and one principle written down that the
change had been applying inconsistently without stating.

**The principle, because two guards in one change were fixed two different ways
and nothing said why.** `ProposalLayer._build` catches `GrammarError` whole;
`EmpiricalTable.with_structure` enumerates four `ProgramError` members. The rule
distinguishing them: **catch the family when every member means the same thing;
enumerate when the family contains a framework fault.** Every `GrammarError`
reaching `_build` says "the grammar refuses this defect". `ProgramError` contains
`DeterminismError`, which says the framework is broken.

The reviewers split on this, which is why it is worth recording. One argued for
`except DeterminismError: raise` before `except ProgramError`, since that covers
every future sibling automatically and needs nobody to remember. The other argued
enumeration fails safe. Enumeration wins on the direction of the failure: catching
the family absorbs the *next* sibling silently as `"unmeasurable"`, and if that
sibling is another framework fault the result is a campaign scoring a bug — the
failure this whole class of change exists to prevent. Enumeration lets it escape
and stop the run, which is loud and recoverable. Recorded in the constant's own
docstring.

**Four defects, all of the same shape as the ones the change was fixing.**

- **A missing `claude_agent_sdk` raised the recorded class.** The Messages
  backend's sibling guard was moved to `ProviderUnavailableError` and this one was
  not, so an absent *package* would have been scored as `"refused"`. The
  asymmetry existed because the two backends translate exceptions independently
  with no shared helper — which is the reviewers' structural point, and this was
  its concrete cost.
- **A dead duplicated guard, raising a different class for the same condition.**
  Adding an `ImportError` guard to `AnthropicProvider.complete` made
  `_messages`'s unreachable, and the two disagreed about which class "SDK
  missing" takes. One guard now, in `complete`.
- **`environments/pointproc/tables.py`'s `search_table` caught two exceptions
  that cannot arrive.** `except (ExecutionError, OutOfRangeError)` — both of
  which `with_structure` converts to `StructureNotMeasurableError` before either
  escapes. So its docstring's promise, that unmeasurable candidates are skipped
  rather than fatal, was kept by no code at all. **Predates this change** and is
  the oldest instance of the pattern found: a clause naming the exception
  somebody had in mind rather than the one the boundary raises.
- **Two test names asserted the opposite of what they said.**
  `test_a_transport_failure_becomes_a_provider_error` asserts
  `ProviderUnavailableError`. Cosmetic in effect, and not cosmetic in kind: the
  entry above nominates `grep -rn 'ProviderError'` as *the* check for this class
  of gap, and a misleading name defeats exactly that.

**A miscount, in a file where figures are load-bearing.** Two docstrings said
`validate_defect` can raise "six" `GrammarError` subclasses and that "five" are
unreachable. There are **four** (`EditNotInGrammarError`, `InvalidEditError`,
`OffGridParameterError`, `UnknownParameterError`), so three are unreachable.
Measured with `GrammarError.__subclasses__()`.

**Declined, with reasons.** Two suggestions were not taken.
`_stored_address`'s before/after store diff could be `call_address` recomputed
from inputs the script already holds — but the diff encodes *an address exists
iff a transcript was stored*, which is precisely the joinability property the fix
needed, whereas recomputing would hand out addresses for calls that stored
nothing. And the broad `except` in `agent_sdk_provider` still spans `drain`'s own
two `isinstance` checks rather than only the SDK's iterator; scoping it tighter
means a manual `__anext__` loop, and the consequence of a local bug landing there
dropped from "scored as refused" to "stops the run with a wrong message" the
moment the class began propagating.

**Closes off.** Three review rounds found defects in this change; every round
found at least one, and the last found the oldest instance of the pattern in code
the change did not touch. The rate is the finding worth carrying: a guard whose
`except` clause was written from memory of a failure rather than from the
boundary's contract is a recurring defect in this repository, not an incident.

## 2026-08-19 — editor and terminal setup: what the files cannot tell you

Most of this session's work is derivable from its own diff — the settings, the
extension list, the palettes and the reasoning are all in the files, commented.
Four things are not, and one of them is a trap that fails silently.

**The installed Nerd Font is not called what the documentation says.** The
upstream Nerd Fonts patcher names families `<CamelCase> Nerd Font`, and its
readme states the rule explicitly with a worked example. The winget package
`DEVCOM.JetBrainsMonoNerdFont` 3.3.0 does **not** follow it: enumerating
`System.Drawing.Text.InstalledFontCollection` after installing shows
`JetBrainsMono NF` (double-width icons), `JetBrainsMono NFM` (single-width),
`JetBrainsMono NFP` (proportional), plus a `JetBrainsMonoNL` no-ligature flavour
of each — 42 families, none of them named `JetBrainsMono Nerd Font`.

**Why that matters more than a naming quibble.** A `fontFamily` naming a font
that does not exist produces no error, no warning and no log line; the editor
falls through to the next entry in the chain. The first version of
`%APPDATA%\Code\User\settings.json` written this session asked for
`JetBrainsMono Nerd Font` and would have rendered as Cascadia Code indefinitely,
looking merely disappointing rather than broken. Verify a font by enumerating the
installed collection, never by trusting the package name or the upstream docs.

**Tokyo Night was chosen as the default theme and then abandoned before it
shipped.** It ships `semanticTokenColors` with eight selectors but never sets
`"semanticHighlighting": true`. VS Code's `editor.semanticHighlighting.enabled`
defaults to `configuredByTheme` and the theme-side property defaults to false, so
every one of those rules is inert unless the setting is forced on. Its repository
was last pushed 2025-02-05 with 13 unanswered issues. Catppuccin was pushed
2026-08-18 and carries genuinely Python-aware selectors — `class:python`,
`class.builtin:python`, `variable.typeHint:python`, `function.decorator:python`.
For a Python repository that made the choice evidential rather than aesthetic.
Both are installed; Mocha is active. `editor.semanticHighlighting.enabled` is
forced true regardless, which also revives Tokyo Night's dormant rules if it is
ever selected. Note also that `Avetis.tokyo-night` is a different publisher
shipping an identically-named extension — `enkia.tokyo-night` is the one meant.

**Work left deliberately incomplete: the global Claude Code settings are
unwired, and this repository is why.** `.claude/settings.json` denies
`Edit(~/.claude/settings.json)`. That is a hard block rather than a prompt, so no
session working in this repository can write global Claude Code settings, and
verbal permission does not lift it. Consequently `~/.claude/statusline.sh` and
`~/.claude/themes/{catppuccin-mocha,tokyo-night}.json` exist and are valid but
nothing references the statusline, and `preferredNotifChannel` is unset — which
matters because the VS Code integrated terminal receives no desktop notification
(only Ghostty, Kitty and iTerm2 do). Waiting on either a paste into that file by
hand, `/statusline` and `/config` run from inside a session, or a deliberate
relaxation of the deny rule. Routing around it via `WebClient` or
`Start-BitsTransfer` was available and declined: a deny rule the user wrote is
not an obstacle to be engineered past.

**Two smaller facts that would cost a future session time.** `winget install
Microsoft.PowerShell` delivers 7.6.5 as an **MSIX** package, so `pwsh.exe` lives
under `%LOCALAPPDATA%\Microsoft\WindowsApps\Microsoft.PowerShell_8wekyb3d8bbwe\`
and **not** `C:\Program Files\PowerShell\7\` — a hardcoded Program Files path
fails. And measured on this desktop: pwsh 7 starts in **804ms** with the starship
profile against **299ms** at `-NoProfile`, so the prompt costs about 505ms per
shell. Windows PowerShell 5.1 is deliberately left with no profile, because
Claude Code's PowerShell tool runs 5.1 and should not pay that on every call.

**Closes off.** The font finding generalises past fonts: three of this session's
surfaces fail silently rather than loudly — an unknown Claude Code theme token is
ignored, an invalid theme colour is ignored, and a missing font family falls
back. In all three "it looks like nothing happened" is the failure mode, so each
was verified by enumerating what the system actually accepted rather than by
reading the file back. Nothing here touches `src/` or the acceptance gates; the
suite was not run, and no test covers `.claude/hooks/`, so `bash -n` plus direct
execution against captured payloads is the whole of the available check.

## 2026-08-19 — statusline render cost, and a guard written from intent

Follow-up to the entry above, recording what its review found. Both items are
things the diff no longer shows, because the review's fixes removed them.

**Measured: the first version doubled statusline render time.** Twenty renders,
warm, identical payload, two trials each. Before the change **296–366ms** per
render; with three `hook_field`-style extractions **581–596ms**; after replacing
them with `BASH_REMATCH` **367–368ms**. The extractions alone accounted for
~168ms, from nine subprocesses per render — three `printf|sed|head` chains. The
figure matters because the script's own header rejects `scripts/status.py` at 4.2s
on precisely this ground, and a statusline is debounced at 300ms, so a 200ms
regression sits inside the interval it re-renders on. `hook_field` remains
`sed`-based and is correct to: a PostToolUse hook fires once per edit and can
afford three subprocesses, where this cannot. Cost is per-*call-site*, not
per-helper.

**A guard whose comment asserted the opposite of the measured behaviour.** The
context-percentage segment guarded on `printf '%.0f' "$ctx"` producing empty
output for a non-numeric value, and said so in a comment. `printf '%.0f' abc`
prints `0` with a nonzero status; the status was discarded, so the guard was dead
and a malformed value would have rendered a confident red `0%` rather than being
skipped. Fixed by validating the shape at extraction instead, which makes the
condition unreachable rather than merely unlikely.

**Why that is worth an entry rather than a line in the diff.** The entry
immediately above this one closes by nominating exactly this defect class as
recurring in this repository — "a guard whose `except` clause was written from
memory of a failure rather than from the boundary's contract". This is the same
error in `bash` rather than Python, committed one entry later, in a file whose
whole purpose was to be verified by direct execution. Reaching for what a
primitive *ought* to do on bad input, instead of running it once, is the shape to
watch for; `printf` was two seconds away from being tested.

**Also fixed, without needing an entry each.** `hook_field_num` truncated
exponential notation, so a cost of `1e-7` rendered as `$1.00` — a seven-decade
error, now `$0.00`. `.vscode/settings.json` had excluded `.claude/worktrees/**`
from `files.watcherExclude` two blocks after a comment explaining why worktrees
must stay visible; the watcher does not read `.gitignore`, so that would have
blinded the editor to the only tree that changes mid-session. And its interpreter
pin was Windows-only in a file tracked specifically so Ubuntu cloud sessions read
it; removed entirely, since deleting the *global* override was the actual fix and
the Python extension auto-discovers `.venv` on every platform.

**Closes off.** Nothing here is covered by a test, and nothing can be: no test in
this repository reaches `.claude/hooks/`, which is why the review's method —
extracting the payload constructor from the shipped `claude.exe` to confirm
`remaining_percentage` is an integer 0–100 rather than a 0–1 fraction — was the
only way to establish that the `<10` and `<25` thresholds point the right way.
That contract is now depended upon by two scripts and is not written down
anywhere in the repository except here.

## 2026-08-19 — tried and failed: env-var venv activation in the VS Code terminal

**Symptom.** Opening an integrated terminal shows the Starship prompt in about
700ms, and then several seconds later
`(Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned) ; (& ...\.venv\Scripts\Activate.ps1)`
appears at that prompt and runs. The delay is not the shell starting: measured
here, `pwsh -NoProfile` is ~330ms and the Starship profile adds ~380ms, both of
which are spent before the prompt is drawn. The visible junk arrives afterwards,
because the Python extension activates an environment with `sendText` — it waits
for shell integration, then types the command.

**Tried, and it did not work.** `ms-python.python` 2026.4.0 offers an opt-in
experiment, `pythonTerminalEnvVarActivation`, whose stated purpose is to activate
by setting environment variables at terminal creation instead of typing a
command. Enabled it as `"python.experiments.optInto":
["pythonTerminalEnvVarActivation"]` in user settings, with
`python.experiments.enabled` at its default true. After a window reload and a new
terminal, **the typed command and the delay both persisted.** The enum member is
spelled correctly — it is one of six the extension declares — so this is the
experiment not taking effect rather than a typo.

**Left deliberately incomplete.** The setting is still in
`%APPDATA%\Code\User\settings.json`, where it is currently inert and carries a
comment claiming it fixes the problem. That comment now overclaims, which is the
defect the entry two above this one nominates as recurring here, so it is written
down rather than left to be rediscovered. Untouched for now at the user's
explicit direction — the next attempt should either delete the key and its
comment, or replace both with a platform-keyed
`terminal.integrated.env.windows` / `.linux` block injecting `VIRTUAL_ENV` and a
`PATH` prefix directly, which needs no experiment and no typed command.

**Closes off.** Do not re-enable the experiment expecting a different result, and
do not reach for `python.terminal.activateEnvironment: false` on its own: a
Windows Store Python 3.11 is on this machine's PATH, so switching activation off
without also injecting the venv leaves bare `python` resolving to 3.11 inside a
project pinned to >=3.12. That is a worse failure than the cosmetic one being
fixed.

## 2026-08-19 — gate A26: the version term the eval layer needed, and a gate that had to grow

**Decision.** The §8 dimensions carry their own version,
`sciagent.eval.scoring.DIMENSION_VERSION = "spec8/2"`, held in the cell key's
`config` exactly as `MATRIX_VERSION` is and checked by `report._at_address`. The
backlog entry that asked for A26 had prescribed a `METRIC_VERSION` bump instead;
that is the abandoned approach below.

**Why.** No existing address term moves when a dimension's definition changes.
`env_version` and `data_version` describe the environment and its data, and
`metric_version` is a content hash over the *environment's diagnostic
catalogue* — while D1–D6 are computed in `sciagent/eval/scoring.py` from the
truth and the table. So before this constant, changing D2 or D4 moved no address
at all, and `run_matrix`'s `skip_recorded` default would have reported the stale
reading as the new campaign's without executing anything to disagree with it.

**Tried and abandoned: bumping `METRIC_VERSION`.** It reaches every
`Discretisation`'s content hash through `str(MetricRef)` — `"name@version"` — so
it addresses the *empirical tables*, not only the ledger. Probed before changing
anything:

| `METRIC_VERSION` | one axis | its outcome space |
|---|---|---|
| `1.2.0` | `bins/2ac271cff162c232` | `outcomes/6cf306b0f598cf0f` |
| `1.3.0` | `bins/4630ff0d33995d59` | `outcomes/a29d5b818d9d3634` |

`EmpiricalTable.version` folds in `outcome.version`, and `cache_key` folds in
that, so a bump is a cache miss on every table. The gate table costs **3m11s**
cold against **1.055s** warm — paid in every worktree and on every machine, to
reproduce rows that are bit-identical because no estimator changed. This is not
hypothetical: `.cache/tables/` holds three gate-table and three search-table
generations, and the 2026-08-16 bump to 1.2.0 caused one of them. That bump was
*correct* — a metric had actually been added — which is the distinction worth
keeping: the metric version means the catalogue moved, and borrowing it to mean
"a dimension moved" costs a rebuild every time and says something false.

Landed in `config` rather than as a fourth `CampaignAddress` column, following
`MATRIX_VERSION`'s precedent, so no ledger schema change and no fixture churn.

**Left deliberately incomplete.** Rows recorded before today carry no
`dimensions` key, so `_at_address` now excludes them and `report_matrix` raises
rather than rendering — the message names which readings the ledger actually
holds. **The recorded 1,120-row matrix is therefore unreportable until it is
re-derived**, which is `docs/BACKLOG.md`'s own next entry (gate A40). That is the
intended sequencing — instruments first, decided cold; re-derivation second,
labelled — but it means anyone reaching for `report_matrix` before A40 lands will
meet an error, and that error is the design rather than a regression.

**Closes off.** Do not "fix" that by relaxing `_at_address` to treat a missing
`dimensions` key as the current reading: pooling A26-scored rows with rows whose
D4 is identically zero and whose D2 is improper is precisely the error the
version exists to prevent. And do not reach for `METRIC_VERSION` the next time a
dimension changes — A27, A29 and A31 all change dimension or payload semantics,
and under the bump each would force another full table rebuild for no numerical
reason.

## 2026-08-19 — where post-freeze gate titles live

**Decision.** `scripts/status.py` reads gate titles from `docs/BACKLOG.md`'s
`**Gate.**` lines as well as from SPEC §6, and reports those criteria in their
own "Post-freeze gates" block. SPEC is not amended.

**Why.** A genuine gap between two frozen rules. SPEC §6 is the acceptance
contract and stops at A24; SPEC §13 says new ideas enter `docs/BACKLOG.md`. The
sixteen entries added on 2026-08-18 name gates A25–A40, and `parse_gate_titles`
read §6 alone — so a test named `test_a26_...` exactly as CLAUDE.md requires was
attributed to a gate with no title and silently dropped from the report. Neither
document is wrong; nothing said where a criterion declared *after* the freeze
belongs. Resolved by the user in favour of reading the backlog, on the reasoning
that §13 already routes post-freeze material there and amending §6 sixteen times
would erode the freeze it exists to hold. The separate block is so that A1–A24
keep reading as the frozen contract rather than being diluted by a queue.

**Closes off.** The convention CLAUDE.md calls load-bearing — "a test not named
this way is invisible to the status report" — now holds past A24 as well, which
it had quietly stopped doing. Titles are derived from the test name in the
`**Gate.**` line rather than restated, so the two cannot drift.

## 2026-08-19 — tried and abandoned: an A26 test that only checked the diagonal

**Approach abandoned.** The first version of `tests/acceptance/test_a26.py`
tested D2's propriety on the kernel alone, plus one end-to-end case at
`candidate == truth`. `/test-review` returned TOO WEAK and demonstrated it by
execution rather than argument: with the call site transposed — computing
`sum(mine[c] * log2 theirs[c])`, which is linear in `mine` and therefore
maximised by a point mass — the suite ran **7 passed** while the exact defect
A26 exists to remove was reinstated. `tests/test_scoring.py` did not catch it
either (20 passed); its only D2 assertions are an `isinstance` and an `isnan`.

**Why it failed.** At `candidate == truth` the proper score and its transpose are
numerically identical — both reduce to the row's negative entropy — so the
diagonal is exactly where the two wirings cannot be told apart. Closed by
`test_a26_d2_is_the_proper_score_off_the_diagonal`. On
`query:phase_conditioned_dispersion`, candidate `poisson_mixture` against truth
`hawkes`, the three candidate wirings separate:

| wiring | D2 |
|---|---|
| proper, `sum(theirs * log2 mine)` | **-7.724875483580779** |
| transposed | -5.145434226360676 |
| modal (the pre-A26 reading) | -9.396973478894598 |

One assertion therefore rejects both wrong readings. A two-observation D4 case
was added for the same class of reason: with one observation, clipping per
observation and clipping the total give the same answer.

**Closes off.** The general shape is worth carrying to the next gate: a defect
that is symmetric on the diagonal survives any test that only checks the
diagonal, and "the kernel is correct" is not the same claim as "the dimension is
wired to the kernel". A gate that exercises a helper is testing the helper.

## 2026-08-19 — gate A27: what "held out" means, and a closure deliberately reversed

**Decision.** SPEC §8's "held-out intervention battery" is ambiguous between two
readings, and the second is taken: the battery is a **declared subset of the
designs the scenario offers**, scored whatever the arm ran — not a set withheld
from the offer so that non-use is guaranteed.

**Why.** The withholding reading is the more natural one and it is not available
here. The slice has exactly one intervention, `forced_design()`, and SPEC §4.2
makes it the only design that separates Hawkes self-excitation from latent regime
switching. Reserving it would break S5's "intervention planning", the oracle
policy lengths gate A24 rests on, and S10's budget, which is defined by
measurement against those lengths. Manufacturing a *second* forced arrival to
reserve instead was the other way out, and it costs more than it buys: a new
design changes `EmpiricalTable.version`, retiring every cached table for a 3m11s
cold rebuild in every worktree, and it widens the simulator's scope — all to
score a question no system was going to ask anyway. Three things also point at
the subset reading independently: `tests/test_scoring.py`'s `HELD_OUT`, which the
BACKLOG entry names as the precedent, is exactly that; the gate's own clause
"two arms with different run histories receive identical batteries" is trivially
true under withholding and therefore pointless to state; and the entry claims to
touch no frozen decision, which withholding plainly would.

**What this reverses, said out loud.** The 2026-08-17 entry recorded deriving the
battery from the evidence index as a *closure* — "one way to get a whole matrix
quietly wrong, removed" — on the true observation that `dimension_vector` cannot
check the battery excludes what was run and `reading_of` can. That closure was
real and this undoes it. The reason is that the property it bought is worth less
than the one it spent: across the recorded matrix `n_held_out` came out {3,2} for
the V-arms, {2} for B4/B5 and **0** for B1, whose D3 was `nan` on 20/20 S11 rows.
An instrument whose question set moves with the answer is not an instrument.

**The cost, which is not zero.** An arm that ran a battery design is now scored on
a question it asked. §8's "unused during the investigation" is honoured in intent
rather than mechanically. It is indirect rather than flagrant — D2 and D3 read
simulated table rows for candidate against truth, not the run's own observations —
but it is a real weakening and it is stated at `Scenario.held_out` rather than
left for someone to find.

**Closes off.** Every cell address moves: `battery_key` is in `cell_key`'s config,
so the 1,120 recorded rows stay at their old addresses under append-only and are
not re-derivable under the new scheme without a re-run. That is what the A40
re-derivation entry exists for and it names A27 as its prerequisite. No
`METRIC_VERSION` or `DIMENSION_VERSION` bump was needed, because membership is in
the address itself — the same move A26 made, for the same reason: bumping
`METRIC_VERSION` would reach every `Discretisation`'s content hash and invalidate
the tables, for a change that touches no estimator.

## 2026-08-19 — tried and abandoned: an A27 gate that compared batteries by size

**Approach abandoned.** The first version of `tests/acceptance/test_a27.py`
established "battery membership appears in the recorded address" by comparing the
scenario's declared battery against a *filtered-down* one — `narrower = tuple(d
for d in full if is_intervention(d))`. `/test-review` returned TOO WEAK and showed
why by construction rather than by argument.

**Why it failed.** `narrower` is a subsequence of `full`, so the guard
`narrower != full` forces `len(narrower) < len(full)`, and the slice has exactly
one intervention, so the pair compared is always (3, 1). A `battery_key` that read
`str(len(battery))` therefore separated them — and passed all ten assertions in
the module. Under it, two batteries of three *different* designs share one cell
address, so a cell re-scored on a different question set lands on the address of
the reading it replaced and `run_matrix`'s `skip_recorded` default reports the
stale row as the new campaign's, having executed nothing to disagree with it —
verbatim the failure the term exists to prevent. Not a strawman: `n_held_out` is
already how the ledger payload summarises a battery and how the BACKLOG entry
describes one ("{3,2} … {2} … {0}"), so the count is the natural reach.

The same defect appeared a second time on the scoring side and had to be closed
separately: the arm-scored clause asserted `reading.dimensions.n_held_out == 3`,
and `n_held_out` is `len(held_out)`, so `reading_of` could have been handed any
three offered designs — including three observational ones — and computed D3 on a
battery holding no intervention, which is the exact defect A27 was written about.

**Closes off.** `_swap_one_observational` is the construction that closes both:
exchange one observational member for another offered design, giving equal
cardinality, equal intervention count and different membership. The general shape
is worth carrying, and it is the second gate in a row to need it — A26's was "a
defect symmetric on the diagonal survives a test that only checks the diagonal".
This one is: **wherever membership is the property, a test that varies the size is
testing the size.** If the two batteries in a comparison differ in length, the
comparison cannot distinguish a membership term from a count.

## 2026-08-19 — gates A38 and A39 opened a hole in suite-freshness, and closed it

**Decision.** `.claude/hooks/suite-freshness.sh` now hashes `LICENSE`,
`.github/workflows/` and `docs/SCALE-UP.md` alongside the `.py` files,
`pyproject.toml` and `uv.lock`. The rule it implements is not "code only" but
*every input an acceptance gate reads*.

**Why.** The script's own comment asserted that docs/ is excluded because "a
DECISIONS.md entry cannot change a test result". That premise held until A38 and
A39 landed on the same day and broke it: `tests/acceptance/test_a39.py` reads
`docs/SCALE-UP.md`, and `tests/acceptance/test_a38.py` reads `LICENSE` and
`.github/workflows/`. Under the original hash, deleting a section of SCALE-UP.md
left `check` reporting FRESH while A39 was red — the exact false green the script
was written to prevent, reintroduced through a door it was not watching, by the
work that created the door. Verified after the change rather than assumed:
appending one line to `docs/SCALE-UP.md` between `begin` and `record` moved the
tree hash from `ce9d8c8a…` to `bb28eb67…` and `record` refused.

**Closes off.** Anything that adds a gate over a non-`.py` file has to be added
here in the same commit as the gate, and the comment now says so. The general
docs/ exclusion survives, because nothing asserts over the rest of it and
`/decide` runs between the two suite invocations by design. Note the consequence
for this session: widening the formula changes the hash of an unchanged tree, so
the green recorded before the change does not map onto it and the suite is re-run
under the new definition rather than carried over.

## 2026-08-19 — tried and abandoned: A38 and A39 gates that prose could satisfy

**Approach abandoned.** Both gates were first written as keyword checks over a
file's whole text, and `/test-review` broke both with executed counterexamples.

**Why A38's failed.** The "writes no artefact that outlives the job" clause
enumerated four *mechanisms* — `upload-artifact`, the literal `cache/tables`,
`git push`, `git commit`. A workflow with `actions/cache` on `path: .cache`
mentions none of them and persists Ubuntu-built empirical tables into every later
job. That is the likely workflow, not a contrived one: table acquisition is 3m11s
cold against 1.055s warm, so whoever watches CI spend three extra minutes per push
reaches for exactly that step. Separately, `-n 4` was never asserted, and
`--dist loadfile` does nothing without it — `uv run pytest --dist loadfile` alone
runs serially — so the gate accepted a job at CLAUDE.md's 262.44s claiming the
151.30s invocation. Fixed by stating the condition (no cross-job cache, no mention
of the repository's cache directory) instead of enumerating ways to build one, and
by asserting both flags on the same line.

A third defect surfaced only on running the corrected gate: it read raw YAML and
failed on the workflow's *own comment* explaining that it deliberately has no
`actions/cache` step. Comment lines are stripped now. An assertion that a
mechanism is absent must not be satisfiable, or breakable, by prose describing its
absence.

**Why A39's failed.** The module's docstring claimed each required item was
checked "named together with the thing it is about"; the code checked substring
membership over one blob of the whole file, which is a different statement. Both
senses of "battery" and of "content hash" already coexist in this repository's own
docs, so the pairing was ceremony. The reviewer built a 3,245-character *index* —
six note titles with pointers, documenting zero interface changes and saying so
explicitly — and it passed the module unchanged. Its anti-skeleton guard also read
the raw text while every other check went through a lowercased helper, so
appending a lowercase placeholder line still passed.

**Closes off.** Three closes, and the first is the general one: scope a paired
keyword check to a *section*, not a file. Then require the **claim** and not only
the subject — `not yet`/`becomes` near the version promise, `convention` near the
protocol — because naming a topic is not naming what is asserted about it. Then at
least four sections must name an interface, which is what a pointer index cannot
do. The length floor is kept and explicitly not load-bearing: the counterexample
cleared it on filler, so it measures typing.

## 2026-08-19 — tried and abandoned: a delimited encoding for the battery address term

**Approach abandoned.** `battery_key` built the string it digests by joining
designs on `\x00` and each design's config entries on `\x01`, on the premise that
no config value could contain either. The premise is false by construction:
`operation_config` renders a `CompareCandidates` operation's candidate set as its
`defect_key`s joined on `\x00`, so a single design's value carries the byte that
separates designs. Confirmed by execution rather than by reading — a
two-candidate design's `op.candidates` holds exactly one `\x00`. Replaced by a
length-framed encoding, which is decodable and therefore unambiguous whatever the
parts contain.

**Why it matters even though it is unreachable today.** The pointproc compiler
refuses `CompareCandidates`, and `held_out_designs()` returns only
`QueryDiagnostic` and `ForceArrival` designs, so no live path reaches the
collision. But `battery_key` takes a `Sequence[ExperimentDesign]` and states its
injectivity guarantee unconditionally, and a battery of designs whose per-design
strings are `{"P\x00Q", "R"}` collides with one of `{"P", "Q\x00R"}` — equal
cardinality, different membership, one address. That is verbatim the stale-row
failure the term was added to prevent, arriving through the encoding rather than
through the count, and it is the second time in two gates that the *count* term
`len(battery)` was the thing standing between a defect and a passing test.

**The test decodes rather than collides, and that is not laziness.** A colliding
pair of *real* designs is not constructible: it needs two batteries whose sorted
per-design strings concatenate identically, and a design's config is not free
text — `design`, `operation`, `n_events` and `outcome` are all fixed by the
design itself. So the test parses `_battery_payload` back into the multiset of
designs it was built from, with a parser written independently of the encoder.
A decodable payload cannot be ambiguous, which is the general property; a
specific collision would have been the weaker evidence even if one could be
built.

**Closes off.** Do not "simplify" the framing back to a separator by choosing a
byte believed not to occur. The class of failure is choosing any such byte, and
the repository already contains one counterexample it did not know about.

## 2026-08-19 — the A27/A38/A39 review round, and one test that never failed

**Work left deliberately incomplete.** `/code-review` returned five findings
against the three gates after they were implemented and green. Four are fixed in
the same tree, plus one from `invariant-auditor` lens 3 (the entry above) and two
from lens 2. The fifth is deferred to a new backlog entry, gate A43: `summarise`
checks that a row carries a battery term but never that it carries the *right*
one, so a report built entirely on rows scored under a superseded battery is
accepted and rendered as current. `_refuse_mixed_batteries` does not catch it —
it fires only when two batteries coexist for one scenario.

**Why deferred rather than done.** The check needs a `battery` callback on
`summarise`, the sibling of the `scenario_class` callback it already takes, and it
is worth having only if it is **required**: an optional parameter defaulting to
today's behaviour reproduces the defect for every caller who forgets it, which is
the argument `cell_key`'s own `battery` parameter is written on. Required means
every `summarise` call site changes, which is a wider edit than a review fix
should make to another gate's tree. What was taken instead is the visible half:
the term is rendered per cell, and the "no row matches" diagnostic now names a
row excluded for its battery rather than listing the terms it matched — which is
what anyone pointing `report_matrix` at the recorded 1,120-row matrix meets
first. A43 is sequenced **before A40**: the re-derivation is what first puts two
generations of battery in one ledger.

**One test in `tests/acceptance/test_a27.py` passes against the unfixed code, and
it is meant to.** `test_a27_no_research_system_can_reach_the_battery` asserts that
`sciagent/systems/` imports no `sciagent.eval` and that `Investigation` carries no
scenario-shaped slot. Lens 2 verified both facts and observed that *nothing
asserted them*: `Scenario.held_out` is unreachable from an agent for the same
reasons `Scenario.truth` is, but where `plausibility` has a symbol list, a
derivation check and A17's call-graph analyser, the battery had a structural
accident. Adding `held_out` to `Investigation` tomorrow would have failed no test.
So this one is a regression guard rather than a demonstration of a fix, and it
never went red — the deliberate exception to "watch it fail", recorded here so a
later reader does not read it as the step having been skipped.

**Closes off.** The four fixed findings are in the diff and need no entry. Two
lenses (3 and 6) died mid-response on API connection errors and were re-run to
completion rather than reported from their partial output, so the round is six
agents of six and not four.

## 2026-08-19 — an intermittent subprocess failure in A1 and test_llm, seen and not explained

**Measured, and left open.** Four full `-n 4 --dist loadfile` runs on one desktop
during the A27/A38/A39 preflight: **green, red, red, green**. Each red failed
exactly one test, and a *different* one each time —
`test_llm.py::TestAddressingIsDeterministic::test_in_process_addresses_match_a_subprocess`,
then `test_a01_a05.py::TestA1Determinism::test_a1_byte_identical_across_processes`.
Those two are the only tests in the suite that spawn a child interpreter, and in
both cases the child exited 1. Both passed in isolation and under a targeted
`-n 4` run of just the two files, and `determinism_child.py` run directly printed
its nine digests cleanly. Wall clock moved with it: 155s, 221s, 203s, 181s.

**Why this is written down rather than shrugged off.** The second one is **A1**,
a frozen §6 gate, and an intermittently-red determinism gate is either a real
cross-process determinism defect or an environmental one. Nothing in four runs
distinguishes them, and the changes in flight cannot account for it: they touch
neither child, the greens bracket the reds, and the two failures were in
different files.

**What was fixed is the blindness, not the cause.** Both call sites ran
`subprocess.run(..., capture_output=True, check=True)`, so `CalledProcessError`
carried the child's stderr and never showed it: a dead child reported an exit
status and nothing else, which is why four runs produced no diagnosis. Both now
assert on `returncode` explicitly and put stderr and stdout in the failure
message. The assertion is unchanged in strength.

**The standing hypothesis, untested.** Memory, not CPU. This file already records
`-n auto` (12 workers) dying with `MemoryError` on this 16 GB machine, and these
two tests fork a fifth interpreter beside four workers holding empirical tables.
The shared `.cache/tables` read/write race is the other candidate, already guarded
by retries in `EmpiricalTable.save` and `_read_text_contended`. Neither was
confirmed, and the next occurrence should say which — that is what the stderr is
now there for.

**Closes off.** Do not read a red A1 as a determinism regression without reading
the child's stderr first, and do not "fix" a recurrence by retrying the
subprocess: if the child is dying for want of memory, a retry hides a resource
limit behind a green, and if it is not, a retry hides the determinism defect A1
exists to catch.

## 2026-08-19 — gate A28: B6 draws over the whole grid, and is not a 57th cell of §9

**Decision.** SPEC §12 criterion 5's comparator is built, as
`systems/baselines/uniform.UniformProposer` inside `systems/hybrid.Hybrid` under
the name B6. Three things were settled that the backlog entry left open or that
it decided the other way.

*The draw is the entry's second form.* `docs/BACKLOG.md` named uniform-over-
`enumerate_edits(1)` — the 48-corner stratification — as the cheap first form,
and the full grid as optional. The full grid was chosen instead, before any code
was written. A grid box's corners are where the degenerate parameterisations
live: `BeamSearch` says so at `_UNSCORABLE`, having found them by enumerating
exactly those corners. A B6 confined to them would lose for a reason unrelated to
random generation, and a deflator that is too easy to beat flatters the arm it
exists to deflate — which SPEC §12's "beating B4 or B5 is the research question
rather than an exit criterion" can least afford. So the draw is the menu cell
uniformly, then each of that cell's grid indices uniformly: V7's action space
exactly, leaving the two arms differing in *how a point in it is chosen* and in
nothing else. The measured menu is 5 cells, 16 grids, every grid 64 points,
arities 3 and 4, so the corner form's cost advantage was real — at most 48
structures ever, against at most 40 novel table rows for the whole B6 cell. Forty
rows is a bounded price for a comparator that is not systematically degenerate.

*B6 is `Hybrid`, not a new class.* `Hybrid.layer` was annotated with the concrete
`ProposalLayer`; it is now a `ProposalSource` protocol that both `ProposalLayer`
and `UniformProposer` satisfy. The backlog asked for a proposer drawn "at the same
Stage A gate under the same budget split as V7", and sharing V7's class makes both
facts about construction rather than claims for a later reader to audit — the
argument `memory_ablation` already makes for V3 and V4.

**Why.** Criterion 5 has been unmeasurable since it was written, and
`docs/DECISIONS.md` (2026-08-04) records why in as many words: *"no B6 exists"*.
The two frozen decisions behind that cannot both be satisfied — §5 lists B6 under
"deferred to the full benchmark" while §12 criterion 5 requires B6 to score the
slice — which is precisely SPEC §13's trigger, "a case where two frozen decisions
cannot both be satisfied". §13 resolves it in the backlog and here, so
`docs/SPEC.md` is untouched.

**Closes off.** B6 is `CRITERION5_CELLS` — one cell, S11, twenty replicates — and
deliberately **not** a fifty-seventh row of `SPEC9_CELLS`, whose docstring says
"Do not add an arm" three lines above the tuple. §9 recorded 56 cells and still
records 56; `ALL_CELLS` is the union for the runner, and `run_matrix.py`'s
`--systems` still defaults to §9's seven arms, so B6 is opt-in and a default pass
cannot widen the preregistered matrix by accident. Anything later reporting "the
matrix" must still say which.

**Left deliberately incomplete: nothing was run.** This lands the arm and its
wiring, not a reading. The cell is executable at `--systems B6` with no provider
and API $0. It is unexecuted because A40 ("Re-derivation of the recorded matrix
under fixed metrics") is where readings are produced under the post-A26 metric
version and post-A27 battery term, and a B6 reading filed before it would be
re-derived immediately. Criterion 5 stays *unmeasured* until A40; what changed is
that it is no longer *unmeasurable*.

## 2026-08-19 — the intermittent subprocess failure is memory, confirmed

**Supersedes nothing; it answers the open question in the entry above it.** That
entry — "an intermittent subprocess failure in A1 and test_llm, seen and not
explained" — recorded four `-n 4` runs going green/red/red/green, fixed the
blindness rather than the cause, and left a hypothesis: *"Memory, not CPU"*, with
the shared `.cache/tables` read/write race as the rival candidate. It asked that
the next occurrence say which, and named the newly-surfaced child stderr as the
instrument.

**The next occurrence said which.** One `-n 4 --dist loadfile` run during gate
A28: `3 failed, 1449 passed, 7 skipped in 253.04s`. All three failures were the
subprocess-spawning tests, and the stderr the previous entry added carried the
answer verbatim:

    OpenBLAS error: Memory allocation still failed after 10 retries, giving up.

Not a table-cache `PermissionError`, not a determinism mismatch — an allocator
giving up. The hypothesis is confirmed and the rival is ruled out for this
occurrence. All three passed in isolation on the same tree, and the run did not
reproduce: a rerun with the identical tree was fully green, `1452 passed, 7
skipped in 246.76s`.

**Why this is worth a second entry.** The instrument the previous session
installed worked exactly as intended and cost one run to pay off, which is the
argument for installing it. And the attribution trap is worth naming: on seeing
one red run with a new test file present and one green run with it absent, the
obvious inference is that the new file tipped it. That inference is wrong at n=1
per side — a green rerun *with* the file present is what settles it — and it was
made in this session before being corrected.

**Closes off.** A red on these tests is a resource fact until its stderr says
otherwise, so read the stderr before reading it as a determinism regression. Do
not attribute an intermittent failure to whatever changed most recently without a
rerun on both sides. And do not retry the subprocess to make it green: the
previous entry's reasoning holds and is now better founded — if the child is dying
for want of memory, a retry hides a resource limit, and the limit is real.

## 2026-08-19 — killing a backgrounded suite orphans its workers, and the next run pays

**Measured, and the failure surfaces one run later disguised as a test defect.**
Stopping a backgrounded `uv run pytest -n 4 --dist loadfile` through the harness's
task-stop killed the parent `pytest.exe` and left its four `pytest-xdist` execnet
gateways alive — `python -u -c "import sys;exec(eval(sys.stdin.readline()))"`, at
131, 132, 133 and 183 MB. Eleven stray processes in total, 657 MB, every one of
them traceable to this worktree by command line. Free physical memory: **1662 MB
of 16310**.

The immediately following full-suite run then died in *collection*, not in a test:

    ERROR tests/acceptance/test_a06_a11.py - MemoryError
    ERROR tests/acceptance/test_a01_a05.py - ImportError while importing test mod...
    ERROR gw3 - Different tests were collected between gw0 and gw3.
    6 errors in 26.46s

Reaping the strays took free memory to 2144 MB, and the same command on the same
tree then ran clean: `1452 passed, 7 skipped in 246.76s`.

**Why it is written down.** "Different tests were collected between gw0 and gw3"
reads like a conftest or collection-order defect and is nothing of the kind; it is
what xdist reports when workers die unevenly for want of memory. A session that
took it at face value would go looking in the wrong file, and the cause is one run
in the past and invisible in the tree. This is the same scarcity the entry above
confirms and the same one CLAUDE.md records as "memory binds before cores here" —
a third face of it, with a self-inflicted trigger.

**Closes off.** Do not stop a running suite that way; let it finish or let it time
out. If it is stopped anyway, check for orphaned interpreters before starting
another run rather than diagnosing the next run's collection errors. The check is
`Get-CimInstance Win32_Process -Filter "Name='python.exe'"` filtered on the
worktree path — and filter it, because this repository expects several sessions on
one machine and a blanket kill would take another session's suite with it.

## 2026-08-19 — docs/BACKLOG.md becomes the second build backlog, and one gate namespace spans two files

**The ambiguity.** SPEC §13 sends new work to `docs/BACKLOG.md`; SPEC §11 is
"the ordered build backlog" and is what `scripts/status.py` reads. Nothing said
what happens when the new work is *gated* — as the 2026-08-18 review's sixteen
entries are, each carrying a `**Gate.**` naming a `test_aNN` for A25–A40.

**Decision.** `BACKLOG.md` is a second build backlog, after §11. An entry is
build work exactly when it carries a `**Gate.**`; everything else there stays an
idea. Acceptance numbering is one namespace across two files — A1–A24 in SPEC
§6, A25 onward in BACKLOG entries — and a number claimed twice is an error.
Order comes from an explicit `**Rank.**`, transcribed from `REVIEW-2026-08-18.md`
§5, so the numbers run {1–14, 16, 17}; the gaps are that table's ungated rows.

**Three measurements the change itself destroys.** All taken before any code
moved, none reproducible afterwards:

- `/next` resolved to **no item at all**. Untracked §11 items are
  {1, 7, 9, 11, 12, 13, 14, 15}; item 1 is deferred; 7 and 9–15 each have a
  `backlog item N` commit. The skill's fallback — lowest untracked number with
  no commit naming it — yielded the empty set. The skill had read "this is now
  the normal path, not the exception" since item 14 landed, and by 52f193e that
  path terminated in nothing.
- A gate outside §6 **disappeared**, rather than merely miscounting.
  `parse_gate_titles` returned 1–24; `gate_of("…test_a25_…")` returned 25;
  `gate_statuses` iterated `sorted(titles)`. So the test reached no gate row —
  and because `gate_of` *did* match, it was excluded from the
  `(+N not named for a gate)` tally as well. `test-review/SKILL.md` described
  this as "counted but never reported against anything", which understated it.
- Suite after the change: `1400 passed, 7 skipped in 151.47s` at
  `-n 4 --dist loadfile`. Recorded because CLAUDE.md's `1109 passed` is stale by
  far more than this change adds, and a future session reading 1400 should not
  treat the gap as a defect. It cross-checks against `status.py`'s own
  collection: 201 gate tests + 1206 others = 1407 = 1400 + 7. Two earlier drafts
  of this entry carried figures that did not cross-check; `/code-review` caught
  the arithmetic both times, before either was committed.

**Three approaches declined, with what each would have cost.**

- **Add SPEC §11 rows 16–31 and A25–A40 to §6.** Strictly smaller — `status.py`
  needed no code at all. Declined because §13 puts new work in `BACKLOG.md` and
  the review put it there deliberately; amending a frozen document to satisfy a
  tool is the wrong direction, so the tool moved instead.
- **Use file order as build order**, avoiding the sixteen `**Rank.**` edits. The
  file's tail is in *gate* order A25→A40, which is not the ranked order: A25
  (QTM real-data grounding, XL, ~1–2 weeks) is first on the page and rank **6**,
  and the review's narrative forbids producing its results before ranks 1–2
  land. A file-order cursor would have opened `/next` with the most expensive
  item in the set.
- **Reorder the file's sections into rank order** instead of adding a field.
  Declined: it loses the appended-by-the-review provenance, and a
  priority-ordered tail under a chronological head is incoherent.

**What `/test-review` caught, which no diff now recovers.** The first version of
`tests/test_status.py` asserted only over the three new pure functions, and the
lens returned TOO WEAK on the implementation that gap admits: a *correct* parser
never called from `report()` ships 21/21 green with the whole user-visible
deliverable absent — no second section, no backlog cursor, A25–A40 still
vanishing. Two smaller gaps went with it: the fixture's `STRUCK` and
wrapped-header sections carried no gate, so a parser recognising only `DONE`, and
one splitting naively at every `##`, both passed. The general form is worth
carrying: **a test suite over new pure functions is silent about whether anything
calls them**, and on this change that silence covered the entire point.

**What `/code-review` then found, and the one worth carrying.** Six findings on
this change, all real. The one that matters: `markdown_sections` skipped fenced
lines without clearing `previous_was_heading`, so `## Alpha` followed directly by
a fence and then `## Beta` merged into one heading `Alpha Beta` — one section
holding two `**Gate.**` lines, of which only the first is read, losing the second
entry silently. **That is the same failure the unclosed-fence guard three lines
below it was written to prevent**, left open by the author of the guard in the
same sitting. A guard is evidence that a failure mode was understood, and no
evidence at all that every path to it was closed.

The other five, briefly: the report test pinned the live cursor to rank 1, so
writing `test_a26_` — literally the next thing anyone does here — would have
turned the suite red for correct work; `/next`'s new §2 snippet referenced an
undefined `RANK_POSITION` and indexed sections by file position, which the same
change documents as not being rank order; `session-start.sh` counts printed gate
rows as "gates with tests", and since the cursor's blocking gate is always
printed, the count went one high permanently the moment a backlog cursor always
existed; four assertions hard-coded the set at sixteen, so promoting an idea to
a gated entry — the workflow this file documents — would have failed them; and
the arithmetic above.

**The second review round, and why re-running it was not ceremony.** `/ship` §0
is stricter than the freshness hook: the hook watches `.py` under `src`, `tests`
and `scripts`, so a tree it calls FRESH can still carry edits the *review* never
saw. Fixing the first round's six findings changed parser behaviour, four tests,
a hook and a skill, so the review was re-run before committing. It found seven
more, and two of them justify the rule on their own:

- **A second path to the same silent entry loss.** Round one closed it via
  fences; round two found it via two adjacent `##` headings, which the
  wrapped-heading convention reads as one section — so a merged body holding two
  `**Gate.**` lines drops the second entry. Both paths now raise. The pattern
  across the rounds is the finding: a guard is evidence that a failure mode was
  *understood*, and no evidence that every path to it was closed.
- **A heading word was speaking for a gate.** `DONE`/`STRUCK` rendered `[x]
  landed` and skipped the cursor with zero tests, and did so under `--run` too —
  against this script's stated guarantee that it "never claims a gate passes,
  only that tests for it exist". A marker may order the backlog, since somebody
  decided the work is behind us, but it may not stand in for evidence. It now
  renders `[?] marked landed but no tests`, and a failing gate reads
  `FAILING 1/3` rather than being flattened into "not written".

The rest: `**Rank.**` without a parseable `**Gate.**` silently demoted an entry
to an idea, which is how a mistyped field would drop work out of the backlog it
was just added to; a wrapped `**Held.**` value truncated to a fragment and named
the wrong blocker; and "every gate-tracked backlog item is satisfied" printed
when only *held* entries remained, which the statusline compresses to `all gates
satisfied` — a green cursor over blocked work.

**Closes off.** `/next` has a real criterion to build against again, so the
self-written-standard path in its closing paragraph is now the rare case rather
than the only one. One item is deliberately not startable: rank 3 (A29,
criterion 4's observable) carries `**Held.** OPEN-DECISIONS §1`, and the cursor
names it and passes over it — that decision is the user's, is written up cold,
and until it is taken the entry is not work an agent may begin. Ranks 1, 2, 4
are unblocked and in order.

## 2026-08-19 — Two sessions built the same reader, and the merge kept one of each behaviour

**What happened.** `worktree-backlog-cursor` branched at `52f193e` to make
`docs/BACKLOG.md` a tracked second backlog. While it worked, `main` advanced six
commits, and one of them taught `scripts/status.py` to read the same file:
`parse_backlog_gates` scanned `**Gate.**` lines, gave each a title and printed a
"Post-freeze gates" block. Neither session knew of the other. The merge arrived
with two readers of one file in one module, textually compatible — git resolved
all but three hunks — and semantically duplicated.

**Resolution: one reader, and the better half of each.** `parse_backlog_entries`
survives, because it is a superset: it returns rank, held and closed as well as
the gate, and the cursor is the whole point of the branch. Three behaviours came
back the other way, and each is now covered by a test that did not exist on
either side:

- **The gate title is derived from the test name, not the heading.** `main` chose
  this and was right for a reason the branch had not met: by the time the merge
  happened, five entries had landed, and a landed entry's heading opens with
  `DONE (2026-08-19, gate A26) —`. Keying the report on the heading spent the
  row's width on provenance.
- **A `STRUCK` gate leaves the namespace.** `main` excluded it; the branch did
  not distinguish it from `DONE`. Withdrawn work will never be satisfied, so a
  title for it is a permanently empty row and a number reserved against an entry
  that could still use it. `entry_row` now renders it `withdrawn` rather than as
  the discrepancy a `DONE` with no tests correctly is.
- **The two gate blocks print separately.** A1–A24 keep reading as the frozen
  contract they are; `report` derives which numbers came from the backlog by set
  difference rather than threading a flag through.

`main`'s "SPEC §6 wins any collision" was **not** kept. The branch raises
instead, and a rule that silently prefers one declaration is exactly the quiet
loss the rest of this parser is built to refuse.

**The ranks had to move, and that is a judgement worth flagging.** `main` added
three gated entries with no `**Rank.**`, since the field did not exist there.
A43 states its own sequencing — *"before A40"*, because the re-derivation is what
first puts two battery generations in one ledger — and the review's rank space
had no free number below A40's 5. So the open block shifted down by one (A40
5→6, A25 6→7, … A37 14→15) and A43 took 5. Closed and held entries kept the
numbers the 2026-08-18 review gave them, so 1–4 and 16–17 are unchanged and the
review's ranked table still reads across.

A41 and A42 went to 18 and 19, at the tail, and **this is the weakest call in
the change.** Neither entry states a sequencing constraint, and extending the
user's ranked plan seemed better than reshuffling it on my own reading. But A41
is a truth leak on the `Investigation` surface that four docstrings and
invariant 2 all state cannot happen; ranking it below eight routine entries
understates it, and moving it is one number.

**A42 is held on a decision that is not written up.** Its own cost line says "the
semantic decision is the user's", which is the A29 shape without an
`OPEN-DECISIONS` section to point at. It carries `**Held.** a cold decision on
D4's comparison set` rather than a rank alone, so the cursor names it and passes
over it. If that decision gets written up, the `**Held.**` should point at it.

**Two defects the merge introduced, and how each was found.** Neither came from a
test:

- `CLOSED_PREFIX` was written as "everything up to the first dash", with the
  file's existing `DASHES` constant — which includes the hyphen. `DONE
  (2026-08-19, gate A26) — title` has two hyphens in the date, so every landed
  entry rendered as `08-19, gate A26) — D4 is identically zero by`. Found by
  reading the report, not by running the suite. The separator is now the en and
  em dashes only, and a test covers it.
- `git_state` passed `text=True` with no encoding, so `subprocess` decoded git's
  UTF-8 output with the Windows locale encoding. Latent until a commit subject
  carried a section sign, which this branch's own message did: it printed
  `SPEC Â§6`. Fixed at that call only; the two pytest calls are left alone rather
  than changed speculatively.

**`Path.write_text` translates newlines.** The edit scripts driving this merge
used it, and on Windows `\n` becomes `\r\n`, so whole files silently converted
to CRLF. git normalises on commit and would have hidden it, but
`suite-freshness.sh` hashes the *working tree*: a run pinned against CRLF content
reads STALE the moment git rewrites it. Rewritten with `write_bytes` before
pinning. Use `write_bytes`, or pass `newline="\n"`, for anything the freshness
hook will hash.

**The earlier blocker resolved itself.** On 2026-08-19 this branch stopped short
of merging because another session held uncommitted edits to
`.claude/hooks/statusline.sh` in the main tree, and a `--ff-only` merge would
have rewritten a file under a live session. That work has since landed as
`6ff2a11`, and the merge in this direction — `main` into the worktree — never
needed the main tree to be clean in any case. The rule stands: stop and report
rather than resolving another session's conflict. What is worth carrying is that
waiting cost nothing, and the thing waited on arrived as an ordinary commit.

**Closes off.** The cursor resolves to `BACKLOG rank 5 — The report layer checks
the battery's presence, not its value`, blocked on A43. Ranks 1, 2, 4, 16 and 17
are landed, 3 and 19 are held on decisions that are the user's, and 5 onward are
open in order.

## 2026-08-19 — The merge's review round, and a guard written from the wrong side

**Four findings on the merge resolution**, all real, all fixed. Two are worth
recording because the fix is not the one that was proposed.

**A landed entry's stale `**Held.**` blockaded it.** `entry_row` tested `held`
before `closed`, while every other reader of the field guards it with
`and not closed`. The file's own convention is that an entry keeps its fields
when it lands and gains only a heading marker, so this was the documented path,
not an abuse of one: the moment the OPEN-DECISIONS §1 decision is taken and A29
lands green, rank 3 would have reported as blocked while the cursor summary
below it said nothing was held. The asymmetry is the lesson — a field read in
four places and guarded in three is a defect waiting for the fourth caller.

**The struck-gate exclusion reopened the vanishing hole from the other side.**
Keeping a withdrawn criterion out of the namespace is right, and it has a cost
the change did not see: `gate_of` attributes a test by its name alone and the
report iterates the *declared* numbers, so a test still named `test_a37_…` after
entry 37 is struck is counted into no row and excluded from the "not named for a
gate" tally as well. It disappears — which is the exact failure this whole
change exists to close, arriving by writing the test rather than by omitting the
entry.

The review proposed a struck-specific guard. **`refuse_undeclared_gates` is
general instead**, refusing any gate number the tests name that neither SPEC §6
nor BACKLOG declares. Struck entries are one way to reach it; a typo in a test
name is another, and a number nobody ever declared is a third. A guard shaped to
the one path that was noticed is how this change acquired three separate ways to
lose a section, each found by a different review round. Verified empty against
the tree before the guard went in, so it refuses nothing that exists today.

**One half of one finding was declined.** The statusline now compresses the
fourth cursor form — `nothing open; N BACKLOG entries held on a decision`, 48
characters rendered verbatim before this — down to `N held`. The review also
asked for the dim colour that `all gates satisfied` gets. That was not taken:
dim reads as *done*, and this state is the opposite of done — nothing is
startable because the remaining work waits on the user. It keeps the cyan
"outstanding" colour, which is what it is.

**Closes off.** The two-review pattern recorded on this change holds for a third
round: every round found something, and what it found was another path to a
failure mode the previous round had already named and guarded. The guard is
evidence the mode was understood. It is no evidence the paths were enumerated.

## 2026-08-20 — infrastructure: the CLAUDE.md trim, re-proposed against a close-off that ruled it out, and what made it land this time

**Decision.** `CLAUDE.md` goes from **376 injected lines / 23,180 chars** to
**230 / 13,471** — a 42% reduction in what is billed on every turn of every
session — with every rule preserved and invariant 6 left untouched. The raw file
is 315 lines, of which 85 are HTML comment.

**This overrides the 2026-08-18 close-off, knowingly and with the user's
decision.** That entry — *"the CLAUDE.md trim was attempted, reviewed twice, and
abandoned"* — says plainly: *"It rules out re-proposing a CLAUDE.md size
reduction on token-saving grounds alone."* This change was re-proposed on exactly
those grounds and the agent driving it **did not consult that entry**, having
grepped the file for measurements and stopped. The collision was found by the
`/preflight` history lens reading `git log` over the changed hunks, not by the
work that caused it. That ordering is the finding: a `/recall` for *"was this
already tried"* costs seconds, is prescribed by `CLAUDE.md` for this exact
question shape, and was skipped.

**Why it stands anyway, which is a different argument from the one 2026-08-18
rejected.** The abandoned trim **deleted**: ten slash-command bullets, two
`Stack` lines, and the forensic receipts behind four rules — 6.5%, ~400 tokens a
session, with content leaving the repository. This one **relocates**, and the
distinction is mechanical rather than rhetorical:

- Block HTML comments are **documented** as stripped before injection while
  remaining visible to `Read`, so justification prose stays adjacent to its rule
  at no context cost. **This is still not measured here — see the caveat below,
  which is load-bearing rather than a hedge.**
- `## Cloud sessions` moved to a tracked `docs/CLOUD.md`, reached by a pointer
  and by `session-start.sh` printing it when `CLAUDE_CODE_REMOTE=true`.
- Measurements became pointers into *this* file, which already held them with
  better provenance.

Nothing left the repository. A 93-probe check over every rule and figure in the
old file found all of them still reachable. 42% against 6.5% is also a different
proposition from the one judged not worth the risk.

**The headline figure is contingent, and 2026-08-18 said so first.** That entry's
second open item reads: *"Whether block-level HTML comments are stripped from
`CLAUDE.md` before injection: documented at `code.claude.com/docs/en/memory.md`,
never measured here… the next audit that reaches for the mechanism should measure
it with `/context` first rather than inherit the assumption."* This audit reached
for the mechanism and **inherited the assumption anyway** — it verified the
documentation, which is what that entry already had, and the documentation was
never the gap. Caught by the second `/code-review` pass, not by the work.

So both figures, honestly:

| | chars | lines |
|---|---|---|
| raw file, certain either way | 23,294 → **18,101** (−22%) | 376 → 315 |
| injected, **if** stripping happens | 23,180 → **13,471** (−42%) | 376 → 230 |

The 22% is a floor that holds regardless. The 42% — and with it the claim that
justification prose costs nothing, which is the argument overriding 2026-08-18 —
rests on a mechanism no one here has observed. **It cannot be measured from the
session that made the change**: `/context` reports injected size and needs a
fresh session. The measurement is owed, and it is the first thing to do in the
next session rather than a nice-to-have. If it comes back showing no stripping,
the comments are still valid markdown and nothing breaks — the saving is 22% and
this entry's framing, not the change, is what needs correcting.

**Invariant 6 is byte-identical to its pre-change text, and now carries a
banner.** The first draft of this change reworded its headline to *"Evaluation
apparatus is never written after the system it grades."* — **verbatim the
wording 2026-08-18 quotes and rejects as too broad**, the absolute that
`c4dcee3` already violates. It is restored exactly, with a comment naming both
clauses that must survive, both rejected wordings, and the instruction not to
compress it. 2026-08-18 nominated invariant 6 as the test case for any future
attempt; it failed that test on the first draft and passes it now only because
the lens caught it.

**Measured, and the reason half this entry exists.** Auditing what `CLAUDE.md`
asserted against what this file records found almost everything already here —
the xdist table, the 12–15% contention figure, 3m11s/1.055s, the 18:05:56 false
green, the worktree/agent visibility rule, the Windows pin. **Three numbers
existed nowhere but `CLAUDE.md`** and would have been lost with it:

| measurement | value |
|---|---|
| the `cd <project dir> &&` prefix, across 50 transcripts | **1,141 of 1,402** commands carried it; none needed it |
| `mypy` on the configured file set | **1.9s** |
| shared-cache read probe, before the reader guard | **4 lost reads in 900**, at four writers / six readers |
| the same probe, after the guard | **0 in 900**, and **0 in 1200** at six and eight readers |

The probe figures belong beside the 2026-08-16 entry on the unguarded reader,
which recorded the defect and the fix but not the numbers that closed it.

`docs/DECISIONS.md` is now **~485KB across 169 entries**, by the colon test that
2026-08-18 records — `grep -c '^## '` returns 170, one more, because the
preamble's fenced template matches. The 90k-token figure that entry measured was
taken at ~6,180 lines against 8,035 now, so `CLAUDE.md` no longer restates it as
a number: it says *well over 100k, treat any figure as a floor*.

**What the review caught that the diff alone would not have.** Seventeen findings
across two `/code-review` passes and two history-lens passes, all fixed. The
pattern is worth more than the list: **every round found something, and four of
the five most serious were sentences deleted whose commit messages record the
defect they were added to prevent.** The 2026-08-18 entry predicted exactly this
— *"this file's prose is densely cross-referential in ways not visible from the
line being cut"* — and it was right again. The five:

- Invariant 6's headline was reworded to a phrase 2026-08-18 quotes and rejects.
- *"Cloud sessions run Ubuntu 24.04, where neither caution applies"* was dropped,
  returning the platform rule to the unconditional assertion `8dd0690` fixed.
- The delegation bullet's carve-out — *"the one exception is work that does not
  fit one context at all"*, added by `b3b01a5` — was dropped entirely, leaving
  the general test standing while the Skills section two screens later still
  prescribes the excepted behaviour, `/recall`'s `decisions-sweeper` fan-out.
  **Found by the second history pass, after the first had cleared the file.**
- A **rule** ("batch related work into one worktree session") was placed inside a
  stripped comment, where the model would never see it — the one thing a comment
  must not hold. It was also the one rule here with no evidence anywhere tracked,
  and is now dropped rather than restated.
- `pre-compact.sh` wrote its state with `{ … } > "$STATE"`, which truncates
  first, so a hook killed at its 30s timeout left a file still carrying the
  `session:` line the reader matches on. Two more doors to the same false green
  turned up behind it: a failed atomic replace kept the *previous* compaction's
  state, and `session-start.sh` replayed the file without consuming it, so a
  second compaction whose hook failed would reprint the first one's tree verdict
  as current. The state file is now written with a retry, removed rather than
  left stale on failure, and consumed on read.

The general lesson, which is not the same as 2026-08-18's: a whole-file rewrite
defeats a probe-based check. A 93-probe sweep over the old file's rules passed
while three of the above were already broken, because a probe list is written
from what its author remembers to look for. `git log -S` over the changed hunks
found what the probes could not, and it took **two** passes — the second found
what the first had cleared.

**Two premises in the plan did not survive checking**, recorded so they are not
re-proposed. No subagent frontmatter field suppresses the `CLAUDE.md` hierarchy —
`Explore` and `Plan` are the only agents that omit it, so routing
`decisions-sweeper` through `Explore` costs the sweeper's prompt, which is what
makes it work. And `--plugin-dir` cannot make a worktree-authored skill visible
to its own session: skills in `.claude/skills/` are not a plugin, and 2026-08-16
already settles that the remedy is the merge.

**Closes off.** The 200-line target was not reached and should not be chased
further by cutting prose: what remains is 230 lines of rules, and the next 30
would come out of the six invariants, the conventions, or the worktree safety
rules. The lever left is structural, not another editing pass. **2026-08-18's
close-off is superseded on its narrow point — relocation is not deletion — and
stands on its broad one:** compressing a prohibition changes what it prohibits,
and invariant 6 is still the test case. The check this change actually needs is
behavioural and cannot be run from the session that made it: one full `/next`
item, watching for a convention silently forgotten rather than an error.

## 2026-08-20 — gate A28: the contention rule is measured wrong-way-round, and the fan-out that measured it found six defects

**Decision.** `CLAUDE.md`'s contention rule gains a carve-out: review work that
never reads the suite's result may run beside it. Gate A28 is amended in place —
two defects in landed code, four tests that did not test what they claimed, ten
tests added. No new gate number, no `METRIC_VERSION` bump.

**Why the carve-out, and the one number worth keeping.** Six `/preflight`-shaped
agents cost a `-n 4 --dist loadfile` run **200.65s → 236.59s, +17.9%**, with
**identical result sets** (1505 passed, 7 skipped both times) — so the tax is
time and not correctness, and the 2026-08-16 rule is right that "read-only, so
free" is the wrong premise. What that entry did not name is the mechanism:
the agents are bound on **inference latency, not local CPU**. Under full suite
load `/code-review` ran **350.0s against 360.6s idle**, a 3% *decrease*. That is
why the suite pays the whole tax and the agents pay none, and why the fan-out —
not the suite — is the critical path. Serialising therefore adds the suite's
entire duration to that path to avoid a tax that lands inside time the agents
were spending anyway.

**Abandoned: the wall-clock comparison this set out to make.** Serialised
against overlapped was scored at 610s vs 536s and **withdrawn as unsound**.
`/code-review` spawned nested sweeps in the overlapped condition and not in the
idle one — one ran a further 471s after its parent returned — so the two
conditions did different amounts of work and the difference measures that.
Deeper: **the fan-out has no stable duration.** Lens 6 ran 328.8s at 50 tool
uses, then 126.3s at 15; lens 2 ran 312.5s, then 500.6s. Agents choose their own
depth, and that moves wall-clock by up to **2.6x** — far more than 17.9% ever
could. Do not quote a net saving; the case for overlapping is structural, not a
stopwatch. Anyone re-running this must pin nesting off in both conditions first:
the uncontrolled variable was not the CPU, it was how many agents each condition
decided to become.

**The finding that outranks the one it was looking for.** Running the fan-out
twice was an unintended reproducibility check on the review apparatus, and it
failed. The **invariant-6 lens returned opposite verdicts** on the same commit,
same model, same prompt, thirty minutes apart: "a real violation, well-evidenced"
(arguing the B6 comparator's rationale cited V7's already-measured D3/S11 score),
then "clean, no violation found" (arguing SPEC §12 criterion 5's text predates
V7 by three days at `7f69717` vs `a380a21`, which is correct and decisive). The
split is not random. Everything verifiable **by execution or grep** reproduced
exactly — `/code-review` found the same two bugs both times, lenses 3 and 4 were
clean both times. Only the lens requiring **interpretation of intent and
chronology** flipped. A single pass on lens 6 is a coin flip on the finding that
matters most; it needs an adversarial or multi-vote verify, and the reproducible
lenses do not.

**Why the slug fix moves nothing.** `Hybrid`'s node ids were already the product
of a *double* application of `slug_hypothesis_name` — trim, cut, trim, cut. The
fix trims **after** cutting, which makes one application byte-identical to that
double one, so **no node id moves and no transcript corpus is invalidated**. The
other direction — preserving the trailing underscore — would have moved real ids
*and* forfeited the `__candidate__` guard, which is the only thing keeping a
model-chosen name out of D5's reserved candidate slot and which
`grep -rn "__candidate__" tests/` showed was **untested**. Nothing recorded was
at risk either way: campaign ledgers store sixteen named floats and no id text.

**`"refused"` has two producers, and only one is a provider.** `Hybrid._admit`
returns it on `BudgetExhaustedError`, a path B6 owns outright since it inherits
V7's budget split by construction. A28's exclusion comment named only the
provider case, and its failure message said "the proposer is not drawing" — so a
budget fact would have sent the next reader to `uniform.py` for a bug that is not
there. Worse, `Hybrid._extend` **breaks the proposal loop at the first
`"refused"`**, so a budget-exhausted B6 stops proposing early: the silent
half-budget failure this gate exists to catch, arriving through the door the
exclusion left open. This is invisible in a test-file diff, which is why it is
here.

**Amending a landed gate was in bounds, and the reasoning is worth keeping.**
Invariant 6 forbids a gate written after the system it grades; `CLAUDE.md`'s
`DO NOT COMPRESS` comment already records that the absolute reading is wrong.
The operative rule is 2026-08-16's — *fix the instrument when it cannot measure;
do not touch the bar in the session that reads it.* **A28 has never been read**
(2026-08-19, "nothing was run"), so no measurement exists that these corrections
could have been fitted to. That is the strongest available position, and it will
not be available again: once A40 produces a B6 reading, the same corrections
would be a bar touched after the fact.

**Measured, for the record.** Full suite green at `-n 4 --dist loadfile`:
**183.35s**, 1515 passed, 7 skipped. The 2026-08-19 figure of 1272 tests is
superseded by growth, not by a speed-up; the 1505-test baseline in this entry and
the 1515 after it are the comparable pair.

**Left incomplete, and what it waits on.** Four A28 findings were deferred rather
than fixed, all documentation or coupling rather than behaviour, and none of them
red: the grid-coverage docstring justifies a **per-grid** test with the **pooled**
arithmetic (~200 expected per point against an actual ~13, a survivor of the very
pooling `/code-review` removed); two different standard-error figures are stated
for one quantity, both conservative against an actual ~12 SE; `DRAWABLE_OUTCOMES`
is a **third**, hand-maintained copy of an outcome vocabulary `hybrid.py`
documents as closed and cross-checked in two other places, so a sixth outcome
added correctly in both would turn A28 red saying "the proposer is not drawing";
and `test_a28_the_proposer_leaves_the_investigation_alone` claims a draw "reads
no investigation state and writes none" while asserting only the write half —
which `uniform.py` then cites as its guarantee. They want a ranked backlog entry,
not a hurried pass.

**Closes off.** The 30m37s figure stays disowned and the 12–15% figure stands for
four agents; **17.9% is the six-agent number and neither supersedes the other.**
`/preflight`'s "six concurrent agents at the ceiling" is now false by default —
across two runs the fan-out was **seventeen** agents, because the history lens
and `/code-review` each delegate further unless told not to. The skill now tells
them not to, which cut the history lens from ~253s to 162.1s and returned one
report instead of three fragments. The cost was never only wall-clock: a parent
that returns before its children makes completion **unobservable**, so the step
whose whole job is to say whether the tree is ready could say yes with work still
in flight.

**Addendum, same day — what the history lens found, and a second contention
figure.** Two corrections to the entry above, both from `/preflight`'s own
review of it.

**The carve-out reverses a sentence that no longer existed to argue with.**
CLAUDE.md carried an explicit boundary from 2026-08-17 (`5b15978`): *"four such
agents cost a `-n 4` suite 12–15% (measured 2026-08-16), so the exemption is for
agents running beside* each other, *never beside pytest."* That is precisely the
line this carve-out crosses, and the entry above never cites it — because it was
**already gone**. `f6fc85e`, this morning's CLAUDE.md trim, dropped it while
compressing the rule, hours before this work began. So the reversal was built on
top of a silent deletion rather than against the argument the deletion removed.
Recorded because a later reader of the entry above would otherwise never learn
that a sharper prohibition once existed. The reversal stands, and is now
deliberate rather than accidental: the 2026-08-17 wording rested on the
four-agent 12–15% figure and drew the boundary at *pytest*, where the measurement
since shows the boundary belongs at *whether the agent reads the suite's result*.
It was right about the cost and wrong about where to cut.

This is also the second time in two days that `f6fc85e`'s trim has been found to
have removed something load-bearing — the entry immediately above this one
records the first. A whole-file rewrite defeats a probe-based check, and it
defeats memory too.

**17.9% is not a constant; it scales with overlap.** The same six-agent fan-out
run against this change's own diff cost **183.35s → 190.97s, +4.2%**, not 17.9%.
Nothing about the configuration differed. What differed is how long the agents
were *alive*: against a whole commit they ran 350–500s each and overlapped the
entire suite, while against this smaller diff lens 3 finished at 61.2s and lens 4
at 133.5s. The tax is proportional to the overlap, which is the same shape as
2026-08-16's "alive for only ~38s of it" and was the reason that entry's figure
and this one's differ. **Quote the range 4–18%, and quote what the agents were
reading.** A single number here invites the same splice this file already
disowns once.

**Second addendum — two sharpenings the invariant-6 lens asked for, and one it
did not.**

**"Nothing was run" covers two senses and the argument needs only one of them.**
A28's own tests execute B6 for real: `_b6_run` drives `Hybrid.investigate`, and
has done on every green suite run since 2026-08-19. What has never happened is a
**scored reading** -- no ledger anywhere holds a B6 row, confirmed by grep over
`.cache/campaign/*.db`. The invariant-6 argument rests on the second sense only,
and should say so: the confound invariant 6 prevents is a bar moved to fit a
*measurement*, and a passing assertion is not one. Stated loosely, "nothing was
run" is false; stated precisely, "nothing has been scored" is true and is what
carries the case.

**The numbering was never the argument.** Amending in place rather than claiming
gate A44 sidesteps invariant 6's *narrow* clause on a technicality, and the lens
was right that it does not by itself answer the *broad* one -- amending
apparatus is moving apparatus however it is numbered. What actually carries
compliance is that **not one of the sixteen changes is looser**: the three
`match=` tightenings and the outcome split leave every pass/fail boundary
exactly where it was, the ten added tests are pure additions, and the two code
fixes are id-generation and ledger bookkeeping rather than anything that feeds a
score. That is the test 2026-08-16 sets, and it is the one that should be quoted
if this is ever questioned -- not the gate number.

**What no static read can exclude**, recorded because the lens was honest enough
to name it: an unrecorded interactive run of the pre-amendment tests could in
principle have shown a real `BudgetExhaustedError` and shaped how the `"refused"`
split was written. Nothing in the tree suggests it, and the split's strictness is
identical either way, so the exposure is to the *account* rather than to the
gate.

**Third addendum — the reservation is guarded at the wrong end, and that is
deferred rather than fixed.** Lens 2 found the sharper form of what this
session's slug work was circling. `sciagent.eval.scoring._enabled_value`
reserves `HypothesisId("__candidate__")` and then, if the entertained set
already carries that id, **silently overwrites its structure and replaces its
posterior mass** -- D5 moves and nothing raises. What prevents it today is a
string transformation in one system class, `Hybrid._admit`'s call to
`slug_hypothesis_name`. `Investigation.propose` and `HypothesisGraph.propose`
both accept any `HypothesisId` without a check, and a grep for the reserved id
across `src/` and `tests/` finds the reservation, three docstrings and two tests
of the slug -- and no assertion at the boundary it protects.

So the guarantee lives at a call site far from the thing it guards, which is
precisely the shape invariant 2 says to enforce with a runtime assertion rather
than a convention. One line at the point of reservation -- refusing an
entertained set that already holds the id -- moves it from argument to check.
**Not done here**, because it changes scoring behaviour and this session's scope
was the A28 amendment; it wants its own entry and its own gate. Recorded now
because the session that added two tests pinning the *slug* end of this is the
session most likely to believe the question closed.

Two further things lens 2 established that are worth not re-deriving: the
`__candidate__` foreclosure is **exhaustively verified**, not sampled -- every
string up to length 4 over an 11-character alphabet including Unicode
case-changing forms, plus 400,000 random printable strings, 0 violations. And
the lexicographic tie-break by which an agent-chosen name could influence D1-D6
**has never fired**: all 1,120 rows of the section 9 campaign hold no row where
the truth tied the leader and lost.

## 2026-08-20 — gate A43: a spec ambiguity resolved against the entry's own stated mechanism, and a gate line looser than the idea above it

**Spec ambiguity, resolved.** The backlog entry for A43 says to have
`report._at_address` "compare each row's battery term against the expected one
for its scenario instead of merely requiring the term to be present". Built the
way it is written, that filters superseded rows out *before*
`_refuse_mixed_batteries` sees them — which makes that A27 guard unreachable by
construction and turns `test_a27_two_batteries_are_not_pooled_into_one_cell` red.
The entry's **Touches.** line does not mention A27, so this was not a foreseen
cost.

**Decision.** `_at_address` is unchanged and still decides presence only. A new
`report._refuse_superseded_battery` runs between `_refuse_mixed_batteries` and
`_refuse_reseeded`. Mixed ledger → A27's message, unchanged; a scenario entirely
at a replaced battery → the new message naming both terms; declared battery →
reports unchanged.

**Why, beyond A27.** The mechanical collision is the smaller half. The real
argument is an asymmetry that is easy to get backwards: excluding a stale
`dimensions` row is honest because the caller *chose* the reading they asked for
— `scripts/report_matrix.py` takes `--metric-version` on the command line — but
**nothing names a battery**. The callback returns whatever the scenario declares
now, so there is no argument by which an operator could ask for the other
generation. Excluding would therefore drop rows they cannot ask back and hand
them a report whose replicate counts had quietly fallen. A27's reasoning carries
over verbatim: under the fourth invariant the recorded rows are legitimate and
the module cannot pick which campaign was meant.

The entry's sequencing note — "before A40; the re-derivation is what first puts
two generations of battery in one ledger, and this is the check that keeps them
apart" — reads as an argument for *selection*, and was the reason this looked
ambiguous rather than obvious. It is not: A40's two generations are separated by
`METRIC_VERSION`, which is already on `CampaignAddress` and already selected on.
The generations A43 is about are the ones that arise with **no version column
moving at all**, which is exactly why they have to be refused rather than
sorted.

**The gate line is looser than the idea it sits under, and the idea is what was
built.** `/test-review` established this with executable evidence rather than
argument: it ran eight candidate implementations against the four tests written
first, and two passed everything while still being wrong — refuse only when
*every* row at the address is superseded, and check only the first scenario and
stop. Both render a cell built wholly on a replaced battery whenever a sibling
scenario happens to be current. Read literally, "a ledger holding **only** rows
scored under a battery the scenario no longer declares" admits them, because
such an implementation does raise on that ledger. The **Idea.**'s "each row …
for its scenario" does not, and per-scenario is the unit
`_refuse_mixed_batteries` already uses. Built to the idea.

Worth keeping because the loose reading is not a strawman: it is the ledger a
re-derivation holds partway through — one scenario re-derived, the next not yet
— so the case A43 was sequenced before A40 to catch is precisely the one the
gate line, read on its own, would have let through.

**Closes off.** Do not "simplify" the check into `_at_address` later on the
grounds that it belongs beside the other address terms. The comparison and the
exclusion are different operations on different failures, and the docstrings at
both ends now say so. Do not weaken the per-scenario unit to a whole-ledger one
on the strength of the gate line's wording; if that line is ever treated as the
operative criterion on its own it should be amended to say *the scenario's* rows
rather than *the ledger's*.

**One latent weakness, recorded rather than fixed.** All twelve slice scenarios
currently declare the same battery, so on the *accept* path a callback consulted
per scenario and one consulted once with an arbitrary `ScenarioId` are
indistinguishable by any test that does not substitute a declaration.
`test_a43_each_scenario_is_checked_against_its_own_declaration` pins the
per-scenario consultation on the *refuse* path, where it is observable. Making
it observable on the accept path needs A27's `substitute_battery_ids`
monkeypatch fixture and was not taken here.

## 2026-08-21 — gate A40: the entry says bump `METRIC_VERSION`, and the mechanism that landed the next day says do not

**Decision.** `METRIC_VERSION` stays at 1.2.0. The re-derivation of the recorded
matrix is separated from the campaign it re-derives by `dimensions` and
`battery`, both already in the cell address, and by nothing else. Taken with the
user before any code was written, because the backlog entry instructs the
opposite in as many words.

**Why.** The A40 entry was written 2026-08-18. `DIMENSION_VERSION` landed
2026-08-19, at gate A26, and exists *precisely* so that a change to what D1–D6
mean does not have to move a version column that addresses the environment. Its
own comment records the measurement: `METRIC_VERSION` reaches every
`Discretisation`'s content hash through `str(MetricRef)`, so bumping it
invalidates every cached empirical table and forces a full rebuild on every
machine and in every worktree — for a change that touches no estimator. The
entry's premise was correct when written and was superseded a day later by
apparatus built for exactly this case.

The bump also buys nothing here. All 1,120 recorded rows carry `battery = None`
and `dimensions = None`, so `_at_address` already excludes every one of them on
two terms. A third term would separate rows that are separated twice over.

**The consequence is what made the entry's "labels the re-derivation as such"
load-bearing rather than cosmetic**, and it is worth stating because it inverts.
Under this decision the metric version is the one generation term that does
**not** move between the two generations — and it was the only one `render`
printed. `battery` had been rendered per cell since A27; the dimension reading
was rendered nowhere. So a re-derived report was textually indistinguishable
from one built on the superseded campaign, and the term that would tell them
apart was the one term absent from the page. `render`'s header now carries it.

**Closes off.** Do not "restore" the `METRIC_VERSION` bump later on the strength
of the entry's wording, and do not read the gate's name —
`test_a40_rederivation_selects_by_metric_version` — as requiring one: selection
by metric version is a property of `_at_address` that is tested as written, and
is independent of whether this repository's own ledger ever holds two metric
versions. If the entry is ever treated as the operative instruction on its own,
it should be amended to name the dimension reading instead.

## 2026-08-21 — gate A40: the proposal outcome taxonomy is T3, and the condition that would have favoured T2 did not arrive

**Decision.** `docs/OPEN-DECISIONS.md` §2 is settled: **T3**. Keep
`ProposalRecord`'s five fields, so `yield_fraction`'s denominator never moves,
and add a parallel non-scoring breakdown of causes for reporting. Taken cold, in
the window the A40 entry names for it.

**Why.** §2 recommended T3 while flagging that "T2 is the right answer if the
matrix is going to be re-run anyway", and asked for the decision to be taken
*after* the re-scoring question rather than before. That sequencing is what made
it decidable: T2's whole appeal was that a metric-version bump would be free
because one was happening anyway, and the decision above means none is. Nor
would `DIMENSION_VERSION` have covered T2 as a cheaper substitute —
`yield_fraction` is an agency metric computed in `eval/agency.py`, not one of
§8's six dimensions, so the constant that exists for dimension changes does not
address it. With the bump not free, T3's parallel structure is not redundant,
and T3's own argument — separating what a system *did* from why a call failed —
is the split the framework already enforces everywhere else.

**Closes off.** The decision is what gate A40 owed; the **implementation is not
A40's**. It belongs to `docs/BACKLOG.md`'s *"Audit the proposal path's failure
taxonomy"*, whose text already says the retiering is what remains of it, and
which carries the sequencing constraint that matters — not mid-campaign, and not
in a session that has just watched a particular arm fail against a particular
tier. `max_proposals` stays at 2 until the break asymmetry is settled in that
same change. Do not reopen T2 on the grounds that a future re-run makes the bump
free again without first re-reading why this one did not.

## 2026-08-21 — gate A40: the recorded corpus holds exactly one backend identity, and a replay presenting another misses everything

**Decision.** `scripts/run_matrix.py --replay` reads the backend identity off the
corpus rather than accepting one, and refuses a corpus holding more than one.
`sciagent.systems.llm.provider.RefusingProvider` therefore carries an
`id`/`model`/`settings` triple instead of being a bare stub.

**Why, and the measurement behind it.** A transcript address is a hash over the
brief **and** the backend's `id`, `model` and `settings`. Counted over
`.cache/transcripts/spec9.json`: **112 calls under exactly one triple**,
`("claude-agent-sdk", "claude-opus-5", "effort=high")`. So a replay offering a
stand-in under a stand-in's name computes a different address for every call and
misses the entire corpus — not a degraded replay but no replay at all, surfacing
as "your corpus does not match this code". This is not recoverable from the
diff: the corpus is gitignored, and the number is what rules out the obvious
implementation of handing the replay any convenient provider.

**Closes off.** Do not give `RefusingProvider` default identity fields. A default
would make the wrong replay silent again, and the guard against it —
`test_a40_a_replay_carries_the_recorded_backends_identity` — asserts the
provider's triple equals the corpus's, which a default would satisfy only by
coincidence. If a corpus ever legitimately spans two backends, the answer is one
replay per backend, not a precedence rule.

## 2026-08-21 — gate A40: the re-derivation is built and deliberately not run, and the gate's own third clause cannot fail

**Work left deliberately incomplete.** A40's instrument is built; the
re-derivation itself has not been executed. The entry costs it at ≈20 min for the
38 conventional cells plus ≈2 h to replay the 18 LLM cells, at API $0. The user's
decision was to land and gate the machinery first and start the run as a separate
deliberate act, on the reasoning that a 2h20m job is worth beginning knowingly and
that a replay miss mid-run is a finding rather than a failure.

Until it runs, §12's capability criteria stay undecidable and the preregistered
contrast stays unanswered — the same state the entry describes, now blocked on an
operator rather than on code. Nothing in the ledger has moved: the 1,120 recorded
rows are untouched and remain unreportable, since they carry no `battery` and no
`dimensions`.

**What was actually missing, which the gate line does not say.** The report layer
already selected by metric version — `_at_address` has compared
`key.metric_version` since it was written — and `TranscriptStore` already
implemented replay-with-raise-on-miss. Both of the gate line's outer clauses were
satisfied by standing code. What did not exist was the operator path:
`scripts/run_matrix.py` opened its store in `RECORD` unconditionally and refused
an LLM arm without a live provider, so the corpus could not be replayed through a
campaign at all.

**The gate's third clause is unfalsifiable as written, and this is the note that
matters most.** `store.misses == 0` holds of **every** `REPLAY` store that has
ever existed, including an empty one that replayed nothing:
`TranscriptStore.resolve` increments `_misses` only on the branch that calls out,
which is reachable only in `RECORD`. Asserting it alone would pass against an
implementation that never opened the corpus — and `/test-review` demonstrated
exactly that by building one, which passed every CLI test in the class until the
store's *addresses* were compared against the corpus's rather than only its mode.

**Closes off.** The assertions surrounding `store.misses == 0` in
`tests/acceptance/test_a40.py` are not redundant with it and must not be
"simplified" away: that the campaign *finished* under a `REPLAY` store, that the
store the CLI built holds the corpus's addresses, and that a short corpus *stops*
the campaign are what carry the meaning the counter does not. If the gate line is
ever treated as the operative criterion on its own it should be amended to say
that the replayed campaign *completes*, which is the falsifiable form of what it
was reaching for.
## 2026-08-21 — both cold decisions taken, and A25 deferred behind the reporting fixes

**Decision.** Three, settled in one session after both write-ups in
`docs/OPEN-DECISIONS.md` were read in full. That file said it existed so a later
session would not have to take these hot; this is that session.

1. **SPEC §12 criterion 4 — C1.** Power against size on the named Stage A
   probe: on S11, V7's probe fires at a rate at least matching B1's on the same
   probe, at a false-positive rate on S1–S7 and S9 no higher than B1's. The
   harness evaluates the probe for **every** arm regardless of whether the arm
   consults it. The honesty cost — "B1 detected" names a check B1 never
   consulted — goes into §12's wording rather than being left for a reader to
   find. The user's choice, and the document's own recommendation.

2. **The proposal outcome taxonomy — T2, with T1a folded in.** Split the tier:
   `transport` and `faulted` beside `refused`, on a metric-version bump. While
   in the file, apply T1a to the fault column — the conditions at
   `agent_sdk_provider.py` 267, 327, 361, 448, 456, 462 and
   `anthropic_provider.py` 182 are faults of the machine that a campaign should
   stop on rather than score, so they leave the tier instead of being recounted
   inside it. `max_proposals` stays at **2** until the break asymmetry is
   settled in the same change. Recommended by me, accepted by the user, who
   asked for a recommendation rather than choosing from the four.

3. **A25 (QTM real-data grounding) drops from rank 7 to rank 15**, behind
   A30/A31/A34/A37. The user's call, in their words: answer the research
   question on synthetic data first.

**Why.** On C1 there is nothing to add to the write-up; it was taken as
recommended.

T2 is the interesting one, because the document recommends **T3** and this goes
against it. It also names its own defeater in the next sentence: *"T2 is the
right answer if the matrix is going to be re-run anyway, in which case the
metric-version bump is free and T3's parallel structure is redundant."* A40 is
that re-run and bumps `METRIC_VERSION` regardless, so T3 would buy avoidance of
a cost already being paid, at the price of carrying a parallel non-scoring
structure forever. The document's further instruction — take this *after* the R5
re-scoring question — is satisfied rather than pending: R5 is sequenced after
item 15 and would itself force another re-derivation, so there is no future in
which the bump gets cheaper by waiting. T1a rides along because a condition that
propagates was never scored, so moving it costs nothing in recorded numbers.

A25's deferral is a sequencing call, not a judgement on the entry. At rank 7 it
sat between the re-derivation and every item that makes the recorded matrix
readable — a 1–2 week track opening before the 1,120 rows already paid for could
be reported. Its own risks are unchanged and still recorded in `docs/BACKLOG.md`
(no formal SCEDC licence text; template-matching false detections that mimic the
consensus edit's signature).

**Closes off.** A29 is no longer held: OPEN-DECISIONS §1 was its only blocker,
and at rank 3 it already sits ahead of A40. It goes first — re-deriving before
the probe is arm-symmetric would produce a matrix that still cannot answer the
preregistered contrast, which is the thing the re-derivation exists to make
answerable.

The break asymmetry is now load-bearing rather than latent: `Hybrid._extend`
breaks on `"refused"` and continues on the other four, so any retiering changes
which outcomes break. It must be settled inside the T2 change, not after it.

**Not decided here, and deliberately.** A42 stays held on the D4 comparison-set
question; nothing in this session touched D4's semantics. Nor does anything here
schedule the F11 TEST campaign — three exist for the life of the project, none
is in SPEC §11's list, and the slice matrix remains exploratory by construction
whatever it says. What the plan above buys is the right to spend the first one,
not the spending of it.

## 2026-08-21 — A40 is ranked sixth and is logically last: the build order re-cut around what a replay needs

**Decision.** Gate **A44** is minted for the retiering T2 decided earlier today,
on the previously ungated entry "Audit the proposal path's failure taxonomy",
and the open entries are re-ranked so that **everything touching a number the
recorded campaign reported lands before A40's version bump**, not after it:

```
 3  A29  probe arm-symmetry (C1)          10  A33  substrate versioning
 6  A44  retiering (T2 + T1a)             11  A34  comparator parity
 7  A31  payload: agency, flags, masses   12  A40  re-derivation
 8  A30  verifier adjudication            13  A32  16  A38 (landed)
 9  A35  refusals replay                  14  A37  17  A39 (landed)
                                          15  A36  18  A25  19  A41  20  A42 (held)
```

**Why.** A40 sat at rank 6 with four entries behind it that each move something
it re-derives, so `/next` walking the ranks in order would have paid for the
re-derivation and then invalidated it. Three couplings, in descending severity:

**A35 is a hard blocker, and was ranked six places behind A40.** A40's gate
requires `store.misses == 0` across a replay of all 18 LLM cells; A35 records
that "any replicate the ledger scored as *refused* is currently unreplayable —
replay of the recorded campaign would crash at the first refusal-containing
address". `/next` would have re-run the 38 conventional cells, entered the
replay, and died.

**Whether that crash is certain or vacuous cannot be determined from the
artefacts, which is itself the finding.** Checked directly rather than assumed:
the ledger's reading carries sixteen fields over all 1,120 rows — `correct`,
`d1`–`d6`, `experiments`, `identified`, `inadequate`, `leading_mass`,
`log_score`, `n_held_out`, `ppc_p_value`, `structural_distance`, `truth_mass` —
and **not one proposal-outcome field**; `.cache/transcripts/spec9.json` holds
112 calls whose record kind has no `stop_reason` and no outcome. A refusal in
the recorded campaign stored nothing anywhere, so the number of them is
unrecoverable from the tree. That is A31's omission and A35's missing record
kind observed from the other end, and it is why both now precede A40.

**A31 and A30 feed the payload A40 renders.** Landing them after the bump leaves
the re-derived matrix without the autonomy fraction, the gate flag and the mass
decomposition, so §12 criteria 4, 9 and 11 stay undecidable from the very report
the re-derivation exists to produce — and a second bump would be needed.
**A34** moves D3 if the parity route rather than the bounding-declaration route
is taken, which stales the re-derivation the same way.

A44's gate is written to be gradeable on constructed failures with no live call,
which keeps it framework apparatus rather than a grade of a built system.

**Closes off.** A36 must stay *after* A40 and does, at 15: it bumps the
transcript address version, which is what makes the recorded corpus unreplayable
by design. Anything later moved ahead of A40 needs the same question asked of
it — does it move a number A40 re-derives — and the answer written in the entry.

**A correction against the table proposed in conversation**, recorded because
the ranks differ from what was described: ranks 4 and 5 are **not** free. A28
and A43 landed and, per this file's convention, keep their ranks; only the
heading gains a marker. A44 therefore took 6 and the rest shifted down, rather
than A44 taking 4 with A40 at 10.

## 2026-08-21 — A40 and A35 meet at one catch clause, and the boundary is pinned before either lands

**Decision.** A35 fixes the replay hole by **filling the corpus** — recording
refusals as first-class transcripts — and never by widening
`Hybrid._propose_once`'s `except ProviderError` to `ProposalError` or by making
`TranscriptMissError` a subclass of `ProviderError`. Written into both the A35
and A44 entries in `docs/BACKLOG.md`, because A44 retiers that same catch.

**Why.** The A40 worktree, building the re-derivation concurrently, raised the
question: its
`test_a40_a_replay_stops_on_a_miss_rather_than_recording_a_refusal` pins that a
REPLAY miss raises rather than being converted into a recorded refusal, and if
A35's design were to widen the catch instead, the two gates would contradict.

It is not, and the hierarchy says so. Verified in `src/sciagent/core/errors.py`:

```
ResearchSystemError
├── SystemConfigurationError   :357   <- RefusingProvider raises this
└── ProposalError              :374
    ├── TranscriptMissError    :394
    └── ProviderError          :433   <- the only class hybrid.py catches
```

A35's **Idea.** already names the compatible route and describes the sibling
relationship as a fact rather than as something to change. So the contradiction
is reachable only by an implementation choice, which is why it is now pinned in
prose in both entries rather than left to be discovered by a red gate.

**The reason it matters is the same one T1a rests on.** A40's `RefusingProvider`
raises `SystemConfigurationError` and not `ProviderError` deliberately: the
latter is caught at `hybrid.py` and recorded as `"refused"`, so raising it there
would turn a harness fault into a scored datum. That is the identical principle
T2/T1a settled hours earlier from the other end — machine faults propagate, they
do not score — arrived at independently by a session that had not read the
decision. Two routes to one boundary is the strongest evidence available that
the boundary is real.

**Closes off.** A44 must widen the catch by *kind* — adding `transport` and
`faulted` beside `refused` — and never by moving up the hierarchy. Any later
change to `core/errors.py`'s `ProposalError` subtree now has two gates reading
it and should check both.

**On sequencing, from the same exchange.** T2's version-bump argument bears on
*running* the re-derivation, not on A40's code, and the run was always a
separate step — so nothing in the A40 worktree is stranded by the re-ranking.
What it does invert is the premise for the run itself: re-deriving before the
retiering lands means re-deriving twice, which is the outcome the rank order
exists to avoid. The build order stands as re-cut earlier today at the user's
direction; A40's code may land out of rank, its run may not.

## 2026-08-21 — supersedes today's T2: the bump it called free was never going to be paid

**Supersedes** the taxonomy half of *"both cold decisions taken, and A25
deferred behind the reporting fixes"* (02a4e3d) earlier today. Its §12
criterion 4 half — **C1** — is untouched and stands. The taxonomy is **T3**, as
`docs/OPEN-DECISIONS.md` §2 recommended, and the A40 worktree's concurrent
decision to the same effect is the one that governs.

**Decision.** T3: `ProposalRecord`'s five fields stay exactly as they are,
`yield_fraction`'s denominator never moves, and a parallel non-scoring breakdown
of causes carries the diagnostic question. **T1a survives** — machine faults
leave the tier by propagating, which moves no recorded number because a
condition that propagates was never scored. Gate A44 stays minted; its gate line
loses the `METRIC_VERSION` clause and gains the pin that the recorded campaign's
`yield_fraction` is bit-identical across the change.

**Why the earlier entry was wrong.** Its whole argument was one conditional
lifted from the write-up — *"T2 is the right answer if the matrix is going to be
re-run anyway, in which case the metric-version bump is free"* — plus the claim
that A40 bumps `METRIC_VERSION` regardless. The second half is false. It was
read out of the A40 backlog entry, written **2026-08-18**, and
`DIMENSION_VERSION` landed at gate A26 on **2026-08-19** precisely so that a
change to what D1–D6 mean need not move a column that also addresses the
empirical tables. `eval/scoring.py:71-77` records the measurement, not an
argument: `METRIC_VERSION` reaches every `Discretisation`'s content hash through
`str(MetricRef)`, so bumping it invalidates every cached table and forces a
**3m11s rebuild on every machine and in every worktree**. A40 re-derives on
`dimensions` and `battery` and bumps nothing.

So the bump T2 needs is not free; it is that rebuild, everywhere, for a change
that touches no estimator. `DIMENSION_VERSION` would not have absorbed it either
— `yield_fraction` is an agency metric under §12 criterion 11, not one of §8's
six dimensions.

**What this says about how the earlier decision was taken.** The premise was
checkable in the source at the time and was checked only against the backlog
entry that the source had superseded a day earlier. A decision resting on one
conditional is only as good as the conditional's antecedent, and that antecedent
was a fact about the code, not about the write-up. Recorded because the failure
mode is reusable: this file's entries age against the tree, and an entry written
three days ago describing apparatus is evidence about intent, not about what is
there now.

**Closes off.** Do not re-open T2 on the grounds that some later change bumps
`METRIC_VERSION` anyway — R5 included. The bump's cost is the table rebuild and
it is paid by whoever forces it; T3 removes the reason to force it at all, which
is a better position than waiting for someone else to pay.

The rank order set earlier today stands, but two of its four reasons were T2's
and are now void: A44 no longer *must* precede A40 because it no longer moves
`yield_fraction`. What still binds is unchanged and independently confirmed by
the A40 session's own report — **A35 blocks the re-derivation run**, which "is
expected to crash at the first refusal-containing address", and A31/A30 must
land before the run or the re-derived payload cannot answer §12 criteria 4, 9
and 11. A40's *code* is finished and may land out of rank; its *run* waits.

## 2026-08-22 — gate A29: the probe is read before the run, and criterion 4's power clause becomes an equality

**Decision.** `ScenarioRun.probe` is computed in `run_scenario` **between
`_run_stage_a` and `system.investigate`** — on the graph the harness hands every
arm, with the Stage A reading recorded and nothing else. C1's *"the harness
evaluates the probe for every arm regardless of whether the arm consults it"* is
discharged by evaluating it **once, before any system code runs**, rather than
once per arm afterwards.

**Why. The entry's own two halves cannot both be met literally, and that is the
ambiguity this settles.** The **Idea** says the probe is evaluated "uniformly at
the gate point", which reads as SPEC F6's consultation point — mid-run, after an
arm has entertained a library and spent half its budget, and therefore
arm-specific. The **Gate** says the verdict is "identical whichever system ran",
which requires a value no arm can move. The Gate is the contract, so it wins. If
the backlog wording is ever revised it should say *before the run*.

The rejected reading is not merely weaker; **it is already withdrawn**. Reading
the probe per arm at the end of a run is the `ScenarioRun.adequacy` field written
and removed on 2026-08-16, and `eval/campaign.py` has carried the reason ever
since: the end-of-run posterior is the one the arm's *proposals* moved, so a
system that successfully proposed a structure explaining the probe is recorded as
having failed to detect. It also cannot satisfy the Gate arithmetically — the
recorded end-of-run S11 probe is 0.0112 under V7 and 0.0294 under B1, and V7's
own gate saw 0.0128.

**The cost, stated because it is real and runs against the change — and it is
larger than this entry first claimed.** Both arms now read one instrument, so
C1's power clause — *"V7's probe fires at a rate at least matching B1's"* — is
satisfied **by construction**, not by measurement.

The first version of this paragraph then said the false-positive term "stays
falsifiable", and **that is false**. C1 words *both* clauses as comparisons of V7
against B1 — the second is "at a false-positive rate on S1–S7 and S9 **no higher
than B1's**" — and `replicate_seeds` already pairs every arm on one seed
sequence. Two arms reading one instrument at one seed agree exactly, so the size
clause is an equality on S1–S7 and S9 exactly as the power clause is on S11.
**SPEC §12 criterion 4 as re-specified cannot fail, under either clause.** Caught
by `/preflight` — `/code-review` and the invariant-6 lens reached it
independently — and the demonstration was sitting in the gate's own tests, which
assert the arms' probe p-values identical *on S1*, one of the scenarios the size
clause is read on.

What this buys is therefore an **instrument**, not a bar: the probe is
well-defined for every arm, discriminates S11 from the in-library scenarios, and
is recorded and reported per cell. Restoring a bar needs an *absolute* threshold
in place of the comparison. That is not taken here — it changes what V7 is graded
on, which is invariant 6's territory and the user's call, and it belongs in
`docs/BACKLOG.md` to be written up and taken cold as C1 was. Both the SPEC
wording and `ScenarioRun.probe`'s docstring now say this outright rather than
claiming a falsifiability the code does not deliver.

**The reusable lesson is about the shape of the error, not the arithmetic.** C1's
diagnosis was that criterion 4 compared two arms on two different checks. Giving
both arms one check fixes the comparison by making it vacuous, and the write-up
that recommended it did not notice because it was arguing about *which* check,
not about what a comparison between identical readings could still mean. A
re-specification that removes a confound by removing the variance removes the
criterion with it.

It is not degenerate for being symmetric. B1 holds only the null and proposes
nothing, so its live set never changes and its posterior never moves: the
pre-run value **is** B1's reading throughout its run, which is the 0.0294 that
the 2026-08-16 table measured firing on S11 alone across the twelve.

**Measured, and expensive to reproduce — the whole-record flag is arm-dependent
in its *verdict*, not only its p-value.** On S11 at the scenario seed, the
harness probe reads 0.029409 (fires) for every arm, while the whole-record check
reads:

| arm | whole-record p | fires |
|---|---|---|
| B1 | 0.045811 | **yes** |
| V1 | 0.527271 | no |
| rotating (library + rotation) | 0.223485 | no |

B1 fires because it holds only the null, so its space is inadequate on eleven of
twelve by construction — `docs/OPEN-DECISIONS.md`'s own table says so ("full-record
ppc | B1 on 5 of 12 (S1, S5, S8, S10, S11)"). **This nearly shipped a gate that
could not pass.** The first draft of `test_a29` asserted the two flags disagree
for *every* arm on S11, which is true of V1 and V7 and false of B1, and the
whole point of the scenario is that it is where §9's contrast is read. Found by
`/test-review` before any implementation existed.

**A transposition of the two boolean labels passed everything.** Swapping
`inadequate` and `probe_inadequate` inside `reading_of` passed all four A29 tests
and the full suite: the tests compared the two payload keys to each other and
never to the attribute each is named for, and the one sibling that pins
`reading_of` runs on S9, where both verdicts are `False` and a boolean swap is
invisible. Closed by asserting each payload entry equals its source attribute.
The reusable part is the shape: a test that pins *two* derived fields against
each other pins neither to its origin, and picking the fixture scenario where
they agree hides it.

**Closes off.** Criterion 4 is answerable for the first time, which is one of the
three the previous entry said must land before A40's re-derivation *runs*; A31
(criteria 9 and 11) and A30 still bind, and A35 still blocks the run outright.

**Left deliberately incomplete, so the silence is not read as an oversight.**
`contrast()` still conditions on the `inadequate` payload key — the whole-record
check — and not on the new probe flag. SPEC §9 says its primary contrast is
"conditional on inadequacy detection", and under C1 the named detection check is
now the probe, so there is a live argument that the conditioning should move. It
was not taken here: the entry's **Touches** names §12 criterion 4, `eval/campaign.py`
and the payload, and not §9's preregistration, and changing what a preregistered
contrast conditions on is its own decision rather than a consequence of this one.
It belongs with A31, which this entry's own **Touches** line says it couples to.
Not recorded here and deliberately: the `DIMENSION_VERSION` bump to `spec8/3`,
which is visible in the diff and argued in the constant's own comment.

## 2026-08-22 — gate A44: the taxonomy splits by cause beside the record, and the clause meant to pin it cannot be read

**Decision.** T3 and T1a are implemented as `docs/OPEN-DECISIONS.md` §2 and the
2026-08-21 supersession settled them. What is recorded here is only what the diff
does not say: four resolutions, one measurement, and two things left undone on
purpose.

**The gate's third clause is unsatisfiable as written, and the substitution is
recorded rather than quietly made.** It asks that `ProposalRecord`'s five fields
be *"pinned by the recorded campaign's `yield_fraction` coming out bit-identical
across the change"*. Checked rather than assumed: `.cache/campaign/spec9.db`
holds 1,120 rows whose `reading` carries **sixteen keys and not one
proposal-outcome field** — `correct`, `d1`–`d6`, `experiments`, `identified`,
`inadequate`, `leading_mass`, `log_score`, `n_held_out`, `ppc_p_value`,
`structural_distance`, `truth_mass`. `yield_fraction` cannot come out
bit-identical because it cannot come out at all. That is A31's omission seen from
a third side, after A35 and A40 each found it from their own. Recomputing it
would mean replaying the eighteen LLM cells, which A35 records as crashing at the
first refusal and which contradicts the entry's own *"no live call is needed to
grade it"*. Substituted: literal fractions under the pre-change taxonomy, plus
the property a recorded campaign would have been evidence **of** — retagging a
refusal across all five refused-tier causes moves the fraction nowhere.

**One half of the clause was recovered exactly, and is a measurement worth
keeping.** `METRIC_VERSION` reaches the recorded address through
`MetricRegistry.version`, and every one of the 1,120 rows carries
`metrics/9b1c54c9d49f49f656c30e32d21d4a7b`; the live catalogue still addresses to
it. Asserting *that* rather than the constant is the stronger check, because the
constant goes green on any change that leaves the string alone while moving what
the catalogue addresses over. The ledger's `env_version` beside it is
`pointproc/pointproc/1.1.0+1.2.0+1.1.0`.

**Two rows `docs/OPEN-DECISIONS.md` §2 calls "arguably misfiled" were kept in the
record, and this is a decision against that aside.** A stream draining with no
`ResultMessage` and a session ending `is_error`/non-`success` are dead-session
conditions, and §2 floats moving them out behind transport. A44's gate text says
a dead session reads **as transport** — a cause in the breakdown, not a
propagation — and the gate is the criterion. Pinned by type rather than by
intent: `ProviderError` and `ProviderUnavailableError` are siblings, so the
enumeration test fails if either row is later moved out. Anyone revisiting this
is revisiting the gate, not tidying a loose end.

**Nothing is re-filed *between* the five tiers, and the constraint is sharper
than it looks.** A provider response that will not parse stays under `refused`
and gains a cause beside it, which reads wrong and is not: `_extend` breaks on
`"refused"` and continues on `"malformed"`, so re-filing it moves no field
definition and still moves how many requests a run makes. That is T2 wearing T3's
clothes, and it is the one way this change could have spent the bump it exists to
save.

**The two sub-decisions the entry deferred, taken at the user's direction.**
`EditNotInGrammarError` at apply time **propagates**: reaching it means the agent
grammar licensed a structure the edit grammar refuses, which is a divergence
between two framework-supplied grammars rather than a fact about the candidate,
so `CANDIDATE_FAULTS` is *not* widened — the same reasoning that keeps
`DeterminismError` out of it, reached from the `GrammarError` family instead. The
break asymmetry is settled by **not** moving the break rule, since which outcome
breaks decides how many requests a run makes; the allowance becomes a guarded
ceiling instead, so raising it reopens the question in the same change.

**Two test-first defects, found by `/test-review` before any implementation
existed, and the first is worth the space because it is self-fulfilling.** The
draft imported `METRIC_VERSION` from `sciagent.eval.scoring`, where no such name
exists — the real one is in `environments/pointproc/catalogue.py`, and invariant
1 is why it is not in `sciagent/` — and asserted it equalled `"m1"`, a literal
invented to look like a version. The cheapest way to green that red is to *add*
`METRIC_VERSION = "m1"` to `eval/scoring.py`, at which point the assertion is a
tautology over a constant created to satisfy it and the gate reports green over
exactly the bump it was written to forbid. **A test-first red that names a
missing symbol is an invitation to create it**; watching one `ImportError` and
stopping is how the rest of the missing-name set goes unexamined. The second: the
propagation check was written as `not isinstance(error, ProposalError)`, which is
false as a premise — `ProviderUnavailableError` *is* a `ProposalError` by design —
and worse than absent, since the obvious way to satisfy it is to reparent that
class off the family, silently changing the `except ProposalError` guards in
`systems/llm/transcripts.py` and breaking the very A35/A40 boundary this gate
holds. The reusable shape: **a red that points at the wrong fix is more dangerous
than no test**, and a test that reads the catch set from the code under test
cannot notice the catch set widening.

**Eleven existing tests moved tier, and none moved boundary.** The five retiered
conditions had assertions in `tests/test_llm.py` naming `ProviderError`. Every
`match=` string is byte-identical and only the class changed — to one *outside*
`ProposalError`, which makes each assertion stricter in the sense that matters,
since it now pins that `Hybrid` cannot catch the condition and score it. Recorded
because "eleven tests edited in the change that made them fail" is the shape
invariant 6 exists to be suspicious of, and the answer to that suspicion is that
not one of them is looser.

**Closes off.** `max_proposals` cannot be raised without reopening the break-set
question in the same change, which is what the ceiling is for. Nothing here moves
a number A40 re-derives, so the re-ranking that put A44 at 6 and A40 at 12 is
discharged for this entry. The unreadable third clause is A31's business: until
the ledger payload carries a proposal-outcome field, no gate after this one can
pin anything against a recorded `yield_fraction` either, and A31 should be read
as owing that.

## 2026-08-22 — gate A31: the payload carries agency and the masses, and two debts handed to it are left open

**Decision.** The entry's **Idea** and the gate line settled the field list, and
`docs/BACKLOG.md` rank 7 is what implements it. What is recorded here is only
what the diff does not say: one place the gate's own wording would not have
discharged its entry's purpose, one test-first defect found before any
implementation existed, two debts earlier gates assigned to this one and which
this one does **not** pay, and a verification result I cannot fully account for.

**The gate line names two masses and criterion 9 needs three, so a third was
added rather than the gate satisfied.** The **Idea** asks for
`null_mass`/`abstain_mass`; §12 criterion 9 is *"null and abstain mass exceeding
**any single defect's** mass"*. The payload's existing `leading_mass` is not that
third quantity — it is the maximum over *every* hypothesis, the null included —
so wherever the null leads it is `null_mass` restated and says nothing about the
largest defect. Measured on this gate's own fixture rather than argued: on S9 all
three arms report `leading_mass = 1.0`, `null_mass = 1.0` and a largest defect of
`0.0`. S9 and S10 are the two scenarios criterion 9 names, and they are precisely
the ones where the null is *supposed* to lead — so the criterion would have stayed
undecidable in the only case it exists for, and closing it later would have cost a
second `DIMENSION_VERSION` bump. Taken at the user's direction, with the gap put
in front of them before any edit. **The general shape is worth more than the
instance: a gate line can be a faithful abbreviation of its entry and still be
satisfiable without discharging it, and the entry's Rationale — here *"criterion 9
is not decidable from the report"* — is what settles which.**

**`/test-review` returned TOO WEAK on the render clause, and the finding is the
one thing here nothing else would have caught.** The clause is *"render shows the
autonomy fraction beside every dimension block"*. The draft scanned the whole
rendered string for lines beginning `  autonomy` and asserted there were as many
as there were cells — **cardinality, where the standard specifies location**. A
`render` appending a trailing agency section after all six blocks emits exactly
six such lines carrying exactly the right values, and satisfies none of "beside
every dimension block". That is not an adversarial implementation, it is the
*cheaper* one: a footer needs no `CellSummary` field, while an in-block line drags
in the field enumeration in `tests/test_report.py`'s determinism digest. Closed by
locating each block from its own `system / scenario` heading and asserting inside
it, with a whole-report count kept beside that so location and cardinality are
asserted together. **Reusable: a test that counts occurrences of a thing the
standard *places* has tested the wrong property, and the two go green together.**

**Two debts were handed to this gate by earlier entries. Neither is paid, and
saying so is the point of recording it.**

- **A44's, verbatim:** *"until the ledger payload carries a proposal-outcome
  field, no gate after this one can pin anything against a recorded
  `yield_fraction` either, and A31 should be read as owing that."* It is still
  owed. `CellReading.agency` holds a `ProposalRecord` and a `ProposalCauses`,
  so the data now reaches the cell — but `as_payload` emits neither, and no
  recorded row carries an outcome count. What changed is only that the field is
  one line from the ledger instead of dying in `runner.execute`. A31's own gate
  line and **Idea** name agency, the two flags and the masses and no proposal
  outcome, so adding one would have been a third widening in a change that had
  already taken one.
- **A29's:** `contrast()` still conditions on the `inadequate` payload key — the
  whole-record check — and not on the probe flag, and that entry says the
  argument for moving it *"belongs with A31"*. Not taken, for the reason A29
  itself gives: what a **preregistered** contrast conditions on is its own
  decision, and this entry's **Touches** names the payload rather than §9's
  preregistration.

**Verification, including the part I cannot account for.** Two runs on a
byte-identical tree (`9cb1df84…`) came back **1581 passed, 7 skipped**, at
169.64s and 148.66s under `uv run pytest -n 4 --dist loadfile`; `mypy` and `ruff`
clean. Before those, two runs were lost. The first died during xdist worker
bring-up with `OSError: [WinError 1450] Insufficient system resources exist to
complete the requested service` on a numpy source file — *"no tests ran in
13.41s"*. The second was killed by a session boundary at 72% having shown **two
failures**, at the 49% and 54% progress marks, which I never identified: `-q`
prints no name inline and the run ended before its summary. The attribution to
machine resource pressure following the `WinError 1450` is **inference and not
evidence**, and it is written down here rather than dropped because a determinism-
pinned repository is the wrong place to let two unexplained reds disappear into a
temp file. Anyone who sees a `WinError 1450` here should suspect the same cause
and should not assume this entry cleared it.

**Not recorded here and deliberately:** the `DIMENSION_VERSION` bump to
`spec8/4`, which is visible in the diff and argued in the constant's own comment —
the 2026-08-22 A29 entry declined to record its own bump on exactly that ground
and the reasoning is unchanged. Likewise the truthiness-versus-`is not None`
partition in `largest_defect_mass` and the `nan` crossing in `as_payload`: both
would be findings worth keeping if they lived only here, and both are argued at
length in the docstrings of the functions that depend on them.

**Closes off.** Criterion 11 is met for any campaign recorded from here — the
autonomy fraction is derived in `reading_of`, which gives
`sciagent.eval.agency` its first caller outside a test. Criterion 9 is decidable
from a rendered report for the first time. Criterion 4's two observables were
already separable at A29 and are pinned again here. **`yield_fraction` remains
unpinnable against any recorded row**, so A44's third clause stays substituted
rather than satisfied, and the next gate to want it inherits A31's debt rather
than A44's. `tests/acceptance/test_a44.py`'s `DIMENSION_VERSION` literal moved to
`spec8/4` in this change: that assertion exists to pin that *A44's* diff leaves
the term alone, so updating it for the scoring change it was written to allow for
preserves its purpose — but it is an edit to a landed A-test, which is a shape
invariant 6 asks to be suspicious of, and it is named here so it can be audited
rather than found.

## 2026-08-22 — gate A31, after review: three findings fixed and one threat-model change left standing

**Decision.** `/preflight`'s six lenses ran against the A31 diff before it was
committed. Four returned clean; `/code-review` returned three findings, all
fixed; two lenses returned a suspicion each, and one of those is a real change
to what the framework's audit covers, left unaddressed on purpose. This entry
exists for that last item — the three fixes are in the diff and need no record.

**Invariant 2's lens found the thing this change actually alters, and it is not
in the diff.** `HypothesisNode.proposed_at` is compared by **neither**
`_reconcile` nor `_audit` in `sciagent/eval/campaign.py` — the reconciliation
checks recorded experiments, each hypothesis's `program_edit`, and the live set,
and stops there. That was inert while nothing scored the field: before this gate
`agency_metrics` had no production caller, so `proposed_at` reached only SPEC
F9's confirmatory-claim rule in `verify/logical.py`. **A31 makes it decide
`entertained`, `escalated` and `autonomy_fraction` in every recorded row.** A
graph differing from the real one *only* in `proposed_at` passes every clause of
both checks and moves the autonomy fraction toward 1.0.

**Why it is left standing.** It needs private-attribute reach, and `_reconcile`'s
own docstring already scopes that out in as many words — *"a caller able to reach
the private engine to swap it could equally reach anything else. This function is
a check with a scope, not a containment proof."* No shipped system does it: a
sweep of `src/sciagent/systems/` and `src/environments/` for `investigation._`,
`._graph`, `._proposed`, `._engine` and `._history` returns nothing outside
`self._`. Widening `_reconcile` is a change to the reconciliation contract that
every arm is scored under, which is not something to slip into the gate that
first noticed it. **What is recorded here is the fact, not a plan: a field that
was audit-exempt because nothing scored it is now scored, and that is a change in
the framework's threat model rather than in its code.**

**A second suspicion, deliberately not acted on and worth a sentence so it is not
rediscovered as new.** `FrozenDict.__eq__` delegates to `dict.__eq__`, so two
`LedgerEntry` values carrying an otherwise-identical `nan`-bearing reading
compare unequal under the dataclass's generated `__eq__`. This is real and
predates A31. It is unreached: the conflict-detection path in
`CampaignLedger.append` compares `reading_digest` strings, never raw values, and
`float('nan').hex()` is the stable ASCII `'nan'` for every NaN bit-pattern — which
is what makes a `nan` payload deterministic under content addressing, and was
checked rather than assumed. D2 and D3 have shipped `nan` on the same path since
before this gate.

**Closes off.** The autonomy fraction is only as trustworthy as `proposed_at`,
and `proposed_at` is the one input to a recorded number that no reconciliation
clause compares. Anything that later widens `_reconcile` should treat this as the
reason. Nothing here changes what A31 landed.

## 2026-08-23 — gate A30: the verifier gains a production caller, and the test written for it pointed at the wrong implementation

**Decision.** `verify()` is called from `src` for the first time, by
`adjudicate()` in `eval/campaign.py`, reached from `reading_of`. Four things
about it are not recoverable from the diff.

**1. The claims parameter exists because criterion 8's zero was unfalsifiable,
not merely unmeasured.** `claims_from_run` filters to `mass > 0.0` and
`diagnose` gives a rejected hypothesis *exactly* zero, so the generated
population **structurally cannot contain a zombie**. No amount of running the
campaign would have moved the count. That is why the gate line asks for an
*injected* claim and why the injection had to reach the recorded payload rather
than a helper. Claims are structure, so the seam does not touch invariant 2: a
caller says which claims are adjudicated and nothing about how any is decided.

**2. Accumulation is within a run and never across a campaign.** A claim the
verifier *accepts* enters `ClaimContext.accepted` for the claims after it —
accepted only, because that field is documented as the claims already
*admitted*. Without it, `contradiction.py`'s cross-contradiction and reversal
rules are unreachable code: measured over the fixture, no real graph holds a
`CONTRADICTS` edge and every caller passed `accepted` empty, so two of that
module's three rules had never once been evaluated against anything. Doing it
across cells instead would make a cell's reading a function of which cells ran
first, so a resumed campaign would score differently at the same content
address — invariant 3. The accumulator is therefore a local inside `adjudicate`
rather than anything a caller holds.

**3. `zombie_claims` is beyond the gate line on A31's `max_defect_mass`
precedent.** Criterion 8 names *two* quantities and one pooled count leaves the
second underivable — a nonzero figure could be a reversal, which is neither.
Counting it meant exporting `contradiction.zombie(claim, graph)` rather than
recognising a zombie by its `Finding.message`, which `verify/verdict.py` says is
for a human and that nothing branches on. Both this and the accumulation choice
were put to the user as two-option questions and taken as recommended.

**An approach tried and abandoned, found by `/test-review` before any
implementation existed.** The accumulation test first hand-authored two
`supports` claims citing the run's whole evidence and asserted one contradiction
finding. Measured: **both are refused by the `statistical` check** — 7 of the 8
bearing experiments refuted the subject, and an experiment that refuted a
hypothesis is not evidence supporting it — so they never reach the contradiction
rules, `accepted` stays empty, and the correct answer is **zero**. The only
implementation that could satisfy that assertion was one carrying *refused*
claims forward as admitted, which would let the record contradict itself with
claims the verifier threw out. **The test was pointing implementation at a bug.**
Replaced by drawing the pair from the run's own accepted claims — `null` and
`poisson_mixture` on S9/V1, both `correlational`/`suggests`.

The reusable shape is worth more than the fix: a fixture built from
plausible-*looking* objects is not built from *admissible* ones, and a test whose
only satisfying implementation is wrong still fails closed — it does not pass
silently, it misdirects. The gate that catches it is running the checks over the
fixture before asserting on them, which is now `_accepted_supporting`.

**Measured, and expensive to reproduce.** Adjudication costs **0.009–0.019s per
run over 80 claims**, against a 0.05–0.17s run — roughly 20s across 1,120 cells,
which is why there is no opt-in switch. `REFER` occurs **zero times** over this
fixture and **zero times over `tests/baseline_runs.py`'s 2,288 claims**, so every
recordable row's true adjudication rate is 1.0 and `1.0 if claims else nan`
reproduces the entire payload. Criterion 10 would have been *measured but not
falsifiable* — the same vacuity the entry complains about for criterion 8, one
criterion over. `test_a30_the_rate_falls_when_a_claim_cannot_be_decided` closes
it by injecting a component-subject `exclusive` claim, which `logical` and
`statistical` both refer and nothing refuses. That test is not in the entry's
gate line and was not in the approved plan; it exists because the review
measured the gap.

**A blind spot in the literal-pin convention.** Three tests pin
`DIMENSION_VERSION`'s literal so a bump has to be noticed. `mypy` flagged two of
them, because they compare against the constant and narrow to a `Literal`; it
could not flag `tests/test_matrix.py`'s, which is a **dict value** in an expected
config and has no such narrowing. Only the suite caught it. A convention that
exists to make a bump loud is a third silent to the type checker.

**Closes off.** Criterion 10 is answerable from a recorded row for the first
time, and criterion 8's two halves are separately answerable. Neither is *met*
by this: both are bars a campaign may fail, and nothing here asserts an outcome.

**Left undone on purpose.** The recorded campaign is **not** re-derived — A35
blocks the replay outright and A40 is rank 12. This lands the machinery so the
re-derivation has it; it does not spend it. And `contrast()` still conditions on
the whole-record check rather than the probe: A29 left that to A31, A31
deliberately did not take it, and it is not in this entry's **Touches** either.
It is now owed by whichever gate does move §9's preregistration.

## 2026-08-23 — gate A30, after review: three findings fixed, three recorded and left standing

**Decision.** `/preflight` ran `/code-review` plus five lenses against the A30
diff before it was committed. Four lenses returned clean. Three findings were
fixed; three more are real, are **not** fixed, and are recorded here because
each is a change to something wider than this gate.

**Fixed — the report pooled the wrong statistic.** `adjudication_rate` was
summarised as an unweighted mean of per-replicate rates, and SPEC §12 criterion
10 (*"at least 90% of **claims** adjudicated"*) is a share of claims. Two
replicates at 80/100 and 4/4 average to 0.9000 and clear the bar; the 84/104
they actually represent is 0.8077 and does not. `claims` and `adjudicated`
reached the ledger but nothing summarised them, so the criterion's own statistic
was unrecoverable from the report — the identical gap `max_defect_mass` exists
to close for criterion 9. `CellSummary.adjudicated_share` is now that statistic
and both counts are printed beside it.

**Measured, because the finding's stated premise turned out not to hold on the
arms that exist.** The review argued claim counts "genuinely vary within a cell".
Over six replicates each of B1 and V1 on S9 and S11 they do not: every replicate
affords exactly 16 and 80 claims respectively, so the weights are uniform and the
two statistics agree to the last digit. The hazard is **latent on conventional
arms and live on the LLM arms** — V7, V3 and V4 hold a proposal layer, so how
many structures carry mass varies by replicate, and those are precisely the arms
A40's re-derivation exists to compare. Fixed on that basis rather than on the
premise as given.

**Fixed — two places where a docstring was doing an assertion's work**, which is
CLAUDE.md's second invariant verbatim. `Adjudication` was four bare `int`s whose
stated relations (`zombies ⊆ contradictions`, `adjudicated ≤ claims`) nothing
enforced, while `Verdict.__post_init__` does exactly this one level down in the
same call path; it now refuses an inconsistent tally. And `adjudicate` took the
`claims` sequence verbatim — `_about_this_run` now refuses a claim whose subject
the run never entertained or whose citation it never registered.

**Recorded and not fixed: criterion 10 rewards supplying more claims, by three
distinct routes.** A refusal *is* adjudicated — deliberately, since a claim
decided on a mechanical ground has been decided — and everything follows from
that.

1. **Volume.** A population padded with junk drives the rate toward 1.0 with no
   science done. And `adjudicate` does not deduplicate: `claims` is
   `len(population)`, so the padding need not even be distinct — *the same claim
   the run itself affords, repeated N times, passes every check N times.*
2. **A fabricated number, which is the one that is not merely "volume".**
   `Claim.effect` is an `EffectEstimate`, and `statistical.check` branches on it:
   `_from_effect` when a claim carries one, `_from_predictions` otherwise — and
   only the second returns `REFER`. So attaching a *fabricated* effect to an
   otherwise-referrable claim converts it to a refusal and **raises** the rate.
   `numerical.check` does refuse the fabrication, which is gate A19 working
   exactly as designed; the refusal is still adjudicated. This is an
   agent-authored **number** selecting which check grades the claim, and an
   earlier draft of this entry did not have it — `adjudicate`'s docstring said
   *"claims are structure"*, which is false and is now corrected in place.
3. **Predictions**, below.

None of the three is closable at this boundary. Refusing fabricated effects here
would take A19's work away from `verify/numerical.py`, whose purpose is to catch
them — the verifier is *supposed* to receive fabricated effects and refuse them.
`_about_this_run` bounds well-formedness and cannot bound merit; no assertion
can. It is a question about what criterion 10 measures, which is SPEC §12 and
frozen, so it belongs in `docs/BACKLOG.md` rather than in a gate.

**Recorded and not fixed: the `claims` seam sits outside `cell_key`'s content
address.** Two `reading_of` calls at one address can produce different payloads.
Unreachable today — `MatrixRunner.execute` never passes `claims` — and closing it
properly means an address *term*, which `cell_key` cannot carry: it computes the
address **before** the cell runs, so it cannot see a population that does not
exist yet. That is structurally the `battery` problem, whose remedy was a change
to the content-addressing contract every recorded row depends on. Not something
to slip into the gate that first noticed it, which is how A31's `proposed_at`
finding was handled a day earlier and for the same reason.

**Recorded and not fixed: a pre-existing agent-writable surface became
load-bearing.** `Investigation.propose(..., predictions=...)` lets a system
supply its own predictions, and `validate_prediction` does **not** constrain
`prediction.under` to a template the scenario offers. Before this gate that
reached no reported number, because `verify()` had no caller in `src`.
`statistical.prediction_evidence` now reads those predictions, and `evaluated ==
0` yields REFER — the one outcome that is not adjudicated. So a system supplying
a valid, falsifiable prediction under a template it never runs moves
`adjudication_rate`.

**Downward only is an observation about today's arms, not a property of the
code**, and an earlier draft of this entry stated it as the latter. The branch is
symmetric: a prediction aimed at a template the system *does* run makes
`evaluated > 0`, and `_from_predictions` then returns `()` or a REJECT, both
adjudicated. Only downward movement is *observable* right now because the
measured rate is already pinned at its 1.0 ceiling. On any future arm whose
default predictions produce referrals, better-aimed supplied predictions raise
its own figure. No shipped system uses the field (`predictions=` appears twice in
`src/sciagent/systems/`, both the framework's own pass-through), so this stays a
threat-model note — exactly the shape of A31's `proposed_at` entry: a field that
was audit-exempt because nothing scored it is now scored.
`systems/base.py`'s own docstring claimed *"introducing a hypothesis cannot move
a number in the proposer's favour"*; that is now qualified to the posterior,
which is the part still true.

**Closes off.** Every `verify()`-reachable input a system can author is now
either audited or written down here, and the list took three passes to close
rather than one: `claim.effect` was missed by the first draft of this entry and
found by re-auditing the fix. Anything that later widens `_reconcile` should
treat `prediction.under` as the next item, after `proposed_at`.

**One decision is owed before item 12 wires the seam, and is cheap now and
expensive later.** `_about_this_run` *raises*, and `run_matrix` has no `except` —
so once an agent supplies claims, one malformed claim aborts the whole campaign
pass. A system that would score badly and instead aborts is a system moving its
own outcome, which is a different attack from authoring a number and is not
closed by anything here. Whether item 12 refers a malformed claim rather than
raising is a choice about what a campaign does with a bad agent, and it should be
taken deliberately rather than inherited from this gate's default.

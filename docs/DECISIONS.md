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

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

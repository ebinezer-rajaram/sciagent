# v2 decision log

One short dated paragraph per real design decision: what was decided, and why.
Routine choices don't go here. v1's full record is `docs/v1/DECISIONS.md`.

## 2026-10-01 — v2 replaces v1's question with an answerable one

Re-reading v1's ledger showed that the LLM system (V7) equals the no-LLM system
(V1) on D1, D2, D3, D5 and D6 in all twelve scenarios. The agent's grammar had
five structural cells, and the one out-of-library scenario was unwinnable through
its menu. v2 (`docs/SPEC.md`) moves to an open feature space for point-process
GLMs, a generated population of out-of-library truths, agents with tools in two
tiers (constrained and open), and positive controls before any contrast. The v1
code and campaign are frozen at the tag `v1.0`.

## 2026-10-01 — Mathematics only where it removes a confound

The pieces kept are:
- convex GLM fitting with duality-gap certificates, which removes optimiser
  failure as an explanation for a loss;
- Wiener–Hopf nonparametric kernels, a model-free baseline and the reference
  for falsification-seeking design;
- the evidence-wall proposition, which generalises v1's 0/112.

Fisher-information identifiability and HMM likelihoods were considered and left
out, because neither was needed for a first result. A hand-written solver was
rejected in favour of an off-the-shelf conic solver: the certificate is the
point, not the solver.

## 2026-10-01 — The LLM arms are agents, in two tiers

v1's LLM had no tools (`tools=[]`) and made at most two menu proposals per
investigation, so it was not an agent. v2 runs Claude through the Agent SDK with
MCP tools.
- The **constrained** tier gets an equal fit budget against search (Q1).
- The **open** tier adds a Python sandbox and a notebook. It is compared at an
  equal experiment budget, because it can fit privately.

Scientific behaviour is scored mechanically from committed predictions, never by
an LLM judge.

## 2026-10-01 — Phase 0: delete freely, behind a tag

The import graph from what v2 keeps showed that about 39 v1 modules (~16.9k of
29.2k source lines) and ~28.8k test lines were unreachable or v1-specific. They
were deleted, and the workflow was rebuilt minimal:
- CLAUDE.md went from 341 lines to about 70;
- one `/ship` remains, replacing ten skills and four agents;
- one PostToolUse hook (ruff, plus the AST determinism check) replaces eight.

The surviving suite runs in ~24 s with `-n 4` (511 tests), against 21–30 minutes
in v1's CI.

Surviving v1 code keeps its `SPEC §` references, which now mean
`docs/v1/SPEC.md`.

## 2026-10-01 — Surviving v1 substrate kept until P2

The intervention compiler (`pointproc/operations.py`) sits on v1's experiment
DSL, executor, outcome binning and `hypothesis.graph`. They stay until P2's
intervention language replaces them. Deleting them now would have meant
rewriting interventions before the v2 design for them exists.

## 2026-10-01 — P1 grammar contract: ψ everywhere, fixed mark standardisation

The grammar (`sciagent/glm/grammar.py`) carries no numbers, so every
continuous quantity is a ψ slot profiled on a fixed grid (`glm/grids.py`). That
includes the mark-function parameters (`Pow` exponent, `ExpOf` coefficient,
`Above` threshold) and the `PhaseWindow` period and phase. Marks are
standardised by an environment-fixed `ChannelSpec` (location, scale), not by
data statistics, so a fitted threshold means the same thing on observational,
interventional and held-out data. Kernels are normalised densities, so under
the identity link a coefficient is a branching ratio. The link is part of the
submitted `Structure`, because exp-vs-identity is a structural choice that the
agent makes. Grids are in mean-rate-1 time units, the truth sampler's operating
point.

## 2026-10-01 — Compensator by quadrature for non-identity links; ψ profiling cap

Spec gaps found while planning P1, defaults approved by the user and amended
into SPEC §2.2. (1) Only the identity link has a compensator linear in
per-feature integrals. Exp and softplus need quadrature of `g(Xθ)`, so those
fits are certified up to a recorded quadrature-error estimate. (2) The joint ψ
grid grows as the product of per-slot grids. It is searched exhaustively up to
512 points; above that, ψ is profiled coordinate-wise and the fit is flagged
"certified in θ, coordinate-optimal in ψ". The flag keeps the weaker guarantee
visible rather than silently claiming a global optimum.

## 2026-10-01 — Canonical form is depth-minimal; ψ-bearing duplicates are kept

Products commute and associate, and gates distribute over products
(`Product(Gate(a,c), b) ≡ Gate(Product(a,b), c)`). So a feature reduces to a
multiset of atoms plus a multiset of gate conditions. The normal form deals
the sorted gates round-robin onto the sorted atoms, then merges products
shallowest-first. Hoisting every gate to the top would be simpler, but it can
push a valid depth-3 feature to depth 4, out of the grammar.

De-duplication drops only repeats that have no ψ slot (`Trend`). Two
`Excite(ExpK, …)` features are not redundant, because each profiles its own
timescale. That clarifies SPEC §2.2's "de-duplication".

## 2026-10-01 — Structural distance: Steinhaus-normalised TED inside OSPA

The SPEC asked for a "normalised" tree edit distance tested for the metric
axioms. Both obvious normalisations fail the triangle inequality. The
counterexamples, found by exhaustive search over trees of up to four nodes, are
pinned as tests. The feature cost is instead `2T/(|a|+|b|+T)` (Li & Liu 2007),
which is a metric and lies in [0, 1].

Feature sets are compared by OSPA with p = 1 and c = 1 (Schuhmacher et al.
2008). That is a metric because the pair cost never exceeds the unmatched
cost. The link enters as a 0.2-weighted discrete metric.

Reference points: Hawkes↔S11 = 0.18, Hawkes↔Periodic = 0.71. The near, mid
and far strata thresholds are set on this [0, 1] scale by the truth sampler (P2).

## 2026-10-01 — Evidence wall proved; FSD fixed as squared Hellinger against the best fit

`docs/v2/evidence-wall.md` proves that EIG ≤ ε·H(w) ≤ ε·log K, where ε is the
largest pairwise TV among the entertained predictives. The bound is attained by
an erasure channel. EIG depends on the truth only through the weights, so
lookahead BOED hits the same wall.

For the v1 statistic, `size_gap_correlation` has permutation mean 0 and
variance exactly 1/(N−1) under any entertained hypothesis with exchangeable
marks. The resulting EIG ≈ 0.0035 nats, against 0.38 nats had S11 been
entertained.

FSD's discrepancy is squared Hellinger: a bounded metric whose triangle
inequality gives the "not walled" and "own wall" propositions. FSD is taken
against the MAP (best-fitted) parametric model, as SPEC §2.4 already says.
Before Q6 is reported, FSD needs a null calibration: a parametric bootstrap of
FSD when the fitted model is the truth. Without it, Wiener–Hopf estimation
noise reads as falsification.

## 2026-10-01 — Wiener–Hopf: exact Galerkin system, log bins to 10 mean gaps, reported negatives

Arrivals and mark-weighted arrivals share event times, so the normal equations
carry a lag-0 singular term for every driver pair, not only the diagonal. The
system is assembled by Galerkin projection on indicator bins, with exact
pair sums. That makes it symmetric, valid for any bin layout, and checkable by
brute force to 1e-11.

The support is 10 mean inter-event times. A null kernel's norm has sd that
grows with the support; 10 still covers 99% of an exponential kernel's mass at
rate 0.5. The ridge is fixed and tiny (1e-3 of the diagonal) and only guards
against collinear drivers, because shrinkage would bias the norms that B-np
reads. Negative kernel values are reported, not clipped. B-np's intensity is
floored at 1e-6 × the mean rate for scoring.

Calibrated null cross-kernel norm: sd ≈ 0.067 at ~3k events.

## 2026-10-01 — Simulator: interval-arithmetic thinning bound, independent of the likelihood

The Ogata thinning bound comes from interval arithmetic over each column on
the look-ahead window. It combines per-event kernel ranges, exact sin/cos
ranges, gates that include 0, interval products, the signs of θ and the
monotone link. So it stays valid for signed marks and negative coefficients.
Exceeding it raises an error, as does negative identity-link intensity.

The simulator deliberately shares no code with `features.py` or
`likelihood.py`. Time-rescaling of its output under their compensator is then a
genuine cross-check for SPEC §6.3 test 1.

`gamma_shape` must be at least 1, because the density is unbounded at lag 0
below that. Speed is 0.4 s for a 5k-event ExpK log and 4–7 s for power or
gamma kernels, which the exact `fsum` folds dominate.

## 2026-10-01 — Harness: hash-chained session records; replay re-executes tools

An investigation is one `ClaudeSDKClient` session. It has only in-process MCP
tools (`tools=[]`, an `allowed_tools` allowlist, `dontAsk`, no settings, skills
or plugins). This was verified live: the manifest held exactly the lab tools,
and a request for a shell was declined.

Every assistant message and tool call goes into a sha256 hash chain, whose head
is the run's content address. Timing and cost are kept out of the hash. Replay
needs no SDK. It re-issues the recorded tool calls against a fresh tool layer
and compares digests, budgets and the submission, so a perturbed tool result or
a changed tool implementation is caught at its step.

A metered call is charged only when the handler succeeds, so a malformed
request costs the agent nothing. A tool use outside the layer voids the run
(SPEC §9) rather than aborting it. The served-model check accepts a
`canonicalModel` that is the pinned id with its date suffix stripped, as
measured live for Haiku.

## 2026-10-01 — Sandbox: one Docker container per call, opaque run directories

Each `python` call is one fresh container. It runs with `--network none`, a
read-only root, a read-only `data/` and a writable `work/`, non-root, with no
capabilities, plus pids, memory, file-size and wall-time limits. There is no
persistent kernel, so replay is a deterministic re-execution, and state
persists only through files in `work/`; the system prompt must say so. The
image pins numpy and scipy to the framework's versions.

Measured overhead is 0.5 s per call, plus 0.5–2.4 s of imports.

Known limits:
- `/proc/self/mountinfo` reveals the host path of the run directory, so run
  directories must be named opaquely (no truth id, seed, arm or condition),
  or anonymisation leaks.
- Isolation is at the container level on the Docker Desktop VM, not gVisor.
- The total disk use in `work/` is unbounded.

## 2026-10-01 — Likelihood: a ψ-independent quadrature rule, cacheable feature blocks

The quadrature rule depends on the structure, the data and the ψ grid, never
on the ψ values. It is composite Gauss–Legendre with 8 nodes per panel. Panels
break at events, at grid PhaseWindow switches, and on geometric panels after
each event, sized by the sharpest kernel on the grid. So every ψ point of a
profile shares one rule, and the fitter caches a column block per (feature, ψₖ).
A profile then costs Σ|gridₖ| block computations, not Π|gridₖ|.

Columns under gates have closed-form integrals. Product columns are integrated
by quadrature and flagged. The error estimate is 2·|Q_q − Q_2q|, which tracked
the true error within a factor of about 2 in every tested case.

Integer-shape gamma kernels use an exact O(n) moment recursion. PowerK remains
O(n·m) direct sums: about 10 s at 5,000 events, which is a throughput risk for
fitting.

Forced events count as history, including for `LastMarkAbove`. Window
boundaries are inside the excluded window.

## 2026-10-01 — Null is the empty feature set; Mark(sign) on a signed source is forbidden

`Structure((), link)` is the intercept-only null. It is the library's null and
the abstention answer, so `validate` accepts 0..4 features, and the DSL spells
it `null`.

`Excite(K, Mark(s), sign=±)` is rejected. On a source filtered by `s`, the mark
is the constant ±1, which makes it a duplicate of `Excite(K, One, sign=±)`.
The space counter found this redundancy.

Computed space at depth 3 with K ≤ 4:
- pointproc: 367,075 canonical features and 2.3e21 structures;
- QTM: 4,774 features and 6.5e13 structures.

That is at least 1e10 times the 10×F budget, so SPEC §2.1's size requirement
holds. The B-sparse depth-2 dictionary is about 1.03M columns for pointproc
and 74k for QTM, and it expresses only about 1e-21 of the parameterised space.

## 2026-10-01 — Diagnostic catalogue (28) and pointproc v2; power-of-two time factor

The catalogue (`sciagent/diagnostics`) ports v1's 14 metrics, generalised to any
channel, and adds 14 more: cross-mark, clustering, spectral-at-period,
burstiness and others. SPEC §4.0's "20" was v1 folklore; v1 had 14.

Time arguments are absolute times, with defaults and bounds tied to the mean
gap. Each diagnostic declares how it transforms under t → c·t. Predictive
checks freeze the resolved arguments on the observed log, so every replicate
computes the same statistic.

The anonymisation time factor is c = 8. A power of two makes rescaling exact,
so binned statistics commute with it exactly, and 8 is not a recognisable
constant.

pointproc v2:
- `size` is standardised with the mean and sd of Exp(1).
- Seasonality uses the exp link, as in v1.
- Truths keep v1's shapes, with baselines solved for a mean rate of exactly 1.
- Regime switching and the Poisson mixture remain to be implemented as
  out-of-grammar library members.

Note for the sampler: `ExpOf(size)` with a ≥ 1 has an infinite mean under Exp(1)
sizes, so stationarity must be checked, not assumed.

## 2026-10-02 — Independent review of the P1 proofs; Wiener–Hopf first bin 0.05 → 0.01

An independent Opus reviewer checked the evidence-wall note, the distance metric
proof and the Wiener–Hopf derivation.
- **Distance:** sound. Exhaustive triangle probes showed no excess.
- **Wiener–Hopf:** sound with gaps. The estimand is now stated as the
  grid-projected best linear predictor, with its consistency assumptions.
- **Evidence-wall note:** one real error. Corollary 3 used weak convergence
  where TV convergence is needed; the corrected route bins the statistic and
  uses Portmanteau. Ten smaller gaps were also fixed.

The Wiener–Hopf grid check found that the old first bin under-resolved the
PowerK(c=0.05) norms by about 0.006. At 0.01, finer grids change the norms by
less than 3e-4, and null-norm noise is unchanged. Heavy-tailed kernels remain
limited by the support (max lag of 10 mean gaps), which is a stated limit.

## 2026-10-02 — Intervention language: forced events excite but are exogenous

The primitives are `force_events`, `inject_marks`, `censor` and `clamp_rate`,
plus `compose`. Every experiment is a fresh run from empty history.
- **Forced events** enter history but are not endogenous. They are never
  censored, because the experimenter placed them.
- **Mark injection** overrides only generated events.
- **Clamping** replaces λ, so the model's λ is not evaluated inside a clamp.
  Clamped and censored windows go to `Dataset.excluded`.
- **The mark sampler** is called for every event, and overrides are applied
  afterwards. The RNG stream therefore never shifts, which makes
  inject-on-Hawkes byte-identical to its control: the S11 discriminator has an
  exact null.
- **Caps** are validated in the agent's time units.

Sampler requirement found here: an exp-link truth with positive
self-excitation can explode slowly. The truth sampler must reject by a bounded
pilot simulation (a cap on events and time), not by waiting for `max_events`.

## 2026-10-02 — Certified fitter: Newton plus an explicit dual, not Clarabel by default

Clarabel took about 3.2 s per exp-link solve at about 89k quadrature nodes,
against 60+ ψ points per fit. The default inner solver is now damped Newton.
The certificate is the gap to an explicitly built dual point (Fenchel equality
on event rows, a Newton-metric correction for dual feasibility), summed exactly.
"The certificate is the point, not the solver."

For the identity link, positivity is enforced at the nodes by cutting planes
through CVXPY and Clarabel, only when it is violated.

Certification requires a relative gap ≤ 1e-8 and a dual residual ≤ 1e-9; fits
reach about 1e-16. A fit is certified only if every ψ point it evaluated is.
BIC counts len(θ) plus the ψ slots.

Timings, serial:
- Hawkes on 2k events: 0.09 s.
- PowerK + Periodic, exp link, on 2k events: 24 s.
- The same on 9 datasets (17.7k events): 226 s, or 86 s on 12 workers.

PowerK feature blocks dominate. For exp and softplus links, "branching ratio
< 1" has no meaning, so the truth sampler needs a stationarity rule there
(bounded pilot simulation).

## 2026-10-02 — Out-of-grammar library and B-lib/B-np/ORACLE

Regime switching is an MMPP-2 with an exact forward algorithm, in which forced
events are ignored and the chain runs hidden through excluded windows. The
Poisson mixture is a renewal process with two-component exponential gaps. Its
likelihood is conditional across excluded windows, so per-event comparisons on
windowed data are slightly apples-to-oranges, but the observational held-out
data is exact.

B-lib fits all five members and submits the best by BIC. If an out-of-grammar
member wins, the submitted structure is `None`, which scores as maximal
distance. B-np resamples training marks for simulation.

Positive control (SPEC §6.4) seen early on the S11 truth: ORACLE beats B-lib
by 0.040–0.046 nats/event on four seeds, with B-lib always picking Hawkes.

## 2026-10-02 — Tool layer and investigation runner; served-model check accepts family ids

- **Charging.** Fits are charged for distinct canonical fits only, the same
  rule as the search baselines, so C1 compares like with like.
- **The fit configuration is pinned framework-side** (`FIT_CONFIG`) and its
  key is recorded, so an agent cannot loosen certification (audit N4).
- **Anonymised numbers.**
  - Diagnostics and kernels run on the agent-view (rescaled) data; they are
    covariant.
  - Fits run natively, and θ, ψ, log L and BIC are converted by exact time
    powers. Softplus θ is not covariant, so it is shown unconverted, which is
    a known limit.
  - "Named" differs from "anonymised" only in names and time units. Neither
    condition tells a domain story.
- **Failures.** A stopped simulation still consumes the experiment. A sandbox
  infrastructure fault, or python code probing out of bounds, voids the run.

First live AG-o run (Sonnet, S11 truth, named, E=3, F=8): it submitted the true
structure `Excite(ExpK, Mark(size), all)` in 16 turns and 57 s. It used no
experiments or python. It aborted on the served-model check, because Sonnet
5.5 reports `canonicalModel` `claude-sonnet-5` and Claude Code adds an
auxiliary Haiku call. The check now accepts a served canonical id that names
the pinned model's family; every assistant turn must still carry the exact
pinned id. Replay of that run diverged at the first fit, because the fitter
changed afterwards without a `FIT_VERSION` bump, which replay correctly caught.

## 2026-10-02 — Scorer: gap sign, recovery threshold, battery, go/no-go precedence

- **Gap.** `gap = LL_sub − LL_oracle` per event, so higher is better and the
  ORACLE scores 0. Held-out data uses a seed stream disjoint by construction
  from the investigation's streams (spawn-key length), and this is tested.
- **"Recovers"** (SPEC §7.1) means exact canonical recovery, or a gap ≥ −0.005
  nats/event. The d/(2n) cost of a correct model is ≤ 0.0025 at n = 2,000, and
  the S11 library gap is about 0.04.
- **"Closes the gap"** means gap-closed ≥ 0.9.
- **The B-sym@10F control** passes when its median gap is ≥ −0.01.
- **The ORACLE-vs-B-lib control** is applied per truth (mean paired difference
  > SE, at least 2 seeds); failing truths are dropped.
- **Verdict precedence:** controls fail, then too easy, then too hard, then go.
- **Interventional similarity** is 1 − mean squared Hellinger over an
  8-experiment battery on a horizon of 120 (burst, mark injection high and low,
  clamp-release, silence-release, censor plus burst), with three readouts and
  paired truth draws.

Measured: S11 against itself 0.974, S11 against Hawkes 0.852, driven by
size-injection at 0.79. The separation is modest: Hawkes count noise dominates
short windows. Revisit the battery horizon in P3 if similarity doesn't
discriminate on dev truths.

## 2026-10-02 — Audit fixes to the fitter; PowerK by sum of exponentials (FIT_VERSION 3)

Audit fixes:
- **Identity-link positivity** is enforced at quadrature nodes and at every
  event's right limit `tᵢ⁺`, the binding point for inhibition (audit B1).
- **`simulable` is a recorded flag, not a constraint.** It is a lower bound on
  λ over every history, computed by interval arithmetic. Inhibition can be the
  maximum-likelihood structure, so forbidding it would bias structure
  comparisons. Truths must be simulable; fits need not be.
- **The reported gap** is `F − D + |r|ᵀ|θ|`, a valid suboptimality bound, with
  a −1e-12 floor (S2).
- **Clarabel cutting planes** use unit-row scaling and at most 64 cuts per
  round (S3).

Speed-up:
- **PowerK histories** use the exact near field for lags below c and a
  verified sum of exponentials beyond it, at ≤ 1e-11 relative (measured worst
  5e-12). The exact path is kept as `exact=True`.
- **Leaf-level caching** means a profile costs Σ over leaves, not Π.

Fit (c), 9 datasets with 17.7k events: 256 s → 76 s. The solver's `fsum`
folds now dominate.

## 2026-10-02 — Search baselines; "recovers" means exact canonical recovery

B-sym is a (μ+λ) tree genetic program. It uses tournament selection, BIC
fitness, AST mutations and feature-set crossover, and is seeded with the
library. Its hyperparameters are untuned and must be tuned on dev and frozen
before test (SPEC §9).

Budget accounting is the same as the agent's: distinct canonical structures,
and a failed fit still costs one. On the S11 truth, B-sym recovered the exact
structure in 6–14 fits; B-lib picked Hawkes.

Measured: B-rand reaches a held-out gap under 0.005 nats/event after 2–5 fits
of wrong structures, because that is within noise at 2,000 held-out events. So
SPEC §7.1 "recovers" is exact canonical recovery only, and the reach criterion
for the planted-hint control is exact recovery too. The gap is reported
separately.

The planted hint keeps each truth feature's root production with defaulted
children. On a plain-Excite truth that hint is Hawkes and gives no
separation; this is a known weakness of the §6.4 wording.

## 2026-10-02 — Inner solve on a fixed blocked pairwise tree; ExactSum for reported numbers (FIT_VERSION 4)

Newton's gradient and Hessian folds use one fixed pairwise tree built from
elementwise numpy adds (`reductions.pairwise_rows`). That makes them
deterministic without BLAS.

F, D and the gap use `ExactSum`, which is bit-identical to `math.fsum` and
three times faster. The certificate's residual bound was corrected to include
product and cross-part rounding.

Timings: fit (c) 76 s → 26 s; (b) 2.95 s; (a) 0.08 s. The remaining cost is
per-row `fsum` in the likelihood's `row_totals` and the Lomax blocks.

## 2026-10-02 — Truth sampler (preregistration material)

The prior:
- Links: identity 0.5, exp 0.25, softplus 0.25.
- K = 1/2/3/4 with probability 0.25/0.4/0.25/0.1.
- Kernels: ExpK 0.5, GammaK 0.25, PowerK 0.25.
- No Trend, because it is non-stationary.

The out-of-dictionary share is exactly 0.5 by stratification; an
out-of-dictionary truth has one depth-3 feature. ψ is on-grid, so ORACLE
measures identifiability rather than grid resolution. `exp_coef` is limited to
0.5 for truths, because larger values give infinite-mean marks.

θ is drawn on each column's operating scale, then calibrated by a one-scalar
secant search under common random numbers: mean rate 1 ± 3% at 5,000 time
units. Truths must also pass:
- an identity branching bound < 0.9, capped pilots and a rate drift within
  1.5×;
- simulability by construction;
- Fano(2) in [2, 5] (the library truths measure 3.1–3.6);
- identifiability, δ = 0.01 nats/event against all five library members,
  certified fits only;
- non-membership, ε = 0.1.

Strata: near ≤ 0.6 < mid ≤ 0.72 < far. Wall time never rejects a candidate,
so the population cannot depend on the machine. Test-split records live
outside the repo, and only their sha256 is committed.

The first dev split (seed 2026, n = 20) took 49 minutes on 4 workers, with
about 6% acceptance; dispersion and simulability are the main rejections. Of
the 20 truths, 11 are softplus: acceptance skews the realised link mix away
from the prior. It was regenerated after the glm freeze (FIT_VERSION 4)
because records are not byte-stable across numeric changes.

## 2026-10-02 — B-sparse; a scoped exemption from the fixed-order-fold guard (REVIEW)

B-sparse runs a softplus group lasso over the full depth-≤2 dictionary
(995,841 groups for pointproc). All group gradients come from one cross
product, so the KKT check over the whole dictionary is exact. The solver is
proximal Newton with an active-set Newton inner loop. The path is selected by
scoring the relaxed, certified refit of each path point on a 75/25 time split,
and the submitted link is the best of three by validation. The result is
capped to 4 features by group norm, and a flag records when the cap applied.

Measured: 155 s per run and 1.7 GB peak. It recovers the S11 truth, a depth-2
gate truth (including ψ), and the null on Poisson data. It does not recover
`Product(Excite, Periodic)`: it selects the one-column alias
`Gate(Excite, PhaseWindow)`, which pays less group penalty and which
validation cannot distinguish. So "B-sparse succeeds wherever the truth is in
the dictionary" (SPEC §4.1) holds predictively but not structurally for
product truths with periodic factors. Read §7.1's B-sparse criterion with
that in mind.

**The decision for review.** The AST guard
`test_metric_values_use_deterministic_reductions` gets a three-module,
reasoned exemption (`REDUCTION_EXEMPT` in tests/test_invariants.py). A
fixed-order fold measured about 8 h per path, against about 1 min with BLAS.

Why this is acceptable:
- The BLAS path only steers a search.
- Every reported number comes from the certified fixed-order `glm.fit`.
- Run-to-run byte identity on the reference platform, with BLAS pinned to one
  thread, is tested.

The residual risk is cross-CPU: the selected structure could flip at a
near-tie. If you'd rather keep the guard absolute, the alternatives are a
fixed-order cross product that makes B-sparse a ~30 min/run baseline, or
coarsening the dictionary's ψ grid.

## 2026-10-02 — Dev split regenerated under FIT_VERSION 4

`data/v2/truths/dev/` (git-ignored): seed 2026, n = 20, records sha256
`1f0fac01e8b572efb4171f26fb8fa80c2ae3f0e8dbf6c9a9c6d248b281b4969d`. It replaces
the FIT_VERSION 1–3 split (sha `324890d8…`, kept under `dev-stale-fit3/` for
comparison). The pilot reads this one.

## 2026-10-02 — Pilot driver design; pilot paused at about 30% LLM coverage

- **LLM units are two cells.** The session cell has no metric version, so a
  scorer change never reruns a model; the score is a separate cell.
- **Same data for everyone.** Non-LLM systems see the observational log only.
  LLM submissions and every charged fit are refitted on that same log, so all
  systems are compared on identical data. The LLM efficiency curve is best by
  BIC so far.
- **The B-sym@10F control** runs on seed 1 of dev-000..009 only, which is
  declared in PILOT.md.
- **Failures.** A failed unit neither recovers nor closes. `-inf` scores are
  stored but excluded from means.
- **Isolation.** Live sessions run in separate processes, killed at wall time
  + 600 s (`hard_timeout`, void), because a tool thread running a long
  simulation can't be cancelled.
- **The pool reuses workers.** `max_tasks_per_child=1` hangs on Python 3.12
  (gh-115634).

Paused at the user's request on 2026-10-02:
- Non-LLM units: 7/60 per system, seed 1 only.
- B-sym@10F: 5/10.
- LLM sessions: named 26 + 25, anon 11 + 12.

No failures, no void runs, no rate limits. No §7.1 verdict yet. Interim
signals: about half the sessions run no experiment, almost none commit
predictions, and agents use a median of ~15 of 40 fits.

Open contract issue: `wall_time_s` does not bound a running tool call. A real
fix caps simulation cost inside `run_experiment`.

## 2026-10-02 — Experiment cost bounded by events, not wall time

PowerK and GammaK histories make simulation cost grow roughly as n². At the
old cap of 50k events, stopped pilot experiments took 250–1,700 s inside an
uncancellable tool call.

Changes:
- `MAX_EVENTS` 50k → 15k. The largest successful pilot experiment had 12,181
  events.
- `MAX_CLAMP_EVENTS` 20k → 5k, so every valid design fits under the cap at
  nominal rate.
- New `MAX_MARK_Z = 10` on injected and forced marks: injecting size 1e4 took
  390 s even under the cap.
- The stop message is unit-free and identical in both conditions.

Worst case measured over 20 dev truths × 13 adversarial designs: about 130 s.

Pilot consistency:
- All 75 recorded pilot experiment designs give identical outcomes and event
  counts under the new caps.
- Session keys are unchanged, so resume continues the same cells.
- About 10 old records containing stopped experiments no longer replay
  byte-identically, because their stop text says "50000". Their outcomes
  reproduce; this is accepted and declared.

## 2026-10-03 — Pilot complete; GO provisional on a fitter defect; decisions taken

The pilot finished (every unit scored) with a GO, but the audit
(`docs/v2/PILOT-AUDIT.md`) found that the coordinate ψ search above the
512-point cap stalls in local optima. ORACLE was therefore not an upper bound
on 22 of 60 units, so the GO is provisional until a fixed fitter (fit/5) has
been rerun. The user approved `docs/v2/P4-PROPOSAL.md` §5 decisions 1-17 as
recommended. In short:
- rescore the fit/4 sessions rather than rerun them;
- keep the dev split;
- apply §7.1 literally;
- void only on an executed foreign tool;
- use a rank-based control 2;
- enforce belief entries;
- run the prompt anchoring check;
- leave the rival tool off;
- add `rescaled` on Sonnet only;
- descope C5 to P4b;
- add controls 4-6;
- use a 40 × 2 test design;
- run the Sonnet-only budget sweep;
- add Δ12 but not Δ13.

The user delegated the six NIGHT-2026-10-01 decisions; 1-5 stand as taken and
6 (a longer battery horizon) is declined, as in P4 decision 17. Before the
rerun, the instruments must pass:
- an independent defect hunt with property checks on real data;
- a mini-pilot gate whose criteria are fixed in advance.

The fit/4 code is tagged `p3-fit4` so the pilot records stay replayable.

## 2026-10-03 — P3.1 analysis: rescore mode, void policy, control-2 rank rule

- **Rescore mode** (`pilot.py --rescore-llm`, `DriverSettings.rescore_llm`). A
  fitter change moves every session to a new address, so the normal plan would
  relaunch all 240 live sessions. Rescore enumerates stored `llm-session` cells
  (`campaign/rescore.py`) and matches each on system, truth and truth digest,
  seed, and session-config digest. Only the fit version and fit config may
  differ. Scores are keyed by the old session's digest plus the current
  versions. Rescore never launches a session: `_launch_llm` raises. The report
  reads sessions this way (`--sessions stored`) but reads scores at the current
  address only. `METRIC_VERSION` is bumped to `score/2`.
- **Void policy** (Δ8, applied at analysis from the stored record; no cell is
  edited). A session is void only if:
  - a foreign tool executed (a non-error result);
  - a foreign use has no recorded denial;
  - a server tool was used;
  - out-of-bounds code was found;
  - there was an infrastructure fault (kept as before).

  A denied unknown name counts as an `unknown_tool_attempt`. The driver no
  longer scores void sessions, and the analysis drops their rows and counts
  them. On the pilot, all 9 voids are reclassified as not void.
- **Control 2** (Δ10). Per-truth d = planted − B-rand mean gap. A −inf mean
  ranks lowest, and both at −inf is a tie. The control passes only if the
  exact one-sided Wilcoxon p < 0.05 **and** the bootstrap CI of the median of
  d has its lower end above 0. Both are required because the proposal named
  them together and did not say which one decides. The exact test cannot pass
  on fewer than 5 truths. Control 3's median also ranks −inf instead of
  dropping it.
- **T4.** Gap closed is flagged when |LL(ORACLE) − LL(B-lib)| on a seed is ≤
  that truth's paired SE over seeds (control 1's yardstick). Flagged ratios
  are not printed, are left out of medians and never pick a transcript. They
  still count in the §7.1 close shares as preregistered.
- **Control 1** also needs zero `oracle_underfit` flags. A missing flag, as on
  every fit/4 cell, counts as "cannot show zero" and fails the control.

## 2026-10-03 — fit/5: screening-start block-coordinate ψ search; ORACLE truth-ψ check

`FIT_VERSION` is `sciagent.glm.fit/5` (P4-PROPOSAL §2.1, audit §1). Grids of
at most 512 points are still searched exhaustively, bit for bit as in fit/4,
pinned by digests in `tests/glm/test_fit_search.py`.

On larger grids the search is now block-coordinate ascent from a **screening
start**. Each feature is fitted alone (intercept plus that feature), on the
full structure's quadrature rule and sharing its block cache. Its ψ is
ascended block-wise from a small start sub-grid of at most 64 points. The
joint start takes every feature's best ψ, and the joint ascent then runs from
there.

A block is one feature's slots if that feature's grid has ≤ 64 points.
Otherwise blocks are its leaves (an `Excite`'s kernel and mark slots, a
`PhaseWindow`'s period and phase), merged consecutively while their product
stays ≤ 64. So no block exceeds 64 points, however deeply nested the feature.

Defaults: `psi_screen=1`, `psi_starts=0`, `psi_line_start=False`,
`psi_loo_starts=False`, `psi_block_max=64`, `psi_max_sweeps=0` (no cap). All
of them are `FitConfig` fields and enter `key()`.

**Benchmark.** A fixed set, scored against the θ-refit at the truth's ψ:

- b1: the 20 dev truths × seeds 1–2.
- b2: 100 seeded prior pairs (A, A+B), with A over 512 ψ points.
- b3: 100 seeded prior structures over 512 points, each against 3 random grid
  points.

Wall ratios against the `p3-fit4` code were measured under equal parallel
load:

| design | b1 misses > 0.5 | b2 violations | b3 random beats | 2-window family misses | ratio median / worst | fits > 3× |
|---|---|---|---|---|---|---|
| fit/4 | 19/40 (max 256) | 5/100 (max 69) | 1/100 (13) | 9/10 | 1 / 1 | — |
| fit/4 + 1× block refine (first fit/5) | 3/20 s1 (max 22) | dev-013 pair fails | — | — | ≤ 2× points | — |
| **screen r=1, block 64 (default)** | **0/40** | **0/100** | **0/100** | 5/10 (max 35) | 1.4–1.8 / 22 | 49/240 |
| + best sub-grid start | 0/40 | 0/100 | 0/100 | 0/10 | 1.5–2.5 / 21 | 70/240 |
| screen r=2 | 0/40 | 0/100 | 0/100 | 3/10 | 1.2–2.1 / 27 | 60/240 |
| screen r=1, block 20 | 3/40 (max 38) | 1/76 (15) | — | 5/10 | 1.1 / 4.1 | — |

The dev-013 s2 pilot pair is now monotone: A −12930.91, A + Excite
−12930.90, A + gated product −12864.20. With a single process on the hawkes
unit, fit/4 → fit/5 timings are:

- ORACLE, B-lib, B-sym, B-sym@10F and planted-hint: unchanged (exhaustive
  path).
- B-sparse: 72.8 s → 85.5 s.
- B-rand: 77 s → 198 s (2.6×).
- dev-008 ORACLE: 17.9 s → 16.8 s.

**Known-failing:** the runtime target of at most 3× is missed on about 20% of
coordinate fits. Some are 7–22×; the worst are cheap fit/4 fits of 3–4
feature structures, and the dev-013 superset on three datasets runs ~6×. T2
(beat k random grid points) also fails, on the synthetic two-window family:
`test_fit_beats_random_grid_points` is a strict xfail citing this entry. Two
fixes were tried and dropped:

- Leave-one-out starts, recursive or one level down: 2–8× more and no gain on
  the benchmark.
- Swap moves between same-type blocks: 2× more and the window case still
  missed.

The leave-one-out start and the sweep cap remain available as options.

`fit(..., psi=…)` θ-refits at one on-grid ψ point (`psi_search = "fixed"`).
ORACLE also θ-refits at the truth's ψ point and keeps that fit if it is
strictly better on training data. It records:

- `oracle_psi_source`;
- `oracle_underfit`: training log L more than 1e-9·|log L| below the true
  parameters', defined only for an on-grid truth ψ;
- `oracle_truth_psi_on_grid`;
- the training log Ls.

The agent prompt and the fit tool's text no longer claim that coordinate fits
are optimal on the grid.

## 2026-10-06 — fit/5 defaults: screen r=2 (D3), taken under the fallback

Three designs were benchmarked against `p3-fit4`, with BLAS on one thread as
the campaign runs it:

- **D1:** screen 1, 1 sub-grid start, 4 swap rounds.
- **D2:** screen 2, 2 starts, no swap.
- **D3:** D1 with screen 2.

The sets were b1 (20 dev × seeds 1–2), b2 (100 nested pairs), b3 (100 random
structures), the two-window family, and T2 (300 derandomised examples,
including (0, exp, 0)).

**Results:**
- **D3 had 0 misses on every set:** 0/40, 0/100, 0/99, 0/10 and 0/300.
- **D2 also had 0 misses,** but cost 1.08× D3.
- **D1 lost 3/300 T2 examples,** the worst by 2.87 nats. That fails even a
  quantified rule.
- **D3 is slow.** Its wall ratio to fit/4 has a median of 3.3–3.8× and a worst
  case of about 37× (b3 #0, a 7 s fit/4 fit). It misses the ≤ 2.5× / ≤ 22×
  runtime target. The ratios carry about ±30% because load was not equal.

**Decision.** D3 was taken under the fallback the user authorised on
2026-10-05: the cheapest design with 0 misses, provided the rerun stays at
about 40 h or less.

- `FIT_VERSION` stays `fit/5`; the config key changes.
- T2 passes outright, so `test_fit_beats_random_grid_points` is strict again.
  The 24 regression cases stay.

**Unit slowdown.** Measured on dev-008, -013 and -017: s is 4.78 for B-rand
and 2.20 for B-sym. The other systems' values were assumed from these. The
projection is 178–221 worker-h, or about 30–37 h at 6 workers.

**The probe failure of `test_bsym_at_least_matches_blib_on_size_excitation`
was memory, not nondeterminism.** It failed once under `-n 6`. 11 runs, alone
and under `-n 6`, gave one digest (`4f2e94efbf94dc24`). The failure was commit
exhaustion from default-threaded OpenBLAS, about 2.2 GB per process.

**Correction to 2026-10-03:** `psi_block_max` is 128, not 64.

**Open, blocking the rerun:** fit/5's block cache grows with the number of
points evaluated. On the dev-013 superset it passed 11 GB, against 1.4 GB at
fit/4. `test_dev013_superset_never_fits_worse_than_its_subset` cannot finish on
this 16 GB machine and is unverified under D3. A bounded cache comes next,
under step 2 (the defect hunt).

## 2026-10-06 — fit/5 block cache bounded (`block_cache_bytes`, not in the key)

**What grew.** fit/5's memory growth came from the feature-block cache. It
holds one block per (dataset, feature, feature ψ), so its size is the product
of the feature's slot grids. On the dev-013 superset it reached 2304 blocks and
10.75 GiB by 1245 solves. The leaf cache stayed at 88 MB.

**The fix.**
- Blocks are now kept in a byte-bounded LRU, `FitConfig.block_cache_bytes`,
  default 1 GiB.
- Eviction order depends only on the order of get and put calls.
- Blocks are built one point at a time.
- A dropped block is rebuilt from the leaf cache, which is never bounded,
  byte for byte. So the bound changes only wall time and memory.
- The bound is not in `key()`, as with `workers`. `FIT_VERSION` stays fit/5.

**Evidence.**
- Output bytes and the evaluated ψ trajectory are identical at bounds 0,
  64 KiB and 1 MiB versus unbounded, on both search paths and all three links.
- The fit/4 digests still hold at bound 0.
- On a three-dataset case, peak traced memory falls from 140 MiB to 11 MiB at
  a 4 MiB bound.
- On dev-013, peak commit was 1.48 GB, flat for 1410 s, against more than
  11 GB before.

**Also changed.** `tests/conftest.py` now pins BLAS to one thread before numpy
loads, matching the campaign. At default threads, each process commits about
2.2 GB.

## 2026-10-06 — P3.1 fixes after defect hunt B

An independent hunt checked the campaign, budgets, replay, rescore and store on
all 240 pilot sessions. All of those properties held. It also found two analysis
defects and two latent ones, fixed test-first in 07be653:

1. **A −inf reference no longer crashes `analysis.derive`.**
   - Gap closed is None when B-lib or the submitter is not finite.
   - An ORACLE at −inf on a (truth, seed) leaves every gap there undefined.
     That truth fails control 1 (margin −inf) and is dropped, because an ORACLE
     that gives a held-out event zero intensity is not an upper bound. This is
     the preregistered drop rule applied to a case the code did not reach
     before. The fit/4 pilot has no ORACLE −inf.
   - B-lib at −inf keeps the preregistered finite-only rule.
2. **A failed ORACLE unit now counts as "unrecorded"** in control 1's underfit
   half. It was skipped, which passed a failure as zero flags.
3. **`CampaignConfig` refuses LLM arms unless `fit_config.key()` equals
   `tools.FIT_CONFIG.key()`.** Agents always fit with the latter, while the
   session key records the former.
4. **The void policy excuses a foreign result only if its text is exactly the
   CLI's "No such tool available: {name}" error.** The pilot's 9 denials match
   exactly.

No session text, digest or key changed: the rescore still finds all 240 pilot
sessions at the same addresses.

**Open for the user (F5):** some address text is hand-written rather than
derived. Examples:
- B-rand's `max_draws_factor`;
- the ORACLE text;
- the B-lib members by type name;
- `METRIC_VERSION`, which covers 3 of about 13 battery constants.

These rely on manual version bumps.

## 2026-10-06 — ORACLE keeps a truth-ψ refit only if certified

**The defect.** Defect hunt A found that ORACLE kept its truth-ψ θ-refit
whenever the refit's training log L was strictly higher than the search fit's,
without checking the certificate. An uncertified fit could therefore become
ORACLE's submission. The defect is latent: it never fired on the 20 dev truths
at seed 1.

**The fix.** ORACLE now keeps the refit only when it is certified and strictly
better. When the refit is better but uncertified:

- the search fit stays;
- `oracle_psi_source` stays "search";
- `oracle_underfit` is computed as before, so control 1 shows the shortfall
  instead of hiding it;
- `oracle_truth_psi_certified: false` is written only in that case, so every
  other cell's bytes are unchanged.

**Verification.** The keys and config text are unchanged.

## 2026-10-05 — P4 deltas: how they are built, and what is for review

The P4 deltas are built on the branch `p4-deltas` in work packages WP1-WP12.
Instruments get tests first. The entries below mark each decision:
- **Approved:** the user approved it (P4-PROPOSAL §5 decisions 1-17).
- **For review:** a SPEC-silent choice taken while building, not yet approved.
- **Open:** not decided. A setting exists where one was needed, at a default
  that keeps current behaviour.

Orchestrator decisions:
- **Δ12 is approved** (decision 17). It was folded into WP6 so that the
  session key changes once, not twice. Every tool result, refusals included,
  ends with "budget left: e of E experiments, f of F fits", appended after
  charging (`prompts.budget_line`).
- **No merge into v2 before the P3.1 rescore finishes,** because a merge
  would change the session keys the rescore matches.
- **The Δ6 Sonnet check runs** only after WP7 and WP8 pass their gates.
- **SPEC is not edited.** The Δ11 SPEC-silent choices and controls 4-6 are
  recorded here.

*Note 2026-10-29 (merge into v2):* The P4 entries below are kept as history. Where v2's LOG
has since settled one of their open or for-review points, a dated note at the end of the entry
names v2's entry. The P3.1 rescore this entry waits for was replaced by the Linux pilot rerun
(v2 LOG 2026-10-27, "The P3 pilot is rerun from scratch on Linux (owner)"), and the merge waits
for that pilot's numbers. Open points are settled under v2's process (v2 LOG 2026-10-29, "No veto
windows; one red-team round per item (owner, INBOX 09:12Z)"). The Δ6 Sonnet check was replaced by
the owner's choice (see the note under Δ6). SPEC §4.2 was amended by the owner (v2 LOG 2026-10-29,
"The owner's four answers ...").

## 2026-10-06 — Δ2: an experiment at the event cap is truncated, not stopped

**Approved** in outline (P4-PROPOSAL Δ2). The SPEC-silent details are **for
review**.

- **τ is the time of the N-th event, which is kept,** whether or not an
  (N+1)-th would follow. Stopping only when an (N+1)-th appears would make
  "truncated" depend on the future. At the N-th event, τ = min(t_N, H) is a
  stopping time and Σ log λ − ∫₀^τ λ is the exact likelihood.
- **The dataset keeps the planned horizon H,** so `Trend` still reads t/H. The
  tail [nextafter(τ), H) joins `excluded`, so the fit-key schema is unchanged.
- **N counts every history event,** including forced, clamped and censored
  ones.
- **The observational log is never truncated.** A truth whose free log reaches
  the cap must fail loudly. `max_events=0` with truncation is refused.
- **Runs below the cap are byte-identical** to before, pinned by a digest.
- **The tool layer** charges a truncated run. Its record carries `truncated_at`
  (native τ) and `analysis_horizon`. Agent-side analysis (diagnostics, kernels,
  sandbox files, prediction checks) reads `view.clip_to_analysis`. Fits read
  the native dataset.
- **The leak rule.** The shown end is τ, unless an excluded window holds τ.
  Then it is that window's start, so the time of a censored event is never
  shown. This conservative cut can drop visible clamped or forced events
  between the window start and τ; that is accepted.

## 2026-10-06 — Δ3: design generators expand to arrays before validation; AG-o `from`

**Approved** in outline (P4-PROPOSAL Δ3). The details are **for review**.

`glm/designs.py` `expand` is a pure JSON→JSON transform run before parsing.
So the recorded and replayed design is always arrays, and a generator design
is the same experiment as its array form, byte for byte.
- `times: {start, step, n}` is computed as `start + k·step` for each k, never
  as a running sum, so it does not drift with n. n is capped at
  `MAX_FORCED_EVENTS`.
- Mark patterns are `constant`, `alternate [a, b]` and `repeat [...]`. A
  `repeat` block must divide n exactly rather than be truncated, because 134
  of the pilot's 155 rejected calls were silent count drift.
- An explicit array of the wrong length is refused with its JSON path.

AG-o can pass `{"from": "/work/x.json"}`. The path must be plain segments
under /work, and every component is `lstat`-checked, so links and reparse
points are refused. It must be a regular file of at most
`MAX_DESIGN_FILE_BYTES` (1,000,000). Its sha256 is recorded, and replay
re-runs the sandbox calls that wrote it. AG-c gets an error.

## 2026-10-06 — `ExactSum` and `total()` survive fsum's partial-sum overflow

`math.fsum` raises OverflowError when a partial sum overflows, which depends on
the order of the addends, even when the total is finite. Hypothesis found a
case with a total of about −7.94e307. `reductions._fsum` now returns fsum's
answer whenever fsum gives one, so existing results are bit-identical. Only on
OverflowError does it fall back to exact integer arithmetic on the 2^-1074
grid, with one correct rounding. A total beyond the largest double raises
`ExecutionError`. `ExactSum.value()`, `total()` and `row_totals()` all go
through it. The property test's reference is now `Fraction`, because fsum
shares the flaw.

## 2026-10-06 — Δ4: beliefs as tool calls, read into a timeline

**Approved:** belief entries are tool-enforced (decision 8).

The tools:
- **`belief(favoured, rivals, confidence)`** is free. It takes 1 to 3 rivals,
  distinct from the favoured structure and from each other after
  canonicalisation, and a confidence in (0, 1).
- **Freshness.** A belief is fresh until the next experiment. A stopped
  experiment uses it up; a refused design does not. `run_experiment` and
  `submit` are refused, uncharged, without a fresh belief.
- **`submit`** needs a `stop_reason` from `STOP_REASONS` and takes a note of
  at most 1,000 characters, which is never echoed.
- **`predict.hypothesis`** is structure-only.
- **Labels.** Every structure is recorded as canonical native text.

`investigation/timeline.py` builds the SPEC §4.4 inputs from framework-written
`record` fields only, in call order. A belief's turn is the number of
experiments run before it; no agent argument reaches a turn. `Belief` gains
`rivals` and `confidence`.

**For review:**
- (a) The runner-up is `rivals[0]` only, so a prediction under `rivals[1]` is
  confirmation-only. Counting any rival is a one-line change.
- (b) Several beliefs between two experiments are all kept, in order, and
  wavering counts as revision.
- (c) The submission is a final belief with no rivals, so submitting a refuted
  structure is a zombie return.

## 2026-10-06 — Δ6: prompt variants `v1` and `link-neutral`

**Approved** (decision 9): adopt whichever variant tries more links and depth
on the dev check. Which one is adopted waits for that Sonnet check.
`prompt_variant` is `v1` or `link-neutral`. `v1` keeps the
pilot's examples and scoring text byte for byte, and a record without a
variant reads as `v1`. `link-neutral` changes only those two places:
- the examples rotate across the exp, softplus and identity links, with one
  depth-3 example;
- the scoring sentence becomes "held-out prediction and structural distance",
  which drops "a simpler structure that is right beats a larger one".

A non-default variant gets a system-name suffix, `+link-neutral`.

*Note 2026-10-29 (merge into v2):* Settled. The owner chose **(A) `link-neutral`** as the P4
prompt variant (v2 LOG 2026-10-29, "The owner's four answers (baselines on dm-devbox, Δ6 (A), SPEC
§4.2 option 2, v2 protected) ..."). The "tries more links on the dev check" rule above was not
applied as written. For the history, see v2 LOG 2026-10-27, "Δ6 prompt variant freeze: `link-neutral`
is not adopted as drafted ...", and 2026-10-28, "Δ6 link-balanced variant red-teamed ...". The guard,
arm matching and reader listed on 2026-10-28 are still required.

## 2026-10-06 — Δ8: one void classifier, at harness level

**Approved:** void only on an executed foreign tool, a server tool use or an
out-of-bounds hit (decision 6). This entry
moves the rule from analysis into the harness. `harness/voids.py` holds the
one classifier, `judge`, and `InvestigationResult`, `runner.run` (via
`assess`) and the analysis (`classify`) all share it, so they cannot drift.
`campaign/voids.py` re-exports it, and an invariant test forbids the harness
from importing campaign.

A session is void when:
- a foreign tool executed;
- a foreign use has no recorded denial;
- a server tool was used (a block, or `usage.server_tool_use`);
- out-of-bounds code was found;
- there was an infrastructure fault.

Denied unknown names count as `unknown_tool_attempts`. `ScriptedDriver`'s
`Step` gains `foreign` and `server_tools`, so every void path has a positive
control.

## 2026-10-06 — Δ7: the session key hashes the rendered surface; harness/2

A prompt or schema edit used to resume old sessions silently under new text.
`session_text` now ends with `surface_digest`, the sha256 of
`canonical_json(rendered_config(...))`. That is the system prompt and every
tool schema, for the channels, anonymised names, arm, condition, budgets,
variant and policy. It does not hash the truth, the seed or the sandbox limits.
The opening prompt reads the observational log, so it is excluded.

`HARNESS_VERSION` is `harness/2`, covering Δ2-Δ4, Δ8 and Δ12.
`legacy_session_text_v1` is frozen. Rescore matches in tiers: the current
address, then the current digest, then the `harness/1` digest. So the pilot's
sessions are still found.

A non-default policy gets the suffix `+policy=<name>`, and policy names must
match `[A-Za-z0-9_.-]+`.

*Note 2026-10-29 (merge into v2):* Two changes from v2.
- **AG-o sessions.** v2 added the AG-o sandbox to the session key (v2 LOG 2026-10-27, "An AG-o
  session's key names its sandbox"). The surface digest still leaves the sandbox out, but an AG-o
  session text ends with the sandbox block under both harnesses. The frozen `harness/1` text is
  v2's pre-merge `session_text`, not the Windows one. Its AG-o text carries that block, and the
  Linux pilot's AG-o sessions were addressed with it. `tests/campaign/test_pilot_harness1_cells.py`
  pins it against that pilot's cells.
- **Address text.** Deriving address text from code is v2's F5 work, done after the pilot (v2 LOG
  2026-10-27, "F5, stale-cell reuse: ...").

## 2026-10-06 — Δ11 evidence policies: the core, and its SPEC-silent choices

**For review,** all of them (plan choices 1-8, 12, 15 and 17). The code is in
`systems/v2/policies.py` (random, EIG, FSD and the FSD null), the truth-aware
control-5 policy in `policy_controls.py`, and `scoring/telling.py`.

- **Menus.** Designs are the battery's shapes at horizon 2000
  (`POLICY_HORIZON`). Diagnostics are the whole catalogue in catalogue order;
  channel arguments expand in channel order and pairs as i < j. Every argument
  is resolved on the observational log and then frozen. The only required
  `period` is frozen at 1/`spectral_peak_frequency`, clipped.
- **Outcome and predictive.** A replicate reads the frozen diagnostic on the
  whole experiment log. An `InsufficientDataError`, or a stop at the 15,000
  cap (`POLICY_MAX_EVENTS`), is undefined. The predictive is an undefined atom
  plus a Gaussian over R = 16 replicates.
- **Entertained set and weights.** At most K = 4 structures, each refitted on
  all the data, uncharged; failed or uncertified fits are dropped. The weights
  are w ∝ exp(−ΔBIC/2), because SPEC §2.4 says "posterior weights".
- **EIG** is Σ w_k KL(p_k‖p_w). WP5's 256-node Gauss–Hermite rule erred by up
  to 0.14 nats at a variance ratio of 1e4. It is replaced by composite
  Gauss–Legendre: 8 nodes, panels every 0.5 sd of each component, ±12 sd. Its
  error is ≤ 2.2e-16 over 306 cases.
- **Ties.** Scores within `EIG_TIE_TOL` = 1e-13 nats of the maximum tie, and
  ties go to menu order, designs before diagnostics. FSD has no floor: it is
  closed form, and its differences are Monte Carlo signal. Each EIG decision
  records H(w), the maximum EIG, the tied cells and a `degenerate` flag. The
  weighting, the tolerance and the quadrature id are part of the EIG policy's
  config, and so of the key.
- **FSD** is He²(top-weight fit, Wiener–Hopf on the observational log with iid
  empirical marks). The raw value selects. `fsd_null` (B = 8 bootstraps) is a
  separate negative control. Its sampling floor is about 1/(2R), not 1/(4R),
  because the mean and scale terms each give about 1/(4R): measured
  2.10/(4R) at R = 16.
- **Random** is one uniform draw over the (design, diagnostic) cells, with
  replacement.
- **Seeds.** `policy_seed(seed, decision, k, r, …)` is
  `SeedSequence(seed, spawn_key=(POLICY, decision, k, r, …))`. There is no
  design index, so designs share common random numbers. Reserved values of k
  are 2^32−1 (reference), 2^32−2 (random draw), 2^32−3 (null) and 2^32−4
  (injected parametric model).
- **Telling diagnostic.** d* = argmax over d of max over e of He²(truth, B-lib's
  pick on obs), with R = 32 on the TELLING stream. Stopped replicates are
  dropped, not counted as undefined, and a design where either side always
  stops scores 0. Otherwise an exploding design ties every diagnostic at 1:
  S11 against Hawkes gave `mean_rate`.

Cost per decision at fit/4 (R = 16, 8 designs): EIG at K = 3 took 97-116 s,
FSD 81-86 s and one FSD-null bootstrap 85-97 s. An exploding Wiener–Hopf
design costs about 23 min per decision. The weights and that cost are open
(see the 2026-10-09 entry on open decisions).

*Note 2026-10-29 (merge into v2):* Several points above are superseded by v2 entries.
None of these is in the code yet. Each lands with the integration, test first.
- **Weights and ties.** BIC weights are kept. EIG is computed to relative precision. A full
  tie is broken by the truth-keyed random draw, not by menu order (v2 LOG 2026-10-27, "Δ11 EIG
  weights: keep BIC, report the degeneracy, break full ties from the truth-keyed random draw,
  compute EIG to relative precision"). This was adopted on 2026-10-28 ("Δ11 EIG weights and
  review items (a) to (e) adopted").
- **Seeds.** Every policy stream is keyed by truth (v2 LOG 2026-10-27, "C5 random-arm seeding:
  key every policy stream by truth"). This was adopted on 2026-10-28.
- **Telling diagnostic.** Stopped replicates are no longer dropped. Under SPEC §4.2 option 2,
  d* is chosen among designs on which neither the truth nor the best member explodes. The
  exclusion is shared with truthFSD, and the explosion share is reported (v2 LOG 2026-10-29,
  "The owner's four answers ..."). The `Telling` code change waits for the preregistered at-stop
  controls A1 and B to pass (v2 LOG 2026-10-29, "FSD at-stop controls, criteria v8 ...").
- **The 15,000 cap** stays (v2 LOG 2026-10-29, "FSD at-stop rule: the drop-rule draft is
  withdrawn ...; the cap stays 15,000; open").

## 2026-10-06 — Δ11 policy mode for AG-c-S; AG-S-boed

**For review** (plan choices 10, 11 and 16). With a policy (random, EIG or
FSD; AG-c only), `run_experiment` takes no arguments, and the belief gate
still runs first.
- **What the framework does.** It refits the latest belief's structures
  (K ≤ 4) on all the data, uncharged and unshown, and picks the cell. The
  design runs through the same `_run` as an agent's, with the same charging,
  sealing and truncation.
- **What the agent sees.** The design in its own names and units, and the
  chosen diagnostic's value. Never a score, a weight or a BIC. The prompt
  never names the rule.
- **What only the record holds.** Under `policy`: the cell, the config, the
  BICs and weights, the score table and the degeneracy summary.
- **Fallback.** If no structure can be fitted, the decision uses the random
  draw, and that is recorded.
- **The key.** `surface_digest` adds the policy's framework-only config for
  policy arms, so the arms are separated without the agent seeing the rule.

AG-S-boed is `LLMArm("AG-c", "named", policy="EIG")` on Sonnet:
`AG-c-S/named+policy=EIG`. Predictions made in a policy arm are left out of
contrast C6, because the agent did not choose the experiment they test. The
analysis applies it (2026-10-09) in `behaviour_sessions`, keyed on the arm's
policy: such a session's predictions leave calibration, falsification-seeking
and revision, and the session is counted as excluded. Its score and its
stopping are kept, since the agent still chose when to experiment and submit.

## 2026-10-09 — WP10 gate: any typed error inside a policy decision is a void

`Lab._run_policy` caught only `PolicyError`. Other typed errors from inside a
decision (for example `BoundViolationError` or `DiagnosticArgumentError`)
reached the harness, which shows a `SciAgentError`'s message verbatim. That
leaked policy numbers, and native channel names in anon, and the call was
neither charged nor void. Now any `SciAgentError` in a decision is an
infrastructure fault, which voids the run. The agent is told only that nothing
ran and nothing was charged. The fix was test-first.

When the branch was rebased onto v2 (ea055be), v2's exact-denial-text rule (F4)
was ported into `harness/voids.py`, where WP4 had moved the classifier.
`ForeignResult` carries the result text, and `campaign/voids.py` re-exports
`denial_text`.

## 2026-10-09 — B-sym policy arms and the telling unit

`B-sym+{random,EIG,FSD}`, the control `B-sym+truthFSD` and the `telling` unit
are opt-in: they are not in `BASELINES`, and every existing unit key is pinned
unchanged. **For review** (plan choices 9, 12 and 13):
1. **Epochs.** Epoch 1 is plain B-sym with F//2 fits on obs and the arm's
   search seed, so it is identical across policies. Epoch 2 gets the rest of
   F, so an odd fit goes to epoch 2. Its GP seed is
   `SeedSequence(search_seed, spawn_key=(2,))`.
2. **Epoch 2's start.** It starts from epoch 1's top `GPConfig.population`
   certified structures. Refitting them on the new data costs epoch-2 fits.
3. **Submission.** The best BIC among epoch-2 fits only, because epoch-1 BICs
   are on other data. No certified epoch-2 fit fails the unit.
4. **Entertained set.** Epoch 1's top four certified structures by BIC, fixed,
   and refitted (uncharged) on obs plus the collected data at every decision.
   With no certified candidate or refit, the decision uses the random draw.
5. **Policy seed.** The investigation seed, as in AG-c-S.
6. **Pairing.** Experiments run on the (truth, seed) truncating World, paired
   with the agent arms, with E = `CampaignConfig.experiments`.
7. **truthFSD.** The truth is `TruthModel(truth.spec)`, built in `execute.py`
   as ORACLE's is. The comparator is B-lib's pick on obs.
8. **Telling unit.** It is its own unit kind, with R = 32 and a cap of 15,000.
9. **Scoring** refits the submission on obs only.
10. **"Observed"** means the policy's chosen diagnostic was computed on an
    experiment that left data. A failed unit is not observed. The rate weights
    truths equally.

*Note 2026-10-29 (merge into v2):* Two items are superseded.
- **Item 5.** The policy seed is a per-(truth, seed) key derived by the framework, not the
  investigation seed (v2 LOG 2026-10-27, "C5 random-arm seeding ..."; adopted 2026-10-28).
- **Item 8.** The 15,000 cap stays. The telling unit's at-stop handling follows SPEC §4.2
  option 2 (see the note under "Δ11 evidence policies").

## 2026-10-09 — Positive controls 4-6 (preregistered)

Plan choice 18 puts controls 4-6 here rather than in SPEC. The code is
`scoring/controls_p4.py`. Each control must pass before the contrast it gates
is reported. A failure means fixing the instrument first (§7.1).

- **C4, naming null (gates C2).** A scripted planted-hint agent runs through
  the tool layer on the same truth and seed in every naming condition (named
  and anon; `rescaled` joins when Δ9 lands). It records a belief in the
  truth's hint, fits the hint and then the truth on obs, runs one unintervened
  experiment, fits both on obs + e0, and submits the truth. It passes if and
  only if the canonical submission, and every fit's structure, datasets and
  native log-likelihood, are bit-identical to named's. The time factor is a
  power of two, so no tolerance is allowed.
- **C5, policy separation (gates C3 and C5).** B-sym+truthFSD against
  B-sym+random, paired by (truth, seed), on whether d* was computed on
  experiment data. A failed unit counts as not observed. With b the units only
  truthFSD observed and c the units only random observed, the one-sided exact
  McNemar p is P(Binom(b+c, ½) ≥ b). It passes if and only if p < 0.05. An
  unpaired unit voids the read. It cannot pass on fewer than 5 discordant
  units. Both arms' truth-weighted telling rates are reported beside it.
- **C6, behaviour instrument (gates C6).** The 14-call scripted agent of
  instrument test 10b runs through the live loop. Its record, read by
  `timeline`, must reproduce the hand values, with floats equal to 1e-12
  relative:
  - refusals at calls 0, 5 and 11 (0-based), all uncharged;
  - calibration coverage 2/3 at mean level 11/15; A (n 1, coverage 0, level
    0.9), B (2, 1, 0.65);
  - falsification-seeking 1/3;
  - one falsification, revised, and one zombie;
  - favoured path A, B, A, C;
  - stopping converged at 3, 0 burned, not early, used share 1.

**For review:**
- C4 submits the truth, because the hint drops mark functions and only the
  truth's text carries channel names through the anonymiser.
- C4 includes one experiment, so that the anon time rescaling is crossed.
- C4 and C5 refuse partial or unpaired input rather than dropping it.

*Note 2026-10-29 (merge into v2):* C5's random arm now draws per truth (v2 LOG 2026-10-27,
"C5 random-arm seeding ..."; adopted 2026-10-28). This is the open point 3 below. truthFSD shares
telling's exclusion of exploding designs under SPEC §4.2 option 2 (v2 LOG 2026-10-29, "The
owner's four answers ..."). The FSD at-stop rule has its own preregistered controls, A1 and B,
which are separate from C4 to C6 (v2 LOG 2026-10-29, "FSD at-stop controls, criteria v8 ...").

## 2026-10-09 — Open decisions for the user (not decided)

Each has a setting or a proposal. None is resolved here.

1. **EIG weights.** On dev the BIC posterior is degenerate. On dev-013,
   H(w) ≈ 3e-13, so EIG is near 0 everywhere and some cells clear the tie
   floor with negligible EIG; the pick then follows tiny but correct values.
   That is the evidence-wall proposition, not a bug. A tempered weighting,
   `tempered(T)`, exists but is off by default. `PolicyArmSettings.weighting`
   (default BIC) is the lever for the B-sym arms. AG-c-S has no weighting
   setting and uses BIC.
2. **FSD cost and the policy event cap.** An exploding Wiener–Hopf design
   costs about 23 min per decision. `PolicyArmSettings.max_events` exists at
   its default of 15,000, which is the current behaviour; changing it moves
   only the EIG, FSD and truthFSD unit keys. AG-c-S's `PolicySettings` has no
   cap setting yet.
3. **C5 random-arm seeding.** `policy_seed` carries no truth id. So on a given
   seed, the random arm draws the same (design, diagnostic) sequence on every
   truth, for both B-sym+random and AG-c-S+random. Pairing is intact, but C5
   then compares truthFSD against one fixed random draw, so its verdict does
   not generalise to random selection. The proposed fix keys random draws by
   truth too. That moves the unit keys of the four B-sym+* arms and of
   AG-c-S+random, so it must be decided before any policy unit runs for real.

*Note 2026-10-29 (merge into v2):* All three are decided in v2's LOG. Each lands with the
integration, test first.
1. **EIG weights.** Adopted: BIC is kept, ties are broken by the truth-keyed draw, and the
   degeneracy is reported (v2 LOG 2026-10-27, "Δ11 EIG weights: ..."; 2026-10-28, "Δ11 EIG weights
   and review items (a) to (e) adopted").
2. **The event cap.** A 6,000 cap was drafted outside this branch and not adopted (v2 LOG
   2026-10-27, "FSD event cap: the draft's 6,000 is not adopted; a measured choice is needed
   first"). After the measurement, the cap stays 15,000 (v2 LOG 2026-10-29, "FSD at-stop rule:
   ... the cap stays 15,000; open"). The at-stop rule is SPEC §4.2 option 2, under the
   preregistered controls A1 and B (v2 LOG 2026-10-29, "FSD at-stop controls, criteria v8 ...";
   "The owner's four answers ..."). AG-c-S's `PolicySettings` now carries `max_events` and
   `weighting` at their defaults, and both enter the session key (rehearsal commit f3da328). The gap
   in `make_policy` was found in v2 LOG 2026-10-28, "The FSD event-cap measurement ...", and again on
   2026-10-29.
3. **C5 seeding.** Adopted: every policy stream is keyed by truth (v2 LOG 2026-10-27, "C5
   random-arm seeding: key every policy stream by truth"; 2026-10-28, "C5 random-arm seeding
   adopted").

## 2026-10-27: The reference platform for v2 numbers is dm-devbox (owner)

The owner's Windows PC is no longer used. From today every reported v2 number
is produced on `dm-devbox`: Ubuntu 24.04 on AWS (EC2 m7i-flex.xlarge, Xeon
Platinum 8488C, 4 vCPUs, 16 GB), Python 3.12.3, numpy 2.5.1 with AVX-512
dispatch. v1's frozen numbers stay as recorded on Windows. Why: the owner's
machines changed, and one platform must own the numbers because output bytes
are not portable (next entry). What the Windows PC held and GitHub does not
is gone from this project's reach unless the owner recovers it: the pilot
store `.cache/pilot`, the mini-pilot store, the defect-hunt scripts under
`.cache/hunt/`, the `p4-deltas` branch (P4 work packages 1-8 committed, 10
uncommitted), `.cache/p4/PLAN.md`, the anchoring-check worktree, and the
`p3-fit4` tag (its fitter is the one at 183da42, and b5e2b87 has the same
fitter).

## 2026-10-27: Fit bytes are per machine class; the fit/4 reference digests are re-pinned

On dm-devbox the fast tier failed only on the six fit/4 reference digests
(`tests/glm/test_fit_search.py`, also used by `test_fit_memory.py`): 12
failures, every case different from the Windows bytes. The fit/4 fitter
(b5e2b87) and fit/5 (v2 at ea055be) give identical digests on dm-devbox for
all six cases, so the property the test pins (the exhaustive path is fit/4
bit for bit) holds on Linux. On the same machine the bytes change with numpy's
SIMD dispatch (`NPY_DISABLE_CPU_FEATURES` removing AVX-512 changes all six)
and with OpenBLAS's kernel (`OPENBLAS_CORETYPE=Haswell` changes two; SkylakeX,
Cooperlake and SapphireRapids give the default bytes); no setting tried
reproduces the Windows bytes. Decision: re-pin the digests to dm-devbox's
fit/4 values and say in the test that they are per machine class. The
Windows values, for the record: hawkes-identity `2c9ebbea…`, periodic-exp
`3abbe355…`, periodic-softplus `93ee8446…`, power-identity `6cb6f6f1…`,
two-datasets `30762740…`, window-exp `b2ce799d…`. Consequences: the invariant
"same seed, config and version give the same bytes" holds per machine class,
which the content address does not record, so no store is ever filled from
two machine classes without a byte check first; and the determinism child
(`tests/acceptance/determinism_child.py`) was byte-identical across two runs
on dm-devbox (sha256 of its output `91ccdcef…`).

## 2026-10-27: The P3 pilot is rerun from scratch on Linux (owner)

The pilot store stayed on the Windows PC, so the P3.1 plan to rescore the 240
stored LLM sessions under fit/5 (P4-PROPOSAL decision 1) cannot apply. The
whole pilot is rerun on dm-devbox under fit/5 from v2: every non-LLM unit and
all 240 LLM sessions live, as SPEC §7 and `scripts/pilot.py` specify. A side
effect is that the sessions are now conducted under the fixed fitter, which
removes the declared fit/4 limitation. The order stays as fixed before:
smoke, the mini-pilot gate (criteria of HANDOFF-2026-10-06, with memory
judged against dm-devbox), the full run, then the §7.1 read under the rules
of P4-PROPOSAL §2.4. The dev split is git-ignored, so it is regenerated on
dm-devbox from the fit/4 code that made it (b5e2b87, seed 2026, n = 20).
Given the previous entry its sha256 is not expected to equal Windows'
`1f0fac01…`. The rule, fixed before the result is seen: if the 20 truths
match PILOT.md's truth table (ids, strata, dictionary membership and DSL
text), the Linux bytes become the dev split and their sha256 is logged; if
any truth differs, that is recorded as a new dev population and the pilot
reads it, since the dev split is not preregistered material (the test split
is). The P4 check of `check_identifiable` under fit/5 on the dev truths
still applies.

## 2026-10-27: Money: a $150 monthly cap; pilot baselines on dm-devbox first (owner)

sciagent may spend at most $150 a month on AWS, inside the shared account's
brake of $1,000 a month (`~/research/CLAUDE.md`). Costs go in
`docs/v2/LEDGER.csv` (agent-control's columns), results only under
`s3://delaymargin-results-482913/sciagent/`, and every launch states its
ceiling first. Recommendation recorded with the decision: run the pilot's CPU
baselines on dm-devbox itself, nice'd and with at most 2 pool workers (the
machine also hosts two Claude orchestrators), unless that would exceed about
48 hours of wall time. The Windows projection for the non-LLM rerun at fit/5
was 178-221 worker-hours (LOG 2026-10-06), which is 89-110 hours at 2
workers, so on present evidence it would exceed 48 hours; the Linux
mini-pilot measures the real per-unit times before that is settled. The
noted AWS option for later: m6i.xlarge (the only CPU type the launch policy
allows), 4 vCPUs and 16 GiB, $0.204 an hour on demand in eu-north-1 (pricing
API, today), so about $9-11 for the whole non-LLM rerun at 4 workers on one
instance. Before any of it: sciagent has no launch tooling yet, and an
instance must first reproduce dm-devbox's bytes on the six reference fits and
the determinism child, because of the per-machine-class finding above.

## 2026-10-27: An unattended lower-priority lane works on v2 (owner)

Headless Claude sessions run sciagent unattended (`~/bin/lane.sh sciagent`)
at about 25% of the shared Max usage; agent-control has the other 75%. They
follow `docs/v2/AUTOPILOT.md`, take work from `docs/v2/QUEUE.md`, keep
`docs/v2/JOURNAL.md`, commit on `v2` and push `v2` only; they never commit to
or push `main`, and merging to `main` stays the owner's `/ship`. LLM arms run
under the Max subscription through the Agent SDK: never usage credits or API
keys, and never Fable. Interactive sessions keep the existing rule: no commit
unless asked, no push without asking.

## 2026-10-27: Dev split regenerated on dm-devbox: same 20 truths, new bytes

`scripts/sample_truths.py --split dev --n 20 --seed 2026 --workers 2` from
b5e2b87 (fit/4) ran in 1,882 s on dm-devbox. All 20 truths match PILOT.md's
truth table in id, stratum, dictionary membership, nearest distance (three
decimals) and DSL text; strata 7/4/9, links 7 identity, 2 exp, 11 softplus,
10 out of dictionary, as on Windows. The records' sha256 is
`604acec5359d9a33b8aedc500de8dff14600041225c386766ec3544b2e8d4420` (Windows:
`1f0fac01…`), as the per-machine-class finding predicted. By the rule fixed
before the result, these bytes are now the dev split at `data/v2/truths/dev`
(a copy of `dev-linux-fit4`, both git-ignored). Caveat: the Windows records
are gone, so ψ, θ and the calibration numbers could not be compared, only
what PILOT.md printed.

## 2026-10-27 — Rootless Docker, the P4 branch, and proposals with a veto window

The owner chose rootless Docker for the AG-o sandbox: the daemon runs as the machine user, so
using it grants no root, and the root daemon is disabled. The `p4-deltas` branch and the
`p3-fit4` tag will be pushed by the owner from the Windows PC rather than redone. The five
decisions open since 2026-10-06 are settled by the lane proposing each one (reasoning, red-team
review, a 24-hour veto window recorded here), adopted unless vetoed in docs/v2/VETO.md.

## 2026-10-27: Sandbox on native Linux Docker: an ownership handoff around each call

Under rootless Docker the sandbox's uid 65534 maps to host uid 165533 (`/etc/subuid`
`ubuntu:100000:65536`), so the code could not write the host-created `work/` (uid 1000, 0755)
and the host could not remove what the code wrote: 4 of 18 slow sandbox tests failed with
`PermissionError`. The agent container is unchanged (uid 65534, no capabilities, no network,
read-only root, same limits). Each call is now bracketed by a helper container from the same
image running a fixed `chown -hRP <owner> /work && chmod -R u+rwX /work` with only CHOWN,
DAC_OVERRIDE and FOWNER, no network and only `work/` mounted: it gives `work/` to 65534 before
the call and back to the host user after it (in a `finally`). The owner comes from `docker
info`: `0:0` under rootless (the host user), the host's `uid:gid` under rootful, no handoff
under Docker Desktop. Running agent code as container root was rejected: under rootless a
breakout would land as the host user, who owns the truths and the store, instead of an unowned
subordinate uid; under rootful it would be real root. Cost about 0.8 s per call. No version
constant moves and `result_digest` is computed as before (two fresh sandboxes gave identical
digests), but what code sees between calls changes: modes come back with `u+rwX` (a file set to
0o040 returns as 0o640), so mode-inspecting code prints differently than under Docker Desktop.
Not tested: rootful Docker (disabled here) and Docker Desktop (code path unchanged). The red-team
review (verdict: commit with follow-ups) found the back handoff does not run on every path and
can run before the agent container is confirmed gone; those are queued to be fixed before any
AG-o run produces reported numbers.

## 2026-10-27: The defect-hunt checks rebuilt as `sciagent.campaign.audit`

The scripts lost with the Windows PC are rebuilt from the contracts in HANDOFF-2026-10-06,
STATUS-2026-10-05 and LOG 2026-10-03, not line for line. `scripts/hunt_gap.py` finds cells by
listing the store, filtered by the fitter named in `env_version`, and pairs a unit with ORACLE
only within one configuration (env, fit config, data, metric version, truth, seed); a missing
ORACLE reference or an unrecorded underfit flag fails the gate, as control 1 requires. Report
determinism (`scripts/report_determinism.py`) means byte identity across hash seeds on one
store; identity across two runs is checked cell by cell (`scripts/store_compare.py`), because
the report prints the store path and wall times. `scripts/store_check.py` checks triggers,
digests, detail bytes and references. Found on the way, not changed: `campaign/llm.py:221`
writes `wall_s` into the `llm-session` cell's reading, against the store's own rule that wall
times are never in a cell, so session cells differ between two runs; `store_compare` therefore
takes `--kinds`. Moving it would change session cell bytes; left for the owner.

## 2026-10-27: The dev truths stay identifiable under fit/5; the dev split is kept

`scripts/recheck_identifiable.py` reruns `check_identifiable` (δ = 0.01 nats/event against the
five library members) on the 20 dev truths, rebuilding their identifiability data from the
records with the sampler's own functions. Positive control: from b5e2b87 (fit/4) it reproduces
every stored margin, log-likelihood, event count and ψ flag exactly on dm-devbox (all
differences 0), and the split's config digest equals the manifest's (`c2689fe0…`). Under fit/5
all 20 still pass, so no truth changes status and the dev split is kept (P4-PROPOSAL §2.2).
Member scores are unchanged; the truth's held-out log L rises on 8 truths, is identical on 11,
and falls on one (dev-001, by 3.66 nats, where fit/5 fits the training log better at a wrong ψ;
its margin is 0.106). The smallest margin is 0.0280 (dev-002, unchanged); dev-019, the tightest
under fit/4 at 0.0244, rises to 0.0698. Note for ORACLE checks: the ψ-recovered flag must allow
for symmetries such as `Product(Periodic, Gate(Periodic, ·))`, whose two periods can swap with
an identical likelihood (dev-011: recovered under fit/4, "not recovered" under fit/5 with the
same held-out log L). Outputs in `.cache/ident/` (git-ignored).

## 2026-10-27: Session wall time leaves the `llm-session` cell (proposal; veto window ends: 2026-10-28T12:00Z)

Proposed under the veto rule (queue item "Session cells carry wall time"). The lane's first
draft kept `wall_s` in the session cell's reading and amended the store's rule. The draft argued
the value is a pure function of the stored record, and that live session cells are not
reproducible anyway. The red-team review rejected it, and both claims fail on checking:
- The record's `timing` section is deliberately not hashed (`harness/record.py:20-22`), so two
  records with the same address give different cells.
- Under the scripted model driver, `wall_s` is the only difference between two runs' session
  cells. `tests/campaign/test_audit_driver.py:85-98` asserts exactly that one differing cell.

The cell's detail also copies `duration_api_ms` and `total_cost_usd` from the same unhashed
section (`campaign/llm.py:204-205`). The reading's `wall_s` also mixes three measurements:
- the harness's elapsed time, for normal sessions;
- the driver's clock including process spawn, for hard timeouts (`llm.py:327`);
- nothing at all, for aborts, which counts as 0 in `pilot_report.py:587`'s median.

**Proposal:**
- Move `wall_s`, `duration_api_ms` and `total_cost_usd` out of the session cell into its
  `timing.jsonl` line, which is already keyed by the cell digest. Keep the harness's own time
  as `harness_wall_s` beside the driver's `wall_s`.
- Have the pilot report read session wall times from `timing.jsonl`.
- Make `test_audit_driver` assert that every cell kind is byte-identical across two
  scripted-driver runs.

This changes session cell bytes, and no store needs them: the Windows store is gone and the
Linux smoke has not run. It goes in before the full pilot. The mini-pilot (no LLM units) is
unaffected. Not to be implemented while the sandbox follow-ups are editing `campaign/llm.py`.

## 2026-10-27: The rebuilt hunt checks cross-checked against the salvaged originals

The Windows hunt scripts (`origin/windows-salvage`, `salvage/windows-2026-10/hunt/`) show that
the first rebuild covered only §6 of hunt B's `store_check.py` and the gap half of hunt A's
`p3_gap.py`. Ported, test first, into `sciagent.campaign.audit`:
- `check_budgets` (§3): each completed session's record must charge exactly the successful
  budgeted calls, and the cell's counts must match the record's.
- `check_sessions` (§2, §5; script `scripts/session_check.py`): every stored session must be
  found by the rescore's own enumeration, with the reason named when it is not. Void-policy
  disagreements are warnings.
- `KEY_FIELDS` (§6): the key fields each kind of cell needs.
- `gap_check(truth=...)` (`hunt_gap.py --split`): p3_gap's "BEATS-TRUTH" flag.

**Proposal (veto window ends: 2026-10-28T13:00Z).** The one real decision changes a gate criterion
fixed in HANDOFF-2026-10-06, so it goes under the veto rule rather than being adopted by the
engineer who wrote it. Under it, a unit above the truth's own held-out parameters by more than
the threshold **fails** the gate when `--split` is given. The code already does this. If
vetoed, it reverts to a printed warning; the mini-pilot gate is not judged before the window
ends. The red-team review the veto rule asks for stalled before reporting (its only note: the
dev truths' held-out logs hold about 1,800 to 2,330 events). It must be rerun and pass before
adoption, whatever the window says. The original only printed it.
ORACLE units are included, though p3_gap skipped them. The reason is that a fitted model
cannot beat the data-generating process on independent held-out data by 0.01 nats/event except
by noise far below that, or by a leak. A flag that only prints is one no test can see. The
reference uses the cells' own held-out log and their default quadrature rule (the original used
the campaign FitConfig's rule). `pilot.campaign_config` is now shared by `pilot.py` and
`session_check.py`, so the check rebuilds exactly the run's configuration. No fit or scoring
output changes, so no version constant moves.

## 2026-10-27: An agent that floods `work/` is scored, not voided (proposal; veto window ends: 2026-10-28T15:00Z)

Sandbox follow-up item 3 asked how to classify a back handoff slowed or broken by the agent's
own files. Proposed (implemented on the follow-ups branch): past `SandboxLimits.max_work_entries`
(100,000) or `max_work_path_bytes` (1,024), the back handoff empties `work/`, tells the agent in
the call's stderr, and the run is **scored**. The handoff timeout is 10 × `wall_seconds` with a
300 s floor. Why: the outcome is caused by the agent's code, decided by a count, visible to the
agent, and covered by the call digest; voiding it would select the sample on agent behaviour.
SPEC (docs/SPEC.md:558) voids only for out-of-bounds access; voids stay for that and for
infrastructure faults. The red team (2026-10-27) agreed and found no path from `work/` to a
score, a fitted parameter or held-out data, but showed the "agent cannot cause a handoff
failure" bound rested on one flat-directory measurement: a directory chain hundreds of
thousands of levels deep may exhaust the 256 MB cleanup helper and turn back into an
agent-caused void. That case, and recording the limits and the helper's sha256 in the run's
record (they are outside the image tag), must be closed before any AG-o run that produces
reported numbers. Known and not addressed: bytes on disk per call are bounded only per file;
the existing `hard_timeout` void is also conditioned on agent behaviour. If vetoed: such runs
are voided as `agent_resource` and reported as a separate count.

## 2026-10-27: Beats-truth gate: red-team review done; adopt at +0.01 with a digest check; the stated reason corrected

The rerun review (narrower question) recommends adopting the hard failure at +0.01 nats/event,
threshold unchanged. An honest fit on independent held-out data exceeds the truth by c nats
with probability at most e^-c (Markov on the likelihood ratio, valid for any proper density):
at 1,767 events (the smallest dev log, dev-007 seed 1; not "about 1,800") the bound is 2e-8 per
unit, and ORACLE on seven dev truths at seed 1 sat 0.00003 to 0.0045 per event below the truth.
**Correction to the entry above:** "except by noise far below that, or by a leak" overstates the
check. A full leak of the held-out log into a k-parameter fit gains about k/2 nats in total,
0.0008 to 0.0045 per event at k = 3 to 18, so it is caught only for large k (B-np's 24·D+1
parameters, yes; ORACLE, no). The gate catches gross mismatches and rich-model leaks, not
small-model leaks. Conditions before adoption, being implemented: compare each unit's
`truth_digest` with the split given (two byte-different splits share the 20 ids), and stop the
check being skipped silently when `--split` is missing. Unverified: whether identity-link fits
(λ ≥ 0 only at training nodes) are proper densities, which the e^-c bound assumes. The veto
window of the entry above (2026-10-28T13:00Z) is unchanged.

## 2026-10-27: The beats-truth reference is tied to the truth record, and `--split` is required

These are red-team conditions on the beats-truth proposal above. They do not adopt it; the
review and the veto window still decide that.

**Matching the truth record.** A truth id does not identify a truth. The Windows split
(`1f0fac01…`) and the Linux split (`604acec5…`) share their 20 ids with different records.
`gap_check`'s `truth` reference is therefore now a `TruthReference(digest, per_event)`. Its
digest is computed the way `pilot.truth_entries` computes a cell's `truth_digest`. Each unit's
own record is read from its cell:
- a baseline: its key;
- an `llm-score`: its session cell, which must be a committed `llm-session` cell for the same
  truth and seed.

A unit fails the gate, named in `digest_issues`, when its record:
- cannot be established, or
- is not the reference's, or
- (without a reference) is one of several records within one (truth, seed) group, because
  that group's gaps would pair different truths.

Such a unit gets no margin over the truth.

**Why `--split` is required.** `hunt_gap.py --split` is now a required argument rather than a
missing flag that prints "not run" and exits non-zero. The two behave the same, but argparse
refuses before any work is done, and no output can contain a gate verdict without the
beats-truth half. The script names no default split because which split the store was run on
is exactly what the operator must state. It also prints that split's `truths.json` sha256.

**Positive control.** The dev-006 positive control, on the real dev split at seed 1, has a
measured margin of −0.00033 nats/event (an honest ORACLE fit through the pilot's configuration).
It is now a slow test.

No fit or scoring output changes, so no version constant moves.

## 2026-10-27: Emptying an over-limit `work/` is bounded in memory; the handoff timeout is 20× the wall time

The red team's deep-chain case was real. A 60 s call nesting directories built a chain about
910,000 levels deep, and GNU `find -delete` in the 256 MB helper was still running after 10.5
minutes with its memory growing. That would have voided the run on the agent's account. The
helper now empties `work/` breadth-first through a queue directory (`_handoff_helper.empty`):
at most three directories open, memory flat (about 13 MB), time linear in the tree. The same
chain empties in 278 to 390 s, bound by disk reads. The handoff timeout rises from 10 to 20
seconds per wall second (1,200 s at 60 s), about 3 times the slowest shape tried. That is a
measured margin, not a guarantee, and a real harness hang now takes 20 minutes to detect.
`max_work_path_bytes` is capped at 1,024 (Python 3.12's `shutil.rmtree` recurses per level).
AG-o records now carry every `SandboxLimits` field and the helper's sha256, and replay refuses
a mismatch. AG-c record bytes and the result digest are unchanged, and no AG-o record exists
yet, so no version constant moves. Open: a campaign session's key (`session_text`) covers
neither the sandbox limits, the helper hash nor the image tag, so stored AG-o session cells
could be reused across a change to any of them (queued; decide before AG-o cells that count).

## 2026-10-27: C5 random-arm seeding: key every policy stream by truth (proposal; veto window ends: 2026-10-28T16:05Z)

Open decision 6 (HANDOFF-2026-10-09; LOG on `origin/p4-deltas`). `policy_seed(seed, decision,
k, r)` carries no truth, and the menu size depends only on the channels (280 cells for
pointproc), so on a seed every truth's random arm draws the same (design, diagnostic) indices:
3 sequences in a 3-seed design, and C5's comparison against random would not generalise to
random selection. Recommendation: derive a per-(truth, seed) policy seed framework-side,
`stable_key(f"{truth_id}|{seed}|policy") % 2**63` as `search_seed` does (`campaign/plan.py`),
and pass that opaque integer where `policy_seed=` takes the investigation seed today
(`execute.py`, `investigation/runner.py`), so every policy stream (random draw, fallback, EIG and
FSD simulations) depends on the truth and the agent-facing modules never see a truth id. Key on
the truth id, not the digest: digests change with the machine class, and the two dev splits
sharing ids hold the same truths, which is pairing, not a collision. Within a (truth, seed) all
arms and both naming conditions still share streams. The red-team review (session 4) adopted it
with changes, folded in here: the random fallback is used by all seven policy arms (AG-c-S+EIG
and +FSD through `random_cell`, the three B-sym policy arms through `random_choice`), so the
change moves every policy unit key, not the five the earlier note listed, and costs nothing
extra for EIG and FSD; the address text must say so (B-sym's `policy_seed=investigation seed`
at `plan.py:571`, and a field in AG-c-S's `policy_config`), or the bytes would change under an
unchanged key; the `seeds.py` docstring sentence on shared policy streams is rewritten. The
reason is consistency with the scorer and search streams, which are truth-keyed; it is not a
variance argument, since world data on a seed is already shared across truths
(`world.py`, `SeedSequence([seed, stream, index])`). No policy unit has run for real. Implement
on the `p4-deltas` integration, test first (seeds are an instrument), after the window unless
VETO.md objects.

## 2026-10-27: Clarabel's `optimal_inaccurate` stays accepted; recording it waits for the next fitter version

The smoke log's repeated `Solution may be inaccurate` (`glm/fit_solve.py:591`) is CVXPY 1.9.3
reporting Clarabel 0.11.1 `AlmostSolved` on identity-link cutting-plane subproblems, a stall
(25 to 31 iterations, not the 100 cap; `max_iter=2000` gives identical bytes). It is not from
ORACLE, B-lib or B-np on dev-000 (softplus link, 0 Clarabel calls when counted); B-rand on
dev-000 seed 1 reproduces it at 3 of 240 ψ points. All three are certified by the code's own
duality gap (relative 9.2e-10, 9.9e-10, 3.9e-11 against 1e-8). Against a tight reference solve
(Clarabel tolerances 1e-12, `optimal`, relative gap ~1e-14) the shipped solves are worse by at
most 1.4e-6 nats per fit and 8.4e-7 nats per held-out event, about 10^4 below the +0.01 gates,
and the points are ~25 nats from the chosen one, so selection cannot flip. Same bytes in three
processes. Decision: keep `ACCEPTED_STATUS = {optimal, optimal_inaccurate}`; the certificate,
not the solver's label, is the test (Clarabel's default tolerance once reported `optimal` while
less accurate). Do not tighten now: it would bump `FIT_VERSION` for a 1e-6 change. At the next
planned bump, record a count of inaccurate ψ solves in `FitResult` and the unit detail (today
only the chosen point's `solver_status` survives, and the store keeps neither), and consider the
tighter settings. The number of warning lines in a log means nothing (once per process per line).
Noted, not acted on: the fitter certifies to 1e-8 relative while `oracle_underfit` flags at
1e-9 relative; fitted ORACLE log-likelihoods sit 0.1 to 1 nat above the truth's, so not a risk
on the truths checked (dev-000, dev-004, dev-006). Evidence: `/tmp/inacc/*.log` (session 4).

## 2026-10-27: An AG-o session's key names its sandbox (proposal; veto window ends: 2026-10-28T17:00Z)

`session_text` covered neither the sandbox limits, the handoff helper's sha256 nor the image tag,
so `scripts/pilot.py` would reuse a stored AG-o session cell across a change to any of them.
Recommendation, implemented test first on the local branch `worktree-agent-a501a8588ae2e77cb`
(2dbd125, based on 5cab148, not merged): for AG-o only, append `sandbox=<sorted-key JSON of the
same dict the record carries: every SandboxLimits field and HANDOFF_HELPER_SHA256>; image=<tag
from image_tag(SANDBOX_CONTEXT), no Docker call>`. The limits come from one new source,
`investigation.runner.run_sandbox_limits()`, which `run()` now builds its spec from (the campaign
config carries no sandbox settings because `run()` takes none). AG-c text and digests are
unchanged and pinned (AG-c named `33163b91…`, anonymised `1f88aa59…`); AG-o named moves
`68b91867…` to `6617e8a5…`, so the smoke's AG-o cells will not be reused. Refusing reuse on a
mismatch is not added: reuse, rescore and audit all go by address, and the address now carries
the sandbox. No version constant moves. Caveat: AG-o `session_text` now raises
`SandboxImageError` without the `docker/sandbox` build context (a checkout is required, as it
already is to build the image). Checks on the branch: ruff clean, mypy no issues in 236 files,
fast tier `2731 passed, 3 skipped`, campaign slow tests `10 passed` and the event-cap test
`1 passed`; 26 new tests seen failing first (import, then 12 failed). Merge into `v2` after the
window unless VETO.md objects; it must land before AG-o cells that count.

## 2026-10-27: F5, stale-cell reuse: a code-fingerprint guard before the full pilot, derived address text after it (proposal; veto window ends: 2026-10-28T19:15Z)

Open decision F5 (HANDOFF-2026-10-06; LOG 2026-10-06, line 828): parts of a unit's address are
hand-written, so a behaviour change reuses stored cells unless someone bumps a label by hand.
Examples: `METRIC_VERSION` (`campaign/plan.py:82`) names 3 of the battery's 14 value constants
(`scoring/battery.py:136-158`); B-rand's text copies `max_draws_factor=100` (`plan.py:322`) from
a field default (`systems/v2/search.py:663`); the ORACLE text is a fixed sentence (`plan.py:316`);
B-lib's out-of-grammar members are keyed by type name (`plan.py:304`). The red-team review
(session 5) found the gap is wider, and I checked its two main points:
- The truth digest hashes only the split record (`scripts/pilot.py` `truth_entries`), but the
  `TruthSpec` adds the domain's `v2.CHANNELS` and `v2.mark_sampler` (`truth_spec`), so the marks
  feeding the world, held-out and battery data are in no address. Its probe gave identical keys
  for all eight systems under a changed mark sampler.
- `DATA_VERSION`'s `world/1` is also hand-written, while `investigation/world.py:65` caps the log
  (`MAX_EVENTS = 15_000`), and a failed cell counts as done (`store.done` is ledger membership).
- No code commit is recorded anywhere: `grep -rn "rev-parse\|git_commit"` over `scripts/pilot.py`
  and `campaign/` finds nothing. A frozen worktree protects one run, not a resume after a fix.
- Constants on a cell's path outside `scoring/` (`systems/v2/systems.py:263`, the `"search"` tag in
  `plan.py:239`, `execute.py:95` and `:345`, the library models' starts and boxes, the sparse
  solver's chunking) would escape a `scoring/`-only rule.

Recommendation:
1. **Before the full pilot: a code-fingerprint guard.** The store keeps a sidecar file (not in
   the cell bytes, so byte comparisons of stores are unaffected) with the code fingerprint: the
   git commit, a clean-tree flag and the `uv.lock` sha256. The fingerprint is opaque to
   `sciagent`; the pilot script supplies it. A run on a dirty tree refuses to write. A resume
   whose fingerprint differs from the store's refuses to reuse cells unless the command names
   the old fingerprint explicitly (`--reuse-from <fp>`), and that permission is appended to the
   sidecar. That turns silent reuse into a recorded decision, covers every case above including
   the domain's marks, moves no address, and does not depend on the machine. Engineer, test
   first (the store boundary is an instrument).
2. **With the `p4-deltas` integration, after the pilot's numbers:**
   - the address text is the repr of the object that runs, built by one constructor shared by
     `plan.py` and `execute.py` (B-rand, B-np, ORACLE, B-lib today agree only because defaults
     match);
   - `METRIC_VERSION` and `DATA_VERSION` gain the SHA-256 of a canonical rendering of every value
     constant on a cell's path, kept complete by a fast AST test over `scoring/`, `systems/v2/`,
     `library/`, `investigation/world.py`, `campaign/execute.py` and `plan.py`, with an explicit,
     commented exclusion list for read-time constants (`gonogo.py`, `stats.py:55,57`,
     `behaviour.py:66`, labels);
   - the domain supplies its own derived text for channels and the mark sampler to the
     environment version (`sciagent` still never imports `environments/`);
   - the new policy arms' and telling unit's prose in `p4-deltas` follows the same rules.
   This moves every baseline and score address (p4-deltas itself moves only `HARNESS_VERSION`),
   so `pilot_report.py` at the new code cannot read the Linux pilot store: PILOT.md records the
   pilot's commit, and the report is regenerated only from that commit.
3. **Not adopted:** per-machine golden digests of each system's output (my draft's rule 2).
   They fail off dm-devbox and on any lock bump, cannot tell a code change from a platform
   change, and would not have caught the ORACLE certified-refit change, whose branch never fired
   on dev seed 1. Hashing source files into addresses is rejected too: a comment edit would
   rerun everything.
No number is known to have been reused stale; nobody has checked older stores for it.

## 2026-10-27: F5 guard built on branch `fingerprint-guard` (2998821), not merged (veto window of the F5 entry above)

The F5 point-1 guard as built (engineer, test first, 30 new tests; branch cut from 98e3aa4).
`Campaign(...)` takes a required, opaque `fingerprint` (and optional `reuse_from`); the guard
runs before the store opens, so a refusal writes and launches nothing; `--rescore-llm` goes
through the same path. The sidecar `<root>/fingerprint.jsonl` is append-only: first line
`establish F`, later lines `reuse G from F`, each naming the fingerprint the previous line left;
a broken chain or malformed line refuses and is never rewritten. `scripts/pilot.py` supplies
`commit=<sha>,tree=clean,uv.lock=<sha256>`, refuses a dirty tree (ignored files do not count)
and takes `--reuse-from`. Choices in it, open to the same veto: a store with cells but no
sidecar (the smoke and mini-pilot stores) is refused unless `--reuse-from unrecorded`, which is
recorded as such, because nothing shows which commit wrote those cells and an operator-typed
commit would enter the record as fact; a `--reuse-from` that does not name the store's current
fingerprint refuses even when the fingerprints match (the operator's picture of the store is
wrong); a new store refuses any `--reuse-from`. Cost: any commit, docs-only included, changes
the fingerprint, so resuming from a newer commit needs an explicit, recorded `--reuse-from`.
Cell bytes are unchanged (`compare_stores` on guarded and bare copies). `store_check.py`
prints the sidecar history.

## 2026-10-27: Δ11 EIG weights: keep BIC, report the degeneracy, break full ties from the truth-keyed random draw, compute EIG to relative precision (proposal; veto window ends: 2026-10-28T21:40Z)

Open decision Δ11 (HANDOFF-2026-10-06, decision 2): on dev the BIC posterior is degenerate,
so EIG is about 0 and the arm picks the first menu cell (`burst` read with `mean_rate`). The
theorist's check (`.cache/drafts/d11-draft.md`, scripts in `.cache/drafts/d11-scripts/`; the red
team reran every number): with the truth in the entertained set all 20 dev truths have
H(w) < 1e-13, the tie floor (`policies.py:187` on `origin/p4-deltas`); with the library alone 6
of 20. The draft recommended ranking tied cells by Σ v_k·KL(p_k‖p_top). The red-team review
(session 5; probes in `.cache/drafts/d11-probe/`) rejected that rule: it is the η → 0 limit of
EIG, not EIG at the actual weights (two cells swap order at dev-013's η, and the lowest-EIG
cell ranks first); KL is infinite whenever a rival's run stops in 1 of 16 replicates (the
"undefined" atom, `policies.py:485-498`, `:625-630`), and a constant leader's floored variance
(`:497`) then decides; 1 − w_top is exactly 0 in float past ΔBIC ≈ 73; and it redefines EIG,
which SPEC §2.4 fixes and whose ≈ 0 is the evidence wall's own prediction, so it would be a SPEC
change. Recommendation:
1. Keep the BIC weights. Reject tempering (a temperature has no data-free value; any fixed T
   collapses again as n grows: on dev T for H = 0.1 nats ranges 1.2 to 52) and a ΔBIC cut-off
   (one survivor makes EIG identically 0).
2. Compute EIG to relative precision: weights from BICs in log space, and the leader's term
   without the rounding that makes it 3e-18 against a true 8e-114 (`policies.py:569`; the red
   team confirmed). Otherwise partial-tie orders are noise. Test first (EIG is an instrument).
3. On a fully tied decision (every cell within the floor), take the cell the random arm draws
   from the truth-keyed policy stream (the C5 seeding proposal), so on degenerate decisions the
   EIG arm equals random and their paired contrast is exactly zero; a strict-subset tie draws
   from its own reserved index. This replaces the 2026-10-06 menu-order rule (p4-deltas LOG
   line 1039, `test_policies_eig_ties.py`), whose fixed design would confound C5 with the menu's
   order. The tie rule enters `EIGPolicy.config` and so the run key.
4. Report the degeneracy as a finding: C5 is split, before any run, into degenerate and
   non-degenerate decisions, using what `EIGSummary` already records.
5. Log the KL-ranked pick on every EIG decision, framework-side, as an exploratory diagnostic
   (it reuses the same simulations), never as the arm's choice.
Not checked: how often stops and floored variances occur in real policy simulations (no policy
unit has run; one EIG decision takes about 95 to 99 s). Lands with the p4-deltas integration.

## 2026-10-27: Review items (a) to (e) of HANDOFF-2026-10-06; (c) sets a P3 go/no-go rule before the Linux pilot (proposal; veto window ends: 2026-10-28T21:45Z)

Drafted by the analyst (`.cache/drafts/review-items-draft.md`), reviewed by the red team in
session 5 (probes in `.cache/drafts/review-items-probe/`); the red team's corrections are folded
in. Branch references are to `origin/p4-deltas` (bff61ec) unless they say v2.
- **(a) A run with exactly N events counts as truncated** (`simulate.py:1031-1037`): keep,
  document only. It costs one event-free tail on a run with exactly N = 15,000 events
  (P(Pois(15000) = 15000) = 0.0033); the alternative would make the likelihood depend on
  whether a later event arrives. No P3 code is affected (v2 has no truncation).
- **(b) The runner-up is `rivals[0]` only** (`behaviour.py:112-114, 255-266`; the prompt at
  `prompts.py:480` says "a rival"): reword the prompt to "your runner-up (the first rival)",
  which keeps SPEC §4.4 literal, and add a C6 step that predicts under `rivals[1]`. The reword
  moves the session surface and so every LLM key; bundle it with any other P4 prompt edit so
  keys change once.
- **(c) An ORACLE at −inf drops the truth from control 1** (v2 `campaign/analysis.py:580-600`):
  **change, for P3 and P4: any ORACLE −inf on any (truth, seed) fails C1** ("fix the
  instrument", SPEC §7.1), the same as an `oracle_underfit` flag. Reasons:
  - P4-PROPOSAL §2.4, which the owner approved, says C1 passes only with zero `oracle_underfit`
    flags. The drop rule was set by the orchestrator on 2026-10-06 (LOG line ~808), after the
    fit/4 pilot, and listed for owner review; it was never preregistered by the owner.
  - A drop is not harmless: on the 5-truth table of the analysis tests, one ORACLE −inf turned
    GO into CONTROLS FAIL by shrinking the samples controls 2 and 3 read (planted-hint vs B-rand:
    Wilcoxon p = 0.0625 on 4 of 4 wins). In the other direction it could hide a failing control.
  - The drops are not random. Only identity- and exp-link truths can reach −inf (9 of the 20
    dev truths, 5 of the 10 B-sym@10F truths). On 0 of 28 susceptible units so far, the exact
    95% upper bound is 10.2% per unit, so a tolerance like the draft's 10% sits inside the
    uncertainty.
  - The mechanism is an extrapolation failure, not an underfit: an identity-link fit is held at
    λ ≥ 0 on the training data only (`fit_bounds.py:3-9`), so it can match the true parameters'
    training likelihood (no flag, `systems/v2/systems.py:266-269, 327-330`) and still put λ ≤ 0
    under a held-out event (`likelihood.py:139-142`).
  - The Linux pilot has not run (`.cache/pilot` does not exist), so setting the rule now is not
    a reinterpretation after seeing numbers. "Keep the drop but stop if it fires" (the draft)
    would add owner choice after the numbers, which P4-PROPOSAL §2.4 forbids.
  - A small defect goes with it: a truth dropped for ORACLE −inf is also reported as "dropped as
    not identifiable (ORACLE ≤ B-lib)" (`analysis.py:599`). Fix the label in the same change.
  Engineer, test first (`analysis.py` is the go/no-go instrument), before the full pilot.
- **(d) Δ12 was folded into WP6**: keep, document only. Δ12 itself was approved (decision 17);
  only the folding was the orchestrator's. The one loss is that a P3-to-P4 change in stopping
  cannot be credited to Δ12 alone, and no preregistered contrast makes that comparison.
- **(e) Belief-gate friction** (31 refusals in 40 Δ6 sessions): not adopted for now; keep the
  gate and record the friction. An inline belief at `submit` would lose no measured data (the
  submit is already the final belief; `confidence` is read nowhere), but a refusal forces a
  reflection step that may change stopping and experiment use, which are measured, and the Δ6
  records that could show it are lost. If the owner wants it, it needs: the system prompt's gate
  text changed (`prompts.py:354-355, 490-492`), atomic validation before any state change,
  canonical rivals (`tools.py:1082` does not canonicalise; `behaviour.py:103-104` would raise),
  a C6 step on the inline path, and a `HARNESS_VERSION` bump (the record's meaning changes).

## 2026-10-27: Δ6 prompt variant freeze: `link-neutral` is not adopted as drafted; a link-balanced variant is recommended, pending its own red team (no veto clock yet)

Open decision Δ6 / decision 9. Draft (analyst, `.cache/drafts/d6-draft.md`; statistics
`.cache/drafts/d6-scripts/`) recommended freezing `link-neutral` for P4, since the approved
rule ("adopt whichever variant tries more links and depth", P4-PROPOSAL:256-257) picks it by
count. The red team reproduced the statistics byte for byte (tried a non-identity link 12/20 vs
16/20, pairs 12/4/0/4, McNemar exact p = 0.125, mid-p 0.0625; borderline, not significant under
exact tests), and found:
- the rule was not applied mechanically when the data came in: the session that ran the
  check wrote "Depth is unchanged … Decision 9 (freeze) is the user's" (STATUS-2026-10-05:196-198),
  and depth ≥ 2 is 12 vs 11, depth 3 is 0 vs 0; "tries more" on a 1-in-20 difference is itself a
  reading after the data;
- `link-neutral` is not neutral: its examples are exp, softplus, identity, softplus (p4-deltas
  `prompts.py:206-212`), against the approved text's rotation across the three links
  (P4-PROPOSAL:160). On softplus truths at least 3 of its 5 extra correct links came from sessions
  where v1 also left identity but chose another link, which is what example priming predicts. The
  dev split is 11 of 20 softplus (all 9 far-stratum truths, 7 of 10 out-of-dictionary), so the
  choice bears on C4 and on C1's fairness (B-sym draws links uniformly; SPEC §9 asks equal tuning
  effort);
- no code guard makes P4 arms use the frozen variant (`PILOT_ARMS` are v1; `pilot_report.py:675-676`
  matches only `/named` and `/anon` suffixes and would silently drop `+link-neutral` sessions); the
  "tried a link" read-out code and the 40 records are lost, and commit d6a46d6 no longer exists.
Lane recommendation (not yet red-teamed, so no veto window starts): freeze a third variant whose
examples rotate one per link as the approved text says (the depth-3 example's link stated in the
entry that defines it), on the design argument alone; keep `v1`'s factual fix; add a guard that
every P4 arm carries the frozen variant and fix the report's suffix matching; define and test the
"tried a non-identity link" reader before preregistering link-choice shares as descriptive
measures. The owner may prefer `v1` with the factual fix (equally consistent with the evidence)
or `link-neutral` by the literal count; this is the owner's decision as the STATUS note said.

## 2026-10-27: FSD event cap: the draft's 6,000 is not adopted; a measured choice is needed first (open; no veto clock)

Open decision "FSD cost" (HANDOFF-2026-10-06, decision 3). Draft (engineer,
`.cache/drafts/fsd-cap-draft.md`, measurements `.cache/drafts/fsd-meas/`): the cost is the
Wiener–Hopf reference exploding under strong inject designs (`models.py:329` on `origin/p4-deltas`;
linear in N for stable runs, about quadratic when exploding: 1.65 s at a cap of 3,000, 7.5 to 8.0 s
at 6,000 for one replicate of dev-002 `inject_sign_high`), so a lower `POLICY_MAX_EVENTS`
(`policies.py:173`) bounds it. The red team (session 5; `.cache/drafts/fsd-review-probe/`) agreed
on the cost and that the cap already reaches every key it affects (`FSDPolicy.config`,
`EIGPolicy.config`, `plan.py:555`, `:571`), but found:
- the telling unit's cap defaults to the same constant (`plan.py:245`, `scoring/telling.py:122`),
  so lowering it moves telling too, against the draft's intent;
- stop-as-undefined makes FSD's pick degenerate on a design where one side always stops (He² = 1 on
  every diagnostic, `choose` returns that design with diagnostic 0, the failure `telling.py:14-27`
  documents), and the Wiener–Hopf reference is fitted once, so the degenerate pick repeats at every
  decision for that truth; this pushes C5 towards null and can weaken control 5;
- 6,000 rests on one replicate of 5 truths, Wiener–Hopf side only; one of the "exploding" designs
  (dev-003 `inject_size_high`) is a legitimate 3,074-event run, inject designs reach ~3,000, and the
  battery's own example averaged 9,662 events; the 15,000 comes from `world.py:62-71`, not the
  battery's 8× rule (which gave `BATTERY_MAX_EVENTS = 1_000`);
- truthFSD (control 5) counts stops as undefined while telling drops them, so a cap below 15,000
  could fail control 5 through the cap alone; `fsd_null` raises rather than marking undefined
  (`policies.py:886-889`); `make_policy` ignores `PolicyArmSettings.max_events`.
Next: measure the event-count distribution per design on all 20 dev truths, all 16 replicates, both
sides (GLM and Wiener–Hopf) at the current cap, with `nice` in tmux, then choose the cap from it;
give telling and truthFSD their own constant; decide the at-cap rule for FSD's ranking (for
example, rank by the defined replicates and flag designs whose reference stops) with a positive
control that FSD's picks are unchanged on decisions where nothing explodes.

## 2026-10-27: Choices made while fixing the three pilot-blocking branches (open to the same veto windows)

- `fingerprint-guard` 903937e (F5 window): one driver per store, enforced by an exclusive
  `flock` on `<root>/fingerprint.lock` for the life of a `Campaign`, which drops the two-driver
  mode (`pilot.py` runs baselines and LLM sessions in one process; re-checking the sidecar per
  write would still race); the fingerprint is `commit=<sha>,tree=clean` (`uv.lock` is tracked, so
  the commit covers it); the imported `sciagent` and `environments` must live under the checkout
  that is fingerprinted, and the checkout must be its own git top level; the fingerprint is taken
  after `--wait` and again after the run, and a change exits non-zero; a torn final sidecar line
  is treated as uncommitted and cut before the next append.
- Session keys f1c5b0f (window 2026-10-28T17:00Z): the AG-o text names the `docker run` argv
  (placeholders for name, paths, image and seed), a hash of the `/data` file bytes of a fixed
  sample, and the python tool's caps and output-format hash, each computed from the code that
  acts; float limits render as floats. The image ID is recorded in the AG-o record
  (`sandbox_image_id`) and checked on replay, but kept out of the address (rebuilding a recipe does
  not reproduce its ID). Older records carry no ID and are not checked.
- `c1-oracle-inf` 285485a (window 2026-10-28T21:45Z): a truth with an ORACLE −inf unit stays
  kept (its ORACLE-vs-B-lib check reads its finite seeds), as a flagged-underfit truth is; C1 fails
  naming the units. "Incomplete" still outranks "controls fail" when nothing is checkable yet,
  the order the underfit case already uses.

## 2026-10-28: Δ6 link-balanced variant red-teamed: the owner's explicit choice, not a veto window (decision 9 stays open)

Draft (analyst, `.cache/drafts/d6-balanced-draft.md`, diff and checks in
`.cache/drafts/d6-balanced-scripts/`): `link-balanced` differs from `link-neutral` (p4-deltas
bff61ec) only in its example block: three examples, one per link in the order exp, softplus,
identity, the depth-3 structure on exp (the 10 depth-3 dev truths are the 10 out-of-dictionary
ones, links softplus 7, identity 2, exp 1, so a softplus depth-3 example hints at the dev
majority), v1's factual fix kept. The red team reproduced the diff, the example links and depths
[3, 1, 2], and that every v1 and `link-neutral` render is byte-identical before and after the edit
(80 renders; all 8 pinned `PRE_POLICY_DIGESTS`), so no session key moves. Its verdict: adopt
with fixes, but not by the lapse of a veto window. Decision 9 was approved as rule (A), "adopt
whichever variant tries more links and depth" (P4-PROPOSAL:256-257), and (A) picks `link-neutral`;
choosing `link-balanced` sets that rule aside after the dev check's numbers were seen, partly on a
post-hoc subgroup pattern, so it takes effect only on the owner's explicit choice; if a default is
unavoidable it is (A). Other findings: P3 agents opened with v1's first example verbatim in 103/240
sessions (TRANSCRIPTS.md:45-46), so whichever example is first will be copied (an exp and depth-3
prime under `link-balanced`), and any link-choice share must be reported with and without fits
identical to a prompt example; the guard must also pin each arm's surface digest at launch, thread
the truth's `split` through to the campaign, enumerate every stored llm-session cell (not only
planned keys), and refuse unknown `--arms` names; the "tried a non-identity link" reader must say
that a structure with no features adds no link (depth 0), count only charged fits ("fitted", not
"tried"), and state how refused and harness-fault sessions enter the denominator; its positive
control (reproduce TRANSCRIPTS.md:53-54) cannot run while the P3 store stays on Windows.
Correction to the 2026-10-27 Δ6 entry: "at least 3 of its 5 extra correct links" is at least 2 of
5, the minimum consistent with the STATUS margins (red team, enumeration of all assignments).
Owner's choice, unchanged in substance: (A) `link-neutral` by the literal rule; (B) v1 with the
factual fix; (C) `link-balanced`. The guard, the report's arm matching and the reader are needed
under any of them, and are P4 work after the choice.

## 2026-10-28: CPU time per job goes beside wall time in `timing.jsonl` (branch `cpu-time`; timing only, no cell moves)

Wall times on dm-devbox swing more than 10× with the other lane's load (JOURNAL session 6), so the
full-plan projection needs a measure that load inflates less. Decided: the process that does the
work records `getrusage` deltas around it (pool workers are reused across jobs, so absolute
figures would carry earlier jobs). `cpu_s` is RUSAGE_SELF user+sys, all threads (BLAS and solver
threads included), over the same interval as the unit's `wall_s`; `cpu_children_s` is
RUSAGE_CHILDREN user+sys, which counts only children that exited **and were reaped** inside the
interval (the agent CLI of a session, `docker` clients), not a still-running or unreaped child,
not orphans reaped by init, and not processes inside a container: an AG-o agent's sandboxed code
is in neither figure. A pool job's lines also carry `job_wall_s`, `job_cpu_s`,
`job_cpu_children_s`, `job_units` (data simulation included, shared by the units of a multi-unit
job, so the pool total is the sum of `job_cpu_s / job_units`). Absent figures are `None`, never
0: in-thread sessions (they share the driver's process), hard timeouts, Windows. An isolated
session's `cpu_*` exclude its process spawn and imports, which its `wall_s` includes. These are
timing, not results: they never enter a cell, reading, detail or address, so no version constant
moves; a test pins byte-identical stores across two runs whose CPU figures differ, and a positive
control (copying `job_cpu_s` into a detail) made it fail. CPU time is not load-free either (cache
and SMT sharing inflate it somewhat), so the projection reports both wall and CPU figures and says
which it uses. Stacked on `wall-time-move` because both edit the same timing lines; merges after it.

## 2026-10-28: The baselines' run-time projection uses CPU time, with the wall/CPU ratio beside it

The "Where the baselines run" projection (full P3 plan at 2 workers, the 48-hour line) takes each
unit's cost as `cpu_s + cpu_children_s` from a CPU-timed rerun of the mini-pilot plan
(`.cache/mini-cpu`, code `cpu-time` 21e7947), not the mini-pilot's wall times. Why: on dm-devbox
the wall time of one unit varies with the other lane's load and with usage-brake freezes, not with
the unit. The first CPU-timed units show it: B-rand dev-008 5210 s wall for 2676 s CPU, B-sym
dev-008 4077 s wall for 1072 s CPU.
CPU time does not accrue while a process is frozen or descheduled. It is a lower bound on
uncontended wall time per worker, so the projection reports CPU totals and the measured wall/CPU
ratio of the same units, and states which drives the 48-hour call. Residual bias: all three mini
truths are in the far stratum, so the projection may overstate the near and mid truths.

## 2026-10-28: A test that "nothing launches" blocks the process start and redirects HOME; a PATH strip is not enough

The rescore guard test (`tests/campaign/test_rescore_guard.py`, branch `rescore-guard`) replaces
every layer a live session passes through with a recorder raising a `BaseException` subclass (the
harness has an `except Exception` at its SDK seam, which would turn an attempt into a retryable
`session_failed`), down to `anyio.open_process` (in-thread path) and `SpawnProcess.start`
(isolated path), and points `HOME` at an empty directory. Why not the salvaged script's PATH strip
alone: claude-agent-sdk 0.2.139 runs the CLI bundled in its wheel before it searches `PATH`, then
falls back to `~/.local/bin/claude`, which exists on dm-devbox. No API key is set, not even an
invalid one: the harness refuses the variable before the SDK, so it adds nothing and would stop the
per-layer positive controls early. Each layer has a positive control (the normal plan, with the
outer layers real, is stopped by that layer and no other), and breaking the driver's rescore
refusal made the main test fail (engineer, in its worktree, reverted). Test only; no instrument
changes.

## 2026-10-28: How `scripts/project_runtime.py` turns a CPU-timed store into the 48-hour call

The projection script (branch `projection`, merged 39d088f) takes the plan from the driver's own
planner (`sciagent.campaign.plan.baseline_units`/`llm_units` through `pilot.py`'s parser,
`select` and `campaign_config`), not a hand count: 430 non-LLM units (7 systems x 60 + B-sym@10F
x 10) and 240 LLM units, which it lists as excluded. A unit's cost is its own `cpu_s +
cpu_children_s`. The shared overhead of a multi-unit job (job CPU minus its units' CPU, i.e. data
simulation) is spread equally over its units, in a separate column. A plain `job_cpu_s /
job_units` split was rejected because it gave ORACLE less than its own unit CPU. Units are
scheduled one by one, longest first, onto the workers. The driver's job bundling (ORACLE, B-lib
and B-np as one job) and its `--max-heavy` limit are not modelled; the output says so. A planned
system with no timing in the store is printed as not measured and left out, so every figure is a
lower bound. `--assume SYSTEM=FxOTHER` adds it as a printed assumption, and is refused for a
system the store has measured. Why: the result feeds a stop point (the owner chooses where the
baselines run), so it should understate rather than guess.

## 2026-10-28: Correction: CPU time under load is not a lower bound on uncontended wall time on dm-devbox

Supersedes one sentence of "The baselines' run-time projection uses CPU time" above, which said
CPU time "is a lower bound on uncontended wall time per worker". Found by the session 11 red team
and checked: mini-a and mini-b ran the same units with identical bytes. In 12 of the 21 units,
one of those runs finished in less wall time than mini-cpu's CPU time for the same unit. Examples
are ORACLE dev-017 at 95.4 s wall against 155.6 s CPU, and planted-hint dev-017 at 1495.9 s
against 2015.6 s. dm-devbox (Xeon 8488C) has 2 cores with 2 hardware threads each, so under the
other lane's load a unit accrues more CPU seconds for the same work. Both versions of the 48-hour
figure are kept:
- (a) as reported at the stop point, contended CPU: 53.3 h at the mean;
- (b) each unit costed at the minimum of its CPU and its three wall times (an upper bound on its
  uncontended cost): the sum falls to 0.906 of (a), about 48.3 h.

The call therefore does hinge on contention. The method stays CPU time, with this bias stated.
The script's caveat line is corrected (text only, no figure changes). The figures stand, but the
reading changes: on a quiet machine the non-LLM plan sits at about the 48-hour line, and
B-sym@10F (unmeasured) and the near and mid strata decide which side.

## 2026-10-28: `project_runtime.py` pools stores, costs by stratum and adds a min(CPU, wall) line (merged e474a6b)

The projection's figures stand on a single far-stratum store. Session 11 extends it without
changing the single-store output: apart from the corrected caveat, that output is byte-identical
(a golden test pins it).
- `--store` is repeatable. A unit with CPU figures in several stores is one measurement at the
  mean of its lines, and the output counts them. A unit repeated within one store keeps every
  line, which preserves the old output.
- `--by-stratum` costs each planned unit by its system's mean in its truth's stratum. The table
  names the source of each cost; an unmeasured stratum falls back to the system's all-strata mean,
  named as such. B-sym@10F, once measured, is costed like any other system, and `--assume` stays
  for unmeasured ones.
- `--wall-from` adds "uncontended" lines that cost each unit at min(its CPU, every `wall_s` it
  has in any given store). These are an upper bound on uncontended cost under the hyperthreading
  bias in the correction above. A `one run` column counts units with a wall in one store only,
  whose figure keeps that run's contention.

Why the extension: the two biases found by the red team (far-only truths and load-inflated CPU)
push in the same direction. Each needs its own printed line, so the owner sees both versions
rather than one number.

## 2026-10-28: C5 random-arm seeding adopted (window closed 16:05Z, no veto)

The proposal "C5 random-arm seeding: key every policy stream by truth" (LOG 2026-10-27) is
adopted as written, with the red team's changes folded in. Checked at 16:06:03Z: VETO.md has no
VETO lines. It is implemented with the `p4-deltas` integration (after the Linux pilot), test
first, since seeds are an instrument. No policy unit has run, so no stored result changes.

## 2026-10-28: The projection costs by link through the same grouping as by stratum; exp-as-softplus is a printed assumption, not the default

`scripts/project_runtime.py` groups unit costs by stratum (`--by-stratum`) or by link
(`--by-link`) through one `Grouping`, with the link read from `read_split`'s records. An
unmeasured (system, link) falls back to that system's mean over all links, as `--by-stratum`
does. Costing the unmeasured exp link as softplus (the red team's hand choice, "both use nodes")
takes a separate `--assume-link exp=softplus`. The output prints that as an assumption, and the
flag is refused for a link that has been measured. On mini-cpu and strata with the mini-a and mini-b
walls: plain `--by-link` gives 43.4 h CPU and 40.3 h uncontended; with the assumption, 44.7 h and
41.5 h, the red team's lower ends (merged 5aff197; checked in lane session 12). Their upper ends
(49.5 and 46.3 h) come from B-sym@10F far guesses, not from link, and no costing flag reproduces
them. Why: the fallback should understate rather than guess, so a reader can see that
exp-as-softplus was assumed.

## 2026-10-28: Δ11 EIG weights and review items (a) to (e) adopted (windows closed 21:40Z and 21:45Z, no veto)

Checked at 21:49Z: VETO.md has no VETO lines. Δ11 (keep BIC, EIG to relative precision, full ties
from the truth-keyed random draw, degeneracy reported, KL pick logged as a diagnostic) is adopted
as written and lands with the `p4-deltas` integration, test first. Review items (a), (d) and (e)
are adopted as "keep, document only" or "not adopted for now"; (b)'s reword waits for the next P4
prompt edit so LLM keys move once. (c) is in force for P3 and P4: any ORACLE −inf on any (truth,
seed) fails control 1, merged into v2 with its label fix at 21:49Z (as bed7454 and 1c8160a, from `rehearsal2`
eb48450 and abb4059). The Linux pilot has not run, so no stored verdict changes.

## 2026-10-28: The FSD event-cap measurement: B-lib's pick stands in for the GLM side, at seed 1 and decision 0

The measurement the FSD cap needs (LOG 2026-10-27) is prepared, not run: `.cache/drafts/fsd-dist/measure_dist.py`
on a worktree of `origin/p4-deltas` bff61ec, one line per (truth, design, side, replicate). The WH side is
exactly FSD's reference stream. The GLM side uses B-lib's pick on truthFSD's parametric stream, because
FSD's real parametric side (B-sym's first-epoch top fit, refitted at each decision) would mean running
B-sym. Fixed at seed 1 and decision 0: WH is fitted once, so only its stream moves across decisions.
The smoke (dev-000 and dev-002, 2 replicates) found the predicted degenerate cell: the WH side of dev-002
`inject_sign_high` stops at 15,000 on both replicates (about 43 CPU s each), and the GLM side completes
(about 1,850 events). B-lib's mark-blind `hawkes` pick makes the GLM side identical on all four inject
designs. Also found: `investigation/policy.py:make_policy` builds `FSDPolicy` with the default cap, so a
cap set through `PolicyArmSettings` alone would not reach AG-c-S. Projected full run: 2 to 3.5 CPU h.

## 2026-10-28: Readers open the store read-only; cells hold across hash seeds (hunt leftover (e))

Every reader opened the store read-write (`ResultStore(root)`), which runs the ledger's
`CREATE ... IF NOT EXISTS` schema and makes the directory. On an intact store the bytes did not
change, but on a ledger that lost an append-only trigger `pilot_report.py`, `store_check`
(`audit.check_store`) and `session_check.py` were measured putting it back, so the audit reported the
tamper once and then passed; a report on a mistyped `--root` would have created an empty store.
Decided: a reader opens with `ResultStore(root, read_only=True)` (sqlite `mode=ro` through
`backing.connect_read_only`, no schema statement, writing methods refused before any file is
touched; a missing store raises), used by `pilot_report.py`, `session_check.py`, `hunt_gap.py`,
`check_store` and `check_budgets`. The driver keeps the read-write open. Read path only: no content
address, FIT_VERSION or METRIC_VERSION moves. Tests: `tests/campaign/test_store_readers.py`
(snapshot of every file under the root before and after each reader; control: the snapshot sees a
timing line and the read-write repair). The hash-seed pair (`test_hash_seed_pair.py`) runs B-lib on
`hawkes` seed 1 through the driver under `PYTHONHASHSEED` 0, 1 and random: cells byte-identical by
`compare_stores`, passed at once. Red team (session 14): merge with one fix, made test first:
`check_store` raised `FileNotFoundError` on a store whose `details/` was deleted (the read-write open
had recreated it); it now reports `missing_details_dir`. Known and accepted: after a driver crash
leaves a hot rollback journal, a `mode=ro` reader cannot roll it back and raises `RegistryError`
("attempt to write a readonly database") until the driver reopens the store; it never reads wrong
data. Not fixed here: `audit._ro` raises untyped sqlite errors and never closes its connection.

## 2026-10-29: FSD at-stop rule: the drop-rule draft is withdrawn (the world truncates at the cap); the cap stays 15,000; open

Measurement finished (LOG 2026-10-28; `.cache/drafts/fsd-dist/`, 5,468 lines, seed 1, decision 0, cap
15,000): WH and GLM sides on 16 replicates, truth side on replicates 0-1 (the full truth side was stopped
after one replicate took 88.4 CPU s to reach the cap). The same 6 WH cells (all `inject_*`) stop on every
replicate at every cap from 6,000 to 15,000; GLM (B-lib's pick) 0 stops in 2,560 runs; truth vs GLM is
one-side-always-stops on 12 cells of 10 truths. Under the undefined-atom rule such a cell has He² = 1 on
every diagnostic and `choose` takes it with diagnostic 0. **Draft (session 15, not adopted):** FSD and
truthFSD adopt telling's rule (drop stopped replicates), because "a stopped world run returns None";
keep 15,000; one cap path. **Red team (session 15): do not adopt.** The premise is false: the B-sym
policy arms and the agent lab run `World(..., truncate=True)` (`campaign/execute.py:436`,
`investigation/runner.py:269`), so a run reaching the cap is truncated at τ and kept; only runaway and
candidate-limit guards return None (`world.py` class docstring; `investigation/policy.py:44-53` names the
mismatch). On the 5 cells where WH and the truth both stop, the world returns a 15,000-event dataset
against a fit predicting about 2,000: the falsification FSD is built to find, which the drop rule would
score 0. Also: with 1 of 16 reference replicates kept the variance floor gives He² near 1 again (a cliff,
not a fix); B-lib's pick is mark-blind `hawkes` or `regime_switching` on 17 of 20 truths, so "GLM never
stops" says little about B-sym's fits; at 15,000 the two B-sym arms' policy simulation is about 76 CPU h on
the dev plan (60 x (1,614 + 2,957) s, decision 0 costed 8 times, a floor for FSD). Decided: the cap stays
15,000 (= the world's cap; a lower cap would select towards stable runs on near-critical cells such as
dev-006 `inject_size_high`, 5 of 16 WH runs stop at 15,000, 13 at 6,000). Open: the at-stop rule. The
option to draft next is that policy simulations truncate at the cap as the world does and read each
diagnostic on [0, τ] (no all-diagnostics cliff, no policy/world mismatch); the WH filter has no truncate
mode (`systems/v2/models.py:358` raises), so it is an instrument change and needs per-diagnostic
measurements first. Found for the p4-deltas integration: `investigation/policy.py:make_policy` builds both
`EIGPolicy` and `FSDPolicy` from `seed` and `replicates` only, so neither the arm settings' cap nor
EIG's `weighting` reaches AG-c-S (fix and test with the integration).

## 2026-10-29: No veto windows; one red-team round per item (owner, INBOX 09:12Z)

The owner waived the 24-hour veto windows for both projects from today, and asked for less paperwork per item.
Proposals and preregistrations are still written, red-teamed and frozen, then go ahead at once inside the money
rules, with a short notice to the control room; VETO.md stays as the way to stop or reverse one afterwards.
One red-team round before a freeze or an owner-facing result, a second only if the first found something
blocking; items good enough to run are not refined further. Why: the windows and repeated review rounds were
spending the shared Max usage and wall time without changing outcomes (six rounds on the FSD control criteria,
JOURNAL session 17). Not changed: stop points (SPEC changes, the P3 go/no-go, spend, AUTOPILOT §stop points)
and choices the owner must make explicitly (Δ6 A/B/C) still wait for the owner. First use: the FSD control
criteria v8 and `controls.py` get a single joint review, then the freeze and calibration.

## 2026-10-29: Runs with no LLM calls start in their own user unit, outside the usage brake (control room)

Decided by the control room (the brake is its infrastructure, so not an owner question; QUEUE "Pilot runs and
the usage brake"). Runs that make no LLM calls (baselines, `--no-llm` pilots, FSD controls, measurements) start
in a transient user unit, `XDG_RUNTIME_DIR=/run/user/1000 systemd-run --user --unit=sci-<run> ...`, so the brake,
which freezes only `research-lane@sciagent`, never stalls them or inflates their wall times. Runs that call the
LLM stay inside the lane's unit as now, so the brake still covers the Max usage they spend. Wall times from units
that overlap an interval in `~/logs/usage/brake.log` stay flagged as before. Consequence for the full pilot:
`scripts/pilot.py` as planned mixes LLM and baseline units in one process, so either it runs whole in the lane
unit (baselines braked with it), or it is split into a `--no-llm` baseline run in its own unit and an LLM-arm run
in the lane unit (whether two `pilot.py` processes may share one store root is not checked yet); the
pilot-runner chooses when the pilot is unblocked and records it.
The usual rules still apply inside the unit: `nice -n 10`, at most 2 pool workers, tmux for the console.

## 2026-10-29: FSD at-stop controls, criteria v8: calibrate pushes its D0 before anyone can read it; the gate needs branch protection

Red team s18 found v7's freeze chain gameable without a force-push: a re-freeze merged into v2, or a LEDGER row
deleted, let fresh test data pass as preregistered (`.cache/drafts/fsd-controls/redteam-s18.md`, scratch
scenarios S2, S3). v8 (theorist) decides: the freezes are every non-merge commit on origin/v2 that ever changed
`controls.py`, each named by a LEDGER row ever added (read from the `+` lines of the file's history, so a
deleted row changes nothing); a control has earlier test data iff some commit touching its `D0-<C>.json` does
not descend from the current freeze; every phase fetches the explicit URL into a private ref under a lock and
refuses on any failure. Calibrate keeps D0 in memory, builds the D commit itself in a temporary object store and
pushes it before writing D0 anywhere, so every D0 anyone has seen is on origin. Chosen over encrypting D0 (the key
stays on this machine) and over counting every calibrate start (an honest crash would then be mislabelled), at
the price of losing a 1-1.6 CPU h calibrate that crashes before its push. The rule assumes v2 is never rewritten,
so v8 checks GitHub branch protection (H1) and the force-push and deletion activity (H2) in every phase; the owner
must switch protection on. Found by the engineer: `python -I` ignores `PYTHONDONTWRITEBYTECODE` and
`PYTHONPYCACHEPREFIX`, so `controls.py` sets `sys.dont_write_bytecode` and `sys.pycache_prefix` itself and the gate
requires `-I`. One joint review of v8 and the code (s18b): ready to freeze; its non-blocking fixes are in v8.1
(963d1495…), with no rule, threshold or seed changed.

## 2026-10-29: The owner's four answers (baselines on dm-devbox, Δ6 (A), SPEC §4.2 option 2, v2 protected); at-stop controls frozen

The owner answered all four open questions as the lane and the control room suggested ("Do all as suggested,
please do your experiments"; INBOX 11:27Z). (1) **The P3 baselines run on dm-devbox**, not AWS, in their own
user unit outside the usage brake. Started 11:31:27Z: `pilot.py --truths 20 --seeds 1,2,3 --b10f-truths 10
--no-llm` (plan: 310 pool jobs) at v2 900e66a in the detached worktree `pilot`, unit `sci-pilot-baselines`, store
`.cache/pilot`, with 1 worker, because at launch 8.7 GiB was available with both calibrates and a test run going,
and a second worker (about 2 GB at peak) would breach AUTOPILOT's 6 GB floor. The LLM half follows with
`--no-baselines` from the same commit. `pilot_report.py` reads one `--root` and a store takes one driver, so the
two halves run in turn on this store unless the report learns to read two stores (open). (2) **Δ6: (A)
`link-neutral`** is the P4 prompt variant, by the owner's pre-set rule (p4-deltas `investigation/prompts.py`). It
lands with the p4-deltas integration; the guard, arm matching and reader listed on 2026-10-28 stay required.
(3) **SPEC §4.2, option 2**: the telling diagnostic is chosen among designs on which neither the truth nor the
best member explodes (K5: at least one truncated or stopped run among telling's replicates on either side; one
shared exclusion, also used by truthFSD), and the explosion share is reported as its own number. The SPEC text
is amended now. As recommended, the code change to `Telling` (an instrument, tests first) waits for controls A1
and B to pass under the rule, and a failure returns it to the owner. (4) **Branch protection** on v2 was switched
on by the control room at 11:27Z (H1 reads `[false,false,true]`). The at-stop controls were then frozen with no
window: F₀ 8405509 (criteria v8.1 963d1495…, controls.py c0895289…) and its LEDGER row (900e66a, ceiling 7.6 CPU h,
$0) were pushed plainly to the protected branch. Calibrate A1 and B started 11:29Z in the units
`sci-fsdctl-A1-calibrate` and `sci-fsdctl-B-calibrate`; both passed the gate.

## 2026-10-29: The P3 pilot runs as two drivers on two stores from one commit, merged into a third store for the report

Decided so that the LLM half need not wait 2-4 days for the non-LLM half. One store takes one driver, and the
brake rule puts LLM runs in the lane unit and non-LLM runs in their own unit. No unit reads another unit's cell
(engineer, from the code: `_plan_baselines` and `_plan_llm` look up only their own addresses; `_launch_llm` gets
no store; `run_score_job` simulates its own data; `session_void` reads only its own record). The halves meet only
when the report reads ORACLE and B-lib beside the LLM gaps. So both halves run at v2 900e66a: `--no-llm` into
`.cache/pilot` (unit `sci-pilot-baselines`, 11:31Z) and `--no-baselines --llm-concurrency 1 --workers 1` into
`.cache/pilot-llm` (tmux `-L sciagent` `sci-pilot-llm`, inside the lane unit, 12:09Z; plan 240 LLM units). For
the report, `scripts/store_merge.py` (local branch `pilot-multiroot` 6b970b6, written test first, not yet merged
into v2) copies both stores into a new one. It refuses unless every source has exactly one established
fingerprint and they are equal, the cells are disjoint, there is one truth digest per id, and every detail is in
canonical bytes. The report and every analyst check then run unchanged on one root. This was chosen over teaching
five readers several roots: no instrument code is edited, and the merge only copies bytes. Its positive control:
a baselines-only store and an LLM-only store, merged, equal one driver's store cell for cell (9 tests; the lead
reran them: 9 passed). Cost: wall times reflect the two halves running together (cells and CPU times do not),
and `replay_check` must run on `.cache/pilot-llm` itself to cover running or void sessions. To be red-teamed
with the report, before PILOT.md.

## 2026-10-29: Harness/1 records are replayed at the pilot's commit 900e66a, not under the merged harness/2 code

Rehearsing the p4-deltas merge (local branch `p4-rehearsal`; engineer, lane session 20) on a 13:37Z copy of
`.cache/pilot-llm` (10 sessions):
- **What works under the merged code.** Keys, audit and rescoring carry over:
  - the merged code re-derives all 17 cell addresses (`match 17 differ 0`);
  - harness/1 session texts are byte-identical to 900e66a's for the four default arms, AG-o's sandbox block
    included;
  - `store_check` and `session_check` pass, and 4 rescored cells are identical.
- **What does not.** `replay_check` passes 0 of 10 under the merged code; every record stops at step −1,
  field `tools`. harness/2 changes the tool set by design: a `belief` tool, `stop_reason` on `submit`, and new
  `run_experiment` and `predict` schemas.
- **Decided.** Pilot (harness/1) records are replayed with the code at 900e66a, from a worktree or archive of that
  commit, as p4's LOG already did with fit/4 (`p3-fit4`). The merge does not keep a frozen harness/1 tool layer.
  - The lane does not push tags (AUTOPILOT), so the full sha is the reference.
  - Follow-up with the integration, test first: `replay_check` refuses a record whose harness version differs
    from the code's, with a message naming the commit to use.
  - Why: a legacy tool layer inside harness/2 code is more than 5 files of instrument code kept alive only for
    replay, and the pilot's commit already replays them (5 of 5 AG-c).
- **Also found.** One AG-o record (dev-003 s1, `50037735…`) fails replay under its own code at step 51 (`digest`).
  The agent's sandbox code prints `time.time()-t0`, and `sandbox/runner.py` documents agent nondeterminism as the
  agent's own. The pilot report counts such AG-o records apart from replay defects, rather than as harness
  failures. This rests on reading the recorded code; the step-51 replay was not run to isolate it.
- **Pinned.** `tests/campaign/test_pilot_harness1_cells.py` (0de8220 on `p4-rehearsal`) pins the four 900e66a
  digests and three real cell addresses. It caught a sandbox-block change in 4 of 7 tests. The older AG-o test
  compared the legacy text against the live sandbox text, so it could not catch one.

## 2026-10-29: `replay_check` refuses a record of another harness (on `p4-rehearsal`, 346676a)

Decided in session 21: under the merged P4 code (harness/2), `scripts/replay_check.py` reports a record whose
config `harness_version` differs from the running `HARNESS_VERSION` as REFUSED, naming both versions and where
to replay it (harness/1 at 900e66a, per the entry above), counts it apart from PASS and FAIL, and exits 3 when
nothing failed; any failure still exits 1. A record that names no harness version is a FAIL, not a refusal:
the live loop has written the field since P2 (558d600), so such a record comes from no harness this project
ran, and treating it as REFUSED would let a record leave the failure count by losing a field. Why: without
this, the pilot's harness/1 records read as 0 of 10 replay failures under the merged code, which looks like a
harness defect but is by design. The new errors sit in `sciagent.harness.errors` beside `RecordError`
(that module's own rule), still under `SciAgentError`.

## 2026-10-29: `store_merge.py` merged into v2 with the red team's three fixes

The store merge for the pilot's two stores (LOG above, "two drivers on two stores") had one red-team round in
session 22: **merge with fixes**. Before merging, three fixes were made test first (9d452fa):
- the merged store is built in a sibling `.<name>.merging-<pid>` directory and renamed into place only when
  complete, so a merge killed partway leaves no store that passes `check_store`;
- a detail whose reading differs from its source ledger row is refused, using the comparison `check_store` itself
  uses (`audit.detail_reading_is_ledgers`), because the merged ledger is rebuilt from the details;
- only the timing lines of copied cells are kept, so a cell committed after the ledger snapshot adds no line to
  the report's wall-time n or pool CPU. A timing line that is not JSON is refused.

Kept as known, with no code change:
- The test called the positive control uses fake jobs, so the claim that real cells do not depend on which half
  ran them rests on reading the code (`_plan_baselines` and `_plan_llm` look up only their own addresses).
- The merged store records no provenance, so `replay_check` runs on `.cache/pilot-llm` itself.

The branch's first commit carried a `Co-Authored-By` trailer, so both commits were replayed without trailers
(40d5d0c, 9d452fa; the tree equals d72ba3c) before the merge.

## 2026-10-29: FSD at-stop controls A1 and B scored under F₀ 8405509: A1 fails under every rule, so SPEC §4.2 option 2's `Telling` change does not proceed and returns to the owner

Score ran at 18:56-18:57Z (both exit 0, determinism rerun OK, no P1 breaches; score files 7386536; S3
`sciagent/fsd-controls/8405509/`; 33,093.5 CPU s, under the 12.0 h ceiling). Read under criteria §8, as frozen;
one red-team round: **read stands**, counts reproduced from the per-seed records and, for a sample, from the
raw runs. **A1** (tested rung 2, θ₊ = 0.6) fails under every rule. The four exclusion rules (K3a, K3h, K4, K5)
give identical results: (i) 12/15 (at the floor), (ii) **78/120 against 84**, (iii) p = 0.0352 (pass); K0 fails
(ii) 0/120 and (iii); K1 and K2z fail (i) 0/15. D0's calibrated pass_ii at that rung was 0.99999775 and pass_iii
0.99988 (Fix 4 requires reporting them): the calibration did not transfer to the test streams (expected (ii)
about 110 under D0's own counts). By §8, "no candidate is ready; He² resolution comes first, and the thresholds
are not changed"; Fix 4 rules out a rerun or extension. **B** (tested rung 3, the size rung; the three θ₋ rungs
were unbuildable, so the dev-005 shape itself was not tested) came out as predicted: Fx passes with K0, K3a, K3h,
K4 and K5 (B1 120/120, B3 15/15, p = 9.8e-4, h_F = 0.952); Fa, Fc and Fz fail B1 (0/120); Fa|K1 and Fc|K1 fail
B2; Fx|K1 and Fx|K2z fail B3. By §8, FSD needs its own exclusion and K1 needs design-aware matching; per §2 this
holds only for truths whose best design is not the artefact design, and per §3 nothing here favours option 2
over option 1. Refused as post hoc: counting telling's rate_after_mark_slope[sign] picks (seeds 102, 114, 115;
in rung 0's D*₀, not rung 2's). Unexplained and not acted on: the red team noted that calibration fixes the
diagnostic menu's arguments on seed 0 while test fixes them per seed (controls.py:1363, 1396 against 1534,
1913), a possible cause of the transfer gap that nobody has tested. Any rerun under a later freeze is exploratory (§5).

## 2026-10-29: After A1's failure: today's telling rule stays; option 2 is parked; the seed lead is tested first (delegated decision)

Decided by the control room at 19:10Z under the owner's standing delegation ("You can do what is best", "Do
all as suggested"; INBOX 2026-10-29 19:10Z), as the lane recommended. The pilot and the next experiment keep
today's `Telling` rule. SPEC §4.2 option 2 is parked, not dropped. The red team's lead (calibrate fixes the
diagnostic arguments on seed 0, test fixes them per seed) is tested first by §8's route, as exploratory work. If it
explains A1's (ii) gap, the lane comes back with a new freeze, preregistered and red-teamed as before. Nothing in
F₀'s criteria or thresholds changes, and A1 is not rescored. The SPEC §4.2 text amended this morning for option
2 stays as written until the owner or the control room says otherwise. Whether to mark it as parked is noted for
the control room, not edited by the lane, because a SPEC change is a stop point.

## 2026-10-29: A session with no SDK `result` message has turns and tokens "not recorded", not 0

A session that ends on `wall_time` gets no SDK `result` message. `campaign/llm.py` then stores turns and every
token count as 0 (`num_turns or 0`, `_usage(None)`). In the pilot, three AG-o-S/named sessions (dev-005,
dev-008 and dev-012, seed 1) hold 42, 83 and 83 assistant entries but read 0. Decided: `pilot_report.py`'s cost
table takes turns and token medians over the sessions whose stored record has a `result` entry. This is decided
from the record, not from the outcome name or a zero value. It prints `(n/m recorded)` the way missing wall
times are shown, with a note only when that happens (58d1ac2, test first). A store where every session has a
result renders byte-identically. Why: P4 sizing and the report's cost row would otherwise count missing counts
as zero, and they read low (AG-o median turns 19 against 26 on the interim store). No version constant moves.
The cell readings are not changed now, because the pilot's code is frozen at 900e66a. The root fix in `llm.py`
(store the counts as missing, or count them from the record) goes with the p4-deltas integration, test first.

## 2026-10-29: The seed lead is closed; A1's calibration was optimistic by construction (exploratory, session 24)

The seed-0-menu lead does not explain A1's (ii) gap. On a fresh pool it moves E[(ii)] by about 0.2, bounded at
about 3 points (red team). The theorist's offline checks find calibrate and test identical in streams, rules
and exclusions, and attribute D0's pass_ii of 0.99999775 to two things. First, a plug-in estimate from one fixed
128-run pool, whose E[(ii)] has an across-pool sd of about 10 to 14. Second, testing only the first rung whose
plug-in passes. Decided: option 2 stays parked, and no new freeze is proposed until its calibration carries pool
uncertainty. The lane does not regenerate the real control streams outside controls.py's stream-id guard
(check (b)) without the owner's or control room's word. Why: these findings are exploratory under F₀ §5; the
guard exists so that control streams are drawn only by the preregistered path. A non-control check (a) is
running to measure the law directly (`.cache/drafts/fsd-seedlead/pool/`).

## 2026-11-04: Check (a) corrects session 24's reading of A1's calibration; "optimistic by construction" and "lucky pool" are withdrawn as facts (exploratory)

The red team reviewed check (a) (session 26; review scripts in `.cache/drafts/fsd-seedlead/pool/review-s26/`, runs sha
e045f23c, `poollaw_analyse.py` 79ab70dd, `poollaw_sim.py` a5ff2b70): it stands with corrections, and no code defect was
found in it. This entry corrects the entry of 2026-10-29 ("The seed lead is closed"), which is left as written. Its across-pool
sd of 10 to 14 came from pools giving p32 and p16 the same 128 runs; calibrate's sets are disjoint, and that structure
gives about 9. Recorded, under F₀ §5 as exploratory: at the seed-0 menu, on 28,672 complete runs, E[(ii)] is about 90
(jackknife 95% CI 83 to 97), p32(MG) 0.935, p16(MG) 0.794, and plug-in pass_ii 0.85 against D0's 0.99999775. Conditioning on
a pass (115 of 400 pools) raises the mean to 99.0, so selection explains about 9 of the 19.7-point gap. None of 400 pools
reached 109.64 (maximum 108.5): P < 0.75% for this pool, and roughly 0.2% to 0.5% allowing for pool and law uncertainty
(order of magnitude; approximations, and any tail reading is post hoc because no tail threshold was set first). The 38%
of session 24 and the 0 of 400 measure different events and do not conflict. Leads only, not results: that D0's pool
was lucky, that calibrate has no defect, that A1's failure was mostly chance, why the test drew low, the CRN effect size,
rung multiplicity, and any design for a calibration fix. Nothing has reproduced a D0 count from runs. Only check (b) can,
so the lane now recommends it to the owner, as the missing positive control rather than because a defect looks likely
(scope and result table in JOURNAL session 26). A1 stays failed as scored; §8's reading and the parking of option 2 are unchanged.

## 2026-11-04: Readings taken from lab state after a wall-time cut are not the record's; the pilot's cell stays, and the record is what counts

`fits_used` of pilot session bd3dc4c4 (AG-o-S/named dev-012 s1, `wall_time`) reads 35; its hash-chained record charges 34.
The timeout cancels the await and not the worker thread, `asyncio.run` then joins the executor for up to 300 s
(`THREAD_JOIN_TIMEOUT`, checked), and `runner.py` reads the lab afterwards, so a tool call that finished in that window
reaches the reading with no `tool_call` entry. Only one of the two unanswered fits reached the lab (the reading would
otherwise be 36). Analyst, all 240 sessions against their records: this is the only disagreement, the three `wall_time`
sessions have no submission, and no score moves; the other two match by timing, not because the read is safe. Decided: the store stays as
it is (append-only), `STORE INCONSISTENT` on this one issue is explained rather than cleared, and the audit is not relaxed. PILOT.md
states it, and 34 is the true count. Root fix goes on `p4-rehearsal` with the p4-deltas integration: a session's numbers are what the record says.

## 2026-11-04: T2's example list is not fixed, and fit/5's search does lose: the "0 misses" of D3 was a statement about one sample (exploratory)

The slow tier on `p4-rehearsal` failed `test_fit_beats_random_grid_points` on (0, softplus, 1074), 0.00397 nats. It is no merge
regression: the gap is identical on v2, and `src/sciagent/glm` is unchanged since 900e66a. The test's list drifts instead: with
the same test source, lists from different scripts, pytest and the two trees differ by 3 to 73 of 300 examples, cause not
established (Hypothesis's local constants fit, but no context tried reproduced 1074). A seeded list of 400 cases, of which 376 ran
before the ceiling (`.cache/drafts/t2-drift/sweep.jsonl`), found 1 loss above the test's tolerance, 1.3249 nats at (0, softplus,
103648458), reproduced by hand: fit -247.1402, generating point -245.8153, both certified, 204 events. That is 1 of 376
(Clopper-Pearson 95% 0.007% to 1.5%). Both known losses are BIG[0] (two gated features, 14,400 grid points) under softplus, with
the fit at period 5 where the generating point has 100: a lead from two cases. The grid point that wins is the generating ψ, so
each loss is a search miss on a θ-refitted point. No decision yet: fit/5 is the frozen instrument of the pilot and a fit/6 is
SPEC-level, so this goes to the owner. The test is neither loosened nor edited until then, so it passes or fails with its sample.

## 2026-11-04: Red team on PILOT.md: two statements of today's LOG corrected; the search miss is in the pilot's own store

Commit with fixes: every headline number recomputed, four blocking text faults fixed before the commit (a clause that went past a computed output, an LLM-arm
comparison no control supports, "rules applied as written" for C3, which reads a declared subset, and T2's absence). Two corrections to the entries above, which stay as
written. (1) "Both known losses are BIG[0] under softplus" is incomplete: ORACLE dev-015 s3 in the pilot store lost 4.53 nats (search -781.9771, truth-ψ -777.4489, read
from the cell; 1,902 events, 0.0024 nats per event) on an `exp` truth with no `Periodic`. Control 1's truth-ψ check rescued it as designed, but only ORACLE has that rescue.
(2) "Only one of the two unanswered fits reached the lab" is an inference from the join, not an observation: the layer lock serialises concurrent calls too, so
order equality does not show one-at-a-time dispatch. Under a stress test that adds T2's 0.0065 nats per event to every unit, no §7.1 outcome moves; that is a stress test,
not an estimate of the rate or size of misses. Not changed by this: no §7.1 number, and no rule.

## 2026-11-04: A session's reading follows the layer's commits; the layer closes when the stream ends

`store_check` flagged pilot session bd3dc4c4 (AG-o-S/named dev-012 s1, `wall_time`): `fits_used` 35 against 34
charged. The wall time cancels the await, not the worker thread. `asyncio.run` then joins the executor for up to
300 s, and only then does `runner.run` read the lab, so a fit ending in that window was counted with no `tool_call`
entry (that session's process ended 267 s after its record). Decided: `ToolLayer.close()` ends the log when the
stream ends, before the outcome is classified. It does not wait for the running handler, whose output is dropped
uncharged; a queued call runs nothing; a short gate shared by commit and close makes each call wholly logged or not
at all. Closing alone is not enough, because a handler changes the lab before it commits (`World.run` moves
`experiments_run` long before the cut can arrive), so `Lab`'s read-outs are now a `Committed` value taken at each
commit (`on_commit`), never the live state. Chosen over deriving each field from the record: stopped experiments'
prediction outcomes and the fault messages are not in the record, and adding them would change record bytes; a
snapshot taken at the commit also leaves a non-wall-time reading identical by construction. `audit.py` stays the
independent check of cell against record. No version moves: the record's meaning, `FIT_VERSION`, `METRIC_VERSION`
and every address are unchanged, and the pilot's stored cells stay as they are, bd3dc4c4's 35 included.

## 2026-11-04: A session that has answered gets 10 s to exit; a sandbox with a call still in it is left to the owner

Two more consequences of the same thread. `_run_isolated` joined the process with no limit after receiving its
outcome, and interpreter exit joins executor threads, so a leftover fit held the unit's slot past the hard deadline:
pilot dev-005 s1 and dev-008 s1 ended their records at 3,606 s and 3,610 s and their units at 5,061 s and 4,645 s.
Each had a fit in flight, and fits outlasting the 300 s join would explain why their `fits_used` is right and only
dev-012's is not (inferred from the times; no log shows the threads). The parent now
gives an answered process `EXIT_GRACE_S` (10 s) and kills its group; the outcome and record are already in hand, and
`_release_session_root` recovers what is left. Cleanup could also race a leftover `run_experiment` writing under the
run directory, and a failed `rmtree` voids the run over a call the record does not hold, so `runner.run` skips
`_discard_sandbox` while a handler is running and leaves the directory to the session root's recovery (outside a
campaign, `scripts/investigate.py` leaves it behind with a warning). The up-to-300 s join in `asyncio.run` stays: it
is what lets an in-flight `python` call end cleanly, at the price of that much CPU after the cut.

## 2026-11-04: A session with no SDK `result` has no turns or tokens in its reading

Queued from session 24. `campaign/llm.py` stored 0 for turns and the four token counts when the record has no
`result` entry (a wall-time end). They are now left out of the reading; every other field is unchanged, and a
session with a `result` keeps the same keys and bytes. Missing, not counted from the record: assistant entries are
per content block and carry the usage of a partly streamed message (dev-012's last turn shows 5 output tokens on all
three entries), so a count would be another measurement under the same name, mixed into the same medians. The only
readers are the driver's progress line (prints `None`) and `pilot_report.py`. Old cells keep their 0s; v2's
`counts_recorded` (58d1ac2, not on this branch yet) decides from the record, so it reads both forms the same, and
until that merges this branch's report still takes a missing field as 0, as it took the stored 0.

## 2026-11-04: Merging v2 into `p4-rehearsal`: `cost_table` takes the `Table`; `counted()` and the left-out fields are one rule

v2 moved the LLM cost table into `cost_table(root, systems, sessions)` while p4 had changed the same rows to
`analysis.prediction_tally(tab, s)`. `cost_table` now takes the `Table`, because `behaviour_sessions` stays the one
place the policy-arm rule is applied. `counted()` and 3e191dd agree and do not count twice: both key on the record's
`result` entries, so a cell with 0s and a cell with the fields left out render byte-identically, and each session
counts once in `(n/m recorded)` and in the note (`tests/campaign/test_pilot_report_counts.py`, also on real
`run_session` output). This closes the last sentence of the entry above: the report no longer takes a missing field
as 0.

## 2026-11-04: The 19 `run_experiment` inputs that failed were the agents' own; no harness defect (exploratory, session 27)

All 19 are hand-typed `force_events` interventions, and all 19 were followed by a `run_experiment` that reached the layer, so none cost a
session its experiment. Of the 14 "could not be parsed as JSON", 9 have one surplus closing brace as their last character (`json.loads`: "Extra data" at
length-1). The other 5 are over 2,048 bytes and the raw input is recorded only to 2,048, so their fault is not visible. The 5 "Input validation error" are the tool's schema
refusing what it should: `horizon` inside `intervention` (3), a stray `times_note` (1), a list too long (1). Those 5 are all anon-arm sessions; with counts this small and no control
covering an LLM arm, that is an observation and nothing more. Nothing changes in the harness. Scripts: `.cache/drafts/unmatched_s27.py`, `unmatched2_s27.py`.

## 2026-11-04: The search-miss stress test rerun at the largest loss found: no §7.1 outcome moves (exploratory, session 27)

The focused sweep ended at 115 of 400 cases with 3 losses: 0.27, 0.59 and 2.08 nats, on 203, 194 and 183 events; 0.0114 nats per event at worst, above the 0.0065 the red team
used. Shifting every unit by 0.0114 gives B-sparse out-of-dictionary 33% against 70%, and C1 and C2 stay above their limits. The uniform shift that fires too-easy is 0.0227;
C2's lower end reaches 0 at 0.0148 by subtraction. A raised B-lib side was tried too, up to 0.0114: shares fall by at most 0.07 and too-hard is far away. Larger values of that direction make
the closing ratio's denominator negative, so they say nothing. This does not change the owner's question (what T2 should assert, and whether P4 runs on fit/5); it bounds how far it reaches into the pilot's read.

## 2026-11-04: fit/5 search losses: T2 is pinned and the known losses documented (delegated: control room, under the owner's standing delegation)

Decided in INBOX aa83c36, of the three options put to it: pin T2's list by a seeded generator and document the known losses, reported as a found stratum and not a rate. No restated bound and no fit/6 for the pilot.
The test is not loosened: known losses are named in a table and the test asserts that set exactly, so a new loss fails it. The decision cites the session 27 stress numbers, which were not yet red-teamed when it was made; any correction goes back to the control room. The go/no-go and check (b) stay with the owner.

## 2026-11-04: Red team on session 27: the stress test is withdrawn, the failed-inputs entry corrected, and the T2 decision's premise was wrong

Corrects the three session 27 entries above and the stress sentence of the session 26 red-team entry; they stay as written. All from one red-team round (scripts `review-s27/` under `sciagent/p3-linux/report-900e66a/`); the first point also reproduced by the lead.
**Stress test: withdrawn.** (1) The pinned T2 list found 9.0007 nats on 318 events at (2, exp, 1499614403), 0.0283 per event, so "largest loss 0.0114" and "stays below 0.0227" are false, and losses are not one stratum (BIG[0] softplus, BIG[2] exp, the pilot's `exp` ORACLE). (2) T2's gaps are in-sample and `per_event` is held-out; measured ratios run from 1.6 (dev-015 s3) to about 2,500, on T2's short logs, so no per-event size is supported. (3) "No §7.1 outcome moves" held only for the directions tried; C3 was never tested. Through the rule's code: too-easy at 0.0227 on candidates with references held, C2 at 0.0148 (CI half; 0.0175 Wilcoxon half) on B-rand alone, C3 at 0.0100 on ORACLE alone, C1's first drop at 0.0217 (its limit is margin above SE). On the 5 exact control units ORACLE and B-sym@10F share a fit, so a real fix would raise both: this shows sensitivity, not that C3 fails. (4) "Every unit" was wrong: literally nothing moves, and grids of 512 points or fewer cannot miss. (5) "It bounds how far it reaches" is withdrawn; T2's gap is itself a lower bound on a miss. "Larger values say nothing" was also wrong: a non-positive denominator gives `None`, and C1 drops truths. The computed verdict on the stored numbers stands, reproduced through `analysis.verdict`; how far unmeasured misses reach into it is open.
**Failed inputs.** The title's "the agents' own; no harness defect" goes further than the record: no defect found in our code path, and the agents' typing is likely, not shown (107 of 116 inputs above 2,048 bytes reached the layer, so it is no length cut). The 2,048 cap is the SDK's, not the record's. 18 of 19 are `force_events` and 1 a `compose` containing it. "None cost a session its experiment" does not follow: none was charged and each was followed by a charged run, but on 9 of 19 the next call was refused at the layer and on 5 of 19 the session never ran a `force_events` experiment. The named faults are first faults only: 12 of 19 carry mark-count drift, the family P4-PROPOSAL Δ3 cites. The CLI shows the first 200 bytes of a parse failure, so a tail fault is never shown to the agent.
**Process.** I sent the control room the stress numbers before the red team read them, without saying so, and it decided on them. Numbers go out reviewed or labelled unreviewed. Correction sent; the decision stays open in INBOX until it answers.

## 2026-11-04: fit/5: pin-and-document withdrawn; the bound is restated under a rule written first (delegated: control room, under the owner's standing delegation)

INBOX e7c6431 replaces aa83c36, whose figures were wrong. Before any new search: a short rule, red-teamed once, for how misses on the pilot's own units are counted and how their held-out size is measured. Then a CPU-only check over every pilot unit, reporting how many carry a miss, their held-out effect, and whether the computed GO survives. Until then the go/no-go is not put to the owner as final. fit/6 stays a SPEC-level item for after the pilot, and the T2 pin stays off v2, so the p4-deltas slow tier keeps T2 as its one expected failure.

## 2026-11-04: PILOT.md's report wording: the verdict is labelled computed, and C3 counts what it reads

`pilot_report.py` now labels every computed outcome as the rules' output and not the decision (PENDING is unchanged), and C3's line counts the truths it reads by `analysis.verdict`'s own rule. The B-sparse sentence is dropped whole, its advice included, because that advice had no support once the equivalence claim went. Text only: the regenerated output differs in those three lines, and no number moves.

## 2026-11-04: The pilot miss check's pre-registration is not ready and is not frozen

One red-team round on 128f236: not ready, seven blocking findings, report saved verbatim. The draft's sizing script evaluated the generating point into the profile the reference search takes its maximum over (`t2_ref.py` line 26, confirmed by the lead), so "caught at the first start" and "wide basins" were artefacts; blind, one known miss is reached by about 1 start in 77. The other blocking items: the pilot-scale control can pass without R1, `GO_SURVIVES` cannot tell nothing-to-find from could-not-find, the freeze leaves a tuning window and an unpinned split, the tolerance can be infinite, the CPU ceiling holds per unit and not per run, and the statements of prior exposure are inaccurate (a red-team round had already evaluated dev-015 s3). No pilot unit has been searched by this check. Blocking findings get fixed, so no run starts until a second round passes.

## 2026-11-04: The pilot miss check's rule after rounds 1 and 2: found misses only, budget in points (session 28; draft, not frozen)

Round 2 on b6e98db was also not ready (five blocking, report saved verbatim), so the text changed again before any freeze. Design decisions, each with its reason. **The survival label speaks of found misses only** (`GO_SURVIVES_FOUND_MISSES`), with fits split into exact and searched, unvalidated, because 118 of 196 tier 1 fits have grids larger than any T2 or pilot control, and a clean result there is no evidence of absence. **H1 is a stated catch rate, at least 5 of 6 T2 cases plus dev-015 s3 plus enumeration on one case**, not "all must pass": at the blind rates all six pass with about 0.41, so that rule would mostly measure the 1/77 case. **A failed R1 control no longer stops the run**, because the exact class does not rest on R1 and found misses stay real. **`GO_CHANGES` outranks every shortfall**, because round 1's "a STOP is always INCOMPLETE" let a stop bury a flip; a run ended by STOP is not resumed. **Every label's consequence for the go/no-go is in one table**, and only `GO_SURVIVES_FOUND_MISSES` releases the go/no-go. **The budget is points, not starts:** up to 48 starts within 8,000 new solves on exp and softplus and 3,000 on identity. The draft's 0.782 sweeps per start came from T2 grids that fill up; round 2 objected, and seven synthetic fresh-profile cases gave 0.34 to 1.10 over 48 starts (`.cache/drafts/prereg-s28/m.out`). The caps keep controls, group C and tier 1 at 76.1 CPU hours at section 8's rates however much a start costs. The price is that about half the exp and softplus fits and nearly all identity fits stop short of 48 starts, and the text says so. **Ceiling 84 CPU hours, stop at 78,** held by the kernel's count across units. **A synthetic hunt (group C)** is the only control on identity and grids above 24,000 points; it may yield nothing on identity, and 51 of 131 searched tier 1 fits stay above it. **`F` then `R`**, with the script's hash on record before launch and any later change an amendment approved by the control room.

## 2026-11-04: The pilot miss check is pre-registered and frozen (F = 3226f9b; delegated: control room, INBOX e7c6431)

Frozen: `docs/v2/prereg/2026-11-04_pilot-search-misses.md` at 3226f9b9521fe3a8c1d2445d1654cd1609b1eb2d. Red team: round 1 not ready, round 2 not ready, round 3 (narrow) ready with fixes; reports under `docs/v2/redteam/2026-11-04_pilot-search-misses*.md`. Every blocking finding is fixed; round 3's non-blocking findings 2 to 10 were all fixed in the text, and I read those lines against its list, with no fourth round, as round 3 said would do. Ceiling: 84 CPU hours in total, $0, no LLM call. No pilot unit has been searched, and roots 20261104 and 20261106 are unrun. Next is `R` (script, test and their hashes here), then the launch once MemAvailable is at least 8 GiB.
sha256: ea71cdea282c9dac13586649b504286e11221958aa5764aba16326a37c972659  docs/v2/prereg/2026-11-04_pilot-search-misses.md
owner go: 2026-11-04   (the freeze date, under the owner's standing waiver of 2026-10-29)

## 2026-11-04: Pilot miss check: `R`, the script and its test (session 28)

`scripts/pilot_search_misses.py` and `tests/campaign/test_pilot_search_misses.py`, built test first on a side branch against toy data, with `PREREG_SHA256` and `F_COMMIT` set to the freeze. The text's words are the rule. Built detail: a fit's result sits in the `r1` record, so a job that dies in R2 keeps it as `r2_lost`; the hash lines here are what stop an edited later commit from passing the diff guard. Not verified, because forbidden before the launch: the run past the guards, the real split hash inside the script, R0 against any real cell, memory at 14,400 points, real systemd behaviour.
sha256: 94003dea9db544f8ff88d360b2906468db4dba77b06afa99531c34ddecc4713b  scripts/pilot_search_misses.py
sha256: cf8654a3dbfe8dbdfd7b6dd972cc17d5fdb2f5f274b53673a31fa045b190ec71  tests/campaign/test_pilot_search_misses.py

## 2026-11-04: The miss check's exploratory table is defined before any number exists (session 29)

`scripts/p3_report/miss_note.py` (884ba01) prints section 10's note from `decision.json`, `units.jsonl` and `timing.jsonl`, built on toy records while the run was at its start, with no record read. It sets no label and is not part of `R`, so it is not an amendment. Section 4 names the exploratory table's rows but does not define them, so they are fixed here: "sharing of `period` slots" is read as the count of `period` slots in the structure (0, 1, 2 or more) and, for miss rows on fits with 2 or more, whether two slots hold the same grid index at the stored and at the chosen point; G bands use the prereg's own edges (14,400, 24,000, 3,456,000); a miss is any row with `call == "miss"`, LLM rows included, so the total equals M; tallies are counts, with no rates. Floats print as their shortest exact decimal, so a value near a threshold cannot change its look. What the records do not carry prints "not recorded": capped rows, not-applicable fits, the rows behind a moved clause, each statistic's limit in S4. The LEDGER row, the S3 copy, departures and whether anyone read `journal.jsonl` are added by hand; CPU hours come from `cpu.json`, which the tool does not read.

## 2026-11-05: The report recomputes `psi_search` to give SPEC §2.2's shares (session 31)

No pilot cell stores `psi_search`, so `pilot_report.py` recomputes it from the row's structure instead of printing "not recorded": `_search` branches on grid size alone, the 512 limit is in the cell's address, and the only exception is ORACLE's kept truth-ψ refit. The coordinate-optimal share counts certified coordinate fits over all rows reading 0 or 1. "(n uncertified)" marks go only on tables whose numbers read a row's final fit. No version constant moves, and the GO computation is unchanged; the recomputation rests on `fit` calls at the boundary on a toy case and on the match with the prereg's section 2.1 census, not on a rerun pilot fit.

## 2026-11-05: The pilot miss check reports `GO_SURVIVES_FOUND_MISSES` (stop point; session 31)

The frozen check ended complete at 00:49:38Z: 25.41 CPU hours, 12.83 wall hours, $0. H2 and H3 are falsified, H1 and H4 not. M is 25 rows on 25 fits, 23 of them baseline, and the largest |Δ| among the misses found is 0.02198. The verdict on corrected readings is go and no clause moved, so section 5's table releases the hold and the go/no-go goes to the owner with section 2.7's wording. The red team recomputed the label and every number it checked, and rated the note ready with fixes (`docs/v2/redteam/2026-11-05_pilot-misses.md`); a narrow second round, also ready with fixes (`-round2.md`), found three text faults, all fixed, and confirmed SPEC §2.2's shares by a route with no shared code. No analysis changed after the numbers were seen. Departures, as section 10 asks: none in the launch, guards, stage order or budget; the note's first version omitted items sections 2.2, 4, 6 and 10 require, and its strata heading ("the control that speaks for them") claimed more than the rule allows. Both are fixed: first version 95e4341, heading and "largest |Δ| among the misses found" now in `miss_note.py`, which is not part of `R`, and the rest added by hand in `docs/v2/PILOT-MISSES.md`. A correction to the entry of 2026-11-04 on the exploratory table: "before any number exists" holds for pilot units only. The generator was committed at 12:17:04Z, with groups A and D done and the hunt running, and the first pilot unit came at about 13:25Z; no session read those records.

## 2026-11-05: T2 asserts only its listed losses; P4 runs on fit/5 with the miss check (delegated: control room, under the owner's standing delegation)

Decided in the INBOX entry of 2026-11-05 (0577e79). T2 asserts only what it tested: the pinned 300-case list's known losses, each at its recorded gap, with no general bound and no "found stratum" wording, so the `t2-pin` commits come to v2 (db3cef6, a05adfd; list hash 7d2c35128f8aa7fc). This replaces "the T2 pin stays off v2". P4 runs on fit/5, and the frozen pilot miss check (prereg 3226f9b, same rules) is applied to P4's fits before P4's verdict is read. fit/6 stays a SPEC item for after P4. The P3 go/no-go and check (b) stay with the owner. A passing T2 now says nothing about cases off the list.

## 2026-11-05: C5 built on `p4-rehearsal`: every policy stream is keyed by truth (session 33)

The adopted rule (LOG 2026-10-27, 2026-10-28) as written. `scoring/seeds.py` gains `truth_policy_seed(truth_id, seed)`, `stable_key(f"{truth_id}|{seed}|policy") % 2**63`, there rather than beside `search_seed` because `investigation/runner.py` needs it and `campaign/plan.py` imports the runner. `execute.py` and `runner.build_lab` pass it where the investigation seed went; the world and the search keep their seeds, and `policy.py`, `policies.py` and `policy_search.py` still receive an opaque integer. The rule is stated once, `POLICY_SEED_RULE`, and read by the B-sym arms' text and a new `policy_seed` field of `policy_config`. So the four `B-sym+*` units and the AG-c policy sessions move address (EIG and FSD pins re-pinned, 2 of 4 in `PINNED_SESSIONS`); no plain unit and no pilot cell does. No version constant moves: the change sits in config text, and `harness/2` has run no session. A policy session recorded before this commit could not replay, and none exists.

## 2026-11-05: Δ6 built on `p4-rehearsal`: `P4_ARMS` and a split-keyed guard; the field default and `PILOT_ARMS` stay `v1` (session 33)

The owner's (A) as `plan.P4_PROMPT_VARIANT` and `P4_ARMS`, the pilot's four arms on it, named `+link-neutral` as the 2026-10-06 entry says. `LLMArm.prompt_variant`'s default and `PILOT_ARMS` could not move: `test_pilot_harness1_cells.py` builds `LLMArm("AG-c", "named")` and the rescore finds the pilot's 240 sessions by those names and by `legacy_session_config_digest`, which is `None` off `v1`. So the guard does not rest on the default: `campaign/freeze.py` keys on the truth's split, which `TruthEntry.split` (required, unaddressed) now carries from the record. A truth outside `dev` (an unknown split counts as outside) refuses any planned arm off the variant, any arm whose surface differs from its pin or has none (`scripts/pilot.py` `P4_SURFACES`, the four arms at 8 experiments and 40 fits, equal under the test and real environments), and any store holding a session off the variant, planned or not. The first two refuse before anything is created, the third before the fingerprint guard, so a refusal leaves the store as it was. `--arm-set {pilot,p4}` selects the arms and defaults to `pilot`, so `session_check`, `step4.py` and the report keep rebuilding the pilot's configuration. Unknown `--arms` names now refuse; an empty `--arms` still means none, as `project_runtime.py` uses it. The report matches arms by `config.arm_of`, and builds its configuration with `campaign_config`, which it had not (it ignored `--arms`, and would have read no P4 session). No address and no version constant moves. Left open: `P4_ARMS` holds no policy arm and the pins no budget-sweep point, so the amendment adds them or the guard refuses them; the "tried a non-identity link" reader is not built (see the session's report).

## 2026-11-05: F5 stops at the byte-identical part: the rest moves the pilot's addresses, so it returns to the lead (session 33)

F5 point 2 as adopted says it "moves every baseline and score address", and this session's brief requires the pilot store's addresses to keep re-deriving, with a stop rather than a choice if a decision cannot. Measured on a store copy, each item applied in memory (`.cache/drafts/s33-rehearsal/f5_moves.py`; baseline addresses derived from the code, score addresses from `score_key`): `METRIC_VERSION` gaining a digest moves 430 of 430 baselines and 240 of 240 scores, `DATA_VERSION` the same, domain text in the environment version the same; derived ORACLE text moves ORACLE's 60 baselines, derived library text B-lib's 60, and text for B-rand that differs from the pilot's B-rand's 60. Built: what keeps its bytes. B-rand, B-np and the B-sym policy arms come from one constructor shared by `plan.py` and `execute.py` (`b_rand_system`, `b_np_system`, `policy_search_system`), and their text reads that object, so `max_draws_factor=100` is no longer a copy. All 13 system texts are byte-identical before and after. No version constant moves. Not built: `:175`, `:180` (`METRIC_VERSION`, `DATA_VERSION`, the AST completeness test), the domain's text, `:565` (ORACLE) and `:553` (B-lib's model members). Note `step4.py`'s `match 910` cannot see the baseline text moves: it re-hashes each stored key and never derives one from the code. `rederive.py` beside it does (430 of 430).

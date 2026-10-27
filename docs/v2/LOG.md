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

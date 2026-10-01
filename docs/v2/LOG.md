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

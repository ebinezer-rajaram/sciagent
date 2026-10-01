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

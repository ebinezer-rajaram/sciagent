# sciagent v2 — open mechanism discovery under misspecification

**Status: approved 2026-10-01; phase 0 in progress.** v1 (`docs/v1/SPEC.md`,
code at the `v1.0` tag) stays frozen as the recorded baseline campaign. This
document supersedes it for all new work.

---

## 0. Why there is a v2

v1 asked: *once statistics has detected that the model space is inadequate,
does an LLM propose useful new executable structure better than retrieval and
search?* The recorded 1,120-run campaign could not answer that. The cause is
the design, not a bad result:

| Finding (from `spec9.db`, `scripts/report_matrix.py`, 2026-10-01) | Consequence |
|---|---|
| V7 (LLM) equals V1 (no LLM) on D1, D2, D3, D5, D6 in **all 12 scenarios**; they differ only on D4 | The LLM changed no outcome anywhere |
| S1–S7 and S9: closed-world truth mass ≈ 1.0 for every library system | 8 of 12 scenarios were already solved before an LLM was involved |
| The agent grammar has 5 structural cells; the ground grammar has 7 | Too small for proposal to matter, since enumeration solves it |
| S11's truth cannot be expressed through the agent's menu (distance floor 1.5 against the null's 1.0) | The only out-of-library scenario was unwinnable for every system |
| `size_gap_correlation` was available in 112/112 briefs and observed in 0/112 | BOED never showed the LLM the evidence that pointed at the answer |

**The lesson v2 is built around:** a comparison is only informative if some
system is *demonstrated* able to win it. Every v2 contrast ships with a positive
control (§6.4) that shows the instrument can separate systems. A tie is then a
result, not an artefact.

---

## 1. Research questions

**Q1 — Discovery vs search.** In an open mechanism space too large to
enumerate, does an LLM agent find out-of-library mechanisms better than
symbolic search, sparse regression over a feature dictionary and random
generation **at an equal budget of model fits**?
This is the primary question.

**Q2 — Reasoning vs retrieval.** How much of the agent's success survives
anonymisation, i.e. stripping every domain-meaningful name (`arrival`, `size`,
`magnitude`, `earthquake`) from tools, data and DSL? The gap between the named
and anonymised conditions measures reliance on pretraining recall. This is v1's
R1, now measurable.

**Q3 — The evidence wall.** Does an agent that chooses its own experiments and
diagnostics gather the discriminating evidence that BOED over the entertained
set never gathers? Ablation: the same agent's proposals, but with
BOED-selected evidence.

**Q4 — Found data.** On the real SCEDC QTM catalogue, does the agent arrive at
magnitude-dependent triggering (ETAS productivity, already preregistered as
`environments/qtm` `CONSENSUS_EDIT = SIZE_EXCITATION`), named and anonymised?

**Q5 — Scaling.** How do Q1–Q3 move across Claude Haiku → Sonnet → Opus? Other
providers come only after the pipeline is proven (§9).

**Q6 — Experiment design in the open world (methods contribution,
secondary).** Expected information gain computed over the entertained set is
blind, by construction, to evidence that only the unentertained truth explains.
v1 measured this: the telling diagnostic was observed in 0/112 briefs. v2
states it as a proposition (§2.4). It then tests a **falsification-seeking**
design criterion that targets disagreement between the best parametric model and
a model-free estimate, against EIG and against the agent's own choices.

**Q7 — Does it behave like a scientist?** With free analysis (a code
sandbox and a notebook) and committed predictions, is the agent calibrated,
does it seek falsification, does it revise when refuted, and does it stop
sensibly (§4.4)? And does free analysis (AG-o vs AG-c) break the evidence wall
that the catalogue-only agent and BOED share?

### Where the mathematics is, and why

Each piece is here because it removes a confound or creates a capability. None
is included for its own sake.

| Piece | Removes or enables |
|---|---|
| Convex GLM fitting with duality certificates (§2.2) | Removes "the proposal lost because its fit hit a local optimum", which is a v1-style instrument confound |
| Wiener–Hopf nonparametric kernels (§2.3) | A model-free baseline that needs no vocabulary at all; the open-world reference Q6 needs |
| The evidence-wall proposition (§2.4) | Turns v1's measured failure into a statement that holds for any system |

---

## 2. The mechanism space: point-process GLM features

The v2 hypothesis space is the space of **conditional-intensity GLMs** for a
marked temporal point process:

`λ(t | H_t) = g( θ₀ + Σₖ θₖ · φₖ(t; H_t, ψₖ) )`, with link g ∈ {identity, exp, softplus}.

The agent proposes the **feature set** {φₖ}: this is the structure. The
framework fits the coefficients θ and the shape parameters ψ. The DSL has no
numeric literals, so the agent writes no numbers. Point-process GLMs are
standard in neural spike-train modelling (Truccolo et al. 2005), which makes
v2's instrument conventional statistics; it is not something invented here.

### 2.1 Feature grammar (v2.0)

```
Feature  := Excite(Kernel, MarkFn, Source)   Σ_{t_j < t, j ∈ Source} MarkFn(m_j) · Kernel(t − t_j; ψ)
          | Periodic                          sin(2πt/P), cos(2πt/P); P ∈ ψ
          | Trend                             t / T
          | Feature × Feature                 interaction (still linear in θ)
          | Gate(Feature, Cond)               feature active only while Cond holds
Kernel   := ExpK | PowerK | GammaK            shape ψ profiled on a fixed grid
MarkFn   := One | Mark(c) | Pow(Mark(c)) | ExpOf(Mark(c)) | Above(Mark(c))
Cond     := Above(Mark(c)) of the last event | phase window of a Periodic
Source   := all | sign=+ | sign=−
```

`c` ranges over the environment's mark channels (`size`, `sign` in pointproc;
`magnitude` in QTM). Under anonymisation these become `m1`, `m2` (§5).

v1's mechanisms are expressible: Hawkes = {`Excite(ExpK, One, all)`};
seasonality = {`Periodic`}; S11's truth = {`Excite(ExpK, Mark(size), all)`};
ETAS ≈ {`Excite(PowerK, ExpOf(Mark(magnitude)), all)`}. Regime switching and
the Poisson mixture are not functions of observable history. They stay in the
**library** as fitted special cases, outside the feature grammar.

The size of the space (feature sets of up to K features at nesting depth ≤ 3)
is computed in P1, not estimated. It must be far larger than any budget in §4,
and larger than the B-sparse dictionary (§4.1). Otherwise proposal quality
cannot matter, which was v1's failure.

### 2.2 What the framework computes (never the agent)

- **Exact likelihood.** `log L = Σᵢ log λ(tᵢ) − ∫₀ᵀ λ(t) dt`. Feature values
  at event times and their integrals are precomputed per (feature, ψ):
  exponential kernels by the O(n) recursion, power-law and gamma kernels by
  direct O(n²) sums, which is fine at n ≤ 5,000.
- **Certified fitting.** For fixed ψ the negative log-likelihood is **convex in
  θ** for all three links: an affine term minus a log of an affine
  (identity), or a GLM with convex cumulant (exp, softplus). Each inner fit is
  solved by an off-the-shelf conic solver (CVXPY with Clarabel), and the
  **duality gap is recorded as an optimality certificate**. ψ is profiled
  over its grid. So every reported fit is the global optimum on that grid. A
  proposal can lose only because it is a worse structure, never because the
  optimiser stalled. The share of certified fits is reported. Uncertified
  fits (solver failure) are flagged, never silently used.
- **Canonicalisation.** A feature set is a canonical multiset of canonical
  feature trees (commutativity of ×, flattening, de-duplication), so equivalent
  proposals share one hash.
- **Structural distance.** An optimal matching between the two feature sets
  (Hungarian algorithm), with normalised tree edit distance (Zhang–Shasha) as
  the per-pair cost and a fixed cost for unmatched features. It is tested for
  the metric axioms.
- **Goodness of fit.** The time-rescaling theorem (a KS test on rescaled
  inter-arrivals), plus predictive p-values on any diagnostic the agent names.

### 2.3 The model-free estimate: Wiener–Hopf kernels

A linear multivariate Hawkes process is a linear filter driven by a point
process. Its kernels satisfy a Wiener–Hopf system in the second-order
statistics (Bacry & Muzy 2016), so they can be estimated **without proposing
any structure** by solving a regularised Toeplitz system built from empirical
cross-covariance densities. For pointproc, the channels are arrivals and
mark-weighted arrivals, so a non-zero size→arrival cross-kernel is S11's
signature with no vocabulary involved. This serves three roles:

1. **B-np**, a baseline that never writes a structure. It is scored on the
   held-out predictive gap and interventional similarity only (not structural
   recovery).
2. **A tool** the agent and the other baselines may call (`nonparam_kernels`).
3. **The open-world reference** for falsification-seeking design (§2.4).

Its limit is stated rather than hidden: it sees only second-order linear
structure, so it misses gates, thresholds and nonlinear links. Truths built
from those are where structure proposal can beat it.

### 2.4 The evidence wall, and falsification-seeking design

**Proposition (to be stated and proved in P1).** Let Hₑ be the entertained set
with posterior weights w, and let a diagnostic's outcome Y_d have distribution
p(y | h) under h ∈ Hₑ. Then EIG(d) = I(Y_d; H) under w. This is bounded by the
weighted divergence among the entertained hypotheses' predictives, and
**it does not depend on the truth**. So any diagnostic whose distribution is
(near-)invariant across Hₑ has (near-)zero EIG, however strongly the truth
would move it. In v1, sizes were independent of arrivals under every entertained
hypothesis. That made `size_gap_correlation` near-uninformative to BOED, which
matches the 0/112 measurement. The bound and its tightness are written down,
not just the intuition.

**Falsification-seeking design (FSD).** Choose the experiment e and diagnostic
d that maximise the expected discrepancy between the fitted parametric model's
predictive and the model-free (Wiener–Hopf) predictive, under e. EIG asks
"which entertained model is right?"; FSD asks "where is the best entertained
model most likely wrong?". It is tested in Q6 as a fourth evidence policy beside
EIG, random and agent-chosen. Its blind spot is the Wiener–Hopf estimate's own
(non-linear truths), and that is measured too.

---

## 3. Scenarios: a generated population of truths

v1 had one out-of-library scenario. v2 samples **a population of held-out
truths** from the DSL:

- **The library** (what retrieval and the agent's starting model know) is v1's
  four mechanisms plus the null, fitted.
- **Truths** are sampled from the DSL subject to:
  - stationarity (branching ratio < 1);
  - a calibrated operating point (mean rate 1.0, dispersion within a band), so
    the summary statistics alone don't separate truths;
  - identifiability (the truth beats every library member on held-out
    likelihood by ≥ δ at the experiment budget; checked by fitting, not
    assumed);
  - **non-membership**: the truth is not within distance ε of any library
    member.
- **Strata.** Truths are stratified by distance to the nearest library member
  (near / mid / far), so results come out as a curve over distance from the
  library, not one number.
- **Splits.** `dev` truths for building and tuning; `test` truths are
  hash-committed before any agent touches them and run exactly once (§6).

Also kept from v1, as named sanity scenarios: in-library truths (a floor: every
system should solve them), the null (abstention), and S12's garden path.

---

## 4. The investigation: an agent, in two tiers

In v1 the LLM was not an agent. It had no tools (`tools=[]`), made at most two
structured proposals per investigation, was consulted only after a failed
adequacy check, and never chose an experiment. v2's LLM arms are agents. They
decide what to look at, what to run, what to believe and when to stop.

**Harness.** Both tiers run in the Claude Agent SDK loop on Max. The tools are
exposed as MCP tools, and every model turn and every tool result is recorded,
so a whole investigation replays offline byte-for-byte.

### 4.0 Tools

| Tool | Returns | Budget | Tier |
|---|---|---|---|
| `run_experiment(intervention)` | new event log under an intervention written in the intervention language (forced arrivals, mark injection, censoring windows, rate clamps, composed and scheduled) | **E experiments** | both |
| `diagnostic(name, args, data)` | a statistic from the framework catalogue (v1's 20 diagnostics plus residual, cross-mark and spectral diagnostics) | unbudgeted, logged | both |
| `nonparam_kernels(data)` | Wiener–Hopf kernel estimates (§2.3) | unbudgeted, logged | both |
| `fit(feature_set, data)` | certified fit: log-lik, BIC, rescaling-KS, predictive p-values, fitted parameters | **F fits** | both |
| `predict(experiment, statistic, interval)` | records a committed prediction *before* the experiment runs (§4.4) | unbudgeted | both |
| `notebook(append)` | the agent's lab notebook; persists across turns and is shown back in full | unbudgeted | open |
| `python(code)` | runs code in a sandbox over the data the agent has collected (numpy, scipy, pandas, statsmodels; no network, no filesystem outside the run, CPU and time limits) | unbudgeted, logged | open |
| `submit(feature_set, report)` | ends the run with a structure and a short written report | once | both |

Defaults: E = 8, F = 40, varied in a budget sweep. Observational data (one
2,000-event log) is free at the start.

**The two tiers.**
- **Constrained agent (AG-c):** every tool except `python` and `notebook`.
  This is the arm that answers Q1, because its only way to evaluate a structure
  is the metered `fit`, so the fit budget is equal across arms.
- **Open agent (AG-o):** adds the sandbox and the notebook. This is the
  research-scientist condition. It can compute any statistic anyone thought of,
  or one nobody did, and it can fit models privately in the sandbox, so it is
  compared at an **equal experiment budget E**. That is data efficiency, the
  budget that matters scientifically. AG-c vs AG-o asks directly whether giving
  the agent free analysis breaks the evidence wall (§2.4).

**Invariant, restated for the sandbox.** The agent may compute any number for
its own reasoning. It can never set a number the evaluation reads. Scores,
certified fits of the submitted structure, and the held-out data are computed
by the framework after `submit`, from data the sandbox never saw. The sandbox
has no path to the truth, the held-out data or the scorer, and this is tested.

**Determinism with a sandbox.** Sandbox code is executed with a seeded,
single-threaded numerics configuration, and its stdout is recorded with the
transcript. Replay reads the recorded results and re-executes them to check
they still match, so a numerics drift is detected rather than silently
replayed.

### 4.1 Systems

| ID | System | Role |
|---|---|---|
| **AG-c-{H,S,O}** | constrained agent on Claude Haiku / Sonnet / Opus | system under test (Q1, Q2, Q5) |
| **AG-o-{H,S,O}** | open agent (sandbox and notebook) on the same three models | system under test (Q2, Q3, Q5, Q7) |
| **AG-S-boed** | Sonnet proposes; BOED picks experiments and diagnostics (the Model Discovery Agent design, §10) | Q3 ablation |
| **B-lib** | fit every library member, submit the best | retrieval floor |
| **B-rand** | F random DSL samples from the truth prior's structure distribution, best by BIC | proposal-free control |
| **B-sym** | symbolic search over the DSL (genetic programming / beam, PySR-style) using F fits | the serious comparator |
| **B-sparse** | group lasso over a fixed dictionary of all features up to depth 2 at every ψ grid point; one convex fit, regularisation path chosen by held-out likelihood | the strongest non-search rival; succeeds wherever the truth is in the dictionary |
| **B-np** | Wiener–Hopf nonparametric kernels (§2.3); proposes no structure | model-free rival |
| **B-boed** | v1's V1 adapted: BOED over the library | v1 continuity |
| **ORACLE** | fits the true structure | upper bound, positive control |

Each LLM arm runs in **named** and **anonymised** conditions (§5).

The truth sampler places a declared share of test truths **outside the
B-sparse dictionary** (depth 3, gates, interactions). Without that, B-sparse
wins by construction and Q1 is uninformative. The share is reported, and
results are split by in-dictionary vs out-of-dictionary.

### 4.2 Evidence policies (Q3, Q6)

For the evidence-policy comparison, proposal is held fixed (B-sym at budget F,
then AG-c-S) and only the choice of experiments and diagnostics varies:
**random**, **EIG** over the entertained set, **FSD** (§2.4), and **agent-chosen**.
The read-out is the held-out gap at the end, plus whether the telling
diagnostic for each truth (defined per truth by the oracle: the diagnostic with
the largest truth-vs-best-library discrepancy) was ever observed. That second
number is the generalised 0/112.

### 4.3 Scoring (four numbers, reported separately)

1. **Held-out predictive gap.** Per-event log-likelihood of the submitted model
   minus the oracle's, on fresh data from the truth. Primary for Q1, and a
   strictly proper score.
2. **Structural recovery.** Exact recovery rate (canonical hash match) and
   feature-set distance to the truth (§2.2).
3. **Interventional similarity.** Response to a held-out intervention battery
   versus the truth's (v1's D3, kept). It credits interventionally equivalent
   but structurally different answers (v1's R7).
4. **Efficiency curve.** Best-so-far held-out gap against fits used. This is
   the figure that answers "better than search at equal budget".

v1's six-dimension vector, D4 and D5 are retired. They cost most of v1's
instrument-repair work and answered nothing the four above miss.

---

### 4.4 Scientific behaviour, scored mechanically (Q7)

Before any experiment the agent may — and the prompt asks it to — commit a
prediction through `predict`. A prediction is a statistic (a catalogue
diagnostic, or for AG-o a named sandbox function recorded with its code), the
experiment it applies to, the hypothesis it follows from, and a central
interval. The framework evaluates it on the experiment's actual data. No LLM
judge is involved anywhere. From the record:

- **Calibration.** Interval coverage at the stated level, overall and per
  hypothesis.
- **Falsification-seeking.** The share of experiments whose committed
  predictions *could* refute the agent's currently favoured hypothesis, i.e.
  the favoured and runner-up hypotheses predict disjoint intervals. Compared
  with confirmation-only experiments.
- **Revision.** After a falsified prediction, did the favoured hypothesis
  change within the next k turns? How often does a refuted hypothesis come
  back unchanged (zombie hypotheses, v1's term)?
- **Stopping.** Experiments used against the held-out gap at submission:
  does the agent stop too early or burn budget after it has converged?

These are reported per tier and per model. They are the "research scientist"
read-out: not only whether the agent got the answer, but whether it behaved
like someone who could be trusted to get it.

## 5. Anonymisation (Q2)

The anonymised condition rewrites, consistently and reversibly, every name the
agent can see: channel names (`size`→`m1`, `sign`→`m2`, `magnitude`→`m1`),
tool and diagnostic names (`size_gap_correlation`→`d17`), and the system
prompt (no "earthquake", "seismic", "Hawkes", "ETAS", "aftershock"). Time is
rescaled by a fixed factor, so familiar magnitudes and Omori-like constants
aren't recognisable by value. DSL production names are kept, because they
describe mathematics, not a domain.

A leak test greps every rendered prompt in the anonymised condition for a
forbidden-term list and fails on any hit.

---

## 6. Preregistration and positive controls

### 6.1 Frozen before the test campaign
DSL version, truth sampler and its seed, the test-split hash, budgets, the four
scores, the contrasts in 6.2 and the analysis code. All committed in one commit
whose hash is quoted in the results.

### 6.2 Preregistered contrasts
- **C1 (Q1):** AG-c-S vs B-sym on the held-out predictive gap, paired by truth,
  test split, named condition. Paired bootstrap 95% CI.
- **C2 (Q2):** AG-c-S and AG-o-S, each named vs anonymised, same metric, paired.
- **C3 (Q3):** AG-c-S vs AG-S-boed (who chooses the evidence), and AG-o-S vs AG-c-S
  at equal E (does free analysis break the evidence wall).
- **C4 (Q1, the hard rival):** AG-c-S vs B-sparse on **out-of-dictionary**
  truths.
- **C5 (Q6):** FSD vs EIG on the held-out gap, and on the rate of observing
  the telling diagnostic.
- **C6 (Q7):** AG-o-S vs AG-c-S on the §4.4 behaviour measures; exploratory.
- Secondary: everything else, labelled exploratory.

### 6.3 Instrument tests (the only acceptance tests v2 requires)
1. Likelihood matches the analytic value for Poisson and Hawkes, and matches
   simulation-based estimates for each kernel.
2. Fitting is deterministic (byte-identical) and recovers known parameters on
   simulated data within tolerance. Certified fits agree with an independent
   unconstrained optimiser at the optimum. The duality gap is below tolerance
   on every certified fit.
3. Canonicalisation is idempotent and equivalence-preserving (property-based,
   via Hypothesis).
4. The feature-set distance satisfies the metric axioms.
5. Wiener–Hopf recovers known Hawkes and cross-kernels on simulated data, and
   returns a near-zero cross-kernel when there is none.
6. The truth sampler is deterministic, every test truth passes its constraints,
   none is library-near, and the declared out-of-dictionary share holds.
7. Budgets are enforced by the tool layer; no tool path exposes the truth;
   the anonymisation leak test passes.
8. Sandbox isolation: code run through `python` cannot reach the truth, the
   held-out data, the scorer, the network or files outside its run
   directory. Tested with adversarial snippets that try each one.
9. Replay: a recorded AG-o investigation replays byte-identically with the
   provider disabled, and a deliberately perturbed sandbox result is detected.
10. Predictions are evaluated by the framework only, and an interval stated
    after its experiment ran is refused.

### 6.4 Positive controls (run before any contrast is reported)
- ORACLE beats B-lib on every test truth. If not, the truth is not
  identifiable and is dropped by the preregistered rule.
- A **planted-hint agent**, scripted to propose the truth's top-level
  production first, beats B-rand by a clear margin. This proves the efficiency
  curve can separate a good proposer from a bad one.
- B-sym at a very large budget (10× F) approaches ORACLE. This proves the space
  is searchable in principle, so a B-sym loss at budget F is about efficiency.

---

## 7. Phases

Each phase ends in something demonstrable. Phase 3 is a go/no-go gate.

| Phase | Deliverable | Done when |
|---|---|---|
| **P0 Cleanup and restructure** | §8.1, in full | Fast tier < 2 min; CI < 10 min; the v1 tag reproduces the v1 report |
| **P1 Core** | `sciagent/glm/`: feature grammar, canonicaliser, feature precomputation, exact likelihood, certified fitter, feature-set distance, simulator; `sciagent/nonparam/`: Wiener–Hopf; the §2.4 proposition written up in `docs/v2/evidence-wall.md` | Instrument tests 1–5 pass |
| **P2 Scenarios + agent harness** | truth sampler, splits, intervention language, tool layer with budgets as MCP tools, Agent SDK harness with recording/replay, Python sandbox, notebook, `predict`, anonymiser, B-sparse dictionary | Instrument tests 6–10 pass; one end-to-end AG-o run replays offline |
| **P3 Pilot** | 20 dev truths × 3 seeds: AG-c-S and AG-o-S (named + anon), B-lib, B-rand, B-sym, B-sparse, B-np, ORACLE, planted-hint | Go/no-go read (below); a few AG-o transcripts read in full by you |
| **P4 Main campaign** | test split, all arms, Haiku/Sonnet/Opus, both naming conditions, budget sweep; evidence-policy comparison (§4.2) | C1–C5 reported with CIs |
| **P5 Real data** | QTM: agent investigates the real catalogue (observational only, no interventions), named + anon | Recovery of size-dependent triggering reported |
| **P6 Write-up** | Paper draft (workshop length), blog post, figures, README rewrite around results | Draft reviewed |
| P7 later | Other providers; second domain (e.g. ODE/epidemic) | Only after P4 holds |

### 7.1 Pilot go/no-go criteria (decided now, read after P3)

- **Too easy:** B-lib or B-rand recovers ≥ 70% of truths, or B-sparse closes
  the gap on ≥ 70% of out-of-dictionary truths. Raise depth or tighten
  calibration, then re-pilot.
- **Too hard:** ORACLE-relative gap closes for < 10% of truths with *every*
  system, including B-sym at 10×F. Lower depth, then re-pilot.
- **Controls fail** (§6.4): fix the instrument before anything else.
- **Go:** controls pass and difficulty is in band, *regardless* of whether the
  agent wins. A well-instrumented "LLM ≈ symbolic search at equal budget" is a
  reportable result; v1's forced tie was not.
- Also measured in P3: LLM calls per investigation and wall time per
  investigation under Max rate limits. These size P4, since quota rather than
  cost is the binding constraint.

---

## 8. What carries over, what changes

**Kept from v1:** determinism (seeded generators, byte-identical replay); the
framework-writes-numbers rule, now structural because the DSL has no literals;
domain independence (`sciagent/` never imports `environments/`); the
append-only, content-addressed results store; recorded-and-replayable LLM calls
(`systems/llm/transcripts.py`, `agent_sdk_provider.py`); QTM ingestion; the
pointproc diagnostics and intervention compiler.

**Deleted from `main` (preserved at the `v1.0` tag):** the empirical
simulation-table engine, the hypothesis graph, `verify/`, the six-dimension
scorer, matrix/report/agency, the v1 systems (hybrid, ablation, v1 baselines),
and the A1–A49 gate tests.

### 8.1 Phase 0: cleanup and restructure

The aim is a repository a reader can grasp in five minutes and a Claude Code
loop that is fast. Order matters: tag before deleting.

1. **Freeze v1.** Tag `v1.0` at the current `main`, and write
   `docs/v1/RESULTS.md`, a one-page summary of v1's findings with the exact
   command that reproduces the report from the tag. A GitHub release and the
   push are done only with your explicit go-ahead.
2. **Audit what v2 reuses.** Build the import graph from the modules v2
   keeps (`core/` programme and types, `registry/`, `systems/llm/` providers and
   transcripts, `environments/pointproc` diagnostics and operations,
   `environments/qtm`). Everything not reachable from them is a deletion
   candidate, and the list is shown to you before anything is deleted.
3. **Delete** the unreachable code and its tests. The expectation, not a
   promise, is that roughly half of the 29k source and 34k test lines go.
4. **Docs.** `DECISIONS.md` (723 KB), `BACKLOG.md`, `OPEN-DECISIONS.md`,
   `REVIEW-*.md`, `SCALE-UP.md` and `CORPUS.md` move to `docs/v1/`. `SPEC.md`
   becomes `docs/v1/SPEC.md`, and this file becomes `docs/SPEC.md`. New:
   `docs/v2/LOG.md`, one short paragraph per real design decision.
5. **Tests in two tiers.** A `fast` tier (< 2 min locally, the default run)
   and a `slow` tier (simulation-heavy, run in CI and before campaigns),
   selected by a pytest marker. CI runs both, under 10 minutes.
6. **Claude Code workflow, rebuilt minimal.**
   - CLAUDE.md under ~80 lines: what v2 is, the three invariants, the commands,
     and the git rules.
   - Hooks kept: the determinism guard (forbids global RNG) and ruff after
     edit. The rest are removed (session-start status, suite freshness,
     statusline, pre-compact).
   - Skills: one short `/ship`. Removed: next, gate, decide, recall,
     preflight, test-review, handoff, matrix and platform-check, and all four
     agents. Each comes back only when a real need appears.
   - Removed with them: `scripts/status.py`, the A-numbered gate naming
     convention and the mandatory worktree rule.
7. **README.** Rewritten around the v2 question, with a short "v1 → v2" section
   that tells the forced-tie story as the motivation. Final results replace it
   after P4.

**Rules that survive the slimming.** These are the parts that made v1
trustworthy, and they cost little:
- determinism;
- framework-writes-numbers (now structural);
- domain independence;
- tests written before the *instruments* they gate (ordinary code gets ordinary
  tests);
- preregistration of the test campaign;
- `mypy` and `ruff` clean;
- never commit or push unasked.

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| The LLM simply wins easily (space too friendly to language priors) | Anonymised condition plus B-sym at 10×F; a large named/anon gap is itself the Q2 finding |
| B-sym is a weak strawman | Use an established GP/beam setup, tune it on dev with the same effort as the agent prompt, and report its 10×F curve |
| Max rate limits make P4 slow | Measured in P3; campaign is resumable (v1's driver) and replay-cached; size the truth count to the quota |
| Contamination even when anonymised (Omori-like shapes are recognisable) | That's reasoning from data shape, which is what Q2 wants to credit; time rescaling removes value-level recall |
| Overlap with prior work | §10; the distinct combination is structural ground truth, a mechanical out-of-library definition, interventions, an equal-budget search comparator, and the anonymisation control |
| The open agent games the setup (reads files, probes the sandbox, reverse-engineers the sampler) | Sandbox isolation tests (§6.3 no. 8); the truth sampler's seed and code are never in the sandbox; transcripts are scanned for out-of-bounds access, and any hit voids the run and is reported |
| Open-agent runs are long and quota-hungry | P3 measures turns, tokens and wall time per AG-o run; P4's truth count is sized to that |
| B-sparse wins everywhere: the dictionary covers the truths | The out-of-dictionary share is declared and checked (§4.1); if B-sparse still wins on those, that is a clean, reportable answer to Q1 |
| Mathematics added for show | Each piece must name the confound it removes (§1 table). Fisher-information identifiability and HMM likelihoods were considered and left out for that reason |
| v1 repeats: months on instruments, no result | Phase 3 is a hard gate with criteria fixed above; instrument tests are limited to §6.3 |

---

## 10. Related work and positioning

Checked against arXiv on 2026-10-01 in one pass of web searches. Repeat the
keyword search ("Hawkes" / "ETAS" / "conditional intensity" with "LLM" or
"agent") before any submission. Items marked † were seen only in search
results or abstracts, not read in full.

**The work a reviewer will name first.**
- **Model Discovery Agent** (Murphy, arXiv 2608.09696, Aug 2026, still being
  revised). An LLM proposes mechanisms (equations, ODEs, ion channels), and
  value-of-information experiment design runs inside approximate Bayesian
  inference. It asks the LLM for a "novel unnamed mechanism" when residuals are
  large, and scores held-out interventional prediction on synthetic benchmarks.
  It is the closest prior work and overlaps with the loop itself. Our AG-S-boed
  arm is essentially its design applied in our domain, which makes C3 a direct
  test of "agent-chosen evidence vs VOI-chosen evidence".
- **BoxingGym** (Gandhi et al., arXiv 2501.01540) and **Automated Statistical
  Model Discovery with Language Models** (Li, Fox, Goodman, ICML 2024).
  Experiment-then-model-discovery with programs the LM writes, and a
  propose–fit–critique loop. Neither has a known structural edit, an
  out-of-library truth, or an equal-budget search comparator.
- **Anti-memorisation discovery benchmarks:** LLM-SRBench (Shojaee et al.,
  ICML 2025; transformed and synthetic equations), NewtonBench† (ICLR 2026;
  counterfactually shifted laws), DiscoverPhysics† (arXiv 2605.26087).
  They resist recall by changing the *law*. We resist it by changing the
  *names* while keeping the law. The two are complementary, and a clean
  named-vs-anonymised ablation inside an interactive agent benchmark was not
  found.

**Lineage.** FunSearch (Nature 2024) and LLM-SR (ICLR 2025) established
that an LLM proposes structure and an evaluator fits and scores it; our
invariant that the framework writes numbers is that pattern. POPPER (ICML 2025)
is relevant to the statistical validity of agent-run tests. Also relevant:
Geng et al.† (arXiv 2505.17968), who find LLMs stay below Bayesian inference
when reverse-engineering black boxes, even with interventions. DiscoveryWorld,
ScienceAgentBench, DiscoveryBench and The AI Scientist (v1/v2) are broader
context.

**Point processes and seismology.** No work was found in which an LLM proposes
or edits a Hawkes or ETAS intensity structure. Adjacent work uses LLMs as
temporal-point-process *models* (TPP-LLM†, Language-TPP†) or derives logic
rules scored by TPP likelihood (Song et al., arXiv 2406.01124). Neural ETAS is
forecasting, not discovery. The QTM catalogue is Ross et al., *Science* 2019.

**What v2 claims as new, stated narrowly:**
1. Ground truth as a typed structural edit, scored by structural distance *and*
   interventional similarity *and* held-out likelihood. Prior work scores one of
   these.
2. A population of out-of-library truths stratified by distance from the
   agent's library, giving a controlled version of the M-open case.
3. An equal-fit-budget comparison against symbolic search, with positive
   controls that show the comparison can separate systems.
4. A named-vs-anonymised ablation in an interactive agent setting, carried from
   synthetic data to a real catalogue where the expected finding (magnitude-
   dependent triggering) is established in the domain literature.

The risk is that Model Discovery Agent's later revisions add (2) or (4).
Re-read it before P6.

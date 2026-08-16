# Constrained Agentic Scientific Investigation under Model Misspecification

**Final specification. Implementation-ready.**

Supersedes v1, v2, v3 and the amendments document. After this, changes enter the backlog rather than the architecture, unless implementation surfaces a genuine contradiction.

---

## 0. Settled corrections

Recorded once, then not revisited.

**On the agentic conclusion.** V3 contained meaningful agentic components. Its benchmark did not adequately demonstrate that those components were necessary. The amendments repaired the evaluation, not the existence of agency. My earlier phrasing was wrong and this is the accurate statement.

**On the prior.** The structural complexity prior is defined by description length under the edit grammar's prefix code, fixed before any scenario exists: p(D) ∝ 2^(−L(D)), where L(D) is the encoding length of edit set D. This is a statement of scientific belief about parsimony, derived from the representation, and it is independent of how often each defect type happens to appear in the benchmark. A frequency-matched prior is a sensitivity condition only. Benchmark prevalence and prior belief are different objects and conflating them would have made the posterior an artefact of scenario sampling.

**On causal licensing.** Collateral effects are not required to vanish. The estimand is typed and the verifier checks alignment between intervention, graph structure, declared assumptions and claimed estimand. Broad interventions license broad total-effect claims; narrow component-specific claims require an estimand and an intervention that support them.

**On structural recovery.** Programme-edit similarity does not dominate scoring. Six dimensions are reported separately and the primary interpretation is task-dependent. Recovering the exact programme and recovering an interventionally equivalent explanation are distinct successes, and for out-of-library scenarios the second is the more scientifically meaningful one.

**On the edit grammar and bias.** The grammar does not eliminate author bias. It converts diffuse, hard-to-audit ontological bias into a **formalised, inspectable, versioned** artefact. What mechanisms are expressible, what counts as one edit versus two, and what the prefix code charges for each construct are all author decisions with consequences for every score. They are now written down and can be varied in sensitivity analysis, which is the actual improvement. Any claim implying the grammar is neutral is prohibited.

**On decision counts.** Ten or more meaningful decisions is required only where establishable credibly: exact optimal policy length by exhaustive dynamic programming where the space is small enough, otherwise the strongest planning baseline's solution length as a labelled lower bound. Proving benchmark difficulty must not become harder than the benchmark.

---

## 1. Frozen architectural decisions

Not revisited absent a demonstrated contradiction.

| # | Decision |
|---|---|
| F1 | The simulator is a controlled environment with known ground truth. The agentic research system is the object of study. |
| F2 | Two tracks. Core research track answers the central question with no market data, no exchange semantics, no Rust. Domain grounding is a fidelity upgrade and a transfer test. |
| F3 | Environments are compositional executable programmes. Defects are typed structural edits. Ground truth is the edit set, not a label. |
| F4 | One `Environment` interface. Every environment implements it. All evaluation apparatus is domain-independent above it. |
| F5 | Division of labour: conventional methods own posterior updating, statistical computation, experiment selection within a fixed space, and inadequacy *detection*. The LLM owns hypothesis-space construction and revision, residual *interpretation*, decomposition, planning, and open-world sufficiency judgements. |
| F6 | Out-of-library evaluation splits into Stage A (detection, conventional) and Stage B (extension quality, conditional on detection). Never reported combined. |
| F7 | The LLM writes structure and prose. The framework writes numbers. No code path lets an agent set a plausibility or posterior value. |
| F8 | Claims are typed objects. Prose is a rendering. Six verifier check classes are mechanical; prose faithfulness is not. |
| F9 | Late-proposed hypotheses receive no evidential disadvantage in likelihood, but cannot support a confirmatory claim without a prospectively registered discriminating experiment. |
| F10 | Two approval tiers. Autonomy fraction is reported alongside every performance figure. |
| F11 | Three TEST campaigns, ever. Sealed generator, externally-derived seed, preregistered analysis plan. |
| F12 | The posterior engine is staged: empirical table first, full likelihood-free engine later, each independently validated before agent results depend on it. |
| F13 | One preregistered primary contrast. Everything else secondary or exploratory. |

---

## 2. Unresolved research questions

Genuinely open. Not deferred work; things nobody knows the answer to.

**R1. Is the LLM reasoning or retrieving?** If LLM extension proposals only tie the retrieval baseline, the capability is recall of known mechanisms from pretraining. That is real but much less interesting, and it is the most likely outcome. No prior work settles this.

**R2. Does structured memory beat raw history at equal information?** The V3-to-V4 delta. Plausibly zero. Widely assumed non-zero without evidence.

**R3. Is there headroom above two-step-lookahead BOED on collective-value sequences?** If the LLM merely substitutes for lookahead depth, it is cheaper but not novel.

**R4. Do closed-world results transfer to higher fidelity?** The phase 9 question. A toy programme and a market simulator differ in ways that may or may not matter for diagnostic reasoning.

**R5. How much do scores depend on the edit grammar?** Ranking sensitivity to coarse and fine grammar variants is unknown and could be large enough to undermine any ranking.

**R6. Can prose faithfulness be verified reliably?** Judge-versus-human agreement is unknown. If poor, typed claims become the sole authoritative output.

**R7. Is interventional equivalence without structural recovery common?** If agents routinely propose structurally wrong but interventionally correct explanations, that reframes what "correct diagnosis" means and is a result in itself.

---

## 3. Interfaces and data structures to implement first

Implement in this order. Everything else depends on them.

### 3.1 Programme and edits

```python
ComponentKind = Literal["arrival", "size", "sign", "observation"]

@dataclass(frozen=True)
class Component:
    id: ComponentId
    kind: ComponentKind
    family: FamilyId                     # e.g. "poisson_homogeneous"
    parameters: Mapping[str, float]
    latents: tuple[LatentSpec, ...] = ()

@dataclass(frozen=True)
class GenerativeProgram:
    components: Mapping[ComponentId, Component]
    edges: frozenset[tuple[ComponentId, ComponentId]]   # DAG, cycles only via explicit self-history
    def execute(self, seed: Seed, n_events: int) -> EventLog: ...
    def descendants(self, c: ComponentId) -> frozenset[ComponentId]: ...

Edit = ( ChangeDistributionFamily
       | AddLatentVariable
       | AddDependency
       | ReparameteriseComponent )

Defect = frozenset[Edit]

@dataclass(frozen=True)
class EditGrammar:
    version: str
    allowed: frozenset[type[Edit]]
    targets: Mapping[type[Edit], frozenset[ComponentId]]
    def apply(self, p: GenerativeProgram, d: Defect) -> GenerativeProgram: ...
    def code_length(self, d: Defect) -> float:     # prefix code, bits
        ...
    def distance(self, a: Defect, b: Defect) -> float: ...
```

`code_length` defines the prior. It is fixed before any scenario is generated and versioned. The prefix code assigns bits by construct: edit type, target component, family or spec, then parameters. Write it once, justify it in the write-up, never tune it.

### 3.2 Environment protocol

```python
class Environment(Protocol):
    version: EnvVersion                    # content hash of code + reference programme
    def reference_program(self) -> GenerativeProgram: ...
    def edit_grammar(self) -> EditGrammar: ...          # ground-truth grammar
    def agent_grammar(self) -> EditGrammar: ...         # subset; OOL = in first, not second
    def diagnostics(self) -> tuple[MetricRef, ...]:  ...
    def interventions(self) -> tuple[InterventionSpec, ...]: ...
```

Out-of-library is now precisely defined: `ground_truth_edit ∈ edit_grammar() \ agent_grammar()`. No judgement call.

### 3.3 Hypothesis, prediction, claim

```python
@dataclass(frozen=True)
class HypothesisNode:
    id: HypothesisId
    program_edit: Defect | None            # None only before compilation
    status: Literal["live", "suspended", "rejected", "confirmed"]
    predictions: tuple[PredictionId, ...]  # >= 1
    plausibility: Probability              # FRAMEWORK-WRITTEN
    rationale: str                         # LLM prose, never scored
    rejection_reason: RejectionCode | None
    proposed_at: ExperimentId | None       # None = present at start; drives F9
    version: int

@dataclass(frozen=True)
class Prediction:
    hypothesis_id: HypothesisId
    diagnostic: MetricRef
    condition: Condition
    under: ExperimentTemplate
    refutation: Condition                  # must be satisfiable over the diagnostic's range

Estimand = ( TotalEffect(target, outcome)
           | DirectEffect(target, outcome, mediators_blocked)
           | ControlledDirectEffect(target, outcome, held_fixed)
           | PathSpecificEffect(target, outcome, path) )

@dataclass(frozen=True)
class Intervention:
    target: ComponentId
    manipulated: frozenset[ComponentId]
    estimand: Estimand
    collateral: frozenset[ComponentId]     # DERIVED from programme DAG, not declared
    assumptions: tuple[AssumptionCode, ...]
    expected_direction: Direction          # preregistered

@dataclass(frozen=True)
class Claim:
    subject: HypothesisId | ComponentId
    modality: Literal["correlational", "mechanistic", "causal", "predictive"]
    estimand: Estimand | None              # required if modality == "causal"
    strength: Literal["suggests", "supports", "establishes", "refutes"]
    scope: Scope
    evidence: tuple[ExperimentId, ...]
    partition: Literal["exploratory", "confirmatory"]
    effect: EffectEstimate | None
    uniqueness: Literal["exclusive", "non_exclusive"]
    prose: str
```

### 3.4 Diagnosis

```python
@dataclass(frozen=True)
class Diagnosis:
    scenario_id: ScenarioId
    distribution: Mapping[HypothesisId, Probability]
    abstain_mass: Probability
    null_mass: Probability
    proposed_edits: Mapping[HypothesisId, Defect]     # for agent-created hypotheses
    supporting: Mapping[HypothesisId, tuple[ExperimentId, ...]]
    residual_candidates: tuple[HypothesisId, ...]
```

### 3.5 Posterior engine protocol

```python
class PosteriorEngine(Protocol):
    def log_likelihood(self, h: HypothesisId, e: ExperimentId,
                       result: DiagnosticVector) -> tuple[float, float]:
        """Returns (log-likelihood, Monte Carlo standard error)."""
    def posterior(self) -> Mapping[HypothesisId, Probability]: ...
    def expand(self, h: HypothesisNode) -> ExpansionCost:
        """Register, retro-evaluate against all relevant prior experiments, renormalise."""
    def ppc(self) -> PPCResult: ...
```

Two implementations: `EmpiricalTableEngine` (slice) and `LikelihoodFreeEngine` (later). The agent never sees which is in use.

---

## 4. The vertical slice scenario, exactly

### 4.1 Reference programme

Point process event generator. Four components.

| Component | Kind | Family | Parameters |
|---|---|---|---|
| `arrival` | arrival | `poisson_homogeneous` | rate λ |
| `size` | size | `exponential` | mean μ |
| `sign` | sign | `iid_bernoulli` | p = 0.5 |
| `obs` | observation | `identity` | — |

Edges: `arrival → size`, `arrival → sign`, `{size, sign} → obs`.

### 4.2 The four confounded mechanisms

All are edits on `arrival`. All produce overdispersed counts and clustered inter-arrivals, and are indistinguishable under basic dispersion diagnostics.

| Mechanism | Edit | Discriminated by |
|---|---|---|
| **Hawkes self-excitation** | `AddDependency(arrival → arrival, kernel=exponential)` | `ForceArrival` intervention raises subsequent rate. Unique to this mechanism |
| **Latent regime switching** | `AddLatentVariable(arrival, TwoStateMarkov)` | Geometric run-length distribution of high-rate periods; conditioning on inferred state removes overdispersion |
| **Deterministic seasonality** | `ReparameteriseComponent(arrival, PeriodicRate(period, amplitude))` | Phase-conditioning removes overdispersion entirely; fixed spectral peak; unaffected by forced arrivals |
| **Independent Poisson mixture** | `ChangeDistributionFamily(arrival, MixtureOfPoisson(k=2))` | Fano factor flat across window sizes; forced arrival has no effect; no temporal correlation |

Minimum discriminating plan is three stages: establish clustering is temporal rather than static (rules out mixture), test phase-conditioning (rules out seasonality), intervene (separates Hawkes from regime switching). No single diagnostic resolves it.

### 4.3 Diagnostic catalogue (slice)

`InterArrivalDispersion`, `FanoFactorByWindow`, `AutocorrelationOfCounts`, `PowerSpectrum`, `PhaseConditionedDispersion`, `RunLengthDistribution`, `SizeDistributionMoments`, `SignAutocorrelation`, `MarkArrivalCoupling`.

**Amended 2026-08-16**, on the demonstrated contradiction SPEC §13 requires. `MarkArrivalCoupling` — the correlation between a mark and the inter-arrival gap that follows it — was added because every other entry above is a statistic of one component in isolation, and S11's out-of-library mechanism couples two components while perturbing neither marginal. With the original catalogue the posterior predictive check had **0.000** power against it across 100 scenarios (min p 0.0508, median 0.7972), so §4.6 requirement 1 was unsatisfiable and §9's primary contrast conditioned on an event that never occurred. The failing measurement, the alternatives rejected, and the bin-edge quantiles are in `docs/DECISIONS.md`. This moved `METRIC_VERSION` to 1.2.0 and therefore the content address of every experiment registered against the catalogue.

### 4.4 Experiment operations (slice)

`QueryDiagnostic`, `PerturbParameter`, `ConditionOn(covariate)`, `ForceArrival(intervention)`, `AblateComponent`, `CompareCandidates`.

### 4.5 Slice scenarios: twelve

| ID | Class | Ground truth | Tests |
|---|---|---|---|
| S1–S4 | Single | Each mechanism alone | Basic competence |
| S5 | Confounded | Hawkes, with regime switching plausible | Intervention planning |
| S6 | Confounded | Seasonality, with Hawkes plausible | Conditional analysis |
| S7 | Confounded | Regime switching, with mixture plausible | Multi-scale reasoning |
| S8 | Compound | Seasonality + size-distribution mixture | Decomposition |
| S9 | Null | No edit | Abstention |
| S10 | Non-identifiable | Hawkes vs regime switching, budget below discriminating threshold | Calibrated insufficiency |
| S11 | **Out-of-library** | `AddDependency(size → arrival)`: rate depends on prior mark sizes. **In `edit_grammar`, absent from `agent_grammar`** | Stage A detection, Stage B extension |
| S12 | **Garden path** | Regime switching, plus an observation-level censoring nuisance that produces a strong spurious periodic signature in the first two diagnostics | Plan revision, recovery |

S11 and S12 carry the slice's weight. S1–S4 exist to establish a floor and to calibrate the empirical posterior tables.

### 4.6 What the slice must demonstrate

1. Detect inadequacy (S11 Stage A, via PPC)
2. Propose an appropriate missing mechanism (S11 Stage B)
3. Design a discriminating experiment (S5–S7)
4. Revise a mistaken plan (S12)
5. Maintain coherent evidence (all: zero graph contradictions, no zombie hypotheses)

---

## 5. Minimum baseline suite

Six systems on the slice. No more.

| ID | System | Purpose |
|---|---|---|
| **V1** | BOED-only over the closed set (one-step greedy) | Optimal selection, no representation change |
| **V7** | Hybrid: LLM proposes and revises, BOED selects | The proposed architecture |
| **B1** | PPC-only detector | Stage A floor; proposes nothing |
| **B4** | **Retrieval from fixed mechanism library**, keyed on residual signature | **The designated primary comparator.** Tests R1 |
| **B5** | Symbolic beam search over `agent_grammar`, scored by predictive fit | Enumeration versus generation, same output type |
| **V3/V4** | LLM with raw history versus LLM with hypothesis graph | R2, on S8, S11, S12 only |

Deferred to the full benchmark: V0, V2, V5, V6, B2, B3, B6, B7, two-step-lookahead BOED, multi-provider.

---

## 6. Acceptance tests per subsystem

Every subsystem has a pass condition checkable without reference to agent performance. **A weak posterior engine must never be able to masquerade as weak agent performance**, and these tests are how that is prevented.

### 6.1 Generative programme and grammar

- **A1** Determinism: `execute(seed, n)` byte-identical across 100 repeats and across processes.
- **A2** Edit soundness: applying every grammar edit to the reference programme yields an executable programme; 100% of grammar-valid edits compile.
- **A3** Distance metric: `distance` satisfies identity, symmetry, triangle inequality on 10⁴ random edit pairs.
- **A4** Prefix code: `code_length` satisfies Kraft's inequality over the enumerable edit space.
- **A5** Collateral derivation: `descendants` matches hand-computed reachability on the reference DAG.

### 6.2 Posterior engine (each stage independently)

- **A6 Likelihood estimation.** On synthetic cases with analytically tractable likelihoods (homogeneous Poisson, Poisson mixture), estimated log-likelihood is within 2 MC standard errors of exact, across 500 trials.
- **A7 Monte Carlo error.** Reported standard error has correct empirical coverage: across 500 repeats, true value falls within ±2 SE at least 93% of the time. Entropy estimates use the Miller–Madow correction; verify residual bias is under 5% of between-hypothesis separation on cases with known entropy.
- **A8 Posterior calibration.** On 200 DEV scenarios with known ground truth, credible intervals achieve nominal coverage; reliability diagram deviation under 0.05 expected calibration error. **This gates every downstream metric.**
- **A9 Posterior predictive checks.** On 100 correctly-specified scenarios, PPC false-positive rate within 2× nominal α. On 100 misspecified scenarios, detection power reported per defect type. **The measured Stage A detection rate is the number the LLM must not be credited for.**
- **A10 Hypothesis-space expansion.** Adding a hypothesis mid-investigation yields the same posterior as including it from the start, to within MC error, on 50 constructed cases. This is the direct test of F9's "no disadvantage from lateness".
- **A11 Retrospective re-evaluation.** Retro-evaluation of a new hypothesis against N prior experiments produces likelihoods matching a from-scratch run, and the reported `ExpansionCost` matches measured simulation calls.

**Staging rule:** `EmpiricalTableEngine` must pass A6–A11 on the slice's closed set before any agent runs. `LikelihoodFreeEngine` must pass the same suite, plus agreement with the empirical engine on the slice within MC error, before it replaces it. Agent results obtained under a failing engine are void.

### 6.3 Registry and partitions

- **A12** Append-only: no code path updates or deletes a registered row; verified by schema inspection and a fuzz test.
- **A13** Content addressing: identical (env version, config, data version, metric version, seed) yields identical hash; any change yields a different one.
- **A14** Partition isolation: static analysis confirms no call path from the agent tool surface to HOLDOUT or TEST.
- **A15** Reproducibility: 100 registered experiments rerun bit-identically. Anything under 100% is a framework bug, not a result.

### 6.4 Hypothesis and schema validation

- **A16** Falsifiability: hypotheses whose `refutation` is unsatisfiable over the diagnostic range are rejected. Verified against 50 hand-constructed unsatisfiable cases.
- **A17** Plausibility immutability: no agent-accessible path writes `plausibility`. Static analysis plus runtime assertion.
- **A18** Duplicate detection: structurally identical edit sets are detected as duplicates on 200 constructed pairs including permutations.

### 6.5 Verifier

- **A19** Numerical: 200 claims with deliberately corrupted figures are all rejected.
- **A20** Evidence completeness: on 100 constructed cases with a deliberately omitted relevant experiment, the relevance query surfaces it in every case. Relevance relation as specified in §7.
- **A21** Causal licensing: 100 constructed intervention/estimand pairs, half misaligned. Zero misaligned pairs licensed; zero aligned pairs rejected.
- **A22** Prospective confirmation: confirmatory claims resting only on retrospective evidence for a late-proposed hypothesis are rejected. 50 constructed cases.
- **A23** Coverage: at least 90% of agent claims in slice runs adjudicated without human input.

### 6.6 BOED

- **A24** On scenarios with computable optimal policies, one-step-greedy BOED's realised information gain is within 10% of the greedy optimum. Confirms the implementation is a fair baseline rather than a straw man.

---

## 7. Relevance relation, and causal licensing

### 7.1 Registry-relative evidence completeness

Experiment E is relevant to claim C if any of:

1. E's target hypothesis is within 2 edges of C's subject in the hypothesis graph
2. E's metric set intersects C's evidence metric set
3. E's scope overlaps C's scope in family, parameter range, or environment version
4. E's intervention manipulates a component in C's causal target set
5. E instantiates the same template as any cited experiment
6. E's target is `AlternativeTo` or `Contradicts` C's subject

Relevance is computed at C's declared metric and grammar versions. Experiments under superseded versions are surfaced as **relevant-but-version-mismatched**, a third category, reported rather than silently resolved.

**What is verified:** every registry experiment satisfying this query is cited. **What is not:** that no uncited experiment is scientifically relevant. That is not formalisable and the name reflects it.

### 7.2 Causal licensing by estimand

The verifier licenses `modality="causal"` only if intervention, graph structure, assumptions and estimand align:

| Claimed estimand | Requirement |
|---|---|
| **Total effect** of target on outcome | Intervention manipulates target. Collateral effects permitted; they are part of the total effect |
| **Direct effect** | Intervention manipulates target; all mediating paths in the programme DAG are declared blocked; blocking assumptions listed in `assumptions` |
| **Controlled direct effect** | As above, and the held-fixed components were actually held fixed in the executed experiment |
| **Path-specific effect** | The claimed path exists in the DAG; off-path descendants are either not manipulated or explicitly controlled |

**Broad interventions license broad claims.** If `manipulated` covers three components, a total-effect claim about their union is licensed; a component-specific direct-effect claim is not. Misalignment triggers automatic downgrade to the strongest licensed estimand rather than rejection, because the weaker claim is usually true and rejecting outright would push the agent away from causal language entirely.

---

## 8. Scoring: six dimensions, task-dependent primacy

Reported separately, never collapsed into one number.

| Dimension | Computation |
|---|---|
| **D1 Structural edit recovery** | `grammar.distance(proposed, true)` |
| **D2 Held-out predictive adequacy** | Posterior predictive score on diagnostics unused during the investigation |
| **D3 Intervention-response similarity** | Divergence between candidate and true programme across a held-out intervention battery |
| **D4 Explanatory coverage** | Likelihood improvement on previously poorly-explained registered results |
| **D5 Enabled experiment value** | Expected information gain of the best experiment the proposal makes available |
| **D6 Complexity** | `code_length(proposed)` |

**Primary interpretation by task:**

- **Closed-world (S1–S10):** hierarchical proper score over the closed set. D1–D6 not applicable.
- **Out-of-library (S11):** **D3 and D2 primary, D1 secondary.** A structurally different but interventionally equivalent explanation is a legitimate scientific success and must not be scored as a failure. This is R7.
- **Compound (S8):** D1 primary, since decomposition accuracy is the capability under test.

Report D1 through D6 as a vector in all cases. Any headline figure cites which dimension it is.

---

## 9. First experiment matrix

Small and interpretable. Correction 8 applied.

**Core systems on all twelve slice scenarios:** V1, V7, B4, B5.
**Stage A only:** B1, on S9 and S11.
**Targeted ablation:** V3 versus V4, on S8, S11, S12 only.

| | S1–S4 | S5–S7 | S8 | S9 | S10 | S11 | S12 |
|---|---|---|---|---|---|---|---|
| V1 BOED | ● | ● | ● | ● | ● | ● | ● |
| V7 Hybrid | ● | ● | ● | ● | ● | ● | ● |
| B4 Retrieval | ● | ● | ● | ● | ● | ● | ● |
| B5 Beam search | ● | ● | ● | ● | ● | ● | ● |
| B1 PPC-only | | | | ● | | ● | |
| V3 raw history | | | ● | | | ● | ● |
| V4 graph | | | ● | | | ● | ● |

Twenty seeds per cell. Roughly 1,100 investigations. Days of compute on the toy environment, not weeks.

**Preregistered primary contrast (slice-level, exploratory):**

> On S11 Stage B, conditional on inadequacy detection, does V7 exceed **B4** on D3 (intervention-response similarity)?

B4 is designated in advance as the comparator because it is the baseline most likely to deflate the claim. Slice results are exploratory by construction and inform the frozen campaign; they are not reportable as confirmatory findings.

**Headline claim, preserved narrow:**

> Conditional on conventional detection that the current model space is inadequate, does an LLM-guided hypothesis-expansion system propose useful executable explanatory structure more effectively than retrieval, symbolic search and fixed-expansion baselines?

It does not depend on every agency metric, every scenario class, every provider, or market transfer. Those are secondary.

---

## 10. Repository layout

```
sciagent/                       # domain-independent. No market imports, ever.
  core/
    program.py                  # GenerativeProgram, Component
    edits.py                    # Edit types, EditGrammar, prefix code, distance
    types.py                    # Claim, Diagnosis, Prediction, Estimand, Scope
    environment.py              # Environment protocol
  registry/
    store.py                    # append-only, content-hashed SQLite
    metrics.py                  # versioned metric registry
    partitions.py               # exploratory / confirmatory / holdout
    budget.py
  hypothesis/
    graph.py
    validator.py                # falsifiability, duplicates
  inference/
    interface.py                # PosteriorEngine protocol
    empirical.py                # slice engine
    likelihood_free.py          # later
    ppc.py
    entropy.py                  # Miller-Madow correction
  experiments/
    dsl.py
    executor.py
    boed.py
  systems/
    base.py                     # ResearchSystem protocol
    hybrid.py                   # V7
    llm_only.py                 # V6, later
    baselines/
      boed_only.py              # V1
      retrieval.py              # B4
      beam_search.py            # B5
      ppc_only.py               # B1
  verify/
    numerical.py  completeness.py  scope.py
    statistical.py  logical.py  contradiction.py  causal.py
    relevance.py
  eval/
    scenarios.py  scoring.py  agency.py  campaign.py

environments/
  pointproc/                    # the slice
    program.py  components.py  diagnostics.py  grammar.py  scenarios.py
  market/                       # phase 6+
    ...
recorder/                       # runs from day one, independent of everything
tests/
  acceptance/                   # A1-A24, one module per subsystem
```

`sciagent` must not import from `environments`. Enforced by a test.

---

## 11. Ordered build backlog

| # | Item | Gate |
|---|---|---|
| 1 | Recorder to EC2 and S3 | Running, gap detection live. **Do this first; it is the only item with a clock** |
| 2 | `core/program.py`, `core/edits.py`, prefix code | A1–A5 |
| 3 | `environments/pointproc`: reference programme, four mechanisms, diagnostics | A1, A2 |
| 4 | `registry/` | A12–A15 |
| 5 | `hypothesis/` | A16–A18 |
| 6 | `inference/empirical.py`, `ppc.py`, `entropy.py` | **A6–A11. Nothing proceeds until these pass** |
| 7 | `experiments/dsl.py`, `executor.py` | Integration test |
| 8 | `experiments/boed.py` | A24 |
| 9 | Baselines B1, B4, B5 | Run on S1–S10 |
| 10 | `verify/` all six classes plus causal | A19–A23 |
| 11 | Slice scenarios S1–S12 with oracle policy lengths | Exhaustive DP where tractable, planning-baseline lower bound otherwise |
| 12 | `systems/hybrid.py` (V7) and the LLM proposal layer | Slice completion criteria |
| 13 | V3/V4 memory ablation | R2 |
| 14 | Agency metrics | Computed over slice runs |
| 15 | First experiment matrix | §9 |

Items 2 through 11 contain no LLM. That is deliberate: **the entire evaluation apparatus is validated before the agent exists**, so agent performance is never confounded with framework immaturity.

---

## 12. Definition of "vertical slice complete"

All must hold.

**Infrastructure**
1. A1–A24 passing
2. `sciagent` has zero imports from `environments`
3. 100% reproducibility across 100 reruns

**Capability** (V7 versus baselines, 20 seeds, S1–S12)
4. Detects inadequacy on S11 at a rate at least matching B1
5. Proposes an S11 extension exceeding B6-equivalent random structured generation on D3, with a non-overlapping 95% interval
6. Achieves a discriminating three-stage plan on at least two of S5–S7
7. Recovers the correct diagnosis on S12 after the garden-path signal
8. Zero graph contradictions and zero zombie hypotheses across all runs
9. Correct abstention on S9 and S10: null and abstain mass exceeding any single defect's mass

**Methodology**
10. At least 90% of claims adjudicated by the verifier without human input
11. Autonomy fraction reported for every investigation
12. Interface changes required for scale-up documented

**Explicitly not required:** beating B4 or B5. That is the research question, not an exit criterion. **If V7 loses to B4, the slice is still complete** and R1 has an early answer worth having.

---

## 13. After this document

Design is frozen. New ideas enter `BACKLOG.md` with a rationale and a note on which frozen decision they would touch. Architecture changes only on a demonstrated contradiction: a case where two frozen decisions cannot both be satisfied, documented with the failing test.

The known backlog at freeze: full likelihood-free engine, two-step-lookahead BOED, remaining variants and baselines, the 104-scenario benchmark, grammar sensitivity analysis (R5), the Rust market environment, real-data grounding, identifiability work, multi-provider evaluation, independent human study.

**The next action is item 1: put the recorder on a websocket feed.** Everything else can start whenever. That cannot.

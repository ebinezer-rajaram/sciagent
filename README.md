# sciagent

**Can an LLM agent discover a mechanism that isn't in its library?**
A benchmark for LLM research agents in which the ground truth is a hidden
structural change to an executable model of the world, and every number is
computed by the framework, never by the agent.

> **Status (October 2026): v2 is being built.** v1 ran to completion (1,120
> preregistered investigations) and is frozen at the tag
> [`v1.0`](https://github.com/ebinezer-rajaram/sciagent/tree/v1.0). Its results,
> and why they led to v2, are below. The v2 design is in
> [`docs/SPEC.md`](docs/SPEC.md).

## Why there is a v2: an exact tie that turned out to be forced

v1 asked whether, once statistics has detected that a model space is
inadequate, an LLM proposes better new structure than retrieval, symbolic search
and fixed-expansion baselines. I ran the full preregistered matrix: seven
systems, twelve scenarios, 1,120 investigations, with Claude as the proposer.

- **Detection worked.** On the out-of-library scenario, a posterior-predictive
  probe flagged inadequacy on **85%** of runs, with a pooled false-positive rate
  of **3.1%** (5/160).
- **The preregistered contrast tied exactly.** The LLM system and retrieval
  both scored **0.6970** on intervention-response similarity, with a paired
  difference of **0.0000 [0.0000, 0.0000]** (n = 17).
- **The tie was forced, and the framework could show why.** Two walls made the
  task unwinnable for *every* system:
  - **A vocabulary wall.** The agent's proposal menu could not express the true
    mechanism (event *size* driving *arrival rate*). In closed form, every
    expressible proposal sat at structural distance ≥ 1.5 from the truth, while
    the always-available null sat at 1.0.
  - **An evidence wall.** The one diagnostic carrying the truth's signature was
    available in 112 of 112 LLM briefs and *observed* in none. Bayesian
    experimental design ranks experiments by information gain over the
    hypotheses already entertained, and none of them made that diagnostic
    informative.
- **The LLM changed nothing anywhere.** Re-reading the ledger, the LLM system
  matched the no-LLM system on five of six scores in all twelve scenarios. Eight
  scenarios were solved by the library before any proposal was made, and the
  LLM was a one-shot proposer with no tools, choosing among five structural
  cells.

So v1's question was left **open, not answered "no"**. A benchmark that scored
against labels would have reported "LLM ≈ retrieval" and stopped; scoring
against structure is what made the cause visible. The details are in
[`docs/v1/RESULTS.md`](docs/v1/RESULTS.md).

## v2: open-world mechanism discovery, by agents

v2 fixes each cause rather than patching the instrument.

**An open hypothesis space.** Mechanisms are point-process GLMs,
`λ(t | history) = g(θ₀ + Σ θₖ φₖ(t))`. The agent proposes the *features* φₖ
(excitation kernels, mark functions, gates, interactions), and the framework
fits θ. Every fit is a convex problem solved with a **duality-gap certificate**,
so a proposal can only lose because it is a worse structure, never because an
optimiser got stuck.

**A population of hidden truths.** Instead of one out-of-library scenario,
truths are sampled from the feature grammar, held out from the agent's library,
and stratified by how far they lie from it. Results come out as a curve over
distance from the library.

**Real agents, in two tiers.** Both run Claude through the Agent SDK with tools:
run experiments in an intervention language, query diagnostics, fit, commit
predictions, and submit.
- The **constrained** agent's only way to evaluate a structure is a metered
  fit, so it can be compared with symbolic search at an **equal fit budget**.
- The **open** agent adds a Python sandbox and a lab notebook: the research
  scientist condition. It is compared at an **equal experiment budget**.

**Rivals chosen to be hard to beat.**
- symbolic search over the same grammar;
- sparse regression (group lasso) over a large fixed feature dictionary;
- a model-free **Wiener–Hopf** estimate of the excitation kernels;
- retrieval;
- Bayesian experimental design;
- random proposals;
- an oracle.

**Questions v2 is built to answer:**
1. Do agents beat search at equal budget?
2. Do they still do so with every domain-meaningful name **anonymised**?
   This separates reasoning from recall.
3. Does an agent that chooses its own evidence get past the evidence wall that
   stopped Bayesian experimental design?
4. Is falsification-seeking experiment design, a method proposed here, better
   than information gain in the open world?
5. On a real earthquake catalogue, does the agent arrive at magnitude-dependent
   triggering?
6. Does it behave like a scientist? Its committed predictions are scored for
   calibration, falsification-seeking and revision, with no LLM judge.

**v1's lesson, as a rule.** No comparison is reported unless a positive control
has shown the instrument can separate the systems being compared.

## Roadmap

| Phase | What | Status |
|---|---|---|
| P0 | Freeze v1, delete what v2 doesn't use, rebuild the workflow minimal | done |
| P1 | Feature grammar, exact likelihood, certified fitting, Wiener–Hopf, evidence-wall proposition | next |
| P2 | Truth sampler, intervention language, agent harness, sandbox, anonymiser | |
| P3 | Pilot with go/no-go criteria fixed in advance | |
| P4 | Main campaign: Haiku / Sonnet / Opus, named vs anonymised | |
| P5 | Real seismicity catalogue | |
| P6 | Write-up | |

## Environments

**`pointproc`** is a simulated marked point process
(`arrival → {size, sign} → obs`). Four mechanisms (Hawkes self-excitation,
latent regime switching, seasonality and a Poisson mixture) are calibrated to the
same operating point, so **no single statistic separates them**.

**`qtm`** ingests a byte-pinned catalogue of **real Southern California
seismicity** behind the same event-log interface.

## Quickstart

```sh
uv sync
uv run pytest -n 4 --dist loadfile    # ~25 s
```

Apply a structural edit to the reference programme, execute it, and measure it
in edit space:

```python
import numpy as np

from environments.pointproc import edit_grammar, mechanism_defect, reference_program
from sciagent.core.types import ComponentId, Seed

program = reference_program()
grammar = edit_grammar()

defect = mechanism_defect("hawkes")
defective = grammar.apply(program, defect)

log = defective.execute(Seed(20260801), n_events=2000)
gaps = np.diff(log.values[ComponentId("arrival")])
print("dispersion:", round(float(gaps.var() / gaps.mean() ** 2), 3))
print("code_length:", grammar.code_length(defect), "bits")
print(
    "distance to seasonality:",
    round(grammar.distance(defect, mechanism_defect("seasonality")), 3),
)
print("collateral:", sorted(defective.descendants(ComponentId("arrival"))))
```

```
dispersion: 3.273
code_length: 24.0 bits
distance to seasonality: 1.5
collateral: ['arrival', 'obs', 'sign', 'size']
```

Same seed, same bytes, on every run.

## Engineering

- **Bit-exact determinism.** Same seed + config + version ⇒ byte-identical
  output, checked across processes and hash seeds. All randomness flows through
  explicitly passed seeded generators, and an AST-level test, run by a hook
  after every edit, forbids global RNG use.
- **The framework writes numbers; agents write structure.** No path an agent
  can reach may set a score, a fitted parameter of the submitted model, or
  held-out data.
- **Append-only, content-addressed results.** Every result is keyed by a hash
  of (environment version, config, data version, metric version, seed), and the
  store has no update or delete path.
- **Replayable LLM runs.** Recorded calls are addressed by hash, so a campaign
  replays offline with no network.
- **Preregistration.** Contrasts, budgets and the test-set hash are committed
  before an agent touches the test split.
- `mypy --strict`, `ruff`, and property-based tests with Hypothesis. The
  `sciagent` package is domain-independent and never imports `environments`; a
  test enforces it.

Built with [Claude Code](https://claude.com/claude-code) as pair programmer.

## Layout

```
src/sciagent/        domain-independent framework; never imports environments
  core/              programmes, edits, grammar, prefix code, distance
  registry/          append-only content-addressed store
  experiments/       experiment DSL and executor (to be replaced in P2)
  systems/llm/       LLM backends with record/replay
src/environments/
  pointproc/         simulated marked point process
  qtm/               real seismicity catalogue, found-data ingestion
tests/               fast tier by default; `-m slow` for simulation-heavy tests
docs/SPEC.md         the v2 design
docs/v2/LOG.md       v2 design decisions
docs/v1/             v1's spec, results and full decision record
```

## Data

The `qtm` environment uses the QTM catalogue: Ross, Z. E., Trugman, D. T.,
Hauksson, E. & Shearer, P. M. (2019), *Searching for hidden earthquakes in
Southern California*, Science 364, 767–771. Data from the Southern California
Earthquake Data Center (SCEDC, doi:10.7909/C3WD3xH1) and the Southern
California Seismic Network (SCSN, doi:10.7914/SN/CI). The catalogue is not
redistributed here; `scripts/fetch_qtm.py` downloads it from SCEDC and checks it
against pinned SHA-256 checksums.

## Licence

[Apache 2.0](LICENSE) © Ebinezer Rajaram

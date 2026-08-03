# sciagent

A research framework for evaluating **constrained agentic scientific
investigation under model misspecification**.

The question it exists to answer, kept deliberately narrow:

> Conditional on conventional detection that the current model space is
> inadequate, does an LLM-guided hypothesis-expansion system propose useful
> executable explanatory structure more effectively than retrieval, symbolic
> search and fixed-expansion baselines?

Most "AI scientist" evaluations score a system against a label or a human
rubric. This one does not. Environments here are **compositional executable
programmes**; defects are **typed structural edits** to those programmes; the
ground truth of a scenario is the edit set that was applied, not a string. A
research system investigates the defective programme and its diagnosis is
scored against the edit — structurally, predictively, and interventionally.

The full design is `docs/SPEC.md`. It is frozen. Read it before doing anything
non-trivial.

## Status

The build state is derived from the repository, never hand-maintained here:

```sh
uv run python scripts/status.py          # SPEC §11 cursor, A-gate coverage (~1s)
uv run python scripts/status.py --run    # verify gates by execution, not by existence
```

Without `--run` the report says only which gates have tests *written*; it never
claims a gate passes. `docs/DECISIONS.md` holds what the repository cannot tell
you — spec ambiguities and their resolutions, approaches abandoned, measured
numbers that are expensive to reproduce, and work left deliberately incomplete.

## The idea

Four claims carry the design.

**Ground truth is a structure, not a label.** A defect is a `frozenset` of edits
drawn from a versioned `EditGrammar`. "Correct diagnosis" is therefore a
distance in edit space rather than a match against an answer key, and partial
credit means something specific.

**The prior is derived, not fitted.** `EditGrammar.code_length` is a prefix code
over the edit space, fixed before any scenario exists, satisfying Kraft's
inequality. The structural-complexity prior is `p(D) ∝ 2^(−L(D))` — a statement
about parsimony derived from the representation, not from how often each defect
happens to appear in the benchmark. It is never tuned.

**Out-of-library is mechanical.** Each environment declares two grammars. A
scenario is out-of-library exactly when its ground-truth edit lies in
`edit_grammar() \ agent_grammar()`. No judgement call, no annotator.

**The framework writes numbers; agents write structure.** No code path reachable
from an agent may set a plausibility, a posterior value, a metric definition or
a score. An agent proposes executable structure and prose; every number attached
to it is computed by machinery the agent cannot reach.

The evaluation apparatus is built and validated *before* the agent exists —
items 2 through 11 of the build backlog contain no LLM at all — so that weak
agent performance can never be confounded with framework immaturity.

## The vertical slice

One environment, `environments/pointproc`: a marked point process with four
components (`arrival → {size, sign} → obs`).

Onto it, four mechanisms, one per edit type, each calibrated to a common
operating point (mean rate 1.0, inter-arrival dispersion 3.5) so that no single
moment separates them:

| Mechanism | Edit type | Discriminated by |
|---|---|---|
| Hawkes self-excitation | `AddDependency(arrival → arrival)` | a forced arrival raises the subsequent rate |
| Latent regime switching | `AddLatentVariable(arrival, TwoStateMarkov)` | geometric run lengths; conditioning on the inferred state |
| Deterministic seasonality | `ReparameteriseComponent(arrival, PeriodicRate)` | phase-conditioning removes the overdispersion entirely |
| Independent Poisson mixture | `ChangeDistributionFamily(arrival, MixtureOfPoisson)` | flat Fano profile; no temporal correlation; no response to intervention |

The confounding is measured rather than asserted — `scripts/confounding_check.py`
prints the full separability table, and the numbers are recorded in
`docs/DECISIONS.md`. Discriminating the four takes a minimum three-stage plan;
no single diagnostic resolves them.

Twelve scenarios sit on this environment (SPEC §4.5). Two carry the slice's
weight: **S11**, whose ground truth is out-of-library by construction, and
**S12**, a garden path where the first two diagnostics point confidently at the
wrong mechanism.

## Quickstart

```sh
uv sync
uv run pytest
```

Apply a defect to the reference programme, execute it, and score the edit:

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

`arrival` appears in its own descendant set because the Hawkes edit adds a
lagged self-dependency; collateral for an intervention is
`descendants(target) - {target}`.

## Layout

```
src/sciagent/         domain-independent framework. Never imports environments
  core/               programmes, edits, grammar, prefix code, distance
  registry/           append-only content-addressed store, partitions   (item 4)
  hypothesis/         graph, falsifiability and duplicate validation    (item 5)
  inference/          posterior engines, PPC, entropy                   (item 6)
  experiments/        experiment DSL, executor, BOED                    (items 7-8)
  systems/            V7 hybrid and the B1/B4/B5 baselines              (items 9, 12)
  verify/             six mechanical claim-check classes plus causal    (item 10)
  eval/               scenarios, six-dimension scoring, agency, campaign
src/environments/
  pointproc/          the slice: reference programme, mechanisms, diagnostics
scripts/              status, mechanism calibration, confounding check
tests/acceptance/     A1-A24, one module per subsystem
docs/                 SPEC.md (frozen), DECISIONS.md, BACKLOG.md
```

Directories without a source file yet are backlog items, ordered in SPEC §11.

## Invariants

Violating any of these is a bug regardless of whether tests pass.

1. **`sciagent/` never imports from `environments/`.** The framework is
   domain-independent, and a test enforces it.
2. **The framework writes numbers; agents write structure.** Enforced with
   runtime assertions, not comments.
3. **Bit-exact determinism.** Same seed + same config + same version ⇒
   byte-identical output, across processes. All randomness flows through
   explicitly passed seeded generators — never the `random` module, never
   `np.random`'s global functions. Nothing affecting output may depend on dict
   or set iteration order.
4. **The registry is append-only.** No update or delete path exists.
5. **Acceptance tests are the contract.** A1–A24 in SPEC §6. A subsystem is not
   done until its gate passes, and no work proceeds past a failing gate.

Acceptance tests are named for their criterion — `test_a7_...` inside a class
`TestA7MonteCarloError`. That naming is load-bearing: `scripts/status.py`
derives gate coverage from it, and a test named otherwise is invisible to the
report.

## Development

```sh
uv sync                              # install; never call pip
uv run pytest                        # 201 tests, ~36s
uv run mypy --strict                 # must be clean
uv run ruff format . && uv run ruff check --fix .
```

`docs/` is excluded from ruff: `SPEC.md` contains Python blocks that formatting
would silently rewrite, and it must not change.

Python 3.12+, `uv` for dependencies, `numpy` and `scipy` only. Strict typing
throughout, `@dataclass(frozen=True)` for value types, no mutable global state,
no I/O in `core/`.

## Contributing

The design is frozen (SPEC §13). New ideas go to `docs/BACKLOG.md` with a
rationale and a note on which frozen decision they would touch. Architecture
changes only on a demonstrated contradiction — a case where two frozen
decisions cannot both be satisfied, documented with the failing test.

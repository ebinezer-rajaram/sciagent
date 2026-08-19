# Interface changes required for scale-up

SPEC §12 criterion 12 asks that the interface changes required for scale-up be
documented. This is that document. The scale-up in question is the one named in
SPEC §13's known backlog at freeze: the **104-scenario benchmark**, against the
twelve-scenario slice (S1–S12) the framework has actually been run on.

Nothing here is a proposal to change a frozen decision. SPEC §13 allows
architecture changes only on a demonstrated contradiction, and none of the six
items below is one. Each is a place where the slice *fits inside* a contract that
104 scenarios would not fit inside, and each entry names the contract that has to
move. Where an item already has a `docs/BACKLOG.md` entry with its own rationale
and measurements, this document cites rather than restates it — what it adds is
the interface consequence, which the entries mostly leave implicit because at
twelve scenarios there is not one.

The six are ordered by how early they bind. The first three are the ones SPEC §12
criterion 12's gate names.

---

## 1. Stage A becomes a battery, and the combination rule becomes part of §4.6

**What the slice does.** Stage A is one adequacy probe —
`query:size_gap_correlation` — run by the framework before the system sees
anything, carried on `Scenario.stage_a` as a single `ExperimentDesign | None`.

**Why twelve scenarios did not expose the problem.** The probe was built for
S11's mechanism, and §12 criterion 4 is about S11, so a probe aimed at S11
answers the question the slice asks. Measured at 100 scenarios per arm, alpha
0.05: scoping the check to the probe takes power against `size_excitation` from
52% to **90%**, and takes power against `size_mixture` — a misspecification in a
direction the probe does not measure — from 100% to **3%**.

**Why 104 scenarios break it.** The misspecification directions are not known in
advance, so a single-direction gate reports a detection rate that means nothing.
Every Stage A detection figure currently in this repository is a statement about
the mark-arrival direction alone.

**The interface change.** `Scenario.stage_a` becomes a *sequence* of designs, and
SPEC §4.6 — which describes Stage A as a check rather than a battery — gains a
stated combination rule. The rule is the design and not a detail of it: the
`1 + ln(n)` dilution is why eight readings do worse than one on
`size_excitation`, so a minimum-p with an explicit correction, or a
per-direction verdict reported separately, is a different contract from a
harmonic mean and has to be chosen deliberately rather than on the way past
other work. `PPCResult` gains a per-direction field, and the ledger payload's
single `inadequate` flag becomes one flag per probe plus a combined verdict.

Full rationale and the measurement: `docs/BACKLOG.md`, *"A Stage A battery,
because one probe only looks in one direction"*.

---

## 2. `ENV_VERSION` becomes the content hash SPEC §3.2 already promises

**What the spec says.** SPEC §3.2 declares the field as
`version: EnvVersion  # content hash of code + reference programme`.

**What the code does.** It is a declared string, assembled from three
hand-maintained constants:

```python
ENV_VERSION = EnvVersion(
    f"pointproc/{GRAMMAR_VERSION}+{LIBRARY_VERSION}+{OPERATIONS_VERSION}"
)
```

The promise is **not yet** kept. Between them the three cover every construct a
programme can hold, every semantics it can be executed under, and every act that
can be performed on it — which is a careful decomposition and still a manual one.

**Why twelve scenarios did not expose the problem.** One environment, one author,
and a small enough surface that a mechanism change and its version bump are in
the same diff and the same head.

**Why 104 scenarios break it.** The registry is content-addressed by
`(env version, config, data version, metric version, seed)`, and the empirical
table cache is keyed the same way. A mechanism bugfix without a manual bump
silently reuses stale 2000-replicate rows — across every worktree and every
machine, because all trees share one cache — and every content address stays
fixed while the numbers behind it move. At one environment that risk is carried
by one person's attention; at 104 scenarios over several environments it is
carried by nobody.

**The interface change.** `EnvVersion` stops being an author-supplied string and
becomes derived: a digest over the environment's own module sources plus the
serialised reference programme. That changes the field's *contract* — from
"declare this and remember to bump it" to "this is computed, and a code change
moves it whether you meant it to or not" — and it retires every cached table and
every registered row at the moment it lands, so it rides a deliberate
re-derivation rather than an ordinary change. `docs/BACKLOG.md`'s *"The
verification substrate has unversioned randomness, caches and dependencies"*
entry (gate A33) carries the weaker interim form: fold a simulator-code digest
into the *cache* key, leaving the registry address alone.

---

## 3. The `Environment` protocol: dissolved into convention, and what that costs

**What F4 gives.** *"One `Environment` interface. Every environment implements
it. All evaluation apparatus is domain-independent above it."* SPEC §3.2 spells
the protocol out: `version`, `reference_program`, `edit_grammar`,
`agent_grammar`, `diagnostics`, `interventions`.

**What exists.** No `class Environment(Protocol)` is defined anywhere in `src/`.
The slice supplies exactly that surface as module-level functions —
`program.reference_program`, `grammar.edit_grammar`, `grammar.agent_grammar`,
`catalogue.metric_registry`, `outcomes.slice_designs`, `outcomes.ENV_VERSION` —
and the framework reaches none of them: `sciagent` never imports
`environments`, and every environment-shaped input arrives through a callback
(`run_matrix`'s `execute`, `scenario_seed` and `battery`) or through a plain
argument. The protocol was **dissolved into convention**, and the convention has
held because there is one environment to hold it.

**Why that was right for the slice.** A protocol with one implementation is a
contract nobody can violate and nobody can check. The callback style is what
keeps invariant 1 mechanical rather than aspirational, and it is tested.

**Why 104 scenarios change the calculation.** The benchmark is the point at which
a second environment exists — SPEC §13 names the Rust market environment and
real-data grounding — and "the same surface, by convention" stops being
checkable exactly when it starts being load-bearing. A second author supplying
`agent_grammar` with subtly different semantics for out-of-library would move
every result and break no test.

**The interface change.** Either the protocol is written as F4 says, as a
`Protocol` in `sciagent.core` that environments structurally satisfy — with
`environments/pointproc` gaining a thin object that binds the existing module
functions — or F4's wording is amended to describe the callback convention that
was actually built. The first is the smaller change to the spec and the larger
change to the code; the second is the reverse. What is not available at 104
scenarios is leaving the spec describing an interface the code does not have.

---

## 4. Model tier as a preregistered axis

The matrix's V7 arm runs at one model tier. Running it at several — same
scenarios, same seeds, same grammar, tier declared in advance — is a direct test
of the framework's central claim, since the division of labour leaves the model a
narrow job and a cheaper tier may hold it.

**Interface consequence: none, and that is the finding.** The tier already
reaches the transcript address through `Provider.model`, so tiers cannot
contaminate each other's recorded calls, and `Provider.settings` keeps effort
separate from tier. This item is here because a document about scale-up that
omitted it would imply an interface change is needed where none is. What it
needs is sequencing and budget, not a contract change. See `docs/BACKLOG.md`,
*"Model tier as a preregistered evaluation axis"*.

---

## 5. Grammar sensitivity (R5) becomes a reported axis, not a footnote

R5 asks how much the ranking depends on the edit grammar. On S11 a plain Hawkes
proposal is 1.50 from the truth under `grammar.distance` while the null is 1.00 —
so a system proposing the mechanism that reproduces S11's interventional
behaviour almost exactly scores *worse* structurally than one proposing nothing.
D1 is `grammar.distance` and nothing else, so that number is a statement about
the grammar rather than about the world. SPEC §0 concedes the general point and
says the improvement is that the grammar "can be varied in sensitivity
analysis"; R5 is the only thing that keeps that promise.

**The interface change.** The grammar becomes a *swept coordinate* rather than a
fixed input. `GrammarVersion` already exists and already reaches the reported
figures, but no address distinguishes two readings of one cell under coarse and
fine grammars — `CampaignAddress` carries env, data and metric versions and not
this one. Reporting the spread means the grammar joins the cell address on the
same footing as `DIMENSION_VERSION` and the held-out battery, and the report
layer gains a per-grammar column that §8's no-collapse rule forbids averaging.

---

## 6. The likelihood-free engine, and F12's staging rule

F12: *"The posterior engine is staged: empirical table first, full
likelihood-free engine later, each independently validated before agent results
depend on it."* Stage one is `EmpiricalTableEngine`, and it is what every
recorded number rests on.

**Why the second stage is a scale-up item.** The empirical table is a
discretised likelihood over a fixed design set, built by simulation at 2000
replicates per (structure, design) pair. Its cost is the product of the
structures entertained and the designs offered, and 104 scenarios multiply both.
The table is also why the slice can be exact where a general engine would
approximate, so replacing it is a change in what the numbers *are*.

**The interface change, and the constraint on it.** `PosteriorEngine` is already
a `Protocol` in `sciagent.inference.interface`, so a second engine is a new
implementation rather than a new contract — this is the one item where the
interface was built for the change in advance. What F12 constrains is the
*order*: the second engine is validated independently, against the table engine
on the slice where both are defined, **before** any agent result depends on it.
An engine swap concurrent with a benchmark expansion would confound the two, and
that is the same confound invariant 6 exists to prevent — apparatus is fixed
before the systems it grades are scored on it.

---

## What is deliberately not here

**The platform pin.** `docs/DECISIONS.md` (2026-08-15) records a measured
Windows/Ubuntu divergence, and the project chose to run everything on one
platform and say which. That is settled rather than pending, and 104 scenarios do
not reopen it: the pin costs nothing as long as no second platform produces
numbers. It becomes a scale-up item only if the benchmark has to be distributed
across machines, at which point the remedy is the one already named — a platform
term in the content address.

**Item 1's recorder.** Deferred on a recorded decision, and real-data grounding
(gate A25) is data that already exists rather than data to be recorded.

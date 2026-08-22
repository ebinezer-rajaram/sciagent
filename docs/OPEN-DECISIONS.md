# Open decisions

> **Both decisions were taken 2026-08-21** — §1 as **C1**, §2 as **T3**, the
> recommendation each section makes. §2 was briefly recorded as T2 and corrected
> the same day: that reading turned on A40 bumping `METRIC_VERSION`, which
> `DIMENSION_VERSION` had already made unnecessary a day before. T1a's
> fault-column triage survives the correction and rides with T3. All of it is in
> `docs/DECISIONS.md`. The text below is left as it stood when the decisions were
> taken: the record of what was known at the time, not a live question.

Two decisions this repository deliberately did not take while it was measuring,
written up so they could be taken **cold**.

Neither is here because it is hard to find the answer. Both are here because of
CLAUDE.md's invariant 6: they touch apparatus that scores a system already
measured, and a session that has just watched V7 run is the wrong session to
settle what V7 is graded on. `docs/DECISIONS.md` records the repository paying
for that twice — a §12 criterion 4 rewrite drafted 2026-08-16 and reverted
unshipped, and a CLAUDE.md compression that would have re-permitted it.

**Nothing in this file changes anything.** It states the options, what each one
would make the criterion mean, and a recommendation. Neither `docs/SPEC.md` nor
any code is edited by it. When a decision is taken, it goes in
`docs/DECISIONS.md` and the change lands separately.

---

## 1. SPEC §12 criterion 4 is incoherent

> **4. Detects inadequacy on S11 at a rate at least matching B1**

### Why it has no answer

The criterion names no check, and **two checks answer to the name**. They
disagree, and the disagreement is not marginal:

| | fires on |
|---|---|
| full-record `ppc` | B1 on 5 of 12 (S1, S5, S8, S10, S11); V7 on none |
| Stage A adequacy probe | B1 on S11 alone (0.0294); V7 on S11 alone (0.0112) |

The §9 matrix reached this by a second route on 2026-08-18. `report_matrix.py
--contrast` exits 3 with *"no replicate of V7 on S11 detected inadequacy"* —
because `CellReading.inadequate` records the **whole-record posterior predictive
check**, which fired 0 of 20, while V7 acted on the **Stage A gate** in 17 of
those same 20 runs. `eval/campaign.py:104` already says in as many words that
`inadequate` is "**not** the verdict a system acted on where a scenario declares
a Stage A probe". §9's primary contrast conditions on the first; V7 is driven by
the second.

### Three problems, and the wording survives none

1. **It compares different checks.** B1 holds no proposal layer, so it never
   calls `Investigation.ppc()`. Its "detection" can only be read off a run,
   while V7's is a gate it *acts on*. Reading one arm's gate against the other
   arm's full-record summary is not a comparison.
2. **B1's full-record rate is a multiplicity artefact.** B1 holds only the null,
   so its space is inadequate on eleven of twelve *by construction*, and the 5
   of 12 comes from eight experiments agreeing rather than from adequacy
   detection. A bar set there rewards firing indiscriminately.
3. **There is no false-positive term.** As written the criterion measures
   **size**, not power. An arm that fired on all twelve would pass it.

### The fork any fix has to take

The field a fixed criterion would read **does not exist**. A `ScenarioRun.adequacy`
field was written on 2026-08-16 and withdrawn the same day, because the only
place the harness can evaluate it uniformly across arms is *after* `investigate`
returns — which reads the **final** posterior. A system that successfully
proposed a structure explaining the probe then records as having failed to
detect, inverting the criterion. Measured on V7/S11: gated on p=0.0128,
end-of-run 0.0112.

The deeper trouble is that one field is being asked to hold two different
things:

| | **A — is the space adequate** | **B — did this system detect it** |
|---|---|---|
| property of | space and scenario | behaviour |
| arm-symmetric | yes | no |
| computable | by the harness, before the run | only for arms that consult a check |
| defined for B1 | yes | **no** — B1 consults nothing |
| defined for V1 | yes | **no** |

**A re-specification must pick one and say which.** That is the decision.

### Candidate wordings

**C1 — power against size, on the named Stage A probe.**

> On S11, V7's Stage A probe fires at a rate at least matching B1's on the same
> probe, at a false-positive rate on S1–S7 and S9 no higher than B1's.

Reads B, on one named check, with the missing false-positive term supplied.
Costs: undefined for arms with no proposal layer *unless* the harness evaluates
the probe for every arm regardless of whether the arm consults it — which is
possible, since the probe is a function of the run, but means "B1 detected"
describes something B1 never did.

**C2 — adequacy as a scenario property, detection as a separate report.**

> The harness computes space adequacy per (arm, scenario) from the entertained
> set and the truth. Criterion 4 asserts V7's *gate* agrees with that ground
> truth on S11 at least as often as B1's full-record check does, and reports
> the confusion matrix rather than a single rate.

Reads A for the ground truth and B for the behaviour, keeping them in separate
fields. Costs: more machinery, and a confusion matrix is not a pass/fail bar
without a further threshold decision.

**C3 — strike the criterion and report the numbers.**

> Remove criterion 4. Report V7's and B1's detection rates side by side in §9,
> conditioning nothing on them.

Costs nothing to build and answers no question. Worth naming because §12's own
preamble already does this for a harder case — *"Explicitly not required:
beating B4 or B5. That is the research question, not an exit criterion."*

### Recommendation

**C1, with the probe evaluated by the harness for every arm.** It is the
smallest change that makes the criterion mean something, it supplies the
false-positive term whose absence is the clearest defect, and it keeps the
criterion a bar rather than a report. The honesty cost — that "B1 detected"
names a probe B1 never consulted — is real and should be written into §12's
wording rather than left for a reader to discover, which is what the current
version does.

**What makes this decision safe to take now:** the measurements above are all
recorded, and the matrix is frozen. **What makes it unsafe:** taking it in a
session that has just watched V7 pass or fail a candidate wording. This document
exists so the next session does not have to.

---

## 2. The proposal outcome taxonomy

`docs/BACKLOG.md` records that `ProposalRecord.refused` conflates a model
declining with a transport failure. **Transport left the tier on 2026-08-18** —
it now raises `ProviderUnavailableError`, which `Hybrid` does not catch — so that
half of the entry is discharged. What remains is wider than the entry claimed:
**eighteen distinct conditions across the two backends still land in one tier**,
and only two of them are scientific events.

### What is actually in `"refused"`

`src/sciagent/systems/llm/agent_sdk_provider.py`:

| line | condition | scientific event, or fault? |
|---|---|---|
| 267 | inherited env var would change the run | **fault** — the machine is misconfigured |
| 327 | `claude-agent-sdk` not installed | **fault** |
| 361 | called from inside a running event loop | **fault** — caller misuse |
| 405 | stream drained with no `ResultMessage` | transport — **arguably misfiled**, see below |
| 420 | `stop_reason == "refusal"` | **event** — the model declined |
| 426 | `stop_reason == "max_tokens"` | ambiguous — see below |
| 432 | `is_error` or `subtype != "success"` | transport, usually |
| 448 | part of the turn served by a non-`firstParty` provider | **fault** |
| 456 | no per-model usage, so provenance unverifiable | **fault** |
| 462 | served by a model other than the pinned one | **fault** |
| 516 | `structured_output` is `None` despite a schema | malformed response |
| 521 | `structured_output` is not a dict | malformed response |

`src/sciagent/systems/llm/anthropic_provider.py`:

| line | condition | scientific event, or fault? |
|---|---|---|
| 157 | `stop_reason == "refusal"` | **event** |
| 164 | `stop_reason == "max_tokens"` | ambiguous |
| 182 | `anthropic` SDK not installed | **fault** |
| 208 | no text block to read | malformed response |
| 215 | text block is not JSON | malformed response |
| 220 | JSON parsed to a non-object | malformed response |

**Only two of the eighteen are unambiguously scientific events** — the two
refusals. Several rows now look like candidates to follow transport out of the
tier rather than to be recounted within it: `405` and `432` are dead-session
conditions that reached the wrong side of the split because they are detected
from a `ResultMessage` rather than from an exception, and `267`, `327`, `361`,
`448`, `456`, `462` and `182` are faults of the machine that a campaign should
arguably stop on rather than score. Everything else is a fault of the machine, a fault of the transport,
or a malformed response that is arguably `"malformed"`'s business rather than
`"refused"`'s.

`max_tokens` is the interesting one and does not sort cleanly. A model that
cannot finish within the ceiling has told you something about the task; a
ceiling set too low has told you something about your configuration. It is a
scientific event **at a fixed ceiling** and a fault **when the ceiling moves**,
and the ceiling is deliberately not in the transcript address
(`AnthropicProvider.settings`), so nothing in a recorded corpus distinguishes
the two cases.

### Why this cannot be fixed by adding fields

`PROPOSAL_OUTCOMES` is **derived** from `ProposalRecord`'s fields, and
`requested` sums them, so:

```
yield_fraction = admitted / requested
requested      = admitted + duplicate + refused + malformed + unmeasurable
```

Adding a field moves the denominator. `yield_fraction` is what SPEC §12
criterion 11's autonomy fraction reads — **evaluation apparatus for a system
already scored**. Splitting `"refused"` into `refused` / `transport` / `faulted`
would leave the same run reporting a different autonomy fraction than the matrix
recorded, with no change in what the system did.

There is a second, independent hazard already recorded in `docs/BACKLOG.md`: the
break asymmetry. `Hybrid._extend` breaks on `"refused"` and continues on the
other four, so at `max_proposals >= 3` a model unable to produce a second
admission **would score strictly higher by declining**. Harmless at the current
2, where declining can only lower the ratio. Any retiering changes which
outcomes break, so the two decisions are coupled and should be taken together.

### Options

**T1 — leave the counts as they are.** Status quo. `requested` and
`yield_fraction` are untouched, so no recorded result moves.

Do **not** defend T1 on the grounds that the corpus stays diagnosable. An
earlier draft of this section did, on the strength of `detail` now carrying the
underlying exception type — and review showed the claim is false where it
matters: `MatrixRunner.execute` never reads `attempts`, `CellReading.as_payload`
carries no outcome field and no `detail`, and a call that failed stores no
transcript. The `detail` string exists only in the process that produced it. T1
is "change nothing and accept the conflation", which is a defensible position
and a different one.

**T1a — move more conditions out, rather than recounting them.** What the
transport fix did, applied to the rest of the "fault" column above. Costs
nothing in recorded numbers, because a condition that propagates was never
scored; the question each row needs is whether a campaign should stop on it.

**T2 — split the tier, bump the metric version.** Add `transport` and `faulted`
beside `refused`. Honest, and it makes the counts mean what they say. Requires
re-deriving every recorded `yield_fraction`, which is a registry-visible change
under invariant 4 and needs a new metric version.

**T3 — separate the *record* from the *score*.** Keep `ProposalRecord`'s five
fields exactly as they are, so `yield_fraction`'s denominator never moves, and
add a parallel non-scoring breakdown of causes for reporting. Nothing §12
criterion 11 reads changes; the diagnostic question gets its own answer.

### Decided 2026-08-21, with gate A40: T3

Taken cold, as this file exists to allow, and in the window the A40 entry names.
The reasoning below stands; what settled it is that the condition favouring T2
did not arrive. A40 re-derives the recorded matrix under `dimensions` and
`battery` rather than by bumping `METRIC_VERSION` — `eval/scoring.py` records the
measured cost of that bump, which reaches every `Discretisation`'s content hash —
so the metric-version bump T2 needs is not free after all. Nor would
`DIMENSION_VERSION` have covered it: `yield_fraction` is an agency metric, not
one of §8's six dimensions.

Implementation belongs to `docs/BACKLOG.md`'s *"Audit the proposal path's failure
taxonomy"*, not to A40. The decision is what A40 owed; the retiering is that
entry's remaining work.

**Implemented 2026-08-22 at gate A44.** T3 as a parallel `ProposalCauses`
breakdown, T1a as five conditions retiered out of the scored tier. Two rows this
section calls "arguably misfiled" — 405 and 432, the dead-session pair — were
kept **in** the record as the cause `transport`, because A44's gate text says a
dead session reads as transport rather than propagating; that is a decision
against this section's aside and is recorded as one. `max_proposals` stays at 2,
now as a guarded ceiling rather than a default. `max_proposals` stays at 2 until the break asymmetry is
settled in the same change, exactly as below.

### Recommendation, as written before the decision

**T3.** It is the only option that gets the aggregation without moving a number
a frozen matrix has already reported, and the split it draws — what a system did
versus why a call failed — is the same split the framework already enforces
everywhere else. **T2 is the right answer if the matrix is going to be re-run
anyway**, in which case the metric-version bump is free and T3's parallel
structure is redundant; so this decision should be taken *after* the R5
re-scoring question, not before it.

Whichever is chosen, `max_proposals` should stay at 2 until the break asymmetry
is settled in the same change.

# Backlog

Per SPEC §13: the design is frozen. New ideas enter here with a rationale and a
note on which frozen decision they would touch. Architecture changes only on a
demonstrated contradiction — a case where two frozen decisions cannot both be
satisfied, documented with the failing test.

This is not the build backlog. The ordered build backlog is SPEC §11, and its
live cursor comes from `scripts/status.py`. Decisions already taken go in
`docs/DECISIONS.md`.

Entry format:

```
## Short title

**Idea.** What it is.
**Rationale.** Why it might be worth doing.
**Touches.** Which frozen decision it would change, or "none".
```

---

## Known backlog at freeze

Recorded in SPEC §13, restated here so this file is not empty: full
likelihood-free engine, two-step-lookahead BOED, remaining variants and
baselines, the 104-scenario benchmark, grammar sensitivity analysis (R5), the
Rust market environment, real-data grounding, identifiability work,
multi-provider evaluation, independent human study.

---

## Learned experiment-selection policy (RL over the operation set)

**Idea.** A system variant whose experiment selection is a policy learned by
reinforcement learning over the §4.4 operation set, in place of one-step-greedy
BOED. State is the hypothesis graph plus registered results; actions are the
typed operations; reward is a scalarisation of the §8 dimensions. It enters the
matrix as an additional arm beside V1 and V7, not as a replacement for either.

**Rationale.** R3 asks whether there is headroom above two-step-lookahead BOED
on collective-value sequences. A learned policy is the direct instrument for
answering that: if it cannot beat lookahead after training on DEV, the headroom
is small and R3 has an answer worth having. The machinery RL usually has to be
retrofitted with is already present — bit-exact determinism gives replayable
episodes, ground truth is an edit set rather than a label so return is
computable without a human, and the append-only content-addressed registry is
already a trajectory store.

Three frictions, none fatal:

- §8 forbids collapsing D1–D6 into a single number, and RL needs a scalar. The
  prohibition is on *reporting*, so a training-only scalarisation is
  admissible, but it becomes a versioned inspectable artefact with consequences
  for every learned policy, on the same footing as the prefix code, and must be
  justified in the write-up rather than tuned.
- Fitting and reporting on the same pool. Train on DEV, report on HOLDOUT and
  TEST. Note that A14 does not catch this: reusing DEV is not a partition
  crossing, so it stays a discipline question rather than something the
  analyser can enforce.
- Determinism. Policy weights must enter the content hash alongside the env,
  config, metric and data versions, and action sampling must run through the
  explicitly passed seeded generators.

It does not touch F7. Reward is framework-computed and the policy still emits
only structure, so no agent-reachable path writes a number.

**Touches.** F5, which gives experiment selection within a fixed space to
conventional methods. Also §9's matrix, which gains an arm, and F13, since the
preregistered primary contrast is not about this and must not be restated
around it.

## RL fine-tuning of the hypothesis proposal layer

**Idea.** Fine-tune the LLM proposal layer of V7 by reinforcement learning
against framework-computed scores on generated scenarios, rather than using it
zero-shot or few-shot.

**Rationale.** Recorded mainly to state the objection, because the idea is
otherwise the obvious next step after the learned-selection entry above and
will keep resurfacing.

R1 asks whether the LLM is reasoning or retrieving, and names retrieval as the
most likely outcome. Training the proposal layer on scenarios drawn from our
own edit grammar manufactures a retriever over that grammar and forecloses the
question on the slice: a trained V7 beating B4 would demonstrate that we
successfully trained it, not that generation beats retrieval. Any such arm
would have to be evaluated exclusively on structure outside the grammar it was
trained against, which is a much smaller evaluable surface than S1–S12, and
S11 is the only slice scenario that qualifies.

Sequencing for both entries: not possible before item 12, and belongs after
item 15. The baselines and the first experiment matrix are what establish
whether there is headroom worth learning into, and running either variant
earlier reintroduces exactly the confound between agent performance and
framework immaturity that items 2–11 exist to prevent.

**Touches.** No frozen decision directly. It compromises R2 by adding a
training axis to the memory ablation, and it makes R1 unanswerable on any
scenario whose ground truth lies inside `agent_grammar`.

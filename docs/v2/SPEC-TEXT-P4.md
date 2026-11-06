# SPEC text for P4's reads and controls: proposed words (stop point)

**Status: proposal, revised after its one red-team round, 2026-11-06 (lane session 36). `docs/SPEC.md` is unchanged.** The round: `docs/v2/redteam/2026-11-06_spec-text-p4.md`, ready with fixes, six blocking. The control room accepted the first draft provided it had that round, and said wording that changes what the paper claims comes back (INBOX 2026-11-06). So part A is ready to apply, and part B holds five rulings that are the control room's. Nothing is applied until part B is ruled on, because A and B touch the same lines.

Most of this writes down what P4-PROPOSAL §5 approved. Two points are the lane's own choices, not decisions the owner took, and are marked: the named condition in C3 and C4 (LOG 2026-11-06), and the softplus note showing in `rescaled` (LOG 2026-11-06).

## Part A: ready, the lane's fixes taken

### A1. §5 Anonymisation: a third condition (decision 11, A; Δ9)

Add after the first paragraph of §5:

> A third condition, `rescaled`, keeps every name and takes time as the anonymised condition has it, at the same fixed factor. It splits the step from named to anonymised in two: named to `rescaled` changes time, and `rescaled` to anonymised changes names. Any interaction between the two is not attributed. It runs on Sonnet only, in both tiers. A softplus-link coefficient is not covariant under the time factor, so where time is rescaled the agent is told such coefficients are reported as fitted. That note shows in `rescaled` and in the anonymised condition, and not in the named one (the lane's choice).

The leak test's paragraph stands. `rescaled` keeps the names, so it has no forbidden-term list of its own.

### A2. §4.1, the line after the table

Replace:

> Each LLM arm runs in **named** and **anonymised** conditions (§5).

with:

> Each LLM arm runs in **named** and **anonymised** conditions, and AG-c-S and AG-o-S also in `rescaled` (§5). AG-S-boed runs named only.

### A3. §6.2 C2 (Δ9)

Replace:

> **C2 (Q2):** AG-c-S and AG-o-S, each named vs anonymised, same metric, paired.

with:

> **C2 (Q2):** AG-c-S and AG-o-S, each as two paired differences on the same metric: named vs `rescaled` (time) and `rescaled` vs anonymised (names). Four reads. The softplus note of §5 sits inside the time reads and outside the names reads.

### A4. §6.2 C4 (the lane's choice)

Add to C4: "Named condition."

### A5. §6.1

Add "B-sym's tuned setting" after "budgets" (INBOX 2026-11-06).

### A6. §6.4 heading, numbering and controls 4 to 6 (decision 13)

Replace the heading:

> ### 6.4 Positive controls (run before any contrast is reported)

with:

> ### 6.4 Positive controls (each must pass before the contrast it gates is reported)

Number the existing three bullets 1 to 3, so the list matches every other document, and add:

> 4. **Naming null (gates C2).** A scripted agent that holds the truth records a belief, fits, runs one experiment and submits, in the named, `rescaled` and anonymised conditions, on the same truth and seed. Submissions, every fit and the held-out gap must be identical and the gap finite. The time factor is a power of two, so equality is exact. It covers belief, fit, experiment and submit, and no other tool. It is a null: it shows the conditions do not differ when nothing should, and that C2 can separate rests on controls 1 to 3.
> 5. *(gate: ruling B3)*
> 6. **Behaviour instrument (gates C6).** A scripted agent with a known timeline of committed beliefs (§4.4) must reproduce known calibration, falsification and revision values.

### A7. §7.1

Replace "**Controls fail** (§6.4)" with "**Controls 1 to 3 fail** (§6.4)", since P3 read only those.

### A8. §7, P4 row (decision 12)

Replace:

> | **P4 Main campaign** | test split, all arms, Haiku/Sonnet/Opus, both naming conditions, budget sweep; evidence-policy comparison (§4.2) | C1–C5 reported with CIs |

with:

> | **P4 Main campaign** | test split, AG-c and AG-o on Haiku/Sonnet/Opus, named and anonymised, `rescaled` on Sonnet, the non-LLM systems (B-boed: ruling B5), budget sweep | C1–C4 reported with CIs |
> | **P4b Evidence policies** | evidence-policy comparison (§4.2), AG-S-boed | C5 and C3's AG-S-boed half reported with CIs |

## Part B: rulings for the control room

Each gives the problem, the lane's recommendation and the exact words.

### B1. C3 says "evidence wall", and its read cannot test that (finding 1)

P4's C3 is AG-o-S minus AG-c-S on the held-out gap. SPEC §4.2 measures the wall by whether the telling diagnostic was observed, and P4 has no such read for agents. A gap win would show better prediction, and not a broken wall. Also Q3 is agent against BOED, now all P4b, and AG-o against AG-c is Q7.

**Recommendation.** Say what the read measures, and stop claiming the wall for it. The wall question in Q7 goes unanswered by P4's confirmatory reads. If the control room wants it answered, a telling read on agent sessions could be added before §6.1 as a new read: the sessions are unchanged, the reader is new code, and it needs its own control. I do not recommend that now.

> **C3 (Q7, Q3):** AG-o-S vs AG-c-S at equal E, named condition, on the held-out gap: does free analysis predict better on the same evidence. It does not measure the evidence wall, which needs the telling diagnostic (§4.2). In P4. AG-c-S vs AG-S-boed (Q3, who chooses the evidence) moves to P4b.

§10's "which makes C3 a direct test" becomes "which makes C3's AG-S-boed half, in P4b, a direct test".

### B2. Q2's measure loses its read (finding 2)

SPEC Q2 says the named and anonymised gap "measures reliance on pretraining recall", while Δ9 says that gap cannot be attributed to naming. After A3 no read is named minus anonymised, Haiku and Opus have only that mixed gap, and so has P5.

**Recommendation.** Keep the claim to what the design shows.

- Q2: "The gap between the named and anonymised conditions bounds reliance on pretraining recall, since it mixes names and time. On Sonnet, `rescaled` splits it in two (§5). On Haiku and Opus, and on real data, only the mixed gap exists."
- §9's risk table: "a large gap on the `rescaled` to anonymised step is itself the Q2 finding".
- §10's contribution 4, and its related-work line on the same ablation: "a named-vs-anonymised ablation in an interactive agent setting, with time and names separated on synthetic data, and together on a real catalogue".
- Named minus anonymised is printed for every model as a labelled secondary, so Q5 has a like-for-like figure. This needs a small addition to the analysis, not a new campaign.

### B3. Control 5 and C3's AG-S-boed half (finding 3)

The ruling of 2026-11-05 says control 5 "no longer gates C3", and its reason, no policy, holds for `C3-free` only. AG-S-boed is an EIG policy arm.

**Recommendation.** It gates both policy reads, in P4b. The P4 amendment is unaffected, since it covers `C3-free`.

> 5. **Policy separation (gates C5 and C3's AG-S-boed half, in P4b).** A truth-aware FSD policy must beat random on the telling-diagnostic rate.

### B4. When P4b freezes (finding 4)

§6.1 freezes "the contrasts in 6.2" in one commit before the test campaign. Moving C5 and half of C3 to P4b breaks that, and C5's agent-chosen arm is P4's main AG-c-S cell, so if P4b freezes after P4's reads open, part of its comparison is not blind.

**Recommendation.** Freeze P4b's contrasts before any P4 read is opened, that is, before the miss check's terminal step. The lines I could not otherwise settle this from (STATUS-2026-10-05, HANDOFF-2026-10-06) only say FSD needs an event cap first, so that design work has to land in that window. The alternative is for P4b to run its own agent-chosen arm, 80 more sessions, which removes the ordering constraint.

Add to §6.1:

> P4b's contrasts, C5 and C3's AG-S-boed half, are frozen in their own commit before any P4 contrast is read.

### B5. B-boed (finding 6)

SPEC §4.1 lists B-boed, no code or decision builds or removes it, and "all arms" in the P4 row would include it.

**Recommendation.** It moves to P4b with AG-S-boed, since it is the same machinery. §4.1's row gets "(P4b)".

## Not in this proposal

- **The floor rule** (LOG 2026-11-05). It defines how C1 to C4 treat a failed row, and it lives in the prereg and the contrast code. The red team recommends one sentence under §6.2, because it is a primary-analysis rule and it flipped a sign on pilot rows. I agree: "A row with no finite outcome takes the lowest finite reading of its truth and seed among the contrast rows (LOG 2026-11-05), and the read with those rows left out is printed beside every read." For the control room with B1 to B5.
- **Other rulings the SPEC does not state:** the unchecked-truth rule, which alters §6.4's first control; the miss check before P4's verdict; the numeric rules for controls 1 to 3 (P4-PROPOSAL §4).
- **Text still owed before §6.1, not written here:** Δ1 (§2.2; "never because the optimiser stalled" stands against the 25 miss rows), Δ2 to Δ4 and Δ12 (§4.0), Δ4 (§4.4, test 10b, and "may" where beliefs are enforced), Δ7 (§6.1), Δ8 (§9), Δ10 (§6.4). §4.2 still describes parked option 2.
- **Housekeeping on applying:** `scoring/controls_p4.py`'s docstring says the controls are not in the SPEC; LOG's plan choice 18 needs a line; control 6 has no caller yet and `contrasts.py` has no C6 read, which is right for an exploratory read but means its gate is words only until P4's report is built.
- **fit/6** stays a SPEC item for after P4.

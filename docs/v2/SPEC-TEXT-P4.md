# SPEC text owed for P4: proposed words (stop point)

**Status: proposal, 2026-11-06 (lane session 36). `docs/SPEC.md` is unchanged.** A change to the SPEC is a stop point, so this file gives the exact words for the owner or the control room to accept, change or refuse. Each change writes down something the owner already approved in P4-PROPOSAL §5 (LOG, "decisions 1 to 17"). None adds a new decision. They are due with the §6.1 commit at the latest, because that commit quotes the contrasts and they should read as P4 runs them.

## 1. §5 Anonymisation: a third condition (decision 11, A; Δ9)

Add after the first paragraph of §5:

> A third condition, `rescaled`, keeps every name and takes time as the anonymised condition has it, at the same fixed factor. Anonymised against named changes names and time together, so `rescaled` separates them. It runs on Sonnet only, in both tiers. A softplus-link coefficient is not covariant under the time factor, so where time is rescaled the agent is told such coefficients are reported as fitted. That note shows in `rescaled` and in the anonymised condition, and not in the named one.

The leak test's paragraph stands. `rescaled` keeps the names, so it has no forbidden-term list of its own.

## 2. §6.2 C2 (Δ9)

Replace:

> **C2 (Q2):** AG-c-S and AG-o-S, each named vs anonymised, same metric, paired.

with:

> **C2 (Q2):** AG-c-S and AG-o-S, each as two paired differences on the same metric: named vs `rescaled` (time) and `rescaled` vs anonymised (names). Four reads. The softplus note of §5 sits inside the time reads and outside the names reads.

## 3. §6.2 C3 and C5 (decision 12, B)

Replace:

> **C3 (Q3):** AG-c-S vs AG-S-boed (who chooses the evidence), and AG-o-S vs AG-c-S at equal E (does free analysis break the evidence wall).

with:

> **C3 (Q3):** AG-o-S vs AG-c-S at equal E, named condition (does free analysis break the evidence wall). In P4. AG-c-S vs AG-S-boed (who chooses the evidence) moves to P4b.

and add to C5:

> In P4b, under its own freeze.

## 4. §6.4 Controls 4 to 6 (decision 13)

Add to the list:

> - **Naming null (gates C2).** The scripted planted-hint agent runs through the tool layer in the named, `rescaled` and anonymised conditions and must give identical submissions and gaps. The time factor is a power of two, so equality is exact.
> - **Policy separation (gates C5, in P4b).** A truth-aware FSD policy must beat random on the telling-diagnostic rate.
> - **Behaviour instrument (gates C6).** A scripted agent with a known belief timeline must reproduce known calibration, falsification and revision values.

The proposal said control 5 gates C3 and C5. Under decision 12 its inputs go to P4b and P4's C3 uses no policy, so it gates C5 only (LOG 2026-11-05, accepted by the control room).

## 5. §7, P4 row (decision 12)

Replace:

> | **P4 Main campaign** | test split, all arms, Haiku/Sonnet/Opus, both naming conditions, budget sweep; evidence-policy comparison (§4.2) | C1–C5 reported with CIs |

with:

> | **P4 Main campaign** | test split, all arms, Haiku/Sonnet/Opus, named and anonymised, `rescaled` on Sonnet, budget sweep | C1–C4 reported with CIs, C3 as AG-o-S vs AG-c-S |
> | **P4b Evidence policies** | evidence-policy comparison (§4.2), AG-S-boed | C5 and C3's AG-S-boed half reported with CIs |

## Not in this proposal

- **The floor rule** (LOG 2026-11-05). It defines how C1 to C4 treat a failed row, and it lives in the prereg and the contrast code, which the §6.1 commit freezes. §6.2 says only "paired bootstrap", which stays true. If the owner wants it in the SPEC, one sentence under §6.2 would do.
- **fit/6** stays a SPEC item for after P4.

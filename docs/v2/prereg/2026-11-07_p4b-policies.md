# Pre-registration: P4b, the evidence policies (C5 and C3's AG-S-boed half)

**Status: SECOND TEXT, lane session 38, 2026-11-07. Not frozen. Nothing here has run.** The first text
(2b05867) had one red-team round: not ready, 9 blocking (`docs/v2/redteam/2026-11-07_p4b-policies.md`).
This text answers every finding (section 12). It is written to the lane's recommendation on the at-stop
rule, which waits on the owner (JOURNAL 2026-11-07, session 38 stop point), so it does not freeze before
that answer. A second round runs on this text with the code it names.

SPEC §6.1: "P4b's contrasts, C5 and C3's AG-S-boed half, are frozen in their own commit before any P4
contrast is read." The miss check is not launched on the P4 store until this freeze is in the LOG
(AUTOPILOT, Never; INBOX 2026-11-07).

**What has been touched.** No test split exists, so no P4 or P4b unit exists. No policy arm has run on any
truth for record (no `progress.log` names one; checked in session 38 and by the red team). Seen so far:
- the FSD at-stop controls A1 and B, on planted truths (LOG 2026-10-29);
- check (b) (LOG 2026-11-05);
- the decision-0 stop, cost and tie measurements on dev at bff61ec (`.cache/drafts/fsd-dist/`,
  `.cache/drafts/fsd-ties/`);
- the P3 dev rows of the non-policy systems;
- K0 recomputed from A1's stored runs with no draw (session 38, `.cache/drafts/s38/k0/`).

**Rulings this text rests on.**
- Decision 12: C5 and C3's AG-S-boed half go to P4b.
- B3: control 5 gates C5 and C3's AG-S-boed half.
- B4: this freeze comes before any P4 read.
- B5: B-boed is in P4b.
- R5: confirmatory only if control 5 passes under a rule frozen before P4b's data, and otherwise
  exploratory.
- B1: no wall claim.
- The C5 seeding decision and Δ11's EIG decision (LOG 2026-10-27, 2026-10-28).
- The cap stays 15,000 (LOG 2026-10-29).
- SPEC §4.2: option 2 returns only to the owner.

## 0. Owed before the freeze commit, and before launch

The freeze commit holds this text, the read module and every item marked **(freeze)**. It lands before
the miss check launches. Items marked **(launch)** land before any P4b unit runs.

- **D1 (freeze). The policy's computed diagnostics on AG rows.** Today only the B-sym policy unit writes
  `computed` (`campaign/execute.py:446`, `:517`). `telling_observed` skips an ok row without it, and gives
  `False` to a failed row (`analysis.py:611-618`). Owed, test first: every AG-c-S policy session's row
  records the diagnostics its **policy** chose on experiments that left data, read by the framework from
  the session's hash-chained record. The agent's own `diagnostic` calls are not in it. P4's AG-c-S rows
  (no policy) record the agent's own computed diagnostics, in a separate field that only §4's exploratory
  agent-chosen rate reads. The read refuses any LLM row in a `C5-tell` read with no `computed` field.
- **D2 (launch). B-boed** does not exist. It is built test first. No contrast reads it (§4).
- **D3 (freeze). The read module**, `campaign/contrasts_p4b.py` or similar, is an instrument and is
  written test first. It holds §3's reads, §3.1's rows and §5's rules.
- **D4 (launch). Commit and tables.** C5's gap reads need ORACLE rows, and C3-boed needs P4's AG-c-S named
  rows, both from P4's store. P4b runs at a commit where every key P4 wrote recomputes unchanged; it is
  checked at launch, or P4b does not launch. The tables are joined by `campaign/tables.merge_tables`
  (on v2, c060fc0) with its fingerprint check across the two stores.
- **D5 (launch). The session clock excludes policy time.** A policy decision runs inside the session's
  `asyncio.timeout(3600)` (`harness/live.py:627`; `investigation/tools.py:536-541`), and FSD's
  simulation alone exceeds 3,600 CPU s on 5 of 20 dev truths. Owed, test first: the clock excludes
  framework decision time, recorded per call, for every AG arm alike. The limit the prompt states does
  not change.
- **D6 (launch). A driver for the policy arms with a CPU meter.** `scripts/pilot.py` has `STOP` but no
  meter, and nothing runs the policy arms. The ceiling of §8 is enforced by that meter.
- **D7 (freeze). EIG as adopted on 2026-10-28** (LOG 2026-10-27 items 2, 3 and 5): EIG to relative
  precision; a full tie taken from the truth-keyed random draw, so that on a degenerate decision EIG
  equals random; the KL-ranked pick logged as a diagnostic. All of it enters `EIGPolicy.config`. It was
  never built: menu order still breaks ties (`policies.py:688-703`). Being built, session 38.
- **D8 (freeze). FSD's own exclusion, Fx** (§2.3), test first, in `policies.FSDPolicy` only.

## 1. Question and falsifiable hypotheses

**Q6.** With the proposer held fixed, does choosing experiments by falsification-seeking design (FSD)
predict better, and find the telling diagnostic more often, than choosing them by expected information
gain (EIG)? **Q3.** Does an agent that chooses its own evidence predict better than the same agent with EIG
choosing it?

- **H5g-B, H5g-A** (gap). With B-sym (B) or AG-c-S (A) proposing, FSD's held-out gap equals EIG's.
- **H5t-B, H5t-A** (telling). With B-sym or AG-c-S proposing, FSD's policy observes the telling
  diagnostic as often as EIG's.
- **H3b** (gap). AG-c-S choosing its own experiments and AG-S-boed (the same agent with EIG choosing)
  have equal held-out gaps.

Each is falsified by its read's label (§3). **What the labels speak for.** FSD here is FSD with its own
exclusion (Fx), which passed control B. Telling and truthFSD keep today's rule (K0), under which control
A1 failed (§2.3). **Planned as exploratory:** control 5 is expected to fail (§2.3), and then by R5
every read here is exploratory. The reads and labels are still fixed now, so that no reading is chosen
after the data.

## 2. Design

### 2.1 Truths, worlds, proposers

The 40 truths of P4's test split, frozen at `S`, and nothing else; truths are the bootstrap unit. The
proposer is B-sym at P4's budget F with the tuned setting frozen at `S` (§4.2: "B-sym at budget F, then
AG-c-S"), or AG-c-S on Sonnet with P4's prompt, budgets and harness, in the named condition.

| block | arms | worlds | units |
|---|---|---|---|
| B-sym policies | `B-sym+random`, `B-sym+EIG`, `B-sym+FSD` | 40 truths, seed 1 | 120 baseline |
| control 5 | `B-sym+truthFSD`, with `B-sym+random` above | 40 truths, seed 1 | 40 baseline |
| telling | the telling unit | 40 truths, seed 1 | 40 cells |
| AG policies | `AG-c-S+random`, `AG-c-S+FSD` | 40 truths, seed 1 | 80 sessions |
| AG-S-boed | `AG-c-S/named+policy=EIG` | 40 truths, seeds 1 and 2 | 80 sessions |
| B-boed | B-boed | 40 truths, seeds 1 and 2 | 80 baseline |
| from P4 | AG-c-S named (agent-chosen), ORACLE, B-lib | as P4 | 0 new |

`AG-c-S+EIG` on seed 1 **is** AG-S-boed on seed 1: `plan.LLMArm` gives them one system name and one key.
So the AG side is 160 sessions, and the seed-1 rows serve C5 and C3-boed alike.

### 2.2 Policies and settings

Every setting is at its default in `plan.PolicyArmSettings` (the B-sym arms) and
`investigation/policy.PolicySettings` (the AG arms), as the code stands at the freeze commit:
- R = 16 replicates per side, and the event cap `POLICY_MAX_EVENTS` = 15,000 for every policy
  simulation;
- the telling unit at R = 32, cap 15,000;
- 8 experiments per investigation, and B-sym's two epochs at F//2 and the rest (LOG 2026-10-09);
- the random arms' draws keyed by truth (LOG 2026-10-28);
- one cap path, so the AG arms' settings name the same cap as the B-sym arms (`investigation/policy.py:224`,
  `:229`);
- **EIG:** BIC weights, EIG to relative precision, and a full tie taken from the truth-keyed random draw,
  all entering `EIGPolicy.config` (D7).

### 2.3 The at-stop rule

C5's telling read and control 5 depend on what a policy does with a replicate that truncates at the cap or
stops. The rule names are those of the FSD at-stop criteria, v8.1
(`.cache/drafts/fsd-controls/criteria.md` §0).

**The rule, as recommended (waiting on the owner).**
- **Telling and truthFSD: K0, today's code.** Telling drops stopped runs (`scoring/telling.py:14-24`), and
  truthFSD counts a truncated or stopped replicate as the undefined atom.
- **FSD: Fx.** FSD excludes a design on which either of its sides has a truncated or stopped run among its
  16 replicates (D8). Criteria §8 reads control B as "FSD needs its own exclusion, whatever the telling
  rule": Fa, today's FSD, failed B1 at 0/120, and Fx passed with K0 (`score-ctlB.txt`). Fx changes neither
  telling nor truthFSD, so nothing A1 tested. Its cost is unmeasured: it loses telling's label when the
  artefact sits on the truth's top design (criteria §2).

**What is known about control 5 under K0.**
- K0 is today's code, confirmed by a run with no draw on A1's stored streams: (i) 12/15, (ii) 0/120,
  (iii) p = 1 (session 38, `.cache/drafts/s38/k0/`).
- At decision 0 on dev, truthFSD under rule (a) picks telling's d\* under rule (b) on 3 of 14 truths, and
  on at most 6 of 14 under any tie option (`.cache/drafts/fsd-ties/draft.md:82,113`; reproduced,
  `redteam-s16.md:4`). Random observes d\* with chance 1 − (34/35)^8 = 0.207 per unit, which assumes
  d\* is among the 35 diagnostics. The comparator and the truth do not change between decisions.
- Those figures are at bff61ec, with B-lib's pick as the comparator and two truth-side replicates on the
  stop side.
- So control 5 is expected to fail, and P4b is planned as exploratory.

**Not taken, and why.**
- **Option 2** (K5 for telling and truthFSD) returns only to the owner under SPEC §4.2, after criteria
  §8's He² resolution and a new control on streams with no test data (R5; LOG 2026-10-29). It is not
  proposed here. If the owner reopens it, it is a separate track and an amendment frozen before any P4b
  unit.
- **A dev measurement before the choice** (the first text's recommendation). The dev evidence above
  already answers it. The run would cost about 38 CPU hours, and the freeze it would delay holds back
  P4's miss check.

### 2.4 Streams

Policy streams, telling streams and the random arms' draws are as in the code at the freeze
(`scoring/seeds.py`: `policy_seed`, `truth_policy_seed`, `scorer_seed(TELLING, ...)`; D7's tie draw).
The reads' bootstrap draws from `SeedSequence([20261121, i])`, i = 0 to 4 in §3's order, with 10,000
resamples. 20261121 is a new root: outside 20261104 to 20261113, not tuning's rehearsal root 20261120,
and found nowhere in `src`, `scripts`, `docs` or `tests` (grep, session 38).

## 3. Primary analyses, one per hypothesis

Each read is `first` minus `second`, paired by truth, on the truths that P4's controls 1 to 3 keep. A
truth's value is the mean over its seeds in the block.

| i | read | first | second | outcome | worlds |
|---|---|---|---|---|---|
| 0 | `C5-gap-B` | `B-sym+FSD` | `B-sym+EIG` | held-out gap, per event (nats) | seed 1 |
| 1 | `C5-gap-A` | `AG-c-S+FSD` | AG-S-boed | held-out gap | seed 1 |
| 2 | `C5-tell-B` | `B-sym+FSD` | `B-sym+EIG` | the policy observed d\*, 0 or 1 | seed 1 |
| 3 | `C5-tell-A` | `AG-c-S+FSD` | AG-S-boed | the policy observed d\*, 0 or 1 | seed 1 |
| 4 | `C3-boed` | AG-c-S named | AG-S-boed | held-out gap | seeds 1 and 2 |

**Labels**, applied by the read code:
- **Gap reads (0, 1, 4).** Percentile paired bootstrap 95% CI (`scoring/stats.paired_bootstrap_ci`).
  "First higher" if the CI's low end is above 0, "second higher" if its high end is below 0, and "not
  separated" otherwise. Higher is better, as P4 reads the gap.
- **Telling reads (2, 3).** Exact two-sided McNemar at 0.05, with b (only first observed) and c (only
  second) printed. "First higher" if p < 0.05 and b > c, "second higher" if p < 0.05 and c > b, and "not
  separated" otherwise. The bootstrap CI is printed beside the label and does not set it.
- **"Observed"** in reads 2 and 3 is the policy's chosen diagnostic, computed on an experiment that left
  data, matched by name and channel arguments (`telling.observed`), as for the B-sym arms (D1). For
  AG-S-boed the policy is EIG.
- **Every read is also printed split by `EIGSummary.degenerate`**: degenerate and non-degenerate EIG
  decisions, set before any run (LOG 2026-10-27, item 4). The split is exploratory.
- No correction across the five reads. If they were independent, the chance of at least one false label
  among five would be about 23%, and the report says so beside any label that falsifies.

### 3.1 Which rows

- **Floored or not observed.** A row is floored for a gap read, and counts as not observed for a telling
  read, exactly when `contrasts._takes_floor` floors it: a `FAILED_FIT` failure, no submission, or a
  scored `-inf`. The floor is the lowest finite `per_event` of its (truth, seed) among the rows of this
  table's systems: the five reads' systems, plus ORACLE and B-lib, which are never floored. The read with
  floored rows left out is printed beside each read.
- **Absent.** Every other failure, and every void session (a policy fault voids an AG session,
  `tools.py:542-554`), is absent. It is counted per arm beside each read.
- **Too many absent.** An arm with more than 4 of the 40 truths absent (10%, the share of R2) makes every
  read that uses it exploratory.
- **Missing telling cell.** A truth whose telling cell failed is out of both telling reads, and its count
  is printed.
- **Model drift.** If C3-boed's two arms' recorded served models differ (Sonnet is the alias
  `claude-sonnet-5-5`, `live.py:129`; served models are recorded, `llm.py:269`), C3-boed is exploratory.

## 4. Secondary and exploratory, labelled

All of these are labelled exploratory, and none is read as a result about Q3 or Q6:
- the random arms against FSD and EIG, on both outcomes;
- the agent-chosen telling rate, from P4's AG-c-S rows (D1's separate field);
- every read restricted to truths on which no FSD or truthFSD decision was degenerate (all He² equal at
  the maximum, logged by the framework before any outcome);
- the share of investigations that ran a design on which the truth's runs truncate or stop and the best
  member's do not (§4.2);
- per-arm truncation, stop, Fx-exclusion and absent counts;
- EIG's KL-ranked pick against its actual pick (D7);
- B-boed's rows, and its gap against B-lib and B-sym;
- control 5's per-truth table.

## 5. Decision rules, applicable by a script

1. **Controls 1 to 3** are read on P4's table (P4's code, unfloored, with R2's limits). The truths they
   keep are P4b's truths. If R2's limit trips for P4, every P4b read is not confirmatory.
2. **Control 5** (`controls_p4.policy_separation`): `B-sym+truthFSD` against `B-sym+random` on the 40
   seed-1 units, one-sided exact McNemar, passing if and only if p < 0.05. An unpaired unit, including one
   left unpaired by the ceiling, voids it.
   - Pass: the five reads are confirmatory, subject to §3.1.
   - Fail or void: all five are printed and labelled exploratory.
   - No rerun, no other rule, no other truths.
3. **Control 5 runs first, before any P4b LLM session.** Its verdict and the plan's exploratory status
   go to the control room before the 160 sessions launch.
4. **Labels** are §3's. A read with fewer than 10 paired truths is "not reportable", and the report
   names it and says why.
5. **No read says anything about the evidence wall** (B1).

## 6. Exclusions and failure handling, fixed now

No truth is excluded on any outcome.
- **Failures** are handled by §3.1's mechanical rule only. The driver's retry is for a failure that left
  no record, and it does not depend on the failure's text.
- **A usage-limit stop** pauses the LLM block, and it resumes by rerunning the same command. No session
  with a record is rerun.
- **The CPU ceiling.** Units not run when the ceiling is reached are not rows. A truth left unpaired by
  them makes each read that needs it exploratory, and voids control 5 if it is control 5's.

## 7. Sample size

The sample is fixed by P4's split at 40 truths.

**Control 5.** Its power under K0 is near the size of the test, by §2.3's agreement figures.

**Gap reads.** No policy arm has a measured variance. The nearest proxy is two search procedures at the
same budget on P3 dev rows, B-sym minus B-rand, per-truth means over seeds 1 and 2 (`.cache/drafts/s38/
p3-paired-sd.txt`), floored the way these reads floor them: sd 0.0450 on 20 truths (red team). At 40
truths, the difference detectable at 80% power and 5% is about 2.8 × 0.045 / √40 = 0.020 nats per event.
Without the floor (18 truths) it is about 0.013. For scale, B-sym minus B-lib on the same rows has a mean
of 0.0777. Policy differences smaller than about 0.020 will read "not separated", and the report says so.

**Telling reads.** With 40 truths, an exact two-sided McNemar test needs at least 6 discordant truths, all
in one direction, to label.

## 8. Cost ceiling and wall clock

**CPU, $0, dm-devbox only.** The figures below are recorded and are floors, not bounds:
- a B-sym unit, about 340 CPU s (tuning stage 1: 11.79 CPU h over 124 units, counts only);
- FSD's policy simulation, 1,614 CPU s mean and 6,456 max;
- truthFSD's, 2,957 mean and 17,617 max (`cost-per-investigation.txt`, cap 15,000, decision 0 costed 8
  times);
- EIG, unmeasured, taken as 2 × FSD;
- a score, 115 s.

Left out of the estimate:
- entertained-set refits, up to 4 per decision on growing data;
- epoch-2 fits;
- world truth runs that reach the cap (up to about 108 CPU s each);
- the AG sessions' own fits;
- a tuned B-sym that may cost 2 to 2.5 × `c0`;
- Fx's exclusions, which lower FSD's cost.

| block | arithmetic | CPU hours |
|---|---|---|
| B-sym arms | 40 × (4 × 340 + 1,614 + 2 × 1,614 + 2,957) s | 101.8 |
| AG policy simulation | 40 × 1,614 + 80 × 3,228 s | 89.7 |
| telling | 40 × 2,957 s. truthFSD's 2,957 s covers 8 decisions of 16 truth-side runs per design; telling runs 32 per design and side once, so the allowance is about four times its own reasoning | 32.9 |
| B-boed (allowance) | 80 × 2 × 340 s | 15.1 |
| scores | 400 × 115 s | 12.8 |
| total | | 252.2 |

**Ceiling: 300 CPU hours**, enforced by D6's meter with `STOP`. It is a stop, not an upper bound on the
work: what is unfinished at the ceiling is reported as not run (§6). At 2 workers it runs up to about 6
days, so the block runs after P4 and after stage 2 of any tuning.

**LLM.** 160 Sonnet sessions under Max, no API key. At P3's 346 s mean and 1.5× for experiment use, that
is about 23 hours of model time at concurrency 1, plus the policy simulation inside the sessions (89.7
CPU hours above, outside the session clock by D5): at least 112.7 hours of wall time at concurrency 1. A
usage-limit error stops the block (§9).

## 9. Early-stop conditions

- the CPU ceiling;
- a usage-limit error (stop, record, no retry loop);
- a machine fault, named in the journal first.

No stop on any outcome of a read or of control 5. Control 5's verdict goes to the control room before the
LLM block (§5.3).

## 10. Reported whatever the outcome

- control 5's verdict, with b, c, p and both telling rates;
- the five reads with their labels, CIs, n, McNemar counts, rows-left-out reads and EIG-degeneracy
  splits, and whether each is confirmatory;
- the rule used: K0 for telling and truthFSD with A1's record under it, and Fx for FSD with control B's
  record;
- absent, void, degenerate, truncation, stop and exclusion counts;
- the ceiling's use and every unit not run;
- the multiplicity sentence of §3.

## 11. Code at the freeze

To fill at the freeze: the commit, which includes D1, D3, D7 and D8; the read module's path; the settings'
digests; and the line `sha256: <hex>  docs/v2/prereg/2026-11-07_p4b-policies.md`.

## 12. Round 1's findings and this text's answers

1. EIG tie rule not built: D7, §2.2, and the degeneracy split in §3.
2. Policy time inside the session clock: D5, §8.
3. The §2.3 route: option 2 is left to the owner, the dev run is dropped, and the decision rule is the
   owner's choice (JOURNAL stop point).
4. FSD failed its own control under Fa: Fx (D8, §2.3), with §1's "what the labels speak for".
5. C5-tell-A's outcome: the policy's chosen diagnostic only, with refusal (D1, §3).
6. Anti-conservative bootstrap on 0/1: McNemar labels (§3).
7. Failure and void handling: §3.1 and §6, mechanical, with absent counts and a limit.
8. Read code and root fixed late: D3 in the freeze commit before the miss check, and the root 20261121
   (§2.4).
9. K0 not yet confirmed: done (§2.3).
10. The ceiling is a stop: D6, with §8 stating it and listing what the estimate leaves out.
11. Power proxy: 0.020, floored (§7).
12. Dev figures stated too firmly: caveats in §2.3.
13. Model alias: the drift rule in §3.1.
14. Multiplicity: the sentence in §3.
15. Naming: `plan.PolicyArmSettings` (§2.2).

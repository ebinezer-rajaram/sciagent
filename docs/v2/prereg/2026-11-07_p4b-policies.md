# Pre-registration: P4b, the evidence policies (C5 and C3's AG-S-boed half)

**Status: DRAFT, lane session 38, 2026-11-07. Not frozen. Nothing here has run.**

SPEC §6.1: "P4b's contrasts, C5 and C3's AG-S-boed half, are frozen in their own commit before any P4
contrast is read." This file is that freeze's text. The miss check is not launched on the P4 store until
this freeze is in the LOG (AUTOPILOT, Never; INBOX 2026-11-07).

**What has been touched.** No test split exists, so no P4 or P4b unit exists. No policy arm
(`B-sym+{random,EIG,FSD,truthFSD}`, `AG-c-S+{random,EIG,FSD}`) has run on any truth for record:
none of `.cache/*/progress.log` names one (grep, session 38). Seen so far: the FSD at-stop controls A1
and B on planted truths (LOG 2026-10-29), check (b) (LOG 2026-11-05), the decision-0 stop and cost
measurement on dev at bff61ec (`.cache/drafts/fsd-dist/`, LOG 2026-10-29), and the P3 dev rows of the
non-policy systems.

**Rulings this text rests on.** Decision 12 (C5 and C3's AG-S-boed half go to P4b, LOG 2026-11-05);
B3, B4, B5 (control 5 gates C5 and C3's AG-S-boed half in P4b; this freeze before any P4 read; B-boed in
P4b; LOG 2026-11-06); R5 (control 5 has no passing evidence under today's telling rule, so P4b's reads
are confirmatory only if control 5 passes under a rule frozen before P4b's data, otherwise exploratory;
INBOX 2026-11-07); B1 (no wall claim from C3); the C5 seeding and EIG weights decisions (LOG 2026-10-27,
2026-10-28); the cap stays 15,000 (LOG 2026-10-29).

**Dependencies this text cannot close** (each blocks P4b's launch, not this freeze):

- **D1. The telling rate cannot be read on an AG-c-S row.** `analysis.telling_observed` skips a row
  with no `computed` field, and only the B-sym policy unit writes one (`campaign/execute.py:446`, `:517`;
  `analysis.py:611`). An LLM session's row has none, so C5's agent-side rate read and the agent-chosen
  rate (§4.2) have no input today. Owed: the field for every AG-c-S row, P4's included, derived by the
  framework from the session's hash-chained record (each experiment's diagnostics that the framework
  computed, by name and channel arguments), test first, before `S` if P4's AG-c-S rows are to be read
  here without a rescore.
- **D2. B-boed does not exist** in `src/`. It is built, test first, before P4b's launch. No contrast
  reads it (section 4).
- **D3. P4b's read code does not exist.** `campaign/contrasts.py` holds C1 to C4 only. P4b's reads are a
  new module, an instrument, test first, frozen in this freeze's code commit or an amendment before any
  P4b unit.
- **D4. Which commit P4b runs at, and how its table meets P4's.** C5's gap reads need ORACLE rows and
  C3-boed needs P4's AG-c-S named rows, both from P4's store. If P4b's code commit differs from `S`, the
  read must merge tables across fingerprints (the sweep's `merge_tables`, in progress session 38), and
  every address P4 wrote must be unchanged at P4b's commit. Checked at launch: P4's planned keys
  recomputed at P4b's commit equal P4's stored keys, or P4b does not launch.

## 1. Question and falsifiable hypotheses

**Q6.** Does choosing experiments by falsification-seeking design (FSD) predict better, and find the
telling diagnostic more often, than choosing them by expected information gain (EIG), with the proposer
held fixed? **Q3.** Does an agent that chooses its own evidence predict better than the same agent with
EIG choosing its evidence?

- **H5g-B, H5g-A** (gap). With B-sym (B) or AG-c-S (A) proposing, FSD's held-out gap equals EIG's.
  Falsified by a 95% CI of the mean paired difference that excludes 0, either sign.
- **H5t-B, H5t-A** (telling). With B-sym or AG-c-S proposing, FSD observes the telling diagnostic as often
  as EIG. Falsified as above, on the 0/1 outcome.
- **H3b** (gap). AG-c-S choosing its own experiments and AG-S-boed (the same agent, EIG choosing) have
  equal held-out gaps. Falsified as above.

## 2. Design

### 2.1 Truths, worlds, proposers

P4's test split and nothing else: the 40 truths frozen at `S`, seeds as below. Truths are the bootstrap
unit. The proposer is B-sym at P4's budget F with the tuned setting frozen at `S` (§4.2: "B-sym at budget
F, then AG-c-S"), or AG-c-S on Sonnet with P4's prompt, budgets and harness, named condition.

| block | arms | worlds | units |
|---|---|---|---|
| B-sym policies | `B-sym+random`, `B-sym+EIG`, `B-sym+FSD` | 40 truths, seed 1 | 120 baseline |
| control 5 | `B-sym+truthFSD` (and `B-sym+random` above) | 40 truths, seed 1 | 40 baseline |
| telling | the telling unit | 40 truths, seed 1 | 40 cells |
| AG policies | `AG-c-S+random`, `AG-c-S+FSD` | 40 truths, seed 1 | 80 sessions |
| AG-S-boed | `AG-c-S/named+policy=EIG` | 40 truths, seeds 1 and 2 | 80 sessions |
| B-boed | B-boed | 40 truths, seeds 1 and 2 | 80 baseline |
| from P4 | AG-c-S named (agent-chosen), ORACLE, B-lib | as P4 | 0 new |

`AG-c-S+EIG` on seed 1 **is** AG-S-boed on seed 1: `plan.LLMArm` gives them one system name and one key.
So the AG side is 160 sessions, not P4-PROPOSAL §4's 120 + 80 = 200. The seed-1 rows serve C5 and C3-boed
alike.

### 2.2 Policies and settings

As the code stands at this freeze, every setting in `plan.PolicySettings` and
`investigation/policy.PolicySettings` at its default: R = 16 replicates per side, the event cap
`POLICY_MAX_EVENTS` = 15,000 for every policy simulation, the telling unit's R = 32 and cap 15,000, BIC
weights with the truth-keyed tie draw for EIG, 8 experiments per investigation, B-sym's two epochs at
F//2 and the rest (LOG 2026-10-09). The random arms' draws are keyed by truth (C5 seeding, LOG
2026-10-28). One cap path: the AG arms' settings name the same cap as the B-sym arms
(`investigation/policy.py:224`, `:229`).

### 2.3 The at-stop rule: **a choice for the control room before freezing**

C5's telling read and control 5 both depend on what a policy does with a replicate that truncates at the
cap or stops. Two candidates, nothing else considered:

- **A, today's code.** Telling drops stopped runs (rule (b), `scoring/telling.py:14-24`); FSD and
  truthFSD count a truncated or stopped replicate as the undefined atom (rule (a)). This is K0 with Fa
  of the FSD at-stop criteria (v8.1, `.cache/drafts/fsd-controls/criteria.md` §0). No code changes.
- **B, SPEC §4.2's parked option 2.** K5 for telling and truthFSD (one shared exclusion X, computed from
  telling's 32 replicates) and Fx for FSD (FSD excludes a design where either of its sides has a truncated
  or stopped run among its 16). It needs an instrument change to `Telling` and `policies.py`, test first.

**What is known, and what it implies for control 5.**

- Under A, on the dev truths at decision 0, truth against B-lib's pick is "one side always stops" on 12
  designs of 10 of 20 truths (LOG 2026-10-29). On such a design every diagnostic's He² is 1, and
  `choose` takes it with diagnostic 0. The comparator and the truth do not change between decisions, so
  on those truths truthFSD is expected to compute `mean_rate` only, and observes d\* only if d\* is
  `mean_rate`. Control A1 under K0: (ii) 0/120 and (iii) p = 1 (`score-ctlA1.txt`). The recomputation of
  K0 from `runs-ctlA1.jsonl` (R5's confirming run) is in session 38's journal.
- Under B, A1 under K5: (i) 12/15, (ii) 78/120 against the threshold 84 (fail), (iii) p = 0.0352
  (control 5's own test, pass). F₀ §8 then says "no candidate is ready; He² resolution comes first",
  and LOG 2026-10-29 says no new freeze of option 2 until its calibration carries pool uncertainty.
  Control B: Fx passes with K0 and K5, and Fa (today's FSD) fails B1. By §8, "FSD needs its own exclusion,
  whatever the telling rule". On 6 of 20 dev truths today's FSD takes the same exploding design at every
  decision (fsd-cap-draft2, finding 2).
- **Power of control 5** (exact, `.cache/drafts/s38/c5_power.py`, its output in the journal). 40 units;
  random observes d\* with chance 1 − (34/35)^8 = 0.207 per unit (8 uniform picks over 35 diagnostics,
  assuming d\* is one of them); truthFSD observes d\* with chance h on a truth where it can, and 0 where
  its pick is degenerate. P(pass) at a degenerate share of 0.5 (dev's decision-0 figure): 0.058 at
  h = 0.5, 0.219 at h = 0.65, 0.514 at h = 0.8, 0.830 at h = 0.95. At share 0: 0.822, 0.990, 1.000,
  1.000. h is not measured on any real truth.

So under A, control 5 is more likely to fail than pass on a split like dev, for a reason known now (the
rule mismatch), and then every P4b read is exploratory after 160 sessions. Under B, control 5 has power
if h is moderate. But B adopts a rule A1 failed on a frozen criterion, against F₀ §8 and LOG 2026-10-29,
and Fx's own cost (losing telling's label when the artefact hides the truth's top design) is unmeasured.

**Lane's recommendation: neither yet. Measure control 5 on dev under A first**, then freeze. Run
`B-sym+truthFSD`, `B-sym+random` and the telling unit on the 20 dev truths, seed 1, at the same settings.
That is CPU only, about 38 CPU hours by section 8's per-unit figures, after B-sym tuning's stage 1, and
exploratory. It measures the degenerate share and h under A on real truths, which is the number the
choice turns on. Dev is where this belongs, it touches no test data, and it is before P4b's data, so R5
allows a rule chosen on it. Then: if control 5 passes on dev under A, freeze A. If it fails, the control
room chooses between B, with its record stated, and A with P4b exploratory, before the freeze. The
rule chosen is fixed before any test-split policy unit runs, whatever P4's reads show. **If the
control room prefers to freeze now,** the lane's choice is A, since it needs no instrument change and
no reversal of a ruling, with the likely outcome (exploratory) stated here and in the report.

### 2.4 Streams

Policy streams, telling streams and the random arms' draws as in the code at the freeze
(`scoring/seeds.py`: `policy_seed`, `truth_policy_seed`, `scorer_seed(TELLING, ...)`). Each read's
bootstrap draws from `SeedSequence([read_seed, index])` as `contrasts.read_contrasts` does, with
`read_seed` and the indices fixed in the read module (D3) and disjoint from P4's reads and from
`CONTROL_2_STREAM`.

## 3. Primary analyses, one per hypothesis

Each read is `first` minus `second`, paired by truth (the mean over the truth's seeds in the block), with
a percentile paired bootstrap 95% CI (`scoring/stats.paired_bootstrap_ci`, P4's resamples), on the
truths P4's controls 1 to 3 keep.

| read | first | second | outcome | worlds |
|---|---|---|---|---|
| `C5-gap-B` | `B-sym+FSD` | `B-sym+EIG` | held-out gap (per-event, nats) | seed 1 |
| `C5-gap-A` | `AG-c-S+FSD` | AG-S-boed | held-out gap | seed 1 |
| `C5-tell-B` | `B-sym+FSD` | `B-sym+EIG` | telling observed, 0 or 1 | seed 1 |
| `C5-tell-A` | `AG-c-S+FSD` | AG-S-boed | telling observed, 0 or 1 | seed 1 |
| `C3-boed` | AG-c-S named | AG-S-boed | held-out gap | seeds 1 and 2 |

**Label**, applied by the read code: "first higher" if the CI's low end is above 0, "second higher" if
its high end is below 0, "not separated" otherwise. No correction for the five reads; each is reported
with its CI, as P4's C1 to C4 are. The gap is "higher is better" as P4 reads it.

**Rows.** A gap row with no finite outcome takes the lowest finite `per_event` of its (truth, seed) among
the rows of the systems in this table (the five reads' systems and ORACLE, B-lib), as SPEC §6.2's floor
sentence; ORACLE and B-lib are never floored; the rows-left-out read is printed beside each. A telling
row from a failed unit counts as not observed (`telling_observed`). A truth whose telling cell failed is
out of both `C5-tell` reads, and their count is printed.

## 4. Secondary and exploratory, labelled

All labelled exploratory: the random arms against FSD and EIG on both outcomes; the agent-chosen telling
rate (needs D1); `C5-tell-*` with McNemar's exact two-sided p beside the CI; every read restricted to the
truths on which no FSD or truthFSD decision was degenerate (all He² equal at the maximum, framework-logged
before any outcome); the share of investigations that ran a design on which the truth's runs truncate or
stop and the best member's do not (§4.2); per-arm truncation and stop counts; B-boed's rows and its
gap against B-lib and B-sym; control 5's per-truth table. Nothing in this section is read as a result
about Q3 or Q6.

## 5. Decision rules, applicable by a script

1. Controls 1 to 3 as read on P4's table (P4's code, unfloored, with R2's limits). The truths they keep
   are P4b's truths. If R2's limit trips for P4, every P4b read is reported as not confirmatory.
2. **Control 5** (`controls_p4.policy_separation`): `B-sym+truthFSD` against `B-sym+random` on the 40
   seed-1 units, one-sided exact McNemar, passes iff p < 0.05; an unpaired unit voids it. Pass: the five
   reads are confirmatory. Fail or void: all five are printed and labelled exploratory. No rerun, no
   other rule, no other truths.
3. Control 5 runs first, before any P4b LLM session. If it fails, the 160 sessions are not launched
   without the control room's word, since their reads could only be exploratory.
4. Each read's label is section 3's. A read with fewer than 10 paired truths is "not reportable" and
   named, never labelled.
5. No read says anything about the evidence wall (B1).

## 6. Exclusions and failure handling, fixed now

No truth is excluded on any outcome. A unit that fails with a bug is retried by the driver's rule; a unit
that fails for its own reasons is a row (floored for the gap, not observed for telling). A usage-limit
stop pauses the LLM block and it resumes by rerunning the same command; no session is rerun once it has a
record. A unit killed by the CPU ceiling is a failed unit and is reported as such.

## 7. Sample size

Fixed by P4's split: 40 truths. Control 5's power is section 2.3's table. For the gap reads no policy arm
has a measured variance. The nearest proxy, two search procedures at the same budget on P3 dev rows
(`B-sym` minus `B-rand`, per-truth means, `.cache/drafts/s38/p3-paired-sd.txt`): sd 0.0303 on seed 1
(n 18), so at 40 truths the difference detectable at 80% power and 5% is about 2.8 × 0.0303 / √40 =
0.013 nats per event. For scale, B-sym minus B-lib on the same rows has mean 0.0777. Policy differences
smaller than about 0.013 will read "not separated", and the report says so.

## 8. Cost ceiling and wall clock

**CPU, $0, dm-devbox only.** Per unit, from recorded figures: a B-sym unit about 340 CPU s (tuning
stage 1, 11.79 CPU h over 124 units, journal 2026-11-07, only its counts read); FSD's policy simulation
1,614 CPU s mean, 6,456 max, truthFSD's 2,957 mean, 17,617 max (`cost-per-investigation.txt`, cap
15,000, decision 0 costed 8 times, a floor). EIG unmeasured; taken as 2 × FSD. A score 115 s.

- B-sym arms: 40 × (4 × 340 + 1,614 + 2 × 1,614 + 2,957) s = 101.8 h.
- AG policy simulation: 40 × 1,614 + 80 × 3,228 s = 89.7 h.
- Telling: 40 cells, unmeasured at R = 32. truthFSD's 2,957 s covers 8 decisions of 16 truth-side runs per
  design; telling runs 32 per design and side once, about a quarter of that plus the member side, so the
  allowance is 2,957 s a cell, about twice the reasoning: 32.9 h.
- B-boed: unmeasured; allowance 80 × 2 × 340 s = 15.1 h. Scores: 400 × 115 s = 12.8 h.

**Ceiling 300 CPU hours** (sum 252.2 h plus 19%), enforced by the driver's CPU meter with `STOP`;
reaching it ends the run, and what is unfinished is reported as not run. At 2 workers that is up to about
6 days of wall time, so the block runs after P4 and after stage 2 of any tuning. **LLM:** 160 Sonnet
sessions under Max, no API key; at P3's 346 s mean and 1.5× for experiment use, about 23 hours at
concurrency 1. The dev measurement of 2.3 alone: 20 × (2 × 340 + 2,957 + 2,957) s plus 40 scores =
37.9 CPU hours, ceiling 50, $0.

## 9. Early-stop conditions

The CPU ceiling; a usage-limit error (stop, record, no retry loop); control 5's failure stops the LLM
block (rule 5.3); a machine fault named in the journal first. No stop on any outcome of a read.

## 10. Reported whatever the outcome

Control 5's verdict with b, c, p and both telling rates; the five reads with CIs, labels, n and the
rows-left-out reads; whether each is confirmatory; the at-stop rule used and A1's record under it;
degenerate decision counts; truncation and stop counts; the ceiling's use; every failed unit.

## 11. Code at the freeze

To fill at the freeze: the commit, the read module, the settings' digests, and the line
`sha256: <hex>  docs/v2/prereg/2026-11-07_p4b-policies.md`.

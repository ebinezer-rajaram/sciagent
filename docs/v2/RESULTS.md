# v2 results: stopped after the pilot

The owner stopped v2 on 2026-11-08 (`docs/v2/INBOX.md`, "STOP v2 AFTER ONE CLOSING
SESSION"). This file is the closing record. Its design is [`../SPEC.md`](../SPEC.md); the
pilot's full report is [`PILOT.md`](PILOT.md); the decision record is [`LOG.md`](LOG.md), which
is large, so search it rather than reading it end to end. v1's record is
[`../v1/RESULTS.md`](../v1/RESULTS.md).

## The question

v1 tied retrieval exactly, and the tie was forced by its design (`../v1/RESULTS.md`). v2 asked
the question again under conditions where some system is shown able to win:

> In an open mechanism space too large to enumerate, does an LLM agent find out-of-library
> mechanisms better than symbolic search, sparse regression over a feature dictionary and random
> generation, at an equal budget of model fits? (SPEC §1, Q1, the primary question)

Q2 to Q7 (anonymisation, the evidence wall, real data, scaling across models, experiment design,
scientific behaviour) were to be read in the main campaign, P4, and its sequel P4b.

## What ran

Phases P0 to P3 of SPEC §7. P4, P4b, P5 (real data) and P6 (write-up) never ran.

- **The pilot (P3)**, on `dm-devbox` (Ubuntu 24.04, 4 vCPUs), at commit `900e66a`, from
  2026-10-29 to 2026-11-01: 20 dev truths × 3 seeds. Systems: ORACLE (the true structure,
  refitted), B-lib (closed library), B-np (nonparametric, no structure), B-rand (random
  proposals), B-sym (symbolic search, **untuned**), B-sparse (sparse regression over a feature
  dictionary), planted-hint (scripted proposer that is handed the truth's hint), B-sym at 10×
  the fit budget on a declared 10-truth subset at seed 1, and four LLM arms: the constrained
  agent (AG-c) and the open agent with a code sandbox (AG-o), each with domain names (named) and
  with names stripped (anon), all on `claude-sonnet-5-5` under the Max subscription. 670 scored
  units (430 non-LLM, 240 LLM sessions), 0 failed units, 0 void sessions. The budget was
  F = 40 fits and E = 8 experiments per investigation. Baselines saw the observational log only; the LLM arms could
  also run experiments. Every system's submitted structure was refitted on the same log and
  scored on one shared held-out log per (truth, seed).
- **The pilot's checks**: an offline replay of all 240 LLM sessions (239 PASS; the one FAIL is
  an agent's own `time.time()` call, counted apart by a rule fixed beforehand), a full rescore
  of the 240 sessions under the current fitter (230 identical, 10 equal once `-inf` is
  normalised), and a pre-registered check of the fitter's search misses on every pilot unit.
- **Control-instrument work for P4b** (falsification-seeking design, FSD): calibration of
  control A1 and two exploratory checks of a seed-handling lead.
- **B-sym tuning for P4** (pre-registered 2026-11-06, launched 2026-11-07), stopped by the
  owner's order on 2026-11-08 before it produced a selection. Nothing from its store is reported
  here.

Every run was CPU-only on `dm-devbox`; no AWS compute was launched (see "Cost").

## What it found

All numbers below are the pilot's as recorded in `PILOT.md`. They are **descriptive, on the dev
split**: 20 truths, 3 seeds, a design built to size P4, not to answer Q1. B-sym was untuned.

### The instrument works

The pre-registered positive controls (SPEC §6.4) all passed, and an independent script that
imports neither the analysis nor the gate code reproduced every figure:

1. **ORACLE beats B-lib** on 20 of 20 truths (smallest margin 0.0266 nats/event, smallest
   margin/SE 5.3), with no `oracle_underfit` flag and no `-inf` on 60 units.
2. **Planted-hint beats B-rand**: 18 wins, 2 losses, one-sided Wilcoxon p = 4.768e-06, median
   difference 0.0245 nats/event, 95% CI [0.0148, 0.0306].
3. **B-sym at 10×F approaches ORACLE**: median gap 0.0000 (pass if ≥ -0.01), on its declared
   subset of 10 truths at seed 1 with no mid-stratum truth.

The difficulty was in band (neither too easy nor too hard, SPEC §7.1), so the rules computed
**GO**. The owner gave the go on 2026-11-05 without reading the AG-o transcripts and delegated
that reading to AI agents (below, "Deviations").

### The agents showed no advantage over cheap baselines

Shares are over the 20 truths; "closes" means closing at least 0.9 of the gap between B-lib and
ORACLE on held-out per-event log-likelihood; "recovers" means an exact canonical match of the
true structure.

| system | recovers | closes | median gap closed | mean gap (nats/event) |
|---|---|---|---|---|
| B-rand | 0% | 10% | 0.49 | -0.0577 |
| B-sym (untuned) | 7% | 28% | 0.61 | -0.0528 |
| B-sparse | 3% | 47% | 0.90 | -0.0214 |
| planted-hint | 17% | 43% | 0.85 | -0.0298 |
| B-sym at 10×F (10 truths, seed 1) | 50% | 90% | 1.00 | -0.0012 |
| AG-c-S/named | 13% | 25% | 0.62 | -0.0526 |
| AG-o-S/named | 17% | 32% | 0.68 | -0.0481 |
| AG-c-S/anon | 17% | 33% | 0.69 | -0.0506 |
| AG-o-S/anon | 12% | 27% | 0.72 | -0.0474 |

- On held-out fit, the four agent arms (median gap closed 0.62 to 0.72) sat with untuned B-sym
  (0.61) and below B-sparse (0.90), a cheap non-LLM baseline that sees only the observational
  log. B-sparse also closed the gap on more truths (47% against 25 to 33%).
- On exact recovery the agents (12 to 17%) were above B-sym at budget F (7%) and B-sparse (3%),
  level with planted-hint (17%), and far below B-sym given ten times the fits (50%).
- **Out of dictionary**, the case Q1 is about, no agent arm recovered a single truth exactly (0%
  in all four), and they closed the gap on 7 to 13% of those truths against B-sparse's 23%. On
  the far stratum the agents closed on 0 to 4% against B-sparse's 30%. Their successes were
  on near truths (closing 62 to 67%), where B-sym and B-sparse did as well or better (67%,
  76%).
- Per fit, the agents were not more efficient: at 40 fits their median best-so-far gap was
  -0.029 to -0.044 nats/event, against B-sparse's -0.009 from its first fit.
- Named and anonymised arms looked alike, as did the constrained and open agents. Nothing here
  separates them.

**What this does and does not establish.** No positive control covers a comparison involving an
LLM arm (`PILOT.md`, "What no control shows"), and these are dev-split pilot numbers with no
confidence intervals on any agent-against-baseline difference. So this is not a tested
contrast. It is the plain reading of the pilot: on the instrument the controls validated, the
agents did not beat cheap baselines on the held-out score, and did worst exactly where an
advantage was the point, on truths outside the dictionary. The owner reads it as a null, as v1
was, and stopped the project on it.

### Cost and behaviour of the agents

Median per session (Sonnet 5.5 under Max, `PILOT.md` "P4 sizing"): 24 to 40 tool calls, 4,012
to 9,158 output tokens, 120 to 189 s wall; about 12.6 to 17.0 distinct model responses per
session on average. All 240 sessions sum to 24.18 h of driver wall. Between 25% and 47% of
sessions per arm ran no experiment (median 1 or 2 of the 8 allowed), and committed predictions
were rare (0 to 10% of sessions made any accepted one). Three AG-o-S/named sessions ended on the
one-hour wall limit without a submission, all among the early sessions that ran beside the
baselines on a loaded machine.

## Caveats on the numbers

Each is recorded in `LOG.md` under the date given.

**Deviations from the design or a pre-registration.**

- **B-sym was untuned in the pilot** (SPEC §9 required tuning on dev with the agents' effort).
  Found 2026-11-06, before any test data. Every B-sym, B-sym at 10×F and planted-hint number
  above uses `GPConfig`'s defaults. The comparison with B-sym at budget F is therefore not
  against the tuned rival SPEC asks for.
- **The GO was given without a human reading the transcripts** (P4-PROPOSAL §2.4). The owner
  gave the go on 2026-11-05 and delegated the reading. Two analyst agents read 5 of the 240
  records chosen by rule, and a red-team agent checked them
  (`P3-TRANSCRIPT-READ-A.md`, `-B.md`). No human has read a v2 transcript.
- **Control 3 was read on a declared subset**: 10 truths at seed 1, none from the mid stratum
  (LOG 2026-10-02). SPEC §6.4 names no subset.
- **The fitter's search can miss** (fit/5). A pre-registered check over every pilot unit found
  25 miss rows (23 on baselines, the rows the verdict reads). The largest held-out move was
  0.02198 nats/event. The computed GO survived (`GO_SURVIVES_FOUND_MISSES`, `PILOT-MISSES.md`).
  Misses the search did not find are not bounded: 131 of the 397 fits the verdict reads were
  searched, not exhaustive. An earlier, smaller stress figure (0.0114) was wrong and withdrawn
  on 2026-11-04 after decisions had been taken on it.
- **An unapproved draw on a control's stream** (2026-11-05). During the owner-approved FSD
  check (b), a helper's test mutant with the guard removed drew `ctlA1-cal-r0` for about four
  CPU minutes. The values were never written or seen. It is recorded beside the control
  (`.cache/drafts/fsd-controls/DEVIATION-2026-11-05.md`), and helpers are now forbidden to
  bypass a guard.
- **The B-sym tuning store was read before its record**, twice (2026-11-08, sessions 39 and
  40). Its prereg forbids this. The run was then stopped by the owner's order and not for a
  machine fault, which its prereg also does not allow. It never selected, so neither can move
  a number.
- **Commit trailers.** 22 commits on the `p4-rehearsal` branch carry attribution trailers
  against the lane's rule. They were kept, because 16 were already pushed (LOG 2026-11-06).

**Incidents and defects found.**

- **The platform move.** v2 began on a Windows PC, which was retired. Output bytes depend on the
  machine class, so the whole pilot was rerun from scratch on `dm-devbox` (LOG 2026-10-27).
  The Windows pilot's audit had found ORACLE was not an upper bound on 22 of 60 units, because
  fit/4's search stalled. That led to fit/5 (`PILOT-AUDIT.md`). No number here comes from
  Windows.
- **Wall-time cuts wrote late.** A tool call still running at a session's one-hour cut could
  reach the lab state after the record closed. That happened once (AG-o-S/named dev-012 s1:
  35 fits read against 34 charged). The store is marked `STORE INCONSISTENT` for that line,
  and no score moves (`PILOT.md`).
- **A loaded, shared machine.** Load reached about 20 on 4 vCPUs during the first LLM sessions.
  Their median wall was 4.85 times that of matched later sessions, and all three wall-time cuts
  fell in that window.
- **Replay.** 239 of 240 sessions passed the offline replay. The one failure is the agent's own
  clock read, counted apart by a rule fixed before the replay reached it.

**Controls built for P4b, and their state.**

- **FSD control A1 failed** (2026-10-29): 78 of 120 against the 84 required. A lead about seed
  handling was checked twice. Both checks are exploratory, and neither changes the failure.
- **Control 5 has no passing evidence.** Under the current rule, truthFSD picks the telling
  design on 3 of 14 dev truths. P4b was therefore re-planned as exploratory.
- **Control 2's rule for unchecked truths was not merged** (2026-11-08). The red team showed
  that an unchecked win could open it. The pilot's verdict has no unchecked truth, so it is
  unchanged.

## What was not tested

- **P4 never ran.** No test-split truth was scored, no LLM other than Sonnet 5.5 was run (no
  Haiku, no Opus: Q5), the `rescaled` condition was not run (Q2's split), and there was no
  budget sweep. None of SPEC §6.2's contrasts C1 to C4 was read, with or without a CI.
- **P4b never ran.** No evidence-policy comparison (FSD against EIG, Q6), no AG-S-boed
  ablation (Q3, the evidence wall), no B-boed. Contrast C5 and C3's AG-S-boed half were never
  read.
- **P5 never ran.** The agent never saw the real SCEDC QTM catalogue (Q4).
- **Q7's behaviour measures** (calibration, falsification-seeking, revision, stopping) were
  not computed: the record carries no explicit belief timeline. Only raw prediction coverage
  exists, from 0 to 6 predictions evaluated per arm.
- **Tuned B-sym.** The pilot's B-sym used untuned defaults; the tuning run was stopped before
  its selection. A tuned symbolic search might do better than the table shows; that is not
  known.
- **No human read any transcript** (above, "Caveats").

So v2's question, like v1's, is not answered by a confirmatory test. What v2 adds is that on a
validated instrument the pilot gave no sign of an advantage worth the main campaign's cost.

## Cost

**AWS: $0.** No AWS compute was launched in v2. S3 holds copies of the stores, logged at $0.
`LEDGER.csv` has 52 rows, all on `dm-devbox`, summing to 244.85 hours. That figure mixes CPU
hours, elapsed hours and estimates, so read it as a rough size, not a CPU count. The pilot's
LLM half took 24.8 h elapsed for 240 Sonnet 5.5 sessions under the Max subscription, at
concurrency 1. No usage credits or API keys were used. The stopped B-sym tuning run had used
52.55 CPU hours by its own count at the stop and has no ledger row (see the hand-over).

## Where the data are

- Pilot store: `.cache/pilot-merged/` on `dm-devbox`, merged from `.cache/pilot` and
  `.cache/pilot-llm`; the two halves are on S3 under
  `s3://delaymargin-results-482913/sciagent/p3-linux/` (`pilot-900e66a/`, `pilot-llm-900e66a/`),
  with the run logs (`logs-900e66a/`) and the report's review scripts (`report-900e66a/`).
  Reproducing
  `PILOT.md`: its "How this file was made" section, at the commit it names (the p4-deltas merge
  changed the readers; regenerate from the merge's first parent).
- Miss check: `docs/v2/PILOT-MISSES.md`; records under `p3-linux/search-misses-3226f9b…/`.
- B-sym tuning store, stopped and incomplete: `~/lanes/sciagent-bsym-run/.cache/drafts/bsym-tune/`
  (no result is read from it).
- Nothing was deleted.

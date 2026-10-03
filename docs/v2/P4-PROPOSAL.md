# P4 proposal: close P3, then redesign and run the main campaign

2026-10-03. **For review: approve or reject each numbered decision in §5.**
Sources: `docs/v2/PILOT.md` (verdict), `docs/v2/PILOT-AUDIT.md` (audit),
`docs/v2/TRANSCRIPTS.md` (behaviour), `docs/v2/NIGHT-2026-10-01.md`, LOG
entries from 2026-10-02. This document compares no systems. The §6.4 controls
are under audit, and every number quoted here describes an instrument or a
behaviour.

## 1. Status

P3 has a **provisional GO** (PILOT.md:117). Two things are needed to close it:

1. **The ψ-search fix and a rescore** (audit §1). ORACLE is not an upper bound
   on 22 of 60 units. The coordinate ψ search in `fit._search`
   (src/sciagent/glm/fit.py:515-536) stalls on coupled period/phase slots
   above the 512-point cap. GO survives when ORACLE is re-referenced, but every
   gap, every "closes" share and control 3 move. The true post-fix numbers are
   unknown until the rerun.
2. **The human gate** (SPEC §7, P3 row): you read the AG-o transcripts in
   full. Two of the four picks in PILOT.md:265-268 were chosen by "gap closed"
   of 37.32 and 26.77, which are artifacts of the defect (a near-zero
   denominator on dev-008). The best and worst picks will change after the
   rescore. The "most sandbox use" pick (dev-013 anon s2) stands.

Two audit findings are smaller and fold into Step 0. Control 2 is a genuine
pass, but its printed mean of 6.77 is uninformative (audit §2). The 9 voids are
a policy bug, not a breach (audit §3).

## 2. Step 0, prerequisite: "P3.1"

### 2.1 Fitter fix, written test-first (from audit §1)

Write the tests first and watch each fail:

- **T1 (fails today by about 0.1 nats/event).** Use a two-`Gate(…, PhaseWindow)`
  truth like dev-008 (period 10, phase 0). `fit(truth.structure, sim)` must
  reach log L ≥ the θ-refit at the truth's ψ point.
- **T2 (Hypothesis).** For structures with more than 512 ψ points, the fit's
  log L ≥ the log L at k seeded random grid points.
- **T3.** With the same seed, config and version, the output is byte-identical.
  The exhaustive path (≤ 512 points) is unchanged bit for bit. This is a
  regression check.
- **T4 (report).** "Gap closed" is flagged, not printed as a ratio, when
  |LL_oracle − LL_lib| is within its paired noise.

Then implement:

- **(a) Block-coordinate search.** Optimise all slots of one feature jointly,
  since a feature's grid is small (for example 6·5·4 = 120). Run it from the
  top-k points of the start sub-grid, with a fixed k in `FitConfig` so it
  enters `FitConfig.key()` (fit.py:195). Keep the lexicographic tie rule.
- **(b) ORACLE check.** ORACLE also evaluates the truth's ψ point and keeps
  the better one. It records `oracle_underfit` when its training LL falls below
  the true-parameter training LL. This is legitimate because ORACLE runs on the
  framework side (invariant 2).
- **(c) Version and correctness.** Bump `FIT_VERSION` to `sciagent.glm.fit/5`
  (fit.py:138) and log the change in LOG. Also correct the agent prompt's
  claim that fits are "globally optimal on the grid"
  (src/sciagent/investigation/prompts.py:97). That claim is false for
  coordinate fits.

### 2.2 What must be rerun: content addressing

`FIT_VERSION` and `fit_config.key()` enter **every** key through `_key`
(src/sciagent/campaign/plan.py:316-330): `env_version = "<env>|FIT_VERSION"`
and `config.fit_config`. So a fitter change gives every cell a new address.
Nothing is overwritten, and the append-only store (invariant 4) holds. But the
driver will then treat everything as not done:

- **Non-LLM units: all of them rerun** (8 systems, 430 units). B-lib and B-np
  should reproduce their readings byte-for-byte, because library ψ grids are
  exhaustive and B-np does not fit. That makes a free regression check.
- **LLM sessions: only their scoring needs rerunning.** The go/no-go verdict
  reads non-LLM systems only (audit §3). `run_score_job` refits the submission
  and every charged fit's structure from the session cell's text
  (src/sciagent/campaign/execute.py:377-416).
- **The trap.** `session_key` also carries the FIT_VERSION and fit_config key
  (plan.py:365-385). Unchanged, `_plan_llm` (src/sciagent/campaign/driver.py:262-273)
  would find no done session and **relaunch all 240 live sessions**.
- **The fix.** Add a rescore mode. It enumerates the existing `llm-session`
  cells from `store.cells()` (their key fields are stored, store.py:62-80)
  instead of recomputing their keys. It then writes new `llm-score` cells:
  `score_key` = session digest + the new env_version, so the new score cells
  do not collide with the old ones.
- **Declare the limits.** These sessions were *conducted* under fit/4, so the
  agents saw stalled fits. One agent noticed this (TRANSCRIPTS.md:188-190).
  For the dev pilot that is acceptable and must be declared. Their records
  will **no longer replay** under fit/5, because replay re-executes `fit`.
  Tag the pre-fix commit (`p3-fit4`) so they stay replayable.
- **Dev split: keep it.** `config_digest` includes FIT_VERSION
  (src/sciagent/scenarios/sampler.py:212-217), and identifiability is checked
  by fitting (scenarios/identify.py). A fit/5 regeneration could therefore
  give a different population and invalidate all 240 sessions. Instead, rerun
  `check_identifiable` on the 20 dev truths under fit/5 and report any truth
  that changes status. A fixed search can only fit the truth better, so a flip
  is unlikely.
- **Analysis fixes, same rescore.**
  - Void policy (Δ8 below).
  - Control-2 statistic (Δ10 below).
  - Consistent void exclusion: driver.py:279 currently scores void sessions,
    because their status is 1.

### 2.3 Compute on this machine

These are sums of `timing.jsonl` wall times at fit/4. The units ran under 5-6
concurrent workers, so the sums are worker-hours.

| rerun | worker-h |
|---|---|
| B-rand 17.1, B-sym@10F 18.2, planted 7.1, B-sym 6.9, B-sparse 6.0, ORACLE 0.6, B-lib 0.3, B-np 0.3 | 56.5 |
| 240 LLM scores (means 93-129 s) | 7.6 |
| **total at fit/4 speed** | **64** |

At 6 workers this takes **≥ 11 h of wall time**. The critical path is the
slowest B-sym@10F unit, 3.2 h (PILOT.md:279). Block search with k starts
costs more per coordinate fit, and 42 of the 60 ORACLE fits are coordinate
fits. **Before launching, measure the slowdown s** on dev-008 ORACLE plus one
B-sym unit; this takes about 30 min. At s ≈ 2 on the coordinate-heavy units,
expect **about 20 h**, one overnight run plus a morning. No LLM quota is used.

### 2.4 The §7.1 re-read after the rerun (rules fixed now)

`pilot_report.py` recomputes each item from the rerun cells only. Each one is
then read as follows:

- **Too easy.** This fires if either:
  - B-lib or B-rand recovers ≥ 70% exactly; or
  - B-sparse closes ≥ 0.9 on ≥ 70% of the out-of-dictionary truths.

  The audit's re-referenced estimate was 17% for B-sparse out of dictionary.
- **Too hard.** This fires if every non-LLM system closes on < 10% of truths.
- **Controls.**
  - C1 must pass with zero `oracle_underfit` flags.
  - C2 is read by the rank rule (Δ10).
  - C3 requires the B-sym@10F median gap ≥ −0.01. The audit estimated −0.0031,
    which is close to the line.
- **If any rule flips:**
  - a control failure means fixing the instrument first (§7.1);
  - for C3 specifically, a failure means B-sym is under-tuned, and the cure is
    the dev tuning the LOG already requires (2026-10-02, search baselines);
  - a difficulty flip means a re-pilot.

  **No rule is reinterpreted after the numbers are seen.** P3 closes on a
  rerun GO plus your transcript read.

## 3. P4 redesign: SPEC deltas

"Key" means the effect on run keys. Every P4 cell is new anyway (test split,
fit/5, new harness), so the key consequence mostly concerns mixing with the
pilot store. **E** = essential, **O** = optional.

| # | SPEC § | Change | Why (pilot evidence) | Key / rerun | E/O |
|---|---|---|---|---|---|
| Δ1 | §2.2, §6.3 no. 2 | ψ block-coordinate multi-start search, plus ORACLE's truth-ψ check and `oracle_underfit` (§2.1) | Audit §1: 22/60 ORACLE fits under the truth-ψ point | fit/5: every key changes (§2.2) | **E** |
| Δ2 | §4.0 `run_experiment` | At the event cap, return the log **truncated at the 15,000th event** (world.py:65), flagged `truncated_at=τ`. It is charged as an experiment and stops at the cap. Instrument test: the likelihood on [0, τ) is the standard one, because the N-th event is a stopping time, and the fitter recovers parameters on truncated logs. | 54 of 210 `inject_marks` produced nothing, and 40 sessions lost ≥ 1 experiment (TRANSCRIPTS.md:98, 104-107). The stop message is at tools.py:960-967. | Tool semantics change: bump the tools version (Δ7) | **E** |
| Δ3 | §4.0 `run_experiment` | **Design generators:** `times: {start, step, n}`; mark patterns `constant`, `alternate[a,b]`, `repeat[...]`; AG-o can also pass `from: "/work/x.json"`. Explicit arrays are kept. | 134 of 155 rejected calls were `force_events` arrays whose counts drifted (TRANSCRIPTS.md:161-167) | as Δ2 | **E** |
| Δ4 | §4.0, §4.4 | **Belief timeline:** a new `belief(favoured, rivals[≥1], confidence)` tool. The tool layer **requires** a fresh belief before each `run_experiment` and at `submit`, with a stop reason at submit. `predict` gets a structure-only `hypothesis` field plus a free `note`. The prompt asks for one prediction under a rival. The §4.4 measures are computed from this record. | The notebook was used 0/240 times, thinking is redacted, and only 10 predictions were committed, all confirmatory. Revision, falsification and stopping are not computable (PILOT.md:249; TRANSCRIPTS.md:35-40, 136-142, 294-296). 11 `predict` calls were rejected for commentary in `hypothesis`. | as Δ2. Adds instrument test 10b: the timeline is framework-recorded, and a scripted agent gives known §4.4 values. | **E** |
| Δ5 | §4.0 | **`contrast(a, b, intervention, diagnostic)`:** free and framework-computed. It reports how far two fitted rivals' predicted diagnostics separate under a design. | Experiments are used as "more data"; a median of 7 of 8 go unused; time-domain interventions go unused (TRANSCRIPTS.md:273-285) | as Δ2. It **blurs C3/C5**, because it hands the agent an FSD-like quantity. | O |
| Δ6 | §4.0, prompt | (i) Correct the "globally optimal" claim (prompts.py:97). (ii) **Anchoring check on dev** before freeze: the current prompt against a link-neutral variant (examples rotated across all three links, one depth-3 example, and the scoring sentence at prompts.py:199-200 restated as "scored on held-out prediction and structural distance"). Run AG-c-S named, 20 dev truths, seed 1, 20 sessions per variant. Read-outs: share trying a non-identity link, and share trying depth ≥ 2. Freeze the variant before the test split. This is prompt tuning on dev, as SPEC §9 permits for B-sym. | 40% of sessions only ever tried identity; 31 sessions on softplus truths never left it; Product appears in 14/240 sessions and depth 3 in 5/240 (TRANSCRIPTS.md:51-74, 275-281). The examples (prompts.py:125-130) all start at identity and depth ≤ 2. | Prompt text is **not in the session key today**: `session_text` (plan.py:356-362) hashes only `HARNESS_VERSION`. See Δ7. | **E** for (i) and the check. The chosen variant is decision 9. |
| Δ7 | §6.1, campaign | The session key hashes the **rendered system prompt and tool schemas** per (arm, condition). Bump `HARNESS_VERSION` (live.py:111) for Δ2-Δ4 and Δ8. | Without this, a prompt edit silently resumes old sessions under a new prompt | Keys change once | **E** |
| Δ8 | §9, §4.0 | **Void policy.** A run is void only if a foreign tool *executed* (a non-error result), on server tool use, or on an out-of-bounds hit. Denied unknown names are counted as `unknown_tool_attempts`. Analysis drops voids and counts them in every table. Tests as in audit §3. | All 9 voids were a denied bare `python` name; nothing ran (audit §3; runner.py:369) | Harness change: bump `HARNESS_VERSION`. Pilot: reclassification at rescore only. | **E** |
| Δ9 | §5, §6.2 C2 | **Separate time scale from naming.** Add a third condition, `rescaled`: names kept, time ×8. Run it on Sonnet, both tiers. C2 is then reported as two paired differences: named → rescaled (time) and rescaled → anon (names). | Anon changes channels, diagnostic names and time together, so a difference cannot be attributed to naming (TRANSCRIPTS.md:226-231) | New condition: new cells only | **E** (C2 is preregistered) |
| Δ10 | §6.4, analysis | **Control-2 statistic:** one-sided Wilcoxon plus a bootstrap CI of the per-truth median difference and the win count. −inf enters by rank (lowest). The mean is printed only beside them. Gap closed is flagged when the denominator is within noise (T4). | Mean 6.77 and CI upper end 20.27 are carried by one truth; `paired_bootstrap_ci` cannot take −inf (audit §2) | Metric change: bump `METRIC_VERSION` (plan.py:78). Scores only. | **E** |
| Δ11 | §4.1, §4.2 | **Build the evidence-policy arms**: random, EIG and FSD policies for B-sym and AG-c-S, plus AG-S-boed. None exists in v2 yet; `systems/v2/` has no BOED or FSD. | C3 and C5 cannot run without them | New systems | **E** for C3/C5, or descope (decision 12) |
| Δ12 | §4.0 | Show the remaining E and F in every tool result | No pre-submit text mentions the budget (TRANSCRIPTS.md:213-216) | as Δ2 | O |
| Δ13 | §4.3 | Lengthen the interventional battery horizon (NIGHT decision 6) | It separates S11 from Hawkes only modestly | `METRIC_VERSION` | O |

**Before the §6.1 freeze** (these are not deltas, but they block it):

- tune B-sym on dev and freeze it (SPEC §9);
- resolve the B-sparse fold exemption (NIGHT decision 1);
- run Δ6's dev check;
- run a **P4.0 dev smoke**: 10 dev truths × seed 1, with AG-c and AG-o on each
  model, named. That is 60 sessions. It checks Δ2-Δ4 end to end, replays two
  records offline, and measures **Haiku and Opus** wall time and Max quota,
  which P3 never measured.

## 4. P4 main campaign (SPEC §7, P4 row)

**Split.** A fresh `test` split is sampled under fit/5 and frozen with its hash
committed (§6.1). Proposal: **n = 40**, out-of-dictionary share exactly 0.5 by
stratification, **seeds {1, 2}**, giving 80 worlds. At P3's 6% acceptance,
sampling takes about 1.7 h on 4 workers. Truths are the bootstrap unit for
C1-C5, and seeds reduce within-truth noise. The pilot showed that seed-to-seed
variation is large (TRANSCRIPTS.md:9-10).

| block | design | units |
|---|---|---|
| main LLM grid | AG-c, AG-o × {H, S, O} × {named, anon} × 80 worlds | 960 sessions |
| Δ9 `rescaled` | AG-c-S, AG-o-S × 80 | 160 sessions |
| AG-S-boed (C3) | named × 80 | 80 sessions |
| budget sweep | AG-c-S, AG-o-S named; 20 truths × seed 1; E ∈ {0, 2, 16} at F = 40; F ∈ {20, 80} at E = 8 | 200 sessions |
| §4.2 policies (C5) | {random, EIG, FSD} × {B-sym, AG-c-S} × 40 truths × seed 1; agent-chosen = the main AG-c-S cell | 120 baseline + 120 sessions |
| non-LLM | ORACLE, B-lib, B-np, B-rand, B-sym, B-sparse, planted × 80; B-sym@F=80 × 20 | 580 units |
| control 3 | B-sym@10F × 40 truths × seed 1 | 40 units |
| scores | one per session | 1,520 |

**Compute** (fit/4 per-unit means; the slowdown s from §2.3 and model speed
are both unmeasured):

- **Non-LLM:** 2,303 s per world × 80 = 51 h. B-sym@10F: 40 × 6,541 s = 73 h.
  F = 80: 5 h. Policy arms: about 28 h if each costs 2× B-sym.
  Scores: 1,520 × 115 s = 49 h. **Total about 205 worker-h, about 34 h at
  6 workers.**
- **Sessions:** Sonnet mean 346 s (PILOT.md:286-289). Assuming Haiku 0.7× and
  Opus 1.5×, which are **guesses for P4.0 to replace**, total session wall time
  is about 160 h. That is **about 80 h at LLM concurrency 2**, run alongside
  the pool. Δ2-Δ4 should raise experiment use, and agents that run
  experiments use about 2× the fits (TRANSCRIPTS.md:118-122), so allow 1.5×.
- **Estimate:** about **4-6 days** of wall time on this machine. Opus quota
  under Max is the main unknown. The option "40 × seed 1" roughly halves
  every block except control 3.

**Positive controls that must pass before any C1-C5 number is reported**
(SPEC §6.4, run on the test split under fit/5):

1. ORACLE beats B-lib on every truth, as the paired margin > 1 SE, **and** there
   are zero `oracle_underfit` flags. A failing truth is dropped by the
   preregistered rule, and the drop is reported.
2. Planted-hint beats B-rand by the Δ10 rank rule.
3. The B-sym@10F median gap is ≥ −0.01.

Proposed additions, to be preregistered in §6.4 if you agree (decision 13):

4. **Naming null (gates C2).** The scripted planted-hint agent runs through the
   tool layer in named, rescaled and anon conditions and must give identical
   submissions and gaps. The time factor is a power of two, so exact equality
   is required.
5. **Policy separation (gates C3/C5).** A truth-aware FSD policy (truth against
   best library member) must beat random on the telling-diagnostic rate.
6. **Behaviour instrument (gates C6).** A scripted agent with a known belief
   timeline must reproduce known calibration, falsification and revision values.

## 5. Decisions for you

1. **Fitter fix** (§2.1: block-coordinate, top-k starts, ORACLE truth-ψ check,
   fit/5)? *Recommend yes.*
2. **P3.1 LLM units:** (A) rescore the 240 fit/4 sessions, declared, or
   (B) rerun them under fit/5 (about 12 h of quota)? *Recommend A.* The verdict
   does not read them, and P4 sessions are new anyway.
3. **Dev split:** (A) keep it and recheck identifiability under fit/5, or
   (B) regenerate? *Recommend A.* B invalidates every pilot session.
4. **Human gate:** read the new best and worst picks after the rescore, and
   dev-013 anon s2 now? *Recommend yes.*
5. **§2.4 rules:** if the rerun flips a §7.1 criterion, apply §7.1 literally,
   with no reinterpretation? *Recommend yes.*
6. **Void policy (Δ8):** void only on an executed foreign tool, server tool use
   or an out-of-bounds hit? *Recommend yes.*
7. **Control-2 rule (Δ10):** rank-based, with −inf as the lowest rank?
   *Recommend yes.*
8. **Belief entries (Δ4):** (A) tool-enforced before experiments and at
   submit, or (B) optional and prompted? *Recommend A.* B reproduces P3's empty
   record. A is a measurement intervention applied equally to both tiers;
   declare it.
9. **Prompt (Δ6):** (A) adopt whichever variant tries more links and depth on
   the dev check, or (B) keep the current prompt with only the factual fix?
   *Recommend A*, frozen before the test split.
10. **Rival-comparison tool (Δ5):** (A) off in P4, or (B) on in both tiers, or
    (C) on as a separate tier? *Recommend A*, because it confounds C3/C5.
    Revisit for P5.
11. **Anonymisation (Δ9):** (A) add `rescaled` on Sonnet only, or (B) a full
    2×2 on all models, or (C) drop time rescaling? *Recommend A.*
12. **C5 and §4.2 (Δ11):** (A) build the EIG/FSD policies before the freeze, or
    (B) descope C5 to a later P4b and freeze C1-C4 now? *Recommend B.* It
    takes Δ11 off the critical path, but C3's AG-S-boed half also needs the
    BOED pieces, so B moves that half too.
13. **Add controls 4-6 to §6.4?** *Recommend yes.*
14. **Test design:** (A) 40 truths × 2 seeds, or (B) 40 × 1, or (C) 60 × 1?
    *Recommend A.*
15. **Budget sweep:** Sonnet only, named, 20 truths, E ∈ {0, 2, 16},
    F ∈ {20, 80}? *Recommend yes.* E = 0 is the cleanest test of "do
    experiments matter".
16. **Models:** `MODELS` pins Haiku to `claude-haiku-4-5-20251001` and Sonnet
    and Opus to 5.5 (src/sciagent/harness/live.py:114-118), which confounds
    model scale with generation. Confirm, or name the Haiku id to use?
    *Recommend you confirm what Max serves, and declare it either way.*
17. **Optional Δ12 (show remaining budget) and Δ13 (battery horizon)?**
    *Recommend Δ12 yes and Δ13 no* (Δ13 is a metric change with no pilot
    evidence of harm).

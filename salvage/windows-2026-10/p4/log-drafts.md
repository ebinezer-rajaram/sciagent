## WP2 (Δ3 design generators), draft from the agent; 64 tests

2026-10-06, P4 Δ3 (design generators). `glm/designs.py` adds `expand(obj)`, a
pure JSON→JSON transform run before `experiment_from_json`. The recorded and
replayed design is therefore always explicit arrays, and a generator design is
the same experiment as its array form.

- `times: {start, step, n}` expands per k as `start + k*step`, not as a running
  sum, so the grid does not drift with n.
- Marks take `constant`, `alternate [a, b]` or `repeat [...]`. We chose that
  `repeat` must tile n exactly rather than truncate silently, because the
  pilot's 134 of 155 rejected calls were silent count drift.
- n is capped at `MAX_FORCED_EVENTS` before anything is built.
- Explicit mark arrays pass through unchanged, but a length mismatch is refused
  with its JSON path.
- Ordering, ranges and totals stay with the existing parser.
- Rescaling by a power of two commutes with expansion byte for byte.
- `DESIGN_SCHEMA` is `EXPERIMENT_SCHEMA` with the generator alternatives added.
- The WP6 tool layer publishes the schema and calls `expand` first.

## WP3 (Δ4 timeline), draft from the agent

2026-10-06, Δ4 timeline (WP3). §4.4's inputs are now a pure function of the
session record. `investigation/timeline.py` reads successful `tool_call`
entries in the layer's call order (`body["call"]`), using only the
framework-written `record` field.

- **Turns.** A belief's turn is the number of `run_experiment` records before
  it, and experiment j runs at turn j. `experiments_run` is checked against
  that count and a mismatch refuses the record. No agent argument reaches a
  turn.
- **Labels.** Every hypothesis is labelled `render(canonicalise(parse(·)))`.
- **Predictions.** Evaluated predictions come from the `run_experiment`
  record's `predictions`. Commitments whose experiment never ran, or was
  stopped, stay unevaluated.
- **Submission.** The submit is appended as a last `Belief` with no rivals, so
  submitting a refuted structure counts as a zombie return.
- **Beliefs.** `Belief` gains `rivals` and `confidence`, and `runner_up` is
  `rivals[0]`. `REVISION_K = 1`, and k counts inter-experiment intervals.
- **Stopped experiments.** They count on the clock, and their predictions are
  unevaluated.

Choices on points SPEC leaves open, for the user's review:

- **(a) The runner-up is `rivals[0]` only.** A prediction under `rivals[1]` is
  confirmation-only. Counting any rival would be a one-line change.
- **(b) Several beliefs in one turn** are kept in order. Wavering counts as
  revision.
- **(c) The submission is treated as a belief.**

## WP3 → WP7 contract notes

- The `predict` record must add `"hypothesis"` (canonical native text).
  Without it, a P3 record with a successful `predict` raises
  `RecordFormatError` in `timeline`.
- `_native_text` is `render(structure)` today, not canonicalised. Make it
  canonical.
- Belief `rivals` must be distinct from `favoured` after canonicalisation;
  refuse them in the tool otherwise.

## WP1 (Δ2 truncation), draft from the agent; decision 1 accepted by the orchestrator

1. **Truncation stops at the N-th event, whether or not an (N+1)-th would
   follow.** Stopping only when an (N+1)-th appears makes "truncated" depend on
   the future. With the stop at the N-th event, τ = min(t_N, H) is a true
   stopping time and Σ log λ − ∫₀^τ λ is exact. A run with exactly N events is
   now truncated at t_N. Runs below N are byte-identical (pinned digest
   b10272e9…ab0c).
2. **The tail window starts at nextafter(τ).** Excluded windows are closed for
   events. If nextafter(τ) ≥ H, no window is added. The tail is merged with the
   design's windows by `_union`.
3. **N counts every history event,** including forced, clamped and censored
   events. τ can be the time of a censored or forced event.
4. **World truncation is opt-in** (`truncate=False`) until WP6 wires it in.
5. **The observational log is never truncated.** A truth whose free log
   reaches the cap should fail loudly.
6. **`max_events=0` with truncate is refused.**

## WP1 → WP6 notes

- **Possible leak.** If the N-th event is censored, τ is a hidden event's time.
  The orchestrator directs: never show a raw τ that falls inside a
  censor/excluded window. Show the start of the merged excluded tail instead,
  which the agent can see anyway. Add a test for this.
- τ is in native units; rescale it by f for anon, and test through
  `rescale_dataset`.
- Pass `World(..., truncate=True)`.

## WP6 (tool layer Δ2/Δ3/Δ12), draft from the agent; committed as 289f742

2026-10-06, P4 WP6.

- **Δ2.** The investigation World truncates. A capped run is charged, and its
  record carries `truncated_at` (native τ) and `analysis_horizon`. Agent-side
  analysis (diagnostics, kernels, sandbox files, prediction evaluation) reads
  `clip_to_analysis`, which cuts at the shown end. Fits read the native dataset,
  whose tail is already excluded.
- **Leak rule.** The shown end is τ, unless a closed excluded window holds τ.
  Then it is that window's start, so a τ inside a censor or clamp window is
  never shown. We accept that this conservative cut drops visible clamped or
  forced events between the window start and τ. A cut at time 0 leaves nothing
  to analyse, and the tools say so.
- **Δ3.** `experiment_native` expands generators before validation, so the
  record holds arrays only. AG-o's `{"from": "/work/..."}` loads a design from
  a file:
  - the path must be plain segments under /work, and every component is
    `lstat`-checked, so links and reparse points are refused;
  - it must be a regular file of at most 1,000,000 bytes;
  - its sha256 is recorded, and replay verifies it.

  AG-c gets an error.
- **Δ12.** A ToolLayer footer, applied after charging, adds "budget left: e of
  E experiments, f of F fits" to every output, refusals included, before
  logging.
- **Stop text** for the remaining stop causes is reworded: the event rate
  grew without bound.

## ExactSum overflow (committed as 60ea94f), draft

2026-10-06: the `ExactSum.value()` overflow contract.
- **The flaw.** `math.fsum` raises OverflowError when a partial sum overflows,
  which depends on the order of its addends, even when the total fits.
  Hypothesis found a merge whose exact total is about −7.94e307 that raised.
- **Decided.** `value()` returns fsum's result whenever fsum answers, so
  results are bit-identical. Only on OverflowError does it fall back to exact
  integer arithmetic on the 2^-1074 grid, with one correct rounding. A total
  beyond DBL_MAX raises ExecutionError, a typed error. Non-finite addends keep
  fsum's semantics.
- **Tests.** The property test's reference moved to `Fraction`, because fsum
  shares the flaw. The counterexample is pinned with `@example`.
- **Follow-up.** `total()` and `row_totals()` have the same flaw; in progress.

## WP5 (Δ11 core), draft from the agent; committed as 7492ee5

2026-10-06, Δ11 core: evidence policies (WP5).
- **Menus.** Designs are the battery's 8 shapes at horizon 2000. Diagnostics
  are the whole catalogue in catalogue order, 35 for pointproc. Channel
  arguments expand in channel order, and pairs as i < j. Arguments are
  resolved on the observational log and frozen. The only required `period` is
  frozen at 1/`spectral_peak_frequency`, clipped.
- **Outcome.** The frozen diagnostic is read on the whole experiment log. An
  `InsufficientDataError`, or a stop at the 15k cap, counts as undefined.
- **Entertained set.** At most K = 4 structures, each refitted on all the data;
  failed or uncertified fits are dropped. w ∝ exp(−ΔBIC/2).
- **Predictive.** An undefined atom plus a Gaussian with ddof-1 variance,
  floored as in `hellinger2`.
- **EIG.** Σ w_k KL(p_k‖p_w), by 256-node Gauss–Hermite (64 nodes erred by
  3e-5). Identical components give 0, and each KL is clamped at 0.
- **Seeds.** `SeedSequence(seed, spawn_key=(POLICY, decision, k, r, *extra))`.
  There is no design index, so designs share common random numbers. Reserved
  k values: 2^32−1 is the reference, −2 the random draw, −3 the null, −4 an
  injected model.
- **FSD.** He²(top-weight fit, Wiener–Hopf on the observational log with iid
  empirical marks).
- **FSD null.** A parametric bootstrap, B = 8. Its sampling floor is about
  1/(2R), not 1/(4R): the mean and scale terms each contribute about 1/(4R).
  Measured 2.10/(4R) at R = 16 and 2.02/(4R) at R = 32. The "≈ 1/(4R)" in the
  `battery.py` docstring is off by 2; fix it in WP12.
- **Random.** One integer draw over the cells, with replacement. Ties go to
  menu order, designs before diagnostics.
- **Control 5.** `TruthFSDPolicy(truth, seed, comparator=None)`.
- **Telling diagnostic.** d* = argmax over d of max over e of He², R = 32 on
  `scorer_seed(TELLING, ...)`. **Stopped runs are dropped, not counted as
  undefined.** A design where either side always stops scores 0, and the
  stopped shares are recorded. Otherwise an exploding design ties every
  diagnostic at 1 (S11 vs Hawkes gave `mean_rate`).
- **Cost per decision** (fit/4, R = 16, M = 8, D = 35):
  - EIG at K = 3: 97–116 s;
  - FSD: 81–86 s;
  - one FSD-null bootstrap: 85–97 s.

  An exploding Wiener–Hopf design costs about 23 min per decision; a lower
  policy cap would mitigate it.

**Orchestrator decision.** Keep BIC posterior weights (SPEC §2.4 says "posterior
weights"). EIG ≈ 0 under a concentrated posterior is the evidence-wall
proposition, not a bug. WP5b adds a numerical tie floor (menu order) and a
pluggable "tempered" weight strategy, which is off by default.

## WP7 (committed c5191ec), draft from the agent

2026-10-06, P4 WP7 (Δ4 tools, Δ6 variants).
- **belief.** `belief(favoured, rivals[1..3], confidence∈(0,1))` is free.
  Every structure is labelled `render(canonicalise(view.parse(·)))` in native
  names. Rivals must differ from the favoured structure and from each other
  after canonicalisation.
- **Freshness.** A belief is fresh while `World.experiments_run` is unchanged.
  A refused design does not consume it; a stopped experiment does.
  `run_experiment` and `submit` check freshness first, so a refusal is
  uncharged.
- **submit.** `stop_reason` is a required enum. `note` holds at most 1000
  characters and is never echoed. 2000 collided with the native
  DEFAULT_HORIZON in the anon leak scan.
- **predict.** `predict.hypothesis` is structure-only, and a parse error points
  the agent to `note`. The submit record's `structure` is now canonical
  (`structure_hash` is unchanged).
- **Δ6 variants.**
  - v1 keeps the pilot's examples and scoring text byte for byte.
  - link-neutral changes only those two places. Examples: exp, softplus,
    identity two-feature, and a depth-3 softplus
    `Gate(Product(Periodic, Excite(ExpK, One, all)), LastMarkAbove(c))`.
    Scoring reads "held-out prediction and structural distance", which drops
    "a simpler structure that is right beats a larger one".
  - A record without a variant reads as v1.

## WP7 → WP8 notes

- **Inputs to the rendered surface:**
  - the arm;
  - the condition (view: names, scale, psi grids, example channels);
  - the channel specs;
  - `prompt_variant` (prompt only);
  - the budgets;
  - the code constants (MAX_*, DEFAULT_HORIZON, PSI_GRIDS, DESIGN_SCHEMA, the
    catalogue, STOP_REASONS).

  Not inputs: truth, seed, sandbox limits.
- **The opening prompt** depends on the observational log's n and horizon.
  Exclude it from the key.
- **Reuse** `rendered_surface(lab)` minus `opening_prompt`.

## WP5b (committed 9271879), draft from the agent

2026-10-06, WP5b.
- **Quadrature.** WP5's 256-node Gauss–Hermite EIG quadrature erred by up to
  0.14 nats at variance ratio 1e4: it steps over a narrow component's spike in
  log p_w. It is replaced by composite Gauss–Legendre (8 nodes, panels every
  0.5 sd of each component, ±12 sd). Its error against a dense trapezoid
  reference is ≤ 2.2e-16 over 306 cases. Cost: about 1.3 s per 280-cell
  decision.
- **Tie floor.** EIG_TIE_TOL = 1e-13 nats (100 × the error, rounded up to a
  power of ten). Values within it of the max are ties, broken by menu order.
  FSD has no floor: it is closed form, and its differences are Monte Carlo
  signal.
- **Weights.** BIC stays the default; tempered(T) is available, off by default.
- **Run key.** The strategy, tolerance and quadrature id enter each policy's
  canonical config.
- **Degeneracy summary.** Each decision records H(w), max EIG, the tied cells
  and a degenerate flag.
- **Caveat.** On dev-013, H(w) ≈ 3e-13, so some cells can exceed the floor
  with negligible EIG. The pick then follows tiny but correct values.

## WP4 (committed), draft from the agent

2026-10-06. Δ8 void policy at harness level (WP4).
- **One classifier.** It lives in `harness/voids.py`; the harness may not
  import campaign (a new invariant test). `campaign/voids.py` re-exports it.
  The single core, `judge(raw reasons, foreign results, server count)`, is
  shared by `InvestigationResult`, by `runner.run` (via `assess`) and by the
  analysis (`classify`), so they cannot drift.
- **Void when:** a foreign tool executed; a foreign use has no recorded denial;
  a server tool use (block or `usage.server_tool_use`); an out-of-bounds hit;
  an infrastructure or unknown reason.
- **Denied unknown names** count as `unknown_tool_attempts`, stored in the
  reading.
- **Stored reasons.** Policy-form cells store policy reasons. `judge` is
  idempotent on them, so the analysis reads them back unchanged.
- **Positive controls.** `ScriptedDriver.Step` gains `foreign` and
  `server_tools` (SPEC §6.4).

**WP4 → WP8:** the stored-cell semantics changed, so `harness/2` must land
before any campaign run. Rescore of `harness/1` cells stays correct.

## WP8 (committed as d6a46d6), draft from the agent

2026-10-06, P4 WP8 (Δ7): the session key and harness/2.

- **What the key hashes.** The LLM session key now hashes the rendered
  surface. `session_text` ends with the sha256 of
  `canonical_json(rendered_config(...))`: the system prompt plus every tool
  schema, for the given channels and anon names, arm, condition, budgets and
  variant. The variant and the policy are also named explicitly.
- **What it does not hash.** Truth, seed and sandbox limits. The opening prompt
  is excluded, because it reads the observational log.
- **The version.** `HARNESS_VERSION` is now harness/2, for Δ2-Δ4, Δ8 and Δ12.
- **Rendering.** Tool specs render without a World, and the surface is
  byte-identical to before the refactor.
- **System names.** A non-default variant or policy gets its own system name,
  `+link-neutral` or `+policy=<name>`.
- **Pilot sessions.** `legacy_session_text_v1` is frozen. Rescore matches in
  tiers: the current address first, then the current digest, then the legacy
  digest.
- **Why.** A prompt or schema edit used to resume old sessions silently under
  new text.
- **Extras.** `session_key` checks that the truth's channels match
  `env.channels`, `CampaignConfig` refuses duplicate system names, and policy
  names must match `[A-Za-z0-9_.-]+`.

## WP8 → WP9 / WP10 notes

- **WP9.** B-sym policy baselines are non-LLM. They go in BASELINES and
  `system_config_text`, not `LLMArm.policy`. Avoid `+` and `=` in baseline
  names. Keep the duplicate-name check. The policy config (strategy, tolerance,
  quadrature id) goes in `system_config_text`.
- **WP10.**
  - Replace `run(policy=...)` refusing with `InvestigationSpec.policy`,
    recorded and read back with a None default.
  - Add `policy` to `tool_specs(...)`, `rendered_config(...)` and
    `plan.surface_digest`, so the policy surface is hashed.
  - Extend `test_the_rendered_config_is_what_a_live_lab_shows`.
  - Validate the policy in `rendered_config`.

## WP10 (uncommitted: gates pending), draft from the agent

2026-10-06, P4 WP10 (Δ11 tool layer).
- **Policy mode.** A policy (random, EIG or FSD; AG-c only) makes
  `run_experiment` take no arguments, and the belief gate runs first.
- **Framework-internal fits.** EIG and FSD refit the latest belief (K ≤ 4) on
  obs plus every experiment dataset. These fits are uncharged and unshown.
- **The choice.** The committed core picks the cell, seeded by
  `policy_seed(seed, experiment index, ...)`. The design runs through the same
  `_run`, with the same charging, sealing and truncation.
- **What the agent sees.** The design in its own names and units, plus the
  chosen diagnostic and its value on the clipped data.
- **What only the framework sees.** `record.policy` holds the cell, config,
  BICs and weights, the score table, the `EIGSummary` (with `degenerate`) and
  any fallback. It is replayable.
- **Blinding.** The prompt never names the rule. The digest separates the
  policies by their config: `surface_digest` hashes the policy config for
  policy arms only (plan choice 16).
- **Stops.** The policy simulations treat a stop as undefined. The real run
  follows the world (truncation).
- **Fallbacks.** If no structure can be fitted, the decision falls back to
  random's draw. A core fault is an infrastructure void.
- **AG-S-boed** = `LLMArm("AG-c","named",policy="EIG")`, letter S, giving
  `AG-c-S/named+policy=EIG`.

## WP10 gate + rebase (2026-10-09, orchestrator; committed 98205b4 after rebase)

- **Policy fault containment (defect found at the WP10 gate, fixed test-first).**
  `Lab._run_policy` caught only `PolicyError`; other typed errors from inside a
  decision (`BoundViolationError`, `InvalidSimulationInputError`,
  `DiagnosticArgumentError`) reached the harness, which shows a SciAgentError's
  message verbatim — a leak of policy numbers or native channel names in anon,
  and the call was uncharged and not void. Now any `SciAgentError` in a decision
  is an infrastructure fault (void), and the agent is told only that nothing ran
  and nothing was charged.
- **Rebase onto v2 (ea055be).** v2's exact-denial-text rule (F4) was ported into
  `harness/voids.py` (WP4 had moved the classifier there; the harness must not
  import campaign). `ForeignResult` carries the result text; `campaign/voids.py`
  re-exports `denial_text`.

## WP9 (2026-10-09; committed 1327de9), draft from the agent

B-sym policy arms `B-sym+{random,EIG,FSD}`, control `B-sym+truthFSD`, and the
`telling` unit; opt-in (not in BASELINES); existing unit keys pinned unchanged.
SPEC-silent choices, for review:
1. Odd F: epoch 1 gets F//2, epoch 2 the rest.
2. Epoch 1 is plain BSym at F//2 with the arm's search seed (identical across
   policies); epoch 2's GP seed is `SeedSequence(search_seed, spawn_key=(2,))`.
3. Epoch 2 starts from epoch 1's top `GPConfig.population` certified structures;
   refitting them on new data costs epoch-2 fits.
4. Submission = best BIC among epoch-2 fits only (epoch-1 BICs are on other
   data); no certified epoch-2 fit fails the unit.
5. Entertained set = epoch 1's top K=4 certified by BIC, fixed, refitted
   (uncharged) on obs + collected data at every decision.
6. No certified candidate or refit → the decision uses random's draw, recorded.
7. Policy seed = the investigation seed (as WP10, plan item 15). See the C5
   seeding issue under WP11.
8. Experiments run on the (truth, seed) truncating World, paired with agent arms;
   E = `CampaignConfig.experiments`.
9. truthFSD: truth = `TruthModel(truth.spec)` built in execute.py as ORACLE's;
   comparator = B-lib's pick on obs.
10. Telling unit: own unit kind, R=32, cap 15,000; member = B-lib's pick on obs.
11. Scoring per plan choice 13 (refit the submission on obs only).
12. "Observed" for B-sym arms = the diagnostic the policy chose on an experiment
    that left data; a failed unit is not observed; rate weights truths equally.
- Open user decisions now settings: `PolicyArmSettings.weighting` (default BIC)
  and `PolicyArmSettings.max_events` (default 15,000 = current behaviour; moves
  only the EIG/FSD/truthFSD keys). WP10's AG-c-S `PolicySettings` has no cap yet.

## WP11 (2026-10-09; committed 6ac6142), draft from the agent

Preregistration text proposed for controls 4–6:

> **P4 positive controls 4–6 (preregistered; `scoring/controls_p4.py`).** Each
> must pass before the contrast it gates is reported; a failure means fixing the
> instrument first (§7.1).
> **C4, naming null (gates C2).** A scripted planted-hint agent (belief in the
> truth's hint; fit hint, then truth, on obs; one unintervened experiment; fit
> both on obs+e0; submit the truth) runs through the tool layer on the same
> truth and seed in every naming condition (named, anon; `rescaled` joins when
> Δ9 lands). It passes iff the canonical submission and every fit's structure,
> datasets and native log-likelihood are bit-identical to named's. The time
> factor is a power of two, so no tolerance is allowed.
> **C5, policy separation (gates C3/C5).** B-sym+truthFSD against B-sym+random,
> paired by (truth, seed) on whether d* was computed on experiment data (a
> failed unit counts as not observed). With b = units only truthFSD observed and
> c = units only random observed, one-sided exact McNemar p = P(Binom(b+c, ½) ≥
> b); pass iff p < 0.05. Unpaired units void the read. Truth-weighted telling
> rates of both arms are reported beside it. It cannot pass with fewer than 5
> discordant units.
> **C6, behaviour instrument (gates C6).** The 14-call scripted agent of
> instrument test 10b runs through the live loop; its record, read by
> `timeline`, must reproduce the hand values (refusals at calls 0, 5, 11,
> uncharged; calibration coverage 2/3 at mean level 11/15; A (1, 0, 0.9), B (2,
> 1, 0.65); falsification-seeking 1/3; one falsification, revised, one zombie;
> favoured path A, B, A, C; stopping converged at 3, 0 burned, not early, used
> share 1); floats at 1e-12 relative. Predictions in policy arms are excluded.

SPEC-silent: C4 submits the truth (the hint drops mark functions, so only the
truth's text carries channel names); C4 includes one experiment so the anon time
rescaling is crossed; C4/C5 refuse partial/unpaired input rather than dropping.

**OPEN (user decision): C5 random-arm seeding.** `policy_seed` has no truth id,
so on a given seed the random arm draws the same (e, d) sequence on every truth
(B-sym+random and AG-c-S+random). Pairing is intact, but C5 then compares
truthFSD against one fixed random draw, so its verdict does not generalise to
random selection. Proposed: key random draws by truth too. This moves the unit
keys of the four B-sym+* arms and AG-c-S+random, so decide before any policy
unit runs for real.

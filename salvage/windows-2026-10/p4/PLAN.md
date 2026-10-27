# P4 deltas work plan (orchestrator copy, 2026-10-05)

Source: an Opus planning agent. Read-only research against v2 at 183da42 plus
the uncommitted main tree. Orchestrator decisions are at the bottom.

## Already done in the main tree (it comes with the step-1 commit)

- **Δ6(i) is done.** `prompts.py:95-101` and `tools.py:525-529` carry the
  non-exhaustive wording. Still needed: a regression test that "globally
  optimal on the grid" appears only on the exhaustive branch.
- **The analysis-time half of Δ8 is done.**
  - `campaign/voids.py`: `classify` and `session_void` are pure, and denied
    unknown names count as `unknown_tool_attempts`.
  - `driver._queue_score` skips voids.
  - `analysis.void_counts` exists.
  - Tests are in `tests/campaign/test_campaign_voids.py`.
- **Δ8 still to do at harness level.**
  - `harness/live.py:181-184` sets void = `bool(foreign_tool_uses)`.
  - The `investigation/runner.py:369` void reasons.
  - The `campaign/llm.py:219` stored void flag.
  - `unknown_tool_attempts` is not recorded live.
- **Merge hazard.** `campaign/rescore.py:58,74` matches pilot sessions by
  `session_config_digest`, which includes `HARNESS_VERSION`. WP8 must keep
  `legacy_session_text_v1` so that rescore still matches `harness/1` cells.
  Do not merge the worktree into v2 before the P3.1 rescore finishes.

## Packages (instrument packages: tests first, watch them fail, then implement)

### WP1: Δ2 core, truncation at the event cap (opus, M)

**Files:**
- `glm/simulate.py`: `_Thinning` gets `on_cap: "raise"|"truncate"`, and
  `PlannedRun.truncated_at`.
- `glm/interventions.py`: a new `run_experiment_truncating(...) ->
  (Dataset, truncated_at)`. The `run_experiment` signature is unchanged.
- `investigation/world.py`: `_simulate` truncates, and
  `World.truncated_at(data_id)` exposes τ.
- New tests in `tests/glm/test_simulate_truncation.py` and
  `test_likelihood_truncation.py`.

**Semantics:**
- τ is the time of the N-th event (N = MAX_EVENTS), and that event is kept.
- The Dataset keeps the planned horizon H, so `Trend` uses t/H; see
  `features.py:893` and `simulate.py:1048`.
- The tail [nextafter(τ), H) is added to `excluded`, unioned with the
  existing windows.
- The likelihood is therefore exactly Σ_{i≤N} log λ(t_i) − ∫_0^τ λ, and the
  fit-key schema is unchanged.
- The candidate-cap and runaway `ExplosionError` still stop the run.

**Tests first:**
1. A truncated run's events, marks and flags are byte-identical to the first
   N events of the same seeded run with a larger cap.
2. Log L equals the stopping-time formula computed independently through
   `simulate.intensity` plus quadrature, including a Trend truth so that H
   (not τ) is pinned.
3. The fitter recovers parameters on truncated logs (slow, many seeds).
4. The mean score at the truth is about 0 over replicates (slow).
5. Runs below the cap are byte-identical to today, with a pinned digest.
6. A double run gives identical output.

### WP2: Δ3 core, design generators (sonnet, S)

**Files:** new `glm/designs.py` and `tests/glm/test_designs.py`.

**What it adds:**
- `expand(obj)`, a pure JSON-to-JSON transform:
  - `times: {start, step, n}` → `[start + k*step]`, computed per k and never
    as a running sum;
  - mark patterns `{"constant": v}`, `{"alternate": [a, b]}` and
    `{"repeat": [...]}`, whose expanded length must equal n.
- `DESIGN_SCHEMA`.

**Tests first:**
- Expansion is exact.
- n ≤ MAX_FORCED_EVENTS.
- A length mismatch is refused, with the path in the message.
- The round trip through `from_json` is canonical, so the recorded design is
  always the arrays.
- Rescaling by a power-of-two c commutes with expansion (property test).

### WP3: Δ4 core, the timeline → §4.4 measures (opus, M)

**Files:**
- new `investigation/timeline.py`, which builds `(PredictionRecord[],
  ExperimentRun[], Belief[])` from a `SessionRecord`'s `tool_call` entries;
- `scoring/behaviour.py`: `Belief` gets `rivals: tuple[str, ...]`, and
  `runner_up` = `rivals[0]`;
- tests in `tests/investigation/test_timeline.py` and
  `tests/scoring/test_scoring_behaviour.py`.

**Record contract:**
- A belief record is `{"belief": i, "favoured": <canonical native DSL>,
  "rivals": [...], "confidence": c, "experiments_run": k}`.
- Predict and submit records carry canonical native hypothesis text.
- **Clock:** a belief's turn is the number of experiments run at the time of
  the call, with k = 1. A belief recorded after experiment j has turn j+1.

**Tests first:**
- 10b offline: hand-built records give known calibration, falsification,
  revision and zombie values.
- Turn numbers come from record order and never from agent arguments.
- Non-canonical spellings of the same structure map to the same label.

### WP4: Δ8 at harness level (opus, S-M)

**Files:**
- new `harness/voids.py`: the pure `classify` core, because the harness must
  not import from campaign;
- `campaign/voids.py`: re-imports it;
- `harness/live.py`: `InvestigationResult` gains `unknown_tool_attempts` and
  `void_reasons`, computed from the `foreign_tool_result` entries. A foreign
  `tool_use` with no recorded denial stays void;
- `investigation/runner.py` (`run`) and `InvestigationOutcome.unknown_tool_attempts`;
- `campaign/llm.py`: a new reading field, and void follows the policy.

**Tests first:**
- A ScriptedDriver bare `python` session gives `void=0` and
  `unknown_tool_attempts=1` in the stored reading.
- An executed foreign tool, a `server:` use and an out-of-bounds hit are all
  void.
- The harness and analysis classifications agree.

**Do NOT bump `HARNESS_VERSION`** (WP8 does it).

### WP5: Δ11 core, evidence policies with no tool wiring (opus, L)

**Files:**
- new `systems/v2/policies.py`:
  - design menu, diagnostic menu, and the entertained-set fitter with BIC
    weights;
  - `eig`, `fsd`, the random policy, `choose`;
  - `fsd_null`;
- new `systems/v2/policy_controls.py`: the truth-aware FSD for control 5. It
  gets the truth through its constructor, like ORACLE, and is never imported
  by the tool layer;
- new `scoring/telling.py`;
- `scoring/seeds.py`: new tags `POLICY = 4` and `TELLING = 5`.

**Tests first:**
- EIG matches the closed form on a two-Gaussian toy.
- EIG = 0 for identical predictives.
- EIG ≤ ε·H(w).
- An AST test that `policies.py` never names the truth.
- FSD equals `battery.hellinger2` on the replicate moments.
- The FSD null sits at about 1/(4R).
- The random policy is uniform and seeded.
- Menus have a deterministic order, and ties go to menu order.
- Same seed gives byte-identical choices.
- On the planted Hawkes-vs-S11 case, the telling diagnostic is cross-mark.
- **Every fold uses `sciagent.core.reductions`.**
- Measure the compute on dev-008 and dev-013 before fixing R, M and K.

### WP6: Δ2 + Δ3 (+ Δ12) in the tool layer (opus, M). Depends on WP1, WP2.

**`tools.py`:**
- A truncated experiment is charged, and its record carries `truncated_at`.
- The text gives τ in agent units.
- AG-o `{"from": "/work/x.json"}` is allowed only under `sandbox.work_dir`:
  a regular non-symlink file of at most 1 MB, with its sha256 recorded.
  AG-c gets an error.

**`view.py`:**
- `experiment_native` expands the design first.
- Agent analysis is clipped to [0, τ], while fits use the native dataset.

**`prompts.py`:** text on generators and truncation.

**Δ12 (added by the orchestrator):** every tool result shows the remaining E
and F.

**Tests:**
- Update `test_investigation_event_cap.py`: an EXPLOSIVE design now yields N
  events, is charged, replays, and has identical text in named and anon apart
  from units.
- New `test_investigation_designs.py`: `from` refuses a path outside /work, a
  symlink and an oversized file; the generator and explicit-array designs
  give byte-identical records.
- Δ12 text tests.

### WP7: Δ4 tools + Δ6 variant mechanism and text (opus, L). Depends on WP3, WP6.

**`tools.py`:**
- A new `belief(favoured, rivals[1..3], confidence)` tool.
- `run_experiment` is refused, uncharged, without a belief recorded since the
  last experiment.
- `submit` requires the same plus a `stop_reason` from the enum {confident,
  budget, evidence_unlikely_to_change, other} and a note.
- `predict.hypothesis` is structure-only and canonicalised; a free `note`
  goes into the record.

**`prompts.py`:**
- The belief instructions and "commit one prediction under a rival".
- `prompt_variant` is "v1" (current) or "link-neutral". The link-neutral
  variant rotates the examples across identity, exp and softplus, has one
  depth-3 example, and restates the scoring sentence at :199-203 as "scored
  on held-out prediction and structural distance".

**`runner.py`:** `InvestigationSpec.prompt_variant`, default "v1", recorded and
read back in `from_json`.

**Tests:**
- Enforcement, with refusals uncharged.
- Belief labels canonicalise the same way predict's do.
- The depth-3 example parses.
- The anon render of the link-neutral variant passes `assert_no_leaks`.
- 10b end to end through ScriptedDriver → the live loop → `timeline` →
  `behaviour` gives known values. This is also control 6.

### WP8: Δ7 session key + `HARNESS_VERSION` bump (opus, M). Depends on WP4, WP7.

**Files:**
- `tools.py`: a pure `tool_specs(view, arm, experiments, fits, *, policy,
  handlers)`;
- `runner.py`: `rendered_config(...)` returns the system prompt and the
  schemas, excluding the opening prompt;
- `live.py`: `HARNESS_VERSION = "harness/2"`;
- `plan.py`:
  - `LLMArm` gains `prompt_variant` and `policy`;
  - a non-default variant or policy gets a system-name suffix;
  - `session_text` adds sha256 of `canonical_json(rendered)`;
  - `legacy_session_text_v1` is kept;
- `rescore.py` matches `harness/1` by the legacy digest;
- `llm.py` passes the variant and policy through;
- `driver.py` only as far as its signature changes.

**Tests:**
- One changed character of the prompt or of a schema changes the key.
- The named and anon keys differ.
- A pilot-format `harness/1` cell is still found by rescore.

### WP9: B-sym policy arms + the telling unit (opus, L). Depends on WP5, WP8, WP1.

**Files:**
- new `systems/v2/policy_search.py`: B-sym in two epochs with an injected
  `ExperimentRunner`;
- `plan.py`: the baselines `B-sym+random`, `B-sym+EIG` and `B-sym+FSD`, the
  control `B-sym+truthFSD`, the telling unit, and the policy config in
  `system_config_text`;
- `execute.py`: a World-backed runner and the telling job;
- `analysis.py`.

### WP10: AG-c-S policy arms + AG-S-boed (opus, L). Depends on WP5, WP7, WP8.

**Files:** `tools.py`, `prompts.py`, `runner.py` (`InvestigationSpec.policy`).

**Policy mode:**
- `run_experiment` takes no design.
- Fits of the latest belief's structures are framework-internal, uncharged
  and not shown.
- The output shows the design and the chosen diagnostic's value. **No
  EIG or FSD value ever reaches the agent.**

**Tests:** no policy score appears in any output; charges are identical; a
policy session replays byte for byte; the truth-isolation static test still
passes.

### WP11: Controls 4-6 (opus, M). Depends on WP7, WP9.

**File:** `scoring/controls_p4.py`.
- **C4:** scripted planted-hint, named against anon; the canonical submission
  and the fit log Ls must be exactly equal. Rescaled waits for Δ9.
- **C5:** truthFSD against random on the telling rate; one-sided exact
  McNemar, p < 0.05.
- **C6:** wraps 10b.

### WP12: LOG entries + gates (sonnet, S). Only WP12 edits LOG.md.

## Shared-file rules

| file | packages | rule |
|---|---|---|
| `tools.py` | WP6 → WP7 → WP8 → WP10 | sequential |
| `prompts.py` | WP6 → WP7 → WP10 | sequential |
| `runner.py` | WP4 → WP7 → WP8 → WP10 | sequential |
| `plan.py` | WP8 → WP9 | sequential |
| `llm.py`, `live.py` | WP4 → WP8 | sequential |

**Waves:**
1. {WP1, WP2, WP3, WP4, WP5}
2. WP6 → WP7 → WP8
3. {WP9, WP10}
4. WP11
5. WP12

## Δ11 SPEC-silent choices (log for the user's review; no SPEC edits)

1. **Design menu:** the `standard_battery(channels)` shapes rescaled to
   horizon 2000; 8 designs for pointproc.
2. **Diagnostic menu:** the whole catalogue; channel arguments are resolved
   once on the observational log and then frozen.
3. **Entertained set:**
   - B-sym: the top K = 4 by BIC.
   - AG-c-S: the latest belief's favoured structure plus its rivals (K ≤ 4).
   - Each is refitted on all the data at every decision. These fits are
     framework-internal and uncharged.
4. **Weights:** w ∝ exp(−BIC/2).
5. **EIG:**
   - The predictive is an undefined-atom plus Gaussian moments from R = 16
     replicates.
   - EIG = Σ w_k KL(p_k‖p_w), by Gauss–Hermite quadrature.
   - One simulation set per (e, k) serves every diagnostic.
   - A capped simulation that stops counts as undefined.
6. **FSD:**
   - He²(q_par, q_np), where q_par is the top-weight fit.
   - q_np is `WienerHopfModel` estimated on the observational log, with iid
     empirical marks.
   - The raw value is used for selection; `fsd_null` is a separate negative
     control.
7. **Random:** uniform over (e, d), drawn with replacement and seeded.
8. **Repeats and ties:** repeats are allowed, and ties go to menu order.
9. **B-sym schedule:**
   - Epoch 1: F/2 fits on the observational log, identical across policies.
   - Then E experiments, chosen by the policy.
   - Epoch 2: F/2 fits on all the data, seeded with epoch 1's top population.
   - The arm submits the best by BIC.
10. **AG-c-S policy arms:**
    - `run_experiment` takes no arguments, and the agent decides when to call
      it.
    - The output shows the design and the chosen diagnostic's value.
    - Predictions made in a policy arm are excluded from C6.
11. **AG-S-boed** = AG-c-S + the EIG policy, named, on Sonnet.
12. **Telling diagnostic:**
    - The best library member is B-lib's pick on the observational log.
    - d* = argmax over d of max over e of He²(truth, best library member),
      with R = 32 on the TELLING stream.
    - "Observed" means d* was computed on some experiment dataset.
13. **Scoring** refits the submission on the observational log only.
14. **Stopped experiments** count as used and give no data.
15. **Seeds:** `SeedSequence(entropy=seed, spawn_key=(POLICY, decision, k, r))`.
16. **Names:** `B-sym+{random,EIG,FSD}`, `AG-c-S+{random,FSD}/named` and
    `AG-S-boed/named`. The policy config enters the key.
17. **Control 5:** one-sided exact McNemar test, p < 0.05.
18. **Controls 4-6** are preregistered in LOG, not in SPEC.

**Other deltas:**
- **Δ2:** τ is the N-th event, which is kept; H is unchanged and the tail is
  excluded; agent analysis is clipped to [0, τ].
- **Δ3:** generators are expanded before validation, and the arrays are
  recorded. `from` holds an intervention object under /work, with its digest
  recorded.
- **Δ4:**
  - fresh = a belief recorded after the latest experiment;
  - 1 to 3 rivals, distinct from the favoured structure;
  - confidence in (0, 1);
  - the `stop_reason` enum;
  - the stopping gap refits the favoured structure on the observational log.
- **Δ6:** a non-default variant gets a system-name suffix.

## Invariants

- **`sciagent/` never imports `environments/`.** Channels are arguments, and
  the runner is injected.
- **Agents never set numbers.** Confidence and intervals are statements;
  policy fits are unshown and uncharged; no EIG or FSD value reaches the
  agent. The truth is visible only through World, ORACLE, PlantedHint and the
  truth-aware control's constructor.
- **Determinism.** Everything goes through `SeedSequence`, folds go through
  `core.reductions`, order is fixed, and there is no set iteration.
- **The store is append-only.**
- **Replay.** Every new tool path is in the record.

## Orchestrator decisions (2026-10-05)

- **Δ12 is folded into WP6.** Decision 17 approved it, and doing it now
  avoids a second key change. Logged for review.
- **The worktree branches from the step-1 commit.**
- **No merge into v2 before the P3.1 rescore completes.**
- **The Δ6 Sonnet check runs once WP7 and WP8 have passed their gates.**

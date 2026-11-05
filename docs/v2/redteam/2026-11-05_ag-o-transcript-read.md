# Red-team review: AG-o transcript records and delegated reads (session 33)

Saved by the lead from the reviewer's reply; the reviewer has no write access. The notes it reviews are `docs/v2/P3-TRANSCRIPT-READ-A.md` and `-B.md`, corrected since (findings 1, 3 and 7). Findings 1, 2a and 2b were rerun by the lead.

**Verdict: ready with fixes.** No leak of truth, held-out data or scores, and no agent-set score, in the five records. The go/no-go record needs three fixes first: findings 1, 2 and 3.

Reviewed at HEAD 167271d. `R` = `.cache/pilot-merged/llm`. `[n]` = entry index in the record.

## Blocking findings

### 1. Notes B, C1 has the wrong sign, and its "looks false" must go

B says the periodic part of dev-003 fit 28 has minimum +5.6e-6, so the `simulable: NO` text "looks false". The true minimum is negative, so that sentence is literally true for this fit.

- **Pointer:** `R/50037735b0dbf90f/investigation-8732cce92573d5c7.json` [149], call 55.
- **Cause:** B's grid (`linspace(0,2000,100001)[:-1]+0.01`) steps over the trough by 0.0088. On that grid the reviewer reproduces its +5.643e-06.
- **Exact value:** rebuilding `obs` and `e0` and calling `fit(structure, [obs, e0], channels, config=FIT_CONFIG, psi=<fit 28 psi>)` gave LL -2955.689956 (the record's -2955.69), `certified` True, `simulable` False, θ₀ minus the amplitude of gate 2 = **-6.33e-6**, negative for t in (6.2723, 6.2854) of every 50-unit cycle.
- **When the rate goes negative:** when the `Excite` term is below 6.3e-6, which means about 24 time units since the last qualifying event, or none yet.

```
$ python3 -c "import math;print(0.74938-math.hypot(0.5394,0.520218))"
-5.83e-06   (from printed θ; rounding bound ±1.2e-6)
```

**Fix:** delete "here it looks false" and "non-negative under every history". B's general point stands: the bound's floor here is about -0.745 against a true minimum near -6e-6. This fit is not evidence for changing the tool string.

### 2. The anon record cannot speak for the anon condition

dev-002 s2 is clean, but it is the only anon record of 120 in this sample and it cannot show either item below. Neither notes file mentions them. Neither is a truth leak, and the GO reads non-LLM systems only, so no verdict moves. P4's C2 (named against anon) does depend on them.

**a. An anon-only sentence reached half the anon sessions.**

- "note: softplus-link coefficients are reported as fitted, not converted to your time units" appears 379 times in 60 of 120 anon sessions (31 AG-c, 29 AG-o) and 0 times in named sessions.
- Source: `src/sciagent/investigation/tools.py:606-610`, `view.py:254`.
- dev-002's 17 fits are all identity link, so it never triggers.
- `docs/v2/LOG.md` logs unconverted θ as a known limit, then says at line 356 that the conditions differ "only in names and time units". Nothing counts how many agents saw the sentence.

```
$ grep -rl 'not converted to your time units' .cache/pilot-merged/llm --include='investigation-*.json' | wc -l
60
```

**b. One forbidden term reached an agent through an error message.**

- `R/c78ac1d7040ae7eb/investigation-2ec421d986a1c9fc.json` [154], AG-c-S/anon dev-015 s1: `fit: LastMarkAbove on a sign channel: LastMarkAbove(channel='m2')`.
- `sign` is forbidden at `scripts/pilot.py:71`.
- It was 1 hit in 4,223 runtime texts, from `scan_for_leaks` over all 120 anon records.
- Source: `src/sciagent/glm/grammar.py:401`. The same wording sits at `grammar.py:371` and `:379`, and `syntax.py:140` and `:337`.
- `test_anonymised_run_outputs_do_not_leak` asserts that no call errored (`tests/investigation/test_investigation_truth_isolation.py:225-227`), so error text is never scanned.

**Fix:** state both in the record, and do not imply the anon condition is clean beyond dev-002 s2. The LOG line and the test gap need fixing before P4 freezes prompts.

### 3. Notes A's headline outruns its own limits

A's summary says "No truth or held-out data reaches an agent". Its own section says a transcript cannot prove that. **Fix:** use B's form, "no sign of it in these records", and name the evidence: code reading, sandbox tests, replay.

## Non-blocking findings

### 4. The record does not hold everything the agent saw

- Turn 1 shows `cache_creation_input_tokens` 453 and `input_tokens` 4 in all five records, against a 176-character opening prompt.
- The reviewer's inference is about 400 tokens of context not in the record. It rests on where the cache boundary falls, which it could not confirm.
- Entry 1 lists 16 skills, 5 agents and 48 slash commands, while the config has `skills: []`. The harness checks only `tools` (`src/sciagent/harness/live.py:398`).
- The turn-1 total is 7,453 in all four named records and 7,089 in anon, which argues against truth-specific content but does not prove it.
- Neither notes file mentions this. The record should not repeat the docstring's "sees only" the prompts and schemas (`src/sciagent/investigation/runner.py:15-16`) as established fact.
- **Check:** print `entries[k].body['usage']` for the first assistant entry of each record.

### 5. Parser refusals read as "no prediction"

- Across the store, 19 `predict` calls ran: 9 accepted, 10 refused by the parser.
- Of 16 sessions that tried, 8 ended with none accepted, including dev-012 s1 [58].
- PILOT's "any prediction" column (10%, 0%, 2%, 2%) counts accepted only. Attempts were 15%, 2%, 7%, 3%.
- **Fix:** add one line to the record. No score reads this.
- **Check:** count `tool_call` entries named `predict` by `is_error` over the 240 records.

### 6. Replay timings tighten B's D1 and D3

| dev-012 work | in session | in replay (load 4 to 9) |
|---|---|---|
| fit 28 (call 45) | 1,086.5 s | 106 s |
| call 22 | 595.8 s | 299 s |
| all tool work | about 3,283 s of fit time alone | 966 s total |

This supports B's contention reading and resolves its "cannot explain". It does not show the session would have submitted.

### 7. Small corrections

- **B, C2:** the "76 nats" is the no-constant variant. The same scan's `anch 12.5` row, at [169], is -2820.4, so 36 nats. The conclusion stands.
- **A, dev-018:** "close to the truth's" fits -0.0776 against -0.0717, but not -0.0239 against -0.0026, which is 9 times off ([23]).
- **B's header** names 4443928, but HEAD was 167271d by 11:58. The difference is five `docs/v2` files.

## Part 1 answers

| Question | Answer |
|---|---|
| Truth seen by an agent? | No sign. Own-truth and all-20-truth literal scans of θ, calibration and identifiability values: 0 real hits. Every flag was coincidence (dev-012 call 28 is a forced-time grid) or the agent's own text echoed back. Every truth feature first appears in the agent's own arguments. |
| Held-out data or a score seen? | No sign. Tool text holds no score. The words truth, oracle and held do not occur after entry 0 in dev-003 or dev-012, and a scan of the other three found none in tool text. |
| Native name in the anon session? | None: 0 hits in 34 visible texts. "Hawkes" in the report [78] is the agent's own word. See finding 2 for the wider condition. |
| Anything set that scoring reads? | Only structure text. The framework refits on `obs` (`src/sciagent/campaign/execute.py`). `report` is stored and no scorer reads it. |
| Sandbox | 41 calls in three records; dev-018 and dev-019 made none. Only `/data` and `/work` paths, no `os`, network or environment reads. `/dev/stdin` at call 33 is dead code behind `if False`. Image holds no `sciagent` or truth files, and its ID matches. |
| Budgets | Reconcile at every call in all five, with all digests recomputed. One store difference, already known: dev-012 `fits_used` 35 against 34 charged. |

**How held-out separation is enforced:** `World` holds no held-out data; `held_out_dataset` runs only in the score job, on a `spawn_key` stream disjoint from the world's; `Lab` does not import scoring. All five worlds rebuilt: `obs` counts match the opening prompts, and held-out logs share 0 event times with `obs`.

**What a record can and cannot show:** it can show the absence of such content in the text an agent received, because `shown_matches` is true on every call. It cannot show what was in `/data`, the live mounts, or the full model context. It holds no event arrays and the run directory is deleted, so separation rests on code, tests and replay.

**Controls and integrity:** the scanners fired on planted leaks. 17 selected boundary and truth-isolation tests passed, as did the 50 fast investigation tests. Hash chains verify, and cell heads and truth digests match. The `truths.json` sha256 matches its manifest. All 12 stored voids are one denied bare `python`, and 0 are void by policy; dev-003 s1 is stored `void=1`, but policy excuses it and its score is used. Rules predate the run: pick rule 8916206 and a9f426d, void policy a9f426d, run 900e66a on Oct 29. `pilot_report.py` regenerated with 0 of 271 lines different outside the notes. Neither note compares systems (SPEC §6.4).

**Replay at HEAD:**

| Record | Result |
|---|---|
| dev-018, dev-019, dev-002 | PASS |
| dev-012 | 54 of 54 identical |
| dev-003 | Calls 0-60: 58 identical, 3 differ (51, 54, 58). Calls 61-64 identical in a python-only rerun. |

The three dev-003 differences are exactly one token each, the timing number. That corroborates PILOT's statement for 62 of its 65 calls. Fits 65 and 66 and the submit were not finished at the reviewer's cut-off.

## Spot checks of the notes

**Notes A:** all held except the one wording item in finding 7: 27 thinking blocks, 0 with content; all four arms submitted the same structure on dev-018 s1, `per_event` -0.98986947 each; regeneration 0 lines differ; `m2` count of 291 of 300; LL error 3.78e-6; false next-best claim in the final message confirmed; Haiku 561/19; message ids 7, 10, 20. Absent, confirmed: no sign term on `e0` in dev-019; "Nothing beats" not in dev-019. A's "not individually traced" row is now traced: 3 wall-time sessions plus dev-005 s3 at -inf.

**Notes B:** C1 failed (finding 1), and "76 nats" is a variant choice. These held: "Best anchored period was 10" (-2785.6); reissued code dropped the `/work/f.json` write; 2,999.8 s of answered fits, 45.4 s longest call; `cpu_s` covers all threads, so the 16% basis is sound; `store_check` output reproduced; ψ 0.2 and 1.5 on joint fits, and on `e2` alone `power_p` is 2. Absent, confirmed: wall limit not in the prompt; truth, oracle and held after entry 0; no product with a gate.

**Reading claims:** A's renderer keeps full tool text, so "nothing dropped" holds; it prints unknown keys rather than asserting on them, which is minor. B's array abbreviation is disclosed. Their scripts differ, which is consistent with independence, but both missed findings 2 and 4.

## What could not be checked

Agent reasoning (all thinking blocks are empty, so every "why" is inference); the model's actual context, including the 453-token gap; live `/data` contents and the real Docker command, beyond the canonical argv in the cell; whether anyone read line by line, and B's claims about order and not reading A; which in-flight dev-012 fit landed; replay at 900e66a itself (this one ran at HEAD); literal leaks in the other 235 records, except the anon scan. ψ values cannot be tested at all, because every grid value is in the prompt.

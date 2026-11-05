# Transcript read B: AG-o-S/named dev-003 s1 and dev-012 s1

Written 2026-11-05 at v2 4443928, on dm-devbox, for the reading the owner delegated (INBOX 2026-11-05 10:10 UTC).
This is a record of what two agents did. It is not a verdict and not a comparison of systems (SPEC §6.4).
Thinking blocks are empty (43 and 27 blocks, signature only), so every statement about why is an inference from actions and the two text blocks.
dev-003's truth structure was already in PILOT.md when I started; dev-012's truth and both truths' ψ were opened only after both transcripts had been read.

Sources, marked on each number:

- **[record n]**: entry index n in the record. Records were read only; the hash chain verified on load for both (`SessionRecord.load`).
- **[cell]**: `.cache/pilot-merged/ledger.sqlite` or `details/`.
- **[committed]**: a committed script run this session. `uv run python scripts/store_check.py --store .cache/pilot-merged`; `uv run python scripts/p3_report/wall_time_audit.py --root .cache/pilot-merged`; `uv run python scripts/p3_report/sandbox_nondeterminism.py --root .cache/pilot-merged`.
- **[scratch]**: an uncommitted one-off script, kept in `.cache/drafts/s33/transcripts-b-scripts/`. These numbers are not from a committed script. If any goes into PILOT.md, its script should be committed first.

## Summary

- **Nothing here bears on the GO.** Neither record shows leaked truth or held-out data, an agent reaching a score or a fitted parameter of its submission, sandbox code leaving `/data`, `/work` and `/tmp`, or budget accounting that does not match, apart from the one known 35 against 34.
- **Every PILOT.md claim I could check against these two records holds.** Two I could only partly check: the replay rerun's "timing number alone" and the claim of which pending fit landed.
- **Items for the lane, none of which moves a number:** the 3,600 s wall limit is not in the agent's prompt, and dev-012's own CPU figure points at contention (D1); the `simulable: NO` text is stronger than its rule (C1); dev-003 has a few report sentences that go beyond its actions (C2).

---

## 3. dev-003 s1, AG-o-S/named (`llm/50037735b0dbf90f/investigation-8732cce92573d5c7.json`)

### What was read and how

- **Line by line:** the system prompt [0] and all 187 entries' assistant text, every tool_use, all 29 sandbox code blocks, every fit result, all diagnostics, the nonparametric table, stdout and stderr of every python call, the submission, the post-submit message [184], the result [185] and the outcome [186].
- **Displayed but not read digit by digit:** the two 248-long design arrays at [28] and [30]. By script: the printed design equals the submitted one (times, sign, size) [scratch `last.py`].
- **By script only:** `args` against the tool_use input (identical for all 68 calls; only one copy read); `record.design` (a third copy of the arrays); `key`, `structure_hash`, `digest`, `sandbox_digest` and per-message `usage` fields (not read; chain verified).
- **Partly:** the tool JSON schemas in [0] (read the first part and every tool description in full; the rest of the schema compared by script: the two records' configs differ only in `truth_id` and the opening prompt's event count [scratch `last.py`]).

### What the agent did

Five free looks at `obs` (Fano 2.44, dispersion 2.01, Wiener-Hopf table, spectral prominence 304, peak frequency 0.08), interleaved with five identity-link fits, the best at BIC 3438.75 [22].
It generated a forced-event design: 248 events at U(6,10) gaps, four conditions (sign ±1 by size 0.3 or 3), in a block-randomised order. The first attempt used the bare `python` name and was denied [25, 26]; it reissued the same code through `mcp__lab__python` without the `/work/f.json` write [27].
This became its one experiment, `e0`, re-typed with no length error [30]. It never ran another.

Reading e0's response table [34], it saw sign=-1 with size 3 responding at about 1.5 against about 1 elsewhere. That moved it to `Excite(ExpK, Above(size), sign=-)` over six fits, BIC 6926.61 at [48].

The large step came next. Adding `Periodic` helped a little, but the fit picked period 5 [60], and the agent turned to the sandbox: a periodogram on endogenous events found peaks at 8.33, 12.5 and 25 [65], and a phase fold at 25 was clearly not sinusoidal [68].
It then wrote its own binned likelihood [80 to 90], persisted as `/work/lib*.py`. That showed period exactly 25 (−2841 against about −3580 at neighbours [90]), with a break at phase 12.5 [104].
Finding that one Gate could not express this ([109], [112], [128]), it searched pairs of grid-expressible gated periodics with its own likelihood, 4,950 pairs in 45 s [136, 137]. Top pair: `Gate(Periodic 10, PhaseWindow 50)` at phases π and 3π/2.

The framework confirmed the shape [149, BIC 6028.29]. Trying the links gave softplus −2784.35 and exp −2820.91 [153, 154]. It then rebuilt a softplus surrogate that matched the framework (−2784.42 [160]) and used it to test alternatives: products of periodics, other gates, off-grid periods, mark and kernel variants [163 to 175]. Two last framework fits rejected PowerK and GammaK [179, 180], and it submitted at 1,887 s [182], with 32 of 40 fits and 1 of 8 experiments used.

Against the truth, opened afterwards, it recovered link, excitation source, `Above`, exp_rate 0.25 and above_z 0.5. It replaced `Product(Periodic 50, Gate(Periodic 10, PhaseWindow 25))` with an equivalent-looking two-gate form on the grid. It never fitted any `Product` in the framework. In the sandbox it tried products of two plain periodics [162] but not a product with a gate.

Three of the 29 python calls did no work: [86] and [139] are no-ops, and [107] is a NameError. [114] defines a stub function and only reruns one fit. "Most sandbox use" still holds.

### PILOT.md claims, checked

| claim | result | source |
|---|---|---|
| 29 `python` calls, next is 16 (anon dev-017 s1) | holds. 29 answered, plus 1 denied bare call | [committed `sandbox_nondeterminism.py`], [scratch `audit.py`] |
| gap closed 0.92 | holds: 0.9233 from per_event −0.64695, B-lib −0.82696, ORACLE −0.63201 by PILOT's formula | [cell] |
| not exact; submitted and truth strings | hold | [record 182], `truths.json`, [cell] |
| own code prints wall time; calls 51, 54, 58 numbered from 0 | holds: `time.time()-t0` printed at [136], [145], [156]; outputs at [137], [146], [157] | [record], [committed] |
| replay FAIL at step 51, field `digest` | holds. `.cache/replay-900e66a/AG-o.log` line 146 | log |
| rerun diverged at 51, 54, 58 only, each in its timing number alone; other 65 byte-identical | **only partly checkable.** 68 calls less 3 is 65. Reading the code finds no other source of variation. The log truncates the output, the review script was not kept, and I did not rerun the replay | [record], log |
| no unseeded generator | holds: `default_rng(1)` at [25, 27], `default_rng(s)` for a loop index at [159 to 174]. None uses `SANDBOX_SEED`, as the prompt asked, but all are seeded | [record], [committed] |
| not void, reclassified, 1 denied bare `python`, stored reason `foreign tool: python` | holds. Nothing ran: no tool_call for that id, the call counter goes 9 to 10, and `/work/f.json` is never referenced again. Stored `void` is 1 and a score cell exists | [record 25, 26, 186], [cell] |

One wording point: PILOT's "12 unanswered `python` tool_uses" means unanswered by a layer call. In this record the use does have a harness answer, a `foreign_tool_result` at [26].

### Did the session behave as the harness says

Yes, on everything these two records exercise.

- **Budgets:** `budgets_after` equals the running count of `charged` at every call; 32 fits, 1 experiment, 1 submit; 32 distinct fit keys [scratch `audit.py`]. The cell agrees: fits 32, experiments 1, tool_calls 68, turns 70, out tokens 49,537 [cell]. `shown_matches` true on all 68.
- **Forced events are out of the likelihood:** 4,232 counted = 2,046 + (2,434 − 248) [record 31, 39].
- **Sandbox:** one image, 28 exits 0, 1 exit 1, no time-out, longest call 45.4 s of 60 s. The NameError at [107] confirms a fresh process per call.
- **Local search wording** fits the 512-point rule: structures over 512 combinations say "local search" with the points visited.
- **Limits:** 70 turns of 100; 1,893 s of 3,600 s. `apiKeySource` is `none` and `require_subscription` true; `total_cost_usd` 1.79 is therefore notional. `model_usage` shows a small Haiku call (561 in, 19 out), which PILOT already describes as the CLI's auxiliary call; its content is not in the record.

### Concerns

**C1. `simulable: NO` tells the agent something stronger than the rule. Bears on tool text, not on any score.**
Fit 28 [149] says "simulable: NO (the fitted rate can go negative; it is not a valid process)".
`fit_bounds.py` says the bound is "sufficient, not necessary (columns are treated as independent)". Two gates that are bounded separately trip it even when they cancel.
Corrected after the red team (finding 1): this note first said the periodic part stays positive, from a grid that steps over the trough. The exact minimum is negative, about -6e-6 (a refit at fit 28's ψ gives -6.33e-6, for t in (6.2723, 6.2854) of each 50-unit cycle), so the tool's sentence is literally true for this fit and this fit is no evidence for changing it. The general point stands: the bound is sufficient, not necessary, and its floor here is about -0.745 against a true minimum near -6e-6.
The agent's next move was to other links, including the truth's [151, 152]. Whether this text was the reason cannot be seen. `simulable` is not read by scoring; only the sampler and this tool text use it (grep of `src/sciagent`). The fix would be wording ("could not be shown non-negative"). Changing a tool string changes what agents see, so that is the lane's call.

**C2. Report sentences that go beyond the actions.** None changes a score; the report is not scored.

- [182] item 4 lists "ungated … periodic terms" as rejected. As replacements, yes [166]. But its own fit adding plain period 25 to the two gates gave −2768.0 against −2784.4, a gain of 16.4 nats [166], never run in the framework with 8 fits left and not mentioned.
- [182] "a generic all-event kernel add[s] nothing" and "that was an artefact of the periodic bursts" rest only on its own surrogate [175], at one fixed kernel rate. The sentence does not say so, unlike its Product line. It is correct against the truth.
- [182] "a pulse-like burst at phase 10-12.5" under-describes its own profile, which has a second hump at phases 0 to 3 [84, 104].
- [184], after submit and not scored: "a period-12.5 sinusoid in a period-25 window would fit about as well". Its own scan says −2860.2 against −2784.4, 76 nats worse [169]. Only the two-sinusoid form [25, 12.5] is level (−2780.4). The same scan's best anchored period was 10, which is the truth's. [182]'s caveat that the generator "may use a period of 12.5 or similar" is hedged but runs against that scan. (Red team, finding 7: the -2860.2 is the no-constant variant; the same scan's anchored row at 12.5 is -2820.4, so 36 nats. The conclusion stands.)

All other figures in [182] and [184] match the record: −2784, −2956, −2821, −2819, −2822, p = 0.93, −2875, "about 700" (731 at [90]), and the +A, 0, −A, 0 account of the two gates, which I worked through and which is right.

**C3. Closed, no defect: the agent's likelihood was 10 nats above certified fit 28.**
Its identity two-gate fit gave −2945.88 [137, 143]; the framework gave −2955.69, certified [149], at the same ψ. The same surrogate matches the framework to about 0.1 nats or better elsewhere (fit 23: −3375.770 against −3375.769; fit 29: −2784.418 against −2784.351).
I rebuilt `obs` and `e0` from the seeded world (identical e0 summary line and first rows) and ran the agent's `lib3` code verbatim [scratch `rebuild.py`, `fit28.py`]. At fit 28's θ the agent's likelihood also gives −2955.69, so the two likelihoods agree.
The agent's θ is positive at every event but puts the periodic part below zero on 2.5% of the grid (minimum −0.06). The framework enforces λ ≥ 0 at quadrature nodes as well (`fit_solve.py`), and its constraint is binding. So the 10 nats come from a negative rate between events, and the framework's number is the right one. The agent reported −2956.

---

## 5. dev-012 s1, AG-o-S/named (`llm/bd3dc4c432ee7e78/investigation-39d7634dde194fba.json`)

### What was read and how

- **Line by line:** all 140 entries' tool_uses, the 5 sandbox code blocks, every fit result, all diagnostics, the Wiener-Hopf table, python stdout and stderr, and the outcome [139]. There are no text blocks at all, so there is no first-person statement to read.
- **Abbreviated by script:** the arrays in the four compose calls [76, 79, 82, 87] (lengths and ends shown; lengths checked, and [87] equals the design printed at [86] [scratch `last.py`]). The long arrays at [71] and [74] were displayed but not read digit by digit.
- **By script only:** `args` against input (identical, all 54 calls); hashes and `usage` fields (not read).
- **Not reread:** the system prompt and tool list, compared with dev-003's by script (identical apart from truth id and event count).

### What the agent did

Free looks at `obs` (Fano 3.56, spectral peak at frequency 0.04, that is period 25), then a greedy fit sequence: `Excite(PowerK, Mark(size), all)` [16], split by sign, finding sign=+ carries it [21, 25], with `Periodic` at period 25 from [20] on.
It tried mark functions and took `Pow` [40]. Moving to softplus gave a large gain, BIC 2657.30 [50]. Exp was worse [54]. A sign=- term added nothing [55], and that one fit took 596 s.

Then it turned to contrasts. A `predict` was refused because the hypothesis field held prose [58], not charged. e0 injected sign=-1 on every generated event, which leaves `Periodic` plus something the Excite-on-sign=+ term cannot explain (KS p = 0.0003 [64]).
e1 injected sign=+1 and blew up: stopped at the 15,000-event limit after 119 s and charged [68]. Three attempts at a compose design were rejected for re-typed array lengths (315, 334 and 355 values for 330 times) [77, 80, 83]; none was charged. It then generated a shorter design as one JSON string [85, 86] and ran it as e2 [87, 88]: all generated events sign=-1, plus 100 forced sign=+ events at five sizes. That is a clean probe of the sign=+ kernel; the early response broadly rises with size, with 1.5 out of line [91]. A follow-up failed on a NameError, assuming variables survive between calls [94].

Fits on e2 alone and jointly settled on `Mark(size)` over `Pow`, near `power_c` 0.2, `power_p` 1.5 [98 to 106]. ExpK at rate 8 edged PowerK by BIC on e2 alone [111], but it kept PowerK [113].
From [118] it went back to the unexplained part of e0. Link and Trend variants did not help. The turn came at [134]: `Excite(ExpK, One, all)` on e0 with θ = −0.28, exp_rate 2, KS p = 0.77, against 0.0003 without it. Its last message tried that term jointly over `obs`, `e0` and `e2`, with sources all and sign=- [137, 138].
The clock then cut the session at 3,606 s with neither answered [139].

Against the truth, opened afterwards (`link=softplus; Excite(PowerK, Mark(size), sign=+) + Gate(Excite(ExpK, One, all), PhaseWindow)`): it held the link, the first feature and that feature's ψ, and had just found the second feature's inhibitory `Excite(ExpK, One, all)` at exp_rate 2. What remained was the gate. It never tried `Gate` on an Excite with `PhaseWindow`, and its `Periodic` was standing in for it. This is what the record shows at the cut, not a claim about where the session would have ended.

### PILOT.md claims, checked

| claim | result | source |
|---|---|---|
| `store_check`: `fits_used 35.0 != 34 charged`; STORE INCONSISTENT; 910 cells; 240 sessions; 1 budget issue | holds, reproduced this session | [committed `store_check.py`] |
| record wall 3,606; driver wall 3,874; past the record 268 | holds: 3,606.29, 3,873.86, 267.6 | [committed `wall_time_audit.py`], run log line 131 |
| tool calls 54, charged fits 34, reading 35, experiments 3, no submission | holds. e1 is the stopped one and is charged. Reading fits 35, experiments 3, tool_calls 54; record `budgets_after` 34, 3, 0 | [record 135, 139], [cell] |
| submission null, score cell in no-submission form, no `per_event` | holds | [cell] |
| a `fit` is the last unanswered tool_use; two unanswered | holds: exactly two, [137] and [138]; all other 54 uses answered | [scratch `audit.py`] |
| both fits share one message id; neither repeats an earlier fit on the same data | holds | [record 137, 138], [scratch `last.py`] |
| no join warning in the run log for this session | holds. The warnings are at lines 61 and 99 only | `.cache/pilot-llm-run.log` |
| `fitted` read from the record (34); the curve uses charged fits | holds: `fitted` has 34 entries and the curve 34 points | [cell] |
| "at most 550 s" for the in-flight fit | holds: 3,873.9 − 3,323.4 | [record], [committed] |
| only one of the two reached lab state; which is unknown | **consistent, not provable.** See D3 | |
| not void | holds: `foreign_tool_uses` empty | [record 139] |

### Did the session behave as the harness says

Yes. `budgets_after` equals the running charged count at every call; 34 distinct fit keys; `shown_matches` true on all 54.
Rejected calls [58, 77, 80, 83] were not charged; the stopped experiment was charged and said so [68]. 4,230 counted events = 1,936 + 1,105 + (1,289 − 100) [record 98]. Sandbox: 5 calls, one exit 1, no time-out; paths `/work/f.json`, `/data/obs.npz`, `/data/e2.csv` only. Fits 13 and 24 are arithmetically the same model as fits 3 and 23 (`Pow` at exponent 1 against `Mark`), charged as different structures, which is the stated rule.

### Concerns

**D1. The session lost its submission to a limit the agent cannot see, and its own numbers point at contention. Bears on P4, not on the GO.**

- `wall_time_s` 3600 is in the record's config, not in the system prompt, which I read in full; `prompts.py` mentions only the per-call sandbox limit. Nothing in the session tells the agent a clock exists.
- About 91% of the hour was framework fits: 2,999.8 s answered plus 282.9 s in flight at the cut, of 3,606.3 s [scratch `audit.py`]. Two fits alone took 1,682 s [55, 116]. The agent's own time is about 180 s.
- The worker's CPU for the whole session was 515.5 s (`timing.jsonl`), so the fits ran at no more than about 16% of a core. PILOT says contention, seed and truth "are not separated for the cuts". That holds across the three, but for this session the ratio is hard to explain by truth or seed alone. It ran 19:29 to 20:33Z, inside the overlap.
- The consequence is already in PILOT as a caveat: all three cuts are in the overlap. What it does not state is that the agent is not told. The verdict reads non-LLM systems only, so nothing moves.

**D2. TRANSCRIPTS.md predates this run.** It is dated 2026-10-03 and says "the wall time and turn limits never bind" and "9 voids". On this run the wall limit bound 3 times and 12 voids are stored. Its Method section on redacted thinking holds in both records. Whoever cites it should not read those counts as this pilot's.

**D3. Small open point on which fit landed.** Across both records all 32 multi-call messages returned results in tool_use order, and the fits in them ran one after another (for example [98] then [99]). That supports PILOT's inference that one was in flight and the other never started, and suggests [137] was the one, but it is not proof.
One thing I cannot explain: the landed fit took at most 550 s, while fit 28 on the same three datasets and the same 360-point grid took 1,086 s [116]. Load changes could account for it. It moves nothing, and I did not time the fit offline.

---

## Across both records

- **No leak in the tool surface.** The prompt and tool descriptions are identical across the two sessions apart from `truth_id`, which is in the record's config and not in the prompt, and the event count. The words truth, oracle and held do not occur after entry 0 [scratch `audit.py`]. CSV columns are `time`, `endogenous`, `sign`, `size`, as documented [34, 91]; `_publish` writes only those. No fit, diagnostic or python output carries a score.
- **Held-out data.** Held-out logs come from a separately tagged stream (`scoring/seeds.py`), not the world's `SeedSequence([s, stream, k])`; `Lab` holds no held-out data. This is from reading the code. The records alone cannot prove it, and neither session ran a plain experiment.
- **Sandbox boundary.** All quoted paths are under `/data` and `/work`. The one exception is `/dev/stdin` at [86] in dev-003, inside `if False`, so never opened. No `os`, `subprocess`, `socket` or similar in any call. `run_argv` has `--network none`, `--read-only`, `--cap-drop ALL`, `no-new-privileges`, user 65534, and two mounts [cell]. Neither agent listed `/data`, so this rests on the code they ran; I saw nothing from inside the container.
- **Invariant 2.** Sandbox numbers shaped which structures the agent chose, which is allowed. The submission is the structure only, and the score's `per_event` equals the curve's last point for dev-003 [cell].

## Not done

- The other three AG-o picks (not assigned here).
- Replay not rerun; the claim about "timing number alone" rests on the review's report and on reading the code.
- No statement about why an agent did anything beyond what its actions show.
- `.cache/drafts/s33/transcripts-a.md` was not read, so these notes are independent of it.

## Follow-up, lane session 34 (2026-11-05): red team findings 4 and 5

`uv run python scripts/p3_report/first_turn_and_predict.py --root .cache/pilot-merged`, over all 240 records.

**Finding 4.** The first assistant entry's total input is one value per arm in all 240 records: AG-c/anon 6,430, AG-c/named 6,794, AG-o/anon 7,089, AG-o/named 7,453. The cache-creation figure is 453 in 217 records in every arm, 0 in 6 and the whole total in 17, so 453 is where a cache boundary falls, and it is the same tail across arms, truths and seeds. So the first turn's context does not change in length with the truth, on any record. That is stronger than the five records the review had, and it is still not proof: equal length is not equal content, and what the roughly 400 tokens beyond the 176-character prompt hold stays unknown, since the record does not carry them. The docstring's "sees only" stays unestablished.

**Finding 5.** 19 `predict` calls: 9 accepted, 10 refused. 16 sessions tried, 8 of them with one accepted. Per arm, tried then accepted, of 60 sessions: AG-c/named 9, 6; AG-c/anon 4, 1; AG-o/named 1, 0; AG-o/anon 2, 1. This agrees with the review and with PILOT.md's line 52. No score reads it.

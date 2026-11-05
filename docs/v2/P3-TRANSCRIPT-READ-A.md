# AG-o transcript read, part A (session 33)

Picks 1, 2 and 4 of PILOT.md "AG-o transcripts for the owner's read", read under the owner's delegation (INBOX 2026-11-05 10:10 UTC). This is a record of what the agents did. It is not a verdict, and it compares no systems (SPEC §6.4). Thinking blocks are redacted in all three records (27 blocks, 0 with content), so any statement about why an agent did something is an inference from its actions and its few text blocks, and is marked as such.

`[n]` is `entries[n].index` in the record. All records are under `.cache/pilot-merged/llm/`.

## Summary of findings

- **Nothing in these three records changes what PILOT.md's numbers mean.** There is no sign in these records of truth or held-out data reaching an agent (a transcript cannot prove it; the evidence is the code, the isolation tests and replay), no agent sets a score or a fitted parameter of the submission (they see only their own fits' output), the sandbox is used only on `/data` and `/work`, budgets reconcile at every call, and the anon session shows no native name. This covers three records chosen by the report's rule, not the store.
- **All PILOT.md claims on these picks hold** (table in each section).
- **TRANSCRIPTS.md is not about this store.** It is the 2026-10-03 report on the earlier run, and its sentence about dev-019 named s1 is false of the record scored here (concern C1).
- **Each `report` contains a claim its actions do not support** (C2 to C4). `report` is stored but no scorer reads it, so none of this reaches a number.

## How this was read

| | T1 dev-018 s1 | T2 dev-019 s1 | T4 dev-002 s2 |
|---|---|---|---|
| record | `76088f5b15af15b9/investigation-c4b1a7f2e1f2a288.json` | `3838f39a9162781c/investigation-295dcd772da90c60.json` | `73d599f9d94305fd/investigation-e9bc508d193885d7.json` |
| entries | 41 | 58 | 83 |
| entry 0 (config, system prompt, tool schemas) | read line by line | **by script**: diffed against T1. Differs only in `truth_id` and the event count in `prompt` | **by script**: diffed against T1. Every difference read (list under T4) |
| entry 1 (session init) | read line by line | read line by line | **by script**: equal to T1's |
| entries 2 onward | read line by line, nothing dropped | read line by line in a compact render | read line by line in a compact render |

The compact render (`/tmp/s33tr/compact.py`) drops only `hash`/`prev`, thinking `signature`s and per-message `usage`, and asserts that no unknown key or block type is dropped. It cuts the tool_call `args` echo to 200 characters, but the full input is shown from the matching tool_use, and a script confirmed the two are equal for every call.

Not truly read element by element, in spite of the above:

- **Long arrays (T4).** `run_experiment` inputs [31], [48], [53], the `design` string in [32], and the printed JSON in [46], [52]. I looked at their pattern and counted their lengths by script. I did not check each element.
- **Kernel tables.** The `nonparam_kernels` tables ([8] in T1 and T2, [7] in T4) were read through, not recomputed.
- **Thinking signatures** are opaque. They were seen in T1 and dropped from T2 and T4.

Not checked at all:

- No fit, score or replay was re-run. The replay result comes from `.cache/replay-900e66a/AG-o.log`.
- The record never lists `/data`, so the contents of `/data` come from reading the code (`Lab._publish`, `sandbox/runner.py`), not from observation.
- That the held-out log is unavailable to the agent is also from code plus the absence of any such content in tool results. A transcript cannot prove it.

## Numbers and how they were produced

```
uv run python scripts/pilot_report.py --root .cache/pilot-merged --truths 20 --seeds 1,2,3 \
    --b10f-truths 10 --sessions stored --resamples 10000 --out /tmp/s33tr/PILOT.regen.md
wrote /tmp/s33tr/PILOT.regen.md; verdict go
```

Outside the notes block, the output differs from `docs/v2/PILOT.md` in 0 lines. That is the source of the pick lines.

- Per-row `per_event`, gaps, denominators and the pick-rule check come from `uv run python /tmp/s33tr/rows.py`. It is a scratch script that calls the committed `analysis.load_rows` with the report's setup. It is not itself committed, and neither is `/tmp/s33tr/audit.py`.
- `audit.py` gives the structural checks: `SessionRecord.load` (hash chain), budget replay, tool_use and tool_call pairing, the leak scan and the sandbox path scan.
- Fit values are quoted from the record at the index given. Truth values are from `data/v2/truths/dev/truths.json`.

### Structural checks, all three records

| check | T1 | T2 | T4 |
|---|---|---|---|
| hash chain (`SessionRecord.load`) | OK | OK | OK |
| tool_use = tool_call, all paired, args equal | 16 | 23 | 31 |
| `shown_matches` true on every call | yes | yes | yes |
| budget replay equals `budgets_after` at every call | yes | yes | yes |
| final fits / experiments / submit | 9 / 0 / 1 | 16 / 1 / 1 | 17 / 2 / 1 |
| `fit N of 40` text equals the running count | yes | yes | yes |
| repeated fits | none | none | none |
| rejected calls, charged | 0 | 0 | 2, neither charged |
| `num_turns` = tool_uses + 1 | 17 | 24 | 32 |
| distinct message ids | 7 | 10 | 20 |
| non-lab tools, `foreign_tool_uses` | none | none | none |
| outcome | submitted | submitted | submitted |
| `python` calls | **0** | **0** | 7 |
| `predict` / `notebook` calls | 0 / 0 | 0 / 0 | 0 / 0 |
| text blocks before submit | 0 | 0 | 0 |
| truth structure ever fitted | no | no | no |
| links tried | identity only | identity only | identity only |
| wall s | 79.8 | 307.7 | 185.8 |
| replay at 900e66a | PASS | PASS | PASS |

Common to all three: `apiKeySource` is `none`, `require_subscription` is true, `tools` is empty apart from the lab server, and `claude-haiku-4-5` shows the same 561 in / 19 out, the CLI's fixed auxiliary call as far as can be told (inference). `total_cost_usd` is a notional figure under Max.

The grid of each submission has at most 512 combinations (12, 30 and 72 points as seen in [25], [21], [75]), so none of the three scored structures depends on local search. That is an inference from the prompt's rule and the grid sizes.

**Scoring basis.** `campaign/execute.py` refits the submitted structure on `obs` only, the same data as every baseline. So T4's pooled data can change which structure was submitted, but cannot feed its score directly. The parameters quoted in T4's report (joint fit) are not those of the scored fit: that structure was never fitted on `obs` alone in the session.

**Row mixing ruled out.** The anon arm's extremes equal the named arm's to six places (1.049 on dev-018 s1, -0.215 on dev-019 s1). Each pair submitted the identical structure, and refitting one structure on one `obs` gives one number. Cells on the same (truth, seed) with different structures differ. This is a check on the rows, not a comparison.

---

## T1: dev-018 s1, AG-o-S/named (best gap closed, named)

Truth: `link=identity; Excite(GammaK, Mark(size), all) + Periodic`, gamma shape 2, mean 0.5, theta 0.312, period 5, Periodic theta (-0.0717, -0.0026), intercept 0.673.

### What the agent did

It took 5 diagnostics and the kernel estimate first ([3] to [14]). The kernel estimate gave a `size` integral of 0.33 and an `events` integral of 0.28 [8]. Its first fits compared `One` with `Mark(size)` (BIC 3730.62 and 3626.19, [17], [18]), then added `Periodic` to the ExpK model and swapped the kernel in the same batch ([23] to [25]). GammaK won clearly: BIC 3594.17, KS p = 0.77, against KS p of 0.019 and 0.017 for the ExpK and PowerK versions. Four variants on the GammaK model followed, none better on BIC ([30] to [35]), and it submitted at 9 fits [36]. No `python`, no experiments, no predictions.

The path needs no outside knowledge: each step follows the previous output. On its one structure it got the excitation term right to the parameter: gamma 2 / 0.5, theta 0.301, intercept 0.679 [25].

The session never fitted `Periodic` on the GammaK model, so it never fitted the truth. Its only `Periodic` fit [23] was on the ExpK model. That fit did choose the true period, 5, with coefficients (-0.0776, -0.0239): the first near the truth's -0.0717, the second not (-0.0026; red team, finding 7), and BIC rejected it by 15.02 for 3 extra parameters. I infer it dropped `Periodic` on that result and did not return to it after finding GammaK.

### PILOT.md claims

| claim | check | result |
|---|---|---|
| best gap closed, named, 1.05 | 1.0486, highest of 56 finite unflagged rows; next 1.025 | holds |
| not exact, omits `Periodic` | [37] against the truth; `exact` False, distance 0.4 | holds |
| above ORACLE on one held-out log, gap +0.00167 on 0.0343 | gap +0.001667, denominator 0.034332 | holds |
| "not a win" | gap on the other two seeds -0.00291, -0.00071 | consistent |
| generated line: submitted structure | [37] | holds |

### Concerns

- **C2, report overclaims on `Periodic`** ([36], and [38] after submit). "Adding sign, an unmarked excitation term, Periodic or Trend gave no real gain" treats `Periodic` as tested on the best model. It was tested only on the ExpK model [23], which also fails KS, and `Periodic` is the one term the truth has that the submission lacks. Whether BIC would reject it on the GammaK model cannot be said from this record.
- **Final message misstates a ranking** [38]. "The next best was the unmarked-excitation variant at 3615.65" is false. The next best were +Trend 3601.60 [35], `Pow(size)` 3601.75 [31] and +`Mark(sign)` 3612.25 [30]. This text is after submit and is not in `report`.
- **Final message names a false lead** [38]. It flags a possible cycle near period 19 from the spectral peak 0.053 [12], and calls the period-5 fit "tiny coefficients". The truth's period is 5, and no cycle near 19 exists in it.
- **Nothing here is specific to AG-o.** There is no sandbox use. All four arms submitted this same structure on dev-018 s1, so the 1.05 is that structure's value and says nothing about AG-o's tools.

---

## T2: dev-019 s1, AG-o-S/named (worst gap closed, named)

Truth: `link=softplus; Product(Periodic, Gate(Excite(ExpK, One, all), PhaseWindow))`, Periodic period 100, gate period 5, phase pi, exp_rate 1.

### What the agent did

Spectral prominence was 124.9 and peak frequency 0.0100, a period of about 100 ([9], [12]). The first two fits had no periodic term and failed or nearly failed KS ([15], [16]). `Periodic` took the log-likelihood from -1867.84 to -1838.24, BIC 3721.94, period 100, KS p = 0.14 [21]. `Trend` and a size term were tried beside it without `Periodic` ([22], [23]), then kernels, sign and size terms and one `LastMarkAbove` gate on the `Periodic` base ([28] to [35]). None beat its BIC.

It ran one experiment, a plain run with no intervention at horizon 8000 ([37], [38]), and refitted the leading candidates on `e0` alone ([42] to [51]). Every fit on `e0` failed KS badly: the submitted structure at p = 0.00013 [42], the rest from 0.00064 down to 0.000017. It submitted at 16 fits with 7 experiments unused [53], and said in the report that the misfit remained.

The agent found the period and that events excite each other (its exp_rate is 2, the truth's 1), and it reported the misfit honestly, but it never changed the family: 16 of 16 fits are identity link, with no `Product` and no `PhaseWindow`. A different link or a modulated excitation went untried after [42]. Its offered explanation was that the true period may be off the grid. The evidence it held pointed the other way (peak 0.0100 at [12]), and the truth's period is 100. The session did not test the explanation.

The experiment added data and contrasted nothing.

### PILOT.md claims

| claim | check | result |
|---|---|---|
| worst gap closed, named, -0.22 | -0.2150, lowest of 56; next 0.000 (dev-014 s3) | holds |
| identity link for a softplus truth | [53]; all fits identity | holds; incomplete, as `Product` and `PhaseWindow` are missing too (the generated line shows it) |
| "worst" excludes the 3 no-submission and the -inf session | 56 = 60 - 4 finite rows; count only, the four not individually traced | consistent |
| generated line: submitted structure | [54] | holds |

Score rows: `per_event` -0.936301 against ORACLE -0.859715, gap -0.076586, denominator 0.063033, distance 0.8. Other seeds in this cell: 0.182, 0.074.

### Concerns

- **C1, TRANSCRIPTS.md does not describe this record.** TRANSCRIPTS.md §2 says "dev-019 named s1 is the clearest case: 7 fits, then 'Nothing beats the simple model,' then submit", in a passage about sessions with no experiments. This record has 16 fits, 1 experiment, and no such text (string search: absent; the only text block is [55], after submit). TRANSCRIPTS.md's last commit is 183da42, 2026-10-03, while these records date from 2026-10-29 and 30, and it counts 286 `python` calls where PILOT.md's scan of this store counts 314. It describes the earlier run. Its list of records "read in full" is therefore not a read of this store, and the behavioural counts P4-PROPOSAL cites from it are from that run. Its header gives the date but does not say it predates the store PILOT.md reads.
- **C3, report claim without an action** [53]. "sign and size excitation, and a size-gated excitation, each improved the fit by under 2 log-likelihood units on `obs` and under 3 on `e0`". On `obs` all three hold (+0.31, +0.33, +1.43). On `e0`, size (+1.68) and the gate (+2.58) hold, but no sign term was ever fitted on `e0`. Repeated in [55].
- **Weak base for `Trend`** [22]. "Trend is not supported" rests on one fit on `obs`, without `Periodic`.

---

## T4: dev-002 s2, AG-o-S/anon (best gap closed, anonymised)

Truth: `link=identity; Excite(ExpK, One, sign=+) + Excite(ExpK, One, all)`, native exp_rate 1 and 4, theta 0.845 and 0.196, intercept 0.362. In the session's units (time x 8): rates 0.125 and 0.5, intercept 0.0453.

### What the agent did

This is the only one of the three that used the sandbox and designed interventions. After a look at gaps and marks [6], the kernel estimate showed a slow decay and a large `m2` integral (0.53) [7]. It started directly on a power-law kernel [12], and split by `m2` at once, which moved BIC from 11253.9 to 11149.2 [14]. More variants on `obs` followed. The best was BIC 11144 [21], with KS p = 0.028.

Then experiments:

- **e0** [31]: 100 forced events, `m2=+`, `m1=1`, spaced 40 apart, horizon 4000. The agent measured the response by `python` [35], and an `e0` fit reproduced the `obs` split [43].
- **Abandoned design** [45] to [54]: a three-class forced design written in the sandbox, then re-typed by hand into the tool call. It failed twice on counts, `m1` 246 of 300 (`m2` 291 of 300, not reported by the error) and then `m2` 144 of 150. Neither was charged. It dropped the design after that.
- **e1** [56]: a `compose` of `inject_marks`: `m2=-1` on [0, 4000), `m1=4` on [4000, 8000), `m1=0.05` on [8000, 12000). This is the real contrast: rate about 0.055 under `m2=-1` against 0.0964 overall and 0.132 on `obs` [59].

Pooling `obs`, `e0` and `e1`, adding a second `m2=+` term with ExpK moved BIC from 24435.3 to 24420.6 and KS p from 0.03 to 0.71 ([64], [70]). Merging the two power-law terms into `all` gave BIC 24396.8 and KS p = 0.86 [75]. Keeping the power-law term on `m2=-` only was worse (24442.8) [76]. It submitted at 17 fits with 6 experiments unused [78].

The `m2=+` term is right to the parameter (rate 0.125, theta 0.89 against 0.845), found through an intervention. The `all` term is `PowerK` c=1.6, p=2, not ExpK at 0.5. The session never fitted a plain ExpK `all` term on any data, so it stopped one substitution from the truth. Each move follows from visible output, with no unexplained jump.

### Sandbox

7 calls ([6], [35], [38], [40], [46], [52], [59]). Paths used: `/data/obs.npz`, `/data/e0.npz`, `/data/e1.npz`, `/work/a.json`. No `/proc`, subprocess, socket, `os`, `sys`, clock or random use. One exit 1 [38], a `NameError` because it assumed variables persisted; the sandbox behaved as documented (fresh process). Its `/work/a.json` was written and never used, since `run_experiment` takes no file. This fits SPEC's out-of-bounds scan: no hit.

### Anon condition

- **Config diff against T1:** `m1` and `m2` replace `size` and `sign`, channel order changes, diagnostics are `d01` to `d28`, and time-valued items scale by 8 (grids, default horizon 16000, maximum 64000, clamp 6.25). The diagnostics' descriptions stay in full, and the structure language stays the same.
- **Native names:** a whole-word search for sign/size and a search for the 28 native diagnostic names over every shown string (system prompt, prompt, tool schemas, tool text, assistant text, tool inputs): 0 hits. `npz` keys are `times`, `mark_endogenous`, `mark_m1`, `mark_m2` [6].
- **Native names exist in `record` fields** (`fano_factor` [8]; `sign`, `size` in `design` [32], [57]; the structure in [79]). These are not shown: the server returns `output.text` only (`harness/live.py:230`), and `shown_matches` is true on all 31 calls.
- **Units:** shown log-likelihood equals native minus n ln 8 on all 17 fits (largest relative error 3.8e-6, display rounding). Times map correctly (e0 horizon 4000 to 500, forced 40 to 5).
- "Hawkes process" in the report [78] does not appear in the prompt. It is a generic term, and I read it as prior knowledge of the model class, not a leak (inference).

### PILOT.md claims

| claim | check | result |
|---|---|---|
| best gap closed, anonymised, 1.14 | 1.1385, highest of 60; next 1.049 | holds |
| not exact, `PowerK` where truth has `ExpK` | [79], one kernel on the `all` term; distance 0.0889 | holds |
| above ORACLE on one held-out log, gap +0.00235 on 0.0170 | gap +0.002348, denominator 0.016958 | holds |
| "not a win" | gap on the other two seeds -0.00599, -0.00506 | consistent |
| generated line: submitted structure | same features; hash at [79] equals fit 16's at [75] | holds |

Two points on that 1.14, neither contradicting PILOT.md:

- This seed's denominator is about half the other two (0.0170 against 0.0302 and 0.0327), so the ratio is inflated: the same raw gap on 0.0302 would read 1.08 (my arithmetic).
- On the same (truth, seed), a session that submitted the truth structure has `per_event` equal to ORACLE's and gap closed 1.0, as it should. So the excess is the `PowerK` model on this one log, not a scoring artefact.

### Concerns

- **C4, smaller report points** [78]. "No evidence of periodicity or trend" was tested on the earlier two-power-law base ([69], [71]), not on the submitted one. "A Mark(m1) kernel ... gave essentially nothing" passes over [28], where a `Mark(m1)` term gained 5.6 log-likelihood units and moved KS p from 0.028 to 0.123 while BIC worsened by 4.1. The conclusion matches the truth, but the wording is stronger than the record.
- The agent's two rejected designs are the failure TRANSCRIPTS.md described in the earlier run, and P4's Δ3 addresses it. They cost this session its planned design, not budget.

---

## What the owner or control room may want to act on

Nothing here asks for the GO to be revisited. Three items for the lane:

1. TRANSCRIPTS.md does not state that it describes the earlier run, and its dev-019 s1 sentence does not match this store (C1). Anyone reading it next to PILOT.md's picks will take it for this store.
2. `report` overclaims in all three sessions (C2, C3, C4) follow one pattern: a term is declared unsupported after being tested on an earlier model, or on one dataset only. They matter if reports are ever scored or quoted, for example under §4.4 or in the paper.
3. Two of these three picks use no sandbox, so this part of the read says almost nothing about AG-o's open tools. Pick 3 (dev-003 s1) carries that weight.

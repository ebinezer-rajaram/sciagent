Reviewed: `docs/v2/prereg/2026-11-06_bsym-tuning.md`, second text, sha256 `e5d47486…`. Saved verbatim from the red-team agent, lane session 37, 2026-11-07.

# Red team round 2: B-sym tuning pre-registration, second text

**Verdict: NOT READY.** There are 7 blocking and 6 non-blocking findings. Nothing has run, so every fix is free of bias, and every fix is text-level. Finding 3 needs a ceiling decision from the control room. Most of the faults are in the new stage 2, so after pasting, re-read stage 2, sections 5 and 6 and the script together; a full third round isn't needed.

**Reviewed:** `docs/v2/prereg/2026-11-06_bsym-tuning.md`, sha256 `e5d47486…`, the same at 4c5a21c and at the final head 8e21bb9. Split `truths.json` is `604acec5…`, matching PILOT.md.

## Blocking

### 1. Stage 2 cannot start or resume under the latch, and an unfinished stage 2 has no rule

- Section 5 writes `selection.json` after stage 1. Section 6 "refuses to start at all once `selection.json` exists". So stage 2 can't start as a new run, and a crash in stage 2 can't resume.
- "A stop leaves section 5 to apply" gives nothing here, because section 5 has no stage 2 label.
- Stage 1's numbers are readable once `selection.json` exists (section 9). A person can then write `STOP` or decline to resume stage 2, with the statistics in hand and no rule. This is round 1's finding 5 again.

**Fix:** "Stage 1 writes `selection.json`. Stage 2 writes `final.json`. Both paths are constants with no option. Stage 1 refuses to start once `selection.json` exists. Stage 2 starts only from a `selection.json` naming a winner other than `c0`, on its own store `.cache/drafts/bsym-tune/stage2/` and its own CPU count. It resumes after a crash, reboot or out-of-memory kill, and refuses to start once `final.json` exists. A pass not finished at the CPU stop, at a `STOP` file, or at the §6.1 commit has failed. `final.json` names the setting that stands. If the label is not `SELECTED <name>`, or is `SELECTED c0`, `selection.json` is final."

### 2. Stage 2's read leaves four things to decide

```
gonogo.bsym_approaches_oracle(rows, kept)  -> ScoringError no B-sym@10F rows for truth 'dev-010'
gonogo.oracle_beats_lib on stored rows     -> kept 20 dropped []
control 2, c0, P3: 18-0-2, p 4.77e-06, median CI [0.0148, 0.0306], passed
control 3, c0, P3: median 0.0, passed; worst of 10 -0.00856
strata: dev-000..009 far 5, near 5; dev-010..019 mid 4, far 4, near 2
```

- **Truths.** Control 3 needs the 10 and control 2 needs 20. Finding "kept" needs B-lib, which the export omits. `go_no_go` refuses a 10-truth control (`_check_complete`).
- **Generator.** `planted_beats_rand` needs an `rng`, and no stream is named.
- **Killed or failed units.** `analysis.score_rows` drops a row with no gap, so a killed 10×F unit vanishes and the median is taken over survivors. Section 6's floor can't apply, because controls read unfloored and stage 2 has no 14 settings.
- **Mid truths.** Round 1's fix said "plus mid truths". The text dropped this without saying so, so stage 2 still reads no mid truth. The cost is why simply adding them doesn't work (finding 3), but control 3's rule is absolute, so no P3 10×F row is needed for these truths.

**Fix:** "Rows are `ScoreRow`s with gap = `per_event` minus the stored ORACLE's, unfloored. Control 2 is `planted_beats_rand` on all 20 dev truths, since P3's control 1 kept all 20, with `default_rng(SeedSequence([20261113, p]))`, 10,000 resamples, p = 1 for the first pass and 2 for the second. Control 3 is `bsym_approaches_oracle` on the 10 truths of the units. Pass is both `.passed`. A unit killed, failed or without a reading has gap `-inf`. The 10 truths, by sorted id: the first three near, the first three far, all four mid (dev-001, 002, 004; dev-000, 003, 005; dev-010, 015, 016, 019)."

If you keep dev-000 to 009 instead, say in sections 2 and 10 that no mid truth is read.

### 3. Stage 2's budget does not fit the candidates added for finding 1

Run with `/tmp/rt-bsym2/cost.py`: `BSym.run` only, seeds 1 to 3, no held-out value read.

```
c0   dev-005 s2 166.7 s (stored 155)      c10 dev-005 s2 415.9 s   c9 dev-005 s2 346.7 s
c0   dev-019 s2 not rerun (stored 179)    c10 dev-019 s2 354.4 s
```

So `c9` and `c10` run at about 2.0 to 2.5 times `c0`, on 3 units. At 2.2 times:

- One pass is 43.7 × 2.2 = 96 CPU hours, past the 84 stop.
- dev-008's 5.96 h becomes 13.1 h, past L = 8. dev-000 and dev-002 also pass 8 h.
- This assumes the 40-fit ratio holds at 400 fits, which I did not measure.

Two passes at P3's costs (2 workers, kernel count):

```
cost x1.0   listed order 0 undispatched   10F first 36    without B-sym units 0
cost x1.05  listed order 1                10F first 68    without B-sym units 0
cost x1.25  listed order 6                10F first 119   without B-sym units 2
```

- The 87.4 fits only in the listed order, at exactly P3's costs, and no order is fixed for stage 2.
- The 60 B-sym units feed no control. They only give the margin, which also needs `c0`'s stored B-sym rows, and the export omits those.
- As written, a win by the settings aimed at the diagnosed failures dies on cost. That leans towards the default, against section 1.

**Fix, for the control room:** "B-sym at seeds 1 to 3 runs after the passes, for the setting that stands, and no rule reads it. A pass is 10×F units first, then planted-hint, by sorted truth and seed. L = 14 hours, dispatch stops at 120, 2 in flight give 148." The total becomes 248 CPU hours, still $0. Alternatively keep the ceiling, accept that finding 1's unfinished-pass rule sends those winners to `c0`, and say so in section 2.

### 4. `c0b` has no definition, and on the normal path it is `c0`

```
address comparison over the 14 settings: distinct addresses 13 of 14; collisions [['c0', 'c0b']]
```

- `system_config_text` holds budget, `gp` and seeds, and no search seed.
- `_run_system` always uses `search_seed(truth.id, job.seed)`, and nothing configures it.
- Through the normal path, `c0b` either isn't run or reads `c0`'s cell. Its noise floor then comes out as 0, and the null passes without having measured anything.

**Fix:** "`c0b` is `c0` with search seed `(search_seed(truth, seed) + 1) % 2**63`, round 1's definition. Its cells go in their own store `.cache/drafts/bsym-tune/c0b/`, at `c0`'s addresses. The script's job calls `BSym` with that seed, then `score_submission` and the curve, as `run_baseline_job` does."

### 5. H1 fails on `-inf`, and the cited means rest on a rule the text lacks

```
c0 curve on P3: -inf at 30 on dev-005 s3, dev-019 s3; -inf at 40 on dev-011 s1, dev-005 s3
paired_bootstrap_ci on 3 seeds -> ScoringError: paired samples must be finite
literal arithmetic: nan on every seed set but (1,2), which gives a -inf mean
```

- That is 3 of 60 P3 worlds. Section 3's floor covers "each setting", not fit 30.
- So a literal script raises after every statistic exists, with no label.
- Round 1's `p3c.py` floored both ends. My recomputation that way gives 0.00963, 0.01066 and 0.00765, which is the text's "0.0077 to 0.0107". On 3 seeds it gives 0.0093 [0.0047, 0.0141].
- H1 passes on P3 under every treatment I tried. The fault is the missing rule, not the result.

**Fix:** "`c0`'s reading at 30 fits is the last curve point with `fits_used` ≤ 30, read with `float()`. A `-inf` at 30 or at 40 takes that world's floor, the same number at both ends. A world where `c0` has no curve leaves H1 and is counted. H1 reads complete worlds through `paired_bootstrap_ci`, truths sorted."

### 6. `c11` as written is inert, and it cannot land in `p4_plan`

```
BIC on real fits (seeds 1-3, training data only):
dev-000 s1  identity null 4007.448346   softplus null 4007.448346
dev-002 s2  identity null 4001.712407   softplus null 4001.712407
softplus fits of 40 on dev-005 s2:  c0 0;  c11 1 (the seed itself);  softplus Hawkes seed 24
softplus Hawkes BIC against identity Hawkes: lower on 3 of 4 worlds, all certified
```

- A null has one rate, so the softplus null ties the identity null and ranks last of the four seeds. `tournament()` then picks it about (1/4)³ = 1.6% of the time.
- So finding 1 was moved, not answered. `c11` is close to `c0` with one wasted fit and a changed random path, yet a chance win would move `sym_seeds` everywhere.
- `sym_seeds` is a field of `CampaignEnvironment`, set in `scripts/pilot.py` (line 291 at the final head), and `p4_plan` returns configs. So "goes into `p4_plan`" cannot be done, and "no pilot address moves" holds for `gp` only.

**Fix:** "`c11`: the library's seeds, then `link=softplus; Excite(ExpK, One, all)`, last." In "Where it lands": "`p4_plan` holds `gp`. If `c11` stands, `pointproc_environment` appends that structure to `sym_seeds`, never to `B_LIB_LIBRARY`, in the §6.1 commit, and a test ties it to `final.json`."

### 7. Section 6 contradicts section 5, and the bug route has no path

- "Nobody decides anything after the statistics exist" sits beside "replaces it only by the owner's logged decision", a free choice between two known results.
- `BUG_STOP` points to that rule, but the latch forbids any rerun, and no seeds or store are named.

**Fix:** "A bug found after any outcome is read stops every further step, and `c0` stands, unless an amendment frozen before the §6.1 commit names the fault, shows it with a failing test, and names a new output directory on the same seeds. The rerun's label then applies by section 5, whatever it says, and the first result stays beside it." If the owner wants to keep the choice, delete "Nobody decides…" and say so.

## Non-blocking

**8. Stage 1's margin is thin, and it is one-shot.** `INCOMPLETE` fires when the mean unit cost passes 428.6 s, which is 1.32 times P3's 325.2.

```
c9 2.1x, c10 2.2x, rest 1x         83.8 h for 60 worlds, 64 by the 90 h stop
and c1 like c9                     89.8 h, 60
and c7, c8 at 1.5x (my assumption) 95.2 h, 56
```

A fresh-process `c0` ran 1.08 to 1.17 times its stored CPU on one world. Consider a dispatch stop of 110, ceiling 114.

**9. "Normal path" and `RLIMIT_CPU` conflict.** `Campaign`'s pool reuses workers, and `max_tasks_per_child=1` hangs, so the limit would accumulate across units.
- Say: "one process per unit, as the miss check's `job_main`, calling `run_baseline_job`, cells written in `_record`'s shape, and a `cpu_limit` end journalled so a resume does not rerun it."
- "Per unit" in section 8 must read "per systemd unit (cgroup `cpu.stat`)". A per-job count loses in-flight CPU at a crash.
- Add the miss check's rule that a worker lost twice takes the floor.

**10. "As P4's pair rule does" is false.** `_takes_floor` matches only "the submitted structure could not be fitted", so a B-sym "no proposal" row is absent in P4, and a failure of any other cause is absent too. Reusing `floor_rows` would give the wrong rule.
- Delete the phrase.
- Add: "any row with no finite reading takes the floor, unless all 14 failed."
- Note for the record: the message is prefixed `B-sym: `, so match with `in`, not `startswith`.

**11. The test on `selection.json` cannot pass at `T`.** `.cache/` is git-ignored and is absent in a clean worktree.
- Commit `final.json` (and the exported table) under `docs/v2/`.
- `p4_plan` also does not reach P4b, so name a constant that P4b's plan must import.
- Add `final.json`'s path, an S3 copy, and that stage 2 runs at the same commit.

**12. Figures.**
- Section 4's chance lead was not rescaled to 12. It is 0.0055 to 0.0063, not 0.004 to 0.005.
- Section 7's missing figures: at 12 candidates a lead of 0.01 is picked 86% to 92% of the time, and 0.005 is picked 44% to 51%.
- `weak`'s "about 29 fits" is the stub's. Real units gave 30, 21, 25, 27, 20, 24, 18, mean 23.6.
- I found no source for "0.81 of a core".
- "6 GB" differs from the mechanism's 8 GiB gate; P3's peak worker was 2.18 GiB.

**13. Small gaps.**
- `BUG_STOP`'s mean should read "on complete worlds".
- "Above `c0`" should read "by section 5's order".
- Section 11's tests should add `c0b`, the floor at fit 30, a killed unit, stage 2's rule and `final.json`.
- "What has been touched" must add this round's units (see "How this ran").

## Round 1, finding by finding

| # | Status | Note |
|---|---|---|
| 1 | Moved | `c8`, `c9`, `c10` in, limit stated; the seed decided in writing is inert (6) |
| 2 | Answered | 240+40+4+240 = 524; 720; 840; even at 54 worlds, 648 exceeds 584 |
| 3 | Partly | Controls added; `c0b` and H1 can't be built as written (4, 5) |
| 4 | Partly | Stage exists; rule, mid truths and budget open (1, 2, 3) |
| 5 | Partly | Fallback and latch in; new routes through stage 2 and the owner (1, 7) |
| 6 | Moved | Stage 1 sound; stage 2's fit depends on order and cost (3) |
| 7 | Answered | Stale at 12 (12) |
| 8 | Answered | |
| 9 | Mostly | Strings, ORACLE, precedence, P4b done; 10 and 11 are new |

## Checked and sound

- **Export at 9db1a46:** it works and is the right commit, `43b88dc^1`. `load_rows` there gave 670 rows, 0 missing, on a store copy. Six systems' readings hash to `62cc0d58…` (lines `truth|seed|system|per_event.hex`, sorted), identical to enumerating cells at head. Name the config: seeds 1,2,3, `control_truths=10`. No committed tool writes the table, so commit it, adding B-lib and B-sym.
- **Fit-30 reading:** stored, and read the same way as the 40-fit one. Curve end equals `per_event` on 60 of 60 units, and all 60 have a point at 30. By the code, the first 30 fits of a 40-fit run are the fits of a 30-fit run.
- **`c0` bytes at head:** dev-005 s2 reading, structure and curve equal the store, a second unit after round 1's.
- **`c11` reach:** it moves B-sym, B-sym@10F, B-sym@F=80, planted-hint and the four policy arms. It does not move ORACLE, B-lib, B-np, B-rand, B-sparse or telling. The library and `prior_id` are unchanged.
- **F2:** nothing frozen moves.
  - The miss script pins only itself and its test, and builds addresses at run time.
  - Group B is ORACLE.
  - Identifiability reads `env.library`.
  - The tuned setting can land at `S`.
- **Floor against `weak`:** not a fault. It is implementable and matches B-lib's part in P4. Add a clause saying `weak` and `c0b` are in the floor's domain on purpose.
- **Section 8:** every sum checks; B-sym mean 325.2 s, max 1,537 s; 75.8; 5.4, 10.5, 27.8; 94; 100. The ceiling holds across restarts, given the cgroup count.
- **Other:** the fallback is implementable. Softplus 6 of 33 is confirmed. No seed 4, 5 or 6 in any of 19 ledgers under `.cache/`. Root 20261113 appears only in this text.

## What I could not check

- No candidate's held-out value was computed.
- Candidate costs rest on 3 units, and the ratio at 10×F is unmeasured. Cost, `-inf` rates and failures on seeds 4 to 6 are unknown.
- `-inf` floors at seeds 4 to 6 aside, the standard error still rests on round 1's 6 worlds and treats worlds as independent of truth.
- `bsym_tune.py` is not at head, so `RLIMIT_CPU` and resume were read in the pilot script, not exercised.
- Windows counts come from committed reports only.

## How this ran

- About 0.6 CPU hours, $0, no LLM calls, `nice`. No block or denial.
- Real units on seeds 1 to 3 only, three worlds planned and two reached: `c0`, `c9`, `c10`, `c11`, a softplus-Hawkes variant; plus fits of the seed structures on four worlds. Only CPU, fit counts, link counts and training BIC were read. The fix in finding 6 rests on these.
- Seeds 4 to 6, root 20261113, control ids and guards were not touched. `.cache/pilot-merged/ledger.sqlite` is unchanged.
- I stopped the cost run early on time (exit 143). A `pkill -f` of mine matched its own shell (exit 144), and I reran that check without it.
- The 9db1a46 read used `.venv/bin/python` with `PYTHONPATH`, to import the old tree.
- Head moved four commits during the review. None touched the text, `search.py`, `plan.py` or `execute.py`.

Scratch files: `/tmp/rt-bsym2/` (`cost.py`, `store/`, `c9db/`).

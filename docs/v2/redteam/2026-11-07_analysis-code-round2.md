# Red team, round 2: the analysis-code fixes and `campaign/tables.py` (session 38)

Reviewed: v2 at 742bda4. The commits are c060fc0 (`campaign/tables.py`, the report's per-model merge), 99056b1, 51a34ce and 2a378a1 with the rename (`analysis.control_rows`, `scoring/gonogo.py`, `scripts/pilot.py`), and 8d27554 (`scripts/pilot_report.py`, `campaign/secondaries.py`). Round 1: `docs/v2/redteam/2026-11-07_analysis-code.md`. Red-team agent, lane session 38, 2026-11-07. Saved verbatim below; the lead reran `p1_blib_crash.py` and got the same first two lines.

## Verdict: **not ready**

All four round-1 blocking findings are fixed, and so are most of the non-blocking ones. But the first property you asked me to check fails. A crash of B-lib (control 1's comparator) can still pass control 2. That one needs a ruling. Everything else below is non-blocking.

I reviewed `v2` at 742bda454565 using a `git archive` copy and a clone at `/tmp/rt-s38b/` (the clone has its own copy of the dev split). I ran `nice`, one process at a time, no LLM, $0. I used stand-in rows only, and toy root 11 wherever a probe of mine drew control 2. `git status --porcelain` on the repo is empty. No guard or hook blocked a command.

## Round 1, finding by finding

| # | Status | Evidence |
|---|---|---|
| 1 | Fixed as ruled | Reran `d1_controls.py` on the new head. With every B-rand unit crashed: `controls_pass=False … median=-inf`, reads reportable 0 of 7. The 40-truth case with 0 to 30 crashes: `controls_pass=False` at every count. |
| 2 | Fixed | `pilot_report.py --read-root 15` and `--naming-commit abc` both exit 2 (unrecognized). `--p4-block sonnet --resamples 200` gives `refused: a P4 block reads at the frozen 10000 resamples`. Control 4 is read at the store's commit, and a stored control that differs from `reads.json` is refused. |
| 3 | Fixed | On the Haiku block the scope reads `0 of 40` for both the stored and the fixed table. A block refuses `--ids` and `--truths`. |
| 4 | Fixed | Haiku block: `AG-c-H \| 40 \| 0.0100`. |
| 5 | Fixed for a store that holds test cells; still open on an empty P4 store | Finding 3 below. |
| 6 | Fixed for the ranked systems | A read exception in the miss script becomes `BYTE_FAIL` (`p4_search_misses.py:2421-2436`). B-lib is not covered (finding 1). |
| 7 | Fixed | My 27 mutants: 26 killed. The survivor is finding 7. |
| 8 | Fixed | The control 3 line says `6 of the 6 kept truths (those with a unit, a failed one ranked lowest)`, which equals what the verdict read. |
| 9 | Fixed, with one gap | Through the real CLI, `--arm-set p4-sonnet` on dev, a split holding one test truth, `--no-llm` and `--llm-limit 0` are all refused. `--rescore-llm` is not (finding 4). |
| 10–13 | Fixed | Floored count and left-out read columns are printed; "not reportable" gives its reason; the scope partition is checked; the header prints `Seeds [1, 2]`. |

**Determinism:** the report bytes are identical under `PYTHONHASHSEED` 0 and 12345 (Sonnet block `49a29786…`, Haiku block `1298e5d1…`).

**Tests:** the 8 in-scope test files pass from the clone, 248 passed. The archive copy shows 22 failures, all `FileNotFoundError` for `data/v2/truths/dev`, because `data/` is not tracked.

## Your five checks

- **`+inf` only from a crashed B-rand unit:** holds on the analysis path. `score_rows` drops `+inf` gaps (`analysis.py:704-706`), `control_rows` refuses NaN and `+inf` readings (`:767-784`), and `derive` gives `None` against a non-finite ORACLE. One caveat is in finding 8.
- **A crashed comparator can no longer help any control:** fails, through B-lib (finding 1).
- **The report's frozen root, resamples and commit:** none can be overridden.
- **Reuse refusal and block requirement through the real CLI:** both hold, except an empty P4 store (finding 3) and `--rescore-llm` (finding 4).
- **The store-is-P4 test by truth ids:** it cannot be fooled on real ids. Ids are `{split}-{j:03d}` (`sampler.py:608`) and every cell kind keys `config.truth` by id. It is fooled only when the P4 store has no cell yet (finding 3).

## Findings, ranked

**1. BLOCKING (needs a ruling): a failed, NaN or `-inf` B-lib unit still passes control 2.**
- **Where:** `analysis.py:813-828`. Control 1 reads finite gaps only, so the truth becomes unchecked and leaves `kept`. Controls 2 and 3 read only kept truths (`:880-885`). The NaN refusal covers ranked systems only (`:767-784`).
- **Failing input:** 40 truths on seeds 1 and 2. Planted-hint is 0.01 above B-rand on 30 truths and 0.05 below on 10. B-lib fails on seed 1 of 4 of those 10.
- **Output (`p1_blib_crash.py`, `p5_misc.py`):**
  ```
  no crash                          controls_pass=False c2 n=40 p=0.222   | scope 0/40 confirmatory=True
  B-lib crashed, 4 hostile truths   controls_pass=True  c2 n=36 p=0.00957 | scope 4/40 confirmatory=True
  B-rand / planted crashed, same 4  controls_pass=False
  B-lib = NaN or scored -inf        controls_pass=True  c2 n=36 p=0.00957
  ```
  An ORACLE crash also removes the truths, but control 1 then fails as unrecorded, so it cannot pass anything. `check_table` accepts a failed B-lib row.
- **Expected:** "controls fail" or "not shown", as for the same units of B-rand or planted-hint.
- **Why it needs a ruling:** the code follows INBOX 2026-11-06 ("Truths control 1 cannot check leave the reads"). That ruling collides with R3 direction's aim that "a crash cannot pass a control". R2's limit only labels the reads: up to 4 of 40 stays confirmatory.

**2. Non-blocking (fix before any report on a block other than Sonnet): the headline is computed on the block's own table.**
- **Where:** `pilot_report.py:1182` runs the verdict on `tab`; the gate runs on the merged table (`:966-971`).
- **Failing input:** `--p4-block haiku`, opus or any sweep block, on a table whose controls pass.
- **Output (`p2_blocks.py`):** `Verdict (computed): INCOMPLETE`, then control 1 `FAIL: no ORACLE unit …` for all 40 truths. The same report then prints `AG-c-H | 40 | 0.0100 | 0.0100 | 0.0100` and "0 of 40 truths left the reads". The Sonnet block shows `GO`.
- **Expected:** the headline and controls read from the merged table on every block.
- **Claim against evidence:** LOG.md:2749 says the change was made "so that the headline and the secondary's gate cannot disagree". That holds on 1 of 8 blocks. `test_the_scope_is_the_whole_plans_on_any_block` checks only the scope section.

**3. Non-blocking: reuse onto an established P4 store with no cell yet is allowed** (`pilot.py:456-471`).
- **Real CLI, case B:** `pilot.py --root store-p4-empty --split <dev> --truths 0 --reuse-from commit=000…,tree=clean --no-llm --no-baselines --workers 0` exits 0 and writes a `reuse` line.
- **Consequence:** a later block run on that store needs no flag. Then `store_commit` (`pilot_report.py:183`) and the miss script's witness (`p4_search_misses.py:2679`) refuse the store for good. It fails closed, but only after the block's sessions are spent.
- **Case A (store holding test cells):** refused, exit 1.
- **Expected:** refuse `--reuse-from` on a store with no cell, since there is nothing to carry.

**4. Non-blocking: a block accepts `--rescore-llm`.**
- **Where:** `P4_BLOCK_RUN_FIXED` (`pilot.py:93`) is checked against `build_parser()` defaults (`:412-420`), and `--rescore-llm` is added only in `main()`'s parser (`:519`).
- **Output:** `--p4-block sonnet --rescore-llm: launched, run_llm=True rescore_llm=True`, with `Campaign` recorded and not run. In rescore mode the driver never launches a session (`driver.py:514-515, 614-617`).
- **Expected:** refused, as `--no-llm` is. The module docstring says a block takes no option that would leave units unrun.

**5. Non-blocking (no caller yet): `sweep_rows` does not refuse narrowed references** (`tables.py:177-221`).
- **Failing input:** the Sonnet references narrowed to seed 1.
- **Output (`p4_tables.py`):** it returns without complaint, and `closed_flagged` goes from `True` to `False` on the same row. The docstring at `:49-51` says the seed-2 references "still set the noise flag".
- **No controls gate:** an ORACLE at `-inf` on a sweep world returns rows with no gap, and nothing says control 1 failed (SPEC §6.4).
- **Expected:** refuse unless ORACLE and B-lib cover seeds 1 and 2 on every sweep truth, and gate printing on controls 1 to 3 when it is wired.

**6. Non-blocking: `merge_tables` checks only rows for strays.**
- **Sessions and telling cells:** a session of `zz-999` merges silently (`tables.py:143-155`).
- **Two roots:** `/store-A` and `/store-B` under one fingerprint merge (`:131-135`). The docstring's limit paragraph speaks only of configs from one store.
- **Effect today:** in the only caller (`pilot_report.py:830-851`) both checks are vacuous. The real guard there is `store_commit`.

**7. Non-blocking (test gap): mutant o survives.**
- **Mutant:** the merged-size check at `pilot_report.py:968` replaced by `if False:`.
- **Why it survives:** `test_the_scope_refuses_a_table_short_of_the_split` still exits, but through the `reads.json` equality refusal.
- **Effect:** the mutant is equivalent on reachable test-split data, since that refusal catches the same case. The size check has no test of its own.

**8. Non-blocking (hardening): gonogo accepts `+inf` on any B-rand row from any caller** (`gonogo.py:174, 208-209`).
- **On the P4 path:** only a failed unit from `control_rows` can produce it.
- **Elsewhere:** `bsym_tune.py:2146-2156` builds B-rand rows from P3's stored values. A `+inf` there would now read as a crash instead of raising.
- **Effect today:** none on the running tuning, which runs at c8da063.

## What I did not check

- The slow tier, `mypy` and `ruff`.
- Any real store; no P4 store exists.
- Whether the scorer can actually emit NaN or `+inf` for B-lib.
- The interim-controls line (`pilot_report.py:781`), a limit the LOG already states; I did not re-run it.
- The miss script beyond its read-exception path, and the rehearsal.
- Whether real dev stores hold cells of truth ids outside today's dev split. The reuse refusal would refuse such a store, which fails closed.

My own probes never drew a frozen root. The mutation runs did execute the repo's own report tests, which draw `READS_ROOT` 20261110 on stand-in tables, as the fast tier does.

Files are in `/tmp/rt-s38b/`:
- probes: `p1_blib_crash.py`, `p2_blocks.py`, `p3_setup.py` (the CLI runs used the stores in `p3/`), `p4_tables.py`, `p5_misc.py`, `d1_controls.py`
- mutants: `mutants.py`, `mutants.out`
- the clone, at `clone/`

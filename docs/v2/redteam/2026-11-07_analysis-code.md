# Red team: `secondaries.py`, `analysis.control_rows`, `scripts/pilot.py --p4-block` (session 38)

Reviewed: `src/sciagent/campaign/secondaries.py`, `src/sciagent/campaign/analysis.py` (`control_rows`, `verdict`), `scripts/pilot_report.py`, `scripts/pilot.py` (`--p4-block`, `--reuse-from` refusal), `tests/campaign/test_pilot_p4_blocks.py`, at 7314241 (v2). Red-team agent, lane session 38, 2026-11-07. Saved verbatim below.

## Verdict: **not ready**

I reviewed at 7314241. HEAD moved three times during the review. 2b05867 and bcf8a3b change docs only. c060fc0 changes code ("Merge config tables for the sweep and every model's named minus anon", which touches `scripts/pilot_report.py` and adds `campaign/tables.py`). `secondaries.py`, `analysis.py` and `pilot.py` are byte-identical between 7314241 and HEAD; I compared their sha256s. I did not review c060fc0 itself, and I say below where it touches a finding.

**What held:**
- **R3 for the systems under test.** A failed or unsubmitted planted-hint or B-sym@10F unit ranks exactly as a `-inf` would.
- **The secondary's own rules.** The gates, `MIN_TRUTHS`, voids, the floors coming from the read's own two systems, and the streams (2000+i, clear of reads 0 to 6 and of 1000).
- **R2's thresholds.** 4 of 40 is inside the limit and 5 of 40 is past it; for C4, 2 of 20 is inside and 3 of 20 is past.
- **`p4_block`** equals `p4_plan` for every block.
- **Determinism.** The secondary and scope output had the same digest `525079cb6aeb959e` under PYTHONHASHSEED 0 and 12345, with rows in order and shuffled.
- **Tests.** The four in-scope test files pass: 82 passed at head.

Every scenario is in `/tmp/rt-s38/*.py` and was run with `nice .venv/bin/python <script>` on stand-in rows. I used toy roots 11 to 40 only, never a frozen root.

## Findings, ranked

**1. BLOCKING (needs a ruling): a crash of B-rand passes control 2.** `analysis.py:681` (`RANKED_SYSTEMS` includes `B_RAND`), `:749-772`, `:856-858`.
- **Failing input:** planted-hint is worse than B-rand on every truth, and every B-rand unit crashes.
- **What comes out:** control 2 passes with a median of `+inf`, and all 7 reads become reportable. Before R3 the same table failed the controls.
- **What should come out:** "controls fail". A positive control cannot pass because its comparator crashed.
- **Code against the words:** the code follows R3's words ("a failed unit ranks as `-inf`"). Those words produce the opposite of R3's stated aim, "a crash cannot pass a control". The LOG mentions that a failed B-rand "helps planted-hint", but not that a crash can turn a failing control into a pass.
- **Evidence:** `d1_controls.py`
  ```
  planted -0.80 < B-rand -0.75, nothing failed: controls_pass=False ... reads reportable: 0 of 7
  same, every B-rand unit CRASHED: controls_pass=True planted=passed=True p=0.0156 median=inf ci_low=inf
     reads reportable: 7 of 7
  same crash, pre-R3 rule (score_rows): controls_pass=False planted=None
  P4 shape (40 truths, seeds 1,2, planted 0.01 below B-rand):
    B-rand crashed on seed 1 of 25/40: passed=False ... 27/40: passed=True controls_pass=True
  ```
- **Fix, for the control room:** rank failed units lowest only for the system under test (planted-hint, B-sym@10F). A failed B-rand unit on a kept truth should fail control 2 ("not shown"), or leave it and be counted.

**2. BLOCKING: the report's gate is computed from inputs the operator types, not the frozen ones.** `pilot_report.py:917-930` and `:1339-1356` (at 7314241).
- **(a) Root and resamples.** `--read-root` and `--resamples` set control 2's bootstrap stream and are not pinned to `READS_ROOT`/`READS_RESAMPLES`. On a stand-in table, the report's own verdict says controls fail and `--read-root 11` agrees, but `--read-root 15` opens the gate, so the named-minus-anon reads print under a failing headline (`d3_root.py`):
  ```
  roots passing control 2's CI: [15, 17, 22, 33]
  pilot_report top verdict (default_rng(20261002)) controls_pass: False
  read_controls(--read-root 11) controls_pass: False
  read_controls(--read-root 15) controls_pass: True
  ```
  On a second stand-in table (`d2_secondary.py` (d)), `--resamples 200` gave False where 10,000 gave True.
- **(b) The naming commit.** `--naming-commit` is not checked against the store's commit. A control-4 record written at a stale commit opens the gate in the report but not in the miss script (`d7_naming.py`, scratch record):
  ```
  miss script's question, read_naming_null(T=...): False
  report's question, read_naming_null(--naming-commit 'commit-stale'): True
  ```
- **No test notices.** Mutant M1, which forces `naming_null = True`, survives every test.
- **Fix:** drop both options. Read the root and resamples from the miss script's constants, and the commit from the store's single `establish` line. Better still, read `controls.stored` from `reads.json`.

**3. BLOCKING: R2's scope line can be computed on the wrong table or the wrong truths.** `pilot_report.py:1347-1356`, `secondaries.py:168-177`.
- **(a) Wrong table.** With `--p4-block haiku` (or opus, or a sweep block) the table has no ORACLE or B-lib, so every truth counts as unchecked and the line says "N of N truths left … The reads are not confirmatory". That is false. It still happens at c060fc0, because the scope line still reads `tab`. On the 6-truth toy (`d2` (b), haiku block): `6 of 6 truths left the reads … The reads are not confirmatory`.
- **(b) Wrong truths.** Without a block, `--ids` or `--truths` shrink the denominator. The Sonnet config without a block is the same as the Sonnet block's config, so it reads the same addresses (`d2` (c)):
  ```
  all 40 truths: left=5 total=40 confirmatory=False
  --ids of the 35 kept: left=0 total=35 confirmatory=True   (no "not confirmatory" line)
  campaign_config(--arm-set p4-sonnet --seeds 1,2 --ids ...) == p4_block('sonnet'): True
  ```
- **(c) Only one of the two tables.** The limit is applied to the stored table only. The corrected table's controls (`reads.json` "fixed") never get it.
- **Fix:** compute the scope from the whole split (refuse if the total is not 40 on P4), on the Sonnet block's or merged table, for both of `reads.json`'s ControlsReads.

**4. BLOCKING at 7314241: Haiku and Opus reads can never be printed.**
- **Why:** `load_rows` reads one config (`pilot_report.py:957`), and only the Sonnet block holds ORACLE and B-lib:
  ```
  sonnet  ORACLE=True  B-lib=True  reads with both sides: ['AG-c-S', 'AG-o-S']
  haiku   ORACLE=False B-lib=False reads with both sides: ['AG-c-H', 'AG-o-H']
  ```
- **What the reader sees:** `| AG-c-H | 0 | not reportable | - | - |`, which looks the same as a gate failure.
- **c060fc0:** merges the tables for the `--p4-block` path only; without a block the report still reads a single config. I have not verified c060fc0.

**5. Non-blocking (fix before `S`): the launcher's `--reuse-from` refusal can be bypassed.** `pilot.py:393-408` and `:475`.
- **Route 1:** `--truths 0` on the test split. The refusal looks only at the selected truths, so `records selected: 0 … refuse_p4_reuse: returned (not refused)`.
- **Route 2:** any dev-split run pointed at the P4 store. I ran the real CLI against a scratch store: `pilot.py --root /tmp/rt-s38/store-p4 --split …/dev --truths 0 --reuse-from commit=0ld0000,tree=clean --no-llm --no-baselines --workers 0` exited 0. It wrote `reuse … from 'commit=0ld0000,tree=clean'`, and afterwards `guard_store(root, current)` returned `None`, so a later block run needs no flag.
- **Why only non-blocking:** both routes require naming the store's exact fingerprint. The miss script's store witness (`p4_search_misses.py:2679`) would refuse the store, but only at the terminal step, after the campaign has spent its sessions on the one-shot test split.
- **Tests:** mutant M15 (`main` passes `()` to the refusal) survives.
- **Fix:** pass the whole split, as `refuse_unread` does, and also decide "this is the P4 store" from the store's own content.

**6. Non-blocking: a NaN or `+inf` reading leaves controls 2 and 3 in `analysis.verdict`.** `analysis.py:684-703`, `:749-772`.
- **What comes out:** where `-inf` on 4 of 6 B-sym@10F units gives `controls_pass=False`, NaN and `+inf` give `True` (`d1` (c)).
- **Where it matters:** `read_controls` refuses these rows, so the reads fail closed. The pilot report's headline and `pilot_search_misses` do not.
- **Fix:** make `control_rows` raise a `ScoringError` on NaN or `+inf`.

**7. Non-blocking: the tests would not catch the obvious wrong versions of the report and launcher wiring.** 19 mutants on a `git archive` copy; 14 killed. The survivors:
- **M1:** control 4 forced to True.
- **M2:** the out-of-dictionary set inverted.
- **M3:** the scope read on another root.
- **M15:** no truths passed to the reuse refusal.
- **M16:** `Campaign` given the first truth only.

The killed ones covered R3, the secondary's gates, floors, voids, streams, and the R2 arithmetic. The fix is a set of `main()` tests that use LLM rows, plus a positive-path block test.

**8. Non-blocking: the report's control 3 line still counts from `score_rows`.** `pilot_report.py:665-676`, `:1184`. It says `median over 4 of the 6 kept truths (those with a result)` while the verdict read all 6 (`d1` (d)).

**9. Non-blocking: "a block takes no option that would narrow it" is not true.**
- `--p4-block` accepts `--no-llm`, `--no-baselines` and `--llm-limit 4`.
- Using a block is optional. A run on test truths without `--p4-block` (`--arm-set p4-sonnet --seeds 1,2 --truths 3`, or `--seeds 1 --b10f-truths 2`) passes `check_arms` and `check_surfaces` (`d5_launcher.py`).
- **Fix:** require `--p4-block` outside dev.

**10. Non-blocking: the secondary does not match §6.2's words in two places.** There is no read with the floored rows left out and no floored count, and §6.2 asks for it "beside every read". "Not reportable" gives no reason, and the test named `test_the_report_says_why_…` checks only those two words.

**11. Non-blocking: one docstring claim is false for Sonnet.** `secondaries.py:16-20` says "a read does not depend on which other models ran". A Sonnet AG-c named row at -3.0 on (t1, 1) moves AG-c-H's read from -0.00694 to -0.13194 (`d2` (e)). The behaviour is by design; the words should change.

**12. Non-blocking: `read_scope` double counts if `READ_UNCHECKED_TRUTHS` is ever turned on.** It computes `total = len(left) + len(kept)`, which gives `total=44` for a 40-truth table when the flag is True. It also reports an empty table as confirmatory. **Fix:** take the table's truths and check that kept, dropped and unchecked partition them.

**13. Non-blocking: the report header prints the wrong seeds.** Under `--p4-block` it prints `Seeds [1, 2, 3]` from the parser default; the block's config has `(1, 2)` (`pilot_report.py:952`, `:1006`).

## What I did not check

- c060fc0's `tables.py` and its merged-report path.
- Whether the scorer can actually emit NaN or `+inf`.
- Any real store: no P4 store exists, I read no pilot store, and I stayed out of the tuning store.
- The miss script beyond `compute_reads`, and the rehearsal.
- Slow tests, `mypy` and `ruff`: they write caches into the repo, so I did not run them.
- The sweep blocks' surface pins, which are known to be missing.

The SPEC's §6.4 still carries the old words; R2's and R3's words exist only in `SPEC-TEXT-P4-C.md`.

**Incidents:** no guard or hook blocked a command. No LLM calls were made, cost was $0, and the repo is unmodified (`git status` is empty).

Scratch files are in `/tmp/rt-s38/`: `d1_controls.py` … `d8_seeds.py`, `mutants.py`, `m10.py`, the scratch store `store-p4/`, and the copies `mut/` and `head/`.

Reviewed: `scripts/p4_search_misses.py` and `tests/campaign/test_p4_search_misses.py` at 4c5a21c, against `F2`. Saved verbatim from the red-team agent, lane session 37, 2026-11-07.

# Red team: `scripts/p4_search_misses.py` against `F2`, at 4c5a21c

## Verdict: READY WITH FIXES

I found no path that gives a passing label where the frozen text gives none, and no wrong label. The core is a faithful copy: 97 definitions match the pilot byte for byte, and the instrument functions I diffed differ only in docstrings, group E or `bit_check`.

It is not ready for rehearsal yet, for three reasons:
- The bytes rehearsed must be `R2`'s, so D1 and D2 have to land first.
- No tool can build the rehearsal store (P1).
- One control-room decision (T1) may change the bytes.

**Things you need to know about this review:**
- **Guard block.** One probe command was blocked: "Inline code that writes files and names the lane's own controls". Nothing in it ran, and I did not retry or split it. Its text named a path under the lane's `.claude` directory, which is probably the match, but that is my inference. It cost three checks, marked below.
- **The head moved** to 573e9e6, then 5d23ab1, and another session edited the shared checkout during my review. I reviewed the pinned commit from a read-only export. The script, its test, `contrasts.py`, `analysis.py` and `naming_null_record.py` are unchanged at 5d23ab1. `pilot.py` and `p4plan.py` did change (`--p4-block`, 83 lines), and I did not review that.
- **My first fast-tier run proves nothing.** Exit 0 was `tail`'s, and a doubled `-q` hid the summary. The results below are the rerun, with pytest's own exit code.

## Departs from the frozen text

### D1. BLOCKING before rehearsal: the rehearsal's truths and seeds are not pinned
- **Where:** `scripts/p4_search_misses.py:4497-4505`, `:4526-4527`.
- **Text (2.10):** "the first eight dev truths by sorted id, four of them out of dictionary, two seeds".
- **Code:** accepts any dev subset, and a plan with only `truths` gets three seeds at full budget.
- **Failing input**, `rehearsal_records` and `rehearsal_configs` on the real dev split:
```
hand-picked accepted: ['dev-013', ..., 'dev-019']
seeds with no key: (1, 2, 3) fits: 40 gp: 8
```
- **Why it blocks:** the bar decides whether `R2` can exist. The text lets a short rehearsal raise its budget, not swap truths, and a swap is recorded only as a different plan hash in the output directory's name. The first eight do hold 4 out of dictionary, so the rule can be met.
- **Side effect (by reading, not run):** that plan includes dev-015 with seed 3, which collides with group B's job id.
- **Caveat:** the allowlist sentence does not list this rule, so the lead may read it as procedural. I rank it blocking because it is truth selection on the gate.
- **Fix:** in `rehearsal_records`, require `spec["truths"] == sorted(ids)[:8]`, and require `seeds` to be present with two entries.

### D2. BLOCKING before `R2`: `--finalise --end` on a torn journal loses the label for good
- **Where:** `:2513-2514` appends with no repair. `repair_tail` is called only at `:1013`. `:2409` then fails.
- **Text:** 2.8 says `--end` "is also how a stuck unit is abandoned", and section 5 says `INCOMPLETE` "follows `--end`".
- **Probe output** (toy files):
```
journal tail: b'{"type":"run_start"}\n{"type":"done","job":"abc"{"reason":"end","type":"run_end"}\n'
terminal_step's read_records -> ByteFailure: unreadable record
a resume's repair_tail then check_start -> ByteFailure: unreadable record
```
- **Effect:** the merged line ends in a newline, so nothing treats it as torn. No label is ever possible, and the append-only rule leaves no lawful repair.
- **Reachability:** it needs a write cut off by power loss or a full disk, which is what `repair_tail` exists for.
- **Limit:** I replayed `finalise`'s steps by hand. The run through the real function was the blocked command.
- **Fix:** call `repair_tail(paths.journal)` in `finalise` before the append, with a test.

### Lower: fix in the same commit, since the bytes freeze at `R2`

| # | Where | Text | Code | Fix |
|---|---|---|---|---|
| D3 | `:4058`, same as `pilot_search_misses.py:1809` | "For S2, `c = f"s2\|{truth}\|{seed}\|{text}"`" | S2's full-log search uses plain `fit_case`. Reaches `fixed2` only | Prefix `s2\|` |
| D4 | `:3977`, `:4062`, `:2106` | PR 6 and 2.10: a clock stop is flagged, "the record names which" | The S2 path has 0 `cpu_cut` lines, so `_clock_stops` can never count an S2 cut | Add `cpu_cut` to the S2 payload |
| D5 | `:2764`, by reading only (run blocked) | "`--test-split` outside the repository" | `repo` is the `T` worktree, so the main checkout's `data/v2/truths/test` should pass | Also refuse under the parent of `git rev-parse --git-common-dir` |
| D6 | `:4641-4643`, copied from the pilot | PR 2.8: manifest "before and after" | A resume with `manifest.json` deleted takes a new baseline. This is the only route toward an unearned pass that I found, and it needs a record deleted | Refuse if `cpu.json` exists and the manifest does not |
| D7 | `:4284-4288`, test `:3550` | Group E: "prints only their count" | Prints no count, and the test pins that | Print the count, or record the stricter reading |
| D8 | `:4535-4537` against `:4558-4560`; `naming_null_record.py:38` | LOG 2026-11-06: control 4 is False | Both docstrings still describe a dev record | Correct them |

## Not a script fault, but it gates the rehearsal

**P1. Nothing can build the rehearsal store.** At the pin, `--fits` exists only in `scripts/investigate.py`. `ScriptedDriver` is used only by `p4_naming_null.py`, and `pilot.py`'s `campaign_config` sets no fits, experiments or GP budget. No test ties a store maker to `rehearsal_configs`, so "the store's maker and this script build the same addresses" is a promise in a docstring.

## The frozen text is silent or wrong: for the control room

- **T1 (decide before `R2`): P4b's freeze has no hold in the script.**
  - SPEC §6.1 (22408d7, 23 minutes after `F2`) and QUEUE:341 say P4b's contrasts freeze "before any P4 contrast is read".
  - `conclude` (`:2471-2489`) goes straight to the terminal step on `complete`, `cpu_stop`, `wall_limit` or `stop`, so the reads come at the end of the run with nothing to stop them.
  - Either P4b freezes before launch, as a procedural rule, or a guard goes into `R2`'s bytes.
- **T2: "`R2` adds the script" cannot be literally true.** The script has been on v2 since 48db3b4. `check_hashes` tests bytes only, so this is harmless, but it needs a note.
- **T3: the rehearsal's 24 hours is not enforced.**
  - The stop stays at 172 in the mode (`:281`, `:1070`).
  - `:4549` gives each repeat a fresh ledger, so section 8's 203 total rests on the operator.
  - "Changes exactly these" means fixing this takes an amendment.
- **T4: the C2 reads cannot be shown in a rehearsal.**
  - Control 4 is False there, so four of the seven reads never reach `reportable` before P4, and the bar can be met without them.
  - Their reportable path has run only on toys and on my synthetic store.
  - The LOG already notes that control 4's gap check adds little, so `naming_null`'s log-likelihood and structure comparisons carry it. I did not run its failure branch.
- **T5: group E on an exhaustive grid.** H2 says "over the coordinate rows", but such a job's miss would be counted and substituted. This is by reading and probably unreachable.
- **T6: the S3 launch-copy key at `:192` is the script's own choice.** The launcher must match it. If it does not, the guard refuses the start.

## Tests that prove less than they claim

| Test | Problem |
|---|---|
| `toy_run` (`:3914-3917`) | Mocks `load_store`, `check_table`, `build_census` and `contrast_rows`, and `p4_plan` appears nowhere in the file, so `execute()`'s real composition is untested. It works today (see below). `TUNED_GP` enters `p4_plan` at `S`, after `R2`, and this file would not notice a break. Port my probe as a test |
| `test_the_options_are_the_texts_and_none_sets_a_number` (`:2443`) | A superset check plus a blacklist. `--resamples` would pass it and `:4569`. Use equality |
| `test_a_rehearsal_on_a_toy_store_reads_only_at_its_terminal_step` (`:5010`) | Asserts only that the read was called, not when |
| `test_the_limits_are_section_8s` (`:205`) | Pins `CPU_CEILING_HOURS`, which no code uses (grep count 1), so it says nothing about 178 being enforced |
| `:2369`, `:2510` | A literal compared with a literal |
| `:2552` | Has no worktree case (D5) |

The two pilot tests on ORACLE and sibling rows are still unported, as QUEUE says. `_compare_row` differs from the pilot's only in its docstring, so that is a coverage gap and not a known fault.

## What held

- **Constants:** recomputed.
  - Prereg sha `4fc674dc…` at HEAD and at `F2`.
  - Fit diff `5ab0f61d…`.
  - Reductions blob `466612a9…`.
  - Dev split `31c18f10…`, by the shell pipeline and by the script's function.
- **Group B:** `per_event` and the kept `ll` match the pilot's `units.jsonl`.
- **Real plan on a synthetic store, 40 stand-in ids:**
```
load_store: rows 1940 sessions 1320 planned 1940 missing {}
check_table: passed
tier1 rows in census: 1000 contrast_rows: 1000
C1 ... n 40 rows 240 | C4 reportable True n 20 rows 120
```
- **Determinism:** identical bytes across hash seeds 0, 1 and 12345, and with a reversed journal under seed 7:
```
reads 260c3b96d126956b decision f01a559ad3f67298 units 672feeeefbf70f9e
```
- **Terminal step:** 1.1 to 1.5 CPU s at 10,000 resamples.
- **By reading, with no fault found:**
  - label order and "a read changes"
  - resume and `--end` logic
  - the commit-chain, store-witness and BLAS guards
  - the reads and decision write order
  - what is printed before the end
- **Tests, from the export:**
  - Fast: 1 failed, 377 passed. The failure is `test_the_fit_constants_are_this_repositorys`, because an export has no git, not a fault in the script; its digest and blob are the values recomputed above.
  - Slow: 16 passed, exit 0.
  - Total 394, matching the LOG.
- **Records:** no `p4-search-misses-F2` directory was created.

## What I did not read or could not check

- **Blocked, not done:**
  - the torn-tail run through the real `finalise()`
  - the `check_paths` worktree case
  - group B's R0 rerun at this head (the fit guard is unchanged since the last reviewer's R0 match at 9e61238)
- **`r0.search.ll`:** not compared with the pilot record, because my script stopped on a `KeyError` first.
- **Real data:** no real P4 cell exists. My store's cell details were hand-written; only its keys and addresses are real.
- **Not run:** R1 or R2 on any real unit, the launcher, `aws` inside a unit, and systemd's SIGTERM.
- **Partly read:** I listed all test names but read only about a third of the test file's bodies. I read only parts of `p4_naming_null.py`, and not `pilot_report.py`, LOG entries before 2026-11-05, or red-team rounds 1 and 2.
- **Not done, per the brief:** no mutants.
- **Streams:** I drew only 20261107 to 20261109. No frozen root or `ctl*` id was used.
- **Cost:** CPU was not kernel-counted; I estimate under 30 minutes. $0, no LLM calls, no repo edits.

**Scratch left in place**, since `rm -rf` under `/tmp` is barred: `/tmp/p4rt-head-QdJTv3` (the pinned export), `/tmp/p4rt-store-lm8uf6k1` (the synthetic store), and `/tmp/p4rt-4rkw4sn3`, `/tmp/p4rt-torn-kztzf7pm`, plus four `/tmp/p4rt-term-*` directories.

**Files:**
- `/home/ubuntu/lanes/sciagent/scripts/p4_search_misses.py`
- `/home/ubuntu/lanes/sciagent/tests/campaign/test_p4_search_misses.py`
- `/home/ubuntu/lanes/sciagent/docs/v2/prereg/2026-11-05_p4-search-misses.md`

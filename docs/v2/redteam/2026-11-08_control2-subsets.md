# Red team: control 2's subset rule (9a13b5f), one round

Reviewed: commit 9a13b5f on branch `worktree-agent-a3a86736a3a239031` (worktree `.claude/worktrees/agent-a3a86736a3a239031`) against base 7dfb95a: `src/sciagent/campaign/analysis.py` (`control_2_subset_failure`, `verdict`, `CONTROL_2_SUBSET_CAP`), `scripts/pilot_report.py`, `tests/campaign/test_controls_crash_rule.py`, `tests/campaign/test_pilot_report_wording.py`. Red-team agent, lane session 39, 2026-11-08. The agent's report is below, condensed by the lead with nothing of substance removed. Its scratch scripts and outputs were copied from `/tmp/rt-s39/` to `.cache/drafts/s39/rt-s39/`.

**Verdict: not ready, 1 blocking.** The code does what the commit says and only ever turns a pass into a fail. But the rule stops short of the queued rule and of the general rule, and the commit removes the report's last stated limit on control 2 while a demonstrated gap remains.

## 1. BLOCKING: an unchecked win that stays out can still open control 2 and the gate

The rule enumerates subsets of the unchecked *losses* only. An unchecked win that the lost B-lib unit would have kept can make control 2 fail; with its B-lib lost it stays out, and control 2 passes.

- The exact p is non-monotone in wins too (`scripts/win_search.py`, the project's `signed_rank_greater`): `(9, [-2, 4, -6, -8, 10, 12, 14, 16, 18], 1, 0.048828125, 0.052734375)`. Adding a win of 1 raises p from 0.0488 to 0.0527.
- End to end with `analysis.verdict` (`scripts/probe_crash.py`, the tests' `small_table` and `crashed`):
  ```
  B  no crash     : controls_pass=False outcome=controls fail kept=14 c2=(False, 14, 0.03784, -0.00244)
  B  w B-lib lost : controls_pass=True  outcome=go kept=13 c2=(True, 13, 0.04529, 0.00098) out_2=('w',)
  B' (rng 20261002, 10,000 resamples) no crash: controls fail;  w lost: go
  ```
  Base 7dfb95a gives the same lines, so this predates the commit.
- The report then states something false (`test_rt_s39_render.py`, scratch copy only): "(15 kept; left out, as they could only help it pass: unchecked ['dev-015']) … → **PASS**", verdict GO. With dev-015's B-lib present the same table renders CONTROLS FAIL.
- Neither fixed treatment of a win is safe. Always reading it in fails `test_control_2_an_unchecked_win_cannot_pass_it`; always leaving it out fails as above. The worst case is every subset of *all* unchecked truths that have a planted-hint and B-rand pair: each could have been kept or dropped, and its d does not depend on the lost B-lib unit.
- That is also what was owed: QUEUE.md:511 reads "every subset of the unchecked truths"; INBOX part (1) says "kept and unchecked alike".
- What this commit adds: it deletes the report's "stated limit" text, and the wording test asserts `"stated limit" not in text`.
- **Fix:** enumerate subsets of every unchecked pair truth, wins included, plus the full set when it is not the reported read; the cap applies to that count; table (B) becomes a test.
- Caveat: (B)'s flips go through the bootstrap CI's lower end near its boundary (tail probability from 10^6 draws 0.0254 to 0.0268), so how often this bites is not estimated. The verdict is deterministic and does flip.

## 2. Not blocking for this merge, pre-existing: with three or more seeds, one lost B-lib unit changes control 1's decision

The truth is then checked on its remaining two seeds, so a truth control 1 would drop can be kept (`probe_crash.py`, `probe_unsub.py`, `probe_p4gate.py`):
```
A no crash       : outcome=controls fail kept=4 dropped=('t',) c2=(False, 4, 0.0625, …)
A B-lib lost     : outcome=go kept=5 c2=(True, 5, 0.03125, …)   (not run, NaN, unsubmitted: same)
A3 (control 3)   : median -0.0255 FAIL -> -0.0010 PASS, controls fail -> go
read_controls three-seed lost=False: controls_pass=False / lost=True: controls_pass=True
```
The sweep uses two-seed tables only. Not in P4's main grid (`P4_SEEDS = (1, 2)`, p4plan.py:71), but in any design with three or more seeds, such as P3's. The docstring's "the worst case over every way the lost units could have come out" for control 3 is false there.

## 3. Not blocking: two mutants survive

| Mutant | Result |
|---|---|
| cap compared with `>=` | killed (`cap_boundary[2-False]`) |
| empty subset skipped | killed |
| last subset size skipped | killed (3 tests) |
| no rng copies at all | killed (rng-state assertion) |
| range one too long | survives, equivalent (re-reads the full set from the same state) |
| subset reads share one generator | **survives** |
| `start = rng` instead of a copy | **survives** |

So "each from the same generator state" is untested. A test could compare the failing subset's check with a fresh read that has a non-degenerate CI.

## 4. Not blocking, wording

`CONTROL_2_SUBSETS` prints whenever an unchecked truth entered, including when the full read already failed and no subset was read, yet says "a failing subset is named under the verdict". "Entered … as a loss" also covers ties (d = 0).

## 5. Not blocking, record-keeping

No LOG paragraph for the rule; QUEUE.md:511's wording differs from what was built.

## Other questions

- **Edge cases.** A subset read with fewer than 2 truths cannot occur while the full read passes (p < 0.05 needs at least 5 non-zero values); if reached, `planted_beats_rand` raises `ScoringError`, so it fails closed. Zeros are dropped from the signed-rank test (stats.py:157) but count in the median. When the full read fails, no subset is read and the reason is the full read's own.
- **Determinism.** Byte-equal full read at base and head (`cf843e21…`), the same caller rng state, the same verdict hash under three `PYTHONHASHSEED` values.
- **P3: unchanged.** Today's code reads the P3 stores as `incomplete` (PILOT.md:70, F5), so the table was loaded at 97444b6 (`dump_p3.py`); it reproduces PILOT.md exactly (18/0/2 over 20, p = 4.76837e-06, CI [0.014793, 0.030627], 20 kept, 0 dropped, 0 unchecked). Replayed at both commits: `go`, identical shared-field hash (`27c41a4d…`) and rng state. No truth enters as unchecked.
- **P4 prereg.** No contradiction found; `read_controls` goes through `analysis.verdict` (contrasts.py:406). "Controls 1, 2 and 3 gate every read", so finding 1 is live for P4's gate.

## Checks run by the reviewer

Changed test files `43 passed in 9.16s`; mypy `no issues found in 339 source files`; ruff clean on the four files. Fast tier on a `git archive` export at head: 5,931 passed, 3 failed (need git; the same 3 fail at base), 3 skipped. Worst case at the cap `seconds=12.2` at load 4.4. Not checked: the slow tests that read the verdict (stopped at the time limit), P3 directly from its store, and controls 4 to 6.

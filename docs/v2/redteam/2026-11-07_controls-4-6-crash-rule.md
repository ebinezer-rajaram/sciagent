# Red team: controls 4, 5 and 6 against the general crash rule (session 38)

Reviewed: `src/sciagent/scoring/controls_p4.py`, `src/sciagent/campaign/analysis.py` (`telling_observed`, `telling_rate`), `src/sciagent/campaign/contrasts.py`, `scripts/pilot_report.py`, `scripts/naming_null_record.py`, `scripts/p4_naming_null.py`, `scripts/p4_search_misses.py` (read only), at v2 5eee6d8, with the D5 commits 21e0518 and 156d98e rechecked. The rule under test (INBOX 2026-11-07, last entry): a crash or missing reading must never make a control pass that would otherwise fail. Red-team agent, lane session 38, 2026-11-07. Saved verbatim below; the harness neutralised some control tags in the agent's output, and none was in the findings.

**Verdict: not ready.** Four findings are blocking: three on control 5 and one on control 4. Control 6 has none. In each blocking case a crash or a missing reading turns a failing control into a pass.

I audited `v2` at 5eee6d8. Two commits landed while I worked (21e0518 and 156d98e, both D5 session-clock work). They change the tool layer and the live loop but no file in scope. I reran the two probes that go through the tool layer at 156d98e and got the same output.

**A guard blocked one command.** My first control 4 probe was denied by the permission system. It contained `rm -rf /tmp/rt-ctl456/c4`, which matches the deny rule `Bash(rm -rf /*)` in `.claude/settings.json`. Nothing ran; the scratch directory did not exist afterwards. I did not retry it. It cost the full-session version of control 4: real `run_session`, real score cells and real gaps. Instead I ran the same script straight through the tool layer (finding 3), which deletes nothing.

## Findings, ranked

**1. BLOCKING (control 5): a failed `B-sym+random` unit counts as "not observed", which helps truthFSD.** This is a direct application of the rule: the comparator's failure must count against the pass.
- **Where:** `src/sciagent/campaign/analysis.py:615`, `hit = r.ok and observed(...)`. `policy_separation` (`controls_p4.py:357-392`) receives only booleans, so it cannot tell a failure from a miss.
- **Pinned elsewhere:** the test at `tests/campaign/test_campaign_policy.py:376` asserts "failed: not observed". The P4b draft, `docs/v2/prereg/2026-11-07_p4b-policies.md:192`, says the same for `FAILED_FIT` rows.
- **Input:** 10 truths at seed 1. truthFSD observes t01 to t06, random observes t06 and t07, so b=5 and c=1. Then random's t07 cell is set to status 0.
- **Command:** `nice -n 10 uv run python /tmp/rt-ctl456/c5_standin.py` (and `c5_more.py`)
- **Output:** `as coded: passed=True p=0.0312 units=10 b=5 c=0`. With both arms failed on t07 (case 7): `passed=True p=0.0312`. With random failed on t07 and t01 (case 8): `passed=True`.
- **Expected:** `passed=False p=0.1094 b=5 c=1`, i.e. a failed random unit counts as observed.
- **Why it matters:** under ruling R5, a control 5 pass makes P4b's C5 and C3's AG-S-boed half confirmatory. The prereg also expects control 5's power to be near the test's size, so one crash is enough to flip it.

**2. BLOCKING (control 5): a failed or missing telling cell removes the unit from both arms.**
- **Where:** `analysis.py:289-296` and `:326-330` (a failed telling cell gives `None`), `:323-325` (a missing cell only adds to the missing count), and `:610-612` (`target is None: continue`).
- **Pinned elsewhere:** the test at `test_campaign_policy.py:375` ("no telling cell: not counted"). The draft at prereg line 201 says such a truth is "out of both telling reads".
- **Input:** the same base, with the telling cell for t07 at status 0.
- **Output:** `passed=True p=0.0312 units=9 b=5 c=0`.
- **Expected:** no pass. Either void control 5, as §5 rule 2 already does for an unpaired unit, or count the unit as random-only, which gives p=0.1094. **Choosing between those two is a design choice.**

**3. BLOCKING (control 4): errored tool calls are skipped.** If the experiment errors the same way in all three conditions, the control passes while comparing only the two fits on the observational data. This is a direct application: SPEC §6.4 says control 4 "covers belief, fit, experiment and submit".
- **Where:** `controls_p4.py:220-221` (`if body["is_error"]: continue`). Line 270 checks only that there is at least one fit, not that there are 4 fits and one experiment. The runner, `scripts/p4_naming_null.py:170`, checks only `retryable`; it ignores void status, the outcome and the calls used.
- **Input:** the planted script through the real tool layer on `size_excitation` at seed 20261002 (the test helper's seed). To stand in for a time-conversion fault on the experiment path, `rescaled`'s two `e0` fits are moved by one ulp.
- **Command:** `nice -n 10 uv run python /tmp/rt-ctl456/c4_layer.py`
- **Output with an experiments budget of 1:** `passed=False mismatches=['fit 3: rescaled log L -1546.7817327094585 != -1546.7817327094588', 'fit 4: ...']`
- **Output with a budget of 0:** `run_experiment(ERR: budget exhausted…)`, then both `fit(ERR: fit: no dataset 'e0'…)`, then a successful `submit`. `fits compared=2`, and with the same perturbation, `passed=True mismatches=[]`.
- **Stand-in case 1d** (`c4_standin.py`): the first belief errors in all three conditions, so the experiment is refused for lack of a belief. Result: `passed=True`.
- **Expected:** fail unless every condition shows 2 beliefs, `PLANTED_FITS` fits, 1 experiment and 1 submit with no errored call.
- **Caveat:** a budget of 0 is only a reproducible trigger; `planted_config` sets 1. The realistic triggers are a design or first belief that the layer refuses in every condition. The runner prints only gaps and mismatches, so a pass on 2 fits looks like any other pass.

**4. BLOCKING (control 5): a unit missing from both arms is silently dropped, because `policy_separation` cannot see the plan.**
- **Where:** `analysis.py:323-325` (no cell means no row) and `:613-614` (an `ok` row with no `computed` is skipped). `controls_p4.py:369-373` refuses only when one arm has a unit the other lacks.
- **Output:** with both arms' cells missing on t07 (an uncaught error records no cell), `passed=True p=0.0312 units=9`. The same happens when both arms are `ok` but lack `computed`. With only random missing it correctly gives `REFUSED (control 5's arms cover different units: [('t07', 1)])`.
- **Expected:** void unless every planned unit of both arms and its telling cell is present, or count the unit as random-only. **Partly a design choice.** The draft's §6 ceiling rule voids control 5 only for an unpaired truth, so a truth the ceiling stopped before either arm ran would also drop silently.

**5. Non-blocking (control 5 display):** in case 1, a failed random unit halves random's printed rate (0.20 to 0.10), which widens the gap a reader sees. It follows from finding 1's fix. The same asymmetry in C5's FSD-versus-EIG telling read is a contrast rather than a control, so it is **a design choice for the P4b text**.

**6. Non-blocking: controls 5 and 6 have no runner, no stored verdict and no gate in code.** Only tests call `policy_separation` and `behaviour_control`. `contrasts.py:4` says C6 "has no read", and `pilot_report.py:1035` prints each arm's prediction coverage without any control 6 check. A crash cannot flip anything here today, but nothing would refuse C5 or C6 if their control never ran. When they are wired in, they should fail closed the way control 4's record does: a missing verdict reads as False.

**7. Non-blocking, outside the crash rule:** `read_naming_null` (`scripts/naming_null_record.py:84-101`) ignores `split_sha256`; a record holding `"x"` there reads True. A record from another split labelled "test" at the same commit would therefore open C2's gate.

## What fails or refuses correctly
- **Control 4:**
  - A missing condition record or gap raises `ScoringError`.
  - A NaN or infinite gap fails, and so does a failed score cell, a missing submission, or an ORACLE that failed or scored -inf (`c4_gap.py`).
  - A record that ends before `submit`, in one condition or all three, fails. An experiment that errors in one condition only fails on the fit count.
  - The record reader returns False for a missing, torn or empty file, another or empty commit, one half failed, a single verdict, or `passed` written as `"true"` or `1`. A second write raises `FileExistsError` and keeps the bytes (`c4_reader.py`).
- **Control 6:** every prefix of a real record that ends before the final `submit` fails. Only the prefixes holding all 14 tool calls (entries 29 to 32 of 32) pass. Three runs gave the same record head, `ee1e0ed821abc3cc` (`c6_prefix.py`).
- **Tests:** `uv run pytest -p no:xdist -m "not slow"` on the three control test files gave 63 passed, 6 deselected.

## Not checked
- The full-session control 4 (blocked), and `p4_naming_null.main` end to end.
- `pilot_report.p4_lines`, `store_commit` and the miss script's control path, since they need a store; `read_fingerprints` on a torn sidecar.
- The slow test tier.
- The "Expected" readings in findings 1, 2 and 4 come from my own `ruled()` function in `c5_standin.py`, not from repository code.
- I did not read `SPEC-TEXT-P4-C.md` or the P4b red-team reports.

No store, no record path and no control stream were read, and the repo was left untouched. The scratch scripts are in `/tmp/rt-ctl456/`; findings 1, 2 and 4 come from `c5_standin.py` and `c5_more.py`, finding 3 from `c4_layer.py` and `c4_standin.py`.

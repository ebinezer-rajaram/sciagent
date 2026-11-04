# Red team of fsd-controls/criteria.md v5 (sha256 64fcabe2…) and engineer-contract.md (b82ac0f6…), session 17e, 2026-10-29
Verdict: READY WITH FIXES. There is one new BLOCKING item (N1): v5 drops every preregistered reading of the outcomes. It is a text-only fix and needs no runs. The other new items are non-blocking. Once N1 is fixed, v5 is ready to freeze when controls.py exists.

**Reproduced.**
- Both hashes match the coordinator's.
- `nice -n 10 python3 -I cost5.py`: `cmp` against cost5-out.txt says IDENTICAL (0.01 CPU s). Worst case 6.58 CPU h × 1.15 = 7.57, so the 7.6 h ceiling is consistent.
- cost5 covers the four subsample sets, every rung, scoring and a determinism rerun (charged twice, which is conservative). The per-run costs are medians, so 7.6 h is an estimate; the stop rule is what makes it a cap.

**(a) My 4 BLOCKING items from s17d.**
1. Resolved. The gate now hashes the files on disk (:176), diffs the working tree against F (:177), and lists ignored files in $D and $WT/src (:179-180).
   - The author's correction is right in substance, and my "empty today" was wrong: I had filtered out `__pycache__`. The count is wrong, though. `git -C $WT status --porcelain --ignored -- src` lists 21 `__pycache__/` dirs (mtime 22:21 yesterday), not 3. That is harmless, because the rule allows any number of them.
   - Residual, non-blocking: PYTHONDONTWRITEBYTECODE only stops Python writing bytecode. It still loads a stale or planted .pyc for power2.py or probe.py from an allowed `__pycache__/`. Fix: also set `PYTHONPYCACHEPREFIX=$(mktemp -d)`.
2. Resolved in substance:
   - F must be the only commit that touched controls.py, and nothing after F may touch a frozen file (:178).
   - F is pushed and logged in LEDGER.csv (:169).
   - There is a deviation rule (:189-192).
   - The residual is N2.
3. Resolved. Every rung is reported (:65), VOID applies only when every rung is unbuildable (:67), and §3 has a gated list. The residual is N3.
4. Resolved. Any failure fails the rule, with no rerun or extension (:98). The arithmetic is now correct: the union bound is 1 − 0.0055 − 0.05 − 0.05 = 0.8945.

**(b) My "can an engineer implement" list.** Every bullet is answered:
- which p₃₂ feeds which formula, and the held-out gate (:55, :61);
- how D is identified (:175, :183);
- the files are hashed on disk (:176);
- cpu-total.txt: created at 0.0, refuses if missing, never reset; score is exempt from STOP; resume rules (:199-207);
- B's mixed-failure and size-rung readings (:134-138), and A1's P3 reading (:64-71);
- cut0 and non-finite values (:14, :17);
- the 99% clause: 1,000 subsamples from CRN indices 128–255 (:56);
- Δ_pred is moot because A2 is deferred (§4);
- canonical JSON (:214);
- the import ban, with parity fixtures copied into $D/parity (:7, :223);
- v5 is self-contained;
- a code red team comes before F (:167).

Small gaps that remain:
- p₁₆'s design set ("outside E_cal") is stated only for D*₀ (:54-55).
- `git show --name-only` prints repo-relative paths, not `$D/…` (:183).
- Nobody is named to remove STOP on a resume.
- The pre-F ctl-det run and the stub tests cannot pass the gate, and cpu-total.txt does not exist yet. State that they call internal functions, and that no CLI flag bypasses the gate.
- The determinism rerun is on design 0, which never truncates, so it does not exercise the truncation path. A byte mismatch has no stated consequence. Use e1 instead, and treat a mismatch as VOID.

**(c) New problems in v5.**
- **N1. BLOCKING.** The outcome readings are gone. Every v4 §6 line is missing from v5 (`grep -n -i "ready|thresholds are not|unpredicted|favour nothing|own exclusion|design-aware"`: v4 has hits at 199–211, v5 has only :144 and :207). The lost lines are:
  - "no candidate is ready … the thresholds are not changed";
  - "VOID and INCOMPLETE favour nothing. Unpredicted patterns are reported, and no rule is chosen on them";
  - B's "FSD needs its own exclusion" and "K1 needs design-aware matching".

  With them gone, what a pass or fail means gets decided after the data are in. The only consequence still written down is JOURNAL :1525 ("Option 2, provided the two controls … pass"), and v5 §3 contradicts it. Fix: restore those lines in item 4's wording, and correct the JOURNAL. `grep` shows :1518 and :1525 are still unchanged.
- **N2. Non-blocking.** The deviation rule cannot run under v5's own gate.
  - Problem 1: under any F′, controls.py's log is either "F′, F" or just "F". The gate requires it to equal the commit passed, so it refuses every F′, and "restarts from calibrate under F′" (:191) cannot happen.
  - Problem 2: "has any test data" is not mechanical. runs-*.jsonl is never committed, so data drawn under F can be deleted and the rerun under F′ presented as preregistered.
  - Fix: v6's gate accepts the F → F′ chain recorded in LEDGER.csv. Any control with a pushed D under an earlier F counts as having test data, and its F′ result is labelled exploratory automatically.
- **N3. Non-blocking.** The rung reading misattributes some failures.
  - A rung that fails only the 99% clause (cells saturated at He² 1.0) is reported as an "R = 16 shortfall" (A1) or a "cost of Fx" (B). It should count as unbuildable, like P2.
  - A θ₋-rung failure of P3-B is blamed on the artefact without checking. B's own h (from p₁₆, which B already computes) separates the two causes: call it a cost of Fx's exclusion only if h ≥ 0.95 > h_F.
  - "A stronger cost" for the size rung (:136) overreads. There the artefact sits off the top design, so a failure most likely means plain R = 16 resolution.
- **N4. Non-blocking.** v5 contains no run command. Only the contract has `OPENBLAS_NUM_THREADS=1`, `nice -n 10` and `uv run --no-sync python -I` (`grep -c OPENBLAS criteria.md` gives 0). BLAS threads add to getrusage and can change output bytes, so v5, which wins on conflict, should carry the command.
- **cpu-total rules: sound.** A separate lock file with `os.replace` avoids the lost-update problem. It refuses a missing or unparseable total, score is charged, STOP exits with status 3, and the overshoot is bounded at :196.

**(d) engineer-contract.md against v5.** They agree on the CLI, imports, every gate check and the run-output list, run outcomes, rules, subsample order, records, JSON, ledger, parity, stub tests and sequence. The differences:
- the command and environment line exists only in the contract (N4);
- the contract leaves out step 1 (commit v5) and the audit-line fields;
- neither document says what test and score do, or which exit status they use, when no rung passes or every rung is unbuildable.

**Not checked.**
- controls.py does not exist, so I could not test the gate, the ledger or determinism.
- I did not demonstrate the .pyc loading behaviour; it is standard CPython.
- I did not re-verify the line numbers cited for `policies.choose` or controls_p4.

Files:
- /home/ubuntu/lanes/sciagent/.cache/drafts/fsd-controls/criteria.md
- /home/ubuntu/lanes/sciagent/.cache/drafts/fsd-controls/engineer-contract.md
- /home/ubuntu/lanes/sciagent/.cache/drafts/fsd-controls/cost5.py
- /home/ubuntu/lanes/sciagent/docs/v2/JOURNAL.md
- Scratch: /tmp/rt17e.out

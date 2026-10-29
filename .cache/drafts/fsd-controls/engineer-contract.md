# controls.py contract (theorist, v8; summary of criteria.md v8 §0, §5–§8, which wins). R=/home/ubuntu/lanes/sciagent, D=$R/.cache/drafts/fsd-controls, P=.cache/drafts/fsd-controls, WT=$R/.claude/worktrees/fsd-dist @ bff61ecba3daed876e55896e329000704fb9626b, URL=https://github.com/ebinezer-rajaram/sciagent.git.

**Changes from v7 that touch the code.** These are the code-side parts of criteria.md's changelog:
- fetch into a private ref, then a new chain and test-data rule, and H1/H2;
- calibrate pushes D itself and keeps nothing on disk before the push (drop `runs-<C>-cal.jsonl` and calibrate resume);
- no `--cpu-ceiling-h`; the ceiling comes from rows;
- `result` codes: score exits 12 or 13 on an untested control;
- the runs header; the B separation shortfall;
- internal functions refuse the real ids;
- `VERSION` and `CRITERIA_SHA256` constants.

**Run.**
- In `tmux -L sciagent`, at most 2 processes:

  ```sh
  env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPYCACHEPREFIX=$(mktemp -d) \
    nice -n 10 uv run --project $WT --no-sync python -I $D/controls.py --worktree $WT --freeze-commit F \
    [--d0-commit D] --control {A1,B} --phase {calibrate,test,score}
  ```
- Seeds, R, ids, rungs, thresholds, URL and the ceiling rule are constants. A2 is deferred.
- The process needs git push credentials (calibrate) and `gh` (H1, H2).

**Runtime (E1).**
- The first statements of controls.py, before any import from $D or $WT: `sys.dont_write_bytecode = True` and `sys.pycache_prefix = os.environ["PYTHONPYCACHEPREFIX"]`.
- check_runtime refuses (10) unless:
  - `sys.flags.isolated == 1`;
  - the prefix exists and lies outside $R;
  - the three thread variables are 1;
  - every `sciagent` or `environments` module's `__file__` is under $WT/src.
- Imports: the standard library, numpy, sciagent/environments from $WT/src, and modules in $D only.

**Gate, every phase.** Any failure, including any failed git or gh command, is a typed error and exit 10. No flag or env var bypasses it.
- **Fetch.**
  - Under `fcntl.flock($D/git.lock)`: `git -C $R fetch --no-tags --no-write-fetch-head URL +refs/heads/v2:refs/fsd-controls/v2`, with a 120 s timeout.
  - O = `rev-parse refs/fsd-controls/v2`, held in memory. Every check uses the sha O; nothing reads origin/v2 or FETCH_HEAD.
- **H1.** `gh api repos/ebinezer-rajaram/sciagent/branches/v2/protection --jq '[.allow_force_pushes.enabled,.allow_deletions.enabled,.enforce_admins.enabled]'` prints `[false,false,true]`. This is the owner's action and is not yet done, so the gate refuses until it is.
- **H2.** `gh api 'repos/ebinezer-rajaram/sciagent/activity?ref=refs/heads/v2&activity_type=force_push&per_page=100' --jq length` prints 0, and so does `activity_type=branch_deletion`.
- **Rows ever added.** The `+` lines (not `+++`) of `git -C $R log --full-history --no-merges -p --format= O -- docs/v2/LEDGER.csv`; a row's `item` is CSV field 2.
- **Chain.**
  - C1: `git -C $R rev-list --full-history --no-merges --topo-order --reverse O -- $P/controls.py` is non-empty.
  - C2: each freeze is an ancestor of the next.
  - C3: each is named by some row ever added whose item contains `fsd-controls freeze <full sha>`. Duplicates are fine; other shas are listed and ignored.
  - C4: F is the last freeze.
- **C5.** The sha256 of criteria.md and controls.py on disk equal the lines in F's message, and criteria.md's equals controls.py's `CRITERIA_SHA256`.
- **C6.** `<frozen>` = `git ls-tree -r --name-only F -- $P`. It holds no run-output path.
- **C7.** `git diff --quiet F O -- <frozen>`, `git diff --quiet F HEAD -- <frozen>` and `git diff --quiet F -- <frozen>`. These compare content, not history.
- **C8.** `git status --porcelain --ignored -- $P` lists only `!!` run outputs:
  - `__pycache__/`, `.ruff_cache/`, `*-err.txt`;
  - `cpu-total.txt`, `cpu-total.txt.tmp`, `cpu-total.lock`, `cpu-ledger.csv`;
  - `git.lock`, `STOP`;
  - `D0-*.json`, `runs-*.jsonl`, `score-*.*`.
- **C9.** `git -C $WT rev-parse HEAD` == bff61ec…, `git -C $WT status --porcelain --ignored -- src` shows only `!!` …/__pycache__/, and the runtime checks pass.
- **Status.**
  - It is "exploratory deviation" iff some X in `git -C $R rev-list --full-history O -- $P/D0-<C>.json` fails `merge-base --is-ancestor F X`; otherwise it is "preregistered".
  - Every output carries `"status"`. The score files list every X with its descent flag.

**calibrate.**
- **Refuse (10) at start** if a D under F exists: a non-merge X in `rev-list --full-history --no-merges O -- $P/D0-<C>.json` with F ancestor of X and `cat-file -e X:$P/D0-<C>.json`. Print X.
- **The computation.** Every rung (A1 3, B 4, plus the B ref check); the per-design and CRN sets, 256 per side.
- **Bootstrap**, 1,000 each, in this order:
  - D*0 on indices 0–127;
  - held-out p32 and p16 (e-major over the designs outside E_cal);
  - p_F (B only);
  - the 99% clause (CRN, 128–255).
- **Rung reading.**
  - Unbuildable means it fails P1, P2 or the 99% clause.
  - A1: a buildable rung that fails P3 is an R = 16 shortfall.
  - B: if h < 0.95, an R = 16 shortfall; else if h_F < 0.95, a cost of Fx; else a separation shortfall.
- **Everything stays in memory.** Nothing about any run, value or result goes to a file, stdout or stderr before D is on origin/v2. Progress lines carry counts only. There is no calibration runs file and no calibrate resume.
- **Then, under git.lock, at most 20 attempts with 30 s waits:**
  1. Fetch, and rerun the whole gate at the new O.
  2. Build D with a temporary `GIT_INDEX_FILE` outside $R: `read-tree O`; `hash-object -w --stdin`; `update-index --add --cacheinfo 100644,<blob>,$P/D0-<C>.json`; `write-tree`; `commit-tree -p O`, with message `fsd-controls D0 <C> under <F>` and `D0-<C>.json sha256: <h>`.
  3. `git push URL <D>:refs/heads/v2`: plain, never `+`, never `--force`, 120 s timeout.
  4. Fetch. Success iff D is an ancestor of the new O.
- If every attempt fails, exit 10 with nothing written.
- **On success:** write D0 (tmp + os.replace), print D, exit 0, 12 or 13.
- D0 records no time and not O.

**test and score (`--d0-commit D`).**
- The D-candidates under F are exactly {D}.
- D has one parent, `git show --name-only --format= D` prints exactly `$P/D0-<C>.json`, and D's first line names F.
- sha256(on-disk D0) == sha256(`git cat-file blob D:$P/D0-<C>.json`). Do not use `git diff`, which exits 1 for an untracked file.
- D0's F, criteria and controls.py hashes, numpy and scipy versions and uv.lock hash equal the current ones.
- `runs-<C>.jsonl` starts with the header {control, F, D}. test resumes only on a matching header and refuses otherwise.

**Runs.**
- `run_experiment_truncating` (cap POLICY_MAX_EVENTS); values come from `diagnostic_value` on `clip_to_analysis(ds, τ)`.
- Outcomes: complete | truncated | cut0 (ViewError; counts as truncated, all values None) | stopped (policies.STOPPED).
- Any other DiagnosticError, or a non-finite value, becomes None, counted in none_other or nonfinite.
- Streams exactly as criteria.md §0.
- The internal draw functions refuse "ctlA1", "ctlB" and every derived id unless passed the gate's token.

**Rules** (criteria.md §0).
- (a), (b), (c); K0, K1, K2z, K3a, K3h, K4, K5; Fa, Fc, Fz, Fx.
- Exclusion **removes** a design from the argmax (it does not zero it; E7).
- All excluded: no pick, a miss, the unit kept, no exception, and Telling.from_table is never called.
- Telling is d-major; truthFSD and FSD are e-major. A hit is label equality.

**Results.**
- Every output has `"result": {"code", "name"}`, which is authoritative.
- Codes:
  - 0 done;
  - 10 gate (including a missing cpu-total);
  - 11 STOP or INCOMPLETE (including score on an unfinished test);
  - 12 VOID at construction (every rung unbuildable, or B's ref check failing at both θ₊);
  - 13 not tested;
  - 14 VOID by breaches (more than 2 of 15 seeds);
  - 15 VOID by determinism.
- Any other exit code is a crash.
- calibrate exits 0, 12 or 13; test exits 12 or 13 at once, drawing nothing.
- score exits the terminal code (0, 12, 13, 14 or 15). It checks determinism first, so 15 beats 14. On 14 or 15 it writes the result and its evidence only, with no rule scored; on 12 or 13, D0's per-rung values.
- **P1 breaches at test time** (a truncation, cut0 or stop where calibration had none, or a run that does not truncate where 256/256 did) are scored as observed and listed by seed.

**score.**
- Every rule; A1 (i)–(iii); B1–B3 per (F-rule, K-rule) pair.
- The determinism rerun: seed 101's (A1) or 301's (B) telling runs on the truth's truncating design (A1 e1; B e2, or e3 on the size rung), compared byte for byte.
- It records O, D, the chain and rows, H1 and H2, every X, and the sha256 of the runs file.

**Ledger.**
- cpu-total.txt is created "0.0" once, before F₀, and never reset. Missing or unparseable → exit 10.
- Update under `fcntl.flock(cpu-total.lock)`: add the getrusage(SELF)+getrusage(CHILDREN) user+sys delta, write cpu-total.txt.tmp, os.replace; then append (phase, control, unit, delta, total, pid, UTC time) to cpu-ledger.csv, which is never re-read.
- test and score update after every run, at least every 30 CPU s in loops, and at exit. calibrate updates only every 30 CPU s and at exit, with `unit` a step counter.
- The ceiling is the max c over rows ever added: `fsd-controls freeze <sha> ceiling <c> CPU h` or `fsd-controls ceiling <c> CPU h`. It is cumulative; F₀'s is 7.6.
- Read the total before each step. Total ≥ ceiling → write STOP; on STOP exit 11. score is charged but exempt.
- Only the coordinator removes STOP, after pushing a ceiling row.

**parity/.**
- Copies of the fsd-trunc lines and truths for dev-002/005/006/015 and candidates.json's picks, with source sha256s in a manifest.
- K0–K4 must give 0 mismatches. Parity is 72/72 under removal or zeroing alike, so a stub test checks removal.

**test_controls.py** (output in test_controls-out.txt). It and the pre-F ctl-det run (2 runs, `cmp`) call internal functions, not the CLI. It covers:
- every rule on stub tables;
- removal versus zeroing (all remaining cells 0);
- exclude-all for every arm, telling included;
- cut0 and non-finite values;
- the rung reading, including the separation shortfall;
- P1 breaches;
- every result code, with 15 over 14 and evidence-only VOID files;
- in a scratch git repo with a local bare origin:
  - each gate refusal: fetch failure, C1–C9, the D checks, and H1/H2 via an injected gh reader;
  - the status in s18's S1–S3 and the S4–S5 restarts;
  - the D build and push against a concurrent pusher;
  - nothing written after a calibrate killed before its push;
- the runs-header refusal and the ceiling read from rows;
- the ledger: no lost update with 2 processes, loops charged, a missing total → 10, STOP fires, calibrate's coarse updates;
- canonical JSON bytes;
- the internal functions refusing the real ids;
- X never reaching FSDPolicy, the tool layer or an agent record.

**Sequence.**
1. The coordinator commits criteria v8.
2. Code; controls.py is never committed before F₀. The tests pass.
3. One review, joint with the code review; fixes go in before F₀.
4. The owner turns on v2 protection (H1).
5. cpu-total.txt = 0.0, once.
6. F: one non-merge commit, `git add -f`, changing controls.py, with both hashes in its message and no run output in its tree. Push it, then push the LEDGER row `fsd-controls freeze <F> ceiling 7.6 CPU h` (hours 0, usd 0). There is no veto wait.
7. calibrate (pushes D) → test → score.
8. The coordinator commits and pushes the score files, and copies the runs files to S3.

**Re-freeze (F′).**
- Score every finished test.
- `git rm` the tracked D0-*/score-* in a commit before F′, and archive the untracked outputs to `$R/.cache/fsd-controls-archive/<F>/`.
- F′ needs a new criteria version, its review, and a row.

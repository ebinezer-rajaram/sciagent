# controls.py contract (theorist, s17; summary of criteria.md v7 §0, §5–§8, which wins). R=/home/ubuntu/lanes/sciagent, D=$R/.cache/drafts/fsd-controls, WT=$R/.claude/worktrees/fsd-dist @ bff61ecba3daed876e55896e329000704fb9626b.
- Run (tmux -L sciagent, at most 2 processes): env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1
  PYTHONPYCACHEPREFIX=$(mktemp -d) nice -n 10 uv run --project $WT --no-sync python -I $D/controls.py --worktree $WT --freeze-commit F
  [--d0-commit D] --control {A1,B} --phase {calibrate,test,score} --cpu-ceiling-h 7.6. Seeds, R, ids, rungs, thresholds are constants. A2 deferred.
- Imports: stdlib, numpy, sciagent/environments from $WT/src (assert each module's __file__), modules in $D only.
- Gate, every phase (typed error naming the check, exit 10; no flag or env var bypasses it): git -C $R fetch origin first;
  the env vars above (threads = 1, PYTHONPYCACHEPREFIX outside $R); sha256 of criteria.md and controls.py ON DISK = lines in F's message;
  git -C $R diff --quiet F -- <frozen>, <frozen> = git -C $R ls-tree -r --name-only F -- $D;
  freeze chain F0, F1, … from `git show origin/v2:docs/v2/LEDGER.csv` rows "fsd-controls freeze <sha>"; F = newest and an ancestor of
  origin/v2; git log --format=%H -- $D/controls.py = the chain, newest first; git log --format=%H F..HEAD -- <frozen> empty;
  git -C $R status --porcelain --ignored -- $D shows only !! run outputs (__pycache__/, *-err.txt, cpu-total.txt, cpu-total.lock,
  cpu-ledger.csv, STOP, D0-*.json, runs-*.jsonl, score-*.*); git -C $WT rev-parse HEAD == bff61ec…;
  git -C $WT status --porcelain --ignored -- src shows only !! …/__pycache__/;
  test/score: F strict ancestor of D; D an ancestor of origin/v2; git show --name-only --format= D == .cache/drafts/fsd-controls/D0-<C>.json;
  git diff --quiet D -- it; git log D..HEAD -- it empty; numpy, scipy and sha256($WT/uv.lock) equal D0's.
- Test-data rule: under F_k, if for some j < k `git log --format=%H F_j..F_{j+1} -- .cache/drafts/fsd-controls/D0-<C>.json` is non-empty,
  every output carries "status": "exploratory deviation". A D made under F_k never counts.
- Run: run_experiment_truncating(cap POLICY_MAX_EVENTS); values from diagnostic_value on clip_to_analysis(ds, τ).
  Outcomes: complete | truncated | cut0 (ViewError: counts as truncated, all values None) | stopped (policies.STOPPED).
  Other DiagnosticError or non-finite → None, counted in none_other / nonfinite. Streams exactly as criteria.md §0.
- Rules (criteria.md §0): (a), (b), (c); K0, K1, K2z, K3a, K3h, K4, K5; Fa, Fc, Fz, Fx. Exclusion removes a design from the argmax;
  all excluded → no pick, miss, unit kept, no exception, never Telling.from_table. Telling d-major, truthFSD/FSD e-major. Hit = label equality.
- calibrate: every rung (A1 3, B 4, plus the B ref check); per-design and CRN sets, 256 per side. Bootstrap order (1,000 each): D*0 on
  indices 0-127; held-out p32, p16 (e-major over designs outside E_cal), p_F (B); the 99% clause (CRN, 128-255).
  Rung reading: unbuildable = fails P1, P2 or the 99% clause; buildable but fails P3 = R = 16 shortfall; B P3-B failure = cost of Fx only
  if h ≥ 0.95 > h_F, else R = 16 shortfall. D0-<C>.json holds every rung's values and the status.
- Exit statuses (never 1 or 2, Python's own): 0 done; 10 gate; 11 STOP (INCOMPLETE); 12 every rung unbuildable (VOID); 13 no rung passes,
  at least one buildable (not tested); 14 VOID by test-time P1 breaches (more than 2 of 15 seeds); 15 VOID by determinism mismatch.
  test exits 12 or 13 at once, drawing nothing; score then writes score files from D0 alone and exits 0. score writes its files before 14 or 15.
- P1 breaches at test time (a truncation, cut0 or stop where calibration had none, or a run that does not truncate where 256/256 did):
  scored as observed under every rule, listed by seed.
- test: first rung passing everything; runs-<C>.jsonl keyed (seed, arm, design, side, replicate); resumable (torn tail cut).
  score: every rule; A1 (i)-(iii); B1-B3 per (F-rule, K-rule) pair; determinism rerun of seed 101/301's telling runs on the truth's
  truncating design (A1 e1; B e2, e3 on the size rung), byte compare, a mismatch → VOID, exit 15.
- Records in D0 and score: F, D (score), the freeze chain and every D0 commit per interval F_j..F_{j+1} (score), sha256 of criteria.md,
  controls.py, uv.lock and runs (score), $WT HEAD, numpy/scipy versions. JSON: json.dumps(sort_keys=True, separators=(",", ":"),
  ensure_ascii=True, allow_nan=False) + "\n".
- Ledger: cpu-total.txt (created "0.0" by the coordinator; missing or unparseable → refuse). Under fcntl.flock(cpu-total.lock) add
  the getrusage(SELF)+getrusage(CHILDREN) user+sys delta, write tmp, os.replace; append the audit line (phase, control, unit,
  delta, total, pid, UTC time) to cpu-ledger.csv, never re-read. After every run, at least every 30 CPU s in loops, at exit.
  Read before each step; total ≥ ceiling → write STOP; on STOP exit 11. score is charged but exempt. Only the coordinator removes STOP.
- parity/: copies of the fsd-trunc lines and truths for dev-002/005/006/015 and candidates.json's picks, source sha256s in a manifest; K0-K4: 0 mismatches.
- test_controls.py (output in test_controls-out.txt; it and the pre-F ctl-det determinism run (2 runs, cmp) call internal functions,
  not the CLI): every rule on stub tables; exclude-all for every arm including telling; cut0 and non-finite; rung reading, P1 breaches and
  every exit status; each gate refusal (env vars, origin/v2 ancestry, freeze chain, test-data rule) in a scratch git repo with a bare
  "origin"; ledger (no lost update with 2 processes, loops charged, missing total refused, STOP fires); canonical JSON bytes; X never
  reaching FSDPolicy, the tool layer or an agent record.
- Sequence: (1) coordinator commits criteria v7; (2) code (never commit controls.py) → tests pass; (3) code red team vs v7;
  (4) F (git add -f; both hashes in its message) → push → LEDGER.csv row "fsd-controls freeze <F>" (7.6 CPU h, $0) → cpu-total.txt = 0.0;
  (5) calibrate → commit and push D (D0 only) → test → score. A change after F needs a new criteria version, a red team and F′ in the chain.

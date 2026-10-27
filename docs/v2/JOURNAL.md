# Journal

Append-only. Each entry: what was done, what was learned, what comes next.
Decisions themselves go in `docs/v2/LOG.md`; the work queue is `docs/v2/QUEUE.md`.

## 2026-10-27: dm-devbox becomes the platform; the unattended lane is set up

**Done.**
- Recorded the owner's four decisions of today in LOG: dm-devbox is the
  reference platform, the pilot is rerun from scratch on Linux, the $150
  monthly cap (with the recommendation on where the baselines run), and the
  unattended lane on `v2`.
- Platform check on `v2` at ea055be: `uv sync`; mypy clean (214 files); ruff
  format and check clean. The fast tier first gave 12 failures, all the six
  fit/4 reference digests (Windows bytes). On dm-devbox fit/4 (b5e2b87) and
  fit/5 give the same digests for all six, so they were re-pinned; the fast
  tier is now 2476 passed, 3 skipped (the QTM catalogue is not fetched).
- Determinism child (`tests/acceptance/determinism_child.py`) run twice,
  under PYTHONHASHSEED 0 and random: 36 lines, byte-identical (sha256
  `91ccdcef…`).
- Regenerated the dev split from the fit/4 code on 2 workers in 31 minutes:
  the same 20 truths as PILOT.md's table, new bytes (sha256 `604acec5…`),
  now at `data/v2/truths/dev`.
- Wrote CLAUDE.md's platform, money and lane sections, AUTOPILOT.md, this
  journal, QUEUE.md, LEDGER.csv and the five project agents.

**Learned.**
- Fit output bytes are per machine class: on dm-devbox they change with
  numpy's AVX-512 dispatch and with the OpenBLAS kernel, and no setting tried
  reproduces the Windows bytes. An AWS instance must pass a byte check before
  its cells share a store with dm-devbox's.
- Docker is not installed on dm-devbox, so the AG-o sandbox cannot run. The
  sandbox runs as uid 65534 while the host creates `work/` as uid 1000, mode
  0755; on native Linux Docker writes to `/work` will probably fail. Docker
  Desktop on Windows hid this. Unverified until Docker exists.
- The Windows PC held work that is not on GitHub: the `p4-deltas` branch, the
  pilot and mini-pilot stores, the defect-hunt scripts and the `p3-fit4` tag.
- The truth sampler ran 1,882 s on 2 workers here; the Windows run took 49
  minutes on 4 workers. A first hint that dm-devbox is faster per worker; the
  mini-pilot will measure the pilot's units.

**Next.** The owner's OK to install Docker; then the sandbox fix (test
first), the slow tier, the rebuilt defect-hunt checks, the smoke and the
mini-pilot gate. Ask the owner about the `p4-deltas` branch.

## 2026-10-27: Lane session 1 (10:46 UTC): sandbox, defect-hunt checks, identifiability (in progress)

**Done.**
- Oriented on `v2` at fc89c4f (clean, up to date). No sciagent runs in tmux; no `.cache/`
  stores in the lane checkout. `free -g`: 11 GB available.
- The lane checkout (`~/lanes/sciagent`) had no dev split. Copied it from the owner's checkout
  (`~/research/sciagent/data/v2/truths/dev`, git-ignored) after checking `truths.json` has the
  logged sha256 `604acec5…`; the copy hashes the same.
- Rootless Docker answers on `unix:///run/user/1000/docker.sock` (server 29.9.2).
- Delegated three queue items to engineer agents on disjoint files, due back 12:00 UTC: the
  sandbox on rootless Docker (src/sciagent/sandbox, tests/sandbox), the rebuilt defect-hunt
  checks (new scripts and tests), and the fit/5 identifiability recheck of the 20 dev truths
  (a new script, run nice'd on at most 2 workers).

**Learned.**
- Lane checkouts do not share git-ignored data with `~/research/sciagent`; each needs its own
  copy of the dev split, checked by sha256.

**Next.** Verify and record what the three agents return, in a further entry this session.

## 2026-10-27: Sandbox works on rootless Docker; defect-hunt checks rebuilt; dev truths identifiable under fit/5

**Done.**
- Sandbox (engineer, test first): on unchanged code `nice uv run pytest tests/sandbox -m slow`
  gave 14 passed, 4 failed (`PermissionError` on `/work`): uid 65534 maps to host uid 165533
  under rootless Docker. Fixed with an ownership handoff around each call (LOG 2026-10-27);
  the agent container's flags are unchanged. My rerun: `19 passed, 45 deselected`, no leftover
  containers.
- Red-team review of the sandbox change: commit with follow-ups. Medium: the back handoff can
  run before the agent container is confirmed gone (a failed `docker rm -f` is ignored), and on
  some paths (forward handoff failing, session hard-timeout kill, back handoff failing) it does
  not run, which leaves `work/` unremovable so `investigation/runner.py:364` raises and the
  run's record is lost. Low: an agent writing millions of files can make a handoff time out,
  which voids the run; agent-set modes and ACLs survive; platform mis-detection fails quietly;
  one untyped `TimeoutExpired`; fault paths untested. No escape outside `work/` found; two
  fresh sandboxes gave identical digests. All queued as one item before AG-o reported numbers.
- Defect-hunt checks rebuilt as `sciagent.campaign.audit` with `scripts/hunt_gap.py`,
  `store_check.py`, `store_compare.py` and `report_determinism.py`; 43 tests (my rerun: `43
  passed`), and the engineer's mutation pass caught 12 of 12 injected defects. Run only on
  synthetic and fake-job driver stores so far. Committed as 5a71d29.
- Identifiability recheck (`scripts/recheck_identifiable.py`): fit/4 control reproduces all 20
  records exactly; fit/5 20/20 pass, none changes status, minimum margin 0.0280 (dev-002).
  fit/5 run 398 s on 2 workers. Slow test: `1 passed in 78.14s`.
- Checks on the combined tree: ruff format and check clean, `mypy` no issues in 225 files,
  fast tier `2555 passed, 3 skipped` (the QTM skips).

**Learned.**
- Rootless Docker changes the sandbox's uid story completely; Docker Desktop had hidden it.
- `campaign/llm.py:221` puts `wall_s` in the session cell, so session cells are not
  byte-identical across runs; `store_compare` compares baseline and score cells by default.
- The ψ-recovered flag reads a period swap in `Product(Periodic, Gate(Periodic, ·))` as a miss
  (dev-011); ORACLE ψ checks must compare up to such symmetries.
- Lane checkouts need their own copy of git-ignored data, checked by sha256.

**Next.** The full slow tier was started in tmux `sci-slow` at the end of this session (log in
`.cache/slow/`): read its tail first and root-cause any failure. Then the sandbox follow-ups
(engineer, test first, in a worktree if the slow tier is still running), then the smoke. The
follow-ups come before any AG-o run that produces reported numbers.

## 2026-10-27: Lane session 2 (11:41 UTC): slow tier green, hunt checks cross-checked, two proposals

**Done.**
- Oriented on `v2` at 4a6d780 (clean). The slow tier from session 1 was running in tmux
  `sci-slow`. `free -g`: 8 GB available. `docs/v2/INBOX.md` (new, from the owner) has no entries.
- **Slow tier on dm-devbox, green:** `273 passed, 24 warnings in 5716.76s (1:35:16)`, exit 0,
  at 4a6d780 (`.cache/slow/run.log`). It is slow because the machine is shared with
  agent-control's jobs.
- **Owner pushes during the session:** `origin/p4-deltas` (WP8-WP12 committed; slow tier
  never run), `origin/windows-salvage` (not merged into `v2`; its `salvage/` `.py` files would
  enter ruff and pytest), and three `v2` commits (INBOX, AUTOPILOT reads INBOX first, queue
  items). I read HANDOFF-2026-10-09 before any smoke. It adds open decision 6 (C5 random-arm
  seeding), now in the veto-rule decisions item. My first push hit a queue conflict with the
  owner's commits; I resolved it on the owner's version and re-applied only my items.
- **Session wall time:** proposal in LOG 2026-10-27 (veto window ends 2026-10-28T12:00Z). My
  draft said "leave it in the cell". The red team rejected that, and I checked its two points:
  - the record's timing section is not hashed (`harness/record.py:20-22`);
  - under the scripted driver, `wall_s` is the only difference between two runs' session cells
    (`tests/campaign/test_audit_driver.py:85-98`).

  The proposal is now to move `wall_s`, `duration_api_ms` and `total_cost_usd` to
  `timing.jsonl`, before the full pilot.
- **Cross-check of the rebuilt hunt checks** against the salvaged originals (engineer, test
  first; 7406810, merged). The rebuild had covered only §6 of the original `store_check.py`.
  - **Ported:** `check_budgets` (budget accounting per record, an instrument),
    `check_sessions` (every stored session found by the rescore's enumeration;
    `scripts/session_check.py`), `KEY_FIELDS`, and p3_gap's beats-truth flag
    (`hunt_gap.py --split`).
  - **Failing first:** import errors, then stub runs failing on each condition.
  - **Positive control** on dev-008 seed 1: truth reference −0.59890 per event, ORACLE
    −0.0007 below it.
  - **Gate change:** the engineer made beats-truth by more than +0.01 *fail* the gate. That
    changes a gate fixed in HANDOFF-2026-10-06, so I reworded it as a proposal (veto window
    ends 2026-10-28T13:00Z). Its red-team review stalled after 10 minutes without a verdict
    (its only note: held-out logs hold about 1,800 to 2,330 events), so the review must be
    rerun before adoption.
  - **Not ported** as one-off migration probes: p3b_refit, inf_scan, cache_bound_check, the
    fit/4 equality parts.
  - **Left, queued:** store-level replay, peak memory on Linux, rescore_guard extras, the
    hash-seed pair.
- **Checks on the merged tree:**
  - ruff format and check clean; `mypy` no issues in 227 files.
  - Fast tier in the engineer's worktree (same content): exit 0, 2,593 passed and 3 skipped
    (the QTM tests), counted from the progress marks. The `-q` in addopts plus my `-q`
    suppressed the summary line.
  - Audit slow tests on `v2`: `3 passed`.
- **Sandbox follow-ups** (engineer, worktree `.claude/worktrees/agent-ab762e5af9832a80f`,
  branch `worktree-agent-ab762e5af9832a80f`, based on 4a6d780): still in progress at hand-over;
  asked to commit a checkpoint. State below.

**Learned.**
- Don't add `-q` to pytest here: addopts already has it, and `-qq` drops the count line.
- The research-team guard blocks inline scripts that write under `.claude/` paths, which
  includes agent worktrees. Use the Edit tool there.
- A red-team agent on Opus xhigh can stall. Give it a narrower question, or split the two
  questions.

**Next.**
1. Verify and merge the sandbox follow-ups branch: rebase onto `v2`, the fast tier and
   `tests/sandbox -m slow`.
2. Smoke (pilot-runner), with the dev split copied and its sha256 checked.
3. After 2026-10-28T12:00Z, unless vetoed: implement the `wall_s` move.
4. Re-run the beats-truth red team.
5. Decide how to measure peak worker memory on Linux before the mini-pilot.

**Hand-over addendum (14:05 UTC): sandbox follow-ups checkpoint.**
- The engineer committed aea2b90 ("WIP") on `worktree-agent-ab762e5af9832a80f`, based on
  4a6d780. It is not merged into `v2`.
- **What it reports:** all seven items implemented, each with a test seen failing first.
  - ruff and mypy clean (230 files).
  - Fast tier `2629 passed, 3 skipped`, run before two late small changes (`Sandbox.recover()`
    is now a no-op unless `work/` is still handed away; a misplaced logger line).
  - `tests/sandbox -m slow -n 1`: `24 passed`, also run before those changes.
- **Owed before merge:**
  - re-run the slow sandbox tier and the AG-o replay test after the late changes;
  - finish `tests/campaign/test_campaign_slow.py` (log `.cache/slow_campaign.log` in the
    worktree);
  - `test_investigation_event_cap.py::test_an_adversarial_design_on_a_heavy_truth_stops_within_minutes`
    took 259 s against its 180 s limit at load ~8. It calls none of the changed code, so it
    needs a re-run on a quiet machine.
- **Design choice to log and red-team before merging (item 3):**
  - Past `SandboxLimits.max_work_entries` (100,000) or `max_work_path_bytes` (1,024), the back
    handoff empties `work/`, tells the agent in stderr, and the run is *scored*, not voided.
    The reasoning: agent resource use is a result, as with the file-size limit; voids are for
    out-of-bounds access and infrastructure faults (SPEC §9, the voids policy).
  - The handoff timeout becomes 10 × `wall_seconds` with a 300 s floor. The engineer measured
    1.7 M files deleted in 163 s under load.
  - The engineer flags that the existing `hard_timeout` void is also conditioned on agent
    behaviour, and left it unchanged.
- **Also unrequested:**
  - set-uid and set-gid bits cleared;
  - the ACL strip is a stdlib Python helper (`sandbox/_handoff_helper.py`), so the image tag
    does not move;
  - a sandbox with a failed back handoff refuses further calls;
  - cleanup failure voids with `infrastructure: sandbox cleanup: <ErrorType>`;
  - each isolated AG-o session gets its own sandbox root.
- **Next, first:** red-team aea2b90 and the item-3 choice, write the LOG entry, run the owed
  tests, then rebase onto `v2` and merge.

## 2026-10-27: Lane session 3 (14:04 UTC): sandbox follow-ups merged, beats-truth gate reviewed, smoke running

**Done.**
- Oriented on `v2` at ef16530 (clean, up to date). INBOX has no entries. `free -g`: 10 GB
  available; load about 6 (agent-control's `calib` and `claude` tmux sessions).
- **Peak worker memory on Linux** (hunt leftover b): `scripts/mem_sampler.py` with
  `tests/campaign/test_mem_sampler.py` (6 tests, all seen failing first, then `6 passed`). It
  records `VmRSS` and `VmHWM` per process under the pilot's pid every 5 s (`VmHWM` is the
  kernel's own peak), marks spawn-pool workers, and writes the store's `STOP` below 2 GB
  `MemAvailable`; `--summary` prints peak worker `VmHWM` × workers. Committed 4f57852.
- **Owed tests for the sandbox follow-ups (aea2b90):** `tests/sandbox tests/investigation
  tests/campaign -m slow -n 2` gave `1 failed, 31 passed in 1854.30s`. The failure was the dev
  split missing from the worktree (`test_report_determinism…`, `FileNotFoundError` on
  `data/v2/truths/dev/truths.json`), not the change. The event-cap and AG-o replay tests passed.
- **Red team of aea2b90:** merge with follow-ups; no lost-record path, no isolation regression.
  Two medium findings: an agent-built deep directory chain could exhaust the 256 MB cleanup
  helper (an agent-caused void), and the sandbox limits and helper code were not in the record.
- **Engineer on the findings (a8fc7cc):** the deep-chain void was real (GNU `find -delete`
  still running after 10.5 min on a 910,000-level chain). New breadth-first, flat-memory
  emptying (278 to 390 s for that chain); handoff timeout 20× wall time; AG-o records carry
  every `SandboxLimits` field and the helper sha256; typed errors; symlinked roots;
  process-group kill; four test gaps pinned. Merged into `v2` as a0129ab (aea2b90 rebased plus
  a8fc7cc). I read the new `empty()`: rename-into-queue, constant open descriptors.
- **Checks on the merged tree:** ruff format and check clean; `mypy` no issues in 235 files;
  fast tier `2704 passed, 3 skipped` (QTM); affected slow tiers `1 failed, 37 passed in
  1028.87s`. The one failure is environmental (Learned); that test passes in an unconfined
  shell (`1 passed`).
- **Beats-truth gate red team (rerun, narrower question):** adopt at +0.01. An honest fit beats
  the truth by c nats with probability at most e^-c, so at most 2e-8 per unit at the smallest
  dev log (1,767 events). ORACLE sat 0.00003 to 0.0045 nats/event below the truth on seven dev
  truths. Its conditions, done by an engineer (merged 4677b57): each unit's `truth_digest` must
  match the split's; `--split` is required; a slow positive control on dev-006 (ORACLE 0.00033
  below the truth). Merged tree: fast tier `2610 passed, 3 skipped`, audit slow `4 passed`.
- **LOG:** four entries. The scored-not-voided choice for agents that flood `work/` is a
  proposal (veto window ends 2026-10-28T15:00Z). The beats-truth review result corrects the
  earlier reason: the gate does not catch a full leak into a small model. Plus the
  bounded-memory emptying and the digest check.
- **Smoke launched** (pilot-runner) from the frozen worktree `.claude/worktrees/smoke`
  (detached at e3fc6f1, dev split copied, sha256 `604acec5…`), store `.cache/smoke`, tmux server
  `-L sciagent`, session `sci-smoke` (window `pilot`; window `mem` runs the sampler into
  `.cache/smoke-mem.csv`). Plan: 12 pool jobs, 8 LLM units. At 15:54 UTC: ORACLE, B-lib and B-np
  on dev-000 scored (wall 50.7, 14.6, 13.5 s), AG-c-S/named dev-000 submitted (257 s, 33 turns),
  AG-o-S/named dev-000 launched. Run log `.cache/smoke-run.log`; finished when its last line is
  `exit: <code>`.

**Learned.**
- **The default tmux server on dm-devbox is AppArmor-confined.** It was started from an SSM
  session and runs under `snap.amazon-ssm-agent.amazon-ssm-agent` (complain mode). Complain
  mode still fails a `mkdirat` deeper than PATH_MAX with ENAMETOOLONG, which broke
  `test_empty_takes_a_chain_far_deeper_than_open_files_allow` there. The lane's own shell is
  `unconfined`. Lane jobs now go in `tmux -L sciagent` (checked: `unconfined`, test passes).
  AUTOPILOT and agent-control's jobs may want the same; the owner decides.
- An agent's directory shape, not only its file count, can turn a cleanup into a void; one
  flat measurement did not bound it.
- A truth id does not identify a truth (two dev splits share the ids), so gate references
  carry the digest.
- Worktrees need their own copy of the git-ignored dev split before slow tests.

**Next.**
1. Read the smoke: `tail .cache/smoke-run.log`, `progress.log`, `timing.jsonl`; then
   `uv run python scripts/mem_sampler.py --summary .cache/smoke-mem.csv --workers 2`; replay
   one AG-o and one AG-c record offline; per-unit wall times in the journal. Look at the
   repeated `Solution may be inaccurate` warning (`glm/fit_solve.py:591`) in baseline fits.
2. After 2026-10-28T12:00Z, unless vetoed: the `wall_s` move (the sandbox follow-ups landed).
3. Session keys and the sandbox (new queue item), before AG-o cells that count.
4. The mini-pilot gate once the smoke passes, with `mem_sampler.py` beside it and
   `hunt_gap.py --split data/v2/truths/dev`.
- Left in place: the old worktree `.claude/worktrees/agent-ab762e5af9832a80f` (locked; its
  branch is merged; it holds the owed-test log) and `.claude/worktrees/smoke` (in use).


## 2026-10-27: Lane session 4 (15:57 UTC): smoke 11 of 12 jobs, replays pass, three proposals

**Done.**
- Oriented on `v2` at 5cab148 (clean). INBOX has no entries; VETO.md has no vetoes. `free -g`:
  9 GB available; load 8 to 10 all session (shared machine).
- **Smoke** (`tmux -L sciagent`, session `sci-smoke`, store `.cache/smoke`): at 19:00 UTC 23
  lines in `timing.jsonl`, every one `ok`; still running B-sym@10F dev-000, the last of 12 pool
  jobs. Wall times from `.cache/smoke-run.log` (pool times include queueing behind the other
  worker):
  - LLM sessions (named, seed 1): AG-c-S dev-000 257 s (33 turns), dev-001 109 s (28);
    AG-o-S dev-000 132 s (16), dev-001 53 s (16). Their scores: 178, 100, 209, 38 s.
  - dev-000: ORACLE+B-lib+B-np job 50.7 s, B-sym 772.6 s, B-sparse 1152.5 s, planted-hint
    3684.4 s, B-rand 6812.8 s.
  - dev-001: ORACLE+B-lib+B-np 72.2 s, B-rand 1153.1 s, B-sym 124.3 s, B-sparse 239.0 s,
    planted-hint 738.6 s, B-sym@10F 2083.6 s.
  - B-rand on dev-000 (1.9 h) and planted-hint dominate; they matter for "Where the baselines
    run". Only 4 LLM sessions ran (`--llm-limit 4`; named only).
- **Offline replays pass** (smoke worktree's code, `/tmp/smoke-replay/replay_smoke.py`, log
  `.cache/smoke-replay.log`): AG-o-S/named dev-001 (`6afaace3…`) `PASS steps=15
  budgets={experiments: 0, fits: 10, submit: 1} 31.6s`; AG-c-S/named dev-001 (`93051fa9…`)
  `PASS steps=27 budgets={experiments: 0, fits: 20, submit: 1} 83.6s`; `exit: 0`.
- **Session keys and the sandbox** (engineer, test first): AG-o session text now names every
  `SandboxLimits` field, the handoff helper sha256 and the image tag; AG-c digests pinned
  unchanged. On local branch `worktree-agent-a501a8588ae2e77cb` (2dbd125, worktree locked), not
  merged: it is a veto-rule proposal (LOG, window ends 2026-10-28T17:00Z). I read the src diff.
  Engineer's checks on the branch: ruff clean, mypy 236 files clean, fast tier `2731 passed, 3
  skipped`, campaign slow `10 passed`, event-cap `1 passed`; 26 new tests seen failing first.
- **Solver warning** (engineer, investigation only): `Solution may be inaccurate` is Clarabel
  `optimal_inaccurate` on identity-link cutting-plane subproblems, certified by the code's own
  gap; cost at most 8.4e-7 nats per held-out event. Not from ORACLE/B-lib/B-np (0 Clarabel calls
  on dev-000); reproduced in B-rand. Decision in LOG: stays accepted; record it at the next
  `FIT_VERSION` bump. I checked `ACCEPTED_STATUS` (`glm/fit_solve.py:133`) and the repro logs.
- **Decision 6, C5 random-arm seeding** (veto rule): recommend keying every policy stream by
  truth id through an opaque framework-side seed; red team: adopt with changes (all seven policy
  arms move, address text must say so, not a variance argument), folded in. LOG, window ends
  2026-10-28T16:05Z. I checked three of its cited lines on `origin/p4-deltas`.
- Owner pushes for both proposals were not delivered (Remote Control inactive).
- Checks on `v2` (docs only changed here): ruff format and check clean, `mypy` no issues in
  235 files. The fast tier was not rerun on `v2`: no code changed on it.

**Learned.**
- `cd` into a worktree moves this session's working directory; use absolute paths instead.
- The solver warning's position in the log misattributes it: warnings print once per process
  per line, beside whatever job logs next. Its count means nothing.
- A smoke over two truths takes over 3 hours on this loaded machine, mostly B-rand and
  planted-hint. The mini-pilot's per-unit times will decide where the baselines run.

**Next.**
1. Read the smoke: `tail .cache/smoke-run.log` (last line `exit: <code>`), then
   `uv run python scripts/mem_sampler.py --summary .cache/smoke-mem.csv --workers 2`; mark the
   smoke item done if B-sym@10F dev-000 is `ok`; copy the store to S3.
2. After 2026-10-28T17:00Z, unless vetoed: rebase 2dbd125 onto `v2`, fast tier and
   `tests/campaign -m slow`, merge (session keys).
3. After 2026-10-28T12:00Z: the `wall_s` move (engineer, test first).
4. Mini-pilot gate (pilot-runner) with `mem_sampler.py` beside it.


## 2026-10-27: Lane session 5 (19:03 UTC): checkpoint (smoke's last job running, mini-pilot launched)

**Done so far.**
- Oriented on `v2` at 4cd2564 (clean, up to date). INBOX has no entries; VETO.md has no vetoes.
  `free -g`: 8 GB available.
- **Smoke:** still on its last pool job, B-sym@10F dev-000. From the job durations in
  `.cache/smoke-run.log`, worker 1 has run it since about 17:33Z; B-sym on dev-000 took 6.2× as
  long as on dev-001 (772.6 s against 124.3 s), and B-sym@10F dev-001 took 2083.6 s.
- **Mini-pilot gate launched** (pilot-runner): frozen worktree `.claude/worktrees/mini` at
  4cd2564 (no src/ or scripts/ commit since the smoke's e3fc6f1). `tmux -L sciagent` session
  `sci-mini`. Run A goes into `.cache/mini-a` with `--ids dev-008,dev-013,dev-017 --seeds 1
  --no-llm --systems ORACLE,B-lib,B-np,B-rand,B-sym,B-sparse,planted-hint --workers 1` (log
  line at 19:06:21: `plan: 15 pool jobs, 0 LLM units`). Run B (`.cache/mini-b`, the same
  command) is armed to start when the smoke's log ends with `exit:`. That keeps at most 2
  active pool workers. Each run has `mem_sampler.py` beside it.
- In progress: a store-level replay check (`scripts/replay_check.py`, engineer, test first,
  ported from `origin/windows-salvage`), and an F5 proposal (derived address text) under
  red-team review.

**Next.** Integrate the replay check and the F5 review. Close the smoke when its last job
ends: memory summary, S3 copy, ledger.


## 2026-10-27: Lane session 5 (19:03 UTC): mini-pilot running, replay check merged, five decisions reviewed, three branches built

**Done.**
- Oriented on `v2` at 4cd2564. INBOX had no entries at the start; the owner's usage sprint
  (21:15 entry, up to 6 specialists until 2026-10-28T02:00Z) arrived mid-session and was
  followed from 21:13. VETO.md has no vetoes.
- **Smoke:** still not finished at 22:06. B-sym@10F dev-000 has run since about 17:33Z (4.5 h;
  the same job on dev-001 took 2083.6 s). Interim memory (`mem_sampler.py --summary
  .cache/smoke-mem.csv --workers 2`): peak worker VmHWM 1.923 GB, × 2 = 3.846 GB; lowest
  MemAvailable 5.461 GB (one dip, around 16:25Z, during B-sparse dev-000).
- **Mini-pilot gate launched** (pilot-runner): worktree `.claude/worktrees/mini` at 4cd2564,
  split sha256 `604acec5…` checked; `tmux -L sciagent` session `sci-mini`. Run A
  (`.cache/mini-a`, `--workers 1`, `plan: 15 pool jobs, 0 LLM units`) has dev-008 done:
  ORACLE 75.6 s, B-lib 8.4 s, B-np 6.5 s, B-rand 3829.8 s, B-sym 1081.5 s, B-sparse 1711.4 s;
  planted-hint running. Run B (`.cache/mini-b`) starts by itself when the smoke's log ends with
  `exit:` (`.cache/mini-b-wait.sh`, `.cache/mini-run.sh`); each run has a memory sampler.
- **Store-level replay check merged** (98e3aa4; engineer, test first, ported from
  `origin/windows-salvage`): `scripts/replay_check.py`. On the smoke store `4 of 4 passed,
  REPLAY OK`, store bytes unchanged. On `v2`: ruff clean, mypy 237 files clean, fast tier
  `2711 passed, 3 skipped`, its slow positive control `1 passed`. Hunt leftover (c) done.
- **F5 (hand-written addresses):** red team widened the gap (domain marks and channels in no
  address, `DATA_VERSION` hand-written, no code commit recorded); proposal: code-fingerprint
  guard before the pilot, derived address text after (LOG; window ends 2026-10-28T19:15Z).
  Guard built on local branch `fingerprint-guard`: 2998821, then a second red team (merge with
  fixes: wrong-checkout fingerprint, parent-repo "clean", concurrent drivers, git flags, crash
  safety), fixes in checkpoint 903937e (one driver per store via `flock`, `uv.lock` dropped,
  torn last line treated as uncommitted). On 903937e: ruff, mypy clean, `tests/campaign` fast
  `187 passed`, `test_fingerprint.py` `52 passed`; full fast tier and campaign slow tier NOT
  run on it (killed for memory, below).
- **Session keys** (2dbd125): red team, merge with fixes: the AG-o key still misses the
  `docker run` flags, container environment, CSV precision and tool caps (five edits left every
  key unchanged); image tag is not the image ID; a test mutation survives. All 920 non-AG-o
  addresses confirmed unchanged; merges cleanly with v2 and `fingerprint-guard`. An engineer
  is fixing it on the same branch (state below).
- **Decisions open since 2026-10-06**, each drafted by a specialist and red-teamed:
  - Δ11 EIG weights: the theorist's tie rule rejected (the η→0 limit, not EIG; undefined at
    infinite KL; a SPEC change); proposal: keep BIC, EIG to relative precision, full ties
    from the truth-keyed random draw, report the degeneracy (LOG; window 2026-10-28T21:40Z).
  - Review items (a)-(e) (LOG; window 2026-10-28T21:45Z). (c) sets a P3 rule before the
    Linux pilot: any ORACLE −inf fails C1 (it dropped the truth; one drop flipped GO to
    CONTROLS FAIL on the test table; §2.4 already demands zero underfit flags). New queue item.
  - Δ6 prompt variant: `link-neutral` not adopted as drafted (borderline data, softplus
    priming, no guard on P4 arms); link-balanced variant recommended, not yet red-teamed, no
    veto clock (LOG).
  - FSD event cap: draft's 6,000 not adopted (telling shares the constant, degenerate FSD
    picks at the cap, one replicate of evidence); measurement first (LOG).
  Drafts and probes are in `.cache/drafts/`.
- Owner pushes not delivered (Remote Control inactive); the four proposals were relayed to the
  control-room session (agent-control) for the owner.

**Learned.**
- Six specialists on a 4-vCPU, 16 GB shared machine is too many when three of them run test
  tiers: at 22:05 load was 28 and MemAvailable 2 GB (floor 6 GB, sampler brake 2 GB). I
  killed my helpers' four pytest runs (none of the pilot's processes); available went back to
  5.35 GB. In a sprint, give CPU-bound tests to at most one helper at a time and keep the
  rest to reading and drafting.
- A drafted recommendation rarely survives a red team unchanged: four of five did not. Budget
  a review for every one.
- The research-team guard blocks a whole Bash command that names a `.claude/` path in inline
  file-writing code, including a `python3` queue edit; use the Edit tool.

**Next.**
1. Smoke: read `tail .cache/smoke-run.log`; when `exit:` appears, memory summary, copy the
   store to `s3://delaymargin-results-482913/sciagent/`, add the smoke and mini-pilot lines to
   LEDGER.csv, mark the smoke done. Run B of the mini-pilot then starts by itself.
2. Branches waiting on windows (each needs the full fast tier and `tests/campaign -m slow`
   before merge, run one at a time): `fingerprint-guard` 903937e (window 2026-10-28T19:15Z);
   session keys (window 17:00Z, fixes in progress); `wall-time-move` (window 12:00Z); and
   `c1-oracle-inf` (window 21:45Z). See their entries below for their state.
3. Mini-pilot gate when both runs finish: `store_compare.py`, `hunt_gap.py --split`,
   `store_check.py`, `replay_check.py` (no LLM records), the report, memory × workers.
4. Δ6: red-team the link-balanced recommendation. FSD cap: measure the event-count
   distribution (all dev truths, 16 replicates, both sides) in tmux.

**Addendum (22:12).** All helpers have stopped and nothing of theirs is running. Branch states:
`fingerprint-guard` 903937e, session keys f1c5b0f (three fix commits on 2dbd125),
`wall-time-move` df0ce40 (fast tier `2720 passed, 3 skipped`), `c1-oracle-inf` 285485a (fast
tier had 2 unidentified failures before the kill: find them first). None has a confirmed slow
tier; details are in QUEUE and LOG. The INBOX sprint entry is marked handled.


## 2026-10-28: Lane session 6 (23:50 UTC): checkpoint (smoke closed, usage brake stalls lane-unit runs)

**Done so far.**
- Oriented on `v2` at 46fc27b (clean, up to date). INBOX: the sprint entry is handled; no new
  entries. VETO.md has no vetoes. `free -g`: 7 GB available.
- **Smoke closed.** `.cache/smoke-run.log` ends `exit: 0 at 2026-10-27T22:23:43Z`, `RunSummary`
  20 recorded (2 per system), `failed={}`, `bugs=0`, `retries=0`. Per-unit wall times (s), from
  the log's `ok` lines:

  | system | dev-000 | dev-001 |
  |---|---|---|
  | ORACLE | 50.7 | 72.2 |
  | B-lib | 14.6 | 26.0 |
  | B-np | 13.5 | 27.0 |
  | B-rand | 6812.8 | 1153.1 |
  | B-sym | 772.6 | 124.3 |
  | B-sparse | 1152.5 | 239.0 |
  | planted-hint | 3684.4 | 738.6 |
  | B-sym@10F | 17411.8 | 2083.6 |
  | AG-o-S/named | 209.0 | 37.8 |
  | AG-c-S/named | 177.8 | 99.7 |

  Memory (`mem_sampler.py --summary .cache/smoke-mem.csv --workers 2`): peak worker VmHWM
  1.923 GB, × 2 = 3.846 GB; lowest MemAvailable 2.134 GB (the 22:05 helper test runs, session 5,
  not the smoke). Store and logs copied to
  `s3://delaymargin-results-482913/sciagent/p3-linux/smoke-e3fc6f1/` (35 objects, 4,435,906
  bytes, equal to the local total); ledger lines added. Queue item marked done.
- **Usage brake stalls the mini-pilot.** `~/logs/usage/brake.log`: `freeze until 23:50 UTC:
  five-hour 99% >= 92%` at 2026-10-27T22:13:59Z, thaw 23:50:49Z. The brake runs `systemctl --user
  freeze` on `research-lane@sciagent`, and `/proc/<pid>/cgroup` puts mini-pilot run A's worker
  (684645) and run B's driver in `research-lane@sciagent.service`. Run A's memory sampler has no
  row between 22:13 and 23:50; run B's wait script logged nothing in that gap and started run B
  at 23:50:49Z, 87 min after the smoke ended. So run A's in-flight job (planted-hint dev-008,
  started 20:58:23Z) carries about 97 min of frozen time in its wall time. The smoke (same tmux
  socket, started in session 3) kept running through the freeze; why its processes were outside
  the unit is not established. New queue item (needs the owner; `~/bin` is theirs).
- In progress: an engineer is rebasing and testing the four waiting branches one tier at a
  time (no merges before their windows); an analyst is drafting the Δ6 link-balanced variant
  for its red team.

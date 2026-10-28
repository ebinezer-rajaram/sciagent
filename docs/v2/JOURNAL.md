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


## 2026-10-28: Lane session 6 (23:50 UTC): four branches verified, Δ6 goes to the owner, brake and contention distort wall times

**Done.**
- Checkpoint entry above: smoke closed (S3 copy, ledger, per-unit wall times), usage-brake
  finding and its queue item.
- **Four pilot-blocking branches verified, not merged** (engineer; windows end 2026-10-28
  12:00Z to 21:45Z). All rebased onto v2 without conflict, trees clean, no code commits needed.
  `git merge-tree --write-tree` chained wall-time-move, session keys, fingerprint-guard,
  c1-oracle-inf: clean onto v2; the merged tree is ruff and mypy clean (241 files, engineer).
  Per branch (logs under each worktree's `.cache/`):
  - `wall-time-move` 3f0274b: `tests/campaign -m slow` `12 passed, 1 warning in 1666.30s`; fast
    tier last run on df0ce40 (`2720 passed, 3 skipped`), not rerun after the docs-only rebase.
  - session keys `worktree-agent-a501a8588ae2e77cb` e9a3ce7: fast `2762 passed, 3 skipped`; slow
    `tests/campaign tests/investigation tests/sandbox` without the two longest campaign tests
    `37 passed in 780.82s` (includes the AG-o replay perturbation test and all sandbox slow
    tests, Docker on). Not run: `test_every_non_llm_system_runs_and_scores_one_real_unit` and
    `test_the_spawn_pool_runs_and_records_real_units` (a first full run hit a 60-min tool timeout
    at 36 of 39 with no failure and no summary line, so not a pass).
  - `fingerprint-guard` 1ea5056: fast `2770 passed, 3 skipped`; `tests/campaign -m slow` in two
    parts, `9 passed` and `2 passed, 1 warning`, all 11.
  - `c1-oracle-inf` 9db6d8e: the two unidentified fast-tier failures were `test_replay_check`
    tests, caused by the gitignored dev split missing from that worktree (`replay_check.py` exits
    2, the tests expect 1, and the test helper discards stderr). With the split copied in (sha256
    `604acec5…`): those tests pass (rerun by me). Report slow tests `4 passed in 34.68s`. Clean
    full fast tier (rerun by me, 02:41Z): `2715 passed, 3 skipped, 3 warnings in 150.22s`.
  The 3 skips on every branch are the QTM acceptance tests (no QTM file on the machine).
- **Δ6 link-balanced** drafted (analyst) and red-teamed: adopt with fixes, but only on the
  owner's explicit choice, since it sets aside the approved rule (A) after its numbers were seen
  (LOG 2026-10-28; correction to the softplus bound, ≥2 of 5 not ≥3). Pushed (not delivered:
  Remote Control inactive).
- **Mini-pilot** (still running, `tmux -L sciagent` `sci-mini`): run A 10 of 15 jobs (dev-008
  all 7; dev-013 ORACLE 43.3 s, B-lib 7.0 s, B-np 9.2 s). Run A's planted-hint dev-008 took
  13694.4 s, which includes the 22:13:59Z-23:50:49Z freeze (5810 s). Run B 5 of 15 (dev-008
  ORACLE 860.5 s, B-lib 88.0 s, B-np 125.8 s, B-rand 7162.5 s, B-sym 1211.7 s). The
  `timing.jsonl` digest field is equal for all 5 units both runs have; whether that field is the
  cell's content or its address is not checked here, so byte identity waits for
  `store_compare.py` at the gate.

**Learned.**
- Wall times on this shared machine are not a property of the unit. Run B's ORACLE dev-008 took
  11× run A's (860.5 s against 75.6 s; `run_s` 768.6 against 60.7), B-np 19×: the pilot runs at
  nice 10 while the other lane's tests run at nice 0 (load 9 to 18 on 4 vCPUs). Two distortions,
  then: brake freezes (the whole lane unit stops) and CPU contention. A projection of the full
  plan from these wall times can be off by an order of magnitude in either direction.
- A worktree has no `data/v2/truths/dev` (gitignored); copy it in before any test tier, or
  `test_replay_check` fails with an empty message.
- One helper running test tiers serially kept MemAvailable above 5.8 GB all session (session
  5's six parallel helpers reached 2 GB).

**Next.**
1. After each veto window (VETO.md first): merge wall-time-move (12:00Z), session keys (17:00Z;
   run its two long campaign slow tests first), fingerprint-guard (19:15Z), c1-oracle-inf
   (21:45Z), in that order, then the full fast tier and `tests/campaign -m slow` on v2.
2. Mini-pilot gate when both runs end: `store_compare.py`, `hunt_gap.py --split`,
   `store_check.py`, the report, memory × workers; wall times read with the two distortions above
   (exclude units overlapping `~/logs/usage/brake.log` freezes; compare `run_s` across runs).
3. Before projecting the full plan ("Where the baselines run"): record per-unit CPU time
   (`resource.getrusage` in the worker) beside `wall_s` in `timing.jsonl` (engineer, test first,
   in a worktree, not while a run uses it), or project from uncontended units only; say which.
4. Owner decisions waiting: Δ6 (A/B/C); the brake and pilot-unit question (QUEUE).


## 2026-10-28: Lane session 7 (02:47 UTC), checkpoint: mini-pilot interim gate checks, CPU time in progress

**Done.**
- Inbox: no open entries (the usage sprint ended 02:00Z). VETO.md: no vetoes. No veto window
  ends this session (earliest wall-time-move, 12:00Z), so no merges.
- **Mini-pilot interim gate checks** on the units finished so far (both runs still running,
  read-only, main checkout c02f819). Not the gate: run B has 5 of run A's 13 cells.
  - `store_compare.py .cache/mini-a .cache/mini-b`: `identical cells: 5; only in a: 8; only in
    b: 0; differing: 0` (exit 1 only because of the 8 cells run B has not reached). This
    settles session 6's open question for those 5: cell bytes, not just the timing digest, match.
  - `hunt_gap.py --store .cache/mini-a --env fit/5 --split data/v2/truths/dev`: `units: 13 under
    fit/5`, `gap above +0.01: 0`, `above the truth's own parameters by > +0.01: 0`,
    `oracle_underfit: 0 flagged, 0 unrecorded, of 2 ORACLE units`, `GATE PASS`. Largest gap to
    ORACLE: B-sparse dev-008 −0.0018.
  - `store_check.py`: run A `cells: 13; issues: 0; warnings: 0`, STORE OK; run B `cells: 5`,
    STORE OK.
- In progress: engineer building per-job CPU time (`cpu_s`, `cpu_children_s` from
  `resource.getrusage` deltas in the worker) in `timing.jsonl` on a new branch `cpu-time`
  stacked on `wall-time-move` (both change the same timing lines; wall-time-move merges first).
  Session keys' two long campaign slow tests running in `tmux -L sciagent` `sci-longslow`
  (nice 19, one process, log `.claude/worktrees/agent-a501a8588ae2e77cb/.cache/long-slow-s7.log`,
  started 02:49:21Z, killed by a guard if MemAvailable < 2.5 GB).


## 2026-10-28: Lane session 7 (02:47 UTC): CPU time per job built, mini-pilot interim checks pass

**Done.**
- Checkpoint entry above (interim gate checks at 02:48Z).
- **Interim checks refreshed at 03:32Z** (run A 17 cells, run B 10; both still running):
  `store_compare.py .cache/mini-a .cache/mini-b` `identical cells: 10; only in a: 7; only in b:
  0; differing: 0`; `hunt_gap.py --env fit/5 --split data/v2/truths/dev` GATE PASS on both
  stores (run A `units: 17`, `oracle_underfit: 0 flagged, 0 unrecorded, of 3 ORACLE units`; run B
  `units: 10`, 0 of 2), `gap above +0.01: 0` and `above the truth's own parameters by > +0.01: 0`
  on both; `store_check.py` STORE OK on both (17 and 10 cells, 0 issues). Still not the gate.
- **CPU time per job** (engineer, test first): local branch `cpu-time` 21e7947 in worktree
  `.claude/worktrees/cpu-time`, stacked on `wall-time-move` 3f0274b, not pushed or merged. New
  `campaign/cpu.py`; each `timing.jsonl` line gets `cpu_s` and `cpu_children_s` (`getrusage`
  deltas in the worker over the unit's `wall_s` interval) and the whole job's `job_wall_s`,
  `job_cpu_s`, `job_cpu_children_s`, `job_units`; `None` where not measured, never 0.
  `pilot_report.py` shows CPU beside wall time and reads old lines as wall-only (checked
  read-only on both mini-pilot stores, sha256 unchanged). Follow-up done: `_check` passes stderr
  through, `replay_check.py` names a missing dev split. What it covers and does not (unreaped
  children, container processes) is in LOG 2026-10-28.
  Evidence: tests first failed (`ImportError` on the missing module; the replay test's stderr
  said only `no truths.json`); positive control (copying `job_cpu_s` into a detail) failed both
  determinism tests, then reverted. Engineer: ruff, mypy (240 files) clean; fast `2742 passed, 3
  skipped, 3 warnings in 195.89s`; slow `test_campaign_slow.py test_replay_check.py` without
  `test_every_non_llm_system_runs_and_scores_one_real_unit` `7 passed in 1047.71s` (includes the
  spawn-pool test with its new `cpu_s <= wall_s*1.05 + 0.2` check, which would fail on absolute
  figures). My rerun in the worktree: ruff `251 files already formatted`, `All checks passed!`,
  mypy `Success: no issues found in 240 source files`, `test_cpu_time.py test_replay_check.py -m
  "not slow"` `17 passed in 13.76s`. I read `cpu.py` and the driver and execute diffs.
- **Session keys' two long slow tests** (`sci-longslow`, e9a3ce7): both passed,
  `..  [100%]`, `exit 0 2026-10-28T03:36:35Z` (47 min at nice 19, one process). No count line:
  my `-q` on top of addopts' `-q` suppressed it (session 2's lesson, repeated); exit 0 with two
  marks is the evidence. Log `.claude/worktrees/agent-a501a8588ae2e77cb/.cache/long-slow-s7.log`.
  Session keys now has every campaign slow test passed on e9a3ce7.

**Learned.**
- CPU figures cannot be added to the running mini-pilot (frozen at 4cd2564), so its projection
  still has wall times only: the "Where the baselines run" item needs either one uncontended
  rerun of the slow units on the `cpu-time` code or wall times from units that overlap no brake
  freeze, with the contention caveat.
- The research-team guard rejects a Bash heredoc that mentions the agent CLI by name (it reads it
  as a nested session); write such text with the Edit tool.

**Next.**
1. After each veto window (VETO.md first): merge wall-time-move (12:00Z), then `cpu-time` on top
   (rebase onto v2 first; run `test_every_non_llm_system_runs_and_scores_one_real_unit`), session
   keys (17:00Z; all its tests now run), fingerprint-guard (19:15Z), c1-oracle-inf (21:45Z); then the full fast tier and
   `tests/campaign -m slow` on v2.
2. Mini-pilot gate when both runs end (run A 17 of 21 cells, run B 10, at 03:32Z): the full
   check list in session 6's Next 2, plus `mem_sampler.py --summary` for both runs.
3. "Where the baselines run": decide its timing source as in Learned, before the full pilot.
4. Owner decisions waiting: Δ6 (A/B/C); the brake and pilot-unit question (QUEUE).


## 2026-10-28: Lane session 8 (04:59 UTC), checkpoint: mini-pilot run A complete, CPU-timed rerun started

**Done.**
- Inbox: no open entries. VETO.md: no vetoes. No veto window ends before this session's
  deadline (08:39Z; earliest wall-time-move 12:00Z), so no merges into v2.
- **Mini-pilot run A finished** 2026-10-28T04:59:30Z, exit 0: `recorded={'B-lib': 3, 'B-np': 3,
  'B-rand': 3, 'B-sparse': 3, 'B-sym': 3, 'ORACLE': 3, 'planted-hint': 3}, failed={}, bugs=0`.
  Gate checks on run A (main checkout f570546):
  - `hunt_gap.py --store .cache/mini-a --env fit/5 --split data/v2/truths/dev`: `units: 21 under
    fit/5`, `gap above +0.01: 0`, `above the truth's own parameters by > +0.01: 0`,
    `oracle_underfit: 0 flagged, 0 unrecorded, of 3 ORACLE units`, `GATE PASS`.
  - `store_check.py --store .cache/mini-a`: `cells: 21; issues: 0; warnings: 0`, STORE OK.
  - `mem_sampler.py --summary .cache/mini-a-mem.csv --workers 2`: `peak worker VmHWM 2.050 GB`,
    `x 2 workers = 4.100 GB` (criterion ≤ 6 GB), `lowest MemAvailable 2.124 GB` (machine-wide).
  - `pilot_report.py` renders (exit 0, to `/tmp/sci-s8/PILOT-mini-a.md`, outside the repo). It
    says INTERIM/incomplete only because its planned set still counts B-sym@10F and the four LLM
    arms, which the mini-pilot plan excludes (`--no-llm`, `--systems`); not a gate failure.
  - Wall time per unit from the report (median / max s): B-lib 8/16, B-np 9/16, ORACLE 76/116,
    B-sym 536/1082, B-sparse 714/1711, B-rand 3830/5696, planted-hint 2978/13694 (the max
    includes the 5810 s brake freeze). Contended, so not usable for the projection as is.
- **CPU-timed rerun of the mini-pilot plan** started 05:02:39Z in `tmux -L sciagent`
  `sci-minicpu`: frozen worktree `.claude/worktrees/mini-cpu` at `cpu-time` 21e7947 (dev split
  copied in, sha256 `604acec5…`), store `.cache/mini-cpu`, `--workers 1` while run B still uses
  one worker, nice 10, memory sampler beside it; launcher `.cache/mini-cpu-run.sh <workers>`
  (resume by rerunning). Purpose: per-unit `cpu_s` for the "Where the baselines run" projection
  (CPU time does not accrue while frozen or descheduled), and a third copy of the baseline
  cells, which `cpu-time` must not move (`store_compare.py` against run A).
- In progress: engineer rehearsing the five pending merges (wall-time-move, cpu-time, session
  keys, fingerprint-guard, c1-oracle-inf) on a local branch `merge-rehearsal`, never v2, fast
  tier on the combined tree.


## 2026-10-28: Lane session 8 (04:59 UTC): mini-pilot run A passes its gate criteria, merge rehearsal green, CPU-timed rerun running

**Done.**
- Checkpoint entry above (run A gate checks at 05:00Z; CPU-timed rerun launched 05:02:39Z).
- **Merge rehearsal** (engineer, checked by me): local branch `merge-rehearsal` 9dcb2cd in
  `.claude/worktrees/merge-rehearsal`, from v2 f570546, five `--no-ff` merges in the planned
  order (wall-time-move, cpu-time, session keys, fingerprint-guard, c1-oracle-inf). Only
  conflict: the `campaign/driver.py` module docstring (cpu-time's "Times" paragraph against
  fingerprint-guard's "One driver per store"), resolved by keeping both; I read the resolution
  (`git show 0283ebc --cc`). Engineer: ruff `254 files already formatted`, `All checks passed!`;
  mypy `Success: no issues found in 243 source files`; fast `2856 passed, 3 skipped, 3 warnings in
  187.78s`. My rerun in the worktree: mypy `Success: no issues found in 243 source files`, ruff
  `All checks passed!`. Slow tier not run on the combined tree. Nothing merged into v2 (windows).
- **Mini-pilot run B interim** at 07:50Z (20 of 21 cells; planted-hint dev-017 still running):
  `store_compare.py .cache/mini-a .cache/mini-b` `identical cells: 20; only in a: 1; only in b:
  0; differing: 0`; `hunt_gap.py --store .cache/mini-b --env fit/5 --split data/v2/truths/dev`
  `units: 20`, `gap above +0.01: 0`, `oracle_underfit: 0 flagged, 0 unrecorded, of 3 ORACLE
  units`, GATE PASS; `store_check.py` STORE OK.
- **CPU-timed rerun** (`.cache/mini-cpu`, 21e7947) at 07:50Z, 5 cells: `store_compare.py
  .cache/mini-a .cache/mini-cpu` `identical cells: 5 ... differing: 0` (cpu-time moves no
  baseline cell, as designed). wall_s against cpu_s (cpu_children_s 0.0 in each): ORACLE dev-008
  106.4 / 77.6, B-lib 8.8 / 7.6, B-np 24.7 / 11.4, B-rand 5210.3 / 2675.7, B-sym 4077.3 / 1071.7.
- Decision: the projection uses CPU time with the wall/CPU ratio beside it (LOG 2026-10-28).
- No code on a tracked path changed this session (docs only), so no test tier was run on v2.

**Learned.**
- Contention on dm-devbox this morning (load 5 to 8 on 4 vCPUs) cost B-sym 3.8× and B-rand 1.9×
  in wall time over CPU time. Session 6's 10× swings were wall time; CPU time is the stable figure.
- The three mini truths are all far stratum, so a projection from them alone may overstate the
  full plan's near and mid truths; the next session should say so with the number.
- A Bash background wait is capped at 2 hours; one watcher was killed at its limit (harmless:
  a second covered it).

**Next.**
1. Mini-pilot gate: when run B exits (`.cache/mini-b-run.log` `exit:` line), rerun
   `store_compare.py` (want `identical cells: 21`), `hunt_gap.py` and `store_check.py` on run B,
   `mem_sampler.py --summary .cache/mini-b-mem.csv --workers 2`; record each criterion pass or
   fail, then copy both stores and logs to `s3://delaymargin-results-482913/sciagent/p3-linux/`.
2. Then put the CPU-timed rerun on 2 workers: `touch .cache/mini-cpu/STOP`, wait for the unit in
   flight, delete STOP, `tmux -L sciagent new-session -d -s sci-minicpu
   ".cache/mini-cpu-run.sh 2"` (absolute path). When it finishes, project the full plan from its
   CPU times (QUEUE "Where the baselines run").
3. After each veto window (VETO.md first): merge in the rehearsed order, from wall-time-move at
   12:00Z; `merge-rehearsal` 9dcb2cd shows the resolution of the one conflict. Then
   `tests/campaign -m slow` and the full fast tier on v2.
4. Owner decisions waiting: Δ6 (A/B/C); the brake and pilot-unit question (QUEUE).

## 2026-10-28: Lane session 9 (07:54 UTC), checkpoint: rescore guard test built, run B on its last unit

**Done.**
- Inbox: no open entries. VETO.md: no vetoes. The earliest veto window (wall-time-move) ends
  12:00Z, after this session's deadline (11:34Z), so no merges into v2.
- **Rescore guard test** (queue: hunt-check leftovers (d); engineer, checked by me; LOG
  2026-10-28): new file `tests/campaign/test_rescore_guard.py` only, local branch `rescore-guard`
  fcc45df (from v2 e8711a7). My run there: `10 passed in 26.16s`. On `merge-rehearsal` it failed
  (`TypeError: Campaign.__init__() missing 1 required keyword-only argument: 'fingerprint'`, from
  fingerprint-guard), so I adapted it on local branch `guard-on-rehearsal` 5ef1db7 (one import,
  `fingerprint=FINGERPRINT`): `10 passed in 66.55s`, ruff clean, mypy `Success: no issues found in
  244 source files`. Engineer's checks on fcc45df: ruff and mypy (238 files) clean; `tests/campaign
  -m "not slow" -n 1` `2 failed, 142 passed`, the two being `test_replay_check` on the worktree's
  missing dev split (known, QUEUE CPU-time item). Positive control: with the driver's rescore
  refusal broken, `assert calls == []` failed (`['driver.run_...ion_isolated']`).
- **Slow tier on the combined tree** started 07:55:10Z: `tests/campaign tests/investigation
  tests/sandbox -m slow -n 1` in `.claude/worktrees/merge-rehearsal` at 9dcb2cd, `tmux -L sciagent`
  `sci-slowreh`, log `.cache/slow-rehearsal/run.log` (an `exit:` line at the end). 4 tests done at
  08:51Z.
- **Mini-pilot run B** still on its last unit (planted-hint dev-017; 20 of 21 cells at 07:08:09Z).
  Its worker accrued 1 s of CPU in 20 s of wall at 08:52Z (`ps -o time`: 04:54:37 then 04:54:38):
  at nice 10 it is starved by agent-control's simulation workers, about ten processes, several at
  nice 0 (load 17.8 on 4 vCPUs at 08:32Z). Same for the CPU-timed rerun (35% CPU, 6 of 21 cells).

**Learned.**
- The format hook (ruff `--fix`) removes an import that is unused at the moment of the edit; add
  the import and its use in one edit.
- Wall/CPU on this machine can reach ~20× while agent-control runs nice-0 workers. The baselines'
  projection must quote that ratio beside the CPU total when it makes the 48-hour call.

## 2026-10-28: Lane session 9 (07:54 UTC): mini-pilot run B done and every gate criterion measured (verdict after 13:00Z), slow tier green on the combined tree, two test-first fixes on local branches

**Done.**
- Checkpoint entry above (rescore guard test; run B starved by load).
- **Mini-pilot run B** finished 2026-10-28T09:33:38Z, exit 0: `recorded={'B-lib': 3, 'B-np': 3,
  'B-rand': 3, 'B-sparse': 3, 'B-sym': 3, 'ORACLE': 3, 'planted-hint': 3}, failed={}, bugs=0`
  (planted-hint dev-017 took 8723.2 s wall, against 1495.9 s in run A). Gate checks from the main
  checkout at 61e56c9:
  - `store_compare.py .cache/mini-a .cache/mini-b`: `identical cells: 21; only in a: 0; only in b:
    0; differing: 0`, `IDENTICAL`.
  - `hunt_gap.py --store .cache/mini-b --env fit/5 --split data/v2/truths/dev`: `units: 21 under
    fit/5`, `gap above +0.01: 0`, `above the truth's own parameters by > +0.01: 0`,
    `oracle_underfit: 0 flagged, 0 unrecorded, of 3 ORACLE units`, `GATE PASS`.
  - `store_check.py --store .cache/mini-b`: `cells: 21; issues: 0; warnings: 0`, STORE OK.
  - `mem_sampler.py --summary .cache/mini-b-mem.csv --workers 2`: `peak worker VmHWM 2.045 GB`,
    `x 2 workers = 4.090 GB`, `lowest MemAvailable 4.179 GB`.
  - `pilot_report.py` renders (to `/tmp/sci-s9/PILOT-mini-b.md`, outside the repo; "verdict
    incomplete" because its planned set has the LLM arms). Wall time per unit (median / max s):
    B-lib 11/88, B-np 11/126, ORACLE 95/861, B-sym 415/1212, B-sparse 1250/2063, B-rand
    5074/7163, planted-hint 2378/8723.
- **Red team on the gate claim** (fresh context): PASS with caveats. It reran store_compare and
  hunt_gap on run A (no disagreement), `sha256sum` over both `details/` dirs (no diff), and the
  instrument tests (`47 passed`). I checked each of its points against the files:
  1. LOG 2026-10-27 (beats-truth proposal): "the mini-pilot gate is not judged before the window
     ends" (2026-10-28T13:00Z). So **no verdict is recorded this session**; the result would not
     change (0 beats-truth units, VETO.md empty).
  2. HANDOFF-2026-10-06's criterion was "a measured slowdown"; the queue restated it as "measured
     wall time per unit" in f3323f6, before the runs. The gate passes as restated.
  3. Both runs used `--workers 1` (`cmd:` lines); the x 2 memory figure is the full pilot's
     setting, not what ran, and no byte check at 2 workers exists yet. Session 8's journal and my
     first ledger lines said "2 workers"; the ledger is corrected.
  4. Run A's lowest MemAvailable was 2.124 GiB (machine-wide; the sampler's STOP brake is 2).
  5. The report's control-1 headline printed **PASS** over an empty margin table (fixed, below).
- **Stores and logs to S3**: `s3://delaymargin-results-482913/sciagent/p3-linux/mini-4cd2564/`,
  59 objects, 8.7 MB (both stores, run/mem/pid logs, `mini-run.sh`, both rendered reports).
  Ledger lines added for the copy and the two runs (9.89 h and 9.71 h, $0).
- **Slow tier on the combined tree** (`merge-rehearsal` 9dcb2cd, v2 plus the five pending
  branches): `tests/campaign tests/investigation tests/sandbox -m slow -n 1` `40 passed, 1 warning
  in 6762.88s (1:52:42)`, exit 0 09:47:56Z. The warning is cvxpy's "Solution may be inaccurate"
  from `glm/fit_solve.py:591` in `test_every_non_llm_system_runs_and_scores_one_real_unit`.
- **Control-1 label fix** (engineer, test first, checked by me): local branch `c1-label` 66f7d7d
  on `merge-rehearsal`, `scripts/pilot_report.py` plus `tests/campaign/test_pilot_report_control_1.py`.
  Six of the seven tests failed before the fix, and all 7 pass after it (my rerun: `7 passed in
  2.08s`). Engineer: `tests/campaign -m "not slow" -n 1` `272 passed`; ruff clean; mypy
  `Success: no issues found in 244 source files`. Mini-b now reads `**NOT CHECKABLE**
  (inconclusive): margin not checkable (< 2 paired finite seeds of ORACLE and B-lib) for
  ['dev-008', 'dev-013', 'dev-017']`. The label follows `analysis.verdict`. A truth without
  paired seeds does not block PASS when other truths are kept, but the label names it. This is
  the existing rule, not a new one.
- **CPU-timed rerun on 2 workers**: `.cache/mini-cpu-to2.sh` wrote STOP 09:48:24Z. The unit in
  flight ended at `exit: 0 at 09:51:27Z`, and the script relaunched `to2: relaunch with 2 workers
  (MemAvailable 10 GiB) 09:51:54Z` (`plan: 10 pool jobs, 0 LLM units, 7 units already done`).
- Nothing on a tracked code path changed on v2 this session (docs, queue, ledger only), so no
  test tier ran on v2. The checks above ran on the branches' worktrees.

**Learned.**
- At nice 10, our pilot workers get almost nothing while agent-control runs nice-0 workers.
  Run B's last unit took 5.8 times run A's wall time. This is why the projection uses CPU time.
- The gate's own records can carry a judging rule (the LOG's "not judged before the window
  ends"). Read the veto-window proposals that touch a gate before recording a verdict.

**Next.**
1. After 13:00Z, with VETO.md empty, record the mini-pilot gate verdict: PASS as restated, with
   red-team caveats 2 to 4 above. The numbers are already in this entry.
2. Merges into v2, each after its veto window and a VETO.md check, in this order:
   - wall-time-move (12:00Z);
   - cpu-time;
   - session keys (17:00Z);
   - fingerprint-guard (19:15Z);
   - the rescore guard test (cherry-pick `guard-on-rehearsal`'s two commits d7705b5 and 5ef1db7;
     the v2 version fcc45df fails after fingerprint-guard);
   - c1-oracle-inf (21:45Z);
   - c1-label 66f7d7d.
   `merge-rehearsal` shows the one conflict resolved. After the merges, run the full fast tier
   and `tests/campaign -m slow` on v2.
3. Check `.cache/mini-cpu-run.log` and the run's progress. When it finishes, run `store_compare.py
   .cache/mini-a .cache/mini-cpu` (the 2-worker byte check), then the projection (QUEUE "Where
   the baselines run").
4. Owner decisions still waiting: Δ6 (A/B/C); the brake and pilot-unit question (QUEUE).

## 2026-10-28: Lane session 10 (09:55 UTC), checkpoint: waiting on the 12:00Z and 13:00Z windows, projection script delegated

**Done.**
- Oriented: INBOX has no open entries; VETO.md has no veto lines; tree clean at 15e2e39.
- CPU-timed rerun (`.cache/mini-cpu`) relaunched on 2 workers at 09:51:54Z (`to2: relaunch with 2
  workers (MemAvailable 10 GiB)`); 11 of 21 timing lines at 09:57Z, no STOP file.
- Delegated to the engineer: `scripts/project_runtime.py` with tests on a new local branch
  `projection` (worktree `.claude/worktrees/projection`): projects the full P3 baseline plan at 2
  workers from the store's CPU times (plan taken from `pilot.py`'s planner; unmeasured systems
  such as B-sym@10F printed as not measured unless an explicit `--assume` names a factor).

**Next (this session).**
1. After 12:00Z with VETO.md empty: merge wall-time-move, then cpu-time; ruff, mypy, fast tier.
2. After 13:00Z: record the mini-pilot gate verdict (JOURNAL session 9, Next 1).
3. Check the engineer's projection branch.

## 2026-10-28: Lane session 10 (09:55 UTC): mini-pilot gate PASS recorded, wall-time-move, cpu-time and the projection script merged, interim projection above 48 hours

**Done.**
- **Mini-pilot gate verdict: PASS** as restated in the queue (f3323f6), recorded at 13:00:37Z
  after the beats-truth window (LOG 2026-10-27), with VETO.md empty. The numbers are in JOURNAL
  session 9. The caveats from the session 9 red team stand: wall times are measured but
  contended; both runs used `--workers 1`, so the memory figure (2.045 GiB x 2 = 4.09 GiB) is the
  2-worker setting, not what ran; run A's lowest MemAvailable was 2.124 GiB. The byte check at 2
  workers is not done yet (Next 1).
- **Merged into v2** at 12:00:30Z, VETO.md empty: wall-time-move (4f3adb8), cpu-time (0bb8b41),
  `projection` (39d088f). On v2: `ruff format --check` `253 files already formatted`, `ruff
  check` `All checks passed!`, mypy `Success: no issues found in 242 source files`; `pytest -m
  "not slow" -n 2` exit 0 (2753 passed, 3 skipped, counted from the progress dots: the project's
  `-q` in addopts plus mine suppressed the summary line; `.cache/s10/fast.log`); `pytest
  tests/campaign -m slow -n 2` exit 0 at 12:42:59Z (12 passed; `.cache/s10/slow.log`). Pushed.
- **Projection script** (engineer, `scripts/project_runtime.py` and
  `tests/campaign/test_project_runtime.py`, 7 tests). I reran its tests and its output in the
  worktree and got the same figures; its method choices are in LOG 2026-10-28. Interim, on 20 of
  21 cells of `.cache/mini-cpu` (planted-hint dev-017 was still running at 13:00Z):
  `unit mean: CPU 53.8 h > 48 h`, `unit max: CPU 65.1 h`, `x 2.06 wall/CPU 110.6 h`. B-sym@10F is
  not measured and is left out, so these are lower bounds; with `--assume B-sym@10F=10xB-sym`,
  61.8 h of CPU. Per-unit mean CPU: B-rand 2733 s, planted-hint 2171 s (n=2), B-sparse 862 s,
  B-sym 577 s, ORACLE 87 s. Not recorded as the answer: the last unit can move the planted-hint
  mean, and if it took 0 CPU the mean case would fall to about 47.8 h. All three truths are in the
  far stratum.

**Learned.**
- `pytest -q` on top of addopts' `-q` drops the summary line. Run the tiers without the extra `-q`,
  so the tail shows the counts.
- A background wait timer can be stopped before its target (this one stopped at 11:57Z, short
  of 12:00:30Z). Recheck the clock before acting on a veto window.

**Next.**
1. When `.cache/mini-cpu` exits: `store_compare.py .cache/mini-a .cache/mini-cpu` (the 2-worker
   byte check; want 21 identical), `hunt_gap.py` and `store_check.py` on it, then
   `project_runtime.py --store .cache/mini-cpu --workers 2 --truths 20 --seeds 1,2,3
   --b10f-truths 10`. If it is still above 48 h, that is the stop point: push the owner (longer
   dm-devbox run against the AWS m6i.xlarge option, LOG 2026-10-27), with the far-stratum caveat
   and B-sym@10F unmeasured.
2. Merges, each after its window and a VETO.md check: session keys (17:00Z), fingerprint-guard
   (19:15Z), the rescore guard test (`guard-on-rehearsal` d7705b5 and 5ef1db7), c1-oracle-inf
   (21:45Z), c1-label 66f7d7d. Then the full fast tier and `tests/campaign -m slow` on v2.
3. Owner decisions still waiting: Δ6 (A/B/C); the brake and pilot-unit question (QUEUE).

## 2026-10-28: Stop point (lane session 10, 13:05 UTC): the P3 baseline plan projects above 48 hours on dm-devbox

**Answer.** The full P3 non-LLM plan at 2 workers needs at least 53.3 h of CPU time per worker at
mean unit cost, and about 107 h of wall time at the measured contention, so it is over the
48-hour line. The owner chooses where the baselines run.

**Evidence.** `.cache/mini-cpu` (code 21e7947), finished 2026-10-28T13:00:53Z, exit 0, 21 cells,
0 failed, bugs 0. `scripts/project_runtime.py --store .cache/mini-cpu --workers 2 --truths 20
--seeds 1,2,3 --b10f-truths 10` (full output in `.cache/s10/projection.txt`):
`unit mean: CPU 53.3 h > 48 h`, `x 2.00 wall/CPU 106.8 h`; `unit max: CPU 65.1 h`, `130.3 h`.
Mean CPU per unit: B-rand 2733 s, planted-hint 2119 s, B-sparse 862 s, B-sym 577 s, ORACLE 87 s,
B-np 12 s, B-lib 11 s. B-rand and planted-hint are about 70% of the total. On the same store:
`store_compare.py .cache/mini-a .cache/mini-cpu` `identical cells: 21; ... differing: 0`
(IDENTICAL), `hunt_gap.py` GATE PASS (gap above +0.01: 0; oracle_underfit: 0), `store_check.py`
STORE OK.

**Caveats.** (1) B-sym@10F (10 units) is not measured and is left out, so every figure is a lower
bound; with an assumed 10x B-sym it is 61.4 h of CPU. (2) All three mini truths are in the far
stratum; the plan has far 9, mid 4, near 7, so near and mid units may cost less. (3) CPU time is a
lower bound on uncontended wall time; the 2.00 wall/CPU ratio is this week's contention with the
other lane at nice 10. (4) The projection does not model the driver's job bundling or
`--max-heavy`. (5) 7 of the 21 cells ran on 1 worker, before the relaunch, so the byte check at 2
workers covers the other 14. (6) This has not been red-teamed yet; the next session runs the red
team before this number is used for anything beyond this stop point.

**Recommendation.** The CPU lower bound alone is over the line, so the call does not hinge on the
contention ratio. Option 1: dm-devbox, about 4 to 5 days of wall time at this week's load, with no
new tooling. Option 2: the AWS m6i.xlarge CPU option (LOG 2026-10-27, about $9-11). It needs
sciagent launch tooling and a byte check of the instance against dm-devbox before any store is
filled there. I lean to option 2 if the owner wants the pilot inside a week, and to option 1
otherwise. The pilot cannot start yet in either case: four merges are still waiting on their
veto windows (QUEUE).

**Decision needed.** Option 1 or option 2. If option 2, also approve the AWS launch and its cost
ceiling (AUTOPILOT stop point 3).

Hand-over note (13:03Z): the push to the owner went as a desktop notification only ("Mobile
push not sent (Remote Control inactive)"). `.cache/mini-cpu`, its logs and the projection output
are copied to `s3://delaymargin-results-482913/sciagent/p3-linux/mini-cpu-21e7947/` (31 objects,
4.4 MB). The ledger has lines for the run (7.97 h) and the copy. Next session: red-team the
projection (the stop point above, caveat 6), then the merges from 17:00Z (session 10, Next 2).

## 2026-10-28: Lane session 11 (13:05 UTC), checkpoint: projection red team, strata timing run and rebase rehearsal delegated

**Done.**
- Oriented: INBOX has no open entries; VETO.md has no veto lines; tree clean at b953390; 10 GB
  available, load 4.4. No merge is due this session (the first window, session keys, ends 17:00Z;
  the session ends 16:45Z).
- Delegated, in parallel on disjoint work:
  - red team: the 48-hour projection stop point (session 10, caveat 6): plan count, CPU
    accounting with threads and subprocesses, the 2-worker makespan model;
  - pilot-runner: a CPU-timed baseline run on the strata the projection could not measure,
    `--ids dev-001,dev-010 --seeds 1 --b10f-truths 1 --no-llm` (dev-001 near, dev-010 mid,
    B-sym@10F on dev-001), store `.cache/strata`, frozen worktree `.claude/worktrees/strata` at
    b953390, tmux `-L sciagent` session `sci-strata`;
  - engineer: local branch `rehearsal2` (worktree `.claude/worktrees/rehearsal2`), the five
    waiting branches applied onto current v2 in merge order, with ruff, mypy and the fast tier.

**Next (this session).**
1. Read the three results; record the red team's verdict on the projection.
2. After 16:05Z with VETO.md empty: record C5 seeding (LOG 2026-10-27) as adopted, to be
   implemented with the p4-deltas integration.

## 2026-10-28: Stop point correction (lane session 11, 13:30 UTC): uncontended, the baseline plan sits at about 48 hours, not clearly over

**Answer.** Session 10's stop point said "the CPU lower bound alone is over the line, so the call
does not hinge on the contention ratio". That is wrong. Contended CPU time on dm-devbox is not a
lower bound. On a quiet machine the non-LLM plan is about 48 h at 2 workers, before B-sym@10F. At
this week's load it is still clearly over: 91 h at the 2-worker wall/CPU of 1.71, and 107 h at the
mixed 2.00. The owner's decision is the same, with different numbers behind it.

**Evidence.** Red team (session 11), checked by me on `.cache/mini-{a,b,cpu}/timing.jsonl`:
- 12 of 21 units had a mini-a or mini-b wall time below mini-cpu's CPU time for the same unit
  (identical bytes). Examples: ORACLE dev-017 95.4 s wall against 155.6 s CPU, planted-hint
  dev-017 1495.9 s against 2015.6 s.
- Summing each unit's minimum of its CPU and its three wall times gives 17405 s against 19205 s,
  0.906 of the CPU sum, which puts 53.3 h at about 48.3 h.
- `lscpu`: Xeon 8488C, 2 cores with 2 threads each.

Correct in the red team's report:
- the unit count (430: 420 projected plus 10 B-sym@10F; the rerun reproduces the output byte
  for byte);
- the CPU accounting (`cpu_children_s` is 0 throughout, and units run single-threaded);
- the makespan model (simulating the driver's FIFO order gives 53.58 h against 53.35 h).

Other findings:
- `--max-heavy 2` cannot bind at 2 workers.
- The LLM arms' 240 scoring jobs share the same pool and were left out of option 1's "4-5 days".
- The "$9-11" for the m6i.xlarge assumed 4 workers scaling 4x on a 2-core, 4-thread instance;
  that is untested, so option 2 is not shown to land under 48 h either.

LOG 2026-10-28 records the correction with both versions. The caveat line in
`scripts/project_runtime.py` is corrected as text (7 tests pass).

**Caveats.** Margins are thin, so the unmeasured parts decide the call. The strata run
(`.cache/strata`: dev-001 near, dev-010 mid, B-sym@10F on dev-001; started 13:07:33Z, 2 workers)
measures them, but it also runs under load. Its CPU figures carry the same bias.

**Recommendation.** Unchanged in substance. Neither option is shown under 48 h at 2 workers. If the
owner wants the pilot inside a week, an AWS instance should run more than 2 workers once its
bytes match dm-devbox; that needs the launch tooling and the byte check. Otherwise, run on
dm-devbox for about 4 days at this week's load.

**Decision needed.** As before: option 1 (dm-devbox) or option 2 (AWS, with the launch and its
cost ceiling approved).

## 2026-10-28: Lane session 11 (13:05 UTC), progress: the waiting merges rehearsed on current v2

**Done.**
- Engineer: the five pre-pilot branches applied onto v2 4845e50 as local branch `rehearsal2`
  abb4059, in merge order (QUEUE, new item). The one conflict was `driver.py`'s module docstring
  (kept "Times"; took "One driver per store"). The resolved file is byte-identical to the earlier
  rehearsal's 9dcb2cd. Checks on abb4059 (tails read by me, logs in `.cache/s11/`):
  - ruff: `258 files already formatted`, `All checks passed!`
  - mypy: `Success: no issues found in 247 source files`
  - `pytest -m "not slow" -n 1`: `2886 passed, 3 skipped, 3 warnings in 660.52s`, exit 0
  - `pytest tests/campaign -m slow -n 1`: `12 passed, 1 warning in 2900.29s`, exit 0
  - `git merge-tree --write-tree v2 rehearsal2` against 3e8bf91: clean
- Strata run at 14:10Z: 10 of 15 timing lines. dev-001 near: B-rand 2229.7 s wall, B-sym 418.1 s,
  B-sparse 925.7 s, planted-hint 703.7 s; dev-010 started. These are wall times, not yet read as
  CPU and not a result.
- Delegated to the engineer: `project_runtime.py` with several stores, per-stratum costing, and
  the min(CPU, wall) uncontended line (local branch `projection2`).

**Learned.** The lane's guard hook blocks inline shell code that writes files when the text names
`.claude` (worktree paths included). Use the Edit tool for those edits.

## 2026-10-28: Lane session 11 (13:05 UTC), progress: projection2 merged; interim strata figures (not a result)

**Done.**
- Engineer: `projection2` 1b267e0 adds repeatable `--store`, `--by-stratum` and `--wall-from`
  (LOG 2026-10-28). I reran it in its worktree:
  - `13 passed`; ruff clean; mypy `Success: no issues found in 242 source files`.
  - The single-store command differs from `.cache/s10/projection.txt` only in the path prefix and
    the corrected caveat.
  - The engineer's fast tier there: exit 0, 2759 passed, 3 skipped.
- Merged into v2 as e474a6b (script and tests only; no veto window applies). On v2: ruff and mypy
  clean, the file's `13 passed`. The full fast tier on v2 was still running at this commit
  (`.cache/s11/fast-v2.log`).
- Interim, from `.cache/strata` with 14 of 15 lines (planted-hint dev-010 still running), pooled
  with mini-cpu and walls from mini-a and mini-b:
  - unit mean CPU 45.5 h;
  - stratum mean CPU 40.2 h;
  - uncontended min(CPU, walls): 42.1 h unit mean, 37.5 h stratum mean.

  Strata rest on n = 1 per non-far stratum, and B-sym@10F on one near unit (2121.5 s CPU), so the
  far B-sym@10F units are probably understated. Not recorded as the answer until the run ends and
  the red team has read it.

## 2026-10-28: Stop point update (lane session 11, 14:50 UTC): on a quiet dm-devbox the baseline plan sits at about 48 h (40-60 h); at this week's load about 90 h

**Answer.** With near and mid truths measured, the full P3 pool at 2 workers on a quiet machine
(LLM scoring included) is about 48 h, range 40-60 h: at the line, not under it. At this week's
load (wall/CPU 1.83) it is about 90 h, range 75-115 h. Both figures are the red team's (session 11,
second review), and I checked their key inputs. The call depends on the other lane's load and on
B-sym@10F, link and seed spread. The owner's choice stands.

**Evidence.**
- `.cache/strata` (b953390): finished 14:28:54Z, 15 recorded, 0 failed, bugs 0; `store_check.py`
  STORE OK; `hunt_gap.py` GATE PASS (gap above +0.01: 0; oracle_underfit: 0 of 2). Copied to
  `s3://delaymargin-results-482913/sciagent/p3-linux/strata-b953390/` (24 objects).
- Projection with both stores and the walls from mini-a and mini-b
  (`.cache/s11/projection-strata.txt`):
  - unit mean CPU 43.0 h;
  - stratum mean CPU 37.7 h;
  - uncontended min(CPU, walls): 40.0 h unit mean, 35.4 h stratum mean;
  - x 1.83 wall/CPU: 69-79 h.

  The red team's rerun is byte-identical, and its hand checks of a table row and of the LPT match.
- Why those figures understate (red team; inputs checked by me):
  - B-sym@10F on the five far truths is costed at the one near unit's 2121.5 s. The smoke store
    (same computation, identical dev-001 digests) has B-sym@10F dev-000 (far) at 17411.8 s wall,
    against 2083.6 s on dev-001. That adds about 2.5 to 10 h.
  - Stratum is confounded with link. All 9 far truths are softplus; dev-001 and dev-010 are
    identity; 3 of the 4 mid truths are exp or softplus. So dev-010 understates mid. Costed by
    link: 44.7 to 49.5 h CPU, 41.5 to 46.3 h uncontended.
  - The LLM arms' 240 scoring jobs share the pool (front of the queue, `driver.py:360`): +2.1 to
    4.4 h. The in-process session fits and the AG-o containers are not quantified.
  - Seed 1 only, and cost per unit is a draw: a bootstrap over the 5 truths gives a 90% interval
    of 29.6 to 55.6 h on the unit-mean basis.

**Caveats.** No measurement on seeds 2-3, on exp-link truths, or on uncontended dm-devbox. The
Windows pilot's per-unit store is not on this machine or in S3, so the method has no backtest.

**Recommendation.** Option 1 (dm-devbox) is now plausible: about 2 days if the other lane is
quiet, about 4 days at this week's load, with no new tooling and no AWS spend. Option 2 (AWS)
only pays if the instance runs more than 2 workers with matching bytes; that needs launch
tooling and a byte check first. I lean to option 1, started right after the five merges (from
17:00Z to 21:45Z, QUEUE). The run is resumable, so it can be stopped if the owner prefers AWS
later.

**Decision needed.** As before: option 1 or option 2. Option 2 also needs the AWS launch and its
cost ceiling approved.

## 2026-10-28: Lane session 11 (13:05 UTC) hand-over: projection corrected and extended, strata measured, merges rehearsed, C5 adopted

**Done.**
- The 48-hour stop point was red-teamed twice and corrected twice (entries above). Contended
  CPU is not a lower bound on this 2-core, 4-thread box (LOG correction). With near and mid
  measured, the figure is about 48 h quiet (40-60 h) and about 90 h at this week's load (75-115 h).
  Owner notified twice; desktop only, since mobile push is inactive.
- Strata run `.cache/strata` (b953390, 2 workers, 13:07:33Z-14:28:54Z): 15 recorded, 0 failed;
  STORE OK; GATE PASS. Copied to S3 and entered in the ledger (1.36 h).
- `scripts/project_runtime.py`: caveat text corrected. Repeatable `--store`, `--by-stratum` and
  `--wall-from` merged as d4bd845 (`pull --rebase` flattened the merge commit e474a6b named in the
  progress entry). Fast tier on v2: `2759 passed, 3 skipped, 3 warnings in 374.32s`, exit 0
  (`.cache/s11/fast-v2.log`).
- `rehearsal2` abb4059: the five pre-pilot branches on current v2, with fast and campaign slow
  tiers passing (QUEUE item).
- C5 random-arm seeding adopted at 16:06Z, with VETO.md empty (LOG). It is implemented with the
  p4-deltas integration.

**Learned.**
- On a hyperthreaded host, CPU seconds measured under load inflate. min(CPU, walls of identical
  reruns) is the tighter figure.
- Stratum is confounded with link in the dev split (every far truth is softplus, most near truths
  identity). Cost tracks link, so per-stratum costing from one identity mid truth understates mid.
- The smoke store holds a B-sym@10F far measurement (dev-000, 17411.8 s wall) that the CPU-timed
  stores lacked. Check the older stores for evidence before calling a system unmeasured.
- `git pull --rebase` linearises a local merge commit, so record SHAs after the push, not before.

**Next.**
1. Merges from `rehearsal2`, each after its window and a VETO.md check (QUEUE item, top of
   Platform): session keys 7bf2e0a (17:00Z), fingerprint-guard d0c3e69 (19:15Z), the rescore guard
   d5d1d90 and 76a70ef, c1-oracle-inf eb48450 (21:45Z), then c1-label abb4059. Then the fast tier
   on v2 and `tests/campaign -m slow`.
2. When the owner answers the stop point: if option 1, the full pilot on dm-devbox after the
   merges (pilot-runner; the brake question in QUEUE still applies to its wall times). If option
   2, launch tooling and the instance byte check.
3. Optional, if the owner wants a sharper figure: `--by-link` costing in `project_runtime.py`, and
   a CPU-timed far B-sym@10F unit.
4. Owner decisions still waiting: the stop point (option 1 or 2), Δ6 (A/B/C), and the brake and
   pilot-unit question.

## 2026-10-28: Lane session 12 (16:08 UTC), progress: waiting on the 17:00Z window; `--by-link` costing started

**Done.** Oriented: INBOX has no open entry; VETO.md is empty; the tree was clean and up to date
with `origin/v2` (4c140e2). `git merge-tree --write-tree v2 <sha>` is clean for 7bf2e0a (session
keys), d0c3e69 (fingerprint guard) and 76a70ef (rescore guard) against current v2. The rescore
guard (d5d1d90, 76a70ef) is test only and has no veto window of its own, so it lands with the
fingerprint guard at 19:15Z. An engineer is adding `--by-link` to `scripts/project_runtime.py`
on local branch `by-link` (worktree `.claude/worktrees/by-link`), test first, to reproduce the
red team's hand figures for costing by link (44.7 to 49.5 h CPU).

**Learned.** Load is about 6 on 4 vCPUs from the other lane's analysis job (16:09Z), so test tiers
will run slowly.

**Next.** After 17:00Z: check VETO.md again, merge 7bf2e0a, run ruff, mypy and the fast tier, then
push. After 19:15Z: merge up to 76a70ef the same way, before this session's 19:48Z deadline.
c1-oracle-inf and c1-label (21:45Z) go to the next session.

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


## 2026-10-27: Lane session 4 (15:57 UTC): checkpoint, work in progress

**Done so far.**
- Oriented on `v2` at 5cab148 (clean, up to date). INBOX has no entries; VETO.md has no
  vetoes. `free -g`: 9 GB available; load about 9 to 10.
- Smoke (`tmux -L sciagent`, `sci-smoke`) still running. At 15:59 UTC per `.cache/smoke-run.log`:
  ORACLE, B-lib, B-np dev-000 scored (50.7, 14.6, 13.5 s); AG-c-S/named dev-000 submitted
  (257 s, 33 turns), AG-o-S/named dev-000 (132 s, 16 turns), AG-c-S/named dev-001 (109 s,
  28 turns), AG-o-S/named dev-001 (53 s, 16 turns). No exit line yet.
- Started (background): an engineer in a worktree on "Session keys and the sandbox" (test
  first; AG-o session key to cover `SandboxLimits`, the handoff helper sha256 and the image
  tag, AG-c keys unchanged); an engineer investigating the `Solution may be inaccurate`
  warning at `glm/fit_solve.py:591` (read-only, recommendation only); a red-team review of a
  recommendation for open decision 6 (C5 random-arm seeding: key every policy stream by truth).

**Next.** Read the smoke and its replays; verify and record the three results above. This
entry is replaced by the session's final entry at hand-over if the session ends normally.

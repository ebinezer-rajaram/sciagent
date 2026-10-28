# Work queue

The unattended lane (`docs/v2/AUTOPILOT.md`) and interactive sessions take work from the top of
this list. Status: `[ ]` open, `[~]` in progress, `[x]` done (date), `[!]` blocked (say on what).
Each item says when it is done. Move finished items to the bottom section after a week.

## Platform (first)

- [x] 2026-10-27 **Docker on dm-devbox.** Rootless Docker 29.9.2 installed by the owner's
  interactive session (daemon runs as `ubuntu`; no docker group; root daemon disabled). Lanes get
  `DOCKER_HOST=unix:///run/user/1000/docker.sock`. Checked: `docker run --network none hello-world`.
- [x] 2026-10-27 **Sandbox on native Linux Docker** (engineer, test first; after Docker). Fixed
  with an ownership handoff around each call (LOG 2026-10-27); 19 slow sandbox tests pass. Suspected break,
  from reading the code: the container runs as `65534:65534` (`SANDBOX_UID`) while the host
  creates the run's `work/` as uid 1000 with mode 0755, so writes to `/work` (state between
  calls, `test_state_persists_only_through_work`) probably fail, and files the container
  creates may not be removable by the host. Docker Desktop on Windows hid this. Done when
  `nice uv run pytest tests/sandbox -m slow` passes on dm-devbox with every isolation test kept.
- [x] 2026-10-27 **Identifiability of the dev truths under fit/5** (20/20 pass, none changes;
  LOG 2026-10-27) (P4-PROPOSAL §2.2): rerun
  `check_identifiable` on the 20 dev truths with v2's fitter and report any truth that changes
  status. Done when the result is in the journal and LOG.
- [x] 2026-10-27 **Sandbox handoff follow-ups** (merged a0129ab; LOG 2026-10-27, two
  entries; red-team review in JOURNAL 2026-10-27 session 3). All seven items plus the review's
  findings: bounded-memory emptying of an over-limit `work/`, the limits and helper sha256 in
  AG-o records, typed errors, symlinked roots, process-group kill. Not addressed (known):
  bytes on disk per call bounded only per file; no lock on a `Sandbox` (asyncio abandons tool
  threads after 300 s). The scored-not-voided choice is a proposal (veto window ends
  2026-10-28T15:00Z).
- [~] **Session keys and the sandbox** (before any AG-o cell that produces reported numbers):
  `session_text` covers neither `SandboxLimits`, the handoff helper's sha256 nor the image tag,
  so a stored AG-o session cell would be reused across a change to any of them. Recommend
  (veto rule) adding them to the AG-o session key, or refusing reuse on mismatch; engineer,
  test first.
  2026-10-27: implemented on local branch `worktree-agent-a501a8588ae2e77cb` (2dbd125); proposal
  in LOG (veto window ends 2026-10-28T17:00Z). Red team (session 5): merge with fixes; fixes in
  90612ca, 7ce500f, f1c5b0f (argv, /data format and tool caps in the key; image ID recorded and
  checked on replay; only the 240 AG-o addresses move). Fast tier NOT run on f1c5b0f; slow
  campaign and AG-o replay tests NOT passed (killed for memory).
  2026-10-28: rebased, e9a3ce7: fast `2762 passed, 3 skipped`; slow campaign/investigation/sandbox
  `37 passed` (AG-o replay included). 2026-10-28 (session 7): its two longest campaign slow
  tests passed (exit 0, 03:36Z; JOURNAL session 7). Next: merge after the window (second, after
  wall-time-move).
- [~] **Session cells carry wall time**: proposed 2026-10-27 to move `wall_s`, `duration_api_ms`
  and `total_cost_usd` from the `llm-session` cell to its `timing.jsonl` line (LOG 2026-10-27,
  veto window ends 2026-10-28T12:00Z). After the window, unless VETO.md objects, and after the
  sandbox follow-ups land in `campaign/llm.py`: implement (engineer, test first: every cell kind
  byte-identical across two scripted-driver runs; `pilot_report.py` reads session wall times from
  `timing.jsonl`). Must land before the full pilot.
  2026-10-27: implemented on local branch `wall-time-move` (df0ce40; worktree
  `.claude/worktrees/agent-a9c4be026147be112`): ruff, mypy clean, fast tier `2720 passed, 3
  skipped`; `tests/campaign -m slow` NOT confirmed (no summary line).
  2026-10-28: rebased, 3f0274b: `tests/campaign -m slow` `12 passed`. Next: merge after the
  window (first of the four).
- [~] **C1: any ORACLE −inf fails control 1** (review item (c), LOG 2026-10-27; veto window ends
  2026-10-28T21:45Z; sets a P3 go/no-go rule, owner pushed): replace the drop at
  `campaign/analysis.py:580-600` with a C1 failure for P3 and P4, and fix the "dropped as not
  identifiable" label. Engineer, test first (analysis is the go/no-go instrument). Must land
  before the full pilot.
  2026-10-27: on local branch `c1-oracle-inf` (285485a; worktree
  `.claude/worktrees/agent-a2e38fbd1385fefee`): its 21 analysis tests pass, ruff and mypy clean;
  the fast tier showed 2 UNIDENTIFIED failures before it was killed for memory.
  2026-10-28: rebased, 9db6d8e: the 2 were `test_replay_check` failing on the worktree's missing
  (gitignored) dev split, not the branch; report slow tests `4 passed`; clean fast tier
  `2715 passed, 3 skipped`. Next: merge after the window (last of the four).
- [~] **Code-fingerprint guard on stores** (F5, LOG 2026-10-27; veto window ends
  2026-10-28T19:15Z): the store keeps a sidecar with the git commit, a clean-tree flag and the
  `uv.lock` sha256; a dirty tree refuses to write; a resume under a different fingerprint refuses
  to reuse cells unless `--reuse-from <fp>` names it (recorded in the sidecar). Engineer, test
  first. Must land before the full pilot.
  2026-10-27: built on local branch `fingerprint-guard` (2998821; worktree
  `.claude/worktrees/agent-ac2f2d04e01ad1a75`), LOG 2026-10-27. Red-teamed in session 5 (merge
  with fixes); fixes in checkpoint 903937e (one driver per store via `flock`, `uv.lock` dropped
  from the fingerprint, imported code must be under the checkout). `tests/campaign` fast `187
  passed`; full fast tier and campaign slow tier NOT run on 903937e.
  2026-10-28: rebased, 1ea5056: fast `2770 passed, 3 skipped`; `tests/campaign -m slow` all 11
  passed (two parts). Next: merge after the window (third). Resuming the smoke or mini-pilot stores under it needs
  `--reuse-from unrecorded`.
- [!] **Pilot runs and the usage brake** (needs the owner; found 2026-10-28, JOURNAL session 6):
  `~/bin/usage_brake.py` freezes `research-lane@sciagent.service`, and pilot processes started
  from a lane session live in that unit, so a brake freeze stalls a baseline-only run and adds the
  frozen time to its measured wall times (mini-pilot run A, 22:13:59Z-23:50:49Z). Recommendation:
  start pilot tmux servers in their own transient user unit (`systemd-run --user --unit=sci-<run>`)
  so non-LLM runs are not braked, and have the brake also freeze that unit while a run has LLM
  units (it spends Max usage); until then, measure wall times only on units that overlap no
  interval in `~/logs/usage/brake.log`. `~/bin` is the owner's; the lane does not edit it.
- [x] 2026-10-27 **Slow tier on dm-devbox**: `nice -n 10 uv run pytest -m slow -n 2` at 4a6d780,
  `273 passed, 24 warnings in 5716.76s (1:35:16)`, exit 0 (`.cache/slow/run.log`).
- [x] 2026-10-27 **Rebuild the defect-hunt checks** (`scripts/hunt_gap.py`, `store_check.py`,
  `store_compare.py`, `report_determinism.py`; LOG 2026-10-27) lost with the Windows PC (`.cache/hunt/A/p3_gap.py`,
  `B/store_check.py`, `B/report_determinism.py`) as committed scripts with tests (engineer):
  the gap check against ORACLE on a store, store consistency, and a byte comparison of two
  stores. Their contracts are in HANDOFF-2026-10-06 and LOG 2026-10-06. Done when each runs on
  a store and its tests pass.

## P3 pilot, rerun from scratch on dm-devbox (LOG 2026-10-27)

- [x] 2026-10-28 **Smoke** (finished 2026-10-27T22:23:43Z, exit 0: 20 units recorded, 0
  failed, 0 bugs; AG-o and AG-c replays PASS (JOURNAL session 4); per-unit wall times in JOURNAL
  session 6; store and logs at `s3://delaymargin-results-482913/sciagent/p3-linux/smoke-e3fc6f1/`.
  Ran in `tmux -L sciagent` session `sci-smoke`,
  store `.cache/smoke`, frozen worktree `.claude/worktrees/smoke` at e3fc6f1; see JOURNAL)
  (pilot-runner): `nice -n 10 uv run python scripts/pilot.py --root .cache/smoke
  --ids dev-000,dev-001 --seeds 1 --llm-limit 4 --workers 2` in tmux, then replay one AG-o and
  one AG-c record offline (`sciagent.investigation.replay_investigation`). Needs
  Docker. Done when every smoke unit is recorded and scored, both replays pass, and wall times
  per unit are in the journal.
- [ ] **Mini-pilot gate**, criteria fixed before running (HANDOFF-2026-10-06): dev-008, dev-013
  and dev-017 at seed 1, every non-LLM system except B-sym@10F, run twice into two stores.
  Pass needs 0 `oracle_underfit`, no gap above +0.01, byte-identical cells across the two
  runs, measured wall time per unit on dm-devbox, the report rendering, and peak memory per
  worker times workers at most 6 GB. Done when each criterion is recorded as pass or fail; a
  failure stops the rerun until it is fixed.
  2026-10-28 00:00Z: running in `tmux -L sciagent` session `sci-mini` (worktree
  `.claude/worktrees/mini` at 4cd2564). Run A (`.cache/mini-a`) 6 of 15 jobs done; run B
  (`.cache/mini-b`) started 23:50:49Z. Caveat for the wall-time criterion: the usage brake froze
  the lane unit 2026-10-27T22:13:59Z-23:50:49Z (`~/logs/usage/brake.log`) and run A's processes
  are in it, so run A's planted-hint dev-008 wall time includes about 97 min frozen; take wall
  times from units that overlap no freeze interval in brake.log (JOURNAL session 6).
  2026-10-28 (session 7): interim checks on finished units pass (cells identical, 0 differing;
  `hunt_gap.py` GATE PASS on run A; both stores STORE OK; JOURNAL session 7). Not the gate.
- [~] **CPU time per unit** (found 2026-10-28, JOURNAL session 6): wall times on dm-devbox swing
  more than 10× with the other lane's load (run B's ORACLE dev-008 860.5 s against run A's
  75.6 s). Record the worker's CPU time per job (`resource.getrusage`) beside `wall_s` in
  `timing.jsonl` (engineer, test first, in a worktree; not while a run uses that checkout), so the
  projection below can use it. Small follow-up in the same change: `test_replay_check`'s `_check`
  helper passes stderr through, and a missing dev split fails with a clear message.
  2026-10-28 (session 7): built on local branch `cpu-time` 21e7947 (worktree
  `.claude/worktrees/cpu-time`), stacked on `wall-time-move` 3f0274b; LOG 2026-10-28. ruff, mypy
  clean; fast `2742 passed, 3 skipped`; slow `tests/campaign/test_campaign_slow.py
  tests/campaign/test_replay_check.py` without `test_every_non_llm_system_runs_and_scores_one_real_unit`
  `7 passed` (engineer). Next: merge right after wall-time-move (rebase onto v2 then), run that
  one slow test, then the full fast tier and `tests/campaign -m slow` on v2. The mini-pilot
  stores (4cd2564) have no CPU figures; `pilot_report.py` reads their lines as wall-only.
- [ ] **Where the baselines run.** From the mini-pilot's per-unit times, project the full plan
  at 2 workers. At about 48 hours or less, run on dm-devbox. Above that, stop point: the owner
  chooses between the longer dm-devbox run and the AWS CPU option (LOG 2026-10-27: m6i.xlarge,
  about $9-11), which also needs sciagent launch tooling and a byte check of the instance
  against dm-devbox first.
- [ ] **Full pilot** (pilot-runner): `nice -n 10 uv run python scripts/pilot.py --root
  .cache/pilot --truths 20 --seeds 1,2,3 --b10f-truths 10 --workers 2 --llm-concurrency 1` in
  tmux, exactly the SPEC §7 P3 plan: 20 dev truths x 3 seeds; AG-c-S and AG-o-S, named and
  anonymised, live under Max; B-lib, B-rand, B-sym, B-sparse, B-np, ORACLE, planted-hint;
  B-sym@10F on seed 1 of dev-000 to dev-009 as declared. Raise LLM concurrency to 2 only if
  no rate limit has appeared. Done when the coverage table shows every planned unit scored or
  failed with a reason.
- [ ] **Regenerate PILOT.md** (analyst) with `scripts/pilot_report.py` from the Linux store;
  rewrite the notes block for the Linux run (the Windows notes stay in git history; say so).
  Red team before the commit. Done when committed with the red team's findings addressed.
- [!] **P3 go/no-go read: stop point.** SPEC §7.1 under the re-read rules of P4-PROPOSAL §2.4;
  the owner reads a few AG-o transcripts in full. Prepare the evidence and transcript picks,
  push the owner, wait. Done when the owner has decided.

## P4 preparation (after a GO)

- [ ] **Integrate `origin/p4-deltas` after the Linux pilot.** Pushed from the Windows PC on
  2026-10-27 11:45 UTC: P4 work packages 4 to 12 plus core.reductions fixes, based on ea055be
  (older v2). Merge into v2 only after the pilot's numbers are produced (it changes the harness:
  HARNESS_VERSION harness/2, session keys), re-run fast and slow tiers and the determinism check
  on dm-devbox. The `p3-fit4` tag was not pushed; not needed unless an old record cites it.
- [x] 2026-10-27 **Cross-check the rebuilt defect-hunt checks against the originals** (7406810,
  LOG 2026-10-27): ported test-first `check_budgets`, `check_sessions` (`scripts/session_check.py`),
  `KEY_FIELDS` and the beats-truth flag (`hunt_gap.py --split`; making it fail the gate is a
  proposal, veto window ends 2026-10-28T13:00Z). Coverage table in JOURNAL 2026-10-27.
- [ ] **Hunt-check leftovers** (from the cross-check): (a) ~~beats-truth red team~~
  done 2026-10-27: adopt at +0.01 with a digest check (merged 4677b57; LOG 2026-10-27),
  adoption after its veto window; (b) ~~peak worker memory~~ done 2026-10-27:
  `scripts/mem_sampler.py` (`/proc` VmHWM per pool worker, STOP brake below 2 GB available;
  run it beside the mini-pilot with `--pid`),
  measured slowdown on dm-devbox still to decide;
  (c) ~~store-level replay check~~ done 2026-10-27: `scripts/replay_check.py` (98e3aa4;
  smoke store 4 of 4 PASS);
  (d) `rescore_guard` extras (strip the CLI from PATH, patch the SDK entry points);
  (e) explicit hash-seed baseline pair and report-on-a-copy (low).
- [ ] **Decisions open since 2026-10-06** (HANDOFF-2026-10-06): the prompt variant freeze
  (Δ6), EIG weights (Δ11), an event cap for FSD, hand-written address text (F5), the
  review items, and decision 6 of HANDOFF-2026-10-09 (C5 random-arm seeding: `policy_seed` has
  no truth id). Owner's rule (2026-10-27): the lane writes a recommendation for each with its
  reasoning, has the red-team agent review it, records it in LOG.md with `veto window ends:
  <UTC>` 24 hours ahead, pushes the owner a summary, and adopts it after the window unless a
  `VETO` line appears in docs/v2/VETO.md. Done when each is adopted or vetoed and logged.
  Progress: decision 6 (C5 seeding) proposed and red-teamed 2026-10-27 (LOG, veto window ends
  2026-10-28T16:05Z); implement with the p4-deltas integration.
  F5 (hand-written address text) proposed and red-teamed 2026-10-27 (LOG, veto window ends
  2026-10-28T19:15Z): the guard above before the pilot; derived address text with the p4-deltas
  integration. Δ11 (EIG weights) proposed 2026-10-27 after the red team rejected the first
  draft's tie rule (LOG, veto window ends 2026-10-28T21:40Z). Drafts of the FSD event cap and the
  five review items are in `.cache/drafts/`; review items proposed (LOG, window ends 2026-10-28T21:45Z); Δ6: `link-neutral` not adopted, a link-balanced variant recommended pending its own red team (LOG); 2026-10-28: link-balanced red-teamed, **needs the owner's explicit choice** (A/B/C; no veto window, since (C) sets aside rule (A) after the numbers; LOG 2026-10-28); FSD cap: draft not adopted, measurement first (LOG).
- [ ] **Remaining P4 work packages**: WP9 (1327de9), WP10 (98205b4), WP11 controls 4-6
  (6ac6142) and WP12 LOG entries (f4a38e0) are committed on `origin/p4-deltas`
  (HANDOFF-2026-10-09); its slow tier was never run. Folded into the integration item above.
- [ ] **Size P4 from the Linux pilot** (SPEC §7.1, last bullet): LLM calls and wall time per
  investigation under Max at this lane's share, for Haiku, Sonnet and Opus, named and
  anonymised, on the 40 x 2 test design. Done when the estimate is in the journal.
- [ ] **Test split and the §6.1 freeze: stop point.** Generate the test split outside the
  repository (records kept on dm-devbox and under the S3 prefix; only the sha256 committed) and
  prepare the single freeze commit of SPEC §6.1. The owner approves before any agent touches
  the test split.
- [ ] **Fetch the QTM catalogue** (`scripts/fetch_qtm.py`, for P5) so its three skipped
  acceptance tests run. Low priority.

## Done

- [x] 2026-10-27 **Platform check on dm-devbox.** `uv sync`; fast tier, mypy and ruff green
  after re-pinning the six fit/4 reference digests to dm-devbox (LOG 2026-10-27); the
  determinism child gave byte-identical output on two runs.
- [x] 2026-10-27 **Lane set-up.** CLAUDE.md (platform, money, lane), AUTOPILOT.md, this queue,
  JOURNAL.md, LEDGER.csv and the five project agents.
- [x] 2026-10-27 **Dev split on Linux.** Regenerated from b5e2b87 (fit/4): the same 20 truths
  as PILOT.md's table, sha256 `604acec5…`, adopted at `data/v2/truths/dev` (LOG 2026-10-27).

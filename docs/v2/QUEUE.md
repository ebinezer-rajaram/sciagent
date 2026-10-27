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
- [~] **Sandbox handoff follow-ups, before any AG-o run that produces reported numbers**
  (2026-10-27: in progress by an engineer in worktree
  `.claude/worktrees/agent-ab762e5af9832a80f`, branch `worktree-agent-ab762e5af9832a80f`, based
  on 4a6d780; see JOURNAL for its state. Next session: verify, rebase onto `v2`, merge.)
  (engineer, test first; red-team review of 2026-10-27, findings in JOURNAL 2026-10-27):
  (1) before the back handoff, confirm the agent container is gone (`docker inspect`), else
  raise `SandboxError`; catch `TimeoutExpired` in `_remove`; (2) put both handoffs under one
  `try/finally`, check `work_dir` is owned by the host uid afterwards, and give cleanup and the
  session hard-timeout path (`campaign/llm.py:314`) a recovery handoff, so a missed handoff
  cannot make `investigation/runner.py:364` raise and lose the record; (3) decide how a
  handoff slowed by millions of agent files is classified (today a void, which selects on
  agent behaviour); (4) `chmod -R u+rwX,go-w` and strip POSIX ACLs; (5) make wrong platform
  detection fail loudly; (6) typed errors only; (7) tests for `_probe_handoff_owner` parsing
  and the fault paths. Done when each has a test seen failing first and the slow sandbox tier
  passes.
- [~] **Session cells carry wall time**: proposed 2026-10-27 to move `wall_s`, `duration_api_ms`
  and `total_cost_usd` from the `llm-session` cell to its `timing.jsonl` line (LOG 2026-10-27,
  veto window ends 2026-10-28T12:00Z). After the window, unless VETO.md objects, and after the
  sandbox follow-ups land in `campaign/llm.py`: implement (engineer, test first: every cell kind
  byte-identical across two scripted-driver runs; `pilot_report.py` reads session wall times from
  `timing.jsonl`). Must land before the full pilot.
- [x] 2026-10-27 **Slow tier on dm-devbox**: `nice -n 10 uv run pytest -m slow -n 2` at 4a6d780,
  `273 passed, 24 warnings in 5716.76s (1:35:16)`, exit 0 (`.cache/slow/run.log`).
- [x] 2026-10-27 **Rebuild the defect-hunt checks** (`scripts/hunt_gap.py`, `store_check.py`,
  `store_compare.py`, `report_determinism.py`; LOG 2026-10-27) lost with the Windows PC (`.cache/hunt/A/p3_gap.py`,
  `B/store_check.py`, `B/report_determinism.py`) as committed scripts with tests (engineer):
  the gap check against ORACLE on a store, store consistency, and a byte comparison of two
  stores. Their contracts are in HANDOFF-2026-10-06 and LOG 2026-10-06. Done when each runs on
  a store and its tests pass.

## P3 pilot, rerun from scratch on dm-devbox (LOG 2026-10-27)

- [ ] **Smoke** (pilot-runner): `nice -n 10 uv run python scripts/pilot.py --root .cache/smoke
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
- [ ] **Hunt-check leftovers** (from the cross-check): (a) re-run the red-team review of the
  beats-truth gate proposal before adopting it (the first one stalled); (b) peak worker memory
  and measured slowdown on dm-devbox for the mini-pilot gate (the Windows sampler has no Linux
  equivalent: decide how to measure, e.g. a `/proc` RSS sampler, before the mini-pilot);
  (c) store-level replay check (`hunt/B/replay_check.py`), after the sandbox follow-ups land;
  (d) `rescore_guard` extras (strip the CLI from PATH, patch the SDK entry points);
  (e) explicit hash-seed baseline pair and report-on-a-copy (low).
- [ ] **Decisions open since 2026-10-06** (HANDOFF-2026-10-06): the prompt variant freeze
  (Δ6), EIG weights (Δ11), an event cap for FSD, hand-written address text (F5), the
  review items, and decision 6 of HANDOFF-2026-10-09 (C5 random-arm seeding: `policy_seed` has
  no truth id). Owner's rule (2026-10-27): the lane writes a recommendation for each with its
  reasoning, has the red-team agent review it, records it in LOG.md with `veto window ends:
  <UTC>` 24 hours ahead, pushes the owner a summary, and adopts it after the window unless a
  `VETO` line appears in docs/v2/VETO.md. Done when each is adopted or vetoed and logged.
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

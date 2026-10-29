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
- [x] 2026-10-28 **Merge the five pre-pilot branches from `rehearsal2`** (all five merged; last 1c8160a at 21:49Z) (session 11): local branch
  `rehearsal2` abb4059 (worktree `rehearsal2`), on v2 4845e50. In order: session keys 7bf2e0a
  (window 17:00Z), fingerprint-guard d0c3e69 (19:15Z), rescore guard d5d1d90 and 76a70ef,
  c1-oracle-inf eb48450 (21:45Z), c1-label abb4059. The only conflict is `driver.py`'s docstring;
  its resolution is byte-identical to the earlier rehearsal. On abb4059: ruff and mypy (247 files)
  clean; fast tier `2886 passed, 3 skipped`, exit 0; `tests/campaign -m slow` `12 passed`, exit 0
  (`.cache/s11/rehearsal2-*.log`). `git merge-tree` against v2 3e8bf91 is clean. After each
  window, with VETO.md checked, merge v2 up to that branch's commit on rehearsal2
  (`git merge <sha>`). After the last, rerun the fast tier on v2.
  2026-10-28 (session 12): session keys 7bf2e0a merged at 17:01Z with VETO.md empty (87b406f,
  after by-link 5aff197); on v2 ruff and mypy (243 files) clean, fast tier `2815 passed, 3 skipped`
  exit 0, `tests/campaign -m slow` `12 passed` exit 0 (`.cache/s12/*-keys.log`).
  Fingerprint guard and rescore guard (up to 76a70ef) merged at 19:16Z with VETO.md empty
  (de2e8a3; tree identical to the pre-verified 25c4bb0: fast `2885 passed, 3 skipped`, campaign
  slow `12 passed`, `.cache/s12/premerge-*.log`). Left: c1-oracle-inf eb48450 and c1-label
  abb4059 after 21:45Z, then the fast tier and campaign slow tier on v2.
  2026-10-28 (session 13): c1-oracle-inf and c1-label (up to abb4059) merged at 21:49Z with
  VETO.md empty (bed7454, 1c8160a: `git pull --rebase` flattened merge f6e2d0e before the push;
  1c8160a's tree `48699bb…` equals `git merge-tree` of v2 and abb4059, which differs from
  abb4059 only in docs and the by-link script and its tests). On v2: ruff clean, mypy `247 source
  files` clean, fast tier `2897 passed, 3 skipped`, exit 0 (`.cache/s13/fast-c1.log`);
  `tests/campaign -m slow -n 1` `12 passed, 1 warning in 4289.30s`, exit 0
  (`.cache/s13/campaign-slow-c1.log`).
- [x] 2026-10-28 **Session keys and the sandbox** (merged 87b406f at 17:01Z, no veto) (before any AG-o cell that produces reported numbers):
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
- [x] 2026-10-28 **Session cells carry wall time** (merged 4f3adb8 after the window, no veto): proposed 2026-10-27 to move `wall_s`, `duration_api_ms`
  and `total_cost_usd` from the `llm-session` cell to its `timing.jsonl` line (LOG 2026-10-27,
  veto window ends 2026-10-28T12:00Z). After the window, unless VETO.md objects, and after the
  sandbox follow-ups land in `campaign/llm.py`: implement (engineer, test first: every cell kind
  byte-identical across two scripted-driver runs; `pilot_report.py` reads session wall times from
  `timing.jsonl`). Must land before the full pilot.
  2026-10-27: implemented on local branch `wall-time-move` (df0ce40; worktree
  `.claude/worktrees/agent-a9c4be026147be112`): ruff, mypy clean, fast tier `2720 passed, 3
  skipped`; `tests/campaign -m slow` NOT confirmed (no summary line).
  2026-10-28: rebased, 3f0274b: `tests/campaign -m slow` `12 passed`. 2026-10-28 (session 10):
  merged into v2 at 12:00:30Z with VETO.md empty (4f3adb8), then cpu-time (0bb8b41) and
  `projection` (39d088f); on v2 ruff, mypy (242 files) clean, fast tier exit 0 (2753 passed, 3
  skipped; `.cache/s10/fast.log`).
- [x] 2026-10-28 **C1: any ORACLE −inf fails control 1** (merged 1c8160a at 21:49Z, no veto; adopted, LOG 2026-10-28) (review item (c), LOG 2026-10-27; veto window ends
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
  2026-10-28 (session 9): the report's control-1 headline printed **PASS** whenever the underfit
  and -inf parts passed, even with the margin part not checkable (mini-b: PASS over an empty
  margin table, while `Controls pass: False`; red team). Fixed as a label only on local branch
  `c1-label` 66f7d7d, stacked on `merge-rehearsal`: `control_1_headline` reads the verdict's own
  `oracle_checks`/`kept`/`dropped`; NOT CHECKABLE when no truth has 2 paired finite seeds, FAIL on
  any failing part, PASS otherwise naming dropped and uncheckable truths (as `analysis.verdict`
  treats them; no rule changes). Tests first: `tests/campaign/test_pilot_report_control_1.py` 6
  of 7 failed before, `7 passed` after (my rerun too); `tests/campaign -m "not slow" -n 1` `272
  passed`; ruff, mypy (244 files) clean. Merge right after c1-oracle-inf.
- [x] 2026-10-28 **Code-fingerprint guard on stores** (merged de2e8a3 at 19:16Z, no veto; resuming far10f or older stores on new code needs `--reuse-from`) (F5, LOG 2026-10-27; veto window ends
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
- [x] 2026-10-29 **Pilot runs and the usage brake** (decided by the control room 2026-10-29, LOG 2026-10-29: no-LLM runs in their own `systemd-run --user --unit=sci-<run>` unit, LLM runs stay in the lane unit; found 2026-10-28, JOURNAL session 6):
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
- [x] 2026-10-28 **Mini-pilot gate** (verdict PASS with caveats, JOURNAL session 10), criteria fixed before running (HANDOFF-2026-10-06): dev-008, dev-013
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
  2026-10-28 (session 8): run A finished 04:59:30Z, exit 0, 21 recorded, 0 failed; its criteria
  all pass (GATE PASS, 0 oracle_underfit; memory 2.050 GB x 2 = 4.100 GB; report renders; wall
  times measured but contended). Run B 20 of 21 at 07:50Z (planted-hint dev-017 running):
  `identical cells: 20 ... differing: 0`, GATE PASS, STORE OK. Next: when run B exits, rerun
  `store_compare.py .cache/mini-a .cache/mini-b` (want 21 identical), `hunt_gap.py` and
  `store_check.py` on run B, `mem_sampler.py --summary .cache/mini-b-mem.csv --workers 2`, then
  record the gate verdict (JOURNAL session 8).
  2026-10-28 (session 9): run B finished 09:33:38Z, exit 0, 21 recorded, 0 failed, bugs 0. Every
  criterion measured, all meeting their thresholds (as restated in this item, f3323f6, before the runs):
  `store_compare.py` `identical cells: 21 ... differing: 0`; `hunt_gap.py` on B `gap above +0.01:
  0`, `oracle_underfit: 0 flagged`, GATE PASS; STORE OK; memory 2.045 GiB per worker (both runs
  used `--workers 1`; x 2 = 4.09 GiB, + parent 0.16); report renders. Red team (session 9): PASS
  with caveats. **The verdict is not recorded yet**: LOG 2026-10-27 (beats-truth proposal) says the
  gate is not judged before 2026-10-28T13:00Z. Next session, after 13:00Z and with VETO.md
  empty: record PASS with the caveats in JOURNAL session 9. Not shown yet: byte identity at 2
  workers (the full pilot's setting); `store_compare.py .cache/mini-a .cache/mini-cpu` covers it
  once mini-cpu has run on 2 workers. Stores and logs at
  `s3://delaymargin-results-482913/sciagent/p3-linux/mini-4cd2564/`.
  2026-10-28 (session 10, 13:00:37Z, VETO.md empty, beats-truth window over): **verdict PASS** as
  restated, with caveats: wall time per unit measured but contended; both runs at `--workers 1`
  (the 6 GB memory figure is x 2 workers, not what ran); run A's lowest MemAvailable 2.124 GiB.
  The 2-worker byte check is still to come from `.cache/mini-cpu` (Where the baselines run).
- [x] 2026-10-28 **CPU time per unit** (merged 0bb8b41; found 2026-10-28, JOURNAL session 6): wall times on dm-devbox swing
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
  2026-10-28 (session 8): merge rehearsal of all five branches on local branch `merge-rehearsal`
  9dcb2cd (worktree `.claude/worktrees/merge-rehearsal`): only conflict `driver.py`'s docstring
  (union kept); ruff, mypy (243 files) clean; fast `2856 passed, 3 skipped`. Slow tier not run on
  the combined tree (session 9: `tests/campaign tests/investigation tests/sandbox -m slow -n 1`
  on 9dcb2cd `40 passed, 1 warning in 6762.88s`, exit 0; `.cache/slow-rehearsal/run.log`). A CPU-timed rerun of the mini-pilot plan runs on 21e7947 (`sci-minicpu`,
  store `.cache/mini-cpu`), for the projection below.
- [x] 2026-10-29 **Where the baselines run**: dm-devbox, in their own user unit (owner, INBOX 11:27Z;
  LOG 2026-10-29). History below. (Was: needs the owner: stop point, projection above 48 h; JOURNAL session 10 stop point). From the mini-pilot's per-unit times, project the full plan
  at 2 workers. At about 48 hours or less, run on dm-devbox. Above that, stop point: the owner
  chooses between the longer dm-devbox run and the AWS CPU option (LOG 2026-10-27: m6i.xlarge,
  about $9-11), which also needs sciagent launch tooling and a byte check of the instance
  against dm-devbox first.
  2026-10-28 (session 8): timing source chosen: CPU time (`cpu_s + cpu_children_s`) from the
  CPU-timed rerun `.cache/mini-cpu` (`tmux -L sciagent` `sci-minicpu`, `--workers 1`, started
  05:02:39Z; 5 of 21 cells at 07:50Z), with the measured wall/CPU ratio reported beside it (LOG
  2026-10-28). Contention is large: B-rand dev-008 wall 5210 s for CPU 2676 s, B-sym dev-008 wall
  4077 s for CPU 1072 s. When run B exits, the next session may stop it (`STOP`), wait for the
  unit in flight, delete `STOP`, and resume with `.cache/mini-cpu-run.sh 2`. Caveat: all three mini truths are
  in the far stratum.
  2026-10-28 (session 9): at 09:35Z kept at 1 worker (7 GB available with the slow tier running;
  a second worker peaks at ~2 GB; AUTOPILOT's 6 GB floor). At 09:48Z, slow tier done and 8 GB
  available: `.cache/mini-cpu-to2.sh` (window `to2` of `sci-minicpu`) wrote STOP and, when the unit
  in flight ends (the run log's next `exit:` line), deletes STOP and relaunches with 2 workers if
  MemAvailable ≥ 8 GiB at that moment, else 1; it logs `to2: relaunch with N workers` in
  `.cache/mini-cpu-run.log`. Next session: check that line and that the run is going. A 2-worker
  run also gives the 2-worker byte check against run A (`store_compare.py`).
  2026-10-28 (session 10): relaunched on 2 workers 09:51:54Z. `scripts/project_runtime.py`
  merged (39d088f; LOG 2026-10-28). Interim on 20 of 21 cells (planted-hint dev-017 still running
  at 13:00Z): CPU-only lower bound 53.8 h at mean unit cost, 65.1 h at max, B-sym@10F not
  measured and left out; x 2.06 measured wall/CPU, 110.6 h. Above 48 h, so this is heading for the
  stop point; record it only from the finished store (JOURNAL session 10).
  Final (run exited 13:00:53Z, 21 cells): CPU lower bound 53.3 h at mean, 65.1 h at max, x 2.00
  wall/CPU 106.8 h; B-sym@10F left out (61.4 h CPU with 10x B-sym assumed). Stop point pushed to
  the owner. 2-worker byte check: `store_compare.py .cache/mini-a .cache/mini-cpu` `identical
  cells: 21 ... differing: 0` (14 of its 21 cells ran on 2 workers).
  2026-10-28 (session 11): red-teamed. Contended CPU is not a lower bound on this 2-core,
  4-thread box: 12 of 21 units ran faster in mini-a or mini-b than mini-cpu's CPU time.
  Uncontended, the plan is about 48.3 h before B-sym@10F, so it is at the line, not clearly
  over (LOG and JOURNAL corrections). Measuring near, mid and B-sym@10F: `.cache/strata`
  (tmux `sci-strata`, resume `bash .cache/strata-run.sh 2`), then `project_runtime.py` on both
  stores. The owner's choice stands.
  Session 11, later: the strata run finished (15 recorded, 0 failed; STORE OK, GATE PASS; S3
  `p3-linux/strata-b953390/`). `project_runtime.py` (pools stores, `--by-stratum`, `--wall-from`)
  is merged. Red-teamed figure: quiet machine about 48 h (40-60 h) including LLM scoring; this
  week's load about 90 h (75-115 h). The lane leans to dm-devbox after the merges (JOURNAL
  session 11 stop point update). Still the owner's choice.
  2026-10-28 (session 12): `--by-link` and `--assume-link` merged (5aff197; LOG). With exp
  costed as softplus they reproduce the red team's 44.7 h CPU and 41.5 h uncontended; plain
  `--by-link` gives 43.4 h and 40.3 h. The largest open term is B-sym@10F on the five far truths.
  Measuring it: `.cache/far10f` (tmux `far10f`, worktree `far10f` at 87b406f, `--ids
  dev-000,dev-003 --systems B-sym@10F --b10f-truths 2 --no-llm --workers 2`, started 17:30:36Z;
  the smoke store's dev-000 unit took 17411.8 s wall). Resume: `bash .cache/far10f-run.sh 2`.
  Next session: when it ends, run `store_check.py`, rerun `project_runtime.py` on all three stores,
  copy the store to S3 and add the ledger line.
  2026-10-28 (session 13): far10f done (2 recorded, STORE OK; S3 `p3-linux/far10f-87b406f/`).
  Far B-sym@10F costs 14255.4 s CPU per unit (near 2121.5 s). Red-teamed figure: quiet machine,
  whole pool with LLM scoring, about 51 h (45-58 h), just over the line; this week's load about
  85-95 h (floor 78 h). `.cache/s13/projection-*.txt`, JOURNAL session 13. Still the owner's choice.
- [~] **Full pilot** (pilot-runner). 2026-10-29 (session 19): the non-LLM half is running since
  11:31:27Z: `.cache/pilot-run.sh 1` (`--no-llm`, plan 310 pool jobs) at v2 900e66a in the detached
  worktree `.claude/worktrees/pilot` (dev split copied in, sha256 checked), user unit
  `sci-pilot-baselines`, store `.cache/pilot`, log `.cache/pilot-run.log`, memory
  `.cache/pilot-mem.csv`. It runs 1 worker because of the 6 GB floor. Go to 2 when MemAvailable is 8 GiB or
  more (STOP, wait for the units in flight, delete STOP, `systemd-run --user --unit=sci-pilot-baselines
  --setenv=PATH=$PATH bash .cache/pilot-run.sh 2`). The LLM half is running since 12:09:29Z:
  `.cache/pilot-llm-run.sh 1` (`--no-baselines --llm-concurrency 1 --workers 1`, plan 240 LLM units) at
  the same commit in the worktree `.claude/worktrees/pilot-llm`, store `.cache/pilot-llm`, tmux `-L sciagent`
  session `sci-pilot-llm` (inside the lane unit, so braked). Raise to concurrency 2 only if no rate limit has
  appeared. On a usage-limit error: `touch .cache/pilot-llm/STOP` and record it. For the report: ~~merge
  `pilot-multiroot` (6b970b6, `scripts/store_merge.py`) into v2~~ (merged 2026-10-29 session 22 with the red
  team's fixes, LOG 2026-10-29), then `store_merge.py .cache/pilot
  .cache/pilot-llm --out <new>` and run the report there (LOG 2026-10-29, two stores).
  2026-10-29 (session 20): at 13:34Z 7 baseline units `ok` (the last at 12:53Z, so one unit in flight),
  10 LLM sessions submitted and 7 scored `ok`, no failure or rate-limit line, MemAvailable 8.68 GiB. The move
  to 2 workers is automated: `.cache/pilot-to2.sh` in user unit `sci-pilot-to2` wrote STOP at 13:35:36Z,
  waits for the unit in flight (the run log's next `exit:` line and `sci-pilot-baselines` inactive), deletes
  STOP and relaunches `pilot-run.sh` in `sci-pilot-baselines` with 2 workers if MemAvailable ≥ 8 GiB then,
  else 1; it logs `to2: relaunch with N workers` in `.cache/pilot-run.log`. Next session: check that line
  and that the unit is running. LLM concurrency stays 1: 10 sessions in 87 min is about 35 h for 240 units,
  shorter than the baselines' ~51 h at 2 workers, so a second session would only spend Max usage faster.
  2026-10-29 (session 23): LLM half 19 scored `ok` by 19:05Z. Three `Control request timeout: initialize` failures
  18:36-18:51Z (retryable; load ~20; five-hour usage 76%, no brake) paused launches 900 s; launches resumed 19:06Z. AG-o
  dev-008 s1 ended `wall_time` (4,645 s, turns=0 out_tokens=0 logged). Baselines: still 7 of 310 at 19:10Z.
  Original item: `nice -n 10 uv run python scripts/pilot.py --root
  .cache/pilot --truths 20 --seeds 1,2,3 --b10f-truths 10 --workers 2 --llm-concurrency 1` in
  tmux, exactly the SPEC §7 P3 plan: 20 dev truths x 3 seeds; AG-c-S and AG-o-S, named and
  anonymised, live under Max; B-lib, B-rand, B-sym, B-sparse, B-np, ORACLE, planted-hint;
  B-sym@10F on seed 1 of dev-000 to dev-009 as declared. Raise LLM concurrency to 2 only if
  no rate limit has appeared. Done when the coverage table shows every planned unit scored or
  failed with a reason.
- [ ] **Regenerate PILOT.md** (analyst) with `scripts/pilot_report.py` from the Linux store;
  rewrite the notes block for the Linux run (the Windows notes stay in git history; say so).
  Red team before the commit. Done when committed with the red team's findings addressed.
  Note for the report (session 20; LOG 2026-10-29): an AG-o record whose own sandbox code prints wall time
  fails `replay_check` under the pilot's code (dev-003 s1, step 51). Count such records apart from harness
  replay defects, and run `replay_check` at 900e66a.
- [!] **P3 go/no-go read: stop point.** SPEC §7.1 under the re-read rules of P4-PROPOSAL §2.4;
  the owner reads a few AG-o transcripts in full. Prepare the evidence and transcript picks,
  push the owner, wait. Done when the owner has decided.

## P4 preparation (after a GO)

- [ ] **Integrate `origin/p4-deltas` after the Linux pilot.** Pushed from the Windows PC on
  2026-10-27 11:45 UTC: P4 work packages 4 to 12 plus core.reductions fixes, based on ea055be
  (older v2). Merge into v2 only after the pilot's numbers are produced (it changes the harness:
  HARNESS_VERSION harness/2, session keys), re-run fast and slow tiers and the determinism check
  on dm-devbox. The `p3-fit4` tag was not pushed; not needed unless an old record cites it.
  Also with it (LOG 2026-10-29): `investigation/policy.py:make_policy` builds `EIGPolicy` and
  `FSDPolicy` from `seed` and `replicates` only; pass the arm settings' `max_events` and EIG's
  `weighting` through, test first.
  2026-10-29 (session 19): rehearsal merge done on local branch `p4-rehearsal` (worktree
  `.claude/worktrees/p4-rehearsal`, from v2 84e7a2a; not merged into v2, not pushed). 3642c3c merge (8 files,
  17 hunks; v2's fixes kept, p4 added), then 0872a7f, 1d8a7ec, 6561868 and ce4c167 (merge fixes) and f3da328
  (`make_policy`: `PolicySettings` gains `max_events` and `weighting` at their defaults, and the session key
  includes them; test `tests/campaign/test_policy_arm_settings_agcs.py`, 7 passed on the lead's rerun).
  Engineer's checks: ruff clean, mypy `Success: no issues found in 285 source files`, fast tier `3525 passed,
  3 warnings`. The slow tier was not run (only `test_replay_check.py`, 4 passed). Before the real merge:
  (1) confirm the legacy key, so that harness/1 = v2's pre-merge text with the AG-o sandbox block (the pilot's AG-o sessions
  stay rescorable); (2) run `replay_check` and the audit on the Linux pilot's harness/1 records under the
  merged code; (3) reconcile p4's LOG entries (its open decisions and 6,000-cap draft are settled in v2's LOG);
  (4) strip the engineer's `Co-Authored-By` trailers if these commits are replayed; (5) run the slow tier.
  Also: the pinned AG-c EIG/FSD session addresses move even at defaults (no store holds one).
  2026-10-29 (session 20): points (1)-(3) done on `p4-rehearsal` (engineer; not merged, not pushed), all on a
  13:37Z copy of `.cache/pilot-llm` (10 sessions):
  (1) the merged code re-derives the 17 cell addresses (`match 17 differ 0`), and the harness/1 texts are
  byte-identical to 900e66a's; pinned by `tests/campaign/test_pilot_harness1_cells.py` (0de8220; lead's rerun
  7 of 7 pass).
  (2) `store_check` and `session_check` pass, and the rescored cells are identical. `replay_check` passes 0 of
  10 under the merged code (harness/2 tools, by design), so harness/1 records are replayed at 900e66a (LOG
  2026-10-29); add the version-mismatch refusal to `replay_check`, test first.
  (3) dated notes in the merged LOG (85c586a, 0 lines removed).
  Left: (4) trailers (none on 0de8220 or 85c586a), (5) the slow tier, rerunning (1)-(2) on the finished pilot
  store, and the anon-arm digests against stored cells.
  2026-10-29 (session 21): the `replay_check` refusal is done on `p4-rehearsal` 346676a (engineer, test first; not
  merged, not pushed): a record whose config `harness_version` is not the running one is REFUSED (both versions
  named; harness/1 at 900e66a) and counted apart, exit 3 when nothing failed; a record naming no version is a
  FAIL. On the 11 harness/1 records of `.cache/pilot-llm`: `refused: 11`, exit 3. Lead's rerun of
  `tests/campaign/test_replay_check.py -m "not slow"`: 14 passed; ruff clean (LOG 2026-10-29).
- [x] 2026-10-27 **Cross-check the rebuilt defect-hunt checks against the originals** (7406810,
  LOG 2026-10-27): ported test-first `check_budgets`, `check_sessions` (`scripts/session_check.py`),
  `KEY_FIELDS` and the beats-truth flag (`hunt_gap.py --split`; making it fail the gate is a
  proposal, veto window ends 2026-10-28T13:00Z). Coverage table in JOURNAL 2026-10-27.
- [x] 2026-10-29 **Hunt-check leftovers** (from the cross-check; all five done): (a) ~~beats-truth red team~~
  done 2026-10-27: adopt at +0.01 with a digest check (merged 4677b57; LOG 2026-10-27),
  adoption after its veto window; (b) ~~peak worker memory~~ done 2026-10-27:
  `scripts/mem_sampler.py` (`/proc` VmHWM per pool worker, STOP brake below 2 GB available;
  run it beside the mini-pilot with `--pid`),
  measured slowdown on dm-devbox still to decide;
  (c) ~~store-level replay check~~ done 2026-10-27: `scripts/replay_check.py` (98e3aa4;
  smoke store 4 of 4 PASS);
  (d) ~~`rescore_guard` extras~~ merged de2e8a3 2026-10-28 (session 12) (strip the CLI from PATH, patch the SDK entry points);
  2026-10-28 (session 9): built as `tests/campaign/test_rescore_guard.py` (test only; LOG
  2026-10-28): local branch `rescore-guard` fcc45df on v2 (10 passed), and `guard-on-rehearsal`
  5ef1db7 = the same on `merge-rehearsal` plus `fingerprint=FINGERPRINT` (10 passed, mypy clean).
  Next: merge sixth, after fingerprint-guard, from `guard-on-rehearsal`'s two commits (the v2
  version fails on top of fingerprint-guard: `Campaign` needs `fingerprint`);
  (e) ~~explicit hash-seed baseline pair and report-on-a-copy~~ done 2026-10-29, merged 9b1f92c
  (session 14; LOG 2026-10-28): the pair passed at once; report-on-a-copy found readers opening
  the store read-write (a lost trigger was put back), fixed with `ResultStore(root,
  read_only=True)`; red team: merge with one fix (`check_store` on a missing `details/`), made.
- [ ] **Decisions open since 2026-10-06** (HANDOFF-2026-10-06): the prompt variant freeze
  (Δ6), EIG weights (Δ11), an event cap for FSD, hand-written address text (F5), the
  review items, and decision 6 of HANDOFF-2026-10-09 (C5 random-arm seeding: `policy_seed` has
  no truth id). Owner's rule (2026-10-27): the lane writes a recommendation for each with its
  reasoning, has the red-team agent review it, records it in LOG.md with `veto window ends:
  <UTC>` 24 hours ahead, pushes the owner a summary, and adopts it after the window unless a
  `VETO` line appears in docs/v2/VETO.md. **Superseded 2026-10-29 09:12Z** (INBOX; LOG
  2026-10-29): no veto window; red-team once (again only if blocking), record in LOG, adopt at
  once, notify the control room. Δ6 still needs the owner's explicit A/B/C choice. Done when each
  is adopted or vetoed and logged.
  Progress: decision 6 (C5 seeding) proposed and red-teamed 2026-10-27 (LOG, veto window ends
  2026-10-28T16:05Z); adopted 2026-10-28 16:06Z with no veto (LOG); implement with the p4-deltas integration.
  F5 (hand-written address text) proposed and red-teamed 2026-10-27 (LOG, veto window ends
  2026-10-28T19:15Z): the guard above before the pilot; derived address text with the p4-deltas
  integration. Δ11 (EIG weights) proposed 2026-10-27 after the red team rejected the first
  draft's tie rule (LOG, veto window ends 2026-10-28T21:40Z). Drafts of the FSD event cap and the
  five review items are in `.cache/drafts/`; review items proposed (LOG, window ends 2026-10-28T21:45Z); Δ6: `link-neutral` not adopted, a link-balanced variant recommended pending its own red team (LOG); 2026-10-28: link-balanced red-teamed, **needs the owner's explicit choice** (A/B/C; no veto window, since (C) sets aside rule (A) after the numbers; LOG 2026-10-28); FSD cap: draft not adopted, measurement first (LOG). 2026-10-28 (session 13): measurement prepared (LOG 2026-10-28); pass 1 running in `tmux -L sciagent` `fsd-dist`, output `.cache/drafts/fsd-dist/dist-s1-d0.jsonl`; then summary, pass 2, a cap draft and its red team. 2026-10-29 (session 14): pass 1 done (1,280 lines, exit 0; `.cache/drafts/fsd-dist/summary-pass1.txt`): 6 of 160 WH (truth, design) cells stop at 15,000 on every replicate, no GLM stop, 0.31 CPU h; pass 2 (all 16 replicates) running in `fsd-dist` since 00:04Z, about 1 CPU h. Next: summary on the full file, then the cap draft and its red team. 2026-10-29 (session 15): pass 2 done (5,120 lines, 1.105 CPU h sim) and truth side on replicates 0-1 (348 runs); the drop-rule draft (`.cache/drafts/fsd-cap-draft2.md`) was red-teamed **do not adopt** (the world truncates at the cap, it does not return None) and is withdrawn; the cap stays 15,000; the at-stop rule stays open (LOG 2026-10-29). Truncation option measured the same session (`.cache/drafts/fsd-trunc/`, 1.36 CPU h, unreviewed): it changes the decision-0 pick in 1 of 28 (truth, policy) cases; He² saturates at 1.000 and menu-order ties decide, with FSD's `choose` design-first and telling diagnostic-first (JOURNAL session 15). Next: red-team that measurement, then a theorist draft on saturation and the tie order before any proposal. 2026-10-29 (session 16): red-teamed (`.cache/drafts/fsd-trunc/redteam-s16.md`): numbers reproduce (0 mismatches, rerun byte-identical) but "1 of 28 picks change" is near-empty evidence (11 of 28 have no truncation, 9 more are forced by the cap via `mean_rate`, the one change is a 3e-16 rounding tie), and the regime where (a) and (c) differ most (both sides cap) was never sampled; session 15's "barely moves picks" is withdrawn. Theorist draft (`.cache/drafts/fsd-ties/draft.md`, unreviewed): He² is exactly 1 when D = -ln(1-He²) ≥ 54 ln 2 (atom, >~17 sd, or variance floor); recommends breaking ties by D in both `choose` and `Telling.from_table`, conditional on rule (c) for both; found control 5 as built compares truthFSD under (a) with telling under (b), agreeing on the diagnostic in 3 of 14 truths. Next: (1) red-team the ties draft; (2) a positive control where (a) and (c) must differ (a capping parametric side, e.g. B-sym's real top fit), truthFSD at 16 truth replicates, and the WH truncating-mode content checks the red team listed; then a proposal. Session 16, later: the ties draft red-teamed **not ready** (`.cache/drafts/fsd-ties/redteam-s16.md`): rule (c) for telling would put d* on fully truncated truth designs on 9 of 14 truths, ranking by D under (c) rewards cap artefacts, the floor source is misattributed, D needs a new log-space function (instrument change, test first; `hellinger2` unchanged). Three at-stop rules are in play: truthFSD (a), telling (b), world (c). Next: settle the rule question first with (c) treated explicitly and truncated designs excluded or flagged for telling; a positive control with a known d*; only then tie-breaking. Session 16, later: rule draft (`.cache/drafts/fsd-rule/draft.md`, unreviewed): (c) for every simulation; telling and truthFSD leave out of the argmax any design where the truth or member truncates or stops on any replicate (K4), and report the share of runs on such designs beside the telling rate; FSD keeps all designs. Proxy (decision 0, R = 4): telling d* on a truth-truncated design 0 of 14 (9 of 14 under (c) everywhere), control-5 agreement 11 of 14 (3 of 14 today). Red-teamed the same session: **not ready** (`.cache/drafts/fsd-rule/redteam-s16.md`): K4 redefines SPEC §4.2's d* (a SPEC change, so a stop point) and makes control 5 nearly circular; "any replicate" exclusion on each side's own streams (R = 16 vs 32) excludes different designs; the FSD no-exclusion argument is selection (dev-005 saved by menu order); the listed controls do not separate the candidates. Next: an R-independent exclusion computed once and shared, or a design that keeps exploding designs as evidence; controls with preset pass criteria (known d*; WH caps but truth matches member); control 5 at real R; any SPEC §4.2 change goes to the owner. 2026-10-29 (session 17): SPEC §4.2 question pushed to the owner (JOURNAL stop point, options 1/2/3; lane leans 2). Control criteria for A1 (planted d*) and B (dev-005 shape) written and red-teamed six rounds to v6, then v7 fixing the last blocking item (`.cache/drafts/fsd-controls/criteria.md` sha256 9c15e47e…, v7 changes unreviewed; A2 deferred; ceiling 7.6 CPU h); not frozen. Next: engineer writes `controls.py` + stub tests to `engineer-contract.md`, code red team, then freeze (preregister skill, 24 h veto window), then calibrate. 2026-10-29 (session 18): v7 red-teamed **not ready** (s18: freeze chain gameable through merges and deleted LEDGER rows); v8 fixes it (calibrate pushes D before D0 is readable; private-ref fetch; branch protection gate H1); `controls.py`, `ctl_gate.py`, `test_controls.py` written to v8, joint red team s18b **ready to freeze**, its non-blocking fixes made (criteria v8.1 963d1495…, controls.py c0895289…, 90 tests, 16 of 16 mutants caught). No veto window any more (LOG 2026-10-29). **Freeze blocked on the owner turning on GitHub branch protection for v2** (gate H1; command sent to the control room). Next: when `gh api repos/ebinezer-rajaram/sciagent/branches/v2 --jq .protected` is `true`, freeze per criteria §5 (create cpu-total.txt, commit F with `git add -f` and both hashes, LEDGER row with the ceiling, push, notify the control room), then calibrate A1 and B in their own `systemd-run --user` units. 2026-10-29 (session 19): the owner
  answered (INBOX 11:27Z): Δ6 **(A) `link-neutral` adopted** (with the p4-deltas integration); SPEC §4.2
  **option 2 adopted**, with the SPEC amended and the `Telling` code to follow, test first, only after A1 and B
  pass. v2 is protected. **F₀ 8405509 frozen**, LEDGER row 900e66a (ceiling 7.6 CPU h). **Calibrate A1 and B
  running** since 11:29Z (units `sci-fsdctl-A1-calibrate` and `sci-fsdctl-B-calibrate`; status with
  `systemctl --user status`, logs with `journalctl --user -u`). Each pushes its own D commit
  (`fsd-controls D0 <C> under 8405509…`) and then writes `D0-<C>.json`. Next: after a pull brings D, run the
  test phase per criteria §5 with `--d0-commit <D>`, then score, then commit the score files.
  2026-10-29 (session 20): A1's D is c0d1b8f (calibrate exited 13:23:15Z; D0-ctlA1.json sha256 2fe5e7e4… on
  disk equals D's blob). B's calibrate still running at 13:34Z; ledger total 13,450 CPU s (3.74 of the 7.6
  CPU h ceiling). Both test phases start automatically: `.cache/fsdctl-test-launch.sh` in user unit
  `sci-fsdctl-watch` waits for B's calibrate unit to end, takes B's D from its journal line, checks each D's
  subject names F₀ and each D0 on disk equals its D blob, then starts `sci-fsdctl-A1-test` and
  `sci-fsdctl-B-test` with the §7 command; log `.cache/fsdctl-test-launch.log`. If B ends without a D line it
  starts nothing. Expect the 7.6 CPU h ceiling to fire during test (exit 11, `STOP` in $D); resume per §6 with
  a pushed `fsd-controls ceiling <c> CPU h` row, then remove STOP and rerun test. Then score per §5 step 9.
  Session 20, later: B's calibrate exited 13:57Z (2h 20min CPU, systemd) and pushed D = 8d079f4 (D0-ctlB.json
  da7c6de1…, equal to D's blob). The watcher's checks passed, and both test units started 13:57:49Z and wrote
  runs files. At 14:18Z: ledger total 16,863 CPU s (4.68 h), about 100 CPU s a minute, so the 7.6 h ceiling
  (27,360 s) should fire around 16:00Z. Next: if `$D/STOP` exists, push the ceiling row and resume the test
  units, each with its own D: A1 c0d1b8f123fde746e9cf8497b2c9a919a479a4a2, B
  8d079f47cbd5e3a6b82547c32b796f6cd1039fd5.
  2026-10-29 (session 21): test runs at ~706 (A1) and ~810 (B) CPU s per seed, about 1.9x cost5, so the projected
  total is ~10.4 CPU h. Row `fsd-controls ceiling 12.0 CPU h` pushed (b6a63d2). `.cache/fsdctl-resume.sh` in
  unit `sci-fsdctl-resume` restarts each test unit that exits 11 once STOP is present, the row is on origin/v2 and
  the total is under 12 h (log lines `resume:` in `.cache/fsdctl-test-launch.log`). Tests should end ~17:20Z.
  Next: check both units finished (exit 0, 38,401 lines per runs file: header + 15 x 2,560), then score each
  control (§5 step 9) and commit the score files.
  2026-10-29 (session 22): the ceiling fired at 17:04:58Z and `fsdctl-resume.sh` restarted both tests at 17:05Z.
  `.cache/fsdctl-score-launch.sh` in unit `sci-fsdctl-score` starts the score phase for each control whose test exits 0
  (log `score:` lines in `.cache/fsdctl-test-launch.log`). Next: commit the score files, S3 copy, LEDGER row, §8 read, red team.
  2026-10-29 (session 23): scored 18:57Z (exit 0 both; files 7386536; S3 `fsd-controls/8405509/`; LEDGER 9.19 CPU h).
  **A1 fails under every rule**: exclusion rules (i) 12/15, (ii) 78/120 < 84, (iii) p 0.035; D0's pass_ii was 0.99999775.
  B (size rung 3; θ₋ rungs unbuildable) as predicted: Fx passes with K0/K3a/K3h/K4/K5; Fa, Fc, Fz fail B1. Red team: read
  stands (LOG 2026-10-29). By §8 no candidate is ready: He² resolution first, thresholds unchanged, no rerun. **Option 2's
  `Telling` change does not proceed; returned to the owner** (stop point, JOURNAL session 23). Open lead, untested: calibrate
  fixes diagnostic arguments on seed 0, test per seed (controls.py:1363/1396 vs 1534/1913).
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
- [x] 2026-10-29 **Fetch the QTM catalogue** (`scripts/fetch_qtm.py`, for P5) so its three skipped
  acceptance tests run. Low priority. Session 15: fetched into `data/qtm` (gitignored), exit 0;
  `--verify` both files `ok` (201061306 and 99744391 bytes, pinned sha256);
  `tests/acceptance/test_qtm_ingestion.py` `20 passed`, no skips.

## Done

- [x] 2026-10-27 **Platform check on dm-devbox.** `uv sync`; fast tier, mypy and ruff green
  after re-pinning the six fit/4 reference digests to dm-devbox (LOG 2026-10-27); the
  determinism child gave byte-identical output on two runs.
- [x] 2026-10-27 **Lane set-up.** CLAUDE.md (platform, money, lane), AUTOPILOT.md, this queue,
  JOURNAL.md, LEDGER.csv and the five project agents.
- [x] 2026-10-27 **Dev split on Linux.** Regenerated from b5e2b87 (fit/4): the same 20 truths
  as PILOT.md's table, sha256 `604acec5…`, adopted at `data/v2/truths/dev` (LOG 2026-10-27).

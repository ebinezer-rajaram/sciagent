# sciagent

Research framework for evaluating constrained agentic scientific investigation
under model misspecification. Full design: `docs/SPEC.md`. Read it before
non-trivial work. It is frozen; do not propose architectural changes.

## What this is

Environments are compositional executable programmes. Defects are typed
structural edits to those programmes. A research system investigates which
edit was applied. We evaluate LLM-based systems against conventional
baselines on scenarios with known ground truth.

## Non-negotiable invariants

Violating any of these is a bug regardless of tests passing.

1. **`sciagent/` never imports from `environments/`.** The framework is
   domain-independent. There is a test for this. Do not weaken it.
2. **The framework writes numbers; agents write structure.** No code path
   reachable from an agent may set `plausibility`, a posterior value, a
   metric definition, or a score. Enforce with runtime assertions, not
   comments.
3. **Bit-exact determinism.** Same seed + same config + same version =>
   byte-identical output. All randomness through explicitly passed seeded
   generators. Never `random.` or bare `np.random.`. No dict/set iteration
   order dependence in anything affecting output.
4. **The registry is append-only.** No update or delete path exists. Content-
   addressed by hash over (env version, config, data version, metric version,
   seed).
5. **Acceptance tests are the contract.** A1-A24 in `docs/SPEC.md` §6. A
   subsystem is not done until its tests pass. Do not proceed past a gate.
6. **No LLM code until backlog item 12.** ~~Live~~ **Discharged at a380a21.**
   Items 2-11 are pure conventional machinery, and they were built before the
   agent existed, which is what this invariant was for: agent performance can
   never be confounded with framework immaturity. It is kept here rather than
   deleted because the *reason* still governs — anything that would move
   evaluation apparatus after the agent that is scored by it reopens the
   confound. `systems/llm/`, `systems/hybrid.py` and `systems/ablation.py` are
   in bounds; a new gate written after the system it grades is not.

## Stack

- Python 3.12+, `uv` for dependencies (`uv add`, `uv run`)
- `pytest` + `hypothesis` for property-based testing
- `numpy`, `scipy`; `numba` only if profiling justifies it. `anthropic` reaches
  the network from one module and never during a replay
- Strict typing: `mypy` clean — it takes no arguments, since `pyproject.toml`
  sets `strict` and the file set. `from __future__ import annotations`.
  **`mypy` is the gate; `pyright` is advisory.** The pyright LSP plugin supplies
  inline diagnostics and agreed with `mypy` exactly when both were run over
  `registry/` and `verify/` — 15 files, zero findings each. If they ever
  disagree, `mypy` decides; never edit code to satisfy a pyright-only complaint.
- `@dataclass(frozen=True)` for all value types. No mutable global state

## Conventions

- Small modules, one concept each. Prefer explicit over clever.
- Type aliases (`ComponentId`, `Seed`, `Probability`) via `NewType`, not bare `str`/`float`.
- Every public function gets a docstring stating what it guarantees, not what it does.
- Errors are typed exceptions from `sciagent.core.errors`, never bare `ValueError`.
- No I/O in `core/`. Pure functions only.
- **Acceptance tests are named for their criterion**: `test_a7_...` in a class
  `TestA7MonteCarloError`. This naming is load-bearing — `scripts/status.py`
  derives gate coverage from it. A test not named this way is invisible to the
  status report.

## Working defaults

General habits, not project facts. They live in the repository rather than in a
personal config file because cloud sessions clone the repo and see nothing from
`~/.claude/` — see **Cloud sessions** below.

- Show evidence, not assertions. When you say something works, include the
  command you ran and its output.
- Fix root causes. Never suppress an error to make a check pass: no bare
  `except`, no `# type: ignore`, no `# noqa`, no skipped tests.
- **If a task will touch more than three files, plan first and get the plan
  approved before editing anything.** Gate on the *estimate*, made before you
  start — not on the moment the sprawl becomes obvious, which is a gate that
  fires after licensing exactly the edits it existed to stop. This binds `/next`
  as much as ad-hoc work: a §11 backlog row authorises the *work*, not the edits,
  and the A-test is an edit like any other.
- If you are more than 50% unsure what was meant, ask one specific question
  rather than guessing and building the wrong thing.
- Prefer editing an existing file over creating a new one.
- Never commit unless asked, and never push without asking. Invoking `/ship`
  is that asking.
- Platform depends on the surface, so check rather than assume — `uname -s`
  settles it. Local sessions run on Windows through Git Bash: prefer forward
  slashes and do not assume GNU coreutils flags exist. Cloud sessions run
  Ubuntu 24.04, where neither caution applies.
- **Do not prefix shell commands with `cd <project dir> &&`.** The working
  directory is already set. Measured across 50 transcripts, 1,141 of 1,402
  commands carried this prefix and none needed it.
- **Run the suite as `uv run pytest -n 4 --dist loadfile`, and background it
  rather than blocking.** Measured 2026-08-16 on this desktop (12 logical
  processors, 16 GB), warm tables:

  | invocation | wall clock |
  |---|---|
  | serial | 262.44s |
  | `-n 4 --dist loadfile` | **151.30s** |
  | `-n 4` (default `load`) | 135.88s–282.96s |
  | `-n 6` | 253.70s |
  | `-n auto` (12) | 279.16s, **3 failed** on `MemoryError` |

  **`--dist loadfile` is not a tuning knob — it is the thing that makes this
  correct.** Tests in one module share simulated table rows through the gate
  table, so splitting a module across workers makes each worker re-simulate at
  2000 replicates what a sibling already did.
  `test_the_floor_never_exceeds_what_greedy_achieves[S3]` costs **0.00s** serially
  and **118.14s** under default `load`. That is duplicated work, not
  rescheduling, and it is why more workers ran *slower*. `loadfile` keeps a
  module on one worker and the duplication disappears.

  Memory binds before cores here, so do not "improve" this to `-n auto`. `-n 4`
  is also what `-n auto` resolves to on the 4 vCPU cloud VM. It does **not**
  belong in `addopts`: worker startup takes a single file from 1.76s to 3.41s,
  and iterating on one file is the commoner act. Do not read a passed count as a
  total — a green run reports `1109 passed, 7 skipped` out of 1116 collected.
  Quote the invocation beside any timing you record: every figure in
  `DECISIONS.md` before this date is serial, and the three are not comparable.
- **Never run a subagent alongside the suite.** Measured 2026-08-16: four
  read-only `decisions-sweeper` agents took a `-n 4` run from ~163s to **183.25s
  and 185.37s**, about 12–15%, while alive for only ~38s of it. "Read-only, so no
  CPU work" is false as a premise even though the rule it was offered for is
  right. The **30m37s** figure often quoted for this is not a local measurement:
  it was taken on the 4 vCPU cloud VM against a **7m50s** baseline on that same
  VM (`1c01fb1`), and pairing it with the desktop's idle figure splices two
  machines. `mypy` is 1.9s and needs no such care. Before repeating the suite, ask
  `bash .claude/hooks/suite-freshness.sh check`: it reports whether a full run
  already passed on a byte-identical tree, and `/next` and `/ship` between them
  used to run it three times per item. That attribution is historical: the runs
  are `/next`'s and `/preflight`'s now, and after the split `/ship` never runs
  the suite at all. Pin with `begin` before starting pytest
  and `record` after — `record` refuses if a tracked file moved mid-run, because
  such a run describes no single state of the tree. Worktrees make that rare
  rather than routine, but it still catches your own edits during a backgrounded
  run, which is exactly what backgrounding invites.
- You decide when to delegate to subagents; do not ask each time. Delegate for
  coverage: wide sweeps, locating call sites, enumerating across many files,
  and independently verifying a claim you have already made. Do it yourself for
  comprehension: how a module works, whether an invariant holds. The test is
  whether the result could be checked with a grep — if checking it means
  redoing the work, do not delegate. Say what you delegated and what came back.
  The one exception is work that does not fit one context at all: reading 360KB
  of `DECISIONS.md` is not grep-checkable either, but the alternative there is
  not doing it rather than doing it yourself, so the test does not apply.
- For the verification case specifically, use the `evidence-checker` subagent.
  It has not seen your reasoning, which is the whole point: you are the worst
  judge of a claim you just made.
- **Launch in parallel only where the work is genuinely independent.** A list is
  not fan-out: if three items touch one file, that is a sequence wearing a
  list's clothing. Three places here clear the bar — `/preflight` step 2's four
  invariant lenses plus `/code-review`, `/recall`'s four `decisions-sweeper`
  slices, and triaging a suite run that came back with more than about three
  unrelated failures, one agent per failure. `/next`'s A-test lens joins them
  only when an item names more than one gate; on the current all-untracked
  backlog it is one agent and not fan-out at all. The contention is **CPU**, so
  what it forbids is a subagent running *alongside the suite* — it does not
  forbid concurrent read-only agents, which is why five at once in `/preflight`
  step 2 is affordable. Do not read "affordable" as free: four such agents cost
  a `-n 4` suite 12–15% (measured 2026-08-16), so the exemption is for agents
  running beside *each other*, never beside pytest. The triage case is safe for
  a second reason worth stating separately: pytest has already exited.
- **Read-only review is authorised by the work; irreversible action is
  authorised only by the user.** That is what licenses `/preflight`'s subagents
  without asking, including when you invoked `/preflight` yourself: `/code-review`,
  and when the diff touches `src/sciagent/` or an agent-reachable path,
  `invariant-auditor` as its four lenses — *before* committing. That path is
  spelled from the repository root deliberately: there is no top-level
  `sciagent/`, and a condition naming a directory that does not exist never
  fires. Shipping
  136550a without them cost two defects that reached `main`: a hook fix verified
  against the wrong invocation form, and a cache override in an untracked file
  no worktree could read. Both were found by the review minutes after the push,
  and either would have been caught before it.
- **`/preflight` may run on your own initiative; `/ship` never may.** `/preflight`
  scopes, verifies and reviews, and lands nothing — run it at the end of `/next`
  without asking, and whenever else it would help. `/ship` commits, merges and
  pushes, and the user's invocation of it *is* the authorisation. Do not supply
  that yourself on the strength of your own confidence that the work is
  finished: 136550a's own verification claimed to have checked both defects it
  shipped.
- **Grade a subagent's model by what a false negative costs, not by what the
  task looks like.** An agent that misses something reports "nothing found",
  which is indistinguishable from a clean sweep — so cheapness is only
  affordable where you could catch the miss yourself. `haiku` for work whose
  answer a grep would recover (`invariant-auditor` lens 4 is the type case);
  `sonnet` for bounded judgement or extraction from prose; otherwise omit the
  model and inherit the session, which is how the expensive lenses get the
  strong model. Never pin opus explicitly.

When compacting, preserve the list of modified files, the commands used to
verify the work, and any decisions the user pushed back on.

## Concurrent local sessions

**Work in a git worktree, one per session.** Several local sessions run against
this repository at once. Sharing one working tree meant they edited each other's
files, and the cost was not theoretical: a suite that ran 18:00–18:07 on
2026-08-15 was certified green against a tree a second session had changed at
18:05:56. It also blocked real work — `DECISIONS.md` records a one-line fix left
unmade "because another session was holding the file", and `pytest-xdist` (a
measured threefold win) left uninstalled because `uv add` writes two files
another session was committing to.

Use `EnterWorktree` at the start of a session that will edit anything. It
creates the tree under `.claude/worktrees/`, which is gitignored — so the parent
`git status` stays clean, VS Code's search skips it, and the explorer can still
browse it.

`/ship` gets the branch back onto `main`, but **it cannot finish from inside the
worktree and will stop to ask.** Every route to `main` needs `git -C` against the
shared checkout, which the harness refuses from a worktree-isolated session, and
`ExitWorktree` is reserved for you rather than callable by the agent. So a
worktree ship ends with a question — approve leaving the worktree (`keep`), after
which the merge and push run in the main tree. Worth knowing before it happens,
rather than at the blocked step.

Two more things follow that are easy to get wrong:

- **A merge voids the green.** The suite verified *your* tree; merging `main` in
  changes it, so the result no longer describes what lands on `main`. Merge
  first, then verify — never the reverse. Nothing extra is needed to notice: the
  merge moves the tree hash, so `suite-freshness.sh check` reports STALE by
  itself.
- **Never resolve another session's conflict.** Stop and report it. The tree may
  be mid-refactor in a session you cannot see, and that call is the user's.

Worktrees do **not** buy parallel testing — the contention is CPU, and it applies
across worktrees exactly as within one. Parallel testing comes from
`pytest-xdist` instead, and only within a run: `-n 4 --dist loadfile`, as above.
Two suites in
two worktrees at once is still the thing to avoid.

All trees share one table cache, resolved from `git rev-parse --git-common-dir`
so that every worktree finds the main tree's `.cache/tables` with nothing to
configure. Acquiring the slice table is **3m11s** cold against **1.055s** warm,
so a cold worktree costs more than the contention it avoids. An earlier version
set this through an environment variable in `.claude/settings.local.json`; that
file is untracked, so no worktree checkout could contain it and every worktree
silently took the cold path. `SCIAGENT_TABLE_CACHE` still overrides if set.

Sharing one cache means several processes read and write one file, and **both**
halves of that race are now guarded: `EmpiricalTable.save` retries its atomic
replace, and `_read_text_contended` retries the read that `load` goes through.
The reader's half was added on 2026-08-16 after a `-n 4` suite run died with
`PermissionError` on the gate table; before it, a probe at four writers and six
readers lost 4 reads in 900, and after it, none in 900 or in 1200 at six and
eight. Anything new that reads a cached artefact should go through the same door.

## Working style

- **Ask before assuming.** If the spec is ambiguous, ask rather than picking.
  Ambiguity in the spec is a real finding worth surfacing.
- **Tests first for anything with an acceptance criterion.** Write the A-test,
  watch it fail, run `/test-review` on it, then implement.
- **`/next` is not the only way to write code here.** A request made directly —
  "implement X", or something we discussed and planned — is a first-class path.
  What it drops is `/next`'s **§1 and §2**, the two whole sections that resolve
  the cursor and read the §11 row and the §6 gates: an ad-hoc request has no
  backlog row, so there is nothing there to resolve. **`/next` §3 is kept
  entire**, every numbered step of it — write the test, watch it fail,
  `/test-review` it, implement, verify. Read that carefully: §3's own steps 1
  and 2 are the test and its watched failure, and they are the last things to
  drop. Then §4 as written: `/decide`, then `/preflight`. `/ship` needs only
  that `/preflight` has run — it never checks for `/next`.

  The test review is the one step that does not follow from anything else on
  this page, which is why it is named here as well as in both skills. Skipping
  it because the work arrived as a sentence rather than a backlog row is how
  ad-hoc work silently becomes the untested kind. What replaces the §6 criterion
  is your own written statement of what the test establishes — write it before
  the review, not after, and do not narrow it to fit the test.

  **If `/test-review` does not resolve** — `Unknown skill` — open
  `.claude/skills/test-review/SKILL.md` and carry it out inline, and say that is
  what you did. A skill is invisible to a worktree session until its branch
  reaches `main` (`docs/DECISIONS.md`, 2026-08-17), and ad-hoc work asked for
  inside a worktree is exactly where that bites. Losing the step silently is the
  failure this whole entry exists to prevent.
- Run `uv run pytest -n 4 --dist loadfile` and `uv run mypy` before saying done.
  `mypy` takes no
  arguments: `pyproject.toml` sets `strict` and the file set, so naming a path
  checks less than the configured one.
- If something in the spec seems wrong, say so. Do not silently work around it.
- **Append to `docs/DECISIONS.md` the moment a decision is made**, not at the
  end of a session — sessions end by context exhaustion or a closed laptop.
  Use `/decide`, which carries the format and the four-category filter.

## Skills

- `/next` (or `/next 12`) — drive one SPEC §11 backlog item: resolve the
  cursor, A-test first, watch it fail, review the test, implement, verify.
- `/test-review` — review a freshly written failing test against the standard it
  is meant to encode, before the implementation exists. `/next` calls it at
  step 3; on the ad-hoc path you call it yourself. The only moment anything
  looks at a test alone.
- `/decide` — append to `docs/DECISIONS.md`.
- `/gate A9` — run one acceptance criterion and report the real outcome.
- `/preflight` — scope, verify and review independently. Lands nothing, so it needs
  no permission and may run on your own initiative. `/next` ends with it.
- `/ship` — commit, merge and push what `/preflight` has checked. The user invokes
  this one; you never do.
- `/recall <topic>` — find what was already decided, without reading it whole.
- `/handoff` — write a note so a session ending badly does not strand its work.
  Prefer `claude --resume`; this is the fallback when resuming is impossible.
- `/platform-check` — dormant. The instrument for lifting the Windows pin, kept
  against the day that matters; not part of ordinary work.
- `/matrix` — drive item 15, the first experiment matrix.

The SessionStart hook prints `scripts/status.py`, but only in full when the
backlog or the gates have actually moved; otherwise it prints three lines, and
the statusline carries branch, dirty count, cursor and gate coverage
continuously at no cost in context. Do not re-run the script to orient. Do run
`--run` when you need gates verified by execution rather than by their tests
merely existing.

**Do not read `docs/DECISIONS.md` end to end.** It is ~360KB across ~135 entries
and grows every session; reading it whole costs about 90k tokens. Use `/recall`,
which greps headers and reads only what matches. The file still holds what the
repository cannot tell you — that has not changed, only how to get at it.

## Cloud sessions

This repository is set up to be worked on from Claude Code on the web, so that
it can be driven from a phone with no local machine running. `CLAUDE_CODE_REMOTE`
is `"true"` in a cloud session and unset locally; that is the signal to branch
on, not a guess from the platform.

Python and `uv` are on the cloud image, and the default **Trusted** network
level reaches PyPI. The project's own pinned `pytest`, `mypy` and `ruff` are
`[dependency-groups] dev` entries resolved from `uv.lock`, so the first `uv run`
installs them; nothing needs configuring beyond that.

Three differences from a local session actually change behaviour:

- **Work happens on the session's own branch, never `main`.** The GitHub proxy
  accepts a push only for the branch the session is already on, so a commit
  made on `main` cannot be pushed. `/ship` handles this; do not work around it.
- **The VM is 4 vCPU / 16 GB / 30 GB.** `-n auto` resolves to 4 here, which is
  the same worker count the desktop wants, so the invocation does not change.
  The serial suite ran about 8 minutes here against 6m30s–7m45s on a desktop;
  both figures predate `35cf96a`, which took the desktop's serial suite to
  262.44s, and the parallel figure on this VM has never been measured. A cold
  first session also spends roughly half a minute installing dependencies before
  the status hook prints. Four vCPUs is the thing to plan around: a suite run
  measured at **30m37s** while a subagent was working in the same container,
  against **7m50s** idle on that same VM — a near-fourfold slowdown from
  contention alone, and the origin of a figure that is often quoted as though it
  were a desktop measurement. It is not; see the working defaults above for what
  contention costs locally. Do not start a long run and a
  subagent together and then read the timing as the suite's. Do not use the test
  count as a health check either — it moves with every backlog item — read the
  tail of the pytest output instead.
- **Cloud sessions edit code; they do not produce numbers.** Windows is this
  project's reference platform. Every registry entry, cached table and reported
  figure comes from the desktop, and a cloud session is for writing and reviewing
  code, running the suite and shipping — not for generating results.

  This is a deliberate pin, not an open problem. `docs/DECISIONS.md` 2026-08-15
  records a *measured* Windows/Ubuntu divergence and left three remedies open:
  put a platform term in the content address, scope tables by platform, or run
  everything on one platform and say which. The third is chosen. It costs
  nothing, because no second platform produces numbers to be incomparable with.

  So do not gate work on settling the divergence, and do not read a
  single-platform result as provisional — one platform is the design. The pin
  asks one thing in return: if a number, registry entry or `.cache/tables/`
  artefact is ever produced in a cloud session, discard it rather than comparing
  it with a local one. `/platform-check` and
  `tests/acceptance/determinism_child.py` stay in the repository as the
  instrument for lifting the pin, if that ever becomes worth doing.
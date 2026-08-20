# Cloud sessions

This repository is set up to be worked on from Claude Code on the web, so that it
can be driven from a phone with no local machine running. `CLAUDE_CODE_REMOTE` is
`"true"` in a cloud session and unset locally; that is the signal to branch on,
not a guess from the platform.

**Read this file at the start of any cloud session.** It lives here rather than
in `CLAUDE.md` because none of it applies locally, and `CLAUDE.md` is billed on
every turn of every session. `.claude/hooks/session-start.sh` prints a pointer to
it when `CLAUDE_CODE_REMOTE` is set, so a cloud session is told; a local one pays
nothing.

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
  were a desktop measurement. It is not; for what contention costs locally see
  `docs/DECISIONS.md`, 2026-08-16, "pytest-xdist adopted at -n 4, and what the
  contention rule actually is". Do not start a long run and a
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

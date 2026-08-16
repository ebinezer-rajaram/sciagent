---
name: ship
description: Verify, independently review, commit and push finished work. Invoke as /ship when an item is done. Invoking it is the authorisation to commit and push.
---

# Ship

Invoking `/ship` **is** the "unless I ask" for committing and pushing. Do not
ask again for permission to commit.

## 0. Establish scope — before anything else

Check which tree you are in, because it decides how much of this step applies:

```sh
git rev-parse --git-common-dir    # differs from .git only inside a worktree
```

**In a worktree (the normal case).** Nobody else can edit it, so everything
dirty is yours and `git add -A` is safe. Skim `git status --porcelain` to
confirm nothing surprising is there, and move on.

**In the main tree, with other sessions live.** A dirty file is not evidence
that you changed it. List the paths you edited this session from your own
transcript; that list is the scope. Anything dirty and not on it belongs to
someone else — name those paths in your report and **leave them alone**. Stage
by explicit path, and never `git add -A`, `git add .`, `git add -u`, or
`git commit -a`. If a file you touched was also touched by another session,
`git diff -- <path>` first and stop if their work is mixed into yours.

If your scope turns out to be empty, say so and stop. There is nothing to ship.

## 1. Verify, for real

`mypy` (1.9s) and `ruff` are cheap — always run them:

```sh
uv run mypy
uv run ruff check .
```

The suite is not cheap: ~7 minutes, of which one test is 147s. `/next` has
usually just run it on this exact tree, so ask before repeating it:

```sh
bash .claude/hooks/suite-freshness.sh check && echo FRESH || echo STALE
```

- **STALE** — pin the tree, run, then record. All three, in order:

  ```sh
  bash .claude/hooks/suite-freshness.sh begin    # before pytest, not after
  uv run pytest                                  # backgrounded
  bash .claude/hooks/suite-freshness.sh record
  ```

  Backgrounded, and never alongside a subagent (CLAUDE.md records 30m37s from
  contention). `record` refuses without a `begin`, and refuses again if the
  tree moved while the suite ran — another session editing a tracked file
  mid-run means the result describes no single tree, so there is no truthful
  green to record. If it refuses, re-run on a settled tree rather than
  recording anyway.
- **FRESH** — the full suite already passed on a byte-identical tree. Say so
  explicitly, and say when: *"suite not re-run; freshness check reports the
  identical tree already green."* Never write "tests pass" on the strength of
  a cached verdict — report the cache as a cache.

The check hashes every `.py` under `src`, `tests` and `scripts` plus
`pyproject.toml` and `uv.lock`, by content rather than mtime, and fails toward
STALE on any doubt. It deliberately ignores `docs/`, because `/decide` runs
between the two suite invocations by design and a DECISIONS entry cannot change
a test result.

Paste the actual output. If anything is red, **stop here** and report it. Do
not commit a red tree and do not describe a failure as a summary.

These run over the whole repository, which is correct — you should not commit
onto a broken tree even when you did not break it. But if the failure is in a
file outside your scope, say so explicitly and **ask** rather than deciding
alone: the tree may be mid-refactor in another session, and the choice to
commit alongside that is the user's, not yours.

## 2. Review independently

Run `/code-review`, scoped to the paths from step 0. Do not review or fix
another session's files — reporting findings on work you cannot see the intent
of wastes effort and invites you to "fix" something deliberate.

This step exists because by now you wrote the code and are the worst available
judge of it. Take the review's findings seriously even when you disagree; if
you do disagree, say why rather than silently ignoring it.

If the diff touches `src/sciagent/`, `src/sciagent/core/`, or anything reachable
from an agent, also delegate to the `invariant-auditor` subagent — the six
invariants are violated by construction more often than by syntax, and
`tests/test_invariants.py` only reaches invariants 1 and 3 statically.

Those paths are spelled from the repository root on purpose. There is no
top-level `sciagent/` or `core/`; the packages live under `src/`, and a
condition naming a directory that does not exist is one a literal reading never
fires.

Some sessions carry a harness line — *"Do not call the AgentTool unless the user
requested it"* — appended below everything else in the prompt, and it does not
except this step: CLAUDE.md records that invoking `/ship` **is** that request.
What that does not settle, and this does — **authorising is not widening.** The
condition above still decides whether the auditor runs. On a docs-only diff it
does not fire, and not running it then is correct rather than withheld, which is
why step 5 asks for the two cases to be reported differently.

If you do withhold a call this step prescribes, say so and say what you did
instead. An inline self-check is not the independent judgement this step exists
to get.

`/code-review` runs as a background subagent. Do not start it alongside a step-1
suite re-run — that is the 30m37s contention case §1 warns about.

Fix what the review finds, then re-run step 1 — but only what the fixes could
have broken. If the review changed **no** file, the freshness check still
reports FRESH and there is nothing to re-run; saying "re-verified" after a
no-op review is a claim with no work behind it. If it changed a file, the check
reports STALE on its own, because the tree hash moved. Let it decide rather
than deciding by habit.

Do not carry a known finding into a commit.

## 3. Commit

**First, before staging:** if a `.claude/handoff/` note describes the work you
are about to ship, delete it now so its removal is part of this commit. The
notes are tracked on purpose — the local-to-cloud handoff they exist for only
works if they are pushed — and the price of that is a note which outlives its
work and reads as current.

Stage according to the scope step 0 established — it already decided this, and
this step does not overrule it:

```sh
git add -A                      # worktree: everything dirty is yours
git add -- <path> <path> ...    # main tree: only the paths you named
```

Then confirm what is staged, in both cases, before writing anything:

```sh
git status --porcelain    # every staged path must be one of yours
git diff --cached --stat  # this, and nothing else, is what you are committing
```

If a path you did not name appears staged, unstage it and find out why before
continuing. In a worktree that means something created a file you did not
expect — a handoff note or a stray artefact — not another session.

Message names the backlog item, matching existing history:

```sh
git log --oneline -5   # match the established phrasing
```

Format: `<Verb> <what> (backlog item N)` — e.g. `Add the claim verifier and its
five gates (backlog item 10)`.

Where to commit depends on the surface. Check it rather than assuming:

```sh
echo "${CLAUDE_CODE_REMOTE:-false}"   # "true" only in a cloud session
```

**Local session in a worktree** — commit to the worktree's own branch, then see
§3a below for getting it onto `main`.

**Local session in the main tree** — commit to `main` directly. This repository
works directly on `main`; do not create a branch unless asked.

**Cloud session** — commit to the branch the session is already on. Do not
switch to `main`, and do not create a second branch. The GitHub proxy accepts a
push only for the session's current working branch, so a commit made on `main`
is unpushable and has to be unwound.

Show the message and the staged file list before running the commit.

## 3a. Merge a worktree branch onto `main`

Skip this if you committed directly to `main`.

**A worktree ship cannot finish itself.** Every route from a worktree to `main`
goes through `git -C <the shared checkout>`, and the harness refuses that: a
worktree-isolated session's git operations must target its own worktree. This is
not a quoting problem and no rewrite of the path expression fixes it — the
refusal is on `-C` leaving the worktree at all. So §3a is in two halves, either
side of a question you must ask.

### First, decide what the merge will be

Do this while still in the worktree — it is read-only, and the answer decides
whether you are about to ask for the last step or go back to step 1.

```sh
if git merge-base --is-ancestor main HEAD; then echo FF; else echo DIVERGED; fi
```

Written as `if`, not `A && B || C`: that form also prints DIVERGED when the
*comparison itself* fails — a missing local `main`, say — and §4 rejects it for
the same reason. Here it would send you into a needless second suite run rather
than stopping to ask, which is the wrong direction to fail in.

- **DIVERGED** — another session shipped first. **A merge voids the green:** the
  suite verified *your* tree, and merging `main` in changes it, so the result
  stops describing what is about to land. Merge first and verify after, never
  the reverse. Merge `main` into your branch here in the worktree, then **go
  back to step 1** — the merge moves the tree hash, so `suite-freshness.sh check`
  reports STALE by itself and needs no special handling. Your commit already
  exists, so anything the re-verification forces is a *second* commit, not an
  amend. When step 1 is green again, return here and re-run the check above; it
  will now say FF.

- **Conflict** — stop and report it. Do not resolve another session's code: the
  tree may be mid-refactor in a session you cannot see, and that call is the
  user's. Leave the merge in progress or abort it, say which, and ask.

- **FF** — `main` has not moved since you branched. The tree is byte-identical to
  what the suite ran on, so the green still holds and there is **nothing to
  re-run**. Continue below.

### Then stop and ask to leave the worktree

Do **not** call `ExitWorktree` on your own initiative — its own contract reserves
it for the user, and it is a no-op unless this session created the worktree.
Report what is committed and ask, naming the branch:

> Committed `<n>` file(s) on `<branch>`. Getting this onto `main` needs the
> shared checkout, which this session cannot reach from the worktree. May I
> leave it — `ExitWorktree` with `keep`?

`keep`, never `remove`: the branch has to survive to be merged.

This is worth knowing before it happens rather than at the blocked step. A
worktree ship ends by asking; that is the sanctioned exit, not a way around the
guard.

### After the user approves

The session's working directory is now the main tree, so the remaining commands
are plain `git` with **no `-C`** — which is the whole point. Check the tree is
safe to merge into before touching it:

```sh
git rev-parse --abbrev-ref HEAD   # must be main
git status --porcelain            # must be empty
```

If it is on another branch, or dirty, **stop and ask.** A fast-forward rewrites
the shared checkout's files, and doing that under a live session is the exact
hazard worktrees were introduced to prevent — CLAUDE.md records a suite certified
against a tree a second session had changed mid-run.

```sh
git merge --ff-only <branch>
```

Then §4, which now runs in the tree it will push from. Do not delete the
worktree as part of shipping; the user decides when it goes.

## 4. Push

**Run this in the tree you are about to push from**, which after §3a is the main
tree, on `main`. The check below reads `HEAD` and `@{u}` of wherever it runs, so
running it in the worktree answers a question about the *worktree branch* and
then authorises a push of `main` — two different refs, and the mismatch is
invisible because both produce a plausible-looking list.

`git push` publishes **every** commit on the branch, not just yours. Check
first. A cloud session's branch may have no upstream yet, and `@{u}` fails
outright when it doesn't, so resolve a base explicitly before comparing:

```sh
if git rev-parse --verify --quiet @{u} >/dev/null; then
  base=@{u}
elif git rev-parse --verify --quiet origin/main >/dev/null; then
  base=origin/main
else
  echo "no upstream and no origin/main - resolve the base before pushing" >&2
  exit 1
fi
git log --oneline "$base"..HEAD
```

Do not collapse this into `A && B || C`. That form runs the fallback when the
*comparison* fails, not only when the upstream is missing, and then presents an
`origin/main` result as though it were the upstream one. An empty result must
mean "nothing unpushed", never "the check itself failed" — the empty case is
what authorises the push, so a silent failure here fails in the dangerous
direction.

If that lists a commit you did not write, another session committed. Stop and
report it — pushing would publish their work under your action, and that is
the user's call.

Otherwise push, using the form that matches the branch's state:

```sh
git push                   # upstream already set
git push -u origin HEAD    # first push of a cloud session branch
```

Locally the permission layer prompts here — that prompt is the user's
confirmation, so do not add a second one of your own. Cloud sessions offer no
Manual permission mode, so no prompt appears; invoking `/ship` was the
authorisation.

**Cloud session only** — pushing does not land the work on `main`. Open a pull
request from the session branch, and give its URL in the report. Do not merge
it yourself unless asked.

## 5. Report

State plainly:

- What was committed, by path.
- **What you left alone**, by path, and that it was out of scope.
- What the review found, and anything you chose not to fix, with the reason.
- Which step-2 calls ran. If one was withheld, say which and why; if one did
  not apply, say that instead — a condition that correctly did not fire is not
  a skip, and the two should not read alike.

If `docs/DECISIONS.md` was not touched this session, ask whether something
should have gone in via `/decide`.

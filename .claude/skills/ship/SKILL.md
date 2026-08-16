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

If the diff touches `sciagent/`, `core/`, or anything reachable from an agent,
also delegate to the `invariant-auditor` subagent — the six invariants are
violated by construction more often than by syntax, and `tests/test_invariants.py`
only reaches invariants 1 and 3 statically.

Fix what the review finds, then re-run step 1 — but only what the fixes could
have broken. If the review changed **no** file, the freshness check still
reports FRESH and there is nothing to re-run; saying "re-verified" after a
no-op review is a claim with no work behind it. If it changed a file, the check
reports STALE on its own, because the tree hash moved. Let it decide rather
than deciding by habit.

Do not carry a known finding into a commit.

## 3. Commit

Stage the step-0 paths explicitly, then confirm what is staged before writing
anything:

```sh
git add -- <path> <path> ...
git status --porcelain    # every staged path must be one of yours
git diff --cached --stat  # this, and nothing else, is what you are committing
```

If a path you did not name appears staged, unstage it and find out why before
continuing.

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

**A merge voids the green.** The suite verified *your* tree. Merging `main` in
changes that tree, so the result stops describing what is about to land. Merge
first and verify after — never the reverse. Ask which case you are in:

```sh
if git merge-base --is-ancestor main HEAD; then echo FF; else echo DIVERGED; fi
```

Written as `if`, not `A && B || C`: that form also prints DIVERGED when the
*comparison itself* fails — a missing local `main`, say — and §4 rejects it for
the same reason. Here it would send you into a needless second suite run rather
than stopping to ask, which is the wrong direction to fail in.

- **FF** — `main` has not moved since you branched. Fast-forward it. The tree is
  byte-identical to what the suite ran on, so the green still holds and there is
  **nothing to re-run**:

  ```sh
  main_tree="$(cd "$(git rev-parse --git-common-dir)/.." && pwd)"
  git -C "$main_tree" merge --ff-only "$(git rev-parse --abbrev-ref HEAD)"
  ```

- **DIVERGED** — another session shipped first. Merge `main` into your branch,
  then **go back to step 1**. No special handling is needed to notice: the merge
  moves the tree hash, so `suite-freshness.sh check` reports STALE by itself.
  Paying a second suite run here is the cost of the guarantee, not a failure.

- **Conflict** — stop and report it. Do not resolve another session's code: the
  tree may be mid-refactor in a session you cannot see, and that call is the
  user's. Leave the merge in progress or abort it, say which, and ask.

**Then push from the main tree, not the worktree.** This is easy to get wrong:
after the fast-forward the commit is on `main`, but the worktree is still on its
own branch, so `git push` there publishes the branch and leaves `origin/main`
untouched — while §4's `origin/main..HEAD` fallback still prints an empty list
and reads as success. Run §4 in the main tree:

```sh
git -C "$main_tree" push
```

Do not delete the worktree as part of shipping. The user decides when it goes,
via `ExitWorktree`.

## 4. Push

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

If `docs/DECISIONS.md` was not touched this session, ask whether something
should have gone in via `/decide`.

---
name: ship
description: Commit, merge and push work that /preflight has already checked. Invoke as /ship when an item is finished. Invoking it is the authorisation to commit and push.
---

# Ship

Invoking `/ship` **is** the "unless I ask" for committing and pushing. Do not
ask again for permission to commit.

This skill is the irreversible half. Scope, verification and independent review
live in `/preflight`, which is safe to run at any time and which you may run on
your own initiative. Nothing here is. **Do not invoke `/ship` yourself on the
strength of your own confidence that the work is finished** — the user invokes
it, and that invocation is the authorisation.

## 0. Verify first — run `/preflight`

`/preflight` establishes the scope, runs `mypy`, `ruff` and the suite, and runs the
independent review. It is a precondition, not a suggestion: `136550a` was pushed
without the review step and it cost two defects on `main`
(`docs/DECISIONS.md`, 2026-08-16).

- **If `/preflight` has already run and nothing has changed since** — usually
  because `/next` ran it — say so, say when, and continue. Do not repeat it.
- **Otherwise, run `/preflight` now**, in full, and read its report before touching
  anything.
- **If `/preflight` does not resolve** — `Unknown skill` — do not skip it and do
  not push. A skill authored in a worktree is invisible to that worktree's own
  session until its branch reaches `main`, the same rule `docs/DECISIONS.md`
  (2026-08-16) records for `.claude/agents/`. Open
  `.claude/skills/preflight/SKILL.md`, carry out its three steps inline, and say
  that is what you did. This is the failure mode most likely to lose scope,
  `mypy`, `ruff`, the suite *and* the review in one go, silently, one step
  before a push — which is why it gets a branch of its own rather than a
  footnote.

**"Nothing has changed since" is stricter than FRESH, and the two are not
interchangeable.** `suite-freshness.sh` hashes `.py` under `src`, `tests` and
`scripts`, plus `pyproject.toml` and `uv.lock`. It is silent on `docs/`,
`.claude/` and everything else — deliberately, because `/decide` runs in the
middle and a DECISIONS entry cannot change a test result. But it *can* change
what the review should have seen. So a tree the hook calls FRESH may still carry
edits the review never looked at: the suite and the review go stale on different
inputs, and only the suite has a hook watching it. If any file changed after
`/preflight` reported — not merely a hashed one — run it again.

Then check three things before proceeding:

- **The tree is green.** If `/preflight` stopped on a red tree, stop here too.
- **The scope is not empty.** If it is empty, there is nothing to ship; say so
  and stop.
- **No known finding is unresolved.** Do not carry one into a commit.

Everything below stages from the scope `/preflight` established. Do not
re-derive it, and do not widen it — with exactly one exception, named in §1: a
`.claude/handoff/` note you are about to make obsolete. That deletion is a path
`/preflight` never saw, because the note was not dirty when it looked. Add it to
the scope deliberately and stage it by name; in the main tree, where staging is
by explicit path, it is otherwise left unstaged and the stale note survives the
commit that was supposed to retire it.

## 1. Commit

**First, before staging:** if a `.claude/handoff/` note describes the work you
are about to ship, delete it now so its removal is part of this commit. The
notes are tracked on purpose — the local-to-cloud handoff they exist for only
works if they are pushed — and the price of that is a note which outlives its
work and reads as current.

Stage according to the scope `/preflight` established — it already decided this,
and this step does not overrule it:

```sh
git add -A                      # worktree: everything dirty is yours
git add -- <path> <path> ...    # main tree: only the paths you named
```

In the main tree, never `git add -A`, `git add .`, `git add -u`, or
`git commit -a`. Stage by explicit path, because a dirty file there is not
evidence that you changed it.

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

**Only if the work really is that item.** Ad-hoc work — a request made directly
rather than drawn from §11 — takes the same `<Verb> <what>` and **no trailing
parenthesis**: `Factor the A-test lens into its own skill`. Do not invent a
number to fill the slot. `/next` step 1.3 resolves the cursor with
`git log --oneline --grep='backlog item'` and takes the lowest-numbered item no
commit names, so a commit falsely claiming item N marks that item done and the
cursor silently skips it. The convention is a way of finding items, not a
required suffix.

Where to commit depends on the surface. Check it rather than assuming:

```sh
echo "${CLAUDE_CODE_REMOTE:-false}"   # "true" only in a cloud session
```

**Local session in a worktree** — commit to the worktree's own branch, then see
§2 below for getting it onto `main`.

**Local session in the main tree** — commit to `main` directly. This repository
works directly on `main`; do not create a branch unless asked.

**Cloud session** — commit to the branch the session is already on. Do not
switch to `main`, and do not create a second branch. The GitHub proxy accepts a
push only for the session's current working branch, so a commit made on `main`
is unpushable and has to be unwound.

Show the message and the staged file list before running the commit.

## 2. Merge a worktree branch onto `main`

Skip this if you committed directly to `main`.

**A worktree ship cannot finish itself.** Every route from a worktree to `main`
goes through `git -C <the shared checkout>`, and the harness refuses that: a
worktree-isolated session's git operations must target its own worktree. This is
not a quoting problem and no rewrite of the path expression fixes it — the
refusal is on `-C` leaving the worktree at all. So this step is in two halves,
either side of a question you must ask.

### First, decide what the merge will be

Do this while still in the worktree — it is read-only, and the answer decides
whether you are about to ask for the last step or go back to `/preflight`.

```sh
if git merge-base --is-ancestor main HEAD; then echo FF; else echo DIVERGED; fi
```

Written as `if`, not `A && B || C`: that form also prints DIVERGED when the
*comparison itself* fails — a missing local `main`, say — and §3 rejects it for
the same reason. Here it would send you into a needless second suite run rather
than stopping to ask, which is the wrong direction to fail in.

- **DIVERGED** — another session shipped first. **A merge voids the green:** the
  suite verified *your* tree, and merging `main` in changes it, so the result
  stops describing what is about to land. Merge first and verify after, never
  the reverse. Merge `main` into your branch here in the worktree, then **go
  back to `/preflight`** — the merge moves the tree hash, so `suite-freshness.sh
  check` reports STALE by itself and needs no special handling. Tell it you came
  from here, so it knows an empty scope is expected and that it is verifying the
  merged tree rather than reviewing your edits.

  **If the re-verification forces a fix, go back to §1 and commit it before
  returning here.** Your first commit already exists, so this is a *second*
  commit and never an amend — and it needs a step that actually performs it.
  Returning straight to the check below would fast-forward and push with the fix
  sitting unstaged in the working tree, which is the failure this whole branch
  exists to prevent. Only when `/preflight` is green **and** nothing is dirty,
  re-run the check above; it will now say FF.

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

Then §3, which now runs in the tree it will push from. Do not delete the
worktree as part of shipping; the user decides when it goes.

## 3. Push

**Run this in the tree you are about to push from**, which after §2 is the main
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

## 4. Report

State plainly:

- What was committed, by path.
- **What you left alone**, by path, and that it was out of scope.
- What `/preflight` found, and anything you chose not to fix, with the reason. If
  `/preflight` ran earlier in the session rather than just now, say when, and say
  that its review — not merely its suite result — is the one being relied on.
- Which of `/preflight`'s review calls ran. If one was withheld, say which and why;
  if one did not apply, say that instead — a condition that correctly did not
  fire is not a skip, and the two should not read alike.

If `docs/DECISIONS.md` was not touched this session, ask whether something
should have gone in via `/decide`.

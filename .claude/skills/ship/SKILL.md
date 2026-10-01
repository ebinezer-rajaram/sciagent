---
name: ship
description: Verify, commit, and merge finished work, then push only after asking. Invoke as /ship; invoking it is the authorisation to commit.
disable-model-invocation: true
---

# Ship

Invoking `/ship` authorises the commit. It does not authorise the push: ask for
that separately.

1. **Verify.** Run these and quote the tail of each:
   - `uv run ruff format --check . && uv run ruff check .`
   - `uv run mypy`
   - `uv run pytest -m "not slow"`, and `uv run pytest -m slow` if the change
     touches `src/`.

   Stop on any failure.
2. **Review the diff.** Run `git status` and `git diff --stat`. Name anything
   in the diff the user did not ask for. In the main tree, stage by explicit
   path; in a worktree, `git add -A` is fine.
3. **Commit.** Use an imperative one-line subject that matches
   `git log --oneline -5`, a short body if the why is not obvious, and the
   attribution trailer. Show the message and the staged file list first.
4. **Merge.** From a worktree, `main` cannot be reached with `git -C`. Ask the
   user to leave the worktree (keep), then fast-forward `main` to the branch
   from the main tree. If `main` has moved, merge it into the branch and rerun
   step 1 first.
5. **Push.** Ask before running `git push`, and never force-push.

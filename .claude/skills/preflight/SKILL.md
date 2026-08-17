---
name: preflight
description: Establish scope, verify the tree, and review it independently — without committing anything. Invoke as /preflight when work looks finished. Runs automatically at the end of /next; /ship runs it before committing.
---

# Preflight: verify and review

This skill does everything `/ship` used to do **before** it touched history, and
nothing it did after. It is safe to run at any time, on your own initiative or
the user's, because it cannot land anything.

Three steps: scope, verify, review. Then it stops.

## 0. Establish scope — before anything else

Check which tree you are in, because it decides how much of this step applies:

```sh
git rev-parse --git-common-dir    # differs from .git only inside a worktree
```

**In a worktree (the normal case).** Nobody else can edit it, so everything
dirty is yours. Skim `git status --porcelain` to confirm nothing surprising is
there, and move on.

**In the main tree, with other sessions live.** A dirty file is not evidence
that you changed it. List the paths you edited this session from your own
transcript; that list is the scope. Anything dirty and not on it belongs to
someone else — name those paths in your report and **leave them alone**. If a
file you touched was also touched by another session, `git diff -- <path>` first
and stop if their work is mixed into yours.

If your scope turns out to be empty, say so and stop — **unless `/ship` sent you
here to re-verify after a merge.** Then an empty scope is the expected state:
the merge is already committed, so nothing is dirty, and what needs verifying is
the merged tree rather than any edit of yours. Skipping step 1 there would push
a tree on a green that never saw it, which is the exact thing `/ship` §2 sends
you back for. Empty scope means "nothing to *review*", never "nothing to
*verify*", and only the first of those is a reason to stop.

Carry this list forward. `/ship` stages from it and does not re-derive it.

## 1. Verify, for real

`mypy` (1.9s) and `ruff` are cheap — always run them:

```sh
uv run mypy
uv run ruff check .
```

The suite is not cheap: about 2m30s at `-n 4 --dist loadfile`, of which one
oracle test is ~134s. `/next` has usually just run it on this exact tree, so ask
before repeating it:

```sh
bash .claude/hooks/suite-freshness.sh check && echo FRESH || echo STALE
```

- **STALE** — pin the tree, run, then record. All three, in order:

  ```sh
  bash .claude/hooks/suite-freshness.sh begin    # before pytest, not after
  uv run pytest -n 4 --dist loadfile             # backgrounded
  bash .claude/hooks/suite-freshness.sh record
  ```

  Both flags are measured, not guesses — see CLAUDE.md. `-n auto` fails with
  `MemoryError` on this desktop, and dropping `--dist loadfile` makes workers
  duplicate 2000-replicate simulations. Backgrounded, and never alongside a
  subagent: four read-only agents cost a `-n 4` run 12–15%. Delegating the whole
  step to `suite-runner` is the alternative that keeps the output out of context
  altogether. `record` refuses without a `begin`, and refuses again if the tree
  moved while the suite ran — another session editing a tracked file mid-run
  means the result describes no single tree, so there is no truthful green to
  record. If it refuses, re-run on a settled tree rather than recording anyway.
- **FRESH** — the full suite already passed on a byte-identical tree. Say so
  explicitly, and say when: *"suite not re-run; freshness check reports the
  identical tree already green."* Never write "tests pass" on the strength of
  a cached verdict — report the cache as a cache.

The check hashes every `.py` under `src`, `tests` and `scripts` plus
`pyproject.toml` and `uv.lock`, by content rather than mtime, and fails toward
STALE on any doubt. It deliberately ignores `docs/`, because `/decide` runs
between the two suite invocations by design and a DECISIONS entry cannot change
a test result.

Paste the actual output. If anything is red, **stop here** and report it. Do not
carry a red tree into the review, and do not describe a failure as a summary.

These run over the whole repository, which is correct — you should not ship onto
a broken tree even when you did not break it. But if the failure is in a file
outside your scope, say so explicitly and **ask** rather than deciding alone:
the tree may be mid-refactor in another session, and the choice to proceed
alongside that is the user's, not yours.

## 2. Review independently

**Skip this step entirely on the post-merge re-entry from `/ship` §2**, and say
in the report that you did and why. There is nothing here to review: your own
paths were reviewed on the pre-merge pass, and what the merge brought in is
another session's already-reviewed work, which step 0 forbids you to review
anyway. What the merge invalidated is the *suite*, not the review — which is why
`/ship` sends you back at all, and why step 1 is the whole of what it needs.
This is a condition correctly not firing, not a skip of convenience; §3 asks for
those to read differently.

Otherwise: run `/code-review`, scoped to the paths from step 0. Do not review or
fix another session's files — reporting findings on work you cannot see the
intent of wastes effort and invites you to "fix" something deliberate.

This step exists because by now you wrote the code and are the worst available
judge of it. Take the review's findings seriously even when you disagree; if
you do disagree, say why rather than silently ignoring it.

If the diff touches `src/sciagent/`, `src/sciagent/core/`, or anything reachable
from an agent, also delegate to the `invariant-auditor` subagent — the six
invariants are violated by construction more often than by syntax, and
`tests/test_invariants.py` only reaches invariants 1 and 3 statically.

**Launch it as four lenses, in one message, alongside `/code-review`.** The
agent's four sections are unlike investigations, and run as a single pass they
compete for attention: lens 2 is a call-graph trace, lens 4 is a grep, and the
grep always finishes. Splitting them also lets each take the tier it needs
instead of all four sharing the weakest.

| Lens | Model | Why that tier |
|---|---|---|
| 2 — agent→`plausibility` reachability | **omit it** | Inherits the session model. A miss here is a real invariant violation reaching `main`. |
| 3 — ordering sensitivity | `sonnet` | Bounded judgement, one site at a time. |
| 4 — registry append-only | `haiku` | Pattern match; a miss is recoverable by a grep you can run in seconds. |
| 6 — gate-vs-system ordering | `sonnet` | Mechanical, but there is a wrong answer available. |

Omitting the model on lens 2 is deliberate and is not the same as forgetting it:
inheriting is how that lens gets the strong model, since CLAUDE.md forbids
pinning opus explicitly. The tiers are graded by what a **false negative** costs,
not by what the lens costs to run — a cheap auditor reporting "nothing found" is
indistinguishable from a clean sweep, so cheapness is only affordable where you
could catch the miss yourself.

A lens returning nothing is a covered lens. Report four lenses run and three
clean as exactly that; "the auditor found nothing" hides whether a lens was
skipped.

The paths in that condition — `src/sciagent/`, `src/sciagent/core/` — are
spelled from the repository root on purpose. There is no top-level `sciagent/`
or `core/`; the packages live under `src/`, and a condition naming a directory
that does not exist is one a literal reading never fires.

### What authorises these subagents

Some sessions carry a harness line — *"Do not call the AgentTool unless the user
requested it"* — appended below everything else in the prompt. It does not
except this step, and the reason is not that the user typed a slash command:
this skill runs on the agent's own initiative at the end of `/next`, so an
authorisation resting on user invocation would not transfer.

The principle that does hold, and the one this repository runs on:

> **Read-only review is authorised by the work. Irreversible action is
> authorised only by the user.**

Every **subagent** this step launches is read-only: `/code-review` and the four
lenses report and never edit. That is the part the harness line is about, and it
is what the principle licenses.

Be precise about the rest, because the argument is load-bearing and an
overstatement of it would be doing real work. This skill is **not** read-only
end to end — the paragraph below tells you to fix what the review finds, and
fixing means editing files. What it cannot do is *land* anything: no commit, no
merge, no push, no history touched, nothing that leaves this working tree.
Working-tree edits are reversible and are the work the user already asked for;
publishing them is not, and lives in `/ship`. That is the line the split is
drawn on, and "reversible" rather than "read-only" is what carries it.

**Authorising is not widening.** The condition above still decides whether the
auditor runs. On a docs-only diff it does not fire, and not running it then is
correct rather than withheld — step 3 asks for the two cases to be reported
differently.

If you do withhold a call this step prescribes, say so and say what you did
instead. An inline self-check is not the independent judgement this step exists
to get.

`/code-review` runs as a background subagent, and with the lenses that is **five**
concurrent agents rather than two. They are read-only and cheap against each
other — but do not start any of them alongside a step-1 suite re-run. Four such
agents were measured on 2026-08-16 costing a `-n 4` suite 12–15% while alive for
only a fifth of it, so "read-only, so free" is the wrong premise even though the
rule is the right one; five make it worse, not better.

Fix what the review finds, then re-run step 1 — but only what the fixes could
have broken. If the review changed **no** file, the freshness check still
reports FRESH and there is nothing to re-run; saying "re-verified" after a
no-op review is a claim with no work behind it. If it changed a file, the check
reports STALE on its own, because the tree hash moved. Let it decide rather
than deciding by habit.

Do not carry a known finding into `/ship`.

## 3. Report, and stop

State plainly:

- The scope, by path — and **what you left alone**, by path, and that it was
  out of scope.
- The verification result. If the suite was not re-run, report the cache as a
  cache, in the words step 1 gives.
- What the review found, and anything you chose not to fix, with the reason.
- Which step-2 calls ran. If one was withheld, say which and why; if one did
  not apply, say that instead — a condition that correctly did not fire is not
  a skip, and the two should not read alike.

**If `/ship` called you** — from its §0, or from its §2 after a merge — report
the above and **return to it**. You are a subroutine on that path, not the end
of one; the stop below is for the standalone case, and reading it as
unconditional strands a merged, unpushed branch. Say plainly which of the two
call sites you are returning to.

**Otherwise stop. This skill does not commit, merge or push.** Nothing below
this line is yours to do on your own initiative. If the work is ready, say so
and leave the next move to the user:

> Verified and reviewed. `<n>` file(s) in scope, `<summary of findings>`.
> Ready to `/ship` when you are.

Invoking `/ship` is the user's authorisation to commit and push. Do not supply
it yourself by invoking `/ship` on the strength of your own confidence that the
work is finished — that confidence is exactly the signal `docs/DECISIONS.md`
(2026-08-16, `136550a`) records as having been wrong, where a commit's own
verification claimed to have checked both defects that later reached `main`.

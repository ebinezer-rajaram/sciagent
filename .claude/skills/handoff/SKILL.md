---
name: handoff
description: Write a note capturing what this session touched, verified and left unfinished, so work survives a session ending badly. Use proactively when the user signals the session is ending or degrading with work in flight — closing the laptop, stopping for the night, context running low, switching machines, carrying on from a phone or handing to a cloud agent — even when they never say "handoff" and only describe the situation. Do not use when they want to resume or continue an existing session, which restores the real transcript and is strictly better, nor for committing, pushing or recording a decision.
allowed-tools: Bash, Read, Write, Grep
---

# Session handoff

## Prefer resuming over reading this note

`claude --resume` (or `--continue`) restores the actual transcript. A handoff
note is a lossy summary of it. **If resuming is possible, resume** — this skill
exists for when it is not: work moving between the local machine and a cloud
session, a different machine, or a session whose context is exhausted rather
than closed.

Say so if you are asked to write a handoff where resuming would plainly serve
better.

## Why a note at all

Sessions here end by context exhaustion or a closed laptop, neither of which
offers a chance to write anything down. And shipping will not rescue the work
afterwards: `/preflight` step 0 requires naming the paths *you* edited from
*your own* transcript, and explicitly leaves everything else alone, because
other sessions work this repository concurrently. A path nobody can claim is a
path nobody can ship.

## Write it

Filename must not collide with a concurrent session's:

```sh
mkdir -p .claude/handoff
date +%Y%m%d-%H%M%S     # take the stamp from the environment, not a guess
```

Write `.claude/handoff/<stamp>.md`:

```markdown
# Handoff — <date> <time>

**Item.** SPEC §11 item N, or "infrastructure", or what it was.

**Paths I touched.** Explicit list. This is the file's whole reason to exist —
it is what lets a later session claim the work under /preflight step 0. Name
every path, and mark each as new / modified.

**State.** What is done, what is half-done, and what has not been started.
Be specific about half-done: "the A-test is written and failing as intended,
the implementation is not begun" is useful; "in progress" is not.

**Verified.** The commands actually run and their real outcome. If the suite
was not run, say that. An honest "not verified" is the point.

**Next.** The single next action, concretely enough to start on.

**Do not.** Anything a later session might reasonably try that would be wrong —
an approach already abandoned, a file another session holds, a test that is
failing deliberately.
```

## Rules

- **Never** claim a path another session is holding. Check `git status
  --porcelain` and cross-check against your own transcript; if you cannot tell,
  say which paths are uncertain rather than claiming them.
- Record what was **verified by running**, not what looks right.
- A measured number or an abandoned approach belongs in `docs/DECISIONS.md` via
  `/decide`, not here. Handoff notes are transient; decisions are permanent.
  When both apply, `/decide` first, then reference it here.
- Delete the note once the work is shipped. A stale handoff is worse than none,
  because it reads as current.

## Resuming from one

`/recall` for the decisions, the note for the paths, then `git diff -- <path>`
for each path it claims — confirming the diff matches what the note describes
before trusting it. If they disagree, the note is stale; believe the diff.

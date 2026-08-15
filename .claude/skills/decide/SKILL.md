---
name: decide
description: Append an entry to docs/DECISIONS.md in the required format, applying the four-category filter. Invoke as /decide the moment a decision is made, not at the end of a session.
allowed-tools: Read, Edit, Grep, Bash
---

# Record a decision

Append to `docs/DECISIONS.md` the moment a decision is made. Sessions end by
context exhaustion or a closed laptop, and neither offers a chance to write
things down afterwards.

## What goes in — exactly four things

- A **spec ambiguity** found, and how it was resolved.
- An **approach tried and abandoned**, with the reason it failed.
- A **measured number** that is expensive to reproduce.
- **Work left deliberately incomplete**, and what it is waiting on.

## What does not

Anything derivable from the code, the tests, `git log`, or
`scripts/status.py`. Apply this test before writing: *could a fresh session
recover this by running the status script or reading the diff?* If yes, it
does not belong here — say so and write nothing rather than padding the file.

New ideas that would touch a frozen architectural decision go to
`docs/BACKLOG.md` per SPEC §13, never here.

## How

Append only. Newest at the bottom. **Never edit or delete an existing entry** —
if a decision is superseded, write a new one saying so and linking back.

Use the file's own template:

```
## YYYY-MM-DD — item N: short title

**Decision.** What was settled.
**Why.** The reasoning, including what the alternative would have cost.
**Closes off.** What this rules out, or what still depends on it.
```

Get the date from the environment context, not from a guess. Read the last
entry before writing so the new one does not repeat it.

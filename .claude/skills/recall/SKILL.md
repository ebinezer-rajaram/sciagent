---
name: recall
description: Search docs/DECISIONS.md for a decision, measurement or abandoned approach already recorded there, rather than re-deriving it. Use proactively before answering why the project is the way it is, whether something was already tried or measured, how long something takes, or why work was skipped or deferred — even when the user never mentions decisions or history. Questions shaped like "why does X work this way", "did we already try Y", "was that already measured", "has this been proposed before", or "before I redo Z" are answered from that file. It is over 220KB; this greps headers and reads only what matches, and fans out across slices when the question spans the corpus.
allowed-tools: Bash, Read, Grep, Agent
---

# Recall a decision

`docs/DECISIONS.md` is the only place holding what the repository cannot tell
you. It is also **over 220KB across more than 100 entries** and grows by roughly
40 lines a session, so reading it whole costs upwards of 55k tokens and will only
get worse. Append-only means it never shrinks, so treat every figure here as a
floor rather than a measurement — they were taken on 2026-08-16 and move one way.
Do not restate them as exact: a session that appends three entries falsifies its
own copy of the number.

**Never read the file end to end.** Read headers, then read the two or three
entries that match.

## Method

### 1. List the headers, not the bodies

```sh
grep -n '^## ' docs/DECISIONS.md
```

Most entries are `## YYYY-MM-DD — <scope>: <title>`, but **the scope is optional
and a handful of entries have none** — seven as of 2026-08-16. The date is the
only part you can rely on, so never filter on the scope alone. A hundred of those
is a page; a hundred entries is a book. Scan the titles.

### 2. Narrow by whichever axis the question has

```sh
grep -n '^## .*item 13' docs/DECISIONS.md          # by backlog item
grep -n '^## 2026-08-1' docs/DECISIONS.md          # by date
grep -niE 'minutes|seconds|measured' docs/DECISIONS.md | grep -iE 'suite|gate'
```

The last form is how the suite's whole timing history came out in one command:
2m27s at item 6 through 6m50s at item 12, with `tests/test_oracle.py` named at
97 seconds. That is the shape to aim for — a grep that returns *data*, not a
grep that returns a reading list.

Note the scope word is not always `item N`. Entries are also filed under
`infrastructure:`, `review:`, and occasionally nothing at all. A search for
`item 13` alone will miss them, so search the topic as well as the item.

### 3. Read only what matched

Get the line number from the header grep, find the next `^## ` after it, and
read that range with `Read` using `offset` and `limit`. One entry averages 36
lines; the longest is 163.

## When the question is corpus-shaped, sweep instead

The method above answers "was X decided". It fails on questions that span the
file, because **an entry can bear on a question without using its words** — the
suite's whole timing history came out only from a hand-built compound grep, and
a plainer one would have returned a reading list. Reading two or three entries
then yields a confidently incomplete answer, which is worse than a slow one.

**Fan out when either holds**, and not otherwise:

- The question is corpus-shaped: *everything about X*, *all the measurements of
  Y*, *what have we abandoned*, *has this ever come up*.
- The §2 narrowing grep returns **more than about eight** candidate headers —
  meaning it did not narrow, and picking three of them is picking arbitrarily.

Anything else takes the cheap path above. Four agents on a question one grep
answers is waste, and the narrow path is the common case.

### How to slice

§1 already produced what you need: `grep -n '^## '` gives every header's line
number. Cut that list into **four contiguous ranges at entry boundaries** —
roughly 25 entries and 950 lines each — so every line is covered exactly once,
with no overlap and no gap. Range four ends at the end of the file.

Launch four `decisions-sweeper` agents **in a single message** so they run
concurrently, each given the question verbatim and one range. They are read-only
and do no CPU work, so the rule against running subagents alongside the suite
does not bite here — unless a suite is actually running, in which case it does.

**If `decisions-sweeper` does not resolve, do not abandon the sweep.** The agent
registry is read at session start, so the agent is unavailable in the session
that creates it, and it may also be unavailable in a worktree if the registry is
read from the shared checkout — unsettled as of 2026-08-16. Either way the fix
is the same: launch four `general-purpose` agents instead, at `sonnet`, pasting
this file's sweeper contract into each prompt. The slicing and the merge are
unchanged. Say which form you used, because only one of them is the agent whose
definition you can point at afterwards.

### Merging four reports

- The supersession rule below still governs, and now matters more: a later entry
  may overturn an earlier one across a slice boundary, where no single sweeper
  could see both. Order the union by date before drawing a conclusion.
- A sweeper reporting nothing is a covered slice, not a failed one. Say the
  sweep covered the whole file — that coverage is the point of it.
- Do not re-read an entry a sweeper already quoted. Read one yourself only when
  the question turns on something the quote does not settle.

## Reporting

- Quote the **Decision** and the **Why**, and give the entry's date and title so
  it can be found again.
- If several entries touch the topic, say so and give the **newest** — the file
  is append-only and a later entry may supersede an earlier one without the
  earlier one being edited, because editing is forbidden.
- If nothing matches, say **"no decision recorded on this"** plainly. That is a
  real and useful answer: it means the question is open, and whatever gets
  settled now should go back in via `/decide`.

Do not infer a decision from the code and report it as though it were recorded.
The file exists precisely for what the code cannot say.

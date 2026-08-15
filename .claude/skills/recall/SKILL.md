---
name: recall
description: Search docs/DECISIONS.md for a decision, measurement or abandoned approach already recorded there, rather than re-deriving it. Use proactively before answering why the project is the way it is, whether something was already tried or measured, how long something takes, or why work was skipped or deferred — even when the user never mentions decisions or history. Questions shaped like "why does X work this way", "did we already try Y", "was that already measured", "has this been proposed before", or "before I redo Z" are answered from that file. It is 190KB; this greps headers and reads only what matches.
allowed-tools: Bash, Read, Grep
---

# Recall a decision

`docs/DECISIONS.md` is the only place holding what the repository cannot tell
you. It is also ~190KB across ~90 entries and grows by roughly 40 lines a
session, so reading it whole costs about 50k tokens and will only get worse.
Append-only means it never shrinks.

**Never read the file end to end.** Read headers, then read the two or three
entries that match.

## Method

### 1. List the headers, not the bodies

```sh
grep -n '^## ' docs/DECISIONS.md
```

Every entry is `## YYYY-MM-DD — <scope>: <title>`. Ninety of those is a page;
ninety entries is a book. Scan the titles.

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

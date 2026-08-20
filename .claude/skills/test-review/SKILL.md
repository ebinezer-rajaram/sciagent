---
name: test-review
description: Review a freshly written test against the standard it is meant to encode, while the test is still the only thing that exists. Invoke as /test-review after watching a new test fail and before writing the code that makes it pass. /next calls it once at its §3 step 3; ad-hoc work that has any testable claim calls it directly.
effort: max
---

# Review the test, before you build to it

Do this *now*, while the test is the only thing that exists. Once the
implementation lands, a weak test and a sound one both go green and nothing
tells them apart; `/preflight`'s `/code-review` sees the test only in that final
form, so this is the one moment anything looks at it alone.

Nothing else covers this. `/gate` guards the two adjacent failure modes — no
test written, test skipped — and `suite-runner` is forbidden from acting on a
test it believes is wrong. A test that passes for the wrong reason is caught by
nobody, and a vacuous `test_aN_` still counts as a covered gate in
`scripts/status.py`.

## 0. Three inputs, all required

1. **The standard** the test is answerable to — see below.
2. **The test**: absolute path and class name. Not an excerpt; see §3.
3. **The observed failure**: the pytest output from watching it fail, in full.

The third is not optional and cannot be reconstructed later. Question 1 of the
brief is entirely about it: an `ImportError` or a fixture error is a red that
proves nothing, and only the actual output distinguishes that from a genuine
assertion failure. If you have not run the test and watched it fail, stop and do
that first — there is nothing here to review yet.

### Establishing the standard

Where the standard comes from depends on the work, and the three cases are not
equally strong:

- **A gated backlog item** — the SPEC §6 text for the criterion, verbatim. This
  is the strong case: the standard was written before the test, by someone
  answering a different question.
- **An untracked backlog item** — its §11 row, plus your own statement of what
  the test is meant to establish.
- **Ad-hoc work** — the user's request, plus your own statement of what the test
  is meant to establish.

In the second and third cases the standard is a claim *you* wrote, about a test
*you* wrote, and the lens can only hold the test to what you gave it. Two rules
make that worth something rather than circular:

- **Write the claim down before you launch the lens**, in the words you would
  have used before the test existed. A claim composed while looking at the
  assertion will describe the assertion.
- **Do not soften it to fit.** If the honest claim is wider than what the test
  checks, that gap is the finding — hand the lens the wide claim and let it say
  so. Narrowing the claim until the test satisfies it is how this step becomes
  theatre.

## 1. Launch it

**This skill owns the fan-out.** One agent per gate the item names; for
untracked or ad-hoc work, one for the test set you chose. In one message if
there is more than one. Callers invoke this skill *once* and hand it every gate
— `/next` says so explicitly, because invoking per gate and then fanning out per
gate again squares the agent count.

Give each agent the standard's own text, the test path, and the failure you just
watched:

> You are reviewing a test, not the code it will eventually test. You have
> not seen the author's reasoning; do not ask for it.
>
> Standard: `<SPEC §6 text for AN — or the §11 row / the user's request, plus
> what the test is claimed to establish>`
> Test: `<absolute path>`, class `<TestAN...>`. Read the whole module as
> needed; helpers, fixtures and sibling tests are in scope as context,
> since they are what the test actually runs with.
> Observed failure: `<the pytest output, in full>`
>
> Answer three questions, each with `file:line` evidence:
>
> 1. Did it fail **at the assertion**, or before reaching one? An
>    `ImportError`, a collection error or a fixture error is a failure that
>    proves nothing about what the test asserts. Quote the line the traceback
>    ends on. Then check the failure came from **this** test: if the message,
>    the line number or the data in it cannot be produced by the source
>    above, say so — an uncorroborated red is not a watched failure.
> 2. Does the assertion encode the standard, or something **weaker** that
>    the standard merely implies? Name the gap if there is one.
> 3. Name a wrong implementation this test **accepts and the standard
>    rejects**. That gap is the only thing that makes a test too weak.
>    An implementation the standard *also* accepts is out of scope however
>    unsatisfying it looks — do the arithmetic, say it clears the standard,
>    and do not count it against the test. If no such gap exists, say so
>    explicitly; that is the finding, and it is the common answer.
>
> Verdict: **SOUND** (no gap between test and standard) or **TOO WEAK**
> (name the gap, and show the arithmetic that puts it outside the
> standard). Say which question drove it.
>
> If you think the *standard itself* is too weak, that is worth saying —
> but report it separately and do not let it change the verdict. The test
> is answerable for the standard, not for correctness in general.
>
> Report findings only. Do not edit anything.

## 2. Which agent, and which model

Run it on **`evidence-checker`** with that brief inlined, not on a new named
agent and not on `general-purpose`. It is already on `main`, so it resolves from
any worktree — `docs/DECISIONS.md` (2026-08-16) records that an agent authored
in a worktree cannot run there until its branch lands. It fits by contract:
CLAUDE.md already names it for "independently verifying a claim you have already
made", and *this test encodes the standard* is exactly that claim. And its
toolset (`Read, Glob, Grep, Bash`) is the narrowest that still does the job — no
`Edit`, no `Write`, so it cannot casually modify what it is reading, and
arithmetic goes through `Bash`.

**Do not overstate that last point, because the obvious overstatement is
false.** `Bash` writes files, so "this agent *cannot* edit" is not true of
`evidence-checker` and is not true of `invariant-auditor` either — both are
`Read, Glob, Grep, Bash`, and `decisions-sweeper` is the only agent here that is
mechanically write-incapable. Every read-only guarantee this repository runs on,
including `/preflight`'s four lenses, is **contractual**: it holds because the
brief says report-only and the agent obeys it. That is the standing this lens
has too. It is a real reason to prefer the narrow toolset over
`general-purpose`'s full one, and not a reason to claim a guarantee nothing
enforces.

**Omit the model**, so it inherits the session's. Graded by what a false
negative costs, per CLAUDE.md: a missed vacuous test is a silent wrong entry in
whatever contract it was supposed to encode, and no grep recovers it. Same
reasoning that leaves `/preflight`'s lens 2 unpinned.

**Never run this alongside a suite.** It goes before the verification run, not
beside it — the contention rule is absolute, and four read-only agents were
measured on 2026-08-16 costing a `-n 4` run 12–15%.

## 3. Give it the path, never a pasted excerpt

Measured while this step was written: the same A7 test judged as a 40-line
extract came back TOO WEAK on a gap that does not exist in the module, because
the guard closing it lives in a *sibling* criterion's test over the same
`lru_cache`d fixture (`assert len(rows) == TRIALS`, A6). Excerpting changes the
answer. The module is the unit.

## 4. Check a finding against `/recall` before acting on it

The lens will rediscover settled decisions and argue with them — it has no
access to `docs/DECISIONS.md` and no way to tell a defect from a choice.
Measured on the same probe: it reported A6 and A7 measuring one statistic as an
invariant-5 defect, having read and then argued against the very module comment
that encodes the 2026-08-03 decision resolving it ("A6 fixes the *standard*, A7
fixes the *number*"). A real finding survives that check; most of this class
will not.

## 5. Act on the verdict

Fix what it finds *in the test*, then **re-run the test under pytest** and watch
the corrected version fail again — the lens is not what gets re-run, and a
corrected test that was never executed again is an unwatched failure. A finding
you disagree with needs a stated reason, not silence.

Then implement. This skill reviews the test and stops there; it does not write
the code, run the suite, or commit anything.

## Naming, on the ad-hoc path

`test_aN_` names are for declared acceptance criteria and nothing else — SPEC §6
declares A1–A24, and `docs/BACKLOG.md`'s gated entries declare A25 onward on
their `**Gate.**` lines. `scripts/status.py` reads `^test_a0*(\d+)_` off the
final `::` component and attributes the test to gate N; backlog rows then report
coverage as "does gate N have any test at all". So a test named `test_a7_` for
unrelated ad-hoc work makes A7 read as covered when nothing new covers it.

A number *neither* file declares is worse than it used to look. It reaches no
row — the report is built from the declared namespace — and `gate_of` still
matched it, so it is excluded from the "not named for a gate" tally as well. It
disappears rather than merely miscounting.

Name ad-hoc tests for what they check. The gate namespace is not a general
prefix.

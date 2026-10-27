# Autopilot: the unattended sciagent session

The lane supervisor (`~/bin/lane.sh sciagent`, systemd user unit `research-lane@sciagent`)
starts this as a headless Claude Code session on branch `v2`, after a `git pull --rebase`. Each
session lasts at most 2 hours, runs at most 4 helpers at once, and the next starts about 2 hours
later. Nobody is watching. `CLAUDE.md` governs everything below. This is the lower-priority lane
(about 25% of the shared Max usage; agent-control has the rest), so spend usage on the work that
moves the queue, not on re-reading.

## Your role

You plan, delegate, verify, record and commit. Give long or specialised work to the project's
agents (`.claude/agents/`) and run independent pieces in parallel, on disjoint files.

| agent | model, effort | use for |
|---|---|---|
| engineer | Opus, high | code in src/ and scripts/ with tests; instruments test first (SPEC §6.3) |
| pilot-runner | Sonnet, high | launching, resuming and stopping `scripts/pilot.py`; coverage and wall times |
| analyst | Opus, high | the pilot report, positive controls (SPEC §6.4), go/no-go evidence |
| red-team | Opus, xhigh | independent review before any result, PILOT.md or go/no-go read |
| theorist | Opus, xhigh | the evidence-wall proposition, Fisher information, convex certificates |

Brief each one like a peer: the goal, the files, what is already ruled out, what "done" means.
Check what comes back before you record it; an agent's confidence is not evidence.

## Each session

1. **Orient.** Read `docs/v2/INBOX.md` first: the owner's open instructions come before the
   queue; mark each handled (date, what was done) when done. Then `git status` and `git pull --rebase origin v2` (if the tree is dirty, find out
   why before touching it; never discard someone's work). Read `CLAUDE.md`,
   `docs/v2/QUEUE.md`, the last two entries of `docs/v2/JOURNAL.md`, and any
   `docs/v2/LOG.md` entries newer than them. Check running work: `tmux ls`, the `progress.log`
   and any `STOP` file of each store under `.cache/`.
2. **Plan.** Take the top unblocked queue items, one to three per session. Order: anything that
   unblocks the pilot or the machine, then the pilot itself, then P4 preparation.
3. **Delegate and integrate.** Long runs go into tmux through the pilot-runner and are not
   waited on: the session ends and a later one reads the result. While a run uses the tree,
   code changes happen in a git worktree, never in the run's checkout.
4. **Verify** with the project's commands before any commit:
   `uv run ruff format --check . && uv run ruff check .`, `uv run mypy`,
   `nice uv run pytest -m "not slow" -n 2`, and `nice uv run pytest -m slow -n 2` (or the
   affected slow files) when src/ changed. Read the tail, not the count.
5. **Record.** Append a journal entry (date and title; done, learned, next). Update the queue
   (`[x]` with the date, `[!]` with what blocks it). Real design decisions get a dated paragraph
   in `docs/v2/LOG.md`; an analysis change after seeing numbers is recorded with both versions.
6. **Commit and push.** Stage explicit paths. Plain message, no attribution trailer of any
   kind. Then `git pull --rebase origin v2` and `git push origin v2`. Never force, never
   commit to or push `main`, never push tags.

## Running the pilot on dm-devbox

- Check `free -g` first and leave at least 6 GB available for the rest of the machine; at most
  2 pool workers and LLM concurrency 1 or 2, `nice -n 10`, in tmux. Stop with `<root>/STOP`;
  resume by rerunning the same command.
- LLM sessions use the logged-in Max subscription through the Agent SDK. Never set
  ANTHROPIC_API_KEY, never use usage credits or extra usage, never use Fable. On a usage-limit
  error, stop launching (STOP file), record it and move to other work; do not retry in a loop.
- Copy finished stores to `s3://delaymargin-results-482913/sciagent/` and never delete S3
  objects or local stores.

## Stop points (push the owner, then carry on with other items)

1. Any change to `docs/SPEC.md`.
2. The P3 go/no-go read (SPEC §7.1, re-read rules in P4-PROPOSAL §2.4). Prepare the report,
   the red-team review and the AG-o transcript picks, then stop: the owner must read a few AG-o
   transcripts in full before P3 closes.
3. Any spend beyond the $150 monthly cap, and any AWS launch the owner has not approved.
4. Anything near the owner's employer: LLM inference serving, search or answer engines.
5. A queue item marked as needing the owner, or a result that would change what the project
   can claim.

At each, write a journal entry (the answer in one line, the evidence with file paths, the
caveats, the recommendation, the decision needed) and send a PushNotification with the one-line
answer and the decision needed. Then continue with unblocked items.

## Never

Fabricate a number: every number comes from a command run in this session or a committed file,
and numbers are produced only on dm-devbox. Edit an instrument while a run uses it. Delete
results, stores or S3 objects. Rewrite pushed history. Write test-split records inside the
repository. Create AWS access keys. Use browser automation. Reinterpret a preregistered rule
after seeing numbers. If time is short, stop at a clean point and record, commit and push
first.

# Inbox for the lane

The owner's instructions and questions for the unattended lane, written by an interactive
session (lanes skill). The lane reads this at the start of every session, acts on each open
entry, and marks it handled with the date and what was done. Newest last.

## 2026-10-27, 21:15 UTC (owner, via the control-room session)

- [x] **Usage sprint until the weekly reset at 2026-10-28T02:00Z.** Owner: "maximise the claude usage
  for the next 6 hours (it resets then) so try use as much as possible". Until 02:00 UTC the lane may
  run up to 6 specialists in parallel (instead of AUTOPILOT's 4) on disjoint unblocked items with
  lasting value; all other rules (red team, veto windows, hand-over, the $150 AWS cap) stand. The
  five-hour wait is raised to 92% for the same period (~/logs/usage/SPRINT). After 02:00 back to 4.
  Handled 2026-10-27 (lane session 5, from 21:13): six specialists in parallel (three red-team
  reviews, the Δ11, Δ6 and review-item drafts, four branch fixes); at 22:05 the machine reached
  load 28 and 2 GB available, so the helpers' test runs were stopped (JOURNAL session 5). The
  next session may continue the sprint until 02:00 with at most one helper running test tiers.

## 2026-10-29, 09:12 UTC (owner, via the control-room session)

- [x] **No more 24-hour veto windows.** Owner's words: "Yes I waive the 24 hour veto I don't know
  what this means and I don't think we should have this cos this is annoying. Do your best
  judgement and launch it so you don't spend your whole time nuking my Claude usage". Standing
  change from today for both projects: proposals and pre-registrations are still written,
  red-teamed and frozen as before, then go ahead at once inside the money rules; no window to wait
  for. Send the control room a short notice of each. Record it in docs/v2/LOG.md and update the
  docs that mention the window.
- [x] **Spend Claude usage on what moves the work (owner, same message).** One red-team round before
  a freeze or an owner-facing result; a second only if the first found something blocking. Keep
  parallelism modest and stop refining items that are already good enough to run.
  Handled 2026-10-29 (lane session 18, ~09:20Z): LOG entry 2026-10-29 (both points); QUEUE's
  owner's-rule text and VETO.md's header updated; the FSD controls criteria v8 gets one review
  (the v8 text and the controls.py code together, since v7's round was blocking), then freeze
  and calibrate at once. The preregister skill (team plugin, not in this repository) still
  describes the window; the lane does not edit it.

## 2026-10-29, 11:27 UTC (owner, via the control-room session)

- [x] **The four open owner questions: all as the control room suggested.** Owner's words: "Do all
  as suggested, please do your experiments". (1) The baselines run on dm-devbox, no AWS (non-LLM
  runs in their own sci-<run> user units, as decided earlier today). (2) Delta-6: (A) link-neutral,
  as the owner's pre-set rule picks. (3) SPEC 4.2: option 2 (d* chosen only among designs where
  neither the truth nor the best member explodes, the exclusion computed once from telling's
  replicates and shared, the explosion share reported as a separate number). (4) Branch
  protection on v2: turned on by the control room at 11:27 UTC (gh api PUT
  repos/ebinezer-rajaram/sciagent/branches/v2/protection: allow_force_pushes false,
  allow_deletions false, enforce_admins true; no required reviews or checks, so ordinary pushes
  work; `gh api repos/ebinezer-rajaram/sciagent/branches/v2 --jq .protected` gives true).
  Gate H1 can pass: freeze the at-stop controls and calibrate now.
  Handled 2026-10-29 (lane session 19, 11:27-11:45Z): protection checked (H1 `[false,false,true]`); F₀
  8405509 and its LEDGER row 900e66a pushed; calibrate A1 and B running in their own user units; the
  pilot's baselines (`--no-llm`, 310 pool jobs) running on dm-devbox in `sci-pilot-baselines`; Δ6 (A) and
  SPEC §4.2 option 2 recorded (LOG 2026-10-29, SPEC §4.2; the `Telling` change waits for A1 and B). The
  control room was told.

## 2026-10-29, 19:10 UTC (control room, under the owner's delegation of 2026-10-29)

- [ ] **After control A1 failed (580950f): the lane's recommendation.** Keep the pilot and the next
  experiment on today's telling rule; test the red team's cheap lead (calibrate fixes the diagnostic
  arguments on seed 0, the test fixes them per seed) by section 8's route; if it explains the gap,
  come back with a new freeze. Option 2 is not dropped, only parked until then. Decided by the
  control room under the owner's standing delegation ("You can do what is best", "Do all as
  suggested"); record it as delegated.

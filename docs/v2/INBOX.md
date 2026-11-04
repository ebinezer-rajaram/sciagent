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

- [x] **After control A1 failed (580950f): the lane's recommendation.** Keep the pilot and the next
  experiment on today's telling rule; test the red team's cheap lead (calibrate fixes the diagnostic
  arguments on seed 0, the test fixes them per seed) by section 8's route; if it explains the gap,
  come back with a new freeze. Option 2 is not dropped, only parked until then. Decided by the
  control room under the owner's standing delegation ("You can do what is best", "Do all as
  suggested"); record it as delegated.
  Handled 2026-10-29 (lane session 23, 19:13Z): recorded as delegated in LOG 2026-10-29; the queue's FSD item says
  option 2 is parked; the seed lead was given to the theorist as an exploratory check (output
  `.cache/drafts/fsd-seedlead/`, nothing written in the frozen directory). The pilot runs on today's telling, unchanged.

## 2026-11-01, 11:00 UTC (owner, via the control-room session)

- [x] **One short session, then hold until the reset (Wed 4 Nov 02:00 UTC).** The weekly Claude reading is
  92%; the owner wants the machine busy until Wednesday without spending Claude usage. In this one
  session (one hour at most, two helpers at most): start, in their own `sci-<run>` user units,
  every frozen or ready CPU-only run that needs no LLM calls and no Claude session (the pilot
  baselines with --no-llm, the seed-lead test of section 8 if it is CPU only, anything else already
  frozen), sized to keep dm-devbox's cores busy until Wednesday (nice 10; leave two cores free).
  Nothing that calls an LLM (that uses the owner's Claude plan) until the reset. No code beyond
  what a launch needs, no reviews, no drafting. Journal what you started and stop.
  Handled 2026-11-01 (lane session 25, 10:44-10:55Z): both pilot halves had already finished, and so had
  seed-lead check (a). The only ready CPU-only work, the p4-rehearsal slow tier, was started in `sci-p4-slow`.
  The pilot stores were checked and copied to S3. Nothing else is frozen, so the machine stays mostly idle
  until the reset. JOURNAL session 25.

## 2026-11-04, 08:00 UTC (control room)

- [ ] **fit/5 search losses: pin T2 and document the known losses.** Three losses in 115 BIG[0]
  softplus cases, worst 0.0114 nats per event, below the 0.0227 that would flip too-easy and moving
  no section 7.1 outcome. Report them as a found stratum (not a rate) in the method note; no restated
  bound, no fit/6 for the pilot. Unblocks the p4-deltas slow tier. Decided by the control room under
  the owner's standing delegation; record it as delegated.
  Lane note 2026-11-04 (session 27): left open. Its figures came from the lane unreviewed and were wrong (a 0.0283 loss found, and the comparison was invalid; LOG 2026-11-04). Correction sent to the control room; `t2-pin` waits for its answer.
- [x] **P3 go/no-go and check (b) of the FSD seed lead: relayed to the owner.** The go/no-go needs the
  owner to read the five AG-o transcripts; check (b) needs the owner's own approval of the
  ctlA1-boot-r2 bypass. Neither is decided here. Carry on with unblocked items.
  Handled 2026-11-04 (lane session 27): noted; both stay open in the queue for the owner.

## 2026-11-04, 08:30 UTC (control room)

- [ ] **fit/5: the 08:00 decision is WITHDRAWN.** It rested on figures the red team has since disproved
  (worst loss 0.0283 per event, not 0.0114; losses on three strata including the pilot's dev-015 s3;
  in-sample and held-out figures not comparable). New decision: restate the bound under a rule
  written first. Before any new search is run, write and red-team (one round) a short rule that
  says how misses on the pilot's own units are counted and how their held-out size is measured.
  Then run that CPU-only check over every pilot unit, report how many carry a miss and their held-out
  effect, and say whether the computed GO survives. Until then the go/no-go is not put to the owner as
  final. fit/6 stays a SPEC-level item for after the pilot. The T2 pin stays off v2. Decided by the
  control room under the owner's standing delegation; record it as delegated.

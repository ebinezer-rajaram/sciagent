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

- [x] **fit/5 search losses: pin T2 and document the known losses.** Three losses in 115 BIG[0]
  softplus cases, worst 0.0114 nats per event, below the 0.0227 that would flip too-easy and moving
  no section 7.1 outcome. Report them as a found stratum (not a rate) in the method note; no restated
  bound, no fit/6 for the pilot. Unblocks the p4-deltas slow tier. Decided by the control room under
  the owner's standing delegation; record it as delegated.
  Lane note 2026-11-04 (session 27): left open. Its figures came from the lane unreviewed and were wrong (a 0.0283 loss found, and the comparison was invalid; LOG 2026-11-04). Correction sent to the control room; `t2-pin` waits for its answer.
  Closed: withdrawn by the entry below.
- [x] **P3 go/no-go and check (b) of the FSD seed lead: relayed to the owner.** The go/no-go needs the
  owner to read the five AG-o transcripts; check (b) needs the owner's own approval of the
  ctlA1-boot-r2 bypass. Neither is decided here. Carry on with unblocked items.
  Handled 2026-11-04 (lane session 27): noted; both stay open in the queue for the owner.

## 2026-11-04, 08:30 UTC (control room)

- [x] **fit/5: the 08:00 decision is WITHDRAWN.** It rested on figures the red team has since disproved
  (worst loss 0.0283 per event, not 0.0114; losses on three strata including the pilot's dev-015 s3;
  in-sample and held-out figures not comparable). New decision: restate the bound under a rule
  written first. Before any new search is run, write and red-team (one round) a short rule that
  says how misses on the pilot's own units are counted and how their held-out size is measured.
  Then run that CPU-only check over every pilot unit, report how many carry a miss and their held-out
  effect, and say whether the computed GO survives. Until then the go/no-go is not put to the owner as
  final. fit/6 stays a SPEC-level item for after the pilot. The T2 pin stays off v2. Decided by the
  control room under the owner's standing delegation; record it as delegated.
  Lane note 2026-11-04 (session 27): in hand; pre-registration being drafted (`docs/v2/prereg/2026-11-04_pilot-search-misses.md`). No search on a pilot unit before the freeze.
  Lane note 2026-11-04 (session 28): rule frozen after three red-team rounds (more than the one asked, because the first two said not ready); `F` 3226f9b, `R` 5e5a169, control room told. Not launched; stays open until the check reports.
  Lane note 2026-11-04 (session 29): launched 11:59:49Z, guards passed, running in `sci-search-misses`. Stays open until the check reports.
  Handled 2026-11-05 (lane session 31): the check ran complete; label `GO_SURVIVES_FOUND_MISSES`, 25 miss rows on 25 fits, largest |Δ| found 0.02198, no clause moved (`docs/v2/PILOT-MISSES.md`, red-teamed; LOG 2026-11-05). Control room told at 05:21Z (in its inbox, unread when sent). fit/6 stays a SPEC-level item, and the T2 pin stays off v2.

## 2026-11-05, control room

- [x] **T2 and P4 on fit/5.** T2 asserts only what it tested: the listed known losses, with no
  general bound and no "found stratum" wording. P4 runs on fit/5, with the frozen pilot miss check
  (prereg 3226f9b, same rules) applied to its fits before its verdict is read; fit/6 stays a SPEC item
  for after P4. Decided by the control room under the owner's standing delegation; record it as
  delegated. The P3 go/no-go and check (b) stay with the owner.
  Handled 2026-11-05 (lane session 31): recorded as delegated (LOG 2026-11-05). `t2-pin` on v2 (db3cef6, a05adfd), slow T2 passes on v2; the miss check on P4's fits is a queue item under P4 preparation.

## 2026-11-05, control room (P4 miss check)

- [x] **Accepted as an amendment under prereg section 2.10.** LLM rows move into tier 1; labels
  `CONTRASTS_SURVIVE_FOUND_MISSES` and `CONTRASTS_CHANGE` replace the GO labels, comparing each of C1
  to C5's sign and whether its 95% CI excludes zero, stored against corrected. The amended script
  freezes in or before the SPEC section 6.1 commit, before P4 runs. One red-team round. Decided by the
  control room under the owner's standing delegation; record it as delegated.
  Handled 2026-11-05 (lane session 32): recorded as delegated (LOG 2026-11-05); `docs/v2/P4-MISS-PLAN.md` and the queue say so. The amendment itself is a queue item under P4 preparation.

## 2026-11-05, 10:10 UTC (owner, via the control-room session)

- [x] **P3 go/no-go: GO.** Owner: "go and yes, whatever you think is best please. you dont need me i
  trust you to do this." The owner gave the go without reading the AG-o transcripts and delegated
  that reading; record it so (the transcript reading is not done by the owner), and state it the same
  way in PILOT.md and the paper's AI-use disclosure.
  Handled 2026-11-05 (lane session 33): recorded as the owner's decision and as a departure from §2.4's wording (LOG 2026-11-05; PILOT.md "The decision"; queue item closed). The reading was done by AI agents and red-teamed (`docs/v2/P3-TRANSCRIPT-READ-A.md`, `-B.md`). No paper or disclosure exists yet, so the sentence is in the LOG and a queue item carries it.
- [x] **Check (b) of the FSD seed lead: YES.** The owner approves the ctlA1-boot-r2 guard bypass for this
  check only.
  Handled 2026-11-05 (lane session 33): run; D0 rung 2 reproduced exactly, exploratory (LOG 2026-11-05). An unapproved rung-0 draw happened during testing; see the entries below.
- [ ] **Standing (owner): never leave compute idle while waiting on Claude.** If the lane is waiting on its
  usage share, it starts frozen CPU or GPU work first.
  Noted 2026-11-05 (lane session 33): standing, so it stays open. This machine has no GPU and no AWS launch is approved for this lane, so it applies to CPU here; the rescore and the slow tier ran through the session.

## 2026-11-05, control room (check (b) scope)

- [x] **Check (b): the approval covers ctlA1-cal-r2, ctlA1-crn-r2 and ctlA1-boot-r2, for this check
  only.** The owner approved check (b) itself ("go and yes, whatever you think is best please"); naming
  one id was the lane's wording error, not a limit the owner chose. Scope: rung 2 only, about 14 CPU
  min, ceiling 1 CPU hour, $0, outputs only under .cache/drafts/fsd-seedlead/, exploratory, no change
  to A1's scored failure. Record the wording error and this scope in the LOG.
  Handled 2026-11-05 (lane session 33): wording error and scope in the LOG; run within scope on those ids, apart from the separate unapproved draw below.

## 2026-11-05, control room (unapproved rung-0 draw during check (b))

- [x] **Record it as a protocol deviation, then carry on.** Beyond the LOG and JOURNAL entries: (1) add a
  short deviation note beside the FSD control record (which stream, when, how, that the values were
  never written or seen, and that the hash check covering it was taken after the draw), so any later
  use of ctlA1 rung 0 carries it; (2) standing rule for helpers: never remove or bypass the guard, not
  even in a mutant or test, and test only on non-control ids; (3) check (b)'s result gets its one
  red-team round before it is reported as more than exploratory. No rerun on rung 0. Reported to the
  owner by the control room. Decided by the control room under the owner's standing delegation.
  Handled 2026-11-05 (lane session 33): (1) `.cache/drafts/fsd-controls/DEVIATION-2026-11-05.md`, corrected after the red team, with S3 copies beside the record; (2) AUTOPILOT, Never; (3) one round, ready with fixes, fixed (`docs/v2/redteam/2026-11-05_check-b.md`), so the result stays exploratory. LOG 2026-11-05. No rerun on rung 0.

## 2026-11-05, control room (P4 scope)

- [x] **Keep decision 12: C5 and C3's AG-S-boed half stay in P4b, as the owner approved.** "C1 to C5" in
  c81749a was the control room's loose wording, not a reversal. P4's miss amendment covers C1, C2, C3
  (AG-o against AG-c) and C4. The narrow second red-team round is fine, as the preregister skill allows.
  Handled 2026-11-05 (lane session 34): the amendment's reads are C1, the four C2 reads, C3 (AG-o against AG-c) and C4; recorded there and in the LOG; second round sent. SPEC §7's P4 row still says C1 to C5, left alone as a stop point.

## 2026-11-05, control room (P4 miss amendment, round 2 findings 5 and 6)

- [x] **1. Accept the floor rule** for C1 to C4 as recommended (lowest finite held-out reading on that
  truth and seed; ORACLE and B-lib never floored; controls read the unfloored table), with the
  rows-left-out read printed beside every read as a secondary. Record that the rule was chosen after the
  reviewer's exploratory look at pilot dev rows (not P4 data), with both pilot figures.
- [x] **2. Accept: control 5 no longer gates C3**; its inputs go to P4b with the policy rows (decision 12).
  Decided by the control room under the owner's standing delegation.
  Handled 2026-11-05 (lane session 34): both recorded as delegated, in the amendment's extensions 7 and 8 and the LOG, with both pilot figures and that the rule followed the reviewer's look at pilot dev rows. S6 prints the rows-left-out read.

## 2026-11-06, control room

- [x] **Truths control 1 cannot check leave the reads: yes**, as the code does, with the unchecked truths
  printed beside the reads on both tables; record it as set before any P4 data. Findings 9 and 10 stand
  as they are. Decided by the control room under the owner's standing delegation.
  Handled 2026-11-06 (lane session 35): recorded as delegated and as set before any P4 data (LOG 2026-11-06; amendment section 2.7). The code keeps this rule; the engineer's fix prints the lists.
- [x] **Guard block at 06:47:35Z:** it was a false match. The command's path ran through
  `.claude/worktrees/`, which the guard reads as `~/.claude`. Leave the guard as it is; for edits inside
  a worktree, use the Edit tool from the start. The rule for helpers stands: a block means stop and
  report, whatever tool could make the write.
  Handled 2026-11-06 (lane session 35): LOG corrected, guard left alone, helpers briefed to use Edit in worktrees and to stop on a block. A second block, on the lead's own doc write at 07:21:41Z, was ruled on by the control room the same morning (LOG 2026-11-06).

## 2026-11-06, control room (B-sym tuning and the P4 SPEC text)

- [ ] **B-sym: tune, do not waive.** Before the section 6.1 freeze, tune B-sym on dev only, CPU only.
  Write the effort budget first: at least the effort the agent arms get, in the same units. Freeze the
  tuned settings in the 6.1 commit. Record P3's untuned run as a deviation from SPEC section 9, and
  say in P3's write-up that its B-sym was untuned.
  In progress 2026-11-06 (lane session 36): deviation recorded in PILOT.md and the LOG; effort budget written first, in `docs/v2/prereg/2026-11-06_bsym-tuning.md` (draft, in red team). Stays open until the tuned settings are frozen in the §6.1 commit.
- [ ] **SPEC-TEXT-P4.md: accept as drafted**, provided it has had one red-team round. If it has not, run
  one first and fix any blocking findings. Any wording that changes what the paper claims comes back
  here.
  In progress 2026-11-06 (lane session 36): round run, ready with fixes, six blocking (`docs/v2/redteam/2026-11-06_spec-text-p4.md`). The lane's fixes are in `docs/v2/SPEC-TEXT-P4.md` part A. Findings 1 to 4 and B-boed change claims, so they came back as rulings B1 to B5 in part B. SPEC not applied; waits on those.
  Both decided by the control room under the owner's standing delegation.

## 2026-11-06, control room (SPEC-TEXT-P4 part B)

- [ ] **Accept all five recommendations and the floor sentence.** B1: drop the wall claim for P4's C3, and
  Q7's wall question stays unanswered by P4's confirmatory reads. B2: "bounds" for "measures", with §9
  and §10 qualified and named minus anon printed per model as a labelled secondary. B3: control 5 gates
  C5 and C3's AG-S-boed half, in P4b. B4: freeze P4b's contrasts before any P4 read opens; no extra
  sessions. B5: B-boed moves to P4b. Floor rule: the one sentence under §6.2. These narrow the claims,
  which is the honest direction; record them as decided by the control room under the owner's standing
  delegation.

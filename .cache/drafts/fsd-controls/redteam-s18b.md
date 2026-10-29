# Red team s18b: joint review of criteria v8 (b2bcc70c…) and controls.py (ac948694…), ctl_gate.py (ae1849a8…), test_controls.py (30cfff9b…)

Lane session 18, 2026-10-29, finished ~10:35Z. One round under the owner's rule (LOG 2026-10-29). Saved by the
coordinator from the agent's report; its scratch files are in /tmp/rv19/ (not kept).

**Verdict: ready to freeze.** Nothing blocking. Cheap fixes worth making before F0 (after F0 any change needs
F′). Open preconditions: H1 (branch protection, owner) and `cpu-total.txt` (created at freeze).

## Non-blocking, ranked

1. D0's bytes reach `$R/.git/objects` before the push is confirmed (`ctl_gate.py:681-723`, `hash-object -w`
   before push). Killed calibrate or 20 failed attempts leave an unreachable blob holding D0 (shown in a scratch
   repo: `unreachable blob …` → `{"rungs":[{"h":0.973,"reading":"passes"}]}`); contradicts criteria.md:380 and
   changelog line 17. Fix (~10 lines): build and push D in the temporary directory with
   `GIT_OBJECT_DIRECTORY=<tmp>/objects` and `GIT_ALTERNATE_OBJECT_DIRECTORIES=<$R/.git/objects>`; or add to §5
   Residual.
2. Tests that did not fail on wrong code (mutants in /tmp copies): telling stream args swapped; p_F without the
   forced e1 exclusion; D*0 size limit 2→3; II_MIN 84→83 and B1_MIN 108→107; D single-parent check loosened;
   "tree holds D0" on D-candidates removed; status walk without `--full-history`; C8 accepting `??`; the
   `sys.flags.isolated` check removed. Causes: stream tests compare the code with its own helpers
   (`test_controls.py:1593-1606, 1622-1630`); thresholds tested only at extremes; the bootstrap stub plants on e4.
   The code is right today (independent stream check: 0 mismatches; constants read against §0-§2). Fix: build
   `scorer_seed(TELLING,"ctlA1",s,side,e,r)` literally in the test; boundary tests (83/84, 107/108, 11/12, 2 vs 3
   breach seeds, 949/950, 989/990, |D*0| = 3); p_F with the plant on e1; a two-parent D; the isolated flag.
3. B's reference check ignores cut0 (`controls.py:1372` counts only "truncated"; §0 counts cut0 as truncated).
   Unreachable in practice (cut0 needs a shown end ≤ 0; T_A truncates at τ ≥ ~824). Fix: `is_truncated(x)`.
4. Gate git calls inherit `GIT_*` and honour `refs/replace/*` (`ctl_gate.py:148-166, 421-433, 726-742`), against
   §7's "no environment variable bypasses the gate". Fix: drop `GIT_*` from the subprocess env, set
   `GIT_NO_REPLACE_OBJECTS=1`.
5. Exit 1 where 10 is meant: `git()`, `blob_bytes()`, `try_push()` catch only TimeoutExpired (OSError → 1); a
   missing criteria.md or uv.lock (`ctl_gate.py:527, 554`) raises FileNotFoundError; a LedgerError from
   `ledger.at_exit()` inside main's GateError handler (`controls.py:2063-2066`).
6. Text, criteria.md:400: D0's "bytes repeat" on a rerun is false (records include LEDGER rows and status
   commits); say "values repeat".
7. Text vs code on exit 15: criteria.md:156 lists breaches as evidence on 14 or 15; code writes only the
   determinism evidence on 15 and the test asserts it. Clarify the text.
8. B with a failed reference check calibrates no rung (`controls.py:1387-1403`) against criteria.md:128 "Every
   rung … is calibrated"; VOID either way; say so.
9. The calibrate CPU ledger's `step-N` unit counts runs (`controls.py:956-968`), leaking the run rate before the
   push; use a charge counter.
10. Operational: a 1-1.6 CPU h calibrate is lost (exit 10 at the pre-push gate) if a new file appears in $D, HEAD
    leaves v2, or a gh or network call fails; rerunning score after its files are committed leaves a modified
    tracked file and C8 then refuses every phase of both controls. Also: once D is pushed, any F′ makes that
    control exploratory; finish the other control under F first (§5 allows it; say so).
11. Docstrings of `test_controls.py:1` and `ctl_det.py:1` still say v7.

## Question 2 (v8's changed text)

s18 fixes 1-4 hold. Gaming only through items 1 and 4, both deliberate. No deadlock (one lock released between
attempts; timeouts on fetch, push and gh; at most one D can land). No mislabelled honest restart.

## Checked

Hashes match; `CRITERIA_SHA256` = criteria.md at 1dc2ea1; no ref has a commit touching controls.py or D0-*.json;
parity copies match the manifest; $WT at bff61ec. Baseline tests 78 (76 in a time-limited full run, the two
end-to-end tests `2 passed … in 909.25s`). Mutants caught: C7 working-tree diff dropped, criteria-hash check
dropped, smallest ceiling, K3h > → ≥, B reading order swapped, breach "> 2" → "≥ 2", e1 artefact dropped from the
B ref check, member/reference stream indices swapped, calibrate ledger per run, II_SHARE, random arm draw, telling
K0 rule (3 parity mismatches), Fx ignoring stops, P1 ignoring stops, status ancestry check removed, lost-reply
push path, stderr print before push, 14 before 15. ctl_det twice under `python -I`: `cmp: identical`, sha256
a615b49b…, 59 lines. gh reads: protection 404 "Branch not protected"; 0 force-pushes, 0 deletions in 91 v2
activities. Not run: real calibrate or test, anything against GitHub beyond reads, the v8-checks scripts, eight
further mutants (expected uncaught).

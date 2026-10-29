# Red team s18 on criteria v7 (sha256 9c15e47e…), changes only (§0 touched, §5, §7)

Fresh reviewer, lane session 18, 2026-10-29 ~08:50-09:00Z. Scripts it ran are copied to `redteam-s18-scripts/`
(originally /tmp/fsdcheck-scripts). Saved by the coordinator from the agent's report.

**Verdict: not ready.** v7 does not fix s17f's blocking item: two attacks pass every v7 gate check and are
labelled preregistered, without a force-push (one through a merge into origin/v2, which already has 20 merges;
one by deleting a LEDGER row with an ordinary commit). Fixes are text-only but need another design red team.

## Blocking

1. **Test-data rule (L217) and freeze chain (L201-203) bypassable by merge topology; also mislabels honest work.**
   `F_j..F_{j+1}` sees only F_{j+1}'s ancestry, so a D on origin/v2 that F_{j+1} does not descend from is missed.
   L203's plain `git log` on HEAD uses history simplification and can hide an earlier freeze; L201 reads the
   chain from the current LEDGER file, where a row can be deleted.
   - S2: F0 pushed, D_A pushed and tested, F1 committed on a branch from F0 and merged into v2 (what a plain
     `git pull` does in a clone that is behind): `interval F0..F1 D0 commits: []`, GATE PASSES, PREREGISTERED.
   - S3: F1 branched from before F0, merged taking F1's files, F0's LEDGER row deleted: default
     `git log -- controls.py` shows only F1; `--full-history` shows both. GATE PASSES, PREREGISTERED.
   Proposed replacement for L201-203:
   > The freeze commits F0, F1, … are the non-merge commits on origin/v2 that ever touched controls.py, oldest
   > first: `git -C $R rev-list --full-history --no-merges --topo-order --reverse origin/v2 --
   > .cache/drafts/fsd-controls/controls.py`. Each F_j must be an ancestor of F_{j+1}. Each must be named by a
   > row ever added to LEDGER.csv on origin/v2 (the `+` lines of `git -C $R log -p --format= origin/v2 --
   > docs/v2/LEDGER.csv` whose `item` contains `fsd-controls freeze <full sha>`); otherwise refuse. Editing or
   > deleting a row later changes nothing. Rows naming any other sha are listed in the score files and
   > otherwise ignored. F must be the newest freeze. Neither F's change nor its tree may hold a run-output path.
   Replacement for L217 and L219:
   > Under the current freeze F_k, a control has test data from an earlier freeze iff some commit X in
   > `git -C $R rev-list --full-history origin/v2 -- .cache/drafts/fsd-controls/D0-<C>.json` does not descend
   > from F_k (`git merge-base --is-ancestor F_k X` fails). A D made under F_k descends from F_k, so never counts.
   Add to §5: the rule assumes origin/v2 is never force-pushed or deleted; the owner turns on GitHub protection
   for v2 (no force-push, no deletion) before F0, and F0's LEDGER row says so. Today: `gh api
   repos/ebinezer-rajaram/sciagent/branches/v2 --jq .protected` → `false`; rulesets → `[]`.
   L213: "every D0 commit … per interval" → "every commit on origin/v2 touching D0-<C>.json, each marked as
   descending from F_k or not". Checked on the same histories (`scenarios-fix.sh`): S1, S2, S3 exploratory;
   ctlB (no D) preregistered.

2. **D0's outcomes can be seen without pushing, then re-frozen with no label.** v7 makes D0's per-rung values
   evidence (L284, L172; statuses 12/13 results, L82, L281), but L218-220 count only a pushed D.
   Proposed L191: calibrate holds D0 in memory and writes no rung value until D is on origin/v2; it builds D with
   git plumbing and a temporary index (parent = fetched origin/v2 tip, only change D0-<C>.json), pushes it to
   refs/heads/v2, then writes D0 to disk; push failure after one refetch-and-retry → exit 10, nothing written.
   L220: every D0 anyone has seen is on origin/v2; a calibrate killed before the push leaves nothing.
   (Chosen over a launch-time marker, which would mislabel an honest crash-and-restart.)

3. **A failed `git fetch` is not a refusal (L200); a hand-moved origin/v2 is trusted.** `fetchfail.sh`:
   update-ref origin/v2 to D, bad URL, fetch exit 128, "D on origin/v2 after failed fetch: yes". Concurrent
   fetches of A1 and B in the same $R collide (`racefetch.sh`: 40 of 40 pairs nonzero, cannot lock ref).
   Proposed L200: every phase first runs, under exclusive flock on $D/git.lock, `git -C $R fetch
   https://github.com/ebinezer-rajaram/sciagent.git +refs/heads/v2:refs/remotes/origin/v2` with a 120 s timeout;
   nonzero or timeout → exit 10. Add git.lock to the run-output list (L189).

4. **An honest re-freeze makes the exploratory rerun unrunnable (L198 vs L222).** F′ on top of a pushed D holds
   D0-<C>.json in its tree, so D0 enters <frozen> and D′ is refused (S1: `REFUSE: F..HEAD touches frozen`).
   Fix: the tree clause in item 1; L189: a re-freeze first `git rm --cached` any committed D0-*.json.

## Non-blocking

1. Exit codes 1 and 2 still occur (uv bad project 2; `python -I` missing script 2; argparse bad choice 2;
   `sys.exit("boom")` 1; exception in finally after sys.exit(14) 1; signals 129/137/143). score exits 0 on 12/13
   but 14/15 on test-time VOIDs; precedence of 14 vs 15 unstated; B's ref-check VOID (L127) has no code;
   calibrate's exit on 12/13 unstated. Suggested: status field is authoritative, exit codes only signal; any
   other code = crashed; every git failure in the gate exits 10; score exits with the terminal status (15 if
   both 14 and 15); B ref-check failure is 12.
2. Breach VOID unpriced (L90-92): calibration's 512/512 bounds the per-run miss rate only to ≤ 5.8e-3; at 1e-3,
   P(VOID) = 0.387; P(VOID) ≤ 0.05 needs ≤ 3.7e-4 (≤ 2.4e-5 off the planned design). θ₊ = 0.4 rung (e1
   branching 1.05) has no truncation probe (probe3 covers 1.10, 1.15). v4's "score writes the VOID first and
   computes no results" was dropped; L77 ("VOID only if every rung is unbuildable") is now false.
3. STOP and resume: the ceiling is a CLI flag fixed at 7.6, so a ceiling raise cannot reach the process; a
   resume row containing `fsd-controls freeze <sha>` duplicates the chain and every phase refuses; step 5
   creates cpu-total.txt = 0.0 per F′ while §6 says never reset.
4. Stale version numbers: L186 "commits v5", L188 "against v5 … that is v6"; contract L48 says v7.
5. Smaller: L220 overclaims (internal functions callable without the gate with public stream ids; make them
   refuse the real control ids); score files never committed, so "anyone can recompute" has no input (add a
   commit-and-push step); every F must change controls.py (say so); require D single-parent; LEDGER column is
   `item`; the contract's stub-test list (P1 breaches, exit codes, bare origin) is not in §7 L259-265; contract
   lines 9-10 and 16-17 need items 1-3's changes.

## Not checked

controls.py did not exist; the scratch transcription omits the hash-in-message, `status --ignored`, $WT and env
checks (none affect topology); the single-parent point and item 2's attack were reasoned, not run; breach
numbers come from a model on the text's run counts; nothing tested against the real origin.

# Red team of fsd-controls/criteria.md v6 (sha256 3e30503f…) and engineer-contract.md (07ebe4e8…), session 17f, 2026-10-29
Verdict: READY WITH FIXES. One BLOCKING item remains (item 1): the freeze-chain test-data rule can be gamed, and it also mislabels honest restarts. The fix is two lines of text. With it, v6 is ready to freeze once controls.py exists and passes its code red team.

I checked by `diff criteria-v5.md criteria.md` and `grep -n "origin/v2|is-ancestor|fetch|breach|LEDGER"`; both hashes match. No scripts needed rerunning, since nothing numeric changed.

**(a) N1: resolved.** §8 restores every v4 §6 reading:
- "no candidate is ready … the thresholds are not changed";
- K0/K1/K2z against the exclusion rules;
- "FSD needs its own exclusion" and "K1 needs design-aware matching";
- "VOID and INCOMPLETE favour nothing", and "no rule is chosen on unpredicted patterns".

It also adds a status-5 reading that is consistent with Fix 4. One wording point, non-blocking: "the evidence is B3 and the per-rung h_F" (§8 B) contradicts §3, where B3 is gated. Fa's B1 failure is also close to construction (the atom row). The evidence there is the per-rung h_F.

**(b) N3 and N4 are resolved; N2 is not.**
- N3: a 99% clause failure now makes a rung unbuildable, and P3-B failures are read through h.
- N4: the run command is in §7, and the gate checks the thread variables, PYTHONDONTWRITEBYTECODE and a PYTHONPYCACHEPREFIX outside $R.
- N2 is not resolved; see item 1.

1. **BLOCKING. The test-data rule can be gamed, and it misfires on honest restarts.**
   - **The gate never checks that F or D is on origin/v2.** The only ancestry check is F → D (:192). "Each is pushed" (:185) is a statement, not a check. So the following sequence passes every check:
     1. Commit D locally and run test.
     2. Look at the data, then `git reset` the unpushed D.
     3. Freeze F₁, add its LEDGER row and recalibrate.
     4. Push D′.
     The rule (:201) looks only at origin/v2. It finds no D0 commit under F₀, so the F₁ result counts as preregistered.
   - **The rule misfires.** "A D0 commit that descends from an earlier freeze F_j" also matches D′ made under F₁, because F₁ itself descends from F₀. Read literally, every output after any re-freeze is labelled "exploratory deviation", including a clean restart that :202 calls preregistered.
   - **Fix:**
     - The test phase runs `git fetch origin`, then refuses unless `git merge-base --is-ancestor D origin/v2` and the same check for F both hold.
     - Define "has test data" as `git log --format=%H F_j..F_{j+1} -- .cache/drafts/fsd-controls/D0-<C>.json` being non-empty on origin/v2 for some earlier j.
     - Read the LEDGER rows from `git show origin/v2:docs/v2/LEDGER.csv`, not from the working tree.
   - **Residual, acceptable:** the "exploratory deviation" label is written by F′'s own controls.py, so it is only as automatic as that code. The F′ red team is the safeguard, and the score file should record the chain and every D, so anyone can recompute the label.

**(c) Small gaps: mostly closed.**
- The exit statuses 0/2/3/4/5, test exiting at once on 4/5, score writing from D0 alone, STOP removal by the coordinator only, the determinism rerun on the truncating design (mismatch makes it VOID), and the stub tests calling internal functions are all done.
- Still open (non-blocking):
  - The exit status for a determinism VOID found by score is not stated.
  - Python's own exit 2 (argparse usage error) collides with "2 gate", and a crash (exit 1) is not listed. Use distinct codes, e.g. 10 for a gate refusal.
  - v4's test-time P1-breach rule is gone, and I missed this in s17e: `grep -n breach` on v5 and v6 finds nothing. The lost rule: a breach is scored as observed and listed by seed, and the control is VOID if breaches touch more than 2 seeds. Restore it in §0, with an exit status.

**(d) engineer-contract.md matches v6.** It agrees on the run command, imports, the gate (env vars, freeze chain, D checks, environment), outcomes, rules, rung reading, exit statuses, records, ledger, STOP, parity, tests and sequence. It inherits the item-1 wording ("descends from an earlier freeze") and the missing P1-breach rule, so fix both files together.

**(e) New BLOCKING problems.** Only item 1. JOURNAL :1518 and :1525 are still uncorrected; that is the coordinator's job before launch, not part of the freeze.

**Not checked.** controls.py does not exist, so I tested neither the gate, the chain logic nor exit codes. The gaming sequence in item 1 is reasoned from the gate text, not run in a scratch repo.

Files:
- /home/ubuntu/lanes/sciagent/.cache/drafts/fsd-controls/criteria.md
- /home/ubuntu/lanes/sciagent/.cache/drafts/fsd-controls/criteria-v5.md
- /home/ubuntu/lanes/sciagent/.cache/drafts/fsd-controls/engineer-contract.md

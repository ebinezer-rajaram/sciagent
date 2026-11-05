Reviewed: `docs/v2/prereg/2026-11-04_pilot-search-misses.md` at 128f236, with `.cache/drafts/prereg-s27/` (scripts now also under `redteam-s27/` there; `/tmp/rt-s27/` paths below). Saved verbatim, session 27.

# Red team: `docs/v2/prereg/2026-11-04_pilot-search-misses.md` at 128f236

**Verdict: NOT READY. Do not freeze this text.** There are seven BLOCKING findings, and none needs a pilot unit to fix. Findings 2, 4, 5, 6 and 7 are text or a few lines. Findings 1 and 3 need a design decision from the lead: the value of K, and what the label may claim.

**Main problem:** the evidence that R1 works is an artefact of the draft script. Rerun blind, no case is caught at the first start, and one known miss is reached by about 1 start in 77. At K = 32, the six T2 controls alone would all pass with a point estimate of only 0.30.

**Constraints kept:**
- No search, refit or held-out evaluation ran on a pilot unit.
- `.cache/drafts/t2-drift/review-s27/` and `rows.pkl` stayed unopened.
- Scripts and outputs are in `/tmp/rt-s27/`. After the runs, `git status --short` shows 0 lines and 0 files in `.cache/pilot-merged` or `.cache/pilot-llm` are newer than my first script.
- Heavy jobs ran one at a time at `nice -n 15`.
- `census.py` loaded stored rows in memory but printed aggregates only. No score, `oracle_*` value or `certified` value was printed.
- `analysis.verdict` ran on stored readings only.

## BLOCKING

### 1. BLOCKING. R1's measured catch is contaminated by the generating point, and the design rests on it

`t2_ref.py` evaluates the generating point into the same profile that it later takes the maximum over:

```python
gp = p.slots.point(gen); p.evaluate([gp]); sg = p.score(gp)
...
b=max(p.solutions,key=lambda q:(p.score(q)))
if first is None and p.score(b)>=max(s0,sg)-1e-9 and p.score(b)>s0+3e-7: first=k
```

So `first_catch` is 1 by construction. This is truth leaking into the reference, through shared state and not through an argument.

Command: `/tmp/rt-s27/run_blind.sh`. This is the same case, log and seed stream, with the generating point scored on a separate profile. Then `basin.py <case> 96 100` counts ascents reaching the generating value on an independent stream.

| Case | G | Gap (nats) | Draft `first_catch` | Blind first catch | Starts reaching it | P(no catch in 32) |
|---|---:|---:|---:|---:|---:|---:|
| (0, softplus, 103648458) | 14,400 | 1.3249 | 1 | 2 | 9/30 | 0.000 |
| (2, exp, 1499614403) | 24,000 | 9.0007 | 1 | 2 | 47/96 | 0.000 |
| (0, softplus, 1074) | 14,400 | 0.00397 | 1 | 15 | 4/56 | 0.093 |
| (0, softplus, 3273957339) | 14,400 | 0.2719 | 1 | 17 | 4/40 | 0.034 |
| (0, softplus, 358866363) | 14,400 | 0.5943 | 1 | 8 | 1/77 | 0.658 |
| (0, softplus, 2006407976) | 14,400 | 2.0819 | 1 | 2 | 33/51 | 0.000 |

- **Point estimate:** P(all six pass at K = 32) = 0.299. One case dominates it, and that case's 95% range for P(no catch) is 0.097 to 0.990.
- **Basins are not wide.** An exhaustive run of case 1 (`r2.py`) printed `"evaluated": 14400, "n_better_than_fit5": 3`.
- **False statements:** line 58 ("the first random start already reached"), line 151 ("caught at the first of the 32 starts", "wide basins"), and the T2 half of line 149.
- **What survives:** on the draft's own stream R1 reached the generating value or better on all six within 32 starts, and the gains 9.5711 and 2.6685 are real. Exhaustive search confirms R1's best is the global maximum on case 1.
- **Stream not yet run:** line 52 is not the stream measured. `t2_ref.py` uses `[20261104, seed]`, and `truth` in `h` is undefined for a T2 case.
- **The proposed test would not catch this.** The "blind by signature" test (line 56) passes on `t2_ref.py` too, because that R1 loop takes no ψ argument.

Fixes:
1. Replace lines 58, 149 and 151 with the blind numbers.
2. Define `h` for T2 cases.
3. Add a test that the reference profile never holds a point supplied from outside the search.
4. Decide K against the measured rates. A 5% miss rate needs K of about 41 at p = 0.07 and about 229 at p = 0.013. Case 5's profile already held 7,380 points at 77 starts, so a large K will not fit the 8,000 cap on that shape.
5. If K stays 32, "All must pass" (line 147) on T2 should become a stated catch rate, decided now.
6. Fix K and `h` first, then run the frozen stream once. Do not try several streams.

### 2. BLOCKING. The pilot-scale control can pass without R1, and row 8+ is empty or unpassable

- **Vacuous pass.** Line 54 defines the chosen point over R0 to R2, and R0 for ORACLE is the kept fit (line 48). For dev-015 s3 the kept fit is the truth-ψ fit. So under line 145 the chosen L already equals `oracle_truth_psi_ll` and exceeds fit/5 by 4.53 nats, whatever R1 does. "Run blind with R1 only" does not clearly exclude R0.
- **Row 8+ (line 143).** `systems.py:318-320` keeps a higher truth-ψ fit whenever it is certified, and `PILOT.md:82` records `oracle_psi_source` as `search` on 59 and `truth` on 1. So any other unit in row 8+ has an uncertified truth-ψ point. R1 cannot select that point (line 54), so the row is either empty or passes only if R1 finds a different certified point at or above it, otherwise a false `CONTROL_FAIL`. "Unknown until the freeze" also overstates what is open. I did not read these fields, so I do not know which it is.
- **Consequence:** the pilot-scale positive control is one unit, on `exp` at G = 20,736.

Fix: compute pass on R1's own best with R0 excluded, keep ORACLE's R0 out of what sibling rows share, and restrict or delete row 8+.

### 3. BLOCKING. `GO_SURVIVES` cannot tell "nothing to find" from "could not find"

Label rules 4 and 5 (lines 194-195) read only `v.outcome`, and line 123 counts R1-only fits as checked. S1 cannot change a label (line 178). S1 also exists only where G ≤ 14,400, so at best it is an optimistic estimate for the larger grids.

Coverage, from `.venv/bin/python /tmp/rt-s27/census.py`:

```text
tier1 R2 (G<=14400) 65 R1-only 131 G>24000 118 G>1e5 86 G>1e6 58 G>1e8 24
tier1 R1-only by link {'softplus': 82, 'exp': 25, 'identity': 24}
ORACLE G max 33177600, ORACLE G>24000 24
('B-sparse OOD','R1-only',*) 25 of 30; ('ORACLE','R1-only',*) 27 of 60
```

- 118 of the 196 tier 1 fits have a grid larger than any control.
- 24 tier 1 R1-only fits are on the identity link, where no control exists (line 153).
- Most inputs to the too-easy gate and the ORACLE reference rely on R1 alone.
- Rate at G = 14,400 can be 1/77 per start. That is a measured reason to expect worse at 10⁶.
- Line 218's 0.55 treats cases 3 to 7 as independent, and four of the five are one stratum. It should be replaced by the measured rates.
- SPEC §6.4 and CLAUDE.md require a passing control for the instrument. Here it passes on only one slice of the fits.

Fixes, one of:
- (a) Add fixed-seed synthetic controls before any pilot unit: identity link and G above 10⁵ (synthetic logs are allowed), with every loss found entering the control list.
- (b) Split "checked" into exact and "searched, unvalidated", and let the survival statement cover only exact fits plus a stated R1-only remainder.

Either way, add a pre-set S1 floor, including a minimum denominator, below which the label says so.

### 4. BLOCKING. The freeze mechanics leave a tuning window, and the split is unpinned

```text
$ ls scripts/pilot_search_misses.py tests/campaign/test_pilot_search_misses.py
No such file or directory (both)
$ git ls-files data | wc -l
0
$ git check-ignore -v data/v2
.gitignore:44:/data/
$ git cat-file -e 900e66a:data/v2/truths/dev
fatal: path 'data/v2/truths/dev' exists on disk, but not in '900e66a'
```

- **Script timing.** Line 160 runs the script in a worktree at `F`, but the script does not exist. If it is written after `F`, the run is not at `F`, and nothing pins it. The guard covers `src` paths only, so R1 can change after the freeze with the hash intact.
- **`--prereg-sha` is a value the operator types** (line 228), so it enforces nothing unless the expected hash is in committed code.
- **`data` in the guard is vacuous**, because git does not track it. The `data/` part of line 159's "not at all" has no force.
- **A clean worktree has no split.** `scripts/pilot.py:66` resolves it under the worktree root, and `QUEUE.md:88` records this exact failure before.
- **No split hash in the prereg.** Addresses carry `truth_digest` (`plan.py:377`), so a wrong split shows up as missing cells, but neither §5 nor §9 stops on that.
- **Current split hash:** `find data/v2/truths/dev -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum` gives `31c18f10ca7c8a4ad6b599de5436bb15426bd02c67f3eb44fb0e93cb512ba577`.

Fixes:
1. Define `F` as the prereg commit, and `R` as a later commit where `git diff --name-only F R` is exactly the script and its test.
2. Commit the expected prereg hash in the script and record `R` in `decision.json`.
3. Pass `--split` absolutely, pin its hash, and stop unless 0 cells are missing.
4. Add `scripts/pilot.py`, `src/sciagent/core`, `src/sciagent/scenarios` and `pyproject.toml` to the guard.

### 5. BLOCKING. The tolerance can be infinite, which silently exempts a row from being a miss

Line 71 uses `max(3e-7, gap_stored + gap_reference)`. A solve can return a finite primal with an infinite gap (`fit_solve.py:685`, `:847`). `fit` then returns that fit flagged and uncertified (`fit.py:1074`), and `score` still reads the primal. So tol is `inf`, and every better point becomes a `near_tie`. That contradicts line 206 ("compared and substituted like any other").

This cannot happen on B-rand, B-sym, planted-hint or B-lib, which reject uncertified fits (`search.py:497`, `systems.py:219`). It can happen on ORACLE, B-sparse and LLM rows, which include the reference row and the system nearest the too-easy gate. The count is read after the freeze, so the rule has to cover it now.

Fix: for a stored fit that is uncertified or has a non-finite gap, drop `gap_stored`, compare the actual L values, and count the row in its own class.

### 6. BLOCKING. The 72 CPU hours bound holds per unit, not per run

```text
$ cat .../user@1000.service/cgroup.controllers
cpu memory pids
$ loginctl show-user ubuntu -p Linger
Linger=yes
$ systemctl --user show -p DefaultOOMPolicy -p DefaultTimeoutStopUSec
DefaultTimeoutStopUSec=1min 30s
DefaultOOMPolicy=stop
```

- **Sound part:** the cgroup bound for one unit, 2 cores × 36 hours.
- **Resume breaks it.** Lines 157 and 207 allow resume, and a resume is a new `systemd-run` with a fresh 36 hours. Line 224 does not say the 70-hour sum persists across units.
- **Out-of-memory stops everything.** With `MemoryMax=5G` and `OOMPolicy=stop`, killing one worker stops the whole unit, so line 207's retry always needs a new unit.
- **Lines 207 and 208 disagree.** Reaching the per-job limit is "retried once" in one and `reference_failed` in the other.
- **`RLIMIT_CPU` is cumulative per process.** A reused pool worker reaches 3 hours across several jobs, and its death breaks the executor for the sibling job (`search.py:519-521` notes `BrokenProcessPool`). I reasoned this from stdlib and kernel behaviour; I did not run it.
- **`RuntimeMaxSec` ends in SIGTERM, then SIGKILL after 90 s.** Nothing says who writes `decision.json` after that.

Fixes:
1. State the ceiling as a total over all units of this freeze.
2. Persist CPU used and refuse a resume above 70 hours, with the resume's `RuntimeMaxSec` reduced by time already spent.
3. Use one process per job.
4. Set `OOMPolicy=continue`, or say that the unit stops.
5. Resolve the contradiction between lines 207 and 208.
6. Add a finalise step that writes the label.

### 7. BLOCKING. The prereg's statements of prior exposure are not accurate

- **Line 5 ("No search, refit or held-out evaluation has been run on any pilot unit"), and line 142 for case 7 ("Never run before the freeze"):**
  - `LOG.md:1969` reports a measured in-sample to held-out ratio of 1.6 for dev-015 s3, from the red-team round.
  - `QUEUE.md:311` says "Only dev-015 s3 has a known size".
  - The stored ORACLE reading there is the truth-ψ fit, so that ratio implies a held-out evaluation of a refit search fit. This is an inference: I did not open those outputs, as instructed.
  - Line 5 should say what was run, by whom, and what was seen.
  - "No per-unit line was printed" is also not strictly true, since `count3.py` prints dev-015's structure and G. That one is harmless.
- **Line 141 ("written down at 08:18Z, before these ran"):**
  - `stat` gives `run3.sh` birth 08:16:33 and `run3.out` birth 08:16:38, so that batch started before 08:18Z. It covers three of the four "validation" cases.
  - No artefact shows the 08:18Z note. K = 32 does predate everything (`t2_ref.py` default, 08:05:11).
  - The 8,000 cap was never exercised: every T2 run used `1<<62`.
  - `edit2.py` (08:24:26) moved case 3 from "Used to design R1" to "Validation" after `run4.out` (last written 08:23:49) held its result.

Fix: state these facts as they are. Cases 3 to 6 are not an independent validation of anything until they are rerun under the corrected rule.

## Non-blocking

### 8. Non-blocking. Cost inputs are understated in places

- Line 242's "0.60 at most" is false, as is line 262's "4,607 of 8,000". Case (0, softplus, 2006407976) took 6,006 new points, 0.782 of a sweep per start (`blind.out`; `run3.out`: 6659 − 653). Line 242's "at most" is the claim; 0.6 is near the mean of the six cases.
- At 0.782, primary cost is 26.7 to 67.8 CPU hours against the stated 23.2 to 59.9. Measured throughput of 1.62 cores × 36 hours gives 58.3 CPU hours, so the upper end does not finish in the time limit.
- "2,000 events" is not an upper value. In the census, 21 of 60 ORACLE logs are above 2,000, and the maximum is 2,327.

### 9. Non-blocking. The memory gate and wall clock are optimistic

- `MemAvailable` is 7,146,048 kB (6.81 GiB).
- Unit `memory.current`: AG-c 1.06 GiB, AG-o 1.38 GiB, `sci-p4-tier` 1.34 GiB, `sci-p4-rescore` 0.20 GiB. Line 231 omits the two p4 units. These figures include page cache, so they overstate what is freed.
- AG-c ending alone gives about 7.87 GiB, below the 8 GiB gate, so "starts when at least one has ended" does not hold for the first expected to end.
- A second 2 GiB worker needs about all four units gone: 6.81 + 3.98 − 2 = 8.79 GiB.
- One worker at 0.81 of a core puts tier 1 at 23 to 57 hours against a 36-hour limit, which keeps running while the gate waits.
- Launch the unit only once the gate is open.

### 10. Non-blocking. S2 is a verdict input but comes last and has no effect on the label

The B-sparse link choice feeds the too-easy gate, yet S2 runs after tier 2 (line 233), whose rows `v` never reads. It is the part most likely to be cut (line 262). Run it before tier 2, and require the label line to state that `v2` differs when it does.

### 11. Non-blocking. Exhaustive uncertified rows sit outside the survival condition

Line 40 says such rows are not checked, but line 123 requires only tier 1, which is coordinate fits. `v` can therefore read unchecked rows, here ORACLE and B-sparse only (see finding 5). Name them in the survival sentence.

`uncertified_better` should also sit beside M on the identity link. There, 14 of 164 points were uncertified, and no control exists.

### 12. Non-blocking. `GO_CHANGES` carries more discretion than P4-PROPOSAL §2.4

P4-PROPOSAL §2.4 fixed consequences in advance, with "No rule is reinterpreted". Line 125 says the stored verdict stands and the owner decides. It also omits §2.4's specific C3 consequence, dev tuning of B-sym.

The owner's authority is fine, but say that under `GO_CHANGES` the computed GO is not put forward as GO, and that keeping it is a logged override.

### 13. Non-blocking. Δ is undefined from -inf to -inf

A stored -inf row (B-rand 1, B-sym 2, AG-o named 1, `PILOT.md` system table) that stays -inf gives `nan`, and line 171 counts that as falsifying H3. Define Δ = 0 in that case. A move from -inf to finite is unbounded, so report it apart.

### 14. Non-blocking. A stop can follow a look at partial results

`units.jsonl` is readable during the run, and line 273's "no reading is made" cannot be enforced. Record who stopped and why, and state that a STOP is always `INCOMPLETE`.

### 15. Non-blocking. "Restate the bound" is not delivered in those words

The 08:30 inbox entry says to restate the bound. The prereg gives a lower bound on M and the maximum |Δ| over found misses, and no bound on unfound ones. Say that plainly in §1.

## Checked and sound

- **Commit and guard:** HEAD is 128f236 with a clean tree. The line 160 guard gives `guard_exit=0`, and `core`, `library`, `nonparam`, `scripts/pilot.py` and `pyproject.toml` are unchanged since 900e66a.
- **Census:** 255, 196 and 59 fits; 115 and 140 by R2 or R1 only; 65 and 131 in tier 1; 73 identity fits; 26 of 30 B-sparse out-of-dictionary units on the coordinate path.
- **Recorded CPU:** B-rand 36.84 hours, 55.3 s per fit.
- **Config:** `full=512` is in all 430 baseline keys, and `sweeps=0`, so fit/5's ascents are not budget-truncated.
- **Verdict guard is feasible:** `verdict outcome go a==b True a==rederived True rows eq True`. The seed 20261002 and 10,000 resamples match `pilot_report.py:513,565`.
- **`exact` depends only on structure** (`structure.py:44`), so line 101 holds.
- **The text-reproduces-the-fit logic holds** (`search.py:558-568`, `execute.py:416`), and B-sparse's path is fixed-ψ (`sparse.py:505-533`).
- **T2 has no identity case** (`test_fit_search.py:355`).
- **Arithmetic:** the cost table at 0.6 is right, as are 0.55 and 1.2%.
- **Worries that did not hold up:**
  - R0's 55.3 s is conservative: ORACLE coordinate rows average 37.5 s, maximum 92.6 s.
  - The cap binds on only 2 fits at 0.78 (maximum sweep 376).
  - R2's rate is about the same as R1's: `"done": 14400, "cpu": 172.3`, 0.012 s per point at horizon 300, RSS 0.2 GiB on that small log.
- **T2 rerun:** `fit5_ll` and `gen_ll` matched the draft's values exactly on the two cases I compared in full.

## Not checked

- Whether R0 reproduces any stored cell. This is forbidden here, and it stays the plan's central unverified assumption until case 7.
- How many exhaustive rows have `certified` = 0, and any `oracle_truth_psi_*` value. Both left unread to keep the draft's blinding.
- R1's catch rate at pilot scale or on large grids, and whether a known identity-link miss can be constructed.
- R2 memory at about 2,000 events.
- S2's reproduction of `_choose_link`.
- The LLM curve counts, the 2.1 × 10¹¹ maximum and 21,600 median, and the "about 7 times" claim.
- Whether any -inf row is on a coordinate grid.
- The lane's clock claims beyond file times.
- The per-start rates carry wide intervals, and the frozen seed stream was not run, because `h` is undefined for T2.

## Files

- `/home/ubuntu/lanes/sciagent/docs/v2/prereg/2026-11-04_pilot-search-misses.md`
- `/home/ubuntu/lanes/sciagent/.cache/drafts/prereg-s27/t2_ref.py`
- `/tmp/rt-s27/blind.py`, `/tmp/rt-s27/blind.out`
- `/tmp/rt-s27/basin.py`, `/tmp/rt-s27/basin.out`
- `/tmp/rt-s27/r2.py`, `/tmp/rt-s27/r2.out`
- `/tmp/rt-s27/census.py`

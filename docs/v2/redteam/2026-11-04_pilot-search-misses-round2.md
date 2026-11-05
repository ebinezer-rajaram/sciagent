Reviewed: `docs/v2/prereg/2026-11-04_pilot-search-misses.md` at b6e98db (round 2), against round 1's report and the sources under `.cache/drafts/prereg-s27/` and `.cache/drafts/prereg-s28/`. Saved verbatim, session 28.

# Round 2 red team: `docs/v2/prereg/2026-11-04_pilot-search-misses.md` at b6e98db

## Verdict: NOT READY. Do not freeze this text.

- **Round 1:** 12 of its 15 findings are fully fixed, and the other three carry leftovers. Findings 4 and 8 are partly fixed. Finding 14 was fixed as asked, but that fix opened a new hole (finding 1 below).
- **This round:** there are five BLOCKING findings, and none needs a pilot unit to fix. Findings 1, 3, 4 and 5 are text.
- **The one that needs work:** finding 2 needs a synthetic measurement and a fresh decision on K, because the cost figure behind K = 48 doesn't apply to the fits it is used on.
- **After the fixes:** a check limited to §2.2, §5 and §8 should be enough; a full third round shouldn't be needed.

**Your six questions**

| Question | Answer |
|---|---|
| Can the rules be gamed? | Yes. A `STOP` or a CPU stop can turn a flipped verdict into `INCOMPLETE` (finding 1). |
| Are exclusions outcome-neutral? | Yes. The classes are structural, stage order is by hash, and failed fits keep their stored reading. One blemish: a job killed in R2 loses any miss R1 found (finding 7). |
| Is the sample size justified? | The census is, and every count reproduces. K is not (finding 2). |
| Does the primary analysis answer the question? | Yes, within its stated limits, except that three of the five labels have no stated consequence (finding 3). |
| Is the cost ceiling a true upper bound? | Yes, to within about 2 CPU minutes, if `cpu.json` is written every minute (finding 13). The claim that tier 1 fits inside it is what fails (finding 2). |
| Can anything be tuned after the fact? | Yes. A later commit R' passes every guard, and the amendment clause has no gate (finding 4). |

## BLOCKING

### 1. BLOCKING. The label rules contradict each other on the plan's own expected path, and `INCOMPLETE` masks `GO_CHANGES`

- **Contradiction.**
  - Rule 2 (line 230) gives `INCOMPLETE` whenever "the run ended at the CPU stop, the wall limit or a `STOP`".
  - Line 236 says "A tier 2 or S2 shortfall does not alter the label".
  - Line 309 expects exactly that case: "S2 and tier 2 can be cut short".
  - So when tier 1 finishes and the 78-hour stop then cuts S2, the text gives two different labels, and the choice would be made after the numbers are known.
- **Masking.**
  - Rule 2 comes before rule 3. A partial `v` that has already flipped is labelled `INCOMPLETE`, although line 139 says `GO_CHANGES` "stands".
  - Line 322 says a STOP "cannot be used to keep a favourable partial result". It can still be used to bury an unfavourable one, because "A `STOP` is always `INCOMPLETE`" outranks the flip.
  - Line 277 says the label is known at the end of tier 1, which opens a window of tens of hours for this. Round 1's finding 14 fix created this.
- **Also.** Nothing says whether a resume after a STOP is allowed. Rule 1 lets a byte failure on an LLM row in tier 2 void a tier 1 label already called "known".

Evidence: `grep -n "INCOMPLETE\|shortfall\|cut short" docs/v2/prereg/...` returned lines 230, 236, 309, 312 and 322, as quoted.

Fix:
1. Order the labels `BYTE_FAIL`, then `GO_CHANGES` (with a `partial` flag), then `INCOMPLETE`.
2. Limit `INCOMPLETE` to a tier 1 shortfall.
3. State that any stop after tier 1 is a shortfall only.
4. Forbid a resume after a STOP.
5. Call the end-of-tier-1 label provisional.

### 2. BLOCKING. The R1 cost basis is wrong for the fits it prices, so K = 48's stated reason fails

Below, `m` means new points per start as a fraction of one sweep. Lines 58 and 289 take m = 0.782 and call it "at most". That figure comes from six T2 grids that fill up as starts accumulate, while the cost applies it only to fits with G > 14,400.

**Code.** `_ascend` evaluates every block at least once per start, so a start costs about one full sweep unless it overlaps earlier starts. Only overlap can produce 0.782.

**Stored T2 outputs** (`python3 /tmp/rt-s28/decay.py`). These also exclude fit/5's preloaded points, and R1's profile starts empty.

```text
[0, 'softplus', 2006407976] [(4, 1.55), (8, 1.12), (16, 1.0), (32, 0.78)]
[0, 'softplus', 103648458]  [(4, 1.2), (8, 1.01), (16, 0.82), (32, 0.6)]
```

**The spike's own run on `LARGE`** (`d.table`, sweep 272) is the only fresh-profile measurement on a large grid.

```text
{'starts_used': 9, 'solved': 4043}      -> 1.65 to 1.86 sweeps per start
```

**Census** (`/tmp/rt-s28/census.py`, aggregates only):

```text
tier1 R1only G>24000 118 G>1e5 86 G>3456000 51 G>1e8 24
```

**Cost with the 16,000 cap applied** (`python3 /tmp/rt-s28/cost2.py`):

| m | Fits capped | Tier 1 (identity 0.04 to 0.512 s) | Plus controls and group C, at 0.512 s |
|---:|---:|---:|---:|
| 0.782 | 0 | 32.9 to 66.5 | 73.0 |
| 1.0 | 2 | 39.0 to 76.5 | **83.0** |
| 1.25 | 10 | 45.6 to 87.2 | 93.7 |
| 1.65 | 31 | 53.9 to 101.7 | 108.2 |

Consequences:
- **Tier 1 claim.** Line 309's "73 hours at most, under 78" holds only for m ≤ 0.891 (`decay.py`, uncapped). Above that, tier 1 hits the stop and rule 2 gives `INCOMPLETE`.
- **The 0.123 s margin doesn't rescue it.** At 0.06 s and m = 1.65, the top end is still 84.2 hours.
- **The cap binds.** Line 58 says it doesn't, but at m = 1.65 it stops 31 fits before 48 starts, and those are the largest-sweep fits.
- **K's rationale.** Line 58 calls K = 48 "the most the CPU ceiling carries". By that criterion K is about 26 at m = 1.65 (uncapped arithmetic). That drops group A's pass chance from 0.981 to 0.854.
- **0.123 s is not a bound either.** It comes from a 4-column structure, while 106 of 131 tier 1 R1-only fits have more columns and 72 of 131 have 4 features. Against that, recorded ORACLE units peak at 92.6 s, so its true margin is unknown.

Caveat: `LARGE` is one log. `BIG[2]` at G = 24,000 gave only 0.44, so mid-size grids may sit lower. My own measurement across shapes did not run (see "Not checked").

Fix:
1. Measure m on a fresh profile at three or more grid sizes above 14,400, on a non-reserved root.
2. Recompute with `min(m·K·sweep, cap)`.
3. Then either lower K, or cap identity separately, or say the top of the range ends `INCOMPLETE`.

### 3. BLOCKING. Three of the five labels have no stated consequence for the GO

`grep -n "put forward"` hits only line 139, under `GO_CHANGES`. `CONTROL_FAIL` (line 232) and `INCOMPLETE` (line 230) say what isn't stated, and `BYTE_FAIL` says only that the run stops. None says whether the go/no-go may then go to the owner.

The 08:30 instruction asks to "say whether the computed GO survives", and P4-PROPOSAL §2.4 fixes consequences in advance. These labels are not remote (finding 9).

Fix: one table, label to consequence. For example: under those three labels the question stays open, the GO is not put forward as final, and the only route is a frozen amendment.

### 4. BLOCKING. R is not pinned, and the amendment clause is open (round 1 finding 4 only partly fixed)

- **The guard.** Line 192 tests `git diff --name-only F HEAD`, not `F R`, as round 1 asked. After a run at R, an edited script at R' passes every guard.
- **What is not pinned.**
  - `PREREG_SHA256` pins this file only.
  - Nothing holds the script's own hash.
  - `F` isn't stated to be a committed constant.
  - Nothing refuses a second fresh start, or a resume under another commit.
- **The amendment clause.** Line 196 lets any "bug" fix be an amendment after the numbers are seen. It names no approver and no rerun scope, and doesn't say the first records stay in the report.

Fix:
1. Record the sha256 of the script and test in a LOG entry at R, before launch.
2. Make `F` a constant in the script.
3. Write the script's hash in `cpu.json` at first start, and refuse any start or resume that differs.
4. Require control-room approval, a new output directory, and both record sets reported.

### 5. BLOCKING. "T2 controls 1 and 2 are slow-tier tests" contradicts the run-once stream rule

- Line 187 makes T2 cases slow-tier tests.
- Line 60 says the stream "runs once, at the launch; a second run on T2 under another stream is not allowed".
- The sentence is carried over from 128f236 (`git show 128f236:... | grep -n "slow-tier"` hits line 157), and group numbering has since replaced "controls 1 and 2".
- A test on the frozen stream burns it before launch, and every slow tier would rerun it. A test on another root breaks line 60. QUEUE line 312 repeats the rule.
- The script is being written now, so this needs settling before someone implements it.

Fix: slow-tier tests use a test-only root and are never read as catches, or drop them.

## Non-blocking

**6. "Substitution can never move a recovery share" (line 111) is false.** `analysis.py:601` builds `kept` from the ORACLE-vs-B-lib check, and lines 685-689 compute recovery over kept truths. So a substituted ORACLE reading can change `kept` and therefore the share. "B-lib's recovery cannot move by any route" fails the same way. The verdict code is right; only the claim is wrong.

**7. One failed job makes the whole run `INCOMPLETE`, and the identity margin is thin.**
- A job reaches 3 CPU hours above 0.67 s per point at the 16,000 cap (0.746 s at 14,400 points) (`arith.py`).
- The 0.512 s rate is a linear extrapolation of a mean over random points on one 3-feature log, whose maximum was 5.05 s (`t5.out`).
- `t3.out`, the one attempt on an ascent path, holds only a solver warning and no result line.
- Recorded B-rand units:
  ```text
  q50 1867 ... top5 [4726, 5223, 6287, 6791, 12971]
  ```
- §6 also throws away a real R1 miss if the job dies in R2.
- Fix: record per stage, and keep R1's result as `searched`.

**8. Group A's pass rate has only one input.** See finding 9 for the combined figure. All of group A's arithmetic reproduces.

**9. H1's real pass chance is not 0.98.** That figure covers group A only.
```text
H1 overall if B ~ draw from the six catch probs 0.888; if like 4/56 0.953; if like 1/77 0.457
```
- Group B is one unit, and it is unrepresentative: `dev-015 s3 G 20736 sweep 60 ... columns 3`, against R1-only sweep quartiles of 90, 144 and 188.
- Group D contradicts line 149's "R1 alone": read literally, it repeats group A's first case on the same stream and never tests R2.
- A free end-to-end control is unused: if R1 lands on truth ψ, the refit's held-out value must byte-match the stored reading.

**10. S2.**
- 1,400 points is understated. The census gives `sweep mean 163.3 K16 pts at .782 2040`, so S2 is 10.6 to 24.3 hours, not 8.0 to 17.4, and 19.7 to 48.2 at m = 1.65.
- `_choose_link` gives a link -inf if the fit-level flag is false, and that flag covers every visited point. A single-point `fit(psi=...)` passes more easily, so identity can win for a reason that has nothing to do with fit quality.
- S2 has no seed `c`.
- A `sparse.link` mismatch has no stated consequence.

**11. Group C stops at 3,456,000 points, and 51 of 131 tier 1 R1-only fits are above that.**
- "Grids above 10⁵" (line 179) overstates what it covers.
- "Each stratum of the searched fits" (line 137) has no mapping from a fit to a stratum.
- `i` and `stratum` in `c` are undefined.
- Handling of `SimulationError` is undefined.
- Whether R1 runs inline or after the hunt is unstated.

**12. Determinism.** Group C's 6-hour stop, `cpu_limit` and the 78-hour stop all depend on load, so line 199's "byte-identical on a rerun" can't hold when they fire. A point budget for group C would be deterministic.

**13. The ceiling.** 84 holds to within about 2 CPU minutes, because a dispatch can follow a crossing by up to one poll. That assumes `cpu.json` is written and fsynced every minute, which isn't stated. Otherwise a SIGKILL loses a unit's total and a resume restarts low. S2 and hunt work should be declared "jobs" so the RLIMIT covers them.

**14. Line 7 says dev-015 s3 is "never a finding", but line 87 makes it a reading miss if R1 beats truth ψ.** That happened on 2 of 6 T2 cases. Section 2.3 should govern. The census shows no sibling row (`rows sharing ORACLE text: ['ORACLE']`), so H2 isn't falsified in advance.

**15. The tolerance ignores `quadrature_error`.** SPEC §2.2 says L is exact only up to it, and it differs per ψ point, so a difference above `tol` may not be "real" (line 83). M ≥ 1 could rest on that. Size not measured.

**16. Survival wording (line 135).**
- `F` clashes with line 3's definition.
- E + U + X can't sum to the fits the verdict reads, because the 22 B-lib out-of-grammar fits are in no class.
- "E are exact" drops the uncertified-points caveat.
- Line 137 says "most" for 27 of 60.

**17. Small errors.**
- Line 189: `scripts/` also differs in five `scripts/p3_report/` files (`git diff --name-only 900e66a HEAD`).
- Line 257: the top rate is 33/51, not 47/96.
- Line 299 puts group B at horizon 300.
- Line 149's table order runs B before C; line 164 says C runs first.
- `--store` is missing from the launch line, and the store has no pinned manifest.
- The launch line sets no `-E OPENBLAS_NUM_THREADS=1`; I believe the guard would refuse it, but did not test this.
- The exposure list should add the stress test's gate margins and this round's census.

## Round 1, finding by finding

| # | Status | Note |
|---:|---|---|
| 1 | Fixed | Blind rates, `h` for T2, profile test, K decided. Leftover: finding 2 |
| 2 | Fixed | Pass reads R1 alone, row 8+ deleted |
| 3 | Fixed | Two evidence classes, group C, S1 floor |
| 4 | **Partly** | Split hash and guard paths done. R is not pinned (finding 4) |
| 5 | Fixed | Infinite tolerance handled, with its own class |
| 6 | Fixed | One gap (finding 13) |
| 7 | Fixed | Small omission (finding 17) |
| 8 | **Partly** | Numbers corrected, but 0.782 is applied to the wrong fits (finding 2) |
| 9 | Fixed | Gate rewritten, launch only when open |
| 10 | Fixed | Order and label line. Cost understated (finding 10) |
| 11 | Fixed | X count added. Denominator gap (finding 16) |
| 12 | Fixed for `GO_CHANGES` | Other labels: finding 3 |
| 13 | Fixed | The rule is live: 2 baseline `-inf` rows are on coordinate grids |
| 14 | Fixed as asked | The fix opened finding 1 |
| 15 | Fixed | |

## Checked and sound

- **Commit and hash:** HEAD is b6e98db. `guard_exit=0`, and all guard paths exist. The split hash reproduces `31c18f10…`. The store fingerprint is `commit=900e66a…,tree=clean`.
- **Census, all matching the prereg:**
  - 670 / 430 / 240 rows; 267 / 318 / 60 / 22 / 3 by class; every per-system count
  - 255 / 196 / 59 fits; 115 fits at 435,774 points; 168 at 2,677,206
  - median 21,600 and maximum 2.1 × 10¹¹; all sweep and grid sums; maximum sweep 376
  - 5,164 / 2,117 / 922 LLM fits; library G of 1, 6, 5; `missing {}`, `void {}`
- **Arithmetic:** the catch probabilities, 0.98, 0.45, 229, 1.2%, B-rand's 36.84 hours and 55.3 s, 2,133 CPU seconds, and "14 more of 30" all reproduce. The tier 1, S2, tier 2 and worst-case lines also reproduce, given the inputs that findings 2 and 10 dispute.
- **Code references:** all line references checked are right. `full=512` reaches LLM score keys through `_key`.
- **No cherry-picking:** group A is all six known losses (sweeps 1 + 3, pin 1, and (0, softplus, 1074) from QUEUE).
- **Reserved streams:** root 20261106 appears only in the text of `edit3.py`. Earlier uses of 20261104 were different sequences.

## Not checked

- **Per-start cost across grid shapes:** `ascent.py` never ran. `free -g` stayed at 4 after the census, so m on grids above 10⁵ rests on one spike log plus the code argument. No synthetic experiment ran on any root.
- **R0 against stored cells:** this stays unverified until group B runs. Whether group B is caught, and the counts of exhaustive-uncertified and `stored_uncertified` rows, are also unread.
- **Rates and sizes:** the identity rate on an ascent path or at 4+ features, and the size of `quadrature_error`.
- **Runtime behaviour:** R1 determinism on a rerun (I reran no unit; the spike's six `repeat` lines show fit/5 bytes equal), the `systemd-run` line, and S2's reproduction of `_choose_link`.
- **Out of scope:** the script and its test.

## Limits: what happened, including two departures

- No search, refit or held-out evaluation ran on any pilot unit. `analysis.verdict` was not run. `rows.pkl` was not opened. `load_rows` loaded rows in memory, as in round 1, and printed aggregates only (the non-finite figure is a count).
- **Departure 1:** two `grep -rln` calls over `.cache/drafts` for the seed roots also scanned `.py`, `.sh` and `.out` files inside `t2-drift/review-s27/`, and I filtered those paths from the output. I saw nothing from them, but the files were opened.
- **Departure 2:** greps of committed `LOG.md` and `PILOT.md` showed text containing stored values: LOG line 1940 (dev-015 s3's ORACLE values), one "gap closed" figure, and stress-test shares. These came from committed documents, not the store, and I don't repeat them here.
- The census ran once, at 10:37 when `free -g` showed 5, at `nice -n 15` with BLAS threads 1. Its peak memory was not recorded.
- The run took about 38 minutes. My first `pkill` matched its own shell, so I then stopped my gate by PID. No job of mine remains, and no other unit was touched.
- `git status --short` shows 0 lines, 0 store files are newer, and no new `.pyc` files are in the repo.

## Files

- `/home/ubuntu/lanes/sciagent/docs/v2/prereg/2026-11-04_pilot-search-misses.md`
- `/home/ubuntu/lanes/sciagent/docs/v2/redteam/2026-11-04_pilot-search-misses.md`
- `/home/ubuntu/lanes/sciagent/.cache/drafts/prereg-s28/spike/d.table`
- `/home/ubuntu/lanes/sciagent/src/sciagent/campaign/analysis.py`
- `/home/ubuntu/lanes/sciagent/src/sciagent/glm/fit.py`
- `/tmp/rt-s28/census.py`, `/tmp/rt-s28/census.out`
- `/tmp/rt-s28/arith.py`, `/tmp/rt-s28/decay.py`, `/tmp/rt-s28/cost2.py`
- `/tmp/rt-s28/ascent.py` (written, never run)
- `/tmp/rt-s28/gate.sh`, `/tmp/rt-s28/gate2.sh`, `/tmp/rt-s28/gate.log`

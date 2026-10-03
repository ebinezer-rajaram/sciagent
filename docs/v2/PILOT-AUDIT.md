# P3 pilot audit (2026-10-03)

Audit of three anomalies in `docs/v2/PILOT.md` (verdict GO) and of the verdict
logic. Nothing under `src/`, `scripts/`, `tests/`, `docs/SPEC.md` or
`.cache/pilot` was changed. Scratch scripts (not in the repo) read the pilot
store through `sciagent.campaign.analysis.load_rows` with the report's plan
(seeds 1,2,3; B-sym@10F on seed 1, dev-000..009), and refit with the
campaign's `FitConfig`.

**Bottom line.** Anomaly 1 is an **instrument defect**: ORACLE is not an upper
bound on 22 of 60 units, because the fitter's coordinate ψ search stalls. GO
survives a corrected ORACLE, but every gap, gap-closed share and the 10xF
control in PILOT.md move. Treat the GO as provisional until the fitter is
fixed and the scores are rerun. Anomaly 2 is genuine (the pass is robust).
Anomaly 3 is a **reporting/policy bug**, not a sandbox breach.

## 1. Systems beating ORACLE: instrument defect (ψ search), not noise

**Finding.** 36 non-ORACLE units have gap > +0.01. All of them are on dev-008,
dev-013, dev-015, dev-017 and dev-019. The worst are B-sparse dev-008
s1 (+0.138), B-sym@10F dev-008 s1 (+0.135) and AG-o-S/named dev-008 s3
(+0.105). For each (truth, seed) I refitted the true structure, scored the
truth's own ψ point (every truth ψ is on the grid) inside the same `_Profile`,
and evaluated the true parameters:

| dev-008 | ORACLE train LL/ev | true params train | ORACLE held-out | truth-ψ refit held-out | true params held-out |
|---|---|---|---|---|---|
| s1 | -0.632 | -0.532 | -0.8818 | -0.7407 | -0.7400 |
| s2 | -0.773 | -0.649 | -0.7797 | -0.6688 | -0.6683 |
| s3 | -0.770 | -0.628 | -0.8530 | -0.7010 | -0.7008 |

- **Optimizer, not solver.** The θ solve is clean: Newton, `optimal`,
  `duality_gap_rel` of 1e-13 to 1e-15, no warnings (the fit_solve.py:591
  Clarabel path is not used). The ψ search failed. dev-008 has 115,200 ψ
  points, more than the 512 cap, so `fit._search` runs per-slot coordinate
  line search. On s1 it settled on period 5 / phase π/2 for the second
  `PhaseWindow` instead of 10 / 0. Period and phase are coupled, so no
  single-slot move improves the fit. That is a local optimum.
- **Not held-out mismatch.** Recomputed ORACLE held-out per-event LLs equal
  the stored ones exactly (max |Δ| = 0.0 over 60 units).
- **Not different data.** `execute.run_score_job` fits LLM submissions on the
  observational log only, as ORACLE is fitted.
- **Not finite-sample.** The deficit shows up on the *training* data, where an
  MLE of the true structure cannot lose to the truth's own ψ point.
- **Prevalence.** 29 of the 42 coordinate-search ORACLE fits score below the
  truth-ψ point on training data, and 22 of 60 by more than 0.01 nats/event
  (max 0.167). None of the 18 exhaustive (≤ 512-point) fits miss. 28 of 60
  ORACLE fits are below even the *true parameters* on training data.
- **"gap closed 37.32"** compounds this: on dev-008 s3 the denominator
  LL(ORACLE) - LL(B-lib) is 0.0029.

**Re-read with a corrected ORACLE** (the better of the chosen point and the
truth-ψ point, by training LL). Units with gap > +0.01 drop from 36 to **0**.
dev-008's control-1 margin goes from 0.028±0.015 to 0.163±0.004. All 20 truths
are still kept. The B-sym@10F median gap goes from 0.0000 to **-0.0031**
(still a pass at -0.01). Close shares drop: B-sparse from 55% to 37% (OOD 47%
to 17%), planted-hint 53% to 33%, B-sym 33% to 28%, B-rand 22% to 10%,
B-sym@10F 100% to 90%, AG-o named 38% to 28%, AG-o anon 42% to 33%, AG-c
named 38% to 28%. **GO still holds** (controls pass; too-easy and too-hard do
not fire). This only re-references ORACLE, though. Every system fits through
the same `fit`, so a fixed search changes their models too, and the real
post-fix numbers are unknown until a rerun.

**Proposed fix (LOG decision: it changes fits).** (a) Block-coordinate ψ
search: optimise all slots of one feature jointly (per-feature grids are
small, e.g. 6·5·4 = 120), from several starting points (top-k of the
sub-grid). (b) ORACLE also evaluates the truth's ψ point and records
`oracle_underfit` when its training LL falls below the true-parameter
training LL. This is legitimate because ORACLE is framework-side. Then rerun
the non-LLM units and the LLM *scoring* jobs. **Test first:** with a
two-`Gate(…, PhaseWindow)` truth like dev-008 (period 10, phase 0),
`fit(truth.structure, sim)` must reach a log L at least as high as the θ-refit
at the truth's ψ point. That fails today by about 0.1 nats/event. Add a
Hypothesis property: for structures above 512 points, the fit's log L is at
least the log L at k random grid points. Add a report test: gap closed is
flagged when |LL_oracle - LL_lib| is within its noise.

## 2. Control 2 (planted-hint vs B-rand): genuine, robust pass; the mean is uninformative

Per-truth planted minus B-rand differences are positive on 16 of 20 truths,
median +0.0166. One truth carries the mean: dev-000 at +135.04. Its B-rand
seed-1 model, `link=exp` with a triple `Product`, scores -405.7 nats/event on
held-out data because the exp link extrapolates explosively. That and the two
`-inf` units are genuine model outcomes. The `-inf` units (dev-001 s1, dev-006
s3) are identity-link fits with λ ≤ 0 at a held-out event.
`analysis.score_rows` drops `-inf` from the control rows. That *favours*
B-rand: on dev-006 its finite seeds average -0.0026 against planted's -0.0383.

| variant | mean | 95% CI | pass |
|---|---|---|---|
| as implemented (finite only) | 6.767 | [0.0087, 20.27] | yes |
| dev-000 removed | 0.0163 | [0.0054, 0.0273] | yes |
| truths with any B-rand unit < -1 or -inf removed (n=17) | 0.0196 | [0.0094, 0.0303] | yes |
| per-truth median over seeds (-inf-safe) | 0.0193 | [0.0081, 0.0304] | yes |
| unit gaps clipped at -0.2 | 0.0273 | [0.0170, 0.0387] | yes |
| bootstrap of the median diff | 0.0166 | [0.0106, 0.0258] | yes |
| literal: -inf kept as -inf | +inf | [0.0218, nan] | yes |

The Wilcoxon signed-rank test (one-sided) gives p = 0.0028. SPEC §6.4 says
only "by a clear margin" and does not say how -inf is handled. `gonogo`
preregisters "CI lower end > 0". Under the literal reading (keep -inf) the
lower end is still 0.022, but `paired_bootstrap_ci` raises on non-finite
input, so the literal rule cannot be computed in code. The pass does not
depend on the outliers: the lower bound comes from resamples without dev-000.
The printed mean (6.77) and upper bound (20.27) say nothing. ORACLE cancels
in this difference, so anomaly 1 does not affect it. **Fix:** report the
median, the CI of the median and the win count beside the mean, and decide
in LOG how -inf enters (rank-based, or a per-event floor). **Test:** a table
with one -inf and one -400 B-rand unit gives the same verdict and a finite
printed CI.

## 3. "foreign tool: python": reporting/policy bug, no sandbox breach

In all 9 void runs, the session manifest lists only `mcp__lab__*` tools. The
model emitted exactly one `tool_use` named bare `python` (hallucinated, not
the allowed `mcp__lab__python`). Claude Code denied it ("Error: No such tool
available: python"), nothing ran, and the agent then used `mcp__lab__python`
1 to 5 times. `harness/live.py` `on_assistant` flags any name outside
`self.names`, and `investigation/runner.py:369` turns that into a void.
SPEC §9 voids runs for *out-of-bounds access*. A denied call to a tool that
does not exist is not access.

**Voids are not excluded.** `driver._queue_score` skips only status ≠ 1, and
void sessions have status 1, so they are scored. Coverage "scored 60" and
every AG-o table include them (4 named, 5 anon). Excluding them moves AG-o
named mean/median gap from -0.0262/-0.0133 to -0.0253/-0.0124, and anon from
-0.0225/-0.0114 to -0.0195/-0.0084. Close shares do not change. There is no
effect on GO, because the verdict reads non-LLM systems only. All 9 voids are
in seeds 2 and 3 (0 of 40 in seed 1, on the same Claude Code 2.1.233). This
is unexplained; p ≈ 0.03 if voids were spread evenly. **Fix:** record
`unknown_tool_attempts` for a denied unknown name, and void only when a
foreign tool *executed* (a non-error result) or on server tool use. Make the
analysis drop or label voids consistently. **Tests:** a scripted driver with
a bare-name `tool_use` plus an error result is not void and has the count set;
one with a non-error foreign result is void; `summarise` excludes a void
session's row and counts it.

## 4. Verdict logic against SPEC §7.1 / §6.4

- **Too easy:** matches. B-lib and B-rand use exact recovery; B-sparse uses
  OOD close (≥ 0.9, preregistered); shares are truth-weighted.
- **Too hard:** the SPEC says "every system, including B-sym at 10xF". The
  code uses every non-LLM system except ORACLE, each with share < 10%. It
  excludes the LLM arms (declared) and includes planted-hint, a control that
  is given the truth's top-level production. The SPEC sentence could also mean
  "under 10% of truths are closed by *any* system", which is stricter. Neither
  difference matters here.
- **Controls:** the code implements them as preregistered in `gonogo`.
  C1 "beats on every truth" is read as margin > 1 paired SE (weak at
  n = 3). C3 runs on 10 truths × 1 seed (declared). **But** C1, C3 and every
  "closes" share assume ORACLE is an upper bound, and §1 shows it is not. The
  logic is right; one of its inputs is wrong.

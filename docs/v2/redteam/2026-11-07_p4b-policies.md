# Red team: P4b pre-registration, first text (session 38)

Reviewed: `docs/v2/prereg/2026-11-07_p4b-policies.md` (draft, as committed in 2b05867; the reviewer read the uncommitted copy of the same text) against v2 at 7314241, with `.cache/drafts/s38/c5_power.py` and `.cache/drafts/s38/p3-paired-sd.txt`. Red-team agent, lane session 38, 2026-11-07. Saved verbatim below.

**Verdict: not ready.** Nine of the 15 findings below are blocking. The worst one is that the draft describes EIG code that doesn't exist: the EIG tie rule adopted on 2026-10-28 was never implemented. Several findings change what P4b could claim even if control 5 passes.

I checked against 7314241 with the uncommitted draft (`/home/ubuntu/lanes/sciagent/docs/v2/prereg/2026-11-07_p4b-policies.md`). Everything was read-only, at $0, with no control stream drawn. Scratch files are in `/tmp/rt-p4b/`.

## Findings, ranked

**1. BLOCKING: the EIG tie rule in §2.2 is not in the code.**
- The draft says "BIC weights with the truth-keyed tie draw for EIG". At HEAD, `EIGPolicy.choose` sends every tie to menu order (`policies.py:688-703`, `:1005-1017`). Its own docstring says "every cell ties and the pick is the menu-first cell" (`:83-86`).
- `git log -- src/sciagent/systems/v2/policies.py` shows the file last changed at 1d5834f on 2026-10-09, before the decision was adopted on 2026-10-28 (LOG:1721-1755, 2039-2043). I grepped every branch and found no tie draw, no "relative precision" change and no logged KL pick.
- On dev the BIC posterior is degenerate on all 20 truths when the truth is in the entertained set, so an EIG arm would run (`burst`, `mean_rate`) on every degenerate decision.
- That decision also required C5 to be "split, before any run, into degenerate and non-degenerate" EIG decisions (item 4). The draft never does this; §4 splits only on FSD/truthFSD degeneracy.
- **Changes claims:** under today's code, "EIG" in C5 and "EIG choosing" in C3-boed mean "first menu cell" on most decisions.
- **Fix:** build items 2, 3 and 5 test-first before the freeze's code commit. No EIG unit has run, so only unrun keys move.
- **Replacement words for §2.2:** "BIC weights; EIG to relative precision; a full tie taken from the truth-keyed random draw, all entering `EIGPolicy.config`." Add to §3: "Every C5 and C3-boed read is also printed split by `EIGSummary.degenerate`, as LOG 2026-10-27 item 4 requires."

**2. BLOCKING: the agent's wall clock includes the policy's CPU time.**
- The session runs under `asyncio.timeout(3600)` (`harness/live.py:627`). Tool calls run inside it (`:232`, `:401`), and policy decisions run inside `run_experiment` (`investigation/tools.py:536-541`).
- On 5 of 20 dev truths, FSD's simulation alone exceeds 3,600 CPU s, and those figures are floors (`cost-per-investigation.txt`):

```
truths: 20 FSD>3600: 5  dev-002(3973) dev-004(6430) dev-009(4229) dev-010(6456) dev-011(5458)
```

- A session that ends on wall time with no submission takes the floor (`contrasts.py:319-325`, ok and not submitted).
- So C5-gap-A, C5-tell-A and C3-boed would partly measure policy compute time against the limit, and that time is concentrated on exploding truths.
- §8's "about 23 hours at concurrency 1" also leaves out the 89.7 h of policy CPU that runs inside the sessions. The real figure is at least 112.7 h.
- **Replacement:** add "**D5.** A policy decision runs inside the session's wall clock. Owed before launch, test first: the clock excludes framework decision time, recorded per call, for every AG arm alike." This changes no prompt text, because the stated limit stays the same.

**3. BLOCKING: the §2.3 recommendation is partly allowed and partly not.**
- **Allowed:** choosing A on dev data. Dev is not P4b's data, so R5's literal text permits it.
- **Not allowed as written:** the fail branch says "the control room chooses between B … and A". The rulings the draft cites send B elsewhere:
  - SPEC §4.2: "if either fails, the rule returns to the owner".
  - LOG 2026-10-29: "no new freeze is proposed until its calibration carries pool uncertainty".
  - criteria §8: "He² resolution comes first".
  - R5's accepted words: B "returns only under a new freeze, after the He² resolution … on a control with no test data".
- Calling B "SPEC §4.2's parked option 2" is also inaccurate: Fx for FSD is not part of option 2.
- No decision rule is written before the dev run, and a pass or fail at n = 20 is a weak proxy for the test at n = 40. Using the draft's own power function:

| share, h | P(pass) at n = 20 | P(pass) at n = 40 |
|---|---|---|
| 0.25, 0.65 | 0.432 | 0.813 |
| 0.5, 0.8 | 0.185 | 0.514 |

- Uncited dev evidence already predicts failure under A. At decision 0, truthFSD under rule (a) agreed with telling's d\* under rule (b) on 3 of 14 dev truths (`.cache/drafts/fsd-ties/draft.md:84,113`). The session-16 red team reproduced this (`redteam-s16.md:4`, "C5 as built 3/14").
- The draft ignores a third candidate: K0 with Fx, which passed control B (`score-ctlB.txt`: `Fx|K0: pass=True`) and changes nothing A1 tested.
- Rule 5.3 already runs control 5 on the test split before any LLM session. So the dev run mainly produces evidence for an owner decision; it doesn't protect the 160 sessions.
- The dev run also delays the P4 miss check, which is on hold until this freeze. The draft should say so.
- **Replacement:** "Freeze everything except this choice before any dev run. From the dev run, estimate the degenerate share and h, and compute P(pass at 40) with `c5_power.power`. If it is at least 0.8, freeze A. Otherwise A stays the rule and P4b is planned as exploratory, unless the owner reopens option 2 under SPEC §4.2, after criteria §8's He² resolution and a new control on streams with no test data. K0 with Fx is a candidate for the control room."

**4. BLOCKING (changes claims): under A, C5 tests an FSD that failed its own control.**
- A uses rule Fa, which failed control B's B1 criterion at 0/120. The frozen criteria §8 reads that as "FSD needs its own exclusion, whatever the telling rule".
- Control 5 tests truthFSD, not FSD, so a control 5 pass does not cover this.
- **Add to §1 and §10:** "Under A every C5 label speaks for FSD with the undefined-atom rule, which failed B1, and not for FSD as SPEC §2.4 defines it."

**5. BLOCKING: C5-tell-A's outcome is undefined at the freeze.**
- AG-c agents have a `diagnostic` tool that computes any catalogue diagnostic on any dataset (`tools.py:808-821`). Matching ignores time arguments.
- D1's wording ("diagnostics that the framework computed") would therefore count the agent's own calls, so C5-tell-A would mostly measure agent behaviour rather than the policy.
- D1 says today's code gives the agent rate "no input". That's not accurate. `telling_observed` skips an AG row that is ok and returns `False` for every AG row that failed (`analysis.py:611-618`), so a rate read today would be 0 over the failed sessions.
- **Replacement:** "For C5-tell-A, 'observed' is the policy's chosen diagnostic on an experiment that left data, as for the B-sym arms. The agent's own calls count only toward the exploratory agent-chosen rate. The read refuses any LLM row with no `computed` field."

**6. BLOCKING: the telling labels use an anti-conservative test.**
- A percentile bootstrap on paired 0/1 outcomes with sparse discordance labels too eagerly (`/tmp/rt-p4b/boot/binlab.py`, using the project's own `paired_bootstrap_ci`, n = 40):

```
b=4 c=0: bootstrap 'first higher' in 20/20 streams; exact two-sided McNemar p=0.1250
b=5 c=0: bootstrap 'first higher' in 20/20 streams; exact two-sided McNemar p=0.0625
b=5 c=1: bootstrap 'first higher' in 0/20 streams; exact two-sided McNemar p=0.2188
```

- The label is also non-monotone: adding one opposite discordance flips 5-0 to "not separated".
- **Replacement for §3:** "C5-tell-\* are labelled by exact two-sided McNemar at 0.05, with b and c printed; the bootstrap CI is printed beside the label and does not set it."

**7. BLOCKING: failure and void handling is neither mechanical nor outcome-neutral.**
- §6 calls a unit killed at the CPU ceiling "failed" (floored, not observed). §8 says unfinished work is "reported as not run". The two contradict each other.
- Cost tracks exploding truths, so a ceiling kill hits FSD, truthFSD and EIG hardest.
- A policy fault voids an AG session (`tools.py:542-554`), and voids are dropped from every table (`analysis.py:15-19`). The draft never mentions voids, and void rates can differ by arm.
- "Bug" (retried) versus "own reasons" (floored) is a judgment made after seeing the failure.
- P4's pair rule floors only `-inf`, no submission, or `FAILED_FIT`; every other failure is left out (`contrasts.py:319-325`). §6's words are broader than that code.
- **Replacement:** "A row is floored, and counts as not observed, exactly when `contrasts._takes_floor` floors it. Every other failure and every void is absent, counted per arm beside each read. A set limit on absent truths per arm makes a read exploratory. Units not run at the ceiling are not rows; a resulting unpaired truth makes that read exploratory and voids control 5."

**8. BLOCKING: the read code and its seed can be fixed after P4's reads open.**
- D3 allows the read module "or an amendment before any P4b unit", which is after the P4 miss check opens P4's reads. C3-boed's first arm and the ORACLE rows are P4 rows.
- The read root and resample count are not named. P4's frozen text names every root (`2026-11-05_p4-search-misses.md:65`).
- **Replacement:** "The read module and D1's field are frozen in this freeze's code commit, before the miss check launches. Reads draw from `SeedSequence([R, i])` with R = <a new root outside 20261104–20261113>, i = 0–4 in §3's order, 10,000 resamples."

**9. BLOCKING (text): the K0 confirmation hasn't happened.**
- The draft says the K0 recomputation "is in session 38's journal". The journal lists it under "Out". `.cache/drafts/s38/k0/k0_recompute-out.txt` stops at "== 3. today's code", and the process was still running when I checked (started 21:34Z).
- R5 says to mark K0 as "read from the code" and to confirm it with a run before the P4b freeze. The freeze waits on that.

**10. Non-blocking: the 300 h ceiling is a stop, not an estimate with margin.**
- No P4b driver exists. Nothing under `scripts/` runs the policy arms, and `pilot.py` has `STOP` but no CPU meter.
- Costs left out:
  - entertained-set refits: up to 4 per decision × 8 decisions, on growing data (`policy_search.py:283-297`);
  - epoch-2 fits on the larger data;
  - world truth runs that reach the cap (up to about 108 CPU s each);
  - the AG sessions' own fit CPU;
  - a tuned B-sym setting that may cost 2–2.5× `c0`.
- The FSD and truthFSD figures are floors (decision 0 costed 8 times).
- The arithmetic itself reproduces: 252.18 h total, 37.91 h for the dev run.
- The telling allowance is 4× its own reasoning, not "about twice".
- **Fix:** add a D6 for the driver and its meter.

**11. Non-blocking: §7's power proxy understates the detectable difference.**
- I reproduced sd 0.0303 (n = 18) exactly, but it drops dev-010 and dev-011, the two truths with a `-inf` row.
- Floored the way these reads floor them: n = 20, sd 0.0450, so the detectable difference is about 0.020 nats per event.
- **Replacement:** "about 2.8 × 0.045 / √40 = 0.020".

**12. Non-blocking: the dev stop figures are stated more firmly than the data allows.**
- "12 designs of 10 truths" is correct, but the truth side was measured on 2 replicates (`truth-r01.txt`).
- "6 of 20 … at every decision" is inferred with B-lib's pick standing in for FSD's parametric side (fsd-cap-draft2, finding 2; LOG red team: "GLM never stops says little about B-sym's fits").
- The 0.5 share in the power table inherits both caveats.

**13. Non-blocking: C3-boed's two arms run weeks apart on a model alias.**
- Sonnet is pinned as `claude-sonnet-5-5`, not a dated snapshot (`live.py:129`). Served models are recorded (`llm.py:269`).
- **Add:** "If the two arms' recorded served models differ, C3-boed is exploratory."

**14. Non-blocking: no correction for five reads.** With five uncorrected 95% intervals, the chance of at least one false label is about 23% if the reads were independent. The report should say this beside any "falsified".

**15. Non-blocking: naming nit.** `plan.PolicySettings` is the same class as `investigation/policy.PolicySettings` (imported at `plan.py:108`); the B-sym arms' class is `plan.PolicyArmSettings` (`:295`).

## Claims that checked out
- **`computed` and `telling_observed`:** `execute.py:446`/`:517` and `analysis.py:611` say what D1 says.
- **AG-c-S+EIG and AG-S-boed:** they are one `LLMArm`, so one name and one key. 160 sessions is correct.
- **Cap path:** `policy.py:224`/`:229` and `plan.session_policy_settings` carry the same cap to the AG arms.
- **A1 and B scores:** match `score-ctlA1.txt` and `score-ctlB.txt` exactly.
- **Dev costs:** 1,614 / 6,456 / 2,957 / 17,617 s match `cost-per-investigation.txt`.
- **Power script:** rerun gives identical figures (0.058 / 0.219 / 0.514 / 0.830; 0.822 / 0.990 / 1.000 / 1.000).
- **Nothing has run:** no progress log names a policy arm, and only the dev split exists.
- **Truth isolation:** I found no path from the truth to an agent. The AG policy seed is derived framework-side (`runner.py:357`).

## Your six questions
- **Can the decision rules be gamed after seeing data?** Yes, through 3, 5, 7 and 8.
- **Are the exclusions outcome-neutral?** No (7).
- **Is the sample size justified?** It is fixed by the split. Control 5 likely fails under A (3), and the gap detectable difference is about 0.020 (11).
- **Does each primary analysis answer its question?** Not yet (1, 2, 4, 5, 6).
- **Is the cost ceiling a true upper bound?** It bounds CPU only once a meter exists. The work estimate under it is a floor, and there is no LLM ceiling (10, 2).
- **Does anything allow tuning after the fact?** Yes (3, 8).

**Findings that change what the project can claim:** 1, 2, 4, 5, 6 and 11. Finding 3 decides whether any P4b read can be confirmatory at all.

## What I did not check
- I ran no policy unit or determinism rerun; both are beyond the CPU limit.
- I did not run `k0_recompute.py`. I only read its no-draw tripwire.
- I did not read the tuning store, so the 340 s per-unit figure is checked only as 11.79 h ÷ 124 units.
- I did not verify that the three AG policy prompts are byte-identical; I relied on `prompts.py:29-35`.
- I did not check that a wall-time session's row is `ok`, or the brake's effect on session clocks.
- B-boed does not exist, so there was nothing to check.

Scratch files: `/tmp/rt-p4b/power/c5_power.py` (copy of the draft's script), `/tmp/rt-p4b/power/dev20.py`, `/tmp/rt-p4b/boot/binlab.py`, `/tmp/rt-p4b/p3sd.py`, `/tmp/rt-p4b/p3sd_floor.py`.

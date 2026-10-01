# v1 results, in one page

v1 is frozen at the git tag **`v1.0`**. Its design is [`SPEC.md`](SPEC.md) in
this folder, and its full decision record is [`DECISIONS.md`](DECISIONS.md),
which is large; search it rather than reading it end to end.

## The question

Once conventional statistics has detected that the model space is inadequate,
does an LLM-guided system propose useful executable structure better than
retrieval, symbolic search and fixed-expansion baselines?

## What was run

The preregistered matrix: 56 cells × 20 seeds = **1,120 investigations** across
seven systems and twelve scenarios on a simulated marked point process. Claude
was the proposer, with 112 live model calls. A separate 20-replicate arm (B6, the
uniform random proposer) was run for criterion 5.

## What it found

1. **Detection works.** The posterior-predictive probe flagged inadequacy on 85%
   of out-of-library runs (S11). Its pooled false-positive rate on scenarios
   with nothing to find was 3.1% (5/160).
2. **The preregistered contrast tied exactly.** The LLM system (V7) and
   retrieval (B4) both scored 0.6970 on interventional similarity, with a paired
   difference of 0.0000 [0.0000, 0.0000] (n = 17).
3. **The tie was forced.** Two independent walls made S11's extension stage
   unwinnable for every system:
   - **Vocabulary wall.** The agent's proposal menu cannot express the truth (a
     size → arrival dependency). Every expressible proposal sits at structural
     distance ≥ 1.5 from it, while the null sits at 1.0.
   - **Evidence wall.** The one diagnostic carrying the truth's signature was
     available in 112 of 112 LLM briefs and observed in none. BOED never chose
     it, because no entertained hypothesis made it informative.
4. **The LLM changed nothing anywhere.** Re-reading the ledger on 2026-10-01: V7
   equals V1 (BOED with no LLM) on D1, D2, D3, D5 and D6 in all twelve
   scenarios. Eight of the twelve were solved by the closed library before any
   proposal was made, and the agent's grammar had five structural cells.

The research question was left **open**, not answered "no". The design reasons
are what v2 ([`../SPEC.md`](../SPEC.md)) is built to fix.

## Reproducing the report

The ledgers are not in git. They live in `.cache/campaign/` on the machine that
recorded them (`spec9.db`, 3.6 MB; `criterion5.db`, 57 KB), with the LLM
transcripts in `.cache/transcripts/spec9-v3.json`. They are meant to be
attached to the `v1.0` GitHub release.

```sh
git checkout v1.0
uv sync
uv run python scripts/report_matrix.py .cache/campaign/spec9.db \
  --platform "$(uname -sm)" --numpy 2.5.1 --grammar pointproc/1.1.0 \
  --env-version pointproc/pointproc/1.1.0+1.2.0+1.1.0 \
  --data-version pointproc/generated/1.0.0 \
  --metric-version metrics/9b1c54c9d49f49f656c30e32d21d4a7b \
  --contrast --criterion5 .cache/campaign/criterion5.db
```

Every cell was recorded on one platform (`MINGW64_NT-10.0-26200 x86_64`,
numpy 2.5.1). Run this way on 2026-10-01, it exits 0 and prints the figures
above.

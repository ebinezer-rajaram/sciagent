# Transcript corpora

A recorded model call is not a cache. The models this framework targets reject
`temperature`, `top_p` and `top_k` outright, so there is no setting — not even a
degenerate one — that makes two calls with one prompt return one answer. The
recorded response is therefore *the* reproducible artefact, exactly as an
`EmpiricalTable` is the artefact and simulation is the process that produces it
(`src/sciagent/systems/llm/transcripts.py`).

That makes a corpus the reproducibility story for **every LLM number this
project reports**. Gate A35 exists because that story was, until now, uncheckable
by anybody outside the machine that recorded it: no corpus was committed, and no
hash for one appeared anywhere in the repository.

## Why the hash and not the file

The corpora stay out of git. `.gitignore` excludes `.cache/`, `spec9.json` is
644KB, and it grows with every campaign. What the repository carries instead is
the digest, which is enough for the claim being made: a third party handed a
corpus can establish it is *the* corpus these numbers came from, and a
disagreement is visible rather than silent.

The format below is literal `sha256sum` output, so that check needs nothing from
this framework — extract the fenced block and run `sha256sum -c` on it from the
repository root. `sciagent.systems.llm.transcripts.corpus_digest` computes the
same value, over the same bytes, for callers already inside Python.

Hashing the file's bytes rather than a re-canonicalisation of its contents is
safe because `TranscriptStore.save` already writes canonically: sorted keys,
`indent=2`, and `newline="\n"` regardless of platform. The same corpus recorded
on Windows and on Linux is byte-identical.

## The corpora

| Corpus | Calls | Bytes | Scheme | Backend |
|---|---|---|---|---|
| `.cache/transcripts/spec9.json` | 112 | 659,740 | `transcript/2` | `claude-agent-sdk` / `claude-opus-5` / `effort=high` |
| `.cache/transcripts/llm_smoke.json` | 2 | 12,130 | `transcript/2` | `claude-agent-sdk` / `claude-opus-5` / `effort=high` |

`spec9.json` is the SPEC §9 campaign corpus: the calls behind the 18 LLM cells
(V3, V4 and V7) of the 1,120-row recorded matrix. One backend identity, which is
what `scripts/run_matrix.py --replay` requires — a transcript address hashes the
provider id, the model and the settings, so a corpus holding two identities
cannot be replayed under one.

112 calls is fewer than the 360 replicate-calls those cells made, and that is
correct rather than a gap. An address covers the *brief*, not the scenario or the
seed, so replicates presenting an identical brief share one address — recording
the same answer twice would hide that they asked the same question.

```
e2361f03dce79bb5e9d0badd76635b2e2928f790375df82cef63dac78749c49c  .cache/transcripts/spec9.json
a9053d1ab8c4be611609f794f5fb9b4f55a78ed24bb6f162dccd4e10de12458b  .cache/transcripts/llm_smoke.json
```

## What these corpora do not contain

**Neither holds a single refusal record**, and both were recorded before gate A35
made one possible. Checked, not assumed: every one of the 114 calls across the
two files is an `answered` record.

This is the observation `docs/DECISIONS.md` (2026-08-21) reached from the other
direction — a refusal in the recorded campaign stored *nothing* anywhere, so the
number of them is unrecoverable from the tree. A35 closes the hole going forward;
it cannot retro-fill an address whose call left no trace. If any replicate of the
recorded campaign was scored as refused, its address is absent from `spec9.json`
and no code change reaches it.

## Verifying

From the repository root, with the corpora present:

```sh
sed -n '/^[0-9a-f]\{64\}  /p' docs/CORPUS.md | sha256sum -c
```

`tests/acceptance/test_a35.py` checks the manifest's shape and every named path
structurally on every platform, and compares digests for whichever corpora are
present. A fresh checkout has none, so the digest comparison is the one part that
does not run in CI — it runs where the corpora and the recordings that produced
them live.

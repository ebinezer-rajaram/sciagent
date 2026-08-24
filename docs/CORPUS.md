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

**Both are `transcript/2`, and this process addresses calls as `transcript/3`.
Neither replays.** Gate A36 moved the scheme deliberately: the tool schema's
`name` field no longer names a mechanism, the structural menu moved from the
brief into the system block so a cache can hold it, and an address now carries a
sample index. All three change what is hashed, so the addresses in these files
name requests nothing still produces. `TranscriptStore.load` *refuses* them by
version — it does not load them and then miss every address, which would look
like a model that had changed its mind about everything at once — and
`scripts/run_matrix.py --replay` over either raises `TranscriptSchemeError`.

The bump itself touches nothing on disk, and the digests below stay correct: they
are the record of what these numbers came from, and that claim is unaffected by
this process no longer being able to address them. Replaying the §9 LLM cells
again means **recording again**, which is a deliberate act with a live cost and
is not something a code change can restore. `docs/DECISIONS.md` (2026-08-23) is
the decision that declined this bump while A40's re-derivation still needed the
corpus, and records that A36 would take it once A40 landed; A40 landed at
`docs/BACKLOG.md` rank 12.

> **Back these two files up before any recording pass, and do not point
> `--transcripts` at either path.** `TranscriptStore.save`'s append-only guard —
> `_refuse_to_drop` — protects a corpus by *reading* it first, and it cannot read
> one under a superseded scheme. It therefore treats `TranscriptSchemeError` as
> "this file may be replaced", by design and with its reasoning written down:
> every call in it would miss anyway, and refusing as well would strand the file
> forever. The consequence of the bump is that both corpora are now on that
> branch, so a `save()` to either path **silently overwrites it** — the guard
> that would normally refuse is exactly the one that cannot run. Verified rather
> than reasoned about: saving a one-call store over a `transcript/2` fixture
> leaves the fixture's call gone and raises nothing.
>
> Of the repository's two `save()` callers, only one is safe, and it is safe
> only incidentally: `scripts/run_matrix.py` loads the path before writing it, so
> a superseded scheme raises there and it exits. `scripts/rate_limit_pilot.py`
> does **not** — it builds a fresh `RECORD` store, never loads, and writes to
> `<out>.transcripts.json`, so post-bump it replaces a `transcript/2` pilot
> corpus without a word. Its `_save_transcripts` reports *"kept its existing
> calls"* when `save()` refuses; that refusal can no longer fire for such a file,
> so the silence there now means the opposite of what it used to.
> (`scripts/stage_a_seed_sweep.py` records but never saves.)
>
> Nothing in the code enforces this warning. It is a property of the orphaning,
> not a bug in the guard — restoring the guard would mean refusing to ever write
> to a path holding an unreadable corpus, which is the strand-forever behaviour
> that clause exists to avoid. It is written here because these files cannot be
> regenerated without paying for them again.

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

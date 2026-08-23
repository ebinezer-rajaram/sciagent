# Hypothesis corpus

Counterexamples found by the property gates, kept so that one found on one
machine reaches every other. Written by Hypothesis, read by Hypothesis; nothing
here is authored by hand.

`tests/conftest.py` roots the profile's `DirectoryBasedExampleDatabase` at this
directory. Hypothesis's own default lives under `.hypothesis/`, which it
populates with a `.gitignore` holding `*` — so a counterexample found there can
never be committed and dies with the working tree it was found in. That is the
gap gate A33 closes, and it is why this directory is tracked.

## What lands here, and what to do with it

A **passing** run writes nothing. Only a failure writes, and it writes the
minimal failing example under a hash-named subdirectory.

So a file appearing here means a property gate failed. Commit it alongside the
fix: the entry makes the same example the *first* thing every later run tries,
on every machine, which is how a fixed bug stays fixed. Hypothesis removes the
entry itself once the property holds for it again.

A failure also prints a `@reproduce_failure(...)` decorator in its notes —
`print_blob` is on — for reproducing that one case without the corpus.

This directory is deliberately not in the suite-freshness content hash; only its
*index* state is, since gate A33 asserts that it is tracked rather than what it
holds. See the NON-PYTHON INPUTS section of `.claude/hooks/suite-freshness.sh`.

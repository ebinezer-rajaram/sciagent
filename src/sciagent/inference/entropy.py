"""Discrete entropy, with the Miller-Madow correction (SPEC §6.2 A7).

Entropy enters this project twice: as the uncertainty of a posterior over
hypotheses, and as the uncertainty of a predictive distribution over binned
diagnostic outcomes, which is what backlog item 8's expected information gain is
a difference of. Both are distributions over a finite support, so both are
estimated from counts.

Why a correction is needed at all
---------------------------------

Plug-in entropy -- substituting observed frequencies into ``-sum p log p`` -- is
biased *downward*, and the bias does not vanish with the estimator's variance. To
first order it is ``-(K - 1) / (2N)`` nats for ``K`` occupied cells and ``N``
samples, and it is a systematic term: a table built from 2000 replicates over 12
cells understates every entropy by about the same amount. A difference of
entropies is what BOED maximises, so a bias common to both terms largely cancels
there -- but only largely, because the two distributions rarely occupy the same
number of cells, and the residue is exactly the quantity A7 bounds.

:func:`miller_madow_entropy` adds ``(K_hat - 1) / (2N)`` back, using the number of
*occupied* cells. That is a deliberate under-correction when cells exist but were
never drawn, and it is the safe direction: over-correcting would inflate an
entropy difference, which is the error that would make a useless experiment look
informative.

Units are bits throughout, matching the prefix code that defines the prior.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from sciagent.core.errors import InferenceError

_LN2 = math.log(2.0)


@dataclass(frozen=True, slots=True)
class EntropyEstimate:
    """An entropy in bits, its standard error, and the correction applied."""

    bits: float
    standard_error: float
    correction: float
    """Bits added to the plug-in value. Zero for :func:`plugin_entropy`."""

    occupied: int
    """Cells with a non-zero count, the ``K_hat`` of the correction."""

    samples: int


def entropy_bits(probabilities: Sequence[float]) -> float:
    """Return the Shannon entropy of a distribution, in bits.

    Guarantees a deterministic result: terms are summed by
    :func:`math.fsum` in the order given, so the value does not depend on
    accumulation order. Zero probabilities contribute zero, by the usual
    ``0 log 0 = 0`` convention.
    """
    total = math.fsum(probabilities)
    if not math.isfinite(total) or total <= 0.0:
        raise InferenceError(
            f"probabilities must sum to a finite positive number, got {total!r}"
        )
    if not math.isclose(total, 1.0, rel_tol=1e-9, abs_tol=1e-12):
        raise InferenceError(
            f"probabilities must sum to 1, got {total!r}; normalise before "
            f"taking an entropy so that the caller decides what to do about "
            f"mass that is missing"
        )
    for value in probabilities:
        if value < 0.0:
            raise InferenceError(f"negative probability {value!r}")
    return -math.fsum(value * math.log2(value) for value in probabilities if value > 0)


def _frequencies(counts: Sequence[int]) -> tuple[list[float], int, int]:
    """Return ``(frequencies, total, occupied)`` for a count vector."""
    for count in counts:
        if count < 0:
            raise InferenceError(f"negative count {count!r}")
    total = sum(counts)
    if total <= 0:
        raise InferenceError("cannot estimate an entropy from zero samples")
    occupied = sum(1 for count in counts if count > 0)
    return [count / total for count in counts], total, occupied


def _standard_error(frequencies: Sequence[float], bits: float, total: int) -> float:
    """Return the asymptotic standard error of a plug-in entropy, in bits.

    The delta-method variance of ``-sum p log2 p`` under multinomial sampling is
    ``(E[(log2 p)^2] - H^2) / N``. It is the sampling error of the estimate and
    says nothing about the plug-in bias, which is why A7 checks the two
    separately.
    """
    second = math.fsum(
        value * math.log2(value) * math.log2(value)
        for value in frequencies
        if value > 0
    )
    variance = (second - bits * bits) / total
    return math.sqrt(variance) if variance > 0.0 else 0.0


def plugin_entropy(counts: Sequence[int]) -> EntropyEstimate:
    """Return the uncorrected plug-in entropy of a count vector, in bits.

    Provided so that A7 can measure the bias the correction removes rather than
    asserting it. Not for use in a reported number.
    """
    frequencies, total, occupied = _frequencies(counts)
    bits = entropy_bits(frequencies)
    return EntropyEstimate(
        bits=bits,
        standard_error=_standard_error(frequencies, bits, total),
        correction=0.0,
        occupied=occupied,
        samples=total,
    )


def miller_madow_entropy(counts: Sequence[int]) -> EntropyEstimate:
    """Return the Miller-Madow corrected entropy of a count vector, in bits.

    Guarantees the reported ``bits`` is the plug-in value plus
    ``(occupied - 1) / (2 * samples)`` nats, converted to bits, and that
    ``correction`` records exactly what was added so a caller can audit it.

    The standard error is the plug-in standard error unchanged: the correction is
    a deterministic function of the occupancy, so to first order it shifts the
    estimate without widening its sampling distribution.
    """
    estimate = plugin_entropy(counts)
    correction = (estimate.occupied - 1) / (2.0 * estimate.samples * _LN2)
    return EntropyEstimate(
        bits=estimate.bits + correction,
        standard_error=estimate.standard_error,
        correction=correction,
        occupied=estimate.occupied,
        samples=estimate.samples,
    )

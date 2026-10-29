"""Exact arithmetic behind criteria.md's thresholds (no simulation, no randomness).

Run: python3 -I power.py > power-out.txt
"""

from __future__ import annotations

from fractions import Fraction
from itertools import product
from math import comb


def mcnemar_p(b: int, c: int) -> Fraction:
    """controls_p4.mcnemar_exact_greater, in exact arithmetic."""
    n = b + c
    return Fraction(sum(comb(n, i) for i in range(b, n + 1)), 2**n)


def p_pass(n: int, h: float, r: float, alpha: float = 0.05) -> float:
    """P(control 5 passes) over n independent units: truthFSD hits w.p. h, random w.p. r."""
    pb, pc = h * (1 - r), r * (1 - h)
    pboth = h * r
    total = 0.0
    for b, c in product(range(n + 1), repeat=2):
        if b + c > n or not mcnemar_p(b, c) < alpha:
            continue
        rest = n - b - c
        # multinomial over (b, c, concordant)
        w = comb(n, b) * comb(n - b, c)
        total += w * pb**b * pc**c * (1 - pb - pc) ** rest
    del pboth
    return total


def binom_ge(n: int, k: int, p: float) -> float:
    return sum(comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k, n + 1))


r = 1 - (34 / 35) ** 8
print(f"random over 8 decisions, one label in 35: r = {r:.4f}")
print(
    f"smallest b with c = 0 that passes: {min(b for b in range(1, 20) if mcnemar_p(b, 0) < 0.05)}"
)
for h in (1.0, 0.99, 0.9, 0.8, 0.5):
    print(f"N=10: P(control 5 passes | truthFSD ever-hit {h}) = {p_pass(10, h, r):.4f}")
print(
    f"N=10: P(pass | truthFSD ever-hit = r, no better than random) = {p_pass(10, r, r):.4f}"
)
for p in (0.95, 0.9, 0.8):
    print(f"P(>= 8 of 10 seeds | per-seed hit {p}) = {binom_ge(10, 8, p):.4f}")
print(f"P(>= 72 of 80 | per-pair 0.95) = {binom_ge(80, 72, 0.95):.4f}")
print(f"P(>= 56 of 80 | per-pair 0.80) = {binom_ge(80, 56, 0.80):.4f}")
for pi in (0.01, 0.03, 0.05, 0.10, 0.20):
    t32, t16 = 1 - (1 - pi) ** 32, 1 - (1 - pi) ** 16
    differ = t32 * (1 - t16) + (1 - t32) * t16
    ever = 1 - (1 - t16) ** 8
    print(
        f"pi={pi:.2f}: P(telling excludes, R=32)={t32:.3f}, P(truthFSD excludes, R=16)={t16:.3f}, "
        f"K4 per-decision exclusion disagreement={differ:.3f}; "
        f"P(truthFSD excludes at >=1 of 8 decisions)={ever:.4f}"
    )

"""A2 band arithmetic for criteria.md v2 (exact, no randomness). Run: python3 -I a2band.py > a2band-out.txt"""

from math import comb


def void(n, keep):
    return sum(
        comb(n, j) * keep**j * (1 - keep) ** (n - j)
        for j in range(n + 1)
        if j < 5 or n - j < 5
    )


for n in (20, 24):
    for pi in (0.01, 0.015, 0.02, 0.025, 0.03, 0.035, 0.04, 0.045):
        keep = (1 - pi) ** 32
        cap16 = 1 - (1 - pi) ** 16
        print(
            f"N={n} pi={pi:.3f}: P(keep e1)={keep:.3f}; expected kept seeds {n * keep:.1f}; "
            f"P(<5 seeds on a side)={void(n, keep):.4f}; per pair K5 kept-capped {keep * cap16:.3f}, "
            f"K4 kept-uncapped {(1 - keep) * (1 - cap16):.3f}"
        )
for n in (15,):
    k = -(-4 * n // 5)
    for p in (0.95, 0.9):
        print(
            f"N={n}: P(>= {k} of {n} | {p}) = {sum(comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k, n + 1)):.4f}"
        )

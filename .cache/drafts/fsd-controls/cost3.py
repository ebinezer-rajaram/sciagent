"""CPU estimate for criteria.md v3 (no simulation). Per-run CPU s incl. 35 diagnostics (medians,
probe*-out.txt): truth uncapped 0.12, member 0.09; truncated 0.72 (T_A), 0.97 worst (probe3),
0.76 size rung (probe4); near-critical 0.53 (sweep at 1.00). Run: python3 -I cost3.py > cost3-out.txt"""

from math import comb

T, M, NC, H = 0.12, 0.09, 0.53, 3600.0


def a_like(n, tc):
    return n * (
        7 * 32 * (T + M) + 32 * (tc + M) + 8 * (7 * 16 * (T + M) + 16 * (tc + M))
    )


def cal(tc):
    return 2 * 256 * (7 * (T + M) + (tc + M))


def b_test(n, ta, tb):
    return n * (
        7 * 32 * (T + M)
        + 32 * (tb + M)
        + 8 * (6 * 16 * (T + M) + 16 * (ta + M) + 16 * (tb + M))
    )


SIM_DELTA = (
    0.1 * H
)  # 200 simulated A2 tests by subsampling, no simulation runs (allowance)
for case, tca, tcb, ra, rb, rref, steps in (
    ("expected", 0.72, 0.78, 1, 1, 1, 3),
    ("worst", 0.97, 0.97, 3, 4, 2, 6),
):
    a1 = ra * cal(tca) + a_like(15, tca)
    b = rb * cal(tcb) + rref * 256 * tca + b_test(15, tca, tcb)
    a2 = steps * 512 * NC + cal(NC) + SIM_DELTA + a_like(24, NC)
    print(
        f"{case}: A1 {a1 / H:.2f} h, B {b / H:.2f} h, A2 {a2 / H:.2f} h, total {(a1 + b + a2) / H:.2f} CPU h"
    )


def tail_le(n, p, k):
    return sum(comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k + 1))


print(
    f"full-calibration pi-hat band [0.0075, 0.040] on 512 runs: P(below | pi=0.015) = {tail_le(512, 0.015, 3):.3f}; "
    f"P(above | pi=0.030) = {1 - tail_le(512, 0.030, 20):.3f}"
)

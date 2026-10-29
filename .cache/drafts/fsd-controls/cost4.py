"""CPU estimate for criteria.md v4 (no simulation). Per-run CPU s incl. 35 diagnostics (medians,
probe*-out.txt): truth uncapped 0.12, member 0.09; truncated 0.72 (T_A), 0.97 worst (probe3), 0.78 (T_B),
0.76 (size rung, probe4); near-critical 0.53. He2 table (8 x 35) from stored values: 10.7 ms at R=32,
7.1 ms at R=16 (bench-out.txt). B now calibrates all four rungs. Run: python3 -I cost4.py > cost4-out.txt"""

T, M, NC, H = 0.12, 0.09, 0.53, 3600.0
T32, T16 = 0.0107, 0.0071


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


BOOT = 1000 * (T32 + T16)  # p32 + p16 per calibrated rung
BOOT_B = 1000 * (T32 + 2 * T16)  # + p_F
DPRED = 200 * (24 * T32 + 24 * 8 * T16)


def score(n):  # one telling + 8 truthFSD/FSD tables per seed, all rules
    return n * (T32 + 8 * T16) * 4


for case, tca, tcb, ra, rref, steps in (
    ("expected", 0.72, 0.78, 1, 1, 3),
    ("worst", 0.97, 0.97, 3, 2, 6),
):
    a1 = ra * (cal(tca) + BOOT) + a_like(15, tca) + score(15)
    b = 4 * (cal(tcb) + BOOT_B) + rref * 256 * tca + b_test(15, tca, tcb) + score(15)
    a2 = steps * 512 * NC + cal(NC) + BOOT + DPRED + a_like(24, NC) + score(24)
    print(
        f"{case}: A1 {a1 / H:.2f} h, B {b / H:.2f} h, A2 {a2 / H:.2f} h, total {(a1 + b + a2) / H:.2f} CPU h "
        f"(Delta_pred simulation {DPRED / H:.3f} h)"
    )

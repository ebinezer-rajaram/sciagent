"""CPU estimate for criteria.md v2 (no simulation). Per-run CPU s incl. 35 diagnostics, medians from
probe*-out.txt: truth uncapped 0.12, member 0.09; truncated T_A 0.72 (theta+=0.5) / 0.97 (0.6, probe3);
truncated T_B 0.78 (probe2); near-critical 0.53 (sweep at branching 1.00). Worst case uses 0.97 for every
truncated run. Run: python3 -I cost2.py > cost2-out.txt"""

T, M, NC = 0.12, 0.09, 0.53
H = 3600.0


def a_like(
    n, tc
):  # telling (32) + truthFSD (16 x 8 decisions), 7 plain designs + 1 capped
    return n * (
        7 * 32 * (T + M) + 32 * (tc + M) + 8 * (7 * 16 * (T + M) + 16 * (tc + M))
    )


def cal_a(tc):  # two stream sets (per-design and common-random-number), 256 per side
    return 2 * 256 * (7 * (T + M) + (tc + M))


def b_test(
    n, ta, tb
):  # telling (32) + FSD (16 x 8): e1 reference truncates (ta), e2 truncates (tb)
    return n * (
        7 * 32 * (T + M)
        + 32 * (tb + M)
        + 8 * (6 * 16 * (T + M) + 16 * (ta + M) + 16 * (tb + M))
    )


def cal_b(tb):
    return 2 * 256 * (7 * (T + M) + (tb + M))


rows = {}
for case, tca, tcb, rungs_a, rungs_b, ref_rungs, steps in (
    ("expected", 0.72, 0.78, 1, 1, 1, 3),
    ("worst", 0.97, 0.97, 3, 3, 2, 6),
):
    a1 = rungs_a * cal_a(tca) + a_like(15, tca)
    b = rungs_b * cal_b(tcb) + ref_rungs * 256 * tca + b_test(15, tca, tcb)
    a2 = steps * 512 * NC + cal_a(NC) + a_like(24, NC)
    rows[case] = (a1, b, a2)
    print(
        f"{case}: A1 {a1 / H:.2f} h, B {b / H:.2f} h, A2 {a2 / H:.2f} h, total {(a1 + b + a2) / H:.2f} CPU h"
    )

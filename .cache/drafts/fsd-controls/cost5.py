"""CPU estimate for criteria.md v5 (A2 deferred; every rung of A1 and B calibrated). No simulation.
Per-run CPU s incl. 35 diagnostics (medians, probe*-out.txt): truth uncapped 0.12, member 0.09; truncated
0.72 (T_A theta+ 0.5), 0.97 (theta+ 0.6, probe3; also used as worst for every truncated run), 0.78 (T_B),
0.76 (size rung). He2 table 8x35: 10.7 ms (R=32), 7.1 ms (R=16) (bench-out.txt).
Run: python3 -I cost5.py > cost5-out.txt"""

T, M, H, T32, T16 = 0.12, 0.09, 3600.0, 0.0107, 0.0071


def test_a(n, tc):
    return n * (
        7 * 32 * (T + M) + 32 * (tc + M) + 8 * (7 * 16 * (T + M) + 16 * (tc + M))
    )


def test_b(n, ta, tb):
    return n * (
        7 * 32 * (T + M)
        + 32 * (tb + M)
        + 8 * (6 * 16 * (T + M) + 16 * (ta + M) + 16 * (tb + M))
    )


def cal(tc):  # per-design + CRN sets, 256 per side, 8 designs
    return 2 * 256 * (7 * (T + M) + (tc + M))


BOOT_A = 1000 * (2 * T32 + 2 * T16)  # D*0 selection, held-out p32, p16, 99% clause
BOOT_B = BOOT_A + 1000 * T16  # + p_F
SCORE = 15 * (T32 + 8 * T16) * 11  # 7 telling/truthFSD rules + 4 FSD rules, per seed
DET = (
    2 * 64 * (T + M)
)  # determinism rerun: one seed's telling runs on one design, twice
for case, a_rungs, a_test, b_rungs, ta, tb, ref in (
    ("expected", (0.72, 0.72, 0.97), 0.72, (0.78, 0.78, 0.78, 0.76), 0.72, 0.78, 1),
    ("worst", (0.97, 0.97, 0.97), 0.97, (0.97, 0.97, 0.97, 0.97), 0.97, 0.97, 2),
):
    a1 = sum(cal(t) + BOOT_A for t in a_rungs) + test_a(15, a_test) + SCORE + DET
    b = (
        sum(cal(t) + BOOT_B for t in b_rungs)
        + ref * 256 * ta
        + test_b(15, ta, tb)
        + SCORE
        + DET
    )
    print(
        f"{case}: A1 {a1 / H:.2f} h, B {b / H:.2f} h, total {(a1 + b) / H:.2f} CPU h; +15% {1.15 * (a1 + b) / H:.2f}"
    )

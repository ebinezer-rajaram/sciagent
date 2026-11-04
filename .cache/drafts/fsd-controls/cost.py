"""CPU estimate of criteria.md section 3 from the probes' per-run medians (no simulation).

Inputs (CPU s per run incl. the 35 diagnostics, medians from probe*-out.txt):
truth uncapped 0.12, member 0.09, truth truncated 0.72 (A1/B rows: 0.71, 0.69, 0.78),
near-critical truth on e1 0.53 (sweep at branching 1.00: 6.4 s / 12).
Run: python3 -I cost.py > cost-out.txt
"""

from __future__ import annotations

T, M, TC, NC = 0.12, 0.09, 0.72, 0.53
DES, TEL, POL, DEC, CAL = 8, 32, 16, 8, 256


def a_seed(capped_truth: float) -> float:
    """telling + truthFSD for one seed: 7 uncapped designs, 1 design with the given truth cost."""
    telling = (DES - 1) * TEL * (T + M) + TEL * (capped_truth + M)
    truthfsd = DEC * ((DES - 1) * POL * (T + M) + POL * (capped_truth + M))
    return telling + truthfsd


def b_seed() -> float:
    telling = (DES - 1) * TEL * (T + M) + TEL * (TC + M)
    fsd = DEC * (
        (DES - 2) * POL * (T + M) + 2 * POL * (TC + M)
    )  # e1 (T_A) and e2 (T_B) truncate
    return telling + fsd


cal = CAL * ((DES - 1) * (T + M) + (TC + M))
rows = {
    "A1": (cal, 10 * a_seed(TC)),
    "A2": (6 * CAL * NC, 5 * a_seed(NC)),
    "B": (cal, 10 * b_seed()),
}
total = 0.0
for k, (c, t) in rows.items():
    total += c + t
    print(
        f"{k}: calibration {c / 3600:.2f} h + test {t / 3600:.2f} h = {(c + t) / 3600:.2f} CPU h"
    )
print(f"total {total / 3600:.2f} CPU h; with 30% margin {1.3 * total / 3600:.2f} CPU h")

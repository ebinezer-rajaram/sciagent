# P(test-time breach VOID) = P(Bin(15, p_seed) >= 3), criteria.md v7 L92.
# Per seed (A1, from §0 streams and §7 runs key): planned-truncation runs = telling 32 + 8 decisions x 16 = 160;
# unplanned runs = telling 8x2x32 - 32 = 480 plus decisions 8x8x2x16 - 128 = 1920 -> 2400.
# Calibration per rung: planned 2 sets x 256 = 512 clean; unplanned 2 sets x 256 x 15 = 7680 clean.
from math import comb
def pvoid(p): return sum(comb(15, k) * p**k * (1 - p)**(15 - k) for k in range(3, 16))
def pseed(q_miss, q_un): return 1 - (1 - q_miss)**160 * (1 - q_un)**2400
print("95% upper bounds from 0 breaches: planned", round(1 - 0.05**(1/512), 5), " unplanned", round(1 - 0.05**(1/7680), 6))
for q_miss, q_un in [(0, 0), (1e-4, 0), (3e-4, 0), (1e-3, 0), (5.8e-3, 0), (0, 1e-5), (0, 3e-5), (0, 1e-4), (0, 3.9e-4)]:
    p = pseed(q_miss, q_un)
    print(f"q_miss={q_miss:<8g} q_unplanned={q_un:<8g} p_seed={p:.4f}  P(VOID)={pvoid(p):.4f}")
lo, hi = 0.0, 0.01
for _ in range(60):
    mid = (lo + hi) / 2
    (lo, hi) = (mid, hi) if pvoid(pseed(mid, 0)) < 0.05 else (lo, mid)
print("q_miss giving P(VOID)=0.05:", f"{lo:.2e}")
lo, hi = 0.0, 0.01
for _ in range(60):
    mid = (lo + hi) / 2
    (lo, hi) = (mid, hi) if pvoid(pseed(0, mid)) < 0.05 else (lo, mid)
print("q_unplanned giving P(VOID)=0.05:", f"{lo:.2e}")

"""P1 pass and breach-VOID probabilities from the reduced model's per-run miss rates (run_big-out.txt).
A1 planned-truncation runs per seed: telling 32 + 8 decisions x 16 = 160 (redteam-s18 breach.py).
B per seed: telling truth on e2 32 + FSD reference on e2 128 (rung truth) + on e1 128 (T_A theta+=0.5).
Calibration planned runs per rung: 2 sets x 256 = 512 (B also the 256-run reference check on T_A)."""
from math import comb, exp
def pvoid(p): return sum(comb(15, k) * p**k * (1 - p)**(15 - k) for k in range(3, 16))
q = {"1.10": 15 / 200000, "1.05": 526 / 50000, "1.15": 3 / 200000}  # 1.15: 0/200000, rule-of-three bound
for name, qq in q.items():
    print(f"m={name} q={qq:.2e}: A1 P1 pass {(1-qq)**512:.4f}; A1 P(VOID|tested) {pvoid(1-(1-qq)**160):.2e}")
qa = q["1.10"]
for name, qq in q.items():
    p_seed = 1 - (1 - qq)**160 * (1 - qa)**128
    print(f"B rung m={name}: P1 pass (512 rung + 256 ref) {(1-qq)**512*(1-qa)**256:.4f}; P(VOID|tested) {pvoid(p_seed):.2e}")
print("95% bound from 0/512 clean:", f"{1-0.05**(1/512):.2e}", "; from 0/200000:", f"{3/200000:.1e}")

"""Reduced model of a planned-truncation run's miss rate (theorist, v8; NOT the simulator).
Linear Hawkes, mu=0.15, kernel m*exp(-t) (exp_rate 1). Window [800,1200) of horizon 2000: every
generated event is sign=+, so m_in = theta_all + theta_+ ; outside, sign=+ w.p. 1/2 (mean m = 0.85).
Cap 15,000 events. Exact event-by-event simulation (vectorised), stopped early once E >= E_HI inside
the window, then a deterministic projection; runs whose projection is within x0.5..x2 of the need
are counted 'borderline' and reported (a miss is counted only if the projection is < 1x).
Seeded: numpy default_rng(20261029 + rung index)."""
from __future__ import annotations
import sys, time
import numpy as np
MU, CAP, W0, W1, HOR, E_HI = 0.15, 15000, 800.0, 1200.0, 2000.0, 150.0
def sim(tp: float, n: int, seed: int) -> dict[str, float]:
    rng = np.random.default_rng(seed)
    ta = 0.85 - tp / 2  # theta_all; gated kernel theta_+
    m_in = ta + tp
    t = np.zeros(n); E = np.zeros(n); N = np.zeros(n)
    res = np.full(n, -1)  # -1 active, 0 miss, 1 trunc, 2 borderline-trunc, 3 borderline-miss
    taus = np.full(n, np.nan)
    act = np.arange(n)
    while act.size:
        tt, ee = t[act], E[act]
        x_imm = rng.exponential(1.0 / MU, act.size)
        u = rng.random(act.size)
        with np.errstate(divide="ignore", invalid="ignore"):
            arg = 1.0 + np.log(u) / np.where(ee > 0, ee, np.nan)
            x_exc = np.where((ee > 0) & (arg > 0), -np.log(arg), np.inf)
        x = np.minimum(x_imm, x_exc)
        tn = tt + x
        done_h = tn >= HOR
        res[act[done_h]] = 0
        k = act[~done_h]; tn = tn[~done_h]; ee = ee[~done_h]; x = x[~done_h]
        inwin = (tn >= W0) & (tn < W1)
        plus = inwin | (rng.random(k.size) < 0.5)
        ee = ee * np.exp(-x) + np.where(plus, ta + tp, ta)
        t[k] = tn; E[k] = ee; N[k] += 1
        hit = N[k] >= CAP
        res[k[hit]] = 1; taus[k[hit]] = tn[hit]
        # early projection inside the window
        big = (~hit) & inwin & (ee >= E_HI)
        if big.any():
            kb, tb, eb = k[big], tn[big], ee[big]
            r = m_in - 1.0
            g = np.exp(r * (W1 - tb))
            add = eb * (g - 1.0) / r + (eb * g) / 0.15
            ratio = (N[kb] + add) / CAP
            res[kb] = np.where(ratio >= 2, 1, np.where(ratio >= 1, 2, np.where(ratio >= 0.5, 3, 0)))
            # approx tau for projected runs: time to reach CAP if before W1
            need = np.maximum(CAP - N[kb], 1.0)
            with np.errstate(invalid="ignore"):
                taus[kb] = tb + np.log1p(need * r / eb) / r
        # after the window, give up when even the expected remainder cannot reach the cap
        late = (~hit) & (~big) & (tn >= W1) & (N[k] + ee / 0.15 + 50 < CAP * 0.5)
        res[k[late]] = 0
        act = k[(res[k] == -1)]
    out = {f"n": n, "miss": int((res == 0).sum()), "border_miss": int((res == 3).sum()),
           "border_trunc": int((res == 2).sum()), "trunc": int((res == 1).sum())}
    tv = taus[np.isfinite(taus)]
    out["tau_q"] = tuple(np.round(np.quantile(tv, [0.0, 0.5, 0.99, 1.0])).tolist()) if tv.size else ()
    return out
if __name__ == "__main__":
    n = int(sys.argv[1])
    for i, tp in enumerate((0.5, 0.4, 0.6)):
        c0 = time.process_time()
        o = sim(tp, n, 20261029 + i)
        q = (o["miss"] + o["border_miss"]) / n
        print(f"theta+={tp} m_in={0.85 + tp/2:.2f}: {o}  miss-rate est {q:.2e}  cpu {time.process_time()-c0:.1f}s", flush=True)

import sys, time
sys.path.insert(0, "/tmp/fsd-v8-theory")
from breach_model import sim
for i, (tp, n) in enumerate(((0.5, 200000), (0.4, 50000), (0.6, 200000))):
    c0 = time.process_time(); o = sim(tp, n, 777 + i)
    print(f"theta+={tp} n={n}: miss {o['miss']} border_miss {o['border_miss']} border_trunc {o['border_trunc']} tau q(0,.5,.99,1) {o['tau_q']} cpu {time.process_time()-c0:.0f}s", flush=True)

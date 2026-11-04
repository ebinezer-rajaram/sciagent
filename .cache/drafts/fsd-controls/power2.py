"""Power arithmetic for criteria.md v2 (red-team fixes 1, 4, 5). No simulation.

- The random arm's picks are fixed by the seed alone (policy_seed(seed, k, RANDOM_INDEX, 0),
  policies.py:968-972, no truth id): computed here for every seed the controls may use.
- (iii)/B3 power: exact, by dynamic programming over seeds, given those fixed picks.
- (ii) power: exact under clustering (one telling label per seed, 8 decisions given it).
- A2 exposure arithmetic.
The functions pass_iii / pass_ii are the frozen formulas the calibration phase evaluates.

Run (worktree interpreter, for policy_seed):
  WT=/home/ubuntu/lanes/sciagent/.claude/worktrees/fsd-dist
  nice -n 10 uv run --project $WT --no-sync python -I power2.py > power2-out.txt
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from fractions import Fraction
from math import ceil, comb
from pathlib import Path

from sciagent.systems.v2.policies import RANDOM_INDEX

from sciagent.scoring.seeds import generator, policy_seed

N_DESIGNS, N_DIAG, DECISIONS = 8, 35, 8


def _menu_labels() -> list[str]:
    """The 35 menu labels from diagnostic_menu itself (labels do not depend on the log)."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from probe import CH, gated

    from sciagent.investigation.world import OBSERVATIONAL, World
    from sciagent.systems.v2.policies import diagnostic_menu

    log = World(gated(0.5, 0.6, 0.15), 0, truncate=True).datasets[OBSERVATIONAL].log
    return [c.label for c in diagnostic_menu(log, CH)]


LABELS: list[str] = _menu_labels()
SIGN = [lab for lab in LABELS if "sign" in lab]
SIZE = [lab for lab in LABELS if "size" in lab]


def random_picks(seed: int) -> list[tuple[int, str]]:
    """RandomPolicy.choose's (design, label) for decisions 0..7 (policies.py:968-972)."""
    out = []
    for k in range(DECISIONS):
        rng = generator(policy_seed(seed, k, RANDOM_INDEX, 0))
        e, d = divmod(int(rng.integers(N_DESIGNS * N_DIAG)), N_DIAG)
        out.append((e, LABELS[d]))
    return out


def mcnemar_p(b: int, c: int) -> Fraction:
    n = b + c
    return Fraction(sum(comb(n, i) for i in range(b, n + 1)), 2**n)


def pass_iii(
    seeds: Sequence[int],
    p32: Mapping[str, float],
    h_of: Mapping[str, float],
    alpha: float = 0.05,
) -> float:
    """P(policy_separation passes): per seed, telling's label ~ p32, the arm's ever-hit
    given the label is h_of[label], random's hit is fixed by the seed's picks."""
    dist: dict[tuple[int, int], float] = {(0, 0): 1.0}
    for s in seeds:
        labs = {lab for _, lab in random_picks(s)}
        p_b = sum(p * h_of[lab] for lab, p in p32.items() if lab not in labs)
        p_c = sum(p * (1 - h_of[lab]) for lab, p in p32.items() if lab in labs)
        nxt: dict[tuple[int, int], float] = {}
        for (b, c), w in dist.items():
            for db, dc, q in ((1, 0, p_b), (0, 1, p_c), (0, 0, 1 - p_b - p_c)):
                if q > 0:
                    key = (b + db, c + dc)
                    nxt[key] = nxt.get(key, 0.0) + w * q
        dist = nxt
    return sum(w for (b, c), w in dist.items() if mcnemar_p(b, c) < alpha)


def pass_ii(
    n_seeds: int, p32: Mapping[str, float], p16: Mapping[str, float], share: float = 0.7
) -> float:
    """P(sum over seeds of Bin(8, p16[label_s]) >= share * 8 * n), label_s ~ p32 (clustered)."""
    per = [0.0] * (DECISIONS + 1)
    for lab, p in p32.items():
        q = p16.get(lab, 0.0)
        for j in range(DECISIONS + 1):
            per[j] += p * comb(DECISIONS, j) * q**j * (1 - q) ** (DECISIONS - j)
    dist = [1.0]
    for _ in range(n_seeds):
        new = [0.0] * (len(dist) + DECISIONS)
        for i, w in enumerate(dist):
            for j, q in enumerate(per):
                new[i + j] += w * q
        dist = new
    need = ceil(share * DECISIONS * n_seeds)
    return sum(dist[need:])


def binom_ge(n: int, k: int, p: float) -> float:
    return sum(comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k, n + 1))


def ever(p: float) -> float:
    return 1 - (1 - p) ** DECISIONS


def main() -> None:
    print(f"{len(LABELS)} menu labels; [sign] labels: {SIGN}")
    for name, lo in (("A1", 101), ("B", 301)):
        print(
            f"\n== random arm's fixed picks, {name} seeds {lo}-{lo + 29} (label hits per seed) =="
        )
        for lab in [*SIGN, "mean_rate"]:
            hits = [
                s for s in range(lo, lo + 30) if lab in {x for _, x in random_picks(s)}
            ]
            first10 = [s for s in hits if s < lo + 10]
            print(f"  {lab:36s} seeds<{lo + 10}: {first10}  all 30: {len(hits)}")
    print(
        "\n== (iii)/B3 power given the fixed picks; one telling label for all seeds; worst over [sign] =="
    )
    for name, lo in (("A1", 101), ("B", 301)):
        for n in (10, 15, 20, 25, 30):
            seeds = list(range(lo, lo + n))
            row = []
            for h in (0.99, 0.95, 0.9):
                worst = min(
                    (pass_iii(seeds, {lab: 1.0}, {lab: h}), lab) for lab in SIGN
                )
                row.append(f"h={h}: {worst[0]:.3f} ({worst[1]})")
            print(f"  {name} N={n:2d}: " + "; ".join(row))
    print(
        "\n== B3 power at N=15 if D*0 carries [size] (size-gated rung): worst over [size] =="
    )
    seeds = list(range(301, 316))
    for h in (0.99, 0.95):
        worst = min((pass_iii(seeds, {lab: 1.0}, {lab: h}), lab) for lab in SIZE)
        print(f"  B N=15 h={h}: {worst[0]:.3f} ({worst[1]})")
    print("  random hits on [size] labels, seeds 301-315:")
    for lab in SIZE:
        print(
            f"    {lab:36s} {[s for s in seeds if lab in {x for _, x in random_picks(s)}]}"
        )
    print("\n== (ii) clustered power, threshold 0.7 of 8N pairs ==")
    cases = {
        "p32 .95/.05, p16 .84/.12": ({"x": 0.95, "y": 0.05}, {"x": 0.84, "y": 0.12}),
        "p32 .8/.2, p16 1/0": ({"x": 0.8, "y": 0.2}, {"x": 1.0, "y": 0.0}),
        "p32 1, p16 .8": ({"x": 1.0}, {"x": 0.8}),
        "p32 .95/.05, p16 .9/.1": ({"x": 0.95, "y": 0.05}, {"x": 0.9, "y": 0.1}),
    }
    for n in (10, 20):
        for k, (a, b) in cases.items():
            print(f"  N={n} {k}: P(ii passes) = {pass_ii(n, a, b):.3f}")
    print("\n== (i) binomial: P(>= ceil(0.8 N) seeds in D*0 | p) ==")
    for n in (10, 20):
        print(
            f"  N={n}: "
            + "; ".join(
                f"p={p}: {binom_ge(n, ceil(0.8 * n), p):.3f}" for p in (0.95, 0.9)
            )
        )
    print("\n== per-unit ever-hit over 8 decisions from a per-decision match p ==")
    print("  " + "; ".join(f"p={p}: {ever(p):.4f}" for p in (0.2, 0.3, 0.32, 0.4, 0.5)))
    print("\n== A2 exposure (telling R=32 decides X; truthFSD R=16 per decision) ==")
    for pi in (0.01, 0.02, 0.03, 0.04):
        keep = (1 - pi) ** 32
        cap16 = 1 - (1 - pi) ** 16
        k5 = keep * cap16  # telling keeps e1, truthFSD's 16 truncate: K5 keeps them (c)
        k4 = (1 - keep) * (
            1 - cap16
        )  # telling excludes e1, truthFSD sees none: K4 keeps e1
        n = 20
        void = sum(
            comb(n, j) * keep**j * (1 - keep) ** (n - j)
            for j in range(n + 1)
            if j < 5 or n - j < 5
        )
        print(
            f"  pi={pi:.2f}: P(telling keeps e1)={keep:.3f}; per pair: K5 kept-capped {k5:.3f}, "
            f"K4 kept-uncapped {k4:.3f}, sets differ (K4) {k4 + k5:.3f}; "
            f"N=20 P(fewer than 5 seeds on a side) = {void:.4f}"
        )


if __name__ == "__main__":
    main()

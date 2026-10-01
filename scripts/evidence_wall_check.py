"""Check the evidence-wall bounds of ``docs/v2/evidence-wall.md`` numerically.

The note proves, for an entertained set with weights ``w`` and per-hypothesis
predictives ``p_k`` of one design's outcome, that

* ``EIG = sum_k w_k KL(p_k || p_w) = H(Y) - sum_k w_k H(p_k)``  (identity);
* ``EIG <= sum_{k,l} w_k w_l KL(p_k || p_l)``                  (Prop. 1a);
* ``EIG <= sum_k w_k log(1/w_k) TV(p_k, p_w) / (1 - w_k)
       <= eps * H(w) <= H(w)``, ``eps = max_{k,l} TV(p_k, p_l)`` (Prop. 1b);
* the erasure channel attains ``EIG = eps * H(w)`` exactly       (tightness);
* the ratio ``EIG / sum w_k w_l KL`` approaches 1                (tightness);
* the size-gap correlation of iid marks against *any* fixed gaps has
  permutation mean 0 and variance ``1/(n-1)`` exactly           (Lemma 2).

None of this proves anything: the proofs are in the note. The script exists so
that a sign error or a dropped factor in the note shows up as a failure. It also
prints the v1 illustration (§3 of the note) from the moments recorded in
``docs/v1/DECISIONS.md`` (2026-08-16), which are typed in below as constants.

All logarithms are natural (nats). All randomness comes from one seeded
generator. Usage::

    uv run python scripts/evidence_wall_check.py
"""

from __future__ import annotations

import itertools
import math

import numpy as np
from numpy.typing import NDArray
from scipy.stats import norm

from sciagent.core import reductions
from sciagent.core.errors import SciAgentError

FloatArray = NDArray[np.float64]

SEED = 20261001
TOL = 1e-10


class BoundViolationError(SciAgentError):
    """An inequality the note proves failed numerically."""


def check(condition: bool, message: str) -> None:
    """Raise :class:`BoundViolationError` with ``message`` unless ``condition``."""
    if not condition:
        raise BoundViolationError(message)


# --------------------------------------------------------------------------
# Discrete quantities
# --------------------------------------------------------------------------


def entropy(p: FloatArray) -> float:
    """Shannon entropy in nats, with ``0 log 0 = 0``."""
    q = p[p > 0.0]
    return -reductions.total(q * np.log(q))


def kl(p: FloatArray, q: FloatArray) -> float:
    """KL(p || q) in nats; ``inf`` when p is not absolutely continuous wrt q."""
    support = p > 0.0
    if np.any(q[support] <= 0.0):
        return math.inf
    return reductions.total(p[support] * np.log(p[support] / q[support]))


def tv(p: FloatArray, q: FloatArray) -> float:
    """Total variation distance ``sup_A |p(A) - q(A)| = 0.5 * ||p - q||_1``."""
    return 0.5 * reductions.total(np.abs(p - q))


def eig(w: FloatArray, rows: FloatArray) -> float:
    """``sum_k w_k KL(p_k || p_w)``: the mutual information I(Y; H)."""
    mixture = reductions.matvec(rows.T, w)
    return math.fsum(float(w[k]) * kl(rows[k], mixture) for k in range(w.size))


def pairwise_kl_bound(w: FloatArray, rows: FloatArray) -> float:
    """Prop. 1a: ``sum_{k,l} w_k w_l KL(p_k || p_l)``."""
    size = w.size
    return math.fsum(
        float(w[k] * w[j]) * kl(rows[k], rows[j])
        for k in range(size)
        for j in range(size)
        if k != j
    )


def refined_tv_bound(w: FloatArray, rows: FloatArray) -> float:
    """Prop. 1b, first form: ``sum_k w_k log(1/w_k) TV(p_k,p_w) / (1-w_k)``."""
    mixture = reductions.matvec(rows.T, w)
    return math.fsum(
        float(w[k])
        * math.log(1.0 / float(w[k]))
        * tv(rows[k], mixture)
        / (1.0 - float(w[k]))
        for k in range(w.size)
    )


def max_tv(rows: FloatArray) -> float:
    """``eps = max_{k,l} TV(p_k, p_l)``."""
    return max(
        tv(rows[k], rows[j]) for k, j in itertools.combinations(range(rows.shape[0]), 2)
    )


def random_case(rng: np.random.Generator) -> tuple[FloatArray, FloatArray]:
    """Draw weights and predictives, half generic and half near-invariant."""
    k = int(rng.integers(2, 7))
    c = int(rng.integers(2, 9))
    w = rng.dirichlet(np.full(k, 0.7))
    w = np.maximum(w, 1e-12)
    w = w / reductions.total(w)
    if rng.random() < 0.5:
        rows = rng.dirichlet(np.ones(c), size=k)
    else:
        base = rng.dirichlet(np.ones(c))
        eta = 10.0 ** rng.uniform(-5.0, 0.0)
        rows = (1.0 - eta) * base + eta * rng.dirichlet(np.ones(c), size=k)
    return w, rows


def check_random_discrete(rng: np.random.Generator, trials: int) -> None:
    """Assert the identity and every bound on random discrete cases."""
    worst_kl_ratio = 0.0
    worst_tv_ratio = 0.0
    for _ in range(trials):
        w, rows = random_case(rng)
        value = eig(w, rows)
        mixture = reductions.matvec(rows.T, w)
        via_entropy = entropy(mixture) - math.fsum(
            float(w[k]) * entropy(rows[k]) for k in range(w.size)
        )
        check(abs(value - via_entropy) < TOL, f"identity: {value} vs {via_entropy}")
        check(value >= -TOL, f"negative EIG {value}")
        kl_bound = pairwise_kl_bound(w, rows)
        refined = refined_tv_bound(w, rows)
        eps = max_tv(rows)
        hw = entropy(w)
        check(value <= kl_bound + TOL, f"1a: {value} > {kl_bound}")
        check(value <= refined + TOL, f"1b refined: {value} > {refined}")
        check(refined <= eps * hw + TOL, f"1b: {refined} > {eps} * {hw}")
        check(eps * hw <= hw + TOL, "eps > 1")
        if kl_bound > 0.0:
            worst_kl_ratio = max(worst_kl_ratio, value / kl_bound)
        if eps * hw > 0.0:
            worst_tv_ratio = max(worst_tv_ratio, value / (eps * hw))
    print(f"random discrete cases: {trials} passed")
    print(f"  max EIG / pairwise-KL bound seen: {worst_kl_ratio:.4f}")
    print(f"  max EIG / (eps H(w)) seen:        {worst_tv_ratio:.4f}")


def check_erasure(rng: np.random.Generator) -> None:
    """The erasure channel attains ``EIG = eps H(w)`` exactly."""
    k = 4
    w = rng.dirichlet(np.ones(k))
    print("erasure channel (K=4): EIG vs eps*H(w)")
    for eps in (0.01, 0.1, 0.5, 1.0):
        rows = np.zeros((k, k + 1))
        rows[np.arange(k), np.arange(k)] = eps
        rows[:, k] = 1.0 - eps
        value = eig(w, rows)
        target = eps * entropy(w)
        check(abs(max_tv(rows) - eps) < TOL, "erasure TV")
        check(abs(value - target) < TOL, f"erasure: {value} vs {target}")
        print(f"  eps={eps:<5} EIG={value:.6f}  eps*H(w)={target:.6f}")


def check_kl_ratio_supremum() -> None:
    """``EIG / pairwise-KL`` approaches 1: the constant in 1a cannot be lowered.

    With ``w = (1-d, d)``, ``p1 = Bern(e)``, ``p2 = Bern(1/2)``, the ratio tends
    to ``KL(p2||p1) / (KL(p2||p1) + KL(p1||p2))`` as ``d -> 0``; that limit tends
    to 1 as ``e -> 0`` because ``KL(p1||p2) -> log 2`` while ``KL(p2||p1) -> inf``.
    The approach is logarithmic in ``1/e``.
    """
    print("pairwise-KL sharpness: w=(1-d, d), p1=Bern(e), p2=Bern(1/2)")
    for e in (1e-2, 1e-4, 1e-8):
        rows = np.array([[1.0 - e, e], [0.5, 0.5]])
        kl21 = kl(rows[1], rows[0])
        limit = kl21 / (kl21 + kl(rows[0], rows[1]))
        d = e * 1e-5
        w = np.array([1.0 - d, d])
        ratio = eig(w, rows) / pairwise_kl_bound(w, rows)
        check(abs(ratio - limit) < 1e-3, f"ratio {ratio} vs limit {limit}")
        print(f"  e={e:.0e} d={d:.0e}  EIG/bound={ratio:.4f}  d->0 limit={limit:.4f}")
    limits = []
    for log10_e in (-2, -8, -32, -128):
        kl21 = -0.5 * math.log(4.0) - 0.5 * log10_e * math.log(10.0)
        limits.append(kl21 / (kl21 + math.log(2.0)))
        print(f"  e=1e{log10_e}: d->0 limit ~ {limits[-1]:.4f}")
    check(limits == sorted(limits) and limits[-1] > 0.99, "limit does not reach 1")


# --------------------------------------------------------------------------
# Gaussian quantities, by quadrature on a fine grid
# --------------------------------------------------------------------------


def gaussian_rows(
    means: FloatArray, sds: FloatArray, points: int = 400_001
) -> tuple[FloatArray, FloatArray, float]:
    """Return a grid, log densities on it, and the grid step."""
    low = float(np.min(means - 14.0 * sds))
    high = float(np.max(means + 14.0 * sds))
    grid = np.linspace(low, high, points)
    log_rows = np.stack(
        [norm.logpdf(grid, loc=m, scale=s) for m, s in zip(means, sds, strict=True)]
    )
    return grid, log_rows, float(grid[1] - grid[0])


def log_mixture(log_rows: FloatArray, w: FloatArray) -> FloatArray:
    """``log sum_k w_k exp(log_rows[k])`` per grid point, shifted and summed exactly."""
    peak = np.max(log_rows, axis=0)
    scaled = w[:, None] * np.exp(log_rows - peak)
    return peak + np.log(reductions.row_totals(np.ascontiguousarray(scaled.T)))


def integrate(values: FloatArray, step: float) -> float:
    """Riemann sum on the uniform grid; the tails at 14 sd are below 1e-40."""
    return reductions.total(values) * step


def gaussian_eig(w: FloatArray, means: FloatArray, sds: FloatArray) -> float:
    """``sum_k w_k KL(N_k || mixture)`` by quadrature on a fine grid."""
    _, log_rows, step = gaussian_rows(means, sds)
    log_mix = log_mixture(log_rows, w)
    total = 0.0
    for k in range(w.size):
        integrand = np.exp(log_rows[k]) * (log_rows[k] - log_mix)
        total += float(w[k]) * integrate(integrand, step)
    return total


def gaussian_kl(m1: float, s1: float, m2: float, s2: float) -> float:
    """Closed-form KL(N(m1, s1^2) || N(m2, s2^2))."""
    return math.log(s2 / s1) + (s1 * s1 + (m1 - m2) ** 2) / (2.0 * s2 * s2) - 0.5


def gaussian_tv(m1: float, s1: float, m2: float, s2: float) -> float:
    """TV between two Gaussians by quadrature."""
    means = np.array([m1, m2])
    sds = np.array([s1, s2])
    _, log_rows, step = gaussian_rows(means, sds)
    diff = np.abs(np.exp(log_rows[0]) - np.exp(log_rows[1]))
    return 0.5 * integrate(diff, step)


def gaussian_hellinger2(m1: float, s1: float, m2: float, s2: float) -> float:
    """Closed-form squared Hellinger ``1 - int sqrt(pq)`` between Gaussians."""
    var = s1 * s1 + s2 * s2
    return 1.0 - math.sqrt(2.0 * s1 * s2 / var) * math.exp(
        -((m1 - m2) ** 2) / (4.0 * var)
    )


def gaussian_bounds(
    w: FloatArray, means: FloatArray, sds: FloatArray
) -> tuple[float, float, float, float]:
    """Return EIG, pairwise-KL bound, eps and eps*H(w) for a Gaussian family."""
    size = w.size
    value = gaussian_eig(w, means, sds)
    kl_bound = math.fsum(
        float(w[k] * w[j])
        * gaussian_kl(float(means[k]), float(sds[k]), float(means[j]), float(sds[j]))
        for k in range(size)
        for j in range(size)
        if k != j
    )
    eps = max(
        gaussian_tv(float(means[k]), float(sds[k]), float(means[j]), float(sds[j]))
        for k, j in itertools.combinations(range(size), 2)
    )
    return value, kl_bound, eps, eps * entropy(w)


def check_gaussian() -> None:
    """Equal-variance shift family: bounds hold; EIG/KL-bound -> 1/2 locally."""
    print("Gaussian shift family, K=3, means (0, d, 2d), sd 1, w uniform")
    w = np.full(3, 1.0 / 3.0)
    for d in (0.01, 0.1, 0.5, 1.0, 3.0):
        means = np.array([0.0, d, 2.0 * d])
        sds = np.ones(3)
        value, kl_bound, eps, tv_bound = gaussian_bounds(w, means, sds)
        check(value <= kl_bound + 1e-8, f"gauss 1a: {value} > {kl_bound}")
        check(value <= tv_bound + 1e-8, f"gauss 1b: {value} > {tv_bound}")
        print(
            f"  d={d:<5} EIG={value:.3e} KLbound={kl_bound:.3e} "
            f"eps={eps:.3e} epsH={tv_bound:.3e} EIG/KL={value / kl_bound:.3f}"
        )


# --------------------------------------------------------------------------
# Lemma 2: the permutation law of the size-gap correlation
# --------------------------------------------------------------------------


def pearson(a: FloatArray, b: FloatArray) -> float:
    """Sample Pearson correlation."""
    ac = a - reductions.mean(a)
    bc = b - reductions.mean(b)
    return reductions.dot(ac, bc) / math.sqrt(
        reductions.dot(ac, ac) * reductions.dot(bc, bc)
    )


def check_permutation_moments(rng: np.random.Generator) -> None:
    """Exact enumeration: mean 0 and variance 1/(n-1) for any fixed gaps."""
    n = 7
    for label, gaps in (
        ("heavy-tailed gaps", rng.pareto(1.5, size=n) + 1e-3),
        ("clustered gaps", np.concatenate([rng.exponential(0.01, 4), [5.0, 9.0, 0.2]])),
    ):
        marks = rng.lognormal(0.0, 1.0, size=n)
        values = np.array(
            [
                pearson(marks[list(order)], gaps)
                for order in itertools.permutations(range(n))
            ]
        )
        mean = reductions.mean(values)
        var = reductions.mean(values**2) - mean**2
        check(abs(mean) < 1e-12, f"permutation mean {mean}")
        check(abs(var - 1.0 / (n - 1)) < 1e-12, f"permutation var {var}")
        print(
            f"  {label}: mean={mean:+.1e}  var={var:.12f}  1/(n-1)={1.0 / (n - 1):.12f}"
        )


# --------------------------------------------------------------------------
# The v1 illustration (moments from docs/v1/DECISIONS.md, 2026-08-16)
# --------------------------------------------------------------------------

#: size_gap_correlation, 200 replicates of 512 events per structure.
V1_CLOSED_SET: tuple[tuple[str, float, float], ...] = (
    ("null", 0.0001, 0.0445),
    ("hawkes", -0.0012, 0.0456),
    ("poisson_mixture", -0.0012, 0.0444),
    ("regime_switching", -0.0020, 0.0392),
    ("seasonality", 0.0006, 0.0462),
)
V1_TRUTH: tuple[str, float, float] = ("size_excitation (S11)", -0.1342, 0.0308)


def v1_illustration() -> None:
    """EIG over the closed set vs the truth's divergence from the mixture."""
    names = [row[0] for row in V1_CLOSED_SET]
    means = np.array([row[1] for row in V1_CLOSED_SET])
    sds = np.array([row[2] for row in V1_CLOSED_SET])
    w = np.full(len(names), 1.0 / len(names))
    value, kl_bound, eps, tv_bound = gaussian_bounds(w, means, sds)
    theory_sd = 1.0 / math.sqrt(511 - 1)
    same = np.full(len(names), theory_sd)
    exact_value = gaussian_eig(w, np.zeros(len(names)), same)

    _, log_rows, step = gaussian_rows(
        np.append(means, V1_TRUTH[1]), np.append(sds, V1_TRUTH[2])
    )
    log_mix = log_mixture(log_rows[:-1], w)
    truth_kl = float(integrate(np.exp(log_rows[-1]) * (log_rows[-1] - log_mix), step))
    w6 = np.full(6, 1.0 / 6.0)
    with_truth = gaussian_eig(
        w6, np.append(means, V1_TRUTH[1]), np.append(sds, V1_TRUTH[2])
    )
    check(value <= kl_bound + 1e-8 and value <= tv_bound + 1e-8, "v1 bounds")
    print("v1 illustration (Gaussian approximation to recorded moments, nats):")
    print(f"  EIG over closed set, recorded moments:     {value:.4f}")
    print(
        f"    pairwise-KL bound {kl_bound:.4f}; eps={eps:.4f}; eps*H(w)={tv_bound:.4f}"
    )
    print(f"    H(w) = log 5 = {math.log(5.0):.4f}")
    print(f"  EIG with every sd = 1/sqrt(510) (Lemma 2): {exact_value:.2e}")
    print(f"  KL(p_truth || p_w), one observation:       {truth_kl:.4f}")
    print(f"  EIG if S11 were entertained (w = 1/6):     {with_truth:.4f}")
    print(f"    (H(w) = log 6 = {math.log(6.0):.4f})")


def check_fsd_triangle() -> None:
    """Hellinger triangle inequality behind Prop. 3, on a Gaussian example."""
    par = (0.0, 0.0445)
    truth = (-0.1342, 0.0308)
    print("FSD (Hellinger) as the model-free estimate converges to the truth:")
    for shrink in (1.0, 0.5, 0.1, 0.0):
        np_est = (truth[0] * (1.0 - shrink), truth[1] + shrink * 0.01)
        h_par_np = math.sqrt(gaussian_hellinger2(*par, *np_est))
        h_par_truth = math.sqrt(gaussian_hellinger2(*par, *truth))
        h_np_truth = math.sqrt(gaussian_hellinger2(*np_est, *truth))
        check(abs(h_par_np - h_par_truth) <= h_np_truth + TOL, "Hellinger triangle")
        print(
            f"  H(np,truth)={h_np_truth:.4f}  FSD=H^2(par,np)={h_par_np**2:.4f}  "
            f"H^2(par,truth)={h_par_truth**2:.4f}"
        )


def main() -> None:
    """Run every check; raise on the first violated inequality."""
    rng = np.random.default_rng(SEED)
    check_random_discrete(rng, trials=5000)
    check_erasure(rng)
    check_kl_ratio_supremum()
    check_gaussian()
    print("Lemma 2, exact enumeration over all 7! pairings:")
    check_permutation_moments(rng)
    v1_illustration()
    check_fsd_triangle()
    print("all checks passed")


if __name__ == "__main__":
    main()

"""Child process for acceptance test A13's cross-process arm.

Prints one ``name digest`` line per experiment key. Deliberately a separate
process: a content address built on :func:`hash` would agree with itself within
one interpreter and disagree across two, and A13's whole purpose is that the
same tuple addresses the same row tomorrow, on another machine, in another run.

Not named ``test_*`` so pytest does not collect it.
"""

from __future__ import annotations

import sys

from sciagent.core.types import DataVersion, EnvVersion, FrozenDict, MetricVersion, Seed
from sciagent.registry.store import ExperimentKey

CASES: dict[str, ExperimentKey] = {
    "bare": ExperimentKey(
        env_version=EnvVersion("pointproc/1.0.0"),
        config=FrozenDict[str, str]({}),
        data_version=DataVersion("slice/1.0.0"),
        metric_version=MetricVersion("metrics/1.0.0"),
        seed=Seed(0),
    ),
    "populated": ExperimentKey(
        env_version=EnvVersion("pointproc/1.0.0"),
        config=FrozenDict[str, str](
            {"mechanism": "hawkes", "metric": "fano_factor_w2", "n_events": "256"}
        ),
        data_version=DataVersion("slice/1.0.0"),
        metric_version=MetricVersion("metrics/1.0.0"),
        seed=Seed(20240801),
    ),
    "reordered": ExperimentKey(
        env_version=EnvVersion("pointproc/1.0.0"),
        config=FrozenDict[str, str](
            {"n_events": "256", "metric": "fano_factor_w2", "mechanism": "hawkes"}
        ),
        data_version=DataVersion("slice/1.0.0"),
        metric_version=MetricVersion("metrics/1.0.0"),
        seed=Seed(20240801),
    ),
}


def main() -> int:
    for name in sorted(CASES):
        sys.stdout.write(f"{name} {CASES[name].digest}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

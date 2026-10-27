"""Store builders for the defect-hunt check tests: cells written through
:meth:`ResultStore.put` with the driver's layout (``unit``, ``result``,
``system_config`` / ``session_config``, ``session.record_dir``)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from sciagent.campaign.store import ResultStore
from sciagent.core.types import DataVersion, EnvVersion, FrozenDict, MetricVersion, Seed
from sciagent.registry.store import ExperimentKey

FIT5 = "pointproc|sciagent.glm.fit/5"
FIT4 = "pointproc|sciagent.glm.fit/4"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_key(
    kind: str,
    system: str,
    truth: str,
    seed: int,
    fields: dict[str, str],
    *,
    env: str = FIT5,
    fit_config: str = "fc",
    metric: str = "score/2",
) -> ExperimentKey:
    return ExperimentKey(
        env_version=EnvVersion(env),
        config=FrozenDict[str, str](
            {
                "fit_config": fit_config,
                "kind": kind,
                "system": system,
                "truth": truth,
                **fields,
            }
        ),
        data_version=DataVersion("data"),
        metric_version=MetricVersion(metric),
        seed=Seed(seed),
    )


def unit_json(kind: str, system: str, truth: str, seed: int) -> dict[str, Any]:
    return {"truth": truth, "seed": seed, "system": system, "kind": kind}


def put_baseline(
    store: ResultStore,
    system: str,
    per_event: float | None,
    *,
    truth: str = "dev-008",
    seed: int = 1,
    underfit: bool | None = False,
    env: str = FIT5,
    fit_config: str = "fc",
    text: str | None = None,
    variant: str = "",
) -> ExperimentKey:
    """One baseline cell; ``per_event=None`` writes a failed unit. ``text``
    replaces the stored config text (not the key's hash of it)."""
    config_text = f"config of {system}{variant}"
    key = make_key(
        "baseline",
        system,
        truth,
        seed,
        {"truth_digest": sha(truth), "system_config": sha(config_text)},
        env=env,
        fit_config=fit_config,
    )
    if per_event is None:
        reading = {"status": 0.0}
        result: dict[str, Any] = {"error": "SearchError", "message": "boom"}
    else:
        reading = {
            "status": 1.0,
            "per_event": per_event,
            "exact": 0.0,
            "has_structure": 1.0,
            "fits_used": 1.0,
        }
        result = {"structure": "Base()", "curve": []}
        if system == "ORACLE" and underfit is not None:
            result |= {"oracle_underfit": underfit, "oracle_psi_source": "search"}
    store.put(
        key,
        reading,
        {
            "unit": unit_json("baseline", system, truth, seed),
            "result": result,
            "system_config": config_text if text is None else text,
        },
    )
    return key


def put_session(
    store: ResultStore,
    system: str,
    *,
    truth: str = "dev-008",
    seed: int = 1,
    make_record: bool = True,
) -> ExperimentKey:
    text = f"session of {system}"
    key = make_key(
        "llm-session",
        system,
        truth,
        seed,
        {"truth_digest": sha(truth), "session_config": sha(text)},
        metric="none",
    )
    record_dir = f"llm/{str(key.digest)[:16]}"
    if make_record:
        (store.root / record_dir).mkdir(parents=True)
        (store.root / record_dir / "record.json").write_text("{}", encoding="utf-8")
    store.put(
        key,
        {"status": 1.0, "void": 0.0},
        {
            "unit": unit_json("llm-session", system, truth, seed),
            "session_config": text,
            "session": {
                "outcome": "submitted",
                "record": "record.json",
                "record_dir": record_dir,
            },
        },
    )
    return key


def put_score(
    store: ResultStore,
    system: str,
    session: str,
    per_event: float,
    *,
    truth: str = "dev-008",
    seed: int = 1,
) -> ExperimentKey:
    key = make_key("llm-score", system, truth, seed, {"session": session})
    store.put(
        key,
        {"status": 1.0, "per_event": per_event, "exact": 0.0, "has_structure": 1.0},
        {
            "unit": unit_json("llm-score", system, truth, seed),
            "result": {"structure": "Base()", "curve": []},
        },
    )
    return key


def clean_store(root: Path, *, blib: float = -0.80) -> Path:
    """Two (truth, seed) groups, every system below ORACLE, one LLM arm."""
    with ResultStore(root) as store:
        for seed in (1, 2):
            put_baseline(store, "ORACLE", -0.70, seed=seed)
            put_baseline(store, "B-lib", blib, seed=seed)
            put_baseline(store, "B-rand", -0.75, seed=seed)
        session = put_session(store, "AG-o-S/named")
        put_score(store, "AG-o-S/named", str(session.digest), -0.72)
        store.log_timing(
            {
                **unit_json("llm-session", "AG-o-S/named", "dev-008", 1),
                "digest": str(session.digest),
                "wall_s": 1.5,
                "attempt": 1,
            }
        )
    return root

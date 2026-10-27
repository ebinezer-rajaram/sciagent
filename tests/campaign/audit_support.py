"""Store builders for the defect-hunt check tests: cells written through
:meth:`ResultStore.put` with the driver's layout (``unit``, ``result``,
``system_config`` / ``session_config``, ``session.record_dir``), and session
records with the harness's layout (a ``config`` entry with ``budgets`` and
``tools``, one ``tool_call`` entry per layer call)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from sciagent.campaign.store import ResultStore
from sciagent.core.types import DataVersion, EnvVersion, FrozenDict, MetricVersion, Seed
from sciagent.harness.record import SessionRecord
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


#: The session records' budgets and tools (``config`` entry of a record).
BUDGETS: dict[str, int] = {"experiments": 2, "fits": 2, "submit": 1}
TOOLS: tuple[tuple[str, str | None], ...] = (
    ("run_experiment", "experiments"),
    ("fit", "fits"),
    ("diagnostic", None),
    ("submit", "submit"),
)


def call(
    index: int,
    name: str,
    charged: str | None,
    after: tuple[int, int, int],
    *,
    is_error: bool = False,
    record: dict[str, Any] | None = None,
    text: str = "ok",
) -> dict[str, Any]:
    """A ``tool_call`` body; ``after`` is (experiments, fits, submit) used."""
    return {
        "call": index,
        "name": name,
        "args": {},
        "text": text,
        "is_error": is_error,
        "record": record,
        "charged": charged,
        "budgets_after": dict(
            zip(("experiments", "fits", "submit"), after, strict=True)
        ),
    }


#: One experiment, a fit, a refused repeat of it, a free diagnostic, a submit.
CALLS: tuple[dict[str, Any], ...] = (
    call(0, "run_experiment", "experiments", (1, 0, 0)),
    call(1, "fit", "fits", (1, 1, 0), record={"fit": 1}),
    call(
        2,
        "fit",
        None,
        (1, 1, 0),
        is_error=True,
        record={"repeat_of": 1},
        text="not charged: this structure was already fitted",
    ),
    call(3, "diagnostic", None, (1, 1, 0)),
    call(4, "submit", "submit", (1, 1, 1)),
)


def write_record(path: Path, calls: tuple[dict[str, Any], ...] = CALLS) -> None:
    record = SessionRecord()
    record.append(
        "config",
        {
            "budgets": BUDGETS,
            "tools": [{"name": n, "budget": b} for n, b in TOOLS],
        },
    )
    for body in calls:
        record.append("tool_call", body)
    record.save(path)


def session_counts(
    calls: tuple[dict[str, Any], ...],
) -> tuple[dict[str, float], dict[str, Any]]:
    """The reading fields and ``session`` detail fields the campaign derives
    from a record's calls (``campaign.llm.run_session``)."""
    counts: dict[str, int] = {}
    for body in calls:
        counts[body["name"]] = counts.get(body["name"], 0) + 1
    charged = [body["charged"] for body in calls]
    reading = {
        "experiments_used": float(charged.count("experiments")),
        "fits_used": float(charged.count("fits")),
        "tool_calls": float(len(calls)),
    }
    detail = {
        "tool_calls": dict(sorted(counts.items())),
        "fitted": ["Base()"] * charged.count("fits"),
    }
    return reading, detail


def put_session(
    store: ResultStore,
    system: str,
    *,
    truth: str = "dev-008",
    seed: int = 1,
    make_record: bool = True,
    calls: tuple[dict[str, Any], ...] = CALLS,
    reading: dict[str, float] | None = None,
    session: dict[str, Any] | None = None,
) -> ExperimentKey:
    """One completed session cell and its record; ``reading`` and ``session``
    override fields of the reading and of the detail's ``session`` block."""
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
        write_record(store.root / record_dir / "record.json", calls)
    used, counted = session_counts(calls)
    store.put(
        key,
        {"status": 1.0, "void": 0.0, **used, **(reading or {})},
        {
            "unit": unit_json("llm-session", system, truth, seed),
            "session_config": text,
            "session": {
                "outcome": "submitted",
                "record": "record.json",
                "record_dir": record_dir,
                **counted,
                **(session or {}),
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

"""Instrument test 7, parts 2 and 3: no tool path exposes the truth, and the
anonymisation leak test passes (SPEC §6.3, §5).

Static: the agent-facing modules never name the truth, and inside ``world.py``
the truth is touched only by ``World.__init__`` and ``World._simulate``, whose
results are simulated data. Dynamic: a scripted investigation's every output
is searched for the truth's coefficients, shape parameters and canonical DSL.
Leak: every prompt and tool schema of the anonymised condition, for both
tiers, and every output of a scripted anonymised run, carries no forbidden
term (the default domain cues, ``size``/``sign``/``arrival`` and every native
diagnostic name) and no distinctive native-unit constant.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from investigation_support import (
    POINTPROC,
    clean_environment,
    lab,
    plain_run,
    resolve,
    scripted_run,
)

from environments.pointproc import v2
from sciagent.anonymise import assert_no_leaks
from sciagent.glm.canonical import canonicalise
from sciagent.glm.interventions import DEFAULT_HORIZON, MAX_CLAMP_RATE, MAX_HORIZON
from sciagent.glm.syntax import render
from sciagent.harness.record import SessionRecord
from sciagent.harness.scripted import Step
from sciagent.investigation.prompts import Arm
from sciagent.investigation.runner import leak_terms, rendered_surface

PACKAGE = Path(__file__).resolve().parents[2] / "src" / "sciagent" / "investigation"
#: Modules whose code runs on an agent request or renders what the agent sees.
AGENT_FACING = ("tools.py", "view.py", "prompts.py")
#: The only World methods allowed to touch ``self._truth``.
TRUTH_READERS = {"__init__", "_simulate"}
#: Native-unit constants that must not survive into the anonymised view.
NATIVE_CONSTANTS = (DEFAULT_HORIZON, MAX_HORIZON, MAX_CLAMP_RATE)


def _names(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.arg):
            found.add(node.arg)
    return found


@pytest.mark.parametrize("module", AGENT_FACING)
def test_agent_facing_modules_never_name_the_truth(module: str) -> None:
    tree = ast.parse((PACKAGE / module).read_text(encoding="utf-8"))
    used = _names(tree)
    assert not used & {"truth", "_truth", "resolve", "coef", "TruthSpec", "Truth"}, (
        f"{module} names {sorted(used & {'truth', '_truth', 'resolve', 'coef'})}"
    )
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and "world" in node.module
        for alias in node.names
    }
    assert imported <= {"MAX_EVENTS", "OBSERVATIONAL", "World", "experiment_id"}


def test_world_touches_the_truth_only_in_its_readers() -> None:
    tree = ast.parse((PACKAGE / "world.py").read_text(encoding="utf-8"))
    world = next(
        n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "World"
    )
    readers: set[str] = set()
    for method in world.body:
        if not isinstance(method, ast.FunctionDef):
            continue
        for node in ast.walk(method):
            if isinstance(node, ast.Attribute) and node.attr == "_truth":
                readers.add(method.name)
    assert readers <= TRUTH_READERS | {"channels"}, readers
    # ``channels`` reads only the truth's channel specs, which are public.
    channels = next(
        m for m in world.body if isinstance(m, ast.FunctionDef) and m.name == "channels"
    )
    attrs = {
        n.attr
        for n in ast.walk(channels)
        if isinstance(n, ast.Attribute) and n.attr != "_truth"
    }
    assert attrs == {"channels"}


def _truth_tokens(truth_id: str) -> list[str]:
    """Strings that would show the truth: its DSL and its exact numbers."""
    truth = resolve(truth_id)
    tokens = [render(truth.structure), render(canonicalise(truth.structure))]
    numbers = [truth.coef.intercept]
    numbers += [v for column in truth.coef.per_feature for v in column]
    numbers += [v for mapping in truth.psi for v in mapping.values()]
    for value in numbers:
        tokens += [repr(value), f"{value:.6g}"]
    return tokens


def _all_text(record: SessionRecord) -> list[str]:
    texts: list[str] = []
    config = record.of_kind("config")[0].body
    texts += [config["system_prompt"], config["prompt"]]
    texts += [str(schema) for schema in config["tools"]]
    texts += [e.body["text"] for e in record.of_kind("tool_call")]
    return texts


def _probing_script(condition_channel: str) -> list[Step]:
    """Every agent-facing tool, with a non-truth structure fitted."""
    return [
        Step(calls=(("diagnostic", {"name": "mean_rate", "data_id": "obs"}),)),
        Step(calls=(("nonparam_kernels", {"data_id": "obs"}),)),
        Step(
            calls=(
                (
                    "predict",
                    {
                        "experiment": 0,
                        "diagnostic": "mean_rate",
                        "hypothesis": "Excite(ExpK, One, all)",
                        "interval": [0.5, 1.5],
                        "level": 0.8,
                    },
                ),
            )
        ),
        Step(calls=(("run_experiment", plain_run(400.0)),)),
        Step(
            calls=(
                ("fit", {"structure": "Excite(ExpK, One, all)", "data_ids": ["obs"]}),
                ("fit", {"structure": "link=exp; Periodic", "data_ids": ["e0"]}),
            )
        ),
        Step(
            calls=(
                (
                    "submit",
                    {
                        "structure": f"Excite(GammaK, Above({condition_channel}), all)",
                        "report": "probe",
                    },
                ),
            )
        ),
    ]


@pytest.mark.parametrize("truth_id", ["size_excitation", "hawkes"])
def test_no_tool_output_shows_the_truth(
    truth_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_environment(monkeypatch)
    outcome = scripted_run(_probing_script("size"), tmp_path, truth=truth_id)
    assert outcome.outcome == "submitted"
    record = SessionRecord.load(outcome.record_path)
    calls = record.of_kind("tool_call")
    assert len(calls) == 7
    assert not any(c.body["is_error"] for c in calls), [
        c.body["text"] for c in calls if c.body["is_error"]
    ]
    texts = _all_text(record)
    tokens = _truth_tokens(truth_id)
    hits = [(tok, t[:80]) for t in texts for tok in tokens if tok in t]
    # The hawkes truth's DSL is the hypothesis the script fits, so it is
    # legitimately echoed by the fit; its numbers must still never appear.
    if truth_id == "hawkes":
        dsl = {render(resolve(truth_id).structure)}
        hits = [h for h in hits if h[0] not in dsl and "Excite(ExpK, One" not in h[0]]
    assert not hits


@pytest.mark.parametrize("arm", ["AG-c", "AG-o"])
def test_anonymised_prompts_and_schemas_do_not_leak(arm: Arm, tmp_path: Path) -> None:
    # Rendering needs no container: the image is named, not asked of Docker.
    the_lab = lab(arm, "anon", sandbox_root=tmp_path, image="sciagent-sandbox:unused")
    surface = rendered_surface(the_lab)
    terms = leak_terms(POINTPROC, tuple(c.name for c in v2.CHANNELS))
    assert {"size", "sign", "arrival", "mean_rate", "hawkes"} <= set(terms)
    assert_no_leaks(surface, terms, numeric_constants=NATIVE_CONSTANTS)


def test_the_leak_test_would_catch_a_leak(tmp_path: Path) -> None:
    """Positive control: the same scan fails on the named condition."""
    from sciagent.anonymise import LeakError

    surface = rendered_surface(lab("AG-c", "named"))
    terms = leak_terms(POINTPROC, tuple(c.name for c in v2.CHANNELS))
    with pytest.raises(LeakError) as caught:
        assert_no_leaks(surface, terms, numeric_constants=NATIVE_CONSTANTS)
    found = {leak.term for leak in caught.value.leaks}
    assert {"size", "sign", "mean_rate"} <= found


def test_anonymised_run_outputs_do_not_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_environment(monkeypatch)
    the_lab = lab("AG-c", "anon")
    rate = the_lab.view.diagnostic("mean_rate")
    script = _probing_script("m1")
    script[0] = Step(calls=(("diagnostic", {"name": rate, "data_id": "obs"}),))
    predict: dict[str, Any] = dict(script[2].calls[0][1])
    predict["diagnostic"] = rate
    script[2] = Step(calls=(("predict", predict),))
    script[3] = Step(calls=(("run_experiment", plain_run(8 * 400.0)),))
    outcome = scripted_run(script, tmp_path, condition="anon")
    assert outcome.outcome == "submitted"
    record = SessionRecord.load(outcome.record_path)
    calls = record.of_kind("tool_call")
    assert not any(c.body["is_error"] for c in calls), [
        c.body["text"] for c in calls if c.body["is_error"]
    ]
    terms = leak_terms(POINTPROC, tuple(c.name for c in v2.CHANNELS))
    outputs = {f"call {c.body['call']}": c.body["text"] for c in calls}
    assert_no_leaks(outputs, terms)

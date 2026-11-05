"""Instrument test 7, part 4: what an anonymised agent reads when a call fails
(SPEC §5, §6.3; red team 2026-11-05, finding 2b).

``test_anonymised_run_outputs_do_not_leak`` asserts that no call errored, so it
never reads an error text, and ``sign`` reached one anonymised pilot session
through one (dev-015 s1, ``LastMarkAbove on a sign channel``). Here the calls
fail on purpose and every text the agent would read is scanned:

- a scripted session, whose record holds exactly what the agent read;
- each tool's refusals at the layer: structures, diagnostics, designs, the
  prediction ledger, beliefs, budgets, the shape check and the AG-o tools;
- the DSL parser, on every prefix of a structure and on every token replaced;
- the three places a catalogue failure is worded into a text that is not an
  error: the dataset summary, a prediction's outcome and the policy's reading.

The list scanned is the session's own (:func:`leak_terms`) plus ``signed``: the
scan matches a short term as a whole word, so it cannot see ``signed``, which
says the same thing.

Refusal wording is neutral where it is written, so both conditions read the
same words and the anonymised text is the named text with its names mapped:
that is tested too, because a rewrite in the anonymised view alone would pass
every scan here and add a second difference between the conditions.

A static net closes what the table cannot list: no string in a ``raise`` on the
agent's path, and no string of the DSL parser, carries a forbidden term.
"""

from __future__ import annotations

import ast
import dataclasses
import math
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Final

import numpy as np
import pytest
from investigation_support import (
    BELIEF,
    BELIEF_ARGS,
    POINTPROC,
    SMALL_POLICY,
    STOP,
    believing,
    clean_environment,
    lab,
    plain_run,
    scripted_run,
)

import sciagent
from environments.pointproc import v2
from sciagent.anonymise import (
    LeakError,
    anonymise_text,
    assert_no_leaks,
    deanonymise_text,
    scan_for_leaks,
)
from sciagent.core.errors import GrammarError
from sciagent.diagnostics import catalogue
from sciagent.glm.data import EventLog
from sciagent.glm.grammar import (
    Excite,
    KernelKind,
    One,
    Source,
    SourceKind,
    Structure,
)
from sciagent.glm.interventions import DEFAULT_HORIZON, MAX_CLAMP_RATE, MAX_HORIZON
from sciagent.glm.syntax import DslSyntaxError, render
from sciagent.harness.record import SessionRecord
from sciagent.harness.scripted import Step
from sciagent.harness.tools import ToolLayer
from sciagent.investigation import Lab
from sciagent.investigation.runner import leak_terms, replay_investigation

ROOT: Final = Path(__file__).resolve().parents[2]
#: The package this process imported: what runs, wherever it was imported from.
PACKAGE: Final = Path(sciagent.__file__).resolve().parent
#: Native-unit constants that must not survive into the anonymised view.
NATIVE_CONSTANTS: Final = (DEFAULT_HORIZON, MAX_HORIZON, MAX_CLAMP_RATE)
#: A word the scan's whole-word rule for short terms cannot see.
UNSCANNED_FORMS: Final = ("signed",)
TERMS: Final = (
    *leak_terms(POINTPROC, tuple(c.name for c in v2.CHANNELS)),
    *UNSCANNED_FORMS,
)

#: Structures refused for what they do with a channel, in the anonymised
#: names. The channel is ``m1`` (positive) and ``m2`` (-1 or +1).
BAD_STRUCTURES: Final = (
    "Gate(Periodic, LastMarkAbove(m2))",  # the pilot's error
    "Excite(ExpK, Above(m2), all)",
    "Excite(ExpK, ExpOf(m2), all)",
    "Excite(ExpK, Pow(m2), all)",
    "Excite(ExpK, One, m1=+)",
    "Excite(ExpK, One, m1=-)",
    "Excite(ExpK, One, m2)",
    "Excite(ExpK, One, )",
    "Excite(ExpK, Mark(m2), m2=+)",
    "Excite(ExpK, Mark(m3), all)",
    "Excite(ExpK, One, m3=+)",
    "Gate(Periodic, LastMarkAbove(m3))",
    "Gate(Gate(Gate(Gate(Periodic, PhaseWindow), PhaseWindow), PhaseWindow), Trend)",
    "Periodic + Periodic + Periodic + Periodic + Periodic",
)
#: A structure using every production, for the parser's sweep.
RICH: Final = (
    "link=exp; Gate(Product(Excite(GammaK, Above(m1), m2=+), Periodic), "
    "LastMarkAbove(m1)) + Excite(PowerK, Mark(m1), all) + Trend"
)
#: What a diagnostic argument may wrongly be.
BAD_VALUES: Final[tuple[Any, ...]] = (-1, 0, 1e300, "m9", True, "nan", 0.5)


def _scan(texts: Mapping[str, str]) -> None:
    assert_no_leaks(texts, TERMS)


def _call_all(
    layer: ToolLayer,
    calls: Iterable[tuple[str, dict[str, Any]]],
    *,
    errored: bool = True,
) -> dict[str, str]:
    """Each call's text, by the call; every one must be refused if ``errored``."""
    texts: dict[str, str] = {}
    for name, args in calls:
        out = layer.call(name, args)
        if errored:
            assert out.is_error, (name, args, out.text)
        texts[f"{name} {args!r}"] = out.text
    return texts


def _anon_lab(**kwargs: Any) -> Lab:
    return lab("AG-c", "anon", **kwargs)


def _fit(structure: str) -> tuple[str, dict[str, Any]]:
    return "fit", {"structure": structure, "data_ids": ["obs"]}


def _valid_args(spec: catalogue.Diagnostic, the_lab: Lab) -> dict[str, Any]:
    """Arguments that pass for ``spec`` on the observational log."""
    args: dict[str, Any] = {}
    for arg in spec.args:
        if arg.default is not None:
            continue
        match arg.kind:
            case catalogue.ArgKind.CHANNEL:
                allowed = (
                    c.name for c in the_lab.view.channels if c.kind in arg.channel_kinds
                )
                args[arg.name] = next(allowed)
            case catalogue.ArgKind.TIME:
                args[arg.name] = 80.0
            case _:
                args[arg.name] = arg.low
    return args


# --------------------------------------------------------------------------
# A session, as the agent read it
# --------------------------------------------------------------------------


def test_a_scripted_anon_session_reads_no_forbidden_term_in_its_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_environment(monkeypatch)
    good = {"structure": "Excite(ExpK, One, all)", "report": "probe", **STOP}
    bad_submit = {**good, "structure": "Gate(Periodic, LastMarkAbove(m2))"}
    rate = _anon_lab().view.diagnostic("mean_rate")
    script = [
        Step(calls=tuple(_fit(s) for s in BAD_STRUCTURES)),
        Step(
            calls=(
                (
                    "belief",
                    {**BELIEF_ARGS, "favoured": "Excite(ExpK, Above(m2), all)"},
                ),
                ("belief", {**BELIEF_ARGS, "rivals": ["Excite(ExpK, One, m1=+)"]}),
                (
                    "predict",
                    {
                        "experiment": 0,
                        "diagnostic": rate,
                        "hypothesis": "Excite(ExpK, One, m2)",
                        "interval": [0.0, 1.0],
                        "level": 0.5,
                    },
                ),
                ("diagnostic", {"name": rate, "data_id": "obs", "args": {"zzz": 1}}),
            )
        ),
        Step(calls=(BELIEF, ("submit", bad_submit))),
        Step(calls=(("submit", good),)),
    ]
    outcome = scripted_run(script, tmp_path, condition="anon")
    assert outcome.outcome == "submitted"
    record = SessionRecord.load(outcome.record_path)
    calls = record.of_kind("tool_call")
    errors = [c for c in calls if c.body["is_error"]]
    assert len(errors) == len(BAD_STRUCTURES) + 5, [c.body["text"] for c in calls]
    # The pilot's call reached its refusal, not some earlier one.
    assert "LastMarkAbove" in errors[0].body["text"]
    _scan({f"call {c.body['call']}": c.body["text"] for c in calls})
    # Replay compares these texts, so a refusal must be the same on a rerun.
    report = replay_investigation(record, POINTPROC, sandbox_root=tmp_path / "re")
    assert report.steps == len(calls)


# --------------------------------------------------------------------------
# Each tool's refusals
# --------------------------------------------------------------------------


@pytest.mark.parametrize("structure", BAD_STRUCTURES)
def test_a_refused_structure_leaks_through_no_tool(structure: str) -> None:
    the_lab = _anon_lab()
    layer = believing(the_lab)
    predict = {
        "experiment": 0,
        "diagnostic": the_lab.view.diagnostic("mean_rate"),
        "hypothesis": structure,
        "interval": [0.0, 1.0],
        "level": 0.5,
    }
    _scan(
        _call_all(
            layer,
            (
                _fit(structure),
                ("predict", predict),
                ("belief", {**BELIEF_ARGS, "favoured": structure}),
                ("belief", {**BELIEF_ARGS, "rivals": [structure]}),
                ("submit", {"structure": structure, "report": "r", **STOP}),
            ),
        )
    )


def _parse_errors(text: str, the_lab: Lab) -> dict[str, str]:
    """``text``'s refusal, if it has one, keyed by the text."""
    try:
        the_lab.view.parse(text)
    except GrammarError as error:
        return {text: str(error)}
    return {}


def test_no_parser_refusal_names_a_forbidden_term() -> None:
    """Every prefix of a rich structure, and each of its tokens replaced by what
    cannot stand there: the parser's every ``expected``."""
    the_lab = _anon_lab()
    texts: dict[str, str] = {}
    for end in range(len(RICH)):
        texts |= _parse_errors(RICH[:end], the_lab)
    tokens = RICH.replace("(", " ( ").replace(")", " ) ").replace(",", " , ").split()
    for k in range(len(tokens)):
        for filler in ("zz", "1", ",", ")", "(", "#"):
            texts |= _parse_errors(
                " ".join((*tokens[:k], filler, *tokens[k + 1 :])), the_lab
            )
    words = " ".join(texts.values())
    for expected in ("a kernel", "a mark function", "a source", "a condition", "link"):
        assert expected in words, expected
    _scan(texts)


def test_rendering_an_ill_formed_source_names_no_kind() -> None:
    """A source with no channel: the framework's own fault, worded the same way."""
    broken = Structure(
        (Excite(KernelKind.EXP, One(), Source(SourceKind.POSITIVE, None)),)
    )
    with pytest.raises(DslSyntaxError) as caught:
        render(broken)
    _scan({"render": str(caught.value)})


def test_a_refused_diagnostic_names_itself_as_the_agent_does() -> None:
    the_lab = _anon_lab()
    layer = believing(the_lab)
    calls: list[tuple[str, dict[str, Any]]] = []
    for agent, _ in the_lab.view.diagnostics():
        calls.append(
            ("diagnostic", {"name": agent, "data_id": "obs", "args": {"z": 1}})
        )
        calls.append(
            (
                "predict",
                {
                    "experiment": 0,
                    "diagnostic": agent,
                    "args": {"z": 1},
                    "hypothesis": "null",
                    "interval": [0.0, 1.0],
                    "level": 0.5,
                },
            )
        )
        calls.append(("diagnostic", {"name": agent, "data_id": "e9"}))
    _scan(_call_all(layer, calls))


def test_a_bad_diagnostic_argument_leaks_nothing() -> None:
    the_lab = _anon_lab()
    layer = believing(the_lab)
    calls = [
        (
            "diagnostic",
            {
                "name": agent,
                "data_id": "obs",
                "args": {**_valid_args(catalogue.get(native), the_lab), arg.name: bad},
            },
        )
        for agent, native in the_lab.view.diagnostics()
        for arg in catalogue.get(native).args
        for bad in BAD_VALUES
    ]
    assert len(calls) > 100
    _scan(_call_all(layer, calls, errored=False))


@pytest.mark.parametrize("horizon", [0.001, 24.0], ids=["empty", "few-events"])
def test_a_short_run_leaks_nothing_where_a_diagnostic_cannot_be_read(
    horizon: float,
) -> None:
    """The summary, each prediction's outcome and each later diagnostic."""
    the_lab = _anon_lab()
    layer = believing(the_lab)
    diagnostics = the_lab.view.diagnostics()
    predictions = [
        (
            "predict",
            {
                "experiment": 0,
                "diagnostic": agent,
                "args": _valid_args(catalogue.get(native), the_lab),
                "hypothesis": "null",
                "interval": [0.0, 1.0],
                "level": 0.5,
            },
        )
        for agent, native in diagnostics
    ]
    assert not any(layer.call(name, args).is_error for name, args in predictions), (
        "a prediction was refused, so its outcome is not read below"
    )
    ran = layer.call("run_experiment", plain_run(horizon))
    assert not ran.is_error, ran.text
    assert ran.record is not None
    if horizon < 1.0:
        assert ran.record["n_events"] == 0
        assert ran.text.count("could not be evaluated") == len(diagnostics)
    texts = {"run_experiment": ran.text}
    texts |= _call_all(
        layer,
        [
            (
                "diagnostic",
                {
                    "name": agent,
                    "data_id": "e0",
                    "args": _valid_args(catalogue.get(native), the_lab),
                },
            )
            for agent, native in diagnostics
        ],
        errored=False,
    )
    texts |= _call_all(layer, [("nonparam_kernels", {"data_id": "e0"})], errored=False)
    _scan(texts)


def test_a_time_bound_is_shown_in_the_agents_units() -> None:
    """The upper bound of a time argument is the agent's horizon, not the native
    one: exactly ``c`` times what the named condition reads."""
    anon = _anon_lab()
    named = lab("AG-c", "named")
    agent = anon.view.diagnostic("spectral_peak_frequency")
    given = {"data_id": "obs", "args": {"bin_width": 1e300}}
    anon_text = anon.layer().call("diagnostic", {"name": agent, **given}).text
    named_text = (
        named.layer()
        .call("diagnostic", {"name": "spectral_peak_frequency", **given})
        .text
    )
    log = anon.world.datasets["obs"].log
    high = catalogue.get("spectral_peak_frequency").args[0].high * log.horizon
    assert f", {high}]" in named_text
    assert f", {high * anon.view.factor}]" in anon_text


def test_a_diagnostic_the_catalogue_cannot_finish_names_no_native_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The non-finite refusal, a catalogue bug, is shown like any other."""
    the_lab = _anon_lab()
    layer = believing(the_lab)
    native = "mean_rate"
    broken = dataclasses.replace(
        catalogue.get(native), function=lambda _log, _channels, _args: math.nan
    )
    monkeypatch.setitem(catalogue.CATALOGUE, native, broken)
    agent = the_lab.view.diagnostic(native)
    _scan(_call_all(layer, [("diagnostic", {"name": agent, "data_id": "obs"})]))


def test_a_policy_reading_that_fails_names_no_native_diagnostic() -> None:
    """The third wording of a catalogue failure: "diagnostic chosen by the
    framework: ...: not available (<why>)"."""
    the_lab = lab("AG-c", "anon", policy="random", policy_settings=SMALL_POLICY)
    believing(the_lab)
    mode = the_lab.policy_mode
    assert mode is not None
    observational = the_lab.world.datasets["obs"]
    chosen = mode.decide(0, the_lab.state.belief_structures, [observational])
    empty = np.empty(0)
    log = EventLog.create(empty, {c.name: empty for c in the_lab.view.channels}, 1.0)
    value, why = mode.value(chosen, log)
    assert value is None
    assert why is not None
    _scan({"why": why})


def test_a_policy_arm_refusal_leaks_nothing() -> None:
    the_lab = lab("AG-c", "anon", policy="random", policy_settings=SMALL_POLICY)
    layer = believing(the_lab)
    _scan(_call_all(layer, [("run_experiment", plain_run(8.0))]))


def _design(**intervention: Any) -> dict[str, Any]:
    return {"intervention": intervention, "horizon": 800.0}


#: Designs refused at each check: JSON shape, generators, channels, values,
#: windows, caps. In the agent's names and units.
BAD_DESIGNS: Final[tuple[dict[str, Any], ...]] = (
    {},
    {"intervention": 3},
    {"intervention": {}},
    _design(type="zz"),
    _design(type="force_events"),
    _design(type="force_events", times=[]),
    _design(type="force_events", times=[2.0, 1.0]),
    _design(type="force_events", times=[-1.0]),
    _design(type="force_events", times=[1e9]),
    _design(type="force_events", times={"start": 1.0, "step": 1.0}),
    _design(type="force_events", times=[1.0], marks={"m3": [1.0]}),
    _design(type="force_events", times=[1.0], marks={"m2": [0.5]}),
    _design(type="force_events", times=[1.0], marks={"m1": [-1.0]}),
    _design(type="force_events", times=[1.0], marks={"m1": [1e9]}),
    _design(type="force_events", times=[1.0, 2.0], marks={"m2": [1.0]}),
    _design(type="force_events", times=[1.0], marks={"m2": {"zz": 1}}),
    _design(type="inject_marks", window=[0.0, 1.0], channel="m2", value=0.5),
    _design(type="inject_marks", window=[0.0, 1.0], channel="m1", value=-1.0),
    _design(type="inject_marks", window=[0.0, 1.0], channel="m3", value=1.0),
    _design(type="inject_marks", window=[0.0, 1e9], channel="m2", value=1.0),
    _design(type="censor", window=[2.0, 1.0]),
    _design(type="clamp_rate", window=[0.0, 1.0], rate=-1.0),
    _design(type="clamp_rate", window=[0.0, 1.0], rate=1e9),
    _design(
        type="compose",
        parts=[
            {"type": "inject_marks", "window": [0, 2], "channel": "m2", "value": 1},
            {"type": "inject_marks", "window": [1, 3], "channel": "m2", "value": 1},
        ],
    ),
    {"intervention": {"type": "compose", "parts": []}, "horizon": -1.0},
    {"intervention": {"type": "compose", "parts": []}, "horizon": 1e12},
)


def test_a_refused_design_leaks_nothing() -> None:
    """Here the numbers are read too: a design's caps are native constants, and
    each must reach the agent scaled. Elsewhere that scan cannot be used: half of
    the agent's own horizon is 8000.0, which is the native ``MAX_HORIZON``, and
    the kernel estimate's 50 events is ``MAX_CLAMP_RATE``.
    ``test_a_time_bound_is_shown_in_the_agents_units`` covers those texts."""
    layer = believing(_anon_lab())
    texts = _call_all(layer, [("run_experiment", d) for d in BAD_DESIGNS])
    assert_no_leaks(texts, TERMS, numeric_constants=NATIVE_CONSTANTS)


def test_ledger_belief_and_shape_refusals_leak_nothing() -> None:
    the_lab = _anon_lab()
    rate = the_lab.view.diagnostic("mean_rate")
    layer = the_lab.layer()
    predict = {
        "experiment": 0,
        "diagnostic": rate,
        "hypothesis": "null",
        "interval": [0.0, 1.0],
        "level": 0.5,
    }
    texts = _call_all(
        layer,
        (
            ("run_experiment", plain_run(8.0)),  # no belief yet
            ("submit", {"structure": "null", "report": "r", **STOP}),
            ("zz", {}),
            ("fit", {}),
            ("fit", {"structure": "null", "data_ids": ["obs"], "zz": 1}),
            ("fit", {"structure": "null", "data_ids": []}),
            ("fit", {"structure": "null", "data_ids": ["e9"]}),
            ("nonparam_kernels", {"data_id": "e9"}),
            ("predict", {**predict, "experiment": 99}),
            ("predict", {**predict, "experiment": "zz"}),
            ("predict", {**predict, "interval": [1.0, 0.0]}),
            ("predict", {**predict, "interval": [0.0]}),
            ("predict", {**predict, "level": 2.0}),
            ("predict", {**predict, "args": 3}),
            ("belief", {**BELIEF_ARGS, "rivals": []}),
            ("belief", {**BELIEF_ARGS, "rivals": ["null"]}),
            ("belief", {**BELIEF_ARGS, "confidence": 2.0}),
            ("belief", {**BELIEF_ARGS, "favoured": 3}),
        ),
    )
    layer.call(*BELIEF)
    assert not layer.call("run_experiment", plain_run(8.0)).is_error
    texts |= _call_all(
        layer,
        (
            ("predict", predict),  # experiment 0 is sealed
            ("submit", {"structure": "null", "report": "r", "stop_reason": "zz"}),
        ),
    )
    _scan(texts)


def test_budget_and_end_refusals_leak_nothing() -> None:
    the_lab = _anon_lab(experiments=1, fits=1)
    layer = believing(the_lab)
    assert not layer.call("run_experiment", plain_run(8.0)).is_error
    assert not layer.call(*_fit("null")).is_error
    layer.call(*BELIEF)
    texts = _call_all(
        layer,
        (
            ("run_experiment", plain_run(8.0)),
            _fit("link=exp; Trend"),
        ),
    )
    assert not layer.call(
        "submit", {"structure": "null", "report": "r", **STOP}
    ).is_error
    texts |= _call_all(layer, (_fit("null"),))
    _scan(texts)


def test_ag_o_refusals_leak_nothing(tmp_path: Path) -> None:
    """The handlers' own refusals; no container runs, so what a sandbox prints is
    not read here (it is the agent's own code, ``python_tool_text``)."""
    the_lab = lab(
        "AG-o", "anon", sandbox_root=tmp_path, image="sciagent-sandbox:unused"
    )
    layer = believing(the_lab)
    too_long = "x" * 1_000_000
    calls: list[tuple[str, dict[str, Any]]] = [
        ("run_experiment", {"intervention": {"from": path}})
        for path in ("/work/none.json", "/etc/passwd", "/work/../x", 3)
    ]
    calls += [
        ("run_experiment", {"intervention": {"from": "/work/x.json"}, "horizon": 1.0}),
        ("python", {"code": too_long}),
        ("notebook", {"append": too_long}),
    ]
    _scan(_call_all(layer, calls))


# --------------------------------------------------------------------------
# The scan, and what the conditions share
# --------------------------------------------------------------------------


def test_the_error_scan_would_catch_a_leak() -> None:
    """Positive control: the same scan reads a named refusal and fires."""
    layer = believing(lab("AG-c", "named"))
    texts = _call_all(layer, (_fit("Gate(Periodic, LastMarkAbove(sign))"),))
    with pytest.raises(LeakError) as caught:
        _scan(texts)
    assert {leak.term for leak in caught.value.leaks} == {"sign"}


def test_the_list_scanned_is_the_pilots() -> None:
    """``scripts/pilot.py`` holds the list a session runs under; the tests keep a
    copy (importing the script sets process-wide variables). Read, not imported."""
    tree = ast.parse((ROOT / "scripts" / "pilot.py").read_text(encoding="utf-8"))
    pilots = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "FORBIDDEN"
        and node.value is not None
    )
    assert set(ast.literal_eval(pilots)) <= set(TERMS)
    assert set(ast.literal_eval(pilots)) == set(POINTPROC.forbidden)


def _named_text(the_lab: Lab, text: str) -> str:
    name_map = the_lab.view.name_map
    assert name_map is not None
    return deanonymise_text(text, name_map)


@pytest.mark.parametrize("structure", BAD_STRUCTURES)
def test_a_refused_structure_reads_the_same_in_both_conditions(structure: str) -> None:
    """The refusal differs by names alone. A syntax error's position moves with
    a name's length; no structure here has a channel before the refused token."""
    anon = _anon_lab()
    named = lab("AG-c", "named")
    anon_out = anon.layer().call(*_fit(structure)).text
    named_out = named.layer().call(*_fit(_named_text(anon, structure))).text
    name_map = anon.view.name_map
    assert name_map is not None
    assert anonymise_text(named_out, name_map) == anon_out


def test_a_refused_diagnostic_reads_the_same_in_both_conditions() -> None:
    anon = _anon_lab()
    named = lab("AG-c", "named")
    anon_layer = anon.layer()
    named_layer = named.layer()
    for agent, native in anon.view.diagnostics():
        given = {"data_id": "obs", "args": {"z": 1}}
        assert (
            anon_layer.call("diagnostic", {"name": agent, **given}).text
            == named_layer.call("diagnostic", {"name": native, **given}).text
        )


# --------------------------------------------------------------------------
# The static net
# --------------------------------------------------------------------------

#: Packages and modules whose code runs inside a tool call of a live session.
AGENT_PATH: Final = (
    "anonymise.py",
    "diagnostics/catalogue.py",
    "glm",
    "harness",
    "investigation",
    "nonparam",
)
#: The parser's words reach a message through locals and module constants, so
#: every string of it is read, not only those inside a ``raise``.
WHOLLY_SHOWN: Final = "glm/syntax.py"


def _docstrings(tree: ast.AST) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                found.add(id(first.value))
    return found


def _strings(node: ast.AST, skip: set[int]) -> list[tuple[int, str]]:
    return [
        (n.lineno, n.value)
        for n in ast.walk(node)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and id(n) not in skip
    ]


def _shown_strings(source: str, *, whole: bool) -> list[tuple[int, str]]:
    """The strings of a ``raise``; every non-docstring string if ``whole``."""
    tree = ast.parse(source)
    skip = _docstrings(tree)
    if whole:
        return _strings(tree, skip)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise):
            found += _strings(node, skip)
    return found


def _agent_path_modules() -> list[Path]:
    modules: list[Path] = []
    for entry in AGENT_PATH:
        path = PACKAGE / entry
        modules += sorted(path.glob("*.py")) if path.is_dir() else [path]
    return modules


def test_no_refusal_on_the_agents_path_is_written_with_a_forbidden_term() -> None:
    hits: list[str] = []
    modules = _agent_path_modules()
    assert len(modules) > 30
    for path in modules:
        relative = path.relative_to(PACKAGE).as_posix()
        for line, text in _shown_strings(
            path.read_text(encoding="utf-8"), whole=relative == WHOLLY_SHOWN
        ):
            hits += [
                f"{relative}:{line}: {leak.term}: {text!r}"
                for leak in scan_for_leaks(text, TERMS)
            ]
    assert not hits, "\n".join(hits)


def test_the_static_net_would_catch_a_refusal() -> None:
    """Positive control: both of its readings, on the pilot's own sentence."""
    leaky = (
        "def f(c):\n"
        "    expected = 'a source (<sign channel>=+)'\n"
        "    raise ValueError(f'LastMarkAbove on a sign channel: {c}')\n"
    )
    for whole, count in ((False, 1), (True, 2)):
        shown = _shown_strings(leaky, whole=whole)
        hits = [t for _, t in shown if scan_for_leaks(t, TERMS)]
        assert len(hits) == count, hits

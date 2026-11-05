"""Every value constant on a cell's path is in an address (F5 point 2; LOG 2026-10-27).

``METRIC_VERSION`` and ``DATA_VERSION`` end with the SHA-256 of a canonical
rendering of those constants (:mod:`sciagent.campaign.versions`), so changing one
moves the address with no hand bump. These tests keep the tables complete: an
AST walk over the files the LOG names finds every module constant, every
non-trivial numeric literal and every numeric default, and each must be
addressed or carry a stated reason. The domain's channels and marks reach the
environment version the same way (:attr:`CampaignEnvironment.domain_text`).
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import importlib
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

import numpy as np
import pytest
from campaign_support import entry, environment, tiny_config

from sciagent.campaign import plan, versions
from sciagent.campaign.audit import matches_env
from sciagent.campaign.plan import (
    DATA_VERSION,
    METRIC_VERSION,
    LLMArm,
    Unit,
    baseline_key,
    score_key,
    session_key,
)
from sciagent.campaign.versions import (
    DATA_CONSTANTS,
    EXEMPT_CONSTANTS,
    EXEMPT_LITERALS,
    METRIC_CONSTANTS,
    METRIC_LITERALS,
    TRIVIAL_LITERALS,
    AddressTextError,
    data_digest,
    metric_digest,
    render,
)
from sciagent.glm.fit import FIT_VERSION

SRC: Final = Path(__file__).resolve().parents[2] / "src"
PACKAGE: Final = SRC / "sciagent"
#: The files the LOG's completeness test covers.
SCOPE: Final = sorted(
    [
        *PACKAGE.glob("scoring/*.py"),
        *PACKAGE.glob("systems/v2/*.py"),
        *PACKAGE.glob("library/*.py"),
        PACKAGE / "investigation" / "world.py",
        PACKAGE / "campaign" / "execute.py",
        PACKAGE / "campaign" / "plan.py",
    ]
)
UPPER: Final = re.compile(r"_?[A-Z][A-Z0-9_]*")


def _module(path: Path) -> str:
    return str(path.relative_to(SRC).with_suffix("")).replace("/", ".")


def _targets(node: ast.expr) -> Iterator[str]:
    if isinstance(node, ast.Name):
        yield node.id
    elif isinstance(node, ast.Tuple):
        for element in node.elts:
            yield from _targets(element)


def _module_constants(path: Path) -> set[str]:
    """The upper-case names a module assigns at its top level."""
    names: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                names.update(_targets(target))
        elif isinstance(node, ast.AnnAssign):
            names.update(_targets(node.target))
    return {n for n in names if UPPER.fullmatch(n)}


def _number(node: ast.AST) -> float | None:
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
    ):
        return node.value
    return None


def _literals(path: Path) -> dict[str, set[float]]:
    """Qualified name -> the non-trivial numbers in its body, its parameter
    defaults and its field defaults, outside nested definitions."""
    found: dict[str, set[float]] = {}

    def note(name: str, value: float | None) -> None:
        if value is not None and value not in TRIVIAL_LITERALS:
            found.setdefault(name, set()).add(value)

    def walk(node: ast.AST, name: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                inner = f"{name}.{child.name}" if name else child.name
                if not isinstance(child, ast.ClassDef):
                    args = child.args
                    for default in (*args.defaults, *args.kw_defaults):
                        if default is not None:
                            note(inner, _number(default))
                walk(child, inner)
            elif name:
                for sub in ast.walk(child):
                    note(name, _number(sub))
                walk(child, name)
            else:
                walk(child, name)

    walk(ast.parse(path.read_text(encoding="utf-8")), "")
    return found


# --------------------------------------------------------------------------
# The tables are complete
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", SCOPE, ids=_module)
def test_every_module_constant_is_addressed_or_exempt(path: Path) -> None:
    module = _module(path)
    known = {
        *METRIC_CONSTANTS.get(module, ()),
        *DATA_CONSTANTS.get(module, ()),
        *EXEMPT_CONSTANTS.get(module, {}),
    }
    assert _module_constants(path) - known == set()


@pytest.mark.parametrize("path", SCOPE, ids=_module)
def test_every_literal_is_addressed_or_exempt(path: Path) -> None:
    module = _module(path)
    addressed = METRIC_LITERALS.get(module, {})
    exempt = EXEMPT_LITERALS.get(module, {})
    for name, values in _literals(path).items():
        if name not in exempt:
            assert values <= set(addressed.get(name, ())), f"{module}.{name}"


def test_no_table_names_something_gone() -> None:
    for table in (METRIC_CONSTANTS, DATA_CONSTANTS, EXEMPT_CONSTANTS):
        for module, names in table.items():
            loaded = importlib.import_module(module)
            for name in names:
                assert hasattr(loaded, name), f"{module}.{name}"
    scanned = {_module(p): _literals(p) for p in SCOPE}
    for module, literals in METRIC_LITERALS.items():
        for name, values in literals.items():
            assert set(values) <= scanned[module].get(name, set()), f"{module}.{name}"
    for module, reasons in EXEMPT_LITERALS.items():
        for name in reasons:
            assert name in scanned[module], f"{module}.{name}"


def test_no_constant_is_in_two_tables() -> None:
    for module in {*METRIC_CONSTANTS, *DATA_CONSTANTS, *EXEMPT_CONSTANTS}:
        metric = set(METRIC_CONSTANTS.get(module, ()))
        data = set(DATA_CONSTANTS.get(module, ()))
        exempt = set(EXEMPT_CONSTANTS.get(module, {}))
        assert not metric & data and not metric & exempt and not data & exempt


def test_every_exemption_gives_its_reason() -> None:
    for table in (*EXEMPT_CONSTANTS.values(), *EXEMPT_LITERALS.values()):
        assert all(reason.strip() for reason in table.values())


def test_the_exempt_read_time_constants_are_the_logs() -> None:
    assert {"DEFAULT_RESAMPLES", "DEFAULT_LEVEL"} <= set(
        EXEMPT_CONSTANTS["sciagent.scoring.stats"]
    )
    assert "CONVERGENCE_TOLERANCE" in EXEMPT_CONSTANTS["sciagent.scoring.behaviour"]
    assert "CONTROL_2_ALPHA" in EXEMPT_CONSTANTS["sciagent.scoring.gonogo"]


def test_the_constants_the_log_named_are_addressed() -> None:
    assert "UNDERFIT_TOL_REL" in METRIC_CONSTANTS["sciagent.systems.v2.systems"]
    assert "_CHUNK" in METRIC_CONSTANTS["sciagent.systems.v2.sparse_dictionary"]
    assert "_RATE_STARTS" in METRIC_CONSTANTS["sciagent.library.mmpp"]
    assert "_LOGIT_BOX" in METRIC_CONSTANTS["sciagent.library.mixture"]
    assert "SEARCH_SEED_TAG" in METRIC_CONSTANTS["sciagent.scoring.seeds"]
    assert "MAX_EVENTS" in DATA_CONSTANTS["sciagent.investigation.world"]


# --------------------------------------------------------------------------
# The digests
# --------------------------------------------------------------------------


def test_the_versions_end_with_their_digests() -> None:
    assert (
        f"sciagent.campaign.score/2|battery:120.0x32cap1000|constants:{metric_digest()}"
    ) == METRIC_VERSION
    assert (
        f"world-obs:2000.0|heldout:2000.0|world/1|constants:{data_digest()}"
    ) == DATA_VERSION
    assert re.fullmatch(r"[0-9a-f]{64}", metric_digest())
    assert re.fullmatch(r"[0-9a-f]{64}", data_digest())


def test_every_addressed_constant_moves_its_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for table, digest, other in (
        (METRIC_CONSTANTS, metric_digest, data_digest),
        (DATA_CONSTANTS, data_digest, metric_digest),
    ):
        before, unmoved = digest(), other()
        for module, names in table.items():
            loaded = importlib.import_module(module)
            for name in names:
                with monkeypatch.context() as patch:
                    patch.setattr(loaded, name, "changed")
                    assert digest() != before, f"{module}.{name}"
                    assert other() == unmoved, f"{module}.{name}"


def test_a_literal_in_the_table_moves_the_metric_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = metric_digest()
    changed = {**METRIC_LITERALS, "sciagent.library.base": {"multistart": (1.5,)}}
    monkeypatch.setattr(versions, "METRIC_LITERALS", changed)
    assert metric_digest() != before


def test_the_unit_failures_are_addressed(monkeypatch: pytest.MonkeyPatch) -> None:
    before = metric_digest()
    monkeypatch.setattr(versions, "UNIT_FAILURES", (ValueError,))
    assert metric_digest() != before


def test_the_tag_reaches_the_search_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    before = plan.search_seed("dev-000", 1)
    monkeypatch.setattr(plan, "SEARCH_SEED_TAG", "other")
    assert plan.search_seed("dev-000", 1) != before


def test_the_search_seed_itself_does_not_move() -> None:
    # Pinned from hashlib: the digests moved, the streams did not.
    key = hashlib.blake2b(b"dev-000|1|search", digest_size=8).digest()
    assert plan.search_seed("dev-000", 1) == int.from_bytes(key, "big") % 2**63


@dataclasses.dataclass(frozen=True)
class _Value:
    x: float = 0.25


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (1.5, "1.5"),
        (1_000, "1000"),
        ("a", "'a'"),
        ((1, (2.0, "b")), "(1, (2.0, 'b'))"),
        ({"b": 1, "a": 2}, "{'a': 2, 'b': 1}"),
        (frozenset({"b", "a"}), "{'a', 'b'}"),
        (ValueError, "<class 'ValueError'>"),
    ],
)
def test_render_is_canonical(value: Any, text: str) -> None:
    assert render(value) == text


def test_render_reads_a_dataclass() -> None:
    assert render(_Value()).endswith("_Value(x=0.25)")


@pytest.mark.parametrize("value", [object(), np.zeros(2), {1, object()}])
def test_render_refuses_what_is_not_a_stable_value(value: Any) -> None:
    with pytest.raises(AddressTextError):
        render(value)


# --------------------------------------------------------------------------
# The domain's text
# --------------------------------------------------------------------------


def test_the_environment_version_names_the_domain() -> None:
    env, config = environment(), tiny_config()
    key = baseline_key(Unit("hawkes", 1, "ORACLE"), entry("hawkes"), env, config)
    digest = hashlib.sha256(env.domain_text.encode("utf-8")).hexdigest()
    assert str(key.env_version) == f"{env.name}|domain:{digest}|{FIT_VERSION}"


def test_the_fitter_is_still_the_last_field() -> None:
    key = baseline_key(
        Unit("hawkes", 1, "ORACLE"), entry("hawkes"), environment(), tiny_config()
    )
    assert str(key.env_version).split("|")[-1] == FIT_VERSION
    assert matches_env(str(key.env_version), "fit/5")
    assert matches_env(str(key.env_version), FIT_VERSION)
    assert not matches_env(str(key.env_version), "fit/4")


def test_the_domains_text_moves_every_kind_of_key() -> None:
    env, truth = environment(), entry("hawkes")
    other = dataclasses.replace(env, domain_text=env.domain_text + "; changed")
    arm = LLMArm("AG-c", "named")
    config = tiny_config(llm_arms=(arm,))
    unit = Unit("hawkes", 1, arm.system("H"))
    assert baseline_key(
        Unit("hawkes", 1, "ORACLE"), truth, env, config
    ) != baseline_key(Unit("hawkes", 1, "ORACLE"), truth, other, config)
    a = session_key(unit, truth, env, config)
    b = session_key(unit, truth, other, config)
    assert a != b
    assert score_key(unit, a, env, config) != score_key(unit, a, other, config)

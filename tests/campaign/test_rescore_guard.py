"""Rescore mode launches nothing, with every launch path blocked (defect hunt B,
property 1; QUEUE "hunt-check leftovers (d)").

``test_campaign.py``'s rescore test replaces the driver's two session
functions. Here every layer a live session passes through is replaced by a
recorder that raises :class:`LaunchAttempt`, a ``BaseException`` subclass, so
no ``except Exception`` on the way (the harness has one at its SDK seam) can
turn an attempt into an ordinary failed or retried unit. The layers, outermost
first, for the two ways the driver launches a session:

- isolated (the live default, ``isolate_llm=True``): the driver's
  ``run_session_isolated``, then the ``spawn`` process start, past which no
  in-process patch reaches;
- in-thread: the driver's ``run_session``, ``llm.run``,
  ``runner.run_investigation``, ``live.arun_investigation``,
  ``live.sdk_driver``, ``live.ClaudeSDKClient``, and ``anyio.open_process``,
  where the SDK starts the CLI process.

Each name is patched where it is looked up at call time (``llm`` binds
``runner.run`` as ``run``, ``runner`` binds ``live.run_investigation``), so the
definitions' own module attributes and the SDK's package entry points
(``claude_agent_sdk.query``, used by the v1 provider) are patched as well.

The environment is made unable to reach the subscription too: ``PATH`` loses
every directory holding a ``claude`` executable, ``HOME`` points at an empty
directory (the SDK's fallback CLI locations and the CLI's credentials live
under it), and the contaminating variables plus ``CLAUDE_CONFIG_DIR`` are
unset. The PATH strip alone does not hide the CLI: the SDK runs the one bundled
in its wheel first. No API key is set, not even an invalid one (see
``guard``).
"""

from __future__ import annotations

import multiprocessing.context
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

import anyio
import claude_agent_sdk
import pytest
from campaign_support import FINGERPRINT, entry, environment, tiny_config
from test_campaign import SCRIPT, clean

from sciagent.campaign import driver as driver_module
from sciagent.campaign import llm as llm_module
from sciagent.campaign import plan
from sciagent.campaign.driver import LLM_DIR, Campaign, DriverSettings
from sciagent.campaign.execute import ScoreJob, UnitResult
from sciagent.campaign.plan import LLMArm, Unit, score_key, session_key
from sciagent.campaign.store import DETAILS_DIR
from sciagent.harness import live as live_module
from sciagent.harness.scripted import ScriptedDriver
from sciagent.investigation import runner as runner_module

TRUTH: Final = "size_excitation"
UNIT: Final = Unit(TRUTH, 1, "AG-c-H/named")


class LaunchAttempt(BaseException):
    """Raised by every patched launch path: no ``except Exception`` catches it."""


#: ``(label, owner, attribute)`` of the isolated path, outermost first.
ISOLATED: Final[tuple[tuple[str, object, str], ...]] = (
    ("driver.run_session_isolated", driver_module, "run_session_isolated"),
    ("SpawnProcess.start", multiprocessing.context.SpawnProcess, "start"),
)
#: The in-thread path, outermost first.
IN_THREAD: Final[tuple[tuple[str, object, str], ...]] = (
    ("driver.run_session", driver_module, "run_session"),
    ("llm.run", llm_module, "run"),
    ("runner.run_investigation", runner_module, "run_investigation"),
    ("live.arun_investigation", live_module, "arun_investigation"),
    ("live.sdk_driver", live_module, "sdk_driver"),
    ("live.ClaudeSDKClient", live_module, "ClaudeSDKClient"),
    ("anyio.open_process", anyio, "open_process"),
)
#: Patched too, though no campaign path looks them up there.
OFF_PATH: Final[tuple[tuple[str, object, str], ...]] = (
    ("llm.run_session", llm_module, "run_session"),
    ("llm.run_session_isolated", llm_module, "run_session_isolated"),
    ("runner.run", runner_module, "run"),
    ("live.run_investigation", live_module, "run_investigation"),
    ("claude_agent_sdk.query", claude_agent_sdk, "query"),
    ("claude_agent_sdk.ClaudeSDKClient", claude_agent_sdk, "ClaudeSDKClient"),
)
LAUNCH_PATHS: Final = ISOLATED + IN_THREAD + OFF_PATH


def recorder(label: str, calls: list[str]) -> Callable[..., Any]:
    def launch(*args: object, **kwargs: object) -> Any:
        calls.append(label)
        raise LaunchAttempt(label)

    return launch


def guard(
    monkeypatch: pytest.MonkeyPatch,
    home: Path,
    *,
    skip: frozenset[str] = frozenset(),
) -> list[str]:
    """Block every launch path except those in ``skip``; return the call log.

    Setting ``ANTHROPIC_API_KEY`` to an invalid value (the salvaged script did)
    adds nothing here: the harness refuses the variable before it reaches the
    SDK (``live._refuse_contaminated_environment``), so a launch attempt would
    end there as a configuration error, short of the inner recorders the
    positive control must reach, and the project never sets API keys.
    """
    clean(monkeypatch)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    kept = [
        d
        for d in os.environ.get("PATH", "").split(os.pathsep)
        if d and not os.access(Path(d) / "claude", os.X_OK)
    ]
    monkeypatch.setenv("PATH", os.pathsep.join(kept))
    calls: list[str] = []
    for label, owner, name in LAUNCH_PATHS:
        if label not in skip:
            monkeypatch.setattr(owner, name, recorder(label, calls))
    return calls


def _llm_files(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted((root / LLM_DIR).rglob("*"))
        if p.is_file()
    }


def _campaign(root: Path, **settings: Any) -> Campaign:
    config = tiny_config(seeds=(1,), llm_arms=(LLMArm("AG-c", "named"),))
    return Campaign(
        root,
        environment(),
        config,
        [entry(TRUTH)],
        DriverSettings(workers=0, poll_s=0.05, run_baselines=False, **settings),
        fingerprint=FINGERPRINT,
    )


def test_rescore_with_every_launch_path_blocked_scores_and_launches_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An old-fitter session is rescored under the guard: the score cell is
    written from the stored submission, no recorder is called, and ``llm/`` is
    byte-identical. Then the normal plan, under the same guard on the same
    store, does try to launch (its session has a new address), so the guard
    can fail."""
    store = tmp_path / "store"
    store.mkdir()
    scored: list[ScoreJob] = []

    def fake_score(job: ScoreJob) -> UnitResult:
        scored.append(job)
        return UnitResult(job.system, True, {"status": 1.0}, {"s": 1}, 0.0)

    monkeypatch.setattr(driver_module, "run_score_job", fake_score)
    config = tiny_config(seeds=(1,), llm_arms=(LLMArm("AG-c", "named"),))
    with monkeypatch.context() as old:
        clean(old)
        old.setattr(plan, "FIT_VERSION", "sciagent.glm.fit/OLD")
        first = _campaign(store, driver_factory=lambda u: ScriptedDriver(SCRIPT))
        assert first.run().bugs == 0
        first.close()
        old_skey = session_key(UNIT, entry(TRUTH), environment(), config)
    assert "fit/OLD" in str(old_skey.env_version) and len(scored) == 1
    llm_before = _llm_files(store)
    assert llm_before  # the session's record is there to be kept
    details_before = {p.name for p in (store / DETAILS_DIR).glob("*.json")}

    calls = guard(monkeypatch, tmp_path / "home")
    rescore = _campaign(store, rescore_llm=True)
    summary = rescore.run()
    new_ckey = score_key(UNIT, old_skey, environment(), config)
    assert rescore.store.done(new_ckey)
    rescore.close()
    assert calls == []
    assert summary.bugs == 0 and summary.retries == 0
    assert rescore.llm_launched == 0
    assert len(scored) == 2 and scored[1].submission == scored[0].submission
    assert _llm_files(store) == llm_before
    details_after = {p.name for p in (store / DETAILS_DIR).glob("*.json")}
    assert details_after - details_before == {f"{new_ckey.digest}.json"}

    # Positive control: the same guard, the normal plan.
    normal = _campaign(store)
    blocked = normal.run()
    normal.close()
    assert calls == ["driver.run_session_isolated"]
    assert blocked.bugs == 1 and normal.llm_launched == 1
    assert _llm_files(store) == llm_before


CONTROLS: Final = [
    pytest.param(path, k, id=path[k][0])
    for path in (ISOLATED, IN_THREAD)
    for k in range(len(path))
]


@pytest.mark.parametrize(("path", "k"), CONTROLS)
def test_each_blocked_layer_sits_on_the_live_launch_path(
    path: tuple[tuple[str, object, str], ...],
    k: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Positive control for each layer: with the layers outside it left real,
    the normal plan with no scripted driver (the live launch) is stopped by
    that layer's recorder and by no other. A patch off the live path would let
    the launch through to the next layer, and the deepest
    (``anyio.open_process``, ``SpawnProcess.start``) is where a process would
    start, so the test fails before anything runs."""
    label = path[k][0]
    calls = guard(
        monkeypatch, tmp_path / "home", skip=frozenset(p[0] for p in path[:k])
    )
    store = tmp_path / "store"
    store.mkdir()
    campaign = _campaign(store, isolate_llm=path is ISOLATED)
    summary = campaign.run()
    campaign.close()
    assert calls == [label]
    assert summary.bugs == 1 and not summary.recorded
    assert "LaunchAttempt" in (store / "progress.log").read_text(encoding="utf-8")

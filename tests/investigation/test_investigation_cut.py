"""A session's numbers are what its hash-chained record says (Invariants 2, 3).

The wall time cancels the await, not the worker thread running the tool. In
the pilot, session bd3dc4c4 (AG-o-S/named dev-012 s1, ``wall_time``) read
``fits_used`` 35 against 34 charged: a fit in flight at the cut finished while
``asyncio.run`` joined the executor, and the runner read the lab afterwards.

Each test here puts a tool call in flight when the session is cut and lets it
finish only after the record's ``outcome`` entry exists, the pilot's order.
That order is fixed by events, not by time: :class:`CutDriver` raises the
``TimeoutError`` that ``asyncio.timeout`` raises at the cut, once the call is
inside its handler, and the handler waits for the ``outcome`` entry. A tool
call with no ``tool_call`` entry did not happen, whatever the thread did.
"""

from __future__ import annotations

import asyncio
import shutil
import threading
from asyncio import constants as asyncio_constants
from collections.abc import AsyncGenerator, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    Message,
    ResultMessage,
    SdkMcpTool,
    ToolUseBlock,
)
from investigation_support import (
    BELIEF,
    MODEL,
    POINTPROC,
    SEED,
    STOP,
    clean_environment,
    plain_run,
)

from sciagent.glm.fit import fit as glm_fit
from sciagent.harness.record import SessionRecord
from sciagent.harness.scripted import ScriptedDriver, Step
from sciagent.harness.tools import ToolLayer, ToolOutput
from sciagent.investigation import InvestigationOutcome, replay_investigation, run
from sciagent.investigation import runner as runner_module
from sciagent.investigation import tools as tools_module
from sciagent.investigation.world import World
from sciagent.sandbox import Sandbox, SandboxUnavailableError

#: Long enough never to matter; a wait reaching it fails the test.
WAIT_S: Final = 30.0

FIT_A: Final = ("fit", {"structure": "Excite(ExpK, One, all)", "data_ids": ["obs"]})
FIT_B: Final = (
    "fit",
    {"structure": "Excite(ExpK, Mark(size), all)", "data_ids": ["obs"]},
)
SUBMIT: Final = (
    "submit",
    {"structure": "Excite(ExpK, Mark(size), all)", "report": "late", **STOP},
)
PREDICT_E0: Final = (
    "predict",
    {
        "experiment": 0,
        "diagnostic": "mean_rate",
        "args": {},
        "hypothesis": "Excite(ExpK, Mark(size), all)",
        "interval": [0.0, 1000.0],
        "level": 0.9,
    },
)


class Cut:
    """Holds a tool call inside its handler until the record's ``outcome``."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.armed = False
        self.entered = threading.Event()
        self.release = threading.Event()
        #: Released by each worker thread as it reaches the layer: past that
        #: point, cancelling its await cannot stop it. Before it, cancelling
        #: can (the executor drops work it has not started).
        self.calling = threading.Semaphore(0)
        append = SessionRecord.append
        call = ToolLayer.call

        def reaching(
            layer: ToolLayer, name: str, args: Mapping[str, Any]
        ) -> ToolOutput:
            self.calling.release()
            return call(layer, name, args)

        monkeypatch.setattr(ToolLayer, "call", reaching)

        def after_outcome(
            record: SessionRecord,
            kind: str,
            body: Mapping[str, Any],
            *,
            elapsed_s: float | None = None,
        ) -> str:
            head = append(record, kind, body, elapsed_s=elapsed_s)
            if kind == "outcome":
                self.release.set()
            return head

        monkeypatch.setattr(SessionRecord, "append", after_outcome)

    def hold(self) -> None:
        """Called from inside a handler: stay here if this is the in-flight call."""
        if self.armed:
            self.armed = False
            self.entered.set()
            assert self.release.wait(WAIT_S)


class CutDriver:
    """Plays ``script``, then one turn whose tool uses never get a result.

    The first of ``in_flight`` is inside its handler (held by ``cut``) and the
    rest are queued behind it on the layer's lock when the session is cut.
    """

    def __init__(
        self,
        script: Sequence[Step],
        in_flight: Sequence[tuple[str, Mapping[str, Any]]],
        cut: Cut,
    ) -> None:
        self.inner = ScriptedDriver(script)
        self.in_flight = tuple(in_flight)
        self.cut = cut

    def __call__(
        self,
        prompt: str,
        options: ClaudeAgentOptions,
        tools: Mapping[str, SdkMcpTool[Any]],
    ) -> AsyncGenerator[Message]:
        return self._play(prompt, options, tools)

    async def _play(
        self,
        prompt: str,
        options: ClaudeAgentOptions,
        tools: Mapping[str, SdkMcpTool[Any]],
    ) -> AsyncGenerator[Message]:
        model = options.model or ""
        async for message in self.inner(prompt, options, tools):
            if isinstance(message, ResultMessage):
                break
            yield message
        uses = [
            ToolUseBlock(
                id=f"toolu_cut_{k}", name=f"mcp__lab__{name}", input=dict(args)
            )
            for k, (name, args) in enumerate(self.in_flight)
        ]
        yield AssistantMessage(content=list(uses), model=model, stop_reason="tool_use")
        self.cut.armed = True
        self.cut.calling = threading.Semaphore(0)
        tasks: list[asyncio.Task[dict[str, Any]]] = []
        for use in uses:
            handler = tools[use.name].handler
            tasks.append(asyncio.ensure_future(handler(dict(use.input))))
            assert await asyncio.to_thread(self.cut.calling.acquire, True, WAIT_S)
            assert await asyncio.to_thread(self.cut.entered.wait, WAIT_S)
        try:
            raise TimeoutError  # what ``asyncio.timeout`` raises at the cut
        finally:
            for task in tasks:
                task.cancel()


def _run(
    tmp_path: Path, driver: CutDriver, *, arm: Any = "AG-c"
) -> InvestigationOutcome:
    return run(
        POINTPROC,
        "size_excitation",
        arm,
        "named",
        MODEL,
        SEED,
        tmp_path / "out",
        experiments=3,
        fits=4,
        max_turns=40,
        wall_time_s=600.0,
        driver=driver,
        sandbox_root=tmp_path / "sbx",
    )


def _hold_fits(monkeypatch: pytest.MonkeyPatch, cut: Cut) -> list[object]:
    """Hold the armed fit inside the handler; return what started, in order."""
    real = glm_fit
    started: list[object] = []

    def held(structure: Any, *args: Any, **kwargs: Any) -> Any:
        started.append(structure)
        cut.hold()
        return real(structure, *args, **kwargs)

    monkeypatch.setattr(tools_module, "fit", held)
    return started


def _charged(outcome: InvestigationOutcome, budget: str) -> int:
    record = SessionRecord.load(outcome.record_path)
    return sum(e.body["charged"] == budget for e in record.of_kind("tool_call"))


def _agrees_with_replay(outcome: InvestigationOutcome) -> None:
    """The live reading, the record and a replay of the record are one story."""
    record = SessionRecord.load(outcome.record_path)
    report = replay_investigation(record, POINTPROC)
    assert report.budgets_used["fits"] == outcome.fits_used
    assert report.budgets_used["experiments"] == outcome.experiments_used
    assert (report.submission is None) == (outcome.submission is None)
    assert outcome.fits_used == _charged(outcome, "fits")
    assert outcome.experiments_used == _charged(outcome, "experiments")


def test_a_fit_finishing_after_the_cut_is_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Session bd3dc4c4: fits_used 35 != 34 charged."""
    clean_environment(monkeypatch)
    cut = Cut(monkeypatch)
    _hold_fits(monkeypatch, cut)
    outcome = _run(tmp_path, CutDriver((Step(calls=(FIT_A,)),), (FIT_B,), cut))
    assert outcome.outcome == "wall_time"
    assert outcome.fits_used == 1
    _agrees_with_replay(outcome)


def test_a_call_queued_at_the_cut_never_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two parallel fits: the second waits on the layer's lock at the cut, and
    used to start once the first let go, with the session long over."""
    clean_environment(monkeypatch)
    cut = Cut(monkeypatch)
    started = _hold_fits(monkeypatch, cut)
    outcome = _run(tmp_path, CutDriver((), (FIT_A, FIT_B), cut))
    assert outcome.outcome == "wall_time"
    assert len(started) == 1
    assert outcome.fits_used == 0
    _agrees_with_replay(outcome)


def test_a_submit_queued_at_the_cut_is_no_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_environment(monkeypatch)
    cut = Cut(monkeypatch)
    _hold_fits(monkeypatch, cut)
    outcome = _run(tmp_path, CutDriver((Step(calls=(BELIEF,)),), (FIT_A, SUBMIT), cut))
    assert outcome.outcome == "wall_time"
    assert outcome.submission is None
    assert outcome.report is None
    record = SessionRecord.load(outcome.record_path)
    assert record.of_kind("outcome")[-1].body["submission"] is None
    _agrees_with_replay(outcome)


def test_a_submit_inside_its_handler_at_the_cut_is_no_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The handler sets the lab's submission before the layer logs the call."""
    clean_environment(monkeypatch)
    cut = Cut(monkeypatch)
    real = tools_module.Lab.submit

    def held(lab: tools_module.Lab, args: Mapping[str, Any]) -> Any:
        output = real(lab, args)
        cut.hold()
        return output

    monkeypatch.setattr(tools_module.Lab, "submit", held)
    outcome = _run(tmp_path, CutDriver((Step(calls=(BELIEF,)),), (SUBMIT,), cut))
    assert outcome.outcome == "wall_time"
    assert outcome.submission is None
    assert outcome.report is None
    _agrees_with_replay(outcome)


def test_an_experiment_that_ran_before_the_cut_but_never_returned_is_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The world's count moves inside the handler, before any cut: closing the
    layer alone cannot help here, so what is read must follow the commits."""
    clean_environment(monkeypatch)
    cut = Cut(monkeypatch)
    real = World.run

    def held(world: World, experiment: Any) -> Any:
        result = real(world, experiment)
        cut.hold()
        return result

    monkeypatch.setattr(World, "run", held)
    outcome = _run(
        tmp_path,
        CutDriver(
            (Step(calls=(PREDICT_E0,)), Step(calls=(BELIEF,))),
            (("run_experiment", plain_run(50.0)),),
            cut,
        ),
    )
    assert outcome.outcome == "wall_time"
    assert outcome.experiments_used == 0
    assert outcome.predictions == ()
    _agrees_with_replay(outcome)


def _open_lab_without_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner_module, "ensure_image", lambda: "sciagent-sandbox:test")
    monkeypatch.setattr(runner_module, "image_id", lambda *a, **k: "sha256:test")


def test_a_sandbox_fault_of_an_unrecorded_call_does_not_void_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_environment(monkeypatch)
    _open_lab_without_docker(monkeypatch)
    cut = Cut(monkeypatch)

    def failing(sandbox: Sandbox, code: str) -> Any:
        cut.hold()
        raise SandboxUnavailableError("docker went away")

    monkeypatch.setattr(Sandbox, "run", failing)
    outcome = _run(
        tmp_path, CutDriver((), (("python", {"code": "print(1)"}),), cut), arm="AG-o"
    )
    assert outcome.outcome == "wall_time"
    assert not outcome.void, outcome.void_reasons


def test_cleanup_leaves_a_sandbox_a_handler_is_still_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``asyncio.run`` stops joining the worker threads after a while; a tool
    still running then may write under the run directory, so removing it could
    fail and void the run over a call the record does not have. The session's
    owner recovers the directory once the process is gone."""
    clean_environment(monkeypatch)
    _open_lab_without_docker(monkeypatch)
    monkeypatch.setattr(asyncio_constants, "THREAD_JOIN_TIMEOUT", 0.01)
    monkeypatch.setattr(runner_module, "IDLE_WAIT_S", 0.0)
    cut = Cut(monkeypatch)
    still = threading.Event()
    real = glm_fit

    def held(*args: Any, **kwargs: Any) -> Any:
        if cut.armed:
            cut.armed = False
            cut.entered.set()
            assert still.wait(WAIT_S)
        return real(*args, **kwargs)

    monkeypatch.setattr(tools_module, "fit", held)
    removed: list[Path] = []
    monkeypatch.setattr(shutil, "rmtree", lambda path, **_: removed.append(Path(path)))
    try:
        outcome = _run(tmp_path, CutDriver((), (FIT_A,), cut), arm="AG-o")
    finally:
        still.set()
    assert outcome.outcome == "wall_time"
    assert removed == []
    assert not outcome.void, outcome.void_reasons

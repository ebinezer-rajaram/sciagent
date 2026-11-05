"""Address text read from the object that runs (F5 point 2; LOG 2026-10-27).

The part of F5 that moves no pilot address: B-rand, B-np and the B-sym policy
arms are built by one constructor shared by ``plan.py`` and ``execute.py``, and
their text reads that object's fields, so a change to a default there moves the
address instead of leaving a copied number behind. The text itself is
byte-identical to the pilot's (pinned below and in ``test_campaign_policy.py``).

The rest of point 2 moves every baseline and score address and is not built
(LOG 2026-11-05, "F5 stops at the byte-identical part").
"""

from __future__ import annotations

import dataclasses
import hashlib
from typing import Any, Final

import pytest
from campaign_support import entry, environment, tiny_config

from sciagent.campaign import execute, plan
from sciagent.campaign.execute import BaselineJob
from sciagent.campaign.plan import (
    B_SYM_EIG,
    B_SYM_RANDOM,
    POLICY_ARMS,
    system_config_text,
)
from sciagent.nonparam.wiener_hopf import DEFAULT, WienerHopfConfig
from sciagent.systems.v2.policy_search import PolicySearch
from sciagent.systems.v2.search import BRand
from sciagent.systems.v2.systems import BNp

#: ``system_config_text`` as the pilot's cells store it (tiny_config's budget).
B_RAND_TEXT: Final = "budget=3; max_draws_factor=100; prior=test-prior"
OTHER_WH: Final = dataclasses.replace(DEFAULT, max_lag=DEFAULT.max_lag * 2)
#: SHA-256 of each policy arm's text under ``tiny_config``, from the hand text
#: at b7ff29b (with C5's rule), before the shared constructor.
POLICY_TEXT_SHA256: Final = {
    "B-sym+random": "0d3723c1e6db3989dea137f58eebd1d82cf9efce3ec41358a12a036931dc39eb",
    "B-sym+EIG": "d8d7678d35fe62426928bd925866824dc163cf8c5973c05cc3aa02c0e35ee9b1",
    "B-sym+FSD": "2c5404084a302f4448968882f028bfbb2a9138b2d5153caeabf2c9753a8e9c57",
    "B-sym+truthFSD": (
        "d6aa0db93ad645fda087021ed0f5b487a845818aedff9ca9c6620cb3bf2192be"
    ),
}


class Reached(Exception):
    """The unit got as far as running the object under test."""


def _forbidden(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("execute built its own, not the shared constructor's")


def test_the_texts_are_the_pilots_byte_for_byte() -> None:
    env, config = environment(), tiny_config()
    assert system_config_text("B-rand", env, config) == B_RAND_TEXT
    assert system_config_text("B-np", env, config) == f"wiener_hopf: {DEFAULT!r}"


def test_b_rands_text_reads_the_system_that_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def other(*args: Any, **kwargs: Any) -> BRand:
        return dataclasses.replace(BRand(*args, **kwargs), max_draws_factor=7)

    monkeypatch.setattr(plan, "BRand", other, raising=False)
    text = system_config_text("B-rand", environment(), tiny_config())
    assert "max_draws_factor=7" in text


def test_b_nps_text_reads_the_system_that_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def other() -> BNp:
        return BNp(config=OTHER_WH)

    monkeypatch.setattr(plan, "BNp", other, raising=False)
    text = system_config_text("B-np", environment(), tiny_config())
    assert text == f"wiener_hopf: {OTHER_WH!r}"


def test_a_policy_arms_text_reads_the_search_that_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def other(*args: Any, **kwargs: Any) -> PolicySearch:
        return dataclasses.replace(PolicySearch(*args, **kwargs), experiments=99)

    monkeypatch.setattr(plan, "PolicySearch", other, raising=False)
    text = system_config_text(B_SYM_RANDOM, environment(), tiny_config())
    assert "experiments=99;" in text


@pytest.mark.parametrize("system", POLICY_ARMS)
def test_a_policy_arms_text_is_unmoved_by_the_shared_constructor(system: str) -> None:
    text = system_config_text(system, environment(), tiny_config())
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert digest == POLICY_TEXT_SHA256[system]


def test_the_entertained_size_is_read_from_the_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def other(*args: Any, **kwargs: Any) -> PolicySearch:
        return dataclasses.replace(PolicySearch(*args, **kwargs), entertained=1)

    monkeypatch.setattr(plan, "PolicySearch", other, raising=False)
    text = system_config_text(B_SYM_EIG, environment(), tiny_config())
    assert "entertained=top 1 of epoch 1" in text


def _job(system: str) -> BaselineJob:
    return BaselineJob(entry("hawkes"), 1, (system,), environment(), tiny_config())


def test_b_rand_runs_the_constructors_object(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[BRand] = []

    class Spy(BRand):
        def run(self, data: Any) -> Any:
            seen.append(self)
            raise Reached

    monkeypatch.setattr(plan, "BRand", Spy, raising=False)
    monkeypatch.setattr(execute, "BRand", _forbidden, raising=False)
    job = _job("B-rand")
    with pytest.raises(Reached):
        execute._run_system(
            "B-rand", job, execute.observational_data(job.truth, 1, job.env)
        )
    (system,) = seen
    assert system.budget == job.config.fits
    assert system.seed == plan.search_seed("hawkes", 1)
    assert system.fit_config == job.config.fit_config
    assert system.prior == job.env.prior


def test_b_np_runs_the_constructors_object(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[WienerHopfConfig] = []

    class Spy(BNp):
        def run(self, data: Any) -> Any:
            seen.append(self.config)
            raise Reached

    monkeypatch.setattr(plan, "BNp", Spy, raising=False)
    monkeypatch.setattr(execute, "BNp", _forbidden, raising=False)
    job = _job("B-np")
    with pytest.raises(Reached):
        execute._run_system(
            "B-np", job, execute.observational_data(job.truth, 1, job.env)
        )
    assert seen == [DEFAULT]


def test_a_policy_unit_runs_the_constructors_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[PolicySearch] = []

    class Spy(PolicySearch):
        def run(self, data: Any, runner: Any) -> Any:
            seen.append(self)
            raise Reached

    monkeypatch.setattr(plan, "PolicySearch", Spy, raising=False)
    monkeypatch.setattr(execute, "PolicySearch", _forbidden, raising=False)
    job = _job(B_SYM_RANDOM)
    held: Any = None  # read only after the search has run
    with pytest.raises(Reached):
        execute._policy_unit(B_SYM_RANDOM, job, held, 0.0, None)
    (search,) = seen
    assert search.budget == job.config.fits
    assert search.experiments == job.config.experiments
    assert search.seed == plan.search_seed("hawkes", 1)
    assert search.name == B_SYM_RANDOM
    assert search.entertained == job.config.policy.entertained

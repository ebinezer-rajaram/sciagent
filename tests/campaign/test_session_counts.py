"""Turns and tokens are the SDK's ``result`` figures, or they are missing.

A session cut at its wall time has no ``result`` entry. Its reading used to
hold 0 for turns and every token count, which reads as a session that cost
nothing (LOG 2026-10-29, session 24). They are left out instead: an absent
reading field says "not recorded", as an absent ``harness_wall_s`` does. They
are not counted from the record's assistant entries, which are per content
block and carry the usage of a partly streamed message, so such a count would
be another measurement under the same name.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest
from campaign_support import entry, environment, tiny_config
from test_campaign import SCRIPT, clean

from sciagent.campaign.llm import run_session
from sciagent.campaign.plan import Unit
from sciagent.harness.scripted import ScriptedDriver, Step

#: The reading fields only an SDK ``result`` message can give.
RESULT_FIELDS: Final = (
    "turns",
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)
UNIT: Final = Unit("size_excitation", 1, "AG-c-H/named")


def test_a_session_with_no_result_has_no_turns_or_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean(monkeypatch)
    out = run_session(
        UNIT,
        entry("size_excitation"),
        environment(),
        tiny_config(seeds=(1,), wall_time_s=0.2),
        tmp_path,
        driver=ScriptedDriver((Step(text="thinking", delay_s=30.0),)),
    )
    assert out.detail["outcome"] == "wall_time"
    assert not set(RESULT_FIELDS) & set(out.reading)
    # What the record does say stays: a count of nothing is a zero, not a gap.
    assert out.reading["fits_used"] == 0.0
    assert out.reading["experiments_used"] == 0.0
    assert out.reading["tool_calls"] == 0.0


def test_a_session_with_a_result_keeps_its_fields_and_their_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean(monkeypatch)
    out = run_session(
        UNIT,
        entry("size_excitation"),
        environment(),
        tiny_config(seeds=(1,)),
        tmp_path,
        driver=ScriptedDriver(SCRIPT),
    )
    assert out.detail["outcome"] == "submitted"
    assert tuple(out.reading) == (
        "status",
        "outcome",
        "submitted",
        "void",
        "unknown_tool_attempts",
        "turns",
        "fits_used",
        "experiments_used",
        "tool_calls",
        "predictions",
        "predictions_evaluated",
        "predictions_covered",
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    )
    assert out.reading["turns"] > 0.0
    assert out.reading["output_tokens"] > 0.0
    # Zero stays a value where a result gave it.
    assert out.reading["cache_read_input_tokens"] == 0.0

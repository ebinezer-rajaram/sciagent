"""The value constants on a cell's path, and the digests that address them (F5).

``METRIC_VERSION`` and ``DATA_VERSION`` (:mod:`sciagent.campaign.plan`) used to
name a few constants by hand, so a change to the rest reused stored cells under
an unchanged key (LOG 2026-10-27, "F5, stale-cell reuse"). Each now ends with the
SHA-256 of a canonical rendering of every constant in its table:

- :data:`DATA_CONSTANTS`: what fixes the data a unit sees, the world's log and
  the held-out log. These are in every key, sessions included;
- :data:`METRIC_CONSTANTS` and :data:`METRIC_LITERALS`: what the systems and the
  scorer read. Sessions carry no metric version, so a change here rescores and
  never reruns a model.

**Completeness.** ``tests/campaign/test_address_constants.py`` walks the AST of
``scoring/``, ``systems/v2/``, ``library/``, ``investigation/world.py``,
``campaign/execute.py`` and ``campaign/plan.py``. Every upper-case module
constant, and every number in a body, a parameter default or a field default
other than :data:`TRIVIAL_LITERALS`, must be in a table here or in
:data:`EXEMPT_CONSTANTS` or :data:`EXEMPT_LITERALS` with its reason. A new one
fails that test until someone says which it is.

**What this does not see.** Behaviour written as code rather than as a constant,
which still needs the labels (``score/2``, ``world/1``, ``FIT_VERSION``); the
trivial numbers; strings other than module constants; and code outside those
files (``glm/`` has ``FIT_VERSION``, the rest has nothing).

**Rendering.** :func:`render` gives each value one text on every machine: sets
and dicts sorted, and anything whose ``repr`` is not a stable value refused.
Arrays are refused too: an array computed at import has per-machine bytes, and
the constants it comes from are what is addressed.
"""

from __future__ import annotations

import hashlib
import importlib
from collections.abc import Mapping
from typing import Any, Final

import numpy as np

from sciagent.core.errors import SciAgentError

#: The failures recorded as a unit's result (``campaign/execute.py``): they
#: decide which failure is a stored cell and which is a bug. Here because the
#: digest is computed while ``plan`` imports, before ``execute`` can be.
UNIT_FAILURES: Final = (SciAgentError, ValueError, ArithmeticError)

#: Numbers the AST test ignores: arithmetic rather than settings.
TRIVIAL_LITERALS: Final = frozenset({-1, 0, 1, 2, 0.5})

_LABEL: Final = "a name: no number is computed from it"
_READ_TIME: Final = (
    "read when a report is made, after the cell: a change rereads the store and "
    "recomputes no cell"
)
_DERIVED: Final = (
    "computed at import from addressed constants; its bytes are per machine class"
)
_IN_TEXT: Final = "enters the unit's configuration text through the object's repr"
_RANGE_CHECK: Final = "an input check: it refuses, and changes no value"

DATA_CONSTANTS: Final[dict[str, tuple[str, ...]]] = {
    "sciagent.investigation.world": (
        "OBSERVATIONAL",
        "OBSERVATIONAL_HORIZON",
        "MAX_EVENTS",
        "_STREAM_OBSERVATIONAL",
        "_STREAM_EXPERIMENT",
    ),
    "sciagent.scoring.heldout": ("HELDOUT_HORIZON", "HELDOUT_LABEL"),
    "sciagent.scoring.seeds": ("HELDOUT",),
    "sciagent.scoring.truth": ("TRUTH_MAX_EVENTS",),
}

METRIC_CONSTANTS: Final[dict[str, tuple[str, ...]]] = {
    "sciagent.library.base": ("MAX_EVENTS",),
    "sciagent.library.mixture": (
        "NAME",
        "N_PARAMS",
        "_WEIGHT_STARTS",
        "_RATIO_STARTS",
        "_LOG_LO",
        "_LOG_HI",
        "_LOGIT_BOX",
    ),
    "sciagent.library.mmpp": (
        "NAME",
        "N_PARAMS",
        "_RATE_STARTS",
        "_SWITCH_STARTS",
        "_HIGH_SHARE",
        "_LOG_LO",
        "_LOG_HI",
    ),
    "sciagent.scoring.battery": (
        "BATTERY_HORIZON",
        "REPLICATES",
        "BATTERY_MAX_EVENTS",
        "OBSERVATIONAL_SEGMENT",
        "INTERVENTION_WINDOW",
        "POST_BURST",
        "RELEASE",
        "EARLY_AFTER",
        "AFTER",
        "BURST_SIZE",
        "BURST_SPACING",
        "CLAMP_RATE",
        "FANO_WINDOW",
        "LOW_DIVISOR",
        "_FLOOR",
        "_RATE",
        "_FANO",
        "_MARK_GAP",
    ),
    "sciagent.scoring.seeds": (
        "BATTERY",
        "POLICY",
        "TELLING",
        "POLICY_SEED_RULE",
        "SEARCH_SEED_TAG",
        "_WORD",
    ),
    "sciagent.scoring.structure": ("MAX_DISTANCE",),
    "sciagent.scoring.telling": ("TELLING_REPLICATES",),
    "sciagent.systems.v2.policies": (
        "POLICY_HORIZON",
        "REPLICATES",
        "ENTERTAINED_MAX",
        "POLICY_MAX_EVENTS",
        "GL_NODES",
        "PANEL_STEP",
        "_RANGE",
        "EIG_QUADRATURE",
        "EIG_QUADRATURE_ERROR",
        "EIG_TIE_TOL",
        "REFERENCE_INDEX",
        "RANDOM_INDEX",
        "NULL_INDEX",
        "PARAMETRIC_INDEX",
        "_FLOOR",
        "BIC_WEIGHTS",
        "STOPPED",
    ),
    "sciagent.systems.v2.policy_search": ("EPOCH_TWO",),
    "sciagent.systems.v2.search": ("_DEFAULT_EXCITE", "DEFAULT_MUTATION_WEIGHTS"),
    "sciagent.systems.v2.sparse": ("TRAIN_FRACTION",),
    "sciagent.systems.v2.sparse_dictionary": ("VAR_REL_TOL", "_CHUNK"),
    "sciagent.systems.v2.sparse_solver": (
        "_SERIES",
        "_ARMIJO",
        "_MIN_STEP",
        "_EIG_CUTOFF",
        "_STALL",
        "_CROSS_TOL",
        "_MAX_STALLS",
    ),
    "sciagent.systems.v2.systems": ("UNDERFIT_TOL_REL",),
}

#: Numbers written in a body or as a default: module -> qualified name -> values.
METRIC_LITERALS: Final[dict[str, dict[str, tuple[float, ...]]]] = {
    "sciagent.campaign.plan": {"search_seed": (63,)},
    "sciagent.library.base": {
        "multistart": (1e-13, 1e-09, 500),
        "multistart.safe": (1e300,),
    },
    "sciagent.library.mmpp": {"_params": (3,), "MMPP2.fit": (4,)},
    "sciagent.scoring.battery": {"hellinger2": (4.0,)},
    "sciagent.scoring.seeds": {"scorer_seed": (32,), "truth_policy_seed": (63,)},
    "sciagent.systems.v2.policies": {"_check_weights": (1e-09,), "fsd_null": (8,)},
    "sciagent.systems.v2.sparse_dictionary": {"_compute_leaves": (8,)},
    "sciagent.systems.v2.sparse_solver": {
        "_psd_solve": (1e-300,),
        "solve_group_lasso": (1e-08, 0.01, 100, 2000),
    },
    "sciagent.systems.v2.systems": {"BLib.run": (3,)},
}

EXEMPT_CONSTANTS: Final[dict[str, dict[str, str]]] = {
    "sciagent.campaign.execute": {
        "UNIT_FAILURES": "this module's UNIT_FAILURES, which the digest renders",
        "_KNOWN": _LABEL,
    },
    "sciagent.campaign.plan": {
        **dict.fromkeys(
            (
                "ORACLE",
                "B_LIB",
                "B_NP",
                "B_RAND",
                "B_SYM",
                "B_SPARSE",
                "PLANTED",
                "B_SYM_10F",
                "BASELINES",
                "B_SYM_RANDOM",
                "B_SYM_EIG",
                "B_SYM_FSD",
                "B_SYM_TRUTH_FSD",
                "TELLING",
                "ARM_POLICIES",
                "POLICY_ARMS",
                "POLICY_UNITS",
                "KNOWN_BASELINES",
            ),
            _LABEL,
        ),
        "METRIC_VERSION": "the version itself",
        "DATA_VERSION": "the version itself",
        "_NO_STRUCTURE": "ORACLE's text's placeholder for the truth's structure",
        "_POLICY_NAME": "an input check on a name",
        "PILOT_ARMS": "arms enter a session's key by their own fields",
        "P4_ARMS": "arms enter a session's key by their own fields",
        "P4_PROMPT_VARIANT": "enters a session's key through its arm and surface",
        "LEGACY_HARNESS_VERSION": "the pilot's frozen session text; do not edit",
    },
    "sciagent.scoring.behaviour": {
        "CONVERGENCE_TOLERANCE": _READ_TIME,
        "REVISION_K": _READ_TIME,
    },
    "sciagent.scoring.controls_p4": dict.fromkeys(
        (
            "SCRIPT_HORIZON",
            "NAMING_REFERENCE",
            "NAMING_CONDITIONS",
            "PLANTED_FITS",
            "CONTROL_5_ALPHA",
            "BEHAVIOUR_EXPERIMENTS",
            "MISS",
            "HIT",
            "BEHAVIOUR_GAPS",
            "FLOAT_TOLERANCE",
        ),
        _READ_TIME,
    ),
    "sciagent.scoring.gonogo": {
        **dict.fromkeys(
            ("ORACLE", "B_LIB", "B_RAND", "B_SPARSE", "B_SYM_10F", "PLANTED"), _LABEL
        ),
        "REQUIRED": _LABEL,
        **dict.fromkeys(
            (
                "RECOVERY_TOLERANCE",
                "CLOSE_FRACTION",
                "BSYM_TOLERANCE",
                "TOO_EASY_SHARE",
                "TOO_HARD_SHARE",
                "CONTROL_2_ALPHA",
            ),
            _READ_TIME,
        ),
    },
    "sciagent.scoring.stats": {
        "DEFAULT_RESAMPLES": _READ_TIME,
        "DEFAULT_LEVEL": _READ_TIME,
    },
    "sciagent.systems.v2.models": {"NONPARAM_NAME": _LABEL},
    "sciagent.systems.v2.policies": dict.fromkeys(
        ("_GL_X", "_GL_W", "_GL_NODES", "_GL_WEIGHTS", "_STEPS", "_LOG_SQRT_2PI"),
        _DERIVED,
    ),
    "sciagent.systems.v2.search": dict.fromkeys(
        ("B_RAND", "B_SYM", "PLANTED_HINT"), _LABEL
    ),
    "sciagent.systems.v2.sparse": {"B_SPARSE": _LABEL},
    "sciagent.systems.v2.sparse_dictionary": {
        "_WORKER": "a process's worker state, not a value",
    },
    "sciagent.systems.v2.systems": dict.fromkeys(("B_LIB", "B_NP", "ORACLE"), _LABEL),
}

EXEMPT_LITERALS: Final[dict[str, dict[str, str]]] = {
    "sciagent.campaign.execute": {
        "_failure": "the length a failure's message is cut to: text, not a number",
    },
    "sciagent.campaign.plan": {
        "CampaignConfig": "each field is addressed in the units that read it",
    },
    "sciagent.investigation.world": {"World.__init__": _RANGE_CHECK},
    "sciagent.scoring.controls_p4": dict.fromkeys(
        (
            "policy_separation",
            "BehaviourHypotheses.__post_init__",
            "behaviour_script",
            "expected_behaviour",
            "behaviour_control",
        ),
        _READ_TIME,
    ),
    "sciagent.scoring.seeds": {"_check_seed": _RANGE_CHECK},
    "sciagent.systems.v2.policy_search": {"PolicySearch": _IN_TEXT},
    "sciagent.systems.v2.search": dict.fromkeys(
        ("BRand", "GPConfig", "BSym", "PlantedHint"), _IN_TEXT
    ),
    "sciagent.systems.v2.sparse": {"SparseConfig": _IN_TEXT},
}


class AddressTextError(SciAgentError):
    """A value has no rendering that is the same on every machine."""


def render(value: Any) -> str:
    """One text for ``value`` on every machine (module docstring)."""
    if isinstance(value, np.ndarray):
        raise AddressTextError(f"an array has per-machine bytes: {value!r}")
    if isinstance(value, tuple | list):
        inner = ", ".join(render(v) for v in value)
        return f"({inner},)" if len(value) == 1 else f"({inner})"
    if isinstance(value, set | frozenset):
        return "{" + ", ".join(sorted(render(v) for v in value)) + "}"
    if isinstance(value, Mapping):
        items = sorted((render(k), render(v)) for k, v in value.items())
        return "{" + ", ".join(f"{k}: {v}" for k, v in items) + "}"
    text = repr(value)
    if " at 0x" in text:
        raise AddressTextError(f"not a stable value: {text}")
    return text


def _constant_lines(table: Mapping[str, tuple[str, ...]]) -> list[str]:
    lines: list[str] = []
    for module, names in table.items():
        loaded = importlib.import_module(module)
        lines += [f"{module}.{name}={render(getattr(loaded, name))}" for name in names]
    return lines


def _digest(lines: list[str]) -> str:
    text = "\n".join(sorted(lines))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def metric_digest() -> str:
    """SHA-256 of what the systems and the scorer read (module docstring)."""
    literals = [
        f"{module}.{name}:{render(values)}"
        for module, table in METRIC_LITERALS.items()
        for name, values in table.items()
    ]
    failures = f"UNIT_FAILURES={render(UNIT_FAILURES)}"
    return _digest([*_constant_lines(METRIC_CONSTANTS), *literals, failures])


def data_digest() -> str:
    """SHA-256 of what fixes the data a unit sees (module docstring)."""
    return _digest(_constant_lines(DATA_CONSTANTS))

"""Defect-hunt checks on a campaign store: the gap to ORACLE, store consistency,
and a cell-by-cell byte comparison of two stores.

These read the numbers the evaluation reports, so they are instruments (SPEC
§6.3). They rebuild the hunt scripts of 2026-10-06 (HANDOFF-2026-10-06, LOG
2026-10-06 "P3.1 fixes after defect hunt B"), which were lost with the Windows
PC; the scripts under ``scripts/`` are thin command lines over this module.

**Gap check** (:func:`gap_check`; the mini-pilot gate's "0 ``oracle_underfit``,
no gap above +0.01", HANDOFF-2026-10-06 step 5; PILOT-AUDIT §1). Cells are
found by *enumeration*, not by a plan: every committed ``baseline`` and
``llm-score`` cell whose ``env_version`` names the requested fitter (``fit/5``
matches ``<env>|sciagent.glm.fit/5``). They are grouped into units of one
(env_version, fit configuration, data version, metric version, truth, seed),
so cells under two configurations are never paired. Within a group, gaps are
derived by :func:`sciagent.campaign.analysis.derive` (``gap = per_event -
per_event[ORACLE]``), the same code the report uses, so the formula is not
restated here. The gate passes only if

- no unit's gap is above the threshold (+0.01 nats/event by default);
- no submitted unit lacks a gap (no ORACLE cell, a failed ORACLE, or an ORACLE
  at ``-inf``: a gap that cannot be computed cannot be shown to be small);
- control 1's underfit half passes over the ORACLE units
  (:func:`sciagent.campaign.analysis.underfit_check`: zero flags, zero
  unrecorded, at least one checked).

Void LLM sessions are not dropped here: the check is of ORACLE as an upper
bound, and a void session's score is still a fit through the same fitter.

**Store consistency** (:func:`check_store`; hunt B's "store append-only
(triggers)" and the store's own contract, :mod:`sciagent.campaign.store`).
Issues fail the check; warnings do not.

- the ledger file exists and carries the three append-only triggers verbatim;
- every ledger row's stored digest is its key's digest, and its stored reading
  digest is its reading's digest (content addressing of the ledger);
- every ledger row has a detail file whose name, ``digest`` field and key all
  give that digest, whose schema is the current one, whose reading equals the
  ledger's bit for bit, and whose bytes are exactly
  :func:`~sciagent.campaign.store.cell_bytes` of its own content (canonical);
- the address text is derived: a baseline's ``system_config`` and a session's
  ``session_config`` hash to the key's field, and the ``unit`` block agrees
  with the key (truth, seed, system, kind);
- references resolve: an ``llm-score`` cell's ``session`` is a committed
  ``llm-session`` cell; a session's ``record_dir`` is a directory under the
  root and holds its ``record`` file; no two sessions share a record dir;
- warnings: a detail file without a ledger row (a crash between the two
  writes; :meth:`~sciagent.campaign.store.ResultStore.recover` commits it), a
  stray file in ``details/``, a timing line naming a digest the ledger lacks.
  A timing line that is not JSON is an issue.

**Byte comparison** (:func:`compare_stores`; HANDOFF-2026-10-06 step 1,
"compare its cell bytes with run 1"). The committed digests of two stores are
compared as sets, and every common digest's detail file and ledger reading
must be byte-identical. ``timing.jsonl`` is not compared: it holds wall-clock
times by design (store docstring). The comparison can be restricted to some
kinds of cell: an ``llm-session`` cell is not reproducible by design (a live
model; its reading also carries the session's ``wall_s``), so two runs that
each conducted their own sessions compare on ``baseline`` and ``llm-score``.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from sciagent.campaign.analysis import Row, derive, row_from_cell, underfit_check
from sciagent.campaign.plan import ORACLE, CampaignError
from sciagent.campaign.store import (
    DETAIL_SCHEMA,
    DETAILS_DIR,
    LEDGER_FILE,
    TIMING_FILE,
    ResultStore,
    cell_bytes,
    key_from_json,
)
from sciagent.registry.backing import append_only_triggers
from sciagent.registry.ledger import LedgerEntry
from sciagent.registry.store import ExperimentKey
from sciagent.scoring.gonogo import UnderfitCheck

type Json = Any

#: The mini-pilot gate's gap threshold, nats per held-out event (HANDOFF step 5).
GAP_THRESHOLD: Final = 0.01
#: The ledger's table (``sciagent.registry.ledger``); a fresh store passing
#: :func:`check_store` pins it.
LEDGER_TABLE: Final = "cells"
SCORED_KINDS: Final = ("baseline", "llm-score")
CELL_KINDS: Final = ("baseline", "llm-session", "llm-score")
_DIGEST: Final = re.compile(r"[0-9a-f]{64}")


def _ro(path: Path) -> sqlite3.Connection:
    """A read-only connection: a check must never create or migrate a ledger."""
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _require_store(root: Path) -> Path:
    path = root / LEDGER_FILE
    if not path.is_file():
        raise CampaignError(f"{root} is not a campaign store: no {LEDGER_FILE}")
    return path


# --------------------------------------------------------------------------
# Gap check
# --------------------------------------------------------------------------


def matches_env(env_version: str, env: str) -> bool:
    """Whether a key's ``env_version`` (``<env name>|<FIT_VERSION>``) names
    ``env``: the whole text, its fitter part, or the fitter's last dotted
    component (``fit/5`` for ``sciagent.glm.fit/5``)."""
    fitter = env_version.split("|")[-1]
    return env in (env_version, fitter) or fitter.endswith("." + env)


@dataclass(frozen=True)
class GapUnit:
    """One scored unit and its gap to the ORACLE of the same group."""

    truth: str
    seed: int
    system: str
    llm: bool
    ok: bool
    per_event: float | None
    oracle: float | None
    gap: float | None
    oracle_underfit: bool | None


@dataclass(frozen=True)
class GapReport:
    env: str
    threshold: float
    units: tuple[GapUnit, ...]
    underfit: UnderfitCheck

    @property
    def above(self) -> tuple[GapUnit, ...]:
        """Units whose gap exceeds the threshold."""
        return tuple(
            u for u in self.units if u.gap is not None and u.gap > self.threshold
        )

    @property
    def unreferenced(self) -> tuple[GapUnit, ...]:
        """Submitted units without a gap (no finite ORACLE on their group)."""
        return tuple(
            u for u in self.units if u.ok and u.per_event is not None and u.gap is None
        )

    @property
    def passed(self) -> bool:
        return (
            bool(self.units)
            and not self.above
            and not self.unreferenced
            and self.underfit.passed
        )


def _group(key: ExperimentKey) -> tuple[str, str, str, str, str, int]:
    return (
        str(key.env_version),
        key.config.get("fit_config", ""),
        str(key.data_version),
        str(key.metric_version),
        key.config["truth"],
        int(key.seed),
    )


def gap_check(
    store: ResultStore, env: str, *, threshold: float = GAP_THRESHOLD
) -> GapReport:
    """Per unit, the gap of each system to ORACLE (module docstring)."""
    groups: dict[tuple[str, str, str, str, str, int], list[Row]] = {}
    for cell in store.cells():
        key = key_from_json(cell["key"])
        kind = key.config.get("kind")
        if kind not in SCORED_KINDS or not matches_env(str(key.env_version), env):
            continue
        g = _group(key)
        system = key.config["system"]
        rows = groups.setdefault(g, [])
        if any(r.system == system for r in rows):
            raise CampaignError(
                f"two {system} cells for {g[4]} s{g[5]} under one configuration "
                f"({cell['digest']}); select a single run's store"
            )
        rows.append(row_from_cell(g[4], g[5], system, kind == "llm-score", cell))
    derived: list[Row] = []
    units: list[GapUnit] = []
    for g in sorted(groups):
        rows = derive(groups[g])
        derived += rows
        oracle = next(
            (r.per_event for r in rows if r.system == ORACLE and not r.llm), None
        )
        units += [
            GapUnit(
                truth=r.truth,
                seed=r.seed,
                system=r.system,
                llm=r.llm,
                ok=r.ok,
                per_event=r.per_event,
                oracle=oracle,
                gap=r.gap,
                oracle_underfit=r.oracle_underfit,
            )
            for r in rows
        ]
    units.sort(key=lambda u: (u.truth, u.seed, u.system))
    return GapReport(env, threshold, tuple(units), underfit_check(derived))


# --------------------------------------------------------------------------
# Store consistency
# --------------------------------------------------------------------------


@dataclass(frozen=True, order=True)
class Finding:
    """One problem found in a store: what kind, which cell (or file), why."""

    kind: str
    where: str
    message: str


@dataclass(frozen=True)
class StoreCheck:
    cells: int
    issues: tuple[Finding, ...]
    warnings: tuple[Finding, ...]

    @property
    def passed(self) -> bool:
        return not self.issues


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _expected_triggers() -> dict[str, str]:
    out: dict[str, str] = {}
    for statement in append_only_triggers(LEDGER_TABLE):
        sql = statement.replace("CREATE TRIGGER IF NOT EXISTS ", "CREATE TRIGGER ", 1)
        out[sql.split()[2]] = sql
    return out


def _check_triggers(ledger: Path, issues: list[Finding]) -> None:
    with _ro(ledger) as con:
        stored = dict(
            con.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' "
                "AND tbl_name = ?",
                (LEDGER_TABLE,),
            ).fetchall()
        )
    for name, sql in sorted(_expected_triggers().items()):
        if name not in stored:
            issues.append(
                Finding("missing_trigger", name, "append-only trigger absent")
            )
        elif stored[name] != sql:
            issues.append(
                Finding("altered_trigger", name, f"trigger text: {stored[name]}")
            )


def _raw_digests(ledger: Path) -> dict[int, tuple[str, str]]:
    """Per sequence, the digest and reading digest as stored (not recomputed)."""
    with _ro(ledger) as con:
        rows = con.execute(
            f"SELECT sequence, digest, reading_digest FROM {LEDGER_TABLE}"
        ).fetchall()
    return {int(s): (str(d), str(r)) for s, d, r in rows}


def _check_unit(
    where: str, key: ExperimentKey, detail: Mapping[str, Json], issues: list[Finding]
) -> None:
    unit = detail.get("unit")
    kind = key.config.get("kind")
    if not isinstance(unit, dict):
        issues.append(Finding("unit_mismatch", where, "no unit block"))
        return
    expected = {
        "truth": key.config.get("truth"),
        "seed": int(key.seed),
        "system": key.config.get("system"),
        "kind": kind,
    }
    for name, value in sorted(expected.items()):
        if unit.get(name) != value:
            issues.append(
                Finding(
                    "unit_mismatch",
                    where,
                    f"unit {name} {unit.get(name)!r}, key {value!r}",
                )
            )


def _check_address_text(
    where: str, key: ExperimentKey, detail: Mapping[str, Json], issues: list[Finding]
) -> None:
    field = {"baseline": "system_config", "llm-session": "session_config"}.get(
        key.config.get("kind", "")
    )
    if field is None:
        return
    text = detail.get(field)
    if not isinstance(text, str) or _sha(text) != key.config.get(field):
        issues.append(
            Finding("address_text", where, f"{field} text does not hash to the key")
        )


def _check_session_record(
    root: Path,
    where: str,
    detail: Mapping[str, Json],
    record_dirs: dict[str, str],
    issues: list[Finding],
) -> None:
    session = detail.get("session")
    if not isinstance(session, dict) or not isinstance(session.get("record_dir"), str):
        issues.append(Finding("missing_record", where, "session has no record_dir"))
        return
    rel = session["record_dir"]
    other = record_dirs.setdefault(rel, where)
    if other != where:
        issues.append(
            Finding("shared_record_dir", where, f"{rel} also used by {other}")
        )
    directory = root / rel
    if not directory.resolve().is_relative_to(root.resolve()) or not directory.is_dir():
        issues.append(Finding("missing_record", where, f"no record dir {rel}"))
        return
    record = session.get("record")
    if isinstance(record, str) and not (directory / record).is_file():
        issues.append(Finding("missing_record", where, f"no record {rel}/{record}"))


def _check_cell(
    root: Path,
    entry: LedgerEntry,
    raw: tuple[str, str] | None,
    record_dirs: dict[str, str],
    sessions: set[str],
    scores: list[tuple[str, str]],
    issues: list[Finding],
) -> None:
    digest = str(entry.digest)
    if raw is None or raw[0] != digest:
        stored = None if raw is None else raw[0]
        issues.append(
            Finding("ledger_address", digest, f"stored digest {stored} != key digest")
        )
    if raw is not None and raw[1] != str(entry.reading_digest):
        issues.append(
            Finding("ledger_reading", digest, "stored reading digest != reading's")
        )
    path = root / DETAILS_DIR / f"{digest}.json"
    if not path.is_file():
        issues.append(Finding("missing_detail", digest, f"no {path.name}"))
        return
    data = path.read_bytes()
    try:
        cell = json.loads(data.decode("utf-8"))
        key = key_from_json(cell["key"])
        reading = {k: float.fromhex(v) for k, v in cell["reading"].items()}
        body = cell["detail"]
    except (ValueError, KeyError, TypeError) as error:
        issues.append(Finding("unreadable_detail", digest, f"{error!r}"))
        return
    if cell.get("schema") != DETAIL_SCHEMA:
        issues.append(Finding("schema", digest, f"schema {cell.get('schema')!r}"))
    if str(key.digest) != digest or cell.get("digest") != digest:
        issues.append(Finding("detail_address", digest, "detail key/digest != name"))
    hexes = {k: float(v).hex() for k, v in sorted(entry.reading.items())}
    if cell["reading"] != hexes:
        issues.append(Finding("reading_mismatch", digest, "detail reading != ledger"))
    if cell_bytes(key, reading, body) != data:
        issues.append(Finding("noncanonical", digest, "bytes are not cell_bytes"))
    if not isinstance(body, dict):
        issues.append(Finding("unreadable_detail", digest, "detail is not an object"))
        return
    _check_unit(digest, key, body, issues)
    _check_address_text(digest, key, body, issues)
    kind = key.config.get("kind")
    if kind == "llm-session":
        sessions.add(digest)
        _check_session_record(root, digest, body, record_dirs, issues)
    elif kind == "llm-score":
        scores.append((digest, key.config.get("session", "")))


def check_store(root: Path) -> StoreCheck:
    """Every consistency property of the module docstring, over one store."""
    ledger_path = _require_store(root)
    issues: list[Finding] = []
    warnings: list[Finding] = []
    _check_triggers(ledger_path, issues)
    raw = _raw_digests(ledger_path)
    with ResultStore(root) as store:
        entries = store.ledger.entries()
    committed = {str(e.digest) for e in entries}
    record_dirs: dict[str, str] = {}
    sessions: set[str] = set()
    scores: list[tuple[str, str]] = []
    for entry in entries:
        _check_cell(
            root, entry, raw.get(entry.sequence), record_dirs, sessions, scores, issues
        )
    for digest, session in scores:
        if session not in sessions:
            issues.append(
                Finding("dangling_session", digest, f"session {session} not in store")
            )
    for path in sorted((root / DETAILS_DIR).iterdir()):
        stem = path.stem
        if path.suffix != ".json" or not _DIGEST.fullmatch(stem):
            warnings.append(Finding("stray_file", path.name, "not a detail file"))
        elif stem not in committed:
            warnings.append(
                Finding("uncommitted_detail", stem, "detail without a ledger row")
            )
    timing = root / TIMING_FILE
    if timing.is_file():
        for n, line in enumerate(timing.read_text(encoding="utf-8").splitlines(), 1):
            try:
                record = json.loads(line)
            except ValueError:
                issues.append(Finding("timing_line", f"line {n}", "not JSON"))
                continue
            logged = record.get("digest") if isinstance(record, dict) else None
            if logged is not None and logged not in committed:
                warnings.append(
                    Finding("timing_digest", f"line {n}", f"{logged} not in ledger")
                )
    return StoreCheck(len(entries), tuple(sorted(issues)), tuple(sorted(warnings)))


# --------------------------------------------------------------------------
# Byte comparison
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class StoreDiff:
    same: int
    only_a: tuple[str, ...]
    only_b: tuple[str, ...]
    differing: tuple[str, ...]

    @property
    def identical(self) -> bool:
        return not self.only_a and not self.only_b and not self.differing


def _committed(root: Path, kinds: Sequence[str] | None) -> dict[str, str]:
    """Digest -> the ledger's reading text, as stored, for cells of ``kinds``
    (every cell when None)."""
    with _ro(_require_store(root)) as con:
        rows = con.execute(
            f"SELECT digest, config, reading FROM {LEDGER_TABLE}"
        ).fetchall()
    return {
        str(d): str(r)
        for d, c, r in rows
        if kinds is None or json.loads(c).get("kind") in kinds
    }


def _detail(root: Path, digest: str) -> bytes | None:
    path = root / DETAILS_DIR / f"{digest}.json"
    return path.read_bytes() if path.is_file() else None


def compare_stores(
    a: Path, b: Path, *, kinds: Sequence[str] | None = None
) -> StoreDiff:
    """Cell-by-cell byte identity of two stores (module docstring), over the
    cells of ``kinds`` only when given."""
    unknown = sorted(set(kinds or ()) - set(CELL_KINDS))
    if unknown:
        raise CampaignError(f"unknown cell kind(s) {unknown}; known: {CELL_KINDS}")
    ca, cb = _committed(a, kinds), _committed(b, kinds)
    common = sorted(ca.keys() & cb.keys())
    differing = tuple(
        d
        for d in common
        if ca[d] != cb[d] or _detail(a, d) is None or _detail(a, d) != _detail(b, d)
    )
    return StoreDiff(
        same=len(common) - len(differing),
        only_a=tuple(sorted(ca.keys() - cb.keys())),
        only_b=tuple(sorted(cb.keys() - ca.keys())),
        differing=differing,
    )


def same_bytes(paths: Sequence[Path]) -> tuple[Path, ...]:
    """The paths whose bytes differ from the first one's."""
    if not paths:
        return ()
    first = paths[0].read_bytes()
    return tuple(p for p in paths[1:] if p.read_bytes() != first)

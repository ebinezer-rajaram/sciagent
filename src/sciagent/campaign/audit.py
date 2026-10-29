"""Defect-hunt checks on a campaign store: the gap to ORACLE, store consistency,
budget accounting, stored sessions against a campaign, and a cell-by-cell byte
comparison of two stores.

These read the numbers the evaluation reports, so they are instruments (SPEC
§6.3). They rebuild the hunt scripts of 2026-10-06 (HANDOFF-2026-10-06, LOG
2026-10-06 "P3.1 fixes after defect hunt B"), first from their contracts and
then cross-checked against the originals salvaged from the Windows PC (LOG
2026-10-27, "The rebuilt hunt checks cross-checked"); the scripts under
``scripts/`` are thin command lines over this module.

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

With a ``truth`` reference (per (truth, seed), the held-out per-event log L
of the truth's *own* parameters, computed by the domain's script, since this
module cannot simulate a truth), the gate also fails when any unit, ORACLE
included, is above that reference by more than the threshold. A fitted model
beating the data-generating process on independent held-out data by that much
is a leakage flag whatever ORACLE does (hunt A's ``p3_gap.py``, "BEATS-TRUTH").
A scored unit without a reference is refused rather than skipped.

A truth id does not identify a truth: two splits can share their ids with
different records (the Windows and Linux dev splits do). So the reference
carries the digest of the truth record it was computed from
(:class:`TruthReference`), and each unit's own record is established from its
cell (:func:`cell_truth_digest`: a baseline key's ``truth_digest``, an
``llm-score``'s through its session cell). The gate fails, naming the unit
(:attr:`GapReport.digest_issues`), when a unit's record cannot be established
or is not the reference's; such a unit gets no margin over the truth. Without
a reference, a group whose cells come from more than one record fails the
same way, since its gaps would pair cells of different truths.

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
- the key carries every field its kind needs (:data:`KEY_FIELDS`; hunt B §6);
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

**Budget accounting** (:func:`check_budgets`; hunt B ``store_check.py`` §3;
tool budgets are an instrument, SPEC §6.3). For every completed session cell
(reading ``status`` 1), its record (``record_dir``/``record``) is loaded and
verified, and its ``tool_call`` entries, in call order, must show:

- call indices 0, 1, 2, ... with none missing;
- each call charged exactly when it succeeded and its tool draws on a budget
  (the record's ``config`` entry names each tool's budget): a refusal is never
  charged, a successful budgeted call always is;
- ``budgets_after`` equal to the running count of charges, never above the
  ``config`` entry's limit, and a "budget exhausted" refusal only at the limit;
- the charged fits numbered 1, 2, 3, ... in call order (the fit tool's
  ``record.fit``);
- the cell's own counts equal to the record's: ``experiments_used`` and
  ``fits_used`` (:data:`USED_READINGS`) to the charges, ``tool_calls`` to the
  number of entries, and the detail's ``tool_calls`` (per name) and ``fitted``
  (one per charged fit) likewise.

**Stored sessions** (:func:`check_sessions`; hunt B ``store_check.py`` §2 and
§5). Every ``llm-session`` cell must be found by
:func:`~sciagent.campaign.rescore.stored_sessions` under the campaign's
configuration and truths: a rescore scores only what that enumeration finds,
so a session it misses is silently never scored. A missed cell is an issue
naming the reason (system, truth, truth_digest, seed, session_config); two
stored sessions for one unit (the enumeration refuses them) are an issue too.
For each found unit, the void policy
(:func:`~sciagent.campaign.voids.session_void`) is compared with the stored
flag: a stored void the policy does not void (``void_reclassified``), or the
reverse (``void_policy``), is a warning, since sessions recorded under an
older harness are expected to differ (the pilot's nine reclassified voids).

**Byte comparison** (:func:`compare_stores`; HANDOFF-2026-10-06 step 1,
"compare its cell bytes with run 1"). The committed digests of two stores are
compared as sets, and every common digest's detail file and ledger reading
must be byte-identical. ``timing.jsonl`` is not compared: it holds wall-clock
times by design (store docstring). The comparison can be restricted to some
kinds of cell: an ``llm-session`` cell of a live model is not reproducible by
design, so two runs that each conducted their own sessions compare on
``baseline`` and ``llm-score``. Its times are not in it (they are in
``timing.jsonl``), so under a scripted model every kind is byte-identical.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from sciagent.campaign.analysis import Row, derive, row_from_cell, underfit_check
from sciagent.campaign.plan import (
    ORACLE,
    CampaignConfig,
    CampaignEnvironment,
    CampaignError,
    TruthEntry,
    session_config_digest,
    session_key,
)
from sciagent.campaign.rescore import stored_sessions
from sciagent.campaign.store import (
    DETAIL_SCHEMA,
    DETAILS_DIR,
    LEDGER_FILE,
    TIMING_FILE,
    ResultStore,
    cell_bytes,
    key_from_json,
)
from sciagent.campaign.voids import decoded, session_void
from sciagent.harness.errors import RecordError, RecordFormatError
from sciagent.harness.record import SessionRecord
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
#: The key fields each kind of cell needs (``campaign.plan``'s key builders).
KEY_FIELDS: Final = {
    "baseline": (
        "fit_config",
        "kind",
        "system",
        "truth",
        "truth_digest",
        "system_config",
    ),
    "llm-session": (
        "fit_config",
        "kind",
        "system",
        "truth",
        "truth_digest",
        "session_config",
    ),
    "llm-score": ("fit_config", "kind", "system", "truth", "session"),
}
#: The session reading field that counts each budget's charges (``campaign.llm``).
USED_READINGS: Final = {"experiments": "experiments_used", "fits": "fits_used"}
#: The budget the fit tool draws on, and its record's fit number field.
FIT_BUDGET: Final = "fits"
#: The tool layer's refusal for an exhausted budget (``harness.tools``).
EXHAUSTED: Final = "budget exhausted"


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
class TruthReference:
    """The held-out per-event log L of a truth's own parameters on one seed's
    held-out log, and the digest of the truth record it was computed from (the
    SHA-256 a cell's key carries as ``truth_digest``)."""

    digest: str
    per_event: float


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
    truth_ll: float | None = None
    over_truth: float | None = None
    #: The digest of the truth record the cell was scored on (an
    #: ``llm-score``'s comes from its session cell); None when it cannot be
    #: established.
    truth_digest: str | None = None
    #: Why the unit's truth record is not established, or not the reference's
    #: (or, without a reference, not its group's only one); None when it is.
    digest_issue: str | None = None


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
    def beats_truth(self) -> tuple[GapUnit, ...]:
        """Units above the truth's own parameters by more than the threshold
        (empty without a ``truth`` reference)."""
        return tuple(
            u
            for u in self.units
            if u.over_truth is not None and u.over_truth > self.threshold
        )

    @property
    def digest_issues(self) -> tuple[GapUnit, ...]:
        """Units not shown to be scored on the reference's truth record (or,
        without a reference, on their group's only one)."""
        return tuple(u for u in self.units if u.digest_issue is not None)

    @property
    def passed(self) -> bool:
        return (
            bool(self.units)
            and not self.above
            and not self.unreferenced
            and not self.beats_truth
            and not self.digest_issues
            and self.underfit.passed
        )


def _margin(value: float | None, reference: float | None) -> float | None:
    """``value - reference`` when both are finite; None otherwise."""
    if value is None or reference is None:
        return None
    if not (math.isfinite(value) and math.isfinite(reference)):
        return None
    return value - reference


def _group(key: ExperimentKey) -> tuple[str, str, str, str, str, int]:
    return (
        str(key.env_version),
        key.config.get("fit_config", ""),
        str(key.data_version),
        str(key.metric_version),
        key.config["truth"],
        int(key.seed),
    )


def _short(digest: str) -> str:
    return f"{digest[:12]}..."


def cell_truth_digest(
    key: ExperimentKey, cells: Mapping[str, Mapping[str, Json]]
) -> tuple[str | None, str | None]:
    """The digest of the truth record a scored cell was scored on, or None
    and the reason it cannot be established. A ``baseline`` key carries it;
    an ``llm-score`` key names its session, whose key carries it, and that
    session must be a committed ``llm-session`` cell of the same truth and
    seed (``cells``: committed cells by digest)."""
    unit = f"{key.config['truth']} s{int(key.seed)}"
    if key.config.get("kind") == "llm-score":
        session = key.config.get("session", "")
        cell = cells.get(session)
        if cell is None:
            return None, f"its session {_short(session)} is not a committed cell"
        skey = key_from_json(cell["key"])
        skind = skey.config.get("kind")
        if skind != "llm-session":
            return None, f"its session {_short(session)} is a {skind} cell"
        sunit = f"{skey.config.get('truth')} s{int(skey.seed)}"
        if sunit != unit:
            return None, f"its session {_short(session)} is for {sunit}"
        key = skey
    digest = key.config.get("truth_digest", "")
    if not digest:
        return None, f"its {key.config.get('kind')} key has no truth_digest"
    return digest, None


def _digest_issues(
    digests: Mapping[str, tuple[str | None, str | None]],
    reference: TruthReference | None,
) -> dict[str, str]:
    """Per system of one group, why its truth record is not shown to be the
    reference's (without a reference: the group's only one)."""
    known = sorted({d for d, _ in digests.values() if d is not None})
    out: dict[str, str] = {}
    for system, (digest, reason) in sorted(digests.items()):
        if digest is None:
            out[system] = f"truth record not established: {reason}"
        elif reference is not None and digest != reference.digest:
            out[system] = (
                f"truth record {_short(digest)} is not the reference's "
                f"{_short(reference.digest)}"
            )
        elif reference is None and len(known) > 1:
            out[system] = (
                f"the group's cells come from {len(known)} truth records "
                f"({', '.join(_short(d) for d in known)})"
            )
    return out


def gap_check(
    store: ResultStore,
    env: str,
    *,
    threshold: float = GAP_THRESHOLD,
    truth: Mapping[tuple[str, int], TruthReference] | None = None,
) -> GapReport:
    """Per unit, the gap of each system to ORACLE and, given ``truth`` (per
    (truth id, seed), the truth's own held-out per-event log L and its
    record's digest), its margin over the truth (module docstring)."""
    groups: dict[tuple[str, str, str, str, str, int], list[Row]] = {}
    digests: dict[
        tuple[str, str, str, str, str, int], dict[str, tuple[str | None, str | None]]
    ] = {}
    cells = store.cells()
    by_digest = {str(c["digest"]): c for c in cells}
    for cell in cells:
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
        digests.setdefault(g, {})[system] = cell_truth_digest(key, by_digest)
    derived: list[Row] = []
    units: list[GapUnit] = []
    for g in sorted(groups):
        rows = derive(groups[g])
        derived += rows
        oracle = next(
            (r.per_event for r in rows if r.system == ORACLE and not r.llm), None
        )
        reference = None
        if truth is not None:
            if (g[4], g[5]) not in truth:
                raise CampaignError(
                    f"no truth reference for {g[4]} s{g[5]}, which has scored cells"
                )
            reference = truth[(g[4], g[5])]
            if not math.isfinite(reference.per_event):
                raise CampaignError(
                    f"truth reference for {g[4]} s{g[5]} is "
                    f"{reference.per_event!r}, not finite"
                )
        issues = _digest_issues(digests[g], reference)
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
                truth_ll=None if reference is None else reference.per_event,
                over_truth=(
                    None
                    if reference is None or r.system in issues
                    else _margin(r.per_event, reference.per_event)
                ),
                truth_digest=digests[g][r.system][0],
                digest_issue=issues.get(r.system),
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


def detail_reading_is_ledgers(detail_reading: object, entry: LedgerEntry) -> bool:
    """Whether a detail file's ``reading`` (hex text) is its ledger row's
    reading bit for bit (``reading_mismatch`` of :func:`check_store`)."""
    hexes = {k: float(v).hex() for k, v in sorted(entry.reading.items())}
    return detail_reading == hexes


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
    if not detail_reading_is_ledgers(cell["reading"], entry):
        issues.append(Finding("reading_mismatch", digest, "detail reading != ledger"))
    if cell_bytes(key, reading, body) != data:
        issues.append(Finding("noncanonical", digest, "bytes are not cell_bytes"))
    if not isinstance(body, dict):
        issues.append(Finding("unreadable_detail", digest, "detail is not an object"))
        return
    kind = key.config.get("kind")
    missing = [f for f in KEY_FIELDS.get(str(kind), ()) if f not in key.config]
    if kind not in KEY_FIELDS or missing:
        issues.append(
            Finding("missing_field", digest, f"kind {kind!r} key lacks {missing}")
        )
    _check_unit(digest, key, body, issues)
    _check_address_text(digest, key, body, issues)
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
    with ResultStore(root, read_only=True) as store:
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
    details = root / DETAILS_DIR
    detail_files: list[Path] = []
    if details.is_dir():
        detail_files = sorted(details.iterdir())
    else:
        # A reader never recreates it (LOG 2026-10-28, readers read-only).
        issues.append(Finding("missing_details_dir", DETAILS_DIR, "no details dir"))
    for path in detail_files:
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
# Budget accounting
# --------------------------------------------------------------------------


def _record_tools(
    record: SessionRecord,
) -> tuple[dict[str, int], dict[str, str | None]]:
    """The budgets and each tool's budget, from the record's ``config`` entry."""
    configs = record.of_kind("config")
    if not configs:
        raise RecordFormatError("the record has no config entry")
    body = configs[0].body
    budgets = {str(k): int(v) for k, v in dict(body["budgets"]).items()}
    tools = {str(t["name"]): t.get("budget") for t in body["tools"]}
    return budgets, tools


def _audit_calls(
    where: str,
    calls: Sequence[Mapping[str, Json]],
    budgets: Mapping[str, int],
    tools: Mapping[str, str | None],
    issues: list[Finding],
) -> dict[str, int]:
    """Per-call charges against the tool budgets; returns the charge counts."""
    used = {name: 0 for name in sorted(budgets)}
    fit_numbers: list[object] = []
    for position, body in enumerate(calls):
        name = str(body.get("name"))
        here = f"call {body.get('call')} {name}"
        if body.get("call") != position:
            issues.append(
                Finding(
                    "call_index",
                    where,
                    f"call {position} has index {body.get('call')!r}",
                )
            )
        budget = tools.get(name)
        is_error = bool(body.get("is_error"))
        expected = budget if budget is not None and not is_error else None
        charged = body.get("charged")
        if charged != expected:
            issues.append(
                Finding(
                    "budget_charge",
                    where,
                    f"{here}: is_error={is_error} charged {charged!r}, "
                    f"expected {expected!r}",
                )
            )
        if isinstance(charged, str):
            used[charged] = used.get(charged, 0) + 1
            if charged == FIT_BUDGET:
                fit_numbers.append((body.get("record") or {}).get("fit"))
            if used[charged] > budgets.get(charged, 0):
                issues.append(
                    Finding(
                        "over_budget",
                        where,
                        f"{here}: {charged} used {used[charged]} > "
                        f"{budgets.get(charged, 0)}",
                    )
                )
        after = body.get("budgets_after")
        if not isinstance(after, dict) or dict(after) != used:
            issues.append(
                Finding("budgets_after", where, f"{here}: {after!r} != running {used}")
            )
        if (
            is_error
            and budget is not None
            and str(body.get("text", "")).startswith(EXHAUSTED)
            and used.get(budget, 0) < budgets.get(budget, 0)
        ):
            issues.append(
                Finding(
                    "early_exhaustion",
                    where,
                    f"{here}: refused as exhausted with {budget} used "
                    f"{used.get(budget, 0)} < {budgets.get(budget, 0)}",
                )
            )
    if fit_numbers != list(range(1, len(fit_numbers) + 1)):
        issues.append(
            Finding("fit_numbers", where, f"charged fits numbered {fit_numbers}")
        )
    return used


def _audit_counts(
    where: str,
    calls: Sequence[Mapping[str, Json]],
    used: Mapping[str, int],
    reading: Mapping[str, float],
    session: Mapping[str, Json],
    issues: list[Finding],
) -> None:
    """The cell's own counts against the record's (module docstring)."""
    for budget, field in sorted(USED_READINGS.items()):
        if reading.get(field) != float(used.get(budget, 0)):
            issues.append(
                Finding(
                    "reading_used",
                    where,
                    f"{field} {reading.get(field)!r} != {used.get(budget, 0)} charged",
                )
            )
    if reading.get("tool_calls") != float(len(calls)):
        issues.append(
            Finding(
                "reading_calls",
                where,
                f"tool_calls {reading.get('tool_calls')!r} != {len(calls)} recorded",
            )
        )
    names: dict[str, int] = {}
    for body in calls:
        names[str(body.get("name"))] = names.get(str(body.get("name")), 0) + 1
    if session.get("tool_calls") != dict(sorted(names.items())):
        issues.append(
            Finding(
                "detail_calls",
                where,
                f"detail tool_calls {session.get('tool_calls')!r} != {names}",
            )
        )
    fitted = session.get("fitted")
    if not isinstance(fitted, list) or len(fitted) != used.get(FIT_BUDGET, 0):
        issues.append(
            Finding(
                "detail_fitted",
                where,
                f"detail fitted {fitted!r} != {used.get(FIT_BUDGET, 0)} charged fits",
            )
        )


def check_budgets(root: Path) -> StoreCheck:
    """Budget accounting of every completed session's record (module docstring).
    ``cells`` counts the sessions audited."""
    _require_store(root)
    issues: list[Finding] = []
    audited = 0
    with ResultStore(root, read_only=True) as store:
        cells = store.cells()
    for cell in cells:
        if cell["key"]["config"].get("kind") != "llm-session":
            continue
        reading = {k: float.fromhex(v) for k, v in cell["reading"].items()}
        if reading.get("status") != 1.0:
            continue
        audited += 1
        where = str(cell["digest"])
        session = cell["detail"].get("session") or {}
        try:
            record = SessionRecord.load(
                root / str(session["record_dir"]) / str(session["record"])
            )
            budgets, tools = _record_tools(record)
        except (KeyError, TypeError, ValueError, RecordError) as error:
            issues.append(Finding("unreadable_record", where, f"{error!r}"))
            continue
        calls = sorted(
            (dict(e.body) for e in record.of_kind("tool_call")),
            key=lambda b: (int(b.get("call", -1)), str(b.get("name"))),
        )
        used = _audit_calls(where, calls, budgets, tools, issues)
        _audit_counts(where, calls, used, reading, session, issues)
    return StoreCheck(audited, tuple(sorted(issues)), ())


# --------------------------------------------------------------------------
# Stored sessions and the void policy
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionCheck:
    """Stored sessions against a campaign: how many cells, how many the
    rescore's enumeration finds, how many at their current address."""

    sessions: int
    found: int
    at_current: int
    issues: tuple[Finding, ...]
    warnings: tuple[Finding, ...]

    @property
    def passed(self) -> bool:
        return not self.issues


def _not_found_reason(
    key: ExperimentKey,
    config: CampaignConfig,
    truths: Mapping[str, TruthEntry],
    digests: Mapping[str, str],
) -> str:
    """Why :func:`~sciagent.campaign.rescore.stored_sessions` skips a session
    cell: the first of its conditions the cell fails."""
    fields = key.config
    system = fields.get("system", "")
    truth = truths.get(fields.get("truth", ""))
    if system not in digests:
        return f"system {system!r} is not one of the campaign's LLM systems"
    if truth is None:
        return f"truth {fields.get('truth')!r} is not one of the campaign's truths"
    if fields.get("truth_digest") != truth.digest:
        return f"truth_digest of {truth.id} differs from the campaign's"
    if int(key.seed) not in config.seeds:
        return f"seed {int(key.seed)} is not one of the campaign's seeds"
    if fields.get("session_config") != digests[system]:
        return f"session_config of {system} differs from the campaign's"
    return "not matched"


def check_sessions(
    store: ResultStore,
    env: CampaignEnvironment,
    config: CampaignConfig,
    truths: Sequence[TruthEntry],
) -> SessionCheck:
    """Every stored session found by the rescore's enumeration, and the void
    policy against each found session's stored flag (module docstring)."""
    by_id = {t.id: t for t in truths}
    cells = [
        c for c in store.cells() if c["key"]["config"].get("kind") == "llm-session"
    ]
    issues: list[Finding] = []
    warnings: list[Finding] = []
    try:
        # An AG-o digest names the sandbox image, read from its build context.
        digests = {
            s: session_config_digest(config.arm_of(s), config)
            for s in config.llm_systems()
        }
    except CampaignError as error:
        issues.append(Finding("session_config", "campaign", str(error)))
        return SessionCheck(len(cells), 0, 0, tuple(issues), ())
    try:
        found = stored_sessions(store, env, config, truths)
    except CampaignError as error:
        issues.append(Finding("ambiguous", "stored_sessions", str(error)))
        return SessionCheck(len(cells), 0, 0, tuple(issues), ())
    hits = {str(key.digest) for key, _ in found.values()}
    for cell in cells:
        if cell["digest"] not in hits:
            key = key_from_json(cell["key"])
            reason = _not_found_reason(key, config, by_id, digests)
            issues.append(Finding("not_found", str(cell["digest"]), reason))
    at_current = 0
    for unit, (key, cell) in sorted(found.items()):
        at_current += key == session_key(unit, by_id[unit.truth_id], env, config)
        status = session_void(
            store.root, decoded(cell["reading"]), cell["detail"].get("session") or {}
        )
        if status.stored_void != status.void:
            kind = "void_reclassified" if status.reclassified else "void_policy"
            warnings.append(
                Finding(
                    kind,
                    str(key.digest),
                    f"{unit.system} {unit.truth_id} s{unit.seed}: stored void "
                    f"{status.stored_void}, policy {status.void} "
                    f"{list(status.reasons)}",
                )
            )
    return SessionCheck(
        len(cells),
        len(found),
        at_current,
        tuple(sorted(issues)),
        tuple(sorted(warnings)),
    )


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

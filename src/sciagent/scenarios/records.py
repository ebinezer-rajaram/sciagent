"""Truth records, canonical JSON, and the hash-committed splits (SPEC §3, §6.1).

A :class:`TruthRecord` is everything needed to regenerate a truth's data and
to audit it: the canonical DSL text and link, ψ and θ, the seed and stream key
it was drawn from, its dictionary flag and stratum, and the calibration and
identifiability measurements that admitted it.

**Canonical JSON** (:func:`canonical_json`): keys sorted, no whitespace, UTF-8,
floats as Python's shortest round-trip ``repr``, NaN and infinities refused.
A split's records file ``truths.json`` is exactly the canonical JSON of the
list of records, so its SHA-256 (:func:`records_digest`) can be recomputed
with any tool. The test split's file is kept outside the repository and only
that hash is committed (SPEC §3 "hash-committed before any agent touches
them"); :func:`read_split` refuses a file whose hash disagrees with its
manifest.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

from sciagent.core.errors import SciAgentError
from sciagent.glm.canonical import canonicalise, structure_hash
from sciagent.glm.grammar import ChannelSpec, PsiSlot, Structure
from sciagent.glm.simulate import Coefficients, PsiAssignment
from sciagent.glm.syntax import parse

RECORDS_FILE: Final = "truths.json"
MANIFEST_FILE: Final = "manifest.json"
TIMING_FILE: Final = "timing.json"
RECORD_SCHEMA: Final = "sciagent.scenarios.truth/1"

type Json = Any


class SplitIntegrityError(SciAgentError):
    """A split's records do not match its manifest, or a record is malformed."""


def canonical_json(obj: Json) -> str:
    """Sorted keys, no whitespace, shortest round-trip floats; NaN/inf refused."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _finite(x: float) -> float:
    if not math.isfinite(x):
        raise SplitIntegrityError(f"non-finite number in a record: {x}")
    return float(x)


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CalibrationRecord:
    """The calibration that admitted a truth (``calibrate.Calibration``)."""

    knob: float
    iterations: int
    horizon: float
    mean_rate: float
    fano: float
    cv: float
    drift: float
    n_events: int
    branching_bound: float | None


@dataclass(frozen=True)
class IdentifiabilityRecord:
    """Held-out scores of the fitted truth and of every library member.

    Log-likelihoods are held-out totals; ``margins`` are per held-out event, in
    library order. ``truth_psi_recovered`` says whether the truth's own fit
    chose exactly the truth's ψ. ``train_mean_rate`` and
    ``held_out_mean_rate`` are event counts over the horizon, an independent
    re-measurement of the operating point.
    """

    horizon: float
    train_events: int
    held_out_events: int
    train_mean_rate: float
    held_out_mean_rate: float
    truth_log_likelihood: float
    truth_certified: bool
    truth_psi_recovered: bool
    member_log_likelihoods: tuple[tuple[str, float], ...]
    margins: tuple[tuple[str, float], ...]
    min_margin: float


@dataclass(frozen=True)
class TruthRecord:
    """One sampled truth (module docstring).

    ``stream`` is the key prefix of every random stream the truth was drawn
    from: ``stream(seed, f"{stream}/<purpose>")`` (``scenarios.streams``).
    ``psi`` holds, per feature, ``(path, name, value)`` in ``psi_slots`` order;
    ``per_feature`` holds θ per feature as the simulator takes it.
    """

    id: str
    split: str
    seed: int
    stream: str
    candidate_index: int
    out_of_dictionary: bool
    dsl: str
    link: str
    structure_hash: str
    depths: tuple[int, ...]
    psi: tuple[tuple[tuple[tuple[int, ...], str, float], ...], ...]
    intercept: float
    per_feature: tuple[tuple[float, ...], ...]
    nearest_member: str
    nearest_distance: float
    member_distances: tuple[tuple[str, float], ...]
    stratum: str
    calibration: CalibrationRecord
    identifiability: IdentifiabilityRecord

    # -- what P3 consumes ----------------------------------------------------

    def structure(self, channels: tuple[ChannelSpec, ...]) -> Structure:
        """The structure, parsed from the DSL and checked against the hash."""
        s = parse(self.dsl, channels)
        if canonicalise(s) != s or structure_hash(s) != self.structure_hash:
            raise SplitIntegrityError(f"{self.id}: DSL does not match its hash")
        return s

    def psi_assignment(self) -> PsiAssignment:
        return tuple(
            {PsiSlot(path, name): value for path, name, value in feature}
            for feature in self.psi
        )

    def coefficients(self) -> Coefficients:
        return Coefficients(self.intercept, self.per_feature)

    # -- JSON ----------------------------------------------------------------

    def to_json(self) -> dict[str, Json]:
        c, i = self.calibration, self.identifiability
        return {
            "schema": RECORD_SCHEMA,
            "id": self.id,
            "split": self.split,
            "seed": self.seed,
            "stream": self.stream,
            "candidate_index": self.candidate_index,
            "out_of_dictionary": self.out_of_dictionary,
            "dsl": self.dsl,
            "link": self.link,
            "structure_hash": self.structure_hash,
            "depths": list(self.depths),
            "psi": [
                [{"path": list(p), "name": n, "value": _finite(v)} for p, n, v in f]
                for f in self.psi
            ],
            "theta": {
                "intercept": _finite(self.intercept),
                "per_feature": [[_finite(x) for x in f] for f in self.per_feature],
            },
            "nearest_member": self.nearest_member,
            "nearest_distance": _finite(self.nearest_distance),
            "member_distances": [[n, _finite(d)] for n, d in self.member_distances],
            "stratum": self.stratum,
            "calibration": {
                "knob": _finite(c.knob),
                "iterations": c.iterations,
                "horizon": _finite(c.horizon),
                "mean_rate": _finite(c.mean_rate),
                "fano": _finite(c.fano),
                "cv": _finite(c.cv),
                "drift": _finite(c.drift),
                "n_events": c.n_events,
                "branching_bound": (
                    None if c.branching_bound is None else _finite(c.branching_bound)
                ),
            },
            "identifiability": {
                "horizon": _finite(i.horizon),
                "train_events": i.train_events,
                "held_out_events": i.held_out_events,
                "train_mean_rate": _finite(i.train_mean_rate),
                "held_out_mean_rate": _finite(i.held_out_mean_rate),
                "truth_log_likelihood": _finite(i.truth_log_likelihood),
                "truth_certified": i.truth_certified,
                "truth_psi_recovered": i.truth_psi_recovered,
                "member_log_likelihoods": [
                    [n, _finite(x)] for n, x in i.member_log_likelihoods
                ],
                "margins": [[n, _finite(x)] for n, x in i.margins],
                "min_margin": _finite(i.min_margin),
            },
        }

    @staticmethod
    def from_json(obj: Mapping[str, Json]) -> TruthRecord:
        try:
            if obj["schema"] != RECORD_SCHEMA:
                raise SplitIntegrityError(f"unknown record schema {obj['schema']!r}")
            c, i = obj["calibration"], obj["identifiability"]
            return TruthRecord(
                id=str(obj["id"]),
                split=str(obj["split"]),
                seed=int(obj["seed"]),
                stream=str(obj["stream"]),
                candidate_index=int(obj["candidate_index"]),
                out_of_dictionary=bool(obj["out_of_dictionary"]),
                dsl=str(obj["dsl"]),
                link=str(obj["link"]),
                structure_hash=str(obj["structure_hash"]),
                depths=tuple(int(d) for d in obj["depths"]),
                psi=tuple(
                    tuple(
                        (
                            tuple(int(x) for x in s["path"]),
                            str(s["name"]),
                            float(s["value"]),
                        )
                        for s in f
                    )
                    for f in obj["psi"]
                ),
                intercept=float(obj["theta"]["intercept"]),
                per_feature=tuple(
                    tuple(float(x) for x in f) for f in obj["theta"]["per_feature"]
                ),
                nearest_member=str(obj["nearest_member"]),
                nearest_distance=float(obj["nearest_distance"]),
                member_distances=tuple(
                    (str(n), float(d)) for n, d in obj["member_distances"]
                ),
                stratum=str(obj["stratum"]),
                calibration=CalibrationRecord(
                    knob=float(c["knob"]),
                    iterations=int(c["iterations"]),
                    horizon=float(c["horizon"]),
                    mean_rate=float(c["mean_rate"]),
                    fano=float(c["fano"]),
                    cv=float(c["cv"]),
                    drift=float(c["drift"]),
                    n_events=int(c["n_events"]),
                    branching_bound=(
                        None
                        if c["branching_bound"] is None
                        else float(c["branching_bound"])
                    ),
                ),
                identifiability=IdentifiabilityRecord(
                    horizon=float(i["horizon"]),
                    train_events=int(i["train_events"]),
                    held_out_events=int(i["held_out_events"]),
                    train_mean_rate=float(i["train_mean_rate"]),
                    held_out_mean_rate=float(i["held_out_mean_rate"]),
                    truth_log_likelihood=float(i["truth_log_likelihood"]),
                    truth_certified=bool(i["truth_certified"]),
                    truth_psi_recovered=bool(i["truth_psi_recovered"]),
                    member_log_likelihoods=tuple(
                        (str(n), float(x)) for n, x in i["member_log_likelihoods"]
                    ),
                    margins=tuple((str(n), float(x)) for n, x in i["margins"]),
                    min_margin=float(i["min_margin"]),
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SplitIntegrityError(f"malformed truth record: {exc}") from exc


# --------------------------------------------------------------------------
# Splits
# --------------------------------------------------------------------------


def records_json(records: Sequence[TruthRecord]) -> str:
    """The exact text of a split's ``truths.json``."""
    return canonical_json([r.to_json() for r in records])


def records_digest(records: Sequence[TruthRecord]) -> str:
    """SHA-256 of :func:`records_json`: the hash committed for a split."""
    return hashlib.sha256(records_json(records).encode("utf-8")).hexdigest()


class _SplitLike(Protocol):
    """What :func:`write_split` reads (``sampler.SplitResult`` provides it)."""

    @property
    def records(self) -> tuple[TruthRecord, ...]: ...

    @property
    def manifest(self) -> Mapping[str, Json]: ...

    @property
    def timing(self) -> Mapping[str, Json]: ...


def write_split(directory: Path, result: _SplitLike) -> None:
    """Write ``truths.json``, ``manifest.json`` and ``timing.json``.

    ``manifest.json`` is deterministic (indented canonical JSON); ``timing.json``
    holds wall times, which are not part of any hash.
    """
    text = records_json(result.records)
    if result.manifest.get("records_sha256") != records_digest(result.records):
        raise SplitIntegrityError("manifest hash does not match the records")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / RECORDS_FILE).write_bytes(text.encode("utf-8"))
    (directory / MANIFEST_FILE).write_bytes(_pretty(result.manifest).encode("utf-8"))
    (directory / TIMING_FILE).write_bytes(_pretty(result.timing).encode("utf-8"))


def _pretty(obj: Json) -> str:
    return (
        json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
        + "\n"
    )


def read_split(directory: Path) -> tuple[tuple[TruthRecord, ...], dict[str, Json]]:
    """Read a split, verifying the records file against the manifest's hash."""
    data = (directory / RECORDS_FILE).read_bytes()
    manifest = json.loads((directory / MANIFEST_FILE).read_text(encoding="utf-8"))
    digest = hashlib.sha256(data).hexdigest()
    if digest != manifest.get("records_sha256"):
        raise SplitIntegrityError(
            f"{directory / RECORDS_FILE}: sha256 {digest} does not match the "
            f"manifest's {manifest.get('records_sha256')}"
        )
    records = tuple(TruthRecord.from_json(r) for r in json.loads(data))
    if records_json(records).encode("utf-8") != data:
        raise SplitIntegrityError("records file is not in canonical form")
    return records, manifest

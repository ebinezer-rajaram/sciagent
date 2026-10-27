"""Recheck a split's identifiability under the current fitter (P4-PROPOSAL §2.2).

The truths of a split were accepted under the fitter of their day (the dev
split under fit/4). This script reruns the sampler's identifiability step
(``sciagent.scenarios.identify.check_identifiable``, step 7 of
``sciagent.scenarios.sampler``) on every record with the fitter of *this*
tree, and reports any truth whose status changes.

The data are rebuilt exactly as the sampler built them: the record holds the
calibrated θ, ψ and the candidate's stream prefix, and the training and
held-out logs are ``bounded_simulate`` at the identifiability horizon from
``stream(seed, f"{prefix}/train")`` and ``.../held_out``. The truth is scored
by ``GrammarScorer`` with the sampler's ``fit_config``, against the
environment's library, at the sampler's δ. Each scorer is wrapped only to keep
the score it returns, so a rejected truth still shows its margins.

Usage::

    nice -n 10 uv run python scripts/recheck_identifiable.py \\
        --split-dir data/v2/truths/dev --sha256 604acec5...4420 \\
        --workers 2 --out .cache/ident/fit5.json

Standard output is deterministic (records in id order); wall time goes to
standard error. ``--out`` writes the same rows as JSON.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import sys
import time
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

# One BLAS thread per process, set before numpy loads (as sample_truths.py).
for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

if TYPE_CHECKING:
    from sciagent.glm.data import Dataset
    from sciagent.glm.grammar import Structure
    from sciagent.scenarios.identify import (
        HeldOutScore,
        Identifiability,
        LibraryScorer,
    )
    from sciagent.scenarios.records import TruthRecord
    from sciagent.scenarios.sampler import SamplerConfig, SamplerEnvironment

#: sha256 of data/v2/truths/dev/truths.json (seed 2026, n = 20, fit/4).
DEV_SHA256 = "604acec5359d9a33b8aedc500de8dff14600041225c386766ec3544b2e8d4420"


class _Recording:
    """A library scorer that delegates and keeps the score it returned."""

    def __init__(self, inner: LibraryScorer) -> None:
        self.inner = inner
        self.scored: HeldOutScore | None = None

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def structure(self) -> Structure | None:
        return self.inner.structure

    def score(
        self, train: Sequence[Dataset], held_out: Sequence[Dataset]
    ) -> HeldOutScore:
        self.scored = self.inner.score(train, held_out)
        return self.scored


@dataclass(frozen=True)
class Recheck:
    """One truth's identifiability under this tree's fitter.

    ``status`` is ``pass`` or the sampler's rejection reason. ``margins`` are
    in nats per held-out event, for every member that was scored (all of them
    unless a fit was uncertified or failed); ``data_matches`` says that the
    rebuilt logs have the record's training and held-out event counts.
    """

    id: str
    stratum: str
    kind: str
    link: str
    status: str
    detail: str
    min_margin: float | None
    min_member: str
    margins: tuple[tuple[str, float], ...]
    truth_log_likelihood: float | None
    member_log_likelihoods: tuple[tuple[str, float], ...]
    truth_psi_recovered: bool | None
    train_events: int
    held_out_events: int
    data_matches: bool


def _no_data(head: dict[str, str], status: str, detail: str) -> Recheck:
    """A truth whose logs could not be rebuilt (simulation rejected it)."""
    return Recheck(
        id=head["id"],
        stratum=head["stratum"],
        kind=head["kind"],
        link=head["link"],
        status=status,
        detail=detail,
        min_margin=None,
        min_member="",
        margins=(),
        truth_log_likelihood=None,
        member_log_likelihoods=(),
        truth_psi_recovered=None,
        train_events=0,
        held_out_events=0,
        data_matches=False,
    )


def recheck(
    record: TruthRecord, config: SamplerConfig, env: SamplerEnvironment
) -> Recheck:
    """Rebuild the record's train/held-out logs and rerun ``check_identifiable``."""
    from sciagent.glm.data import Dataset
    from sciagent.glm.fit import FitError
    from sciagent.library.base import LibraryError
    from sciagent.scenarios.calibrate import CandidateRejectedError, bounded_simulate
    from sciagent.scenarios.identify import (
        GrammarScorer,
        check_identifiable,
        counted_events,
    )
    from sciagent.scenarios.streams import stream

    channels = env.channels
    structure = record.structure(channels)
    psi = record.psi_assignment()
    coef = record.coefficients()
    horizon = config.identifiability_horizon
    if horizon != record.identifiability.horizon:
        raise ValueError(f"{record.id}: horizon {horizon} is not the record's")
    head = {
        "id": record.id,
        "stratum": record.stratum,
        "kind": "ood" if record.out_of_dictionary else "ind",
        "link": record.link,
    }
    logs = []
    for purpose in ("train", "held_out"):
        try:
            log = bounded_simulate(
                structure,
                psi,
                coef,
                channels,
                env.mark_sampler,
                horizon,
                stream(record.seed, f"{record.stream}/{purpose}"),
                config.calibration,
            )
        except CandidateRejectedError as exc:
            return _no_data(head, exc.reason, exc.detail)
        if log is None:
            return _no_data(head, "explosion", f"the {purpose} log hit the cap")
        logs.append(log)
    train_log, held_out_log = logs
    train = [Dataset.observational(train_log, "train")]
    held_out = [Dataset.observational(held_out_log, "held_out")]
    n_held = sum(counted_events(d) for d in held_out)
    truth = _Recording(GrammarScorer("truth", structure, channels, config.fit_config))
    library = tuple(_Recording(m) for m in env.library)
    status, detail = "pass", ""
    ident: Identifiability | None = None
    try:
        ident = check_identifiable(truth, library, train, held_out, delta=config.delta)
    except CandidateRejectedError as exc:
        status, detail = exc.reason, exc.detail
    except (FitError, LibraryError) as exc:
        status, detail = "fit_failed", str(exc)
    t = truth.scored
    scored = [(m.name, m.scored) for m in library if m.scored is not None]
    margins: tuple[tuple[str, float], ...] = ()
    if ident is not None:
        margins = ident.margins
    elif t is not None and t.n_events > 0:
        # Diagnostic only: check_identifiable's formula, for the members scored.
        margins = tuple(
            (name, (t.log_likelihood - s.log_likelihood) / t.n_events)
            if math.isfinite(s.log_likelihood)
            else (name, math.inf)
            for name, s in scored
        )
    worst = min(margins, key=lambda nm: nm[1]) if margins else None
    return Recheck(
        **head,
        status=status,
        detail=detail,
        min_margin=None if worst is None else worst[1],
        min_member="" if worst is None else worst[0],
        margins=margins,
        truth_log_likelihood=None if t is None else t.log_likelihood,
        member_log_likelihoods=tuple((n, s.log_likelihood) for n, s in scored),
        truth_psi_recovered=None
        if t is None or t.psi is None
        else tuple(dict(m) for m in t.psi) == tuple(dict(m) for m in psi),
        train_events=train_log.n,
        held_out_events=n_held,
        data_matches=train_log.n == record.identifiability.train_events
        and n_held == record.identifiability.held_out_events,
    )


def _fmt(x: float | None, spec: str = ".5f") -> str:
    return "-" if x is None else format(x, spec)


def table(records: Sequence[TruthRecord], rows: Sequence[Recheck]) -> list[str]:
    """Per truth: the recheck beside the record's (accept-time) numbers."""
    lines = [
        "id       stratum kind link     status            min_margin  vs_member"
        "         recorded   d_min      d_truthLL   max|d_memberLL| data psi"
    ]
    for rec, row in zip(records, rows, strict=True):
        ri = rec.identifiability
        d_min = None if row.min_margin is None else row.min_margin - ri.min_margin
        d_truth = (
            None
            if row.truth_log_likelihood is None
            else row.truth_log_likelihood - ri.truth_log_likelihood
        )
        old = dict(ri.member_log_likelihoods)
        diffs = [abs(ll - old[n]) for n, ll in row.member_log_likelihoods]
        d_member = max(diffs) if diffs else None
        psi = "-" if row.truth_psi_recovered is None else int(row.truth_psi_recovered)
        lines.append(
            f"{row.id:8} {row.stratum:7} {row.kind:4} {row.link:8} "
            f"{row.status:17} {_fmt(row.min_margin):>10}  {row.min_member:17} "
            f"{ri.min_margin:.5f}  {_fmt(d_min, '+.2e'):>9}  "
            f"{_fmt(d_truth, '+.3e'):>10}  {_fmt(d_member, '.3e'):>9}"
            f"       {'ok' if row.data_matches else 'DIFF'} "
            f"{psi}"
        )
    for row in rows:
        if row.detail:
            lines.append(f"  {row.id}: {row.status}: {row.detail}")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--split-dir", type=Path, required=True)
    parser.add_argument(
        "--sha256", default="", help="refuse to run unless truths.json has this hash"
    )
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--ids", nargs="*", default=None, help="only these records")
    parser.add_argument("--out", type=Path, default=None, help="write rows as JSON")
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers must be ≥ 1")

    from environments.pointproc.truths_v2 import SAMPLER_CONFIG, environment
    from sciagent.glm.fit import FIT_VERSION
    from sciagent.scenarios.records import read_split
    from sciagent.scenarios.sampler import config_digest

    data = (args.split_dir / "truths.json").read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if args.sha256 and digest != args.sha256:
        parser.error(f"truths.json has sha256 {digest}, not {args.sha256}")
    records, manifest = read_split(args.split_dir)
    if args.ids is not None:
        records = tuple(r for r in records if r.id in set(args.ids))
    records = tuple(sorted(records, key=lambda r: r.id))
    config, env = SAMPLER_CONFIG, environment()
    run = partial(recheck, config=config, env=env)
    start = time.perf_counter()
    rows: list[Recheck] = []

    def progress(row: Recheck) -> None:
        # Progress and timing to stderr only; stdout stays deterministic.
        rows.append(row)
        elapsed = time.perf_counter() - start
        print(
            f"[{len(rows)}/{len(records)} {elapsed:.0f} s] {row.id} {row.status}",
            file=sys.stderr,
            flush=True,
        )

    if args.workers > 1:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            for row in pool.map(run, records):
                progress(row)
    else:
        for record in records:
            progress(run(record))
    wall = time.perf_counter() - start

    changed = [row.id for row in rows if row.status != "pass"]
    lines = [
        f"fit {FIT_VERSION}; split {args.split_dir.name} sha256 {digest}",
        f"split config digest {manifest.get('config_digest')}; "
        f"this tree's {config_digest(config, env)}",
        f"delta {config.delta} nats/event; library "
        f"{', '.join(m.name for m in env.library)}",
        *table(records, rows),
        f"{len(rows) - len(changed)}/{len(rows)} pass; "
        f"changed status (accepted -> rejected): {', '.join(changed) or 'none'}",
    ]
    print("\n".join(lines))
    print(f"wall {wall:.1f} s on {args.workers} worker(s)", file=sys.stderr)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "fit_version": FIT_VERSION,
            "split_sha256": digest,
            "delta": config.delta,
            "rows": [asdict(row) for row in rows],
        }
        args.out.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

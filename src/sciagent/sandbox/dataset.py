"""Writing collected event logs into a run's ``data/`` for the sandbox.

The framework, never the agent, puts data here (SPEC §4.0: "a sandbox over the
data the agent has collected"); the container sees ``data/`` read-only at
``/data``. Each dataset is written twice, as ``<name>.npz`` for numpy and
``<name>.csv`` for pandas, and both are byte-deterministic so a replayed run
re-executes against identical files:

- ``.npz``: arrays ``times`` and ``mark_<channel>``, float64, stored
  uncompressed with every zip timestamp fixed at 1980-01-01 (``np.savez``
  stamps the wall clock, so it is not used).
- ``.csv``: header ``time,<channel>,...``, channels sorted, LF line endings,
  every float written with ``repr``, which round-trips exactly.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Mapping
from pathlib import Path

import numpy as np
import numpy.typing as npt

from sciagent.sandbox.errors import (
    DatasetConflictError,
    SandboxConfigError,
    SandboxPathError,
)

__all__ = ["write_dataset"]

_NAME = re.compile(r"[A-Za-z0-9_]+")
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def _npy_bytes(a: npt.NDArray[np.float64]) -> bytes:
    buf = io.BytesIO()
    np.lib.format.write_array(buf, a, allow_pickle=False)
    return buf.getvalue()


def _npz_bytes(arrays: list[tuple[str, npt.NDArray[np.float64]]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as z:
        for key, arr in arrays:
            info = zipfile.ZipInfo(f"{key}.npy", date_time=_ZIP_EPOCH)
            info.external_attr = 0o644 << 16
            z.writestr(info, _npy_bytes(arr))
    return buf.getvalue()


def _csv_bytes(columns: list[tuple[str, npt.NDArray[np.float64]]]) -> bytes:
    lines = [",".join(name for name, _ in columns)]
    n = columns[0][1].size
    for i in range(n):
        lines.append(",".join(repr(float(col[i])) for _, col in columns))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _write_once(path: Path, data: bytes) -> None:
    if path.exists():
        if path.read_bytes() != data:
            raise DatasetConflictError(
                f"{path} already exists with different contents; data/ is write-once"
            )
        return
    tmp = path.with_name(path.name + ".partial")
    tmp.write_bytes(data)
    tmp.replace(path)


def write_dataset(
    run_dir: Path,
    name: str,
    times: npt.ArrayLike,
    marks: Mapping[str, npt.ArrayLike],
) -> tuple[Path, Path]:
    """Write ``data/<name>.npz`` and ``data/<name>.csv`` under ``run_dir``.

    ``times`` is one float per event; ``marks`` maps a channel name to one
    float per event (the :class:`~sciagent.glm.data.EventLog` shape, taken as
    plain arrays so the sandbox stays independent of the GLM package). Writing
    the same name again with identical contents is a no-op.

    Returns:
        The ``.npz`` and ``.csv`` paths, in that order.

    Raises:
        SandboxPathError: ``name`` or a channel name is outside ``[A-Za-z0-9_]+``.
        SandboxConfigError: ``times`` is not one-dimensional, or a channel's
            length differs from it.
        DatasetConflictError: ``name`` exists with different contents.
    """
    if not _NAME.fullmatch(name):
        raise SandboxPathError(f"dataset name {name!r} must match [A-Za-z0-9_]+")
    t = np.ascontiguousarray(times, dtype=np.float64)
    if t.ndim != 1:
        raise SandboxConfigError(f"times must be one-dimensional, got shape {t.shape}")
    channels: list[tuple[str, npt.NDArray[np.float64]]] = []
    for ch in sorted(marks):
        if not _NAME.fullmatch(ch):
            raise SandboxPathError(f"channel name {ch!r} must match [A-Za-z0-9_]+")
        m = np.ascontiguousarray(marks[ch], dtype=np.float64)
        if m.shape != t.shape:
            raise SandboxConfigError(
                f"channel {ch!r} has shape {m.shape}, times has {t.shape}"
            )
        channels.append((ch, m))

    data_dir = run_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    npz = data_dir / f"{name}.npz"
    csv = data_dir / f"{name}.csv"
    npz_bytes = _npz_bytes([("times", t), *((f"mark_{c}", m) for c, m in channels)])
    csv_bytes = _csv_bytes([("time", t), *channels])
    _write_once(npz, npz_bytes)
    _write_once(csv, csv_bytes)
    return npz, csv

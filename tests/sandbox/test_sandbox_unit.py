"""Fast tests of the sandbox's host-side pieces (no Docker needed).

The isolation tests proper (SPEC §6.3 no. 8) run real containers and live in
``test_sandbox_isolation.py``; this file covers what can be decided on the host
alone: the replay digest (§6.3 no. 9's perturbation check), the image tag, the
deterministic dataset writer, Windows mount paths, and the exact ``docker run``
command line, which is where every isolation flag is either present or not.
"""

from __future__ import annotations

import csv
import dataclasses
import io
import itertools
import zipfile
from pathlib import Path, PureWindowsPath
from typing import Any

import numpy as np
import pytest

from sciagent.core.errors import SciAgentError
from sciagent.sandbox import (
    DatasetConflictError,
    Sandbox,
    SandboxConfigError,
    SandboxError,
    SandboxLimits,
    SandboxPathError,
    SandboxResult,
    SandboxUnavailableError,
    build_run_argv,
    ensure_image,
    image_tag,
    mount_source,
    result_digest,
    write_dataset,
)
from sciagent.sandbox.image import SANDBOX_CONTEXT

# --------------------------------------------------------------------------
# result_digest: what replay compares
# --------------------------------------------------------------------------


_BASE = SandboxResult(
    stdout="mean=0.4931\n",
    stderr="",
    exit_code=0,
    timed_out=False,
    truncated=False,
    duration_s=1.25,
)


def _result(**changes: Any) -> SandboxResult:
    return dataclasses.replace(_BASE, **changes)


def test_digest_is_stable_hex() -> None:
    d = result_digest(_result())
    assert d == result_digest(_result())
    assert len(d) == 64
    int(d, 16)


def test_digest_ignores_duration() -> None:
    assert result_digest(_result(duration_s=0.1)) == result_digest(
        _result(duration_s=99.0)
    )
    # And so does equality: two runs of the same call compare equal.
    assert _result(duration_s=0.1) == _result(duration_s=99.0)


def test_digest_detects_one_byte_stdout_perturbation() -> None:
    """SPEC §6.3 no. 9: a deliberately perturbed sandbox result is detected."""
    original = _result()
    perturbed = dataclasses.replace(original, stdout="mean=0.4932\n")
    assert len(perturbed.stdout.encode()) == len(original.stdout.encode())
    assert result_digest(perturbed) != result_digest(original)
    appended = dataclasses.replace(original, stdout=original.stdout + " ")
    assert result_digest(appended) != result_digest(original)


@pytest.mark.parametrize(
    "change",
    [
        {"stderr": "warning\n"},
        {"exit_code": 1},
        {"timed_out": True},
        {"truncated": True},
    ],
)
def test_digest_covers_every_replayed_field(change: dict[str, Any]) -> None:
    assert result_digest(_result(**change)) != result_digest(_result())


def test_digest_does_not_confuse_field_boundaries() -> None:
    a = _result(stdout="ab", stderr="c")
    b = _result(stdout="a", stderr="bc")
    assert result_digest(a) != result_digest(b)


# --------------------------------------------------------------------------
# Errors and limits
# --------------------------------------------------------------------------


def test_errors_derive_from_sciagent_error() -> None:
    for cls in (
        SandboxError,
        SandboxUnavailableError,
        SandboxConfigError,
        SandboxPathError,
        DatasetConflictError,
    ):
        assert issubclass(cls, SciAgentError)
        assert issubclass(cls, SandboxError)


@pytest.mark.parametrize(
    "bad",
    [
        {"wall_seconds": 0},
        {"wall_seconds": float("nan")},
        {"memory_mb": 0},
        {"cpus": 0},
        {"pids": 1},
        {"output_bytes": 0},
        {"max_file_mb": 0},
    ],
)
def test_limits_reject_nonsense(bad: dict[str, Any]) -> None:
    with pytest.raises(SandboxConfigError):
        dataclasses.replace(SandboxLimits(), **bad)


def test_limit_defaults() -> None:
    lim = SandboxLimits()
    assert (lim.wall_seconds, lim.memory_mb, lim.cpus, lim.pids) == (60, 2048, 1.0, 256)
    assert lim.output_bytes == 20_000


# --------------------------------------------------------------------------
# Image tag
# --------------------------------------------------------------------------


def test_image_tag_is_content_addressed(tmp_path: Path) -> None:
    tag = image_tag()
    assert tag.startswith("sciagent-sandbox:")
    assert tag == image_tag(SANDBOX_CONTEXT)

    ctx = tmp_path / "ctx"
    ctx.mkdir()
    dockerfile = (SANDBOX_CONTEXT / "Dockerfile").read_bytes()
    lock = (SANDBOX_CONTEXT / "requirements.lock").read_bytes()
    (ctx / "Dockerfile").write_bytes(dockerfile)
    (ctx / "requirements.lock").write_bytes(lock)
    assert image_tag(ctx) == tag

    # Line endings do not move the tag (a CRLF checkout is the same image) ...
    (ctx / "Dockerfile").write_bytes(
        dockerfile.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    )
    assert image_tag(ctx) == tag
    # ... but a change to the lock does.
    (ctx / "requirements.lock").write_bytes(lock + b"\n# changed\n")
    assert image_tag(ctx) != tag


def test_image_tag_missing_context_is_typed(tmp_path: Path) -> None:
    with pytest.raises(SandboxError):
        image_tag(tmp_path)


# --------------------------------------------------------------------------
# Docker missing
# --------------------------------------------------------------------------


def test_missing_docker_is_unavailable_error(tmp_path: Path) -> None:
    with pytest.raises(SandboxUnavailableError, match="Docker"):
        ensure_image(docker="sciagent-no-such-docker-binary")
    sb = Sandbox(
        tmp_path,
        image="sciagent-sandbox:none",
        limits=SandboxLimits(),
        seed=0,
        docker="sciagent-no-such-docker-binary",
    )
    with pytest.raises(SandboxUnavailableError):
        sb.run("print(1)")


# --------------------------------------------------------------------------
# Mount paths
# --------------------------------------------------------------------------


def test_mount_source_uses_forward_slashes_on_windows_paths() -> None:
    p = PureWindowsPath(r"C:\Users\Some One\AppData\Local\Temp\run 1\data")
    assert mount_source(p) == "C:/Users/Some One/AppData/Local/Temp/run 1/data"


@pytest.mark.parametrize("bad", [r"C:\a,b\data", 'C:\\a"b\\data', "C:\\a\nb"])
def test_mount_source_refuses_unrepresentable_paths(bad: str) -> None:
    with pytest.raises(SandboxPathError):
        mount_source(PureWindowsPath(bad))


# --------------------------------------------------------------------------
# The command line: every isolation flag is present, nothing else leaks
# --------------------------------------------------------------------------


def _argv(tmp_path: Path, seed: int = 7) -> list[str]:
    return build_run_argv(
        docker="docker",
        name="sciagent-sbx-test",
        image="sciagent-sandbox:abc",
        data_dir=tmp_path / "data",
        work_dir=tmp_path / "work",
        limits=SandboxLimits(memory_mb=512, cpus=1.0, pids=64, max_file_mb=32),
        seed=seed,
        cidfile=tmp_path / "cid",
    )


def _pairs(argv: list[str]) -> list[tuple[str, str]]:
    return list(itertools.pairwise(argv))


def test_run_argv_carries_every_isolation_flag(tmp_path: Path) -> None:
    argv = _argv(tmp_path)
    pairs = _pairs(argv)
    assert argv[:2] == ["docker", "run"]
    for flag in ("--rm", "-i", "--read-only", "--init"):
        assert flag in argv
    expected = [
        ("--network", "none"),
        ("--memory", "512m"),
        ("--memory-swap", "512m"),
        ("--cpus", "1.0"),
        ("--pids-limit", "64"),
        ("--cap-drop", "ALL"),
        ("--security-opt", "no-new-privileges"),
        ("--user", "65534:65534"),
        ("--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m"),
        ("--pull", "never"),
        ("--hostname", "sandbox"),
        ("--workdir", "/work"),
        ("--name", "sciagent-sbx-test"),
        ("--ulimit", f"fsize={32 * 1024 * 1024}"),
    ]
    for pair in expected:
        assert pair in pairs, pair
    assert argv[-4:] == ["sciagent-sandbox:abc", "python", "-B", "-"]


def test_run_argv_mounts_exactly_data_ro_and_work_rw(tmp_path: Path) -> None:
    argv = _argv(tmp_path)
    mounts = [b for a, b in _pairs(argv) if a == "--mount"]
    data = mount_source(tmp_path / "data")
    work = mount_source(tmp_path / "work")
    assert mounts == [
        f"type=bind,source={data},target=/data,readonly",
        f"type=bind,source={work},target=/work",
    ]
    assert "-v" not in argv and "--volume" not in argv
    assert "--privileged" not in argv


def test_run_argv_environment_is_explicit_and_minimal(tmp_path: Path) -> None:
    argv = _argv(tmp_path, seed=12345)
    envs = [b for a, b in _pairs(argv) if a in ("-e", "--env")]
    assert all("=" in e for e in envs), "a bare -e NAME would copy the host's value"
    env = dict(e.split("=", 1) for e in envs)
    assert env == {
        "SANDBOX_SEED": "12345",
        "PYTHONHASHSEED": "0",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "HOME": "/tmp",
        "TZ": "UTC",
        "LC_ALL": "C.UTF-8",
    }
    assert "--env-file" not in argv


# --------------------------------------------------------------------------
# Sandbox construction
# --------------------------------------------------------------------------


def test_sandbox_creates_data_and_work(tmp_path: Path) -> None:
    Sandbox(tmp_path, image="x:y", limits=SandboxLimits(), seed=0)
    assert (tmp_path / "data").is_dir()
    assert (tmp_path / "work").is_dir()


def test_sandbox_refuses_missing_run_dir_and_bad_seed(tmp_path: Path) -> None:
    with pytest.raises(SandboxConfigError):
        Sandbox(tmp_path / "absent", image="x:y", limits=SandboxLimits(), seed=0)
    with pytest.raises(SandboxConfigError):
        Sandbox(tmp_path, image="x:y", limits=SandboxLimits(), seed=-1)


# --------------------------------------------------------------------------
# write_dataset
# --------------------------------------------------------------------------


def _data() -> tuple[np.ndarray, dict[str, np.ndarray]]:
    times = np.array([0.1, 0.25, 1.0 / 3.0, 2.0, 7.5])
    marks = {"size": np.array([1.0, 2.5, 0.1, 3.0, 1e-17]), "depth": np.arange(5.0)}
    return times, marks


def test_write_dataset_is_byte_deterministic(tmp_path: Path) -> None:
    times, marks = _data()
    for d in ("a", "b"):
        (tmp_path / d).mkdir()
        write_dataset(tmp_path / d, "obs", times, marks)
    for ext in ("npz", "csv"):
        a = (tmp_path / "a" / "data" / f"obs.{ext}").read_bytes()
        b = (tmp_path / "b" / "data" / f"obs.{ext}").read_bytes()
        assert a == b
    # The zip carries no wall-clock timestamp.
    with zipfile.ZipFile(tmp_path / "a" / "data" / "obs.npz") as z:
        assert all(i.date_time == (1980, 1, 1, 0, 0, 0) for i in z.infolist())


def test_write_dataset_round_trips(tmp_path: Path) -> None:
    times, marks = _data()
    paths = write_dataset(tmp_path, "obs", times, marks)
    assert [p.name for p in paths] == ["obs.npz", "obs.csv"]
    with np.load(tmp_path / "data" / "obs.npz") as z:
        assert sorted(z.files) == ["mark_depth", "mark_size", "times"]
        np.testing.assert_array_equal(z["times"], times)
        np.testing.assert_array_equal(z["mark_size"], marks["size"])
    text = (tmp_path / "data" / "obs.csv").read_text(encoding="utf-8")
    assert "\r" not in text
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == ["time", "depth", "size"]  # channels sorted
    got = np.array([[float(x) for x in r] for r in rows[1:]])
    np.testing.assert_array_equal(got[:, 0], times)  # exact: repr round-trips
    np.testing.assert_array_equal(got[:, 2], marks["size"])


def test_write_dataset_refuses_bad_input(tmp_path: Path) -> None:
    times, marks = _data()
    for name in ("", "../x", "a/b", "a.b", "con dir"):
        with pytest.raises(SandboxPathError):
            write_dataset(tmp_path, name, times, marks)
    with pytest.raises(SandboxPathError):
        write_dataset(tmp_path, "obs", times, {"bad-name": marks["size"]})
    with pytest.raises(SandboxConfigError):
        write_dataset(tmp_path, "obs", times, {"size": marks["size"][:3]})
    with pytest.raises(SandboxConfigError):
        write_dataset(tmp_path, "obs", times.reshape(5, 1), marks)


def test_write_dataset_is_write_once(tmp_path: Path) -> None:
    times, marks = _data()
    write_dataset(tmp_path, "obs", times, marks)
    write_dataset(tmp_path, "obs", times, marks)  # identical: a no-op
    with pytest.raises(DatasetConflictError):
        write_dataset(tmp_path, "obs", times + 1.0, marks)

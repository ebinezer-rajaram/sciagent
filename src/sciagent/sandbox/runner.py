"""Running the open agent's ``python(code)`` calls in an isolated container.

SPEC §4.0: the open agent (AG-o) gets a sandbox "over the data the agent has
collected (numpy, scipy, pandas, statsmodels; no network, no filesystem
outside the run, CPU and time limits)". This module is that boundary, and it
is an instrument (SPEC §6.3 no. 8): the invariant that the agent may compute
any number but never set one the evaluation reads rests on it.

**One call, one container.** Every :meth:`Sandbox.run` starts a fresh
container process and removes it afterwards; there is no persistent kernel.
State survives between calls only as files the code writes under ``/work``
(the run's ``work/`` directory). Replay is therefore a deterministic
re-execution of the recorded call sequence against the same ``data/`` and a
``work/`` rebuilt by the earlier calls. The agent's system prompt must say so:
a variable defined in one call does not exist in the next.

**What the container sees.**

- ``/data``: the run's ``data/``, read-only. The framework writes collected
  event logs there (:func:`~sciagent.sandbox.dataset.write_dataset`).
- ``/work``: the run's ``work/``, read-write, and the working directory.
- ``/tmp``: a 64 MB ``noexec`` tmpfs. The rest of the root is read-only.
- Nothing else of the host: no other mount, no network (``--network none``,
  loopback only), no host environment variable (every ``-e`` carries an
  explicit value), no capabilities, ``no-new-privileges``, uid 65534.

Everything else of the run -- the truth, its seed, the held-out data, the
scorer -- lives outside ``data/`` and ``work/`` and so is simply not in the
container's mount namespace. The tests in ``tests/sandbox`` try to reach each.

**Known exposure: the host path of the run directory.** ``/proc/self/mountinfo``
shows the host source of each bind mount, so the code can read the absolute
host path of ``data/`` and ``work/`` (on Windows, e.g.
``/Users/<name>/.../<run dir>/work``). Docker gives no way to hide it short of
replacing bind mounts with volumes. The caller must therefore name run
directories opaquely: no truth id, seed, scenario, system, model or
named/anonymised condition anywhere in the path.

**Determinism.** Numerics are single-threaded (``OMP_NUM_THREADS`` and kin
set to 1), ``PYTHONHASHSEED=0``, ``TZ=UTC``, the hostname is fixed, and the
run's seed is in ``SANDBOX_SEED``. Code that wants randomness must seed from
it, ``np.random.default_rng(int(os.environ["SANDBOX_SEED"]))``; code that
reads the clock, ``os.urandom`` or an unseeded generator is not reproducible,
and replay will report the mismatch (that is the agent's problem, and the
system prompt says so). Same code + same ``data/`` + same ``work/`` + same
seed + same image gives the same stdout.

**Results.** Agent code that raises, exits non-zero, times out or is killed
for memory is a :class:`SandboxResult`, never an exception. Exceptions are for
harness faults only (:mod:`sciagent.sandbox.errors`).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import IO, Final

from sciagent.sandbox.errors import (
    SandboxConfigError,
    SandboxError,
    SandboxImageError,
    SandboxPathError,
    SandboxUnavailableError,
)
from sciagent.sandbox.image import check_docker

__all__ = [
    "SANDBOX_UID",
    "TIMEOUT_EXIT_CODE",
    "Sandbox",
    "SandboxLimits",
    "SandboxResult",
    "build_run_argv",
    "mount_source",
    "result_digest",
]

#: ``exit_code`` of a call killed for exceeding ``wall_seconds``. Fixed rather
#: than whatever Docker reports for a removed container, so the digest of a
#: timed-out call does not depend on how the kill raced the exit.
TIMEOUT_EXIT_CODE: Final = -9

#: ``nobody:nogroup`` in the Debian base image.
SANDBOX_UID: Final = "65534:65534"

#: Size of the in-container ``/tmp``.
_TMPFS: Final = "/tmp:rw,noexec,nosuid,nodev,size=64m"

#: Grace for the Docker client to exit after its container was removed.
_REAP_TIMEOUT_S: Final = 30.0

_DIGEST_VERSION: Final = 1

#: ``stat.FILE_ATTRIBUTE_*`` values, spelled out because the ``stat`` module
#: defines them on Windows only.
_FILE_ATTRIBUTE_DIRECTORY: Final = 0x10
_FILE_ATTRIBUTE_REPARSE_POINT: Final = 0x400


@dataclass(frozen=True)
class SandboxLimits:
    """Per-call resource limits.

    ``wall_seconds`` is wall-clock from the host, measured from launching the
    Docker client, so it includes container start-up (about a second on Docker
    Desktop). ``output_bytes`` caps stdout and stderr *each*; the excess is
    drained and discarded, and a marker line is appended. ``max_file_mb`` is
    ``RLIMIT_FSIZE``: the largest single file the code may write.
    """

    wall_seconds: float = 60
    memory_mb: int = 2048
    cpus: float = 1.0
    pids: int = 256
    output_bytes: int = 20_000
    max_file_mb: int = 256

    def __post_init__(self) -> None:
        if not (math.isfinite(self.wall_seconds) and self.wall_seconds > 0):
            raise SandboxConfigError(
                f"wall_seconds must be > 0, got {self.wall_seconds}"
            )
        if self.memory_mb < 16:
            raise SandboxConfigError(f"memory_mb must be >= 16, got {self.memory_mb}")
        if not (math.isfinite(self.cpus) and self.cpus > 0):
            raise SandboxConfigError(f"cpus must be > 0, got {self.cpus}")
        if self.pids < 8:
            # tini, python and a little headroom.
            raise SandboxConfigError(f"pids must be >= 8, got {self.pids}")
        if self.output_bytes < 1:
            raise SandboxConfigError(
                f"output_bytes must be >= 1, got {self.output_bytes}"
            )
        if self.max_file_mb < 1:
            raise SandboxConfigError(
                f"max_file_mb must be >= 1, got {self.max_file_mb}"
            )


@dataclass(frozen=True)
class SandboxResult:
    """What one ``python(code)`` call produced.

    ``duration_s`` is wall time as measured on the host. It is recorded for
    sizing runs and is **excluded** from equality and from
    :func:`result_digest`, which is what replay compares: two executions of
    the same call never take the same time.
    """

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    truncated: bool
    duration_s: float = field(compare=False)


def result_digest(r: SandboxResult) -> str:
    """Return the SHA-256 hex digest replay compares for a sandbox result.

    Covers ``stdout``, ``stderr``, ``exit_code``, ``timed_out`` and
    ``truncated``, serialised as canonical JSON so field boundaries cannot be
    confused; not ``duration_s``. A one-byte change to any covered field
    changes the digest (SPEC §6.3 no. 9).
    """
    payload = {
        "v": _DIGEST_VERSION,
        "stdout": r.stdout,
        "stderr": r.stderr,
        "exit_code": r.exit_code,
        "timed_out": r.timed_out,
        "truncated": r.truncated,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("ascii")).hexdigest()


def mount_source(path: PurePath) -> str:
    """Return ``path`` in the form ``docker --mount source=`` accepts.

    Forward slashes throughout (``C:/Users/...`` on Windows, which Docker
    Desktop maps into its VM). ``--mount`` takes comma-separated ``key=value``
    fields, so a comma, quote or control character in the path would split or
    corrupt the field; those are refused rather than escaped.

    Raises:
        SandboxPathError: the path contains ``,``, ``"`` or a control character.
    """
    s = path.as_posix()
    if any(c in s for c in ',"') or any(ord(c) < 32 for c in s):
        raise SandboxPathError(f"cannot mount {s!r}: comma, quote or control character")
    return s


def _environment(seed: int) -> list[tuple[str, str]]:
    return [
        ("SANDBOX_SEED", str(seed)),
        ("PYTHONHASHSEED", "0"),
        ("OMP_NUM_THREADS", "1"),
        ("OPENBLAS_NUM_THREADS", "1"),
        ("MKL_NUM_THREADS", "1"),
        ("PYTHONDONTWRITEBYTECODE", "1"),
        ("PYTHONIOENCODING", "utf-8"),
        ("HOME", "/tmp"),
        ("TZ", "UTC"),
        ("LC_ALL", "C.UTF-8"),
    ]


def build_run_argv(
    *,
    docker: str,
    name: str,
    image: str,
    data_dir: Path,
    work_dir: Path,
    limits: SandboxLimits,
    seed: int,
    cidfile: Path,
) -> list[str]:
    """Return the full ``docker run`` command line for one call.

    The code itself is not on the command line: it is piped to ``python -B -``
    on stdin. ``--cidfile`` lets the caller tell Docker's own failures (no
    container was created) from the code's exit status, which may legitimately
    be 125-127 too.
    """
    argv = [
        docker,
        "run",
        "--rm",
        "-i",
        "--init",
        "--name",
        name,
        "--cidfile",
        str(cidfile),
        "--pull",
        "never",
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
        _TMPFS,
        "--memory",
        f"{limits.memory_mb}m",
        "--memory-swap",
        f"{limits.memory_mb}m",
        "--cpus",
        repr(float(limits.cpus)),
        "--pids-limit",
        str(limits.pids),
        "--ulimit",
        f"fsize={limits.max_file_mb * 1024 * 1024}",
        "--ulimit",
        "nofile=1024:1024",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        SANDBOX_UID,
        "--hostname",
        "sandbox",
        "--workdir",
        "/work",
        "--mount",
        f"type=bind,source={mount_source(data_dir)},target=/data,readonly",
        "--mount",
        f"type=bind,source={mount_source(work_dir)},target=/work",
    ]
    for key, value in _environment(seed):
        argv += ["-e", f"{key}={value}"]
    argv += [image, "python", "-B", "-"]
    return argv


class _CappedReader(threading.Thread):
    """Drain a pipe to EOF, keeping only the first ``cap`` bytes."""

    def __init__(self, pipe: IO[bytes], cap: int) -> None:
        super().__init__(daemon=True)
        self._pipe = pipe
        self._cap = cap
        self.kept = bytearray()
        self.overflow = False

    def run(self) -> None:
        while chunk := self._pipe.read(65536):
            room = self._cap - len(self.kept)
            if room > 0:
                self.kept += chunk[:room]
            if len(chunk) > room:
                self.overflow = True
        self._pipe.close()

    def text(self, stream: str) -> str:
        out = bytes(self.kept).decode("utf-8", "replace")
        if self.overflow:
            out += f"\n[sciagent sandbox: {stream} truncated at {self._cap} bytes]\n"
        return out


class _Feeder(threading.Thread):
    """Write the code to the container's stdin and close it.

    A thread, so a large snippet cannot block the caller before the wall-clock
    timer starts.
    """

    def __init__(self, pipe: IO[bytes], data: bytes) -> None:
        super().__init__(daemon=True)
        self._pipe = pipe
        self._data = data

    def run(self) -> None:
        try:
            self._pipe.write(self._data)
            self._pipe.close()
        except OSError:
            # Docker exited before reading the code (BrokenPipeError, or EINVAL
            # on Windows). Either the container never started, which run()
            # reports from the cidfile, or it was removed on timeout.
            return


def _sweep_special_files(work_dir: Path) -> list[str]:
    """Delete every entry under ``work_dir`` that is not a regular file or directory.

    Returns the removed paths, relative and POSIX-style, in sorted order.

    The container can create symbolic links and FIFOs in ``/work``. Inside it
    they lead nowhere (its mount namespace holds no host path), but the host
    sees them too. On a Linux host a link in ``work/`` to ``~/.ssh`` would be
    followed by any framework code that read ``work/``; on Docker Desktop for
    Windows they become WSL reparse points (``IO_REPARSE_TAG_LX_SYMLINK``)
    that Windows cannot open, so ``stat`` and ``shutil.rmtree`` on the run
    directory fail with WinError 1920. Removing them after every call closes
    both. It is deterministic, so replay sees the same ``work/``, and the
    agent is told in stderr.
    """
    removed: list[str] = []

    def walk(d: Path, rel: str) -> None:
        with os.scandir(d) as it:
            entries = sorted(it, key=lambda e: e.name)
        for e in entries:
            st = os.lstat(e.path)
            attrs = getattr(st, "st_file_attributes", 0)
            reparse = bool(attrs & _FILE_ATTRIBUTE_REPARSE_POINT)
            name = f"{rel}{e.name}"
            if not reparse and stat.S_ISDIR(st.st_mode):
                walk(Path(e.path), f"{name}/")
            elif reparse or not stat.S_ISREG(st.st_mode):
                if attrs & _FILE_ATTRIBUTE_DIRECTORY:
                    os.rmdir(e.path)
                else:
                    os.unlink(e.path)
                removed.append(name)

    walk(work_dir, "")
    return sorted(removed)


class Sandbox:
    """Runs ``python(code)`` calls for one investigation, each in a fresh container.

    ``run_dir`` must exist; ``data/`` and ``work/`` are created under it if
    absent and are the only host paths the container sees. ``image`` is a tag
    from :func:`~sciagent.sandbox.image.ensure_image`; the tool layer records
    it in the transcript. ``seed`` is exported as ``SANDBOX_SEED`` to every
    call.
    """

    def __init__(
        self,
        run_dir: Path,
        *,
        image: str,
        limits: SandboxLimits,
        seed: int,
        docker: str = "docker",
    ) -> None:
        if not run_dir.is_dir():
            raise SandboxConfigError(f"run directory {run_dir} does not exist")
        if isinstance(seed, bool) or not 0 <= seed < 2**64:
            raise SandboxConfigError(
                f"seed must be an integer in [0, 2**64), got {seed!r}"
            )
        self.run_dir: Final = run_dir.resolve()
        self.image: Final = image
        self.limits: Final = limits
        self.seed: Final = seed
        self.docker: Final = docker
        self.data_dir: Final = self.run_dir / "data"
        self.work_dir: Final = self.run_dir / "work"
        self.data_dir.mkdir(exist_ok=True)
        self.work_dir.mkdir(exist_ok=True)
        # Validate the mount paths now, not on the first call.
        mount_source(self.data_dir)
        mount_source(self.work_dir)
        self._last_container_name: str | None = None

    @property
    def last_container_name(self) -> str | None:
        """The container name of the most recent call (for leak checks)."""
        return self._last_container_name

    def run(self, code: str) -> SandboxResult:
        """Run ``code`` with ``python -`` in a fresh container; return what it did.

        Raises:
            SandboxUnavailableError: Docker cannot be reached.
            SandboxImageError: ``image`` is not present locally (runs never pull).
            SandboxError: Docker failed to create the container for another reason.
        """
        # A unique name so a timed-out container can be removed by name. The
        # name never reaches the result, so its randomness cannot move output.
        name = f"sciagent-sbx-{uuid.uuid4().hex[:20]}"
        self._last_container_name = name
        with tempfile.TemporaryDirectory(prefix="sciagent-sbx-") as tmp:
            cidfile = Path(tmp) / "cid"
            argv = build_run_argv(
                docker=self.docker,
                name=name,
                image=self.image,
                data_dir=self.data_dir,
                work_dir=self.work_dir,
                limits=self.limits,
                seed=self.seed,
                cidfile=cidfile,
            )
            t0 = time.monotonic()
            try:
                proc = subprocess.Popen(
                    argv,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except FileNotFoundError as exc:
                raise SandboxUnavailableError(
                    f"Docker CLI {self.docker!r} not found on PATH; the python "
                    "sandbox needs Docker (Docker Desktop on Windows)"
                ) from exc
            assert proc.stdin is not None and proc.stdout is not None
            assert proc.stderr is not None
            out = _CappedReader(proc.stdout, self.limits.output_bytes)
            err = _CappedReader(proc.stderr, self.limits.output_bytes)
            out.start()
            err.start()
            feeder = _Feeder(proc.stdin, code.encode("utf-8"))
            feeder.start()
            timed_out = False
            try:
                rc = proc.wait(timeout=self.limits.wall_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._remove(name)
                try:
                    rc = proc.wait(timeout=_REAP_TIMEOUT_S)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    rc = proc.wait()
            feeder.join()
            out.join()
            err.join()
            duration = time.monotonic() - t0
            created = cidfile.is_file() and cidfile.read_text().strip() != ""

        if not created and not timed_out:
            self._raise_docker_failure(rc, bytes(err.kept).decode("utf-8", "replace"))
        removed = _sweep_special_files(self.work_dir)
        notes = "".join(
            f"[sciagent sandbox: removed non-regular file work/{r}]\n" for r in removed
        )
        return SandboxResult(
            stdout=out.text("stdout"),
            stderr=err.text("stderr") + notes,
            exit_code=TIMEOUT_EXIT_CODE if timed_out else rc,
            timed_out=timed_out,
            truncated=out.overflow or err.overflow,
            duration_s=duration,
        )

    def _remove(self, name: str) -> None:
        """Force-remove a container by name (kill, then delete)."""
        subprocess.run(
            [self.docker, "rm", "-f", name],
            capture_output=True,
            timeout=_REAP_TIMEOUT_S,
            check=False,
        )

    def _raise_docker_failure(self, rc: int, stderr: str) -> None:
        # Raises SandboxUnavailableError if the daemon is down.
        check_docker(self.docker)
        if "No such image" in stderr or "pull access denied" in stderr:
            raise SandboxImageError(
                f"sandbox image {self.image} is not present; call ensure_image() "
                f"first:\n{stderr.strip()}"
            )
        raise SandboxError(
            f"docker run failed before the container started (exit {rc}):\n"
            f"{stderr.strip()}"
        )

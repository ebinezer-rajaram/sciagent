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

**Who owns ``work/``.** On native Linux Docker the container's uid 65534 is
a uid the host user does not own: a subordinate uid (``/etc/subuid``) under
rootless Docker, the host's ``nobody`` under rootful. Without help the code
could not write to the host-created ``work/``, and the host could not sweep
or remove what the code wrote. So on native Linux every call is bracketed by
an *ownership handoff* (:func:`build_handoff_argv`): a short helper container
from the same image, running no agent code, gives ``work/`` to 65534 before
the call and back to the host user after it (:mod:`._handoff_helper`:
``lchown``, ``chmod u+rwX,go-w``, no set-id file, no POSIX ACL). The agent's
own container is unchanged -- same flags, uid 65534, no capability -- and the
host user owns every file of ``work/`` between calls, whatever modes or ACLs
the code set. Docker Desktop (Windows, macOS) maps ownership to the host user
itself and gets no handoff (:func:`handoff_owner`).

The handoff is part of the boundary, so it is strict about order and
outcome. Both handoffs sit in one ``try``/``finally``, so ``work/`` is handed
back whatever failed in between. The back handoff starts only after
``docker inspect`` has confirmed the agent's container gone; if it cannot be,
nothing is handed back and the call raises. Afterwards ``work/`` must be the
host user's, or the call raises. A sandbox whose back handoff failed refuses
further calls; :func:`recover` (and :meth:`Sandbox.recover`) removes any
container still mounting ``work/`` and hands it back, for cleanup and for a
session killed mid-call.

The platform detection behind the handoff (``docker info``) is checked once
per sandbox against what the daemon does: a container running as uid 65534
writes a file, and the file's owner on the host must be the one the detected
platform implies (:func:`_check_platform`); otherwise
:class:`~sciagent.sandbox.errors.SandboxPlatformError`. Without the check, a
wrong answer would surface only as ``PermissionError`` in the agent's code --
recorded as the agent's own result.

**What the code may leave in ``work/``.** At most ``max_work_entries``
entries, none with a path longer than ``max_work_path_bytes``
(:class:`SandboxLimits`); past either, the back handoff empties ``work/`` and
the call's stderr says so. Files are the code's doing, like its time and
memory, so this is a result of the call, recorded and replayed, never a
harness fault; and it is a count, not a time, so replay reproduces it and
the handoff's own time stays bounded (LOG 2026-10-27). Enforced only where
there is a handoff (native Linux).

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

import dataclasses
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import IO, Final

from sciagent.sandbox._handoff_helper import EMPTIED as HELPER_EMPTIED
from sciagent.sandbox._handoff_helper import OK as HELPER_OK
from sciagent.sandbox.errors import (
    SandboxConfigError,
    SandboxError,
    SandboxImageError,
    SandboxPathError,
    SandboxPlatformError,
    SandboxUnavailableError,
)
from sciagent.sandbox.image import check_docker

__all__ = [
    "HANDOFF_HELPER_SHA256",
    "SANDBOX_UID",
    "TIMEOUT_EXIT_CODE",
    "Sandbox",
    "SandboxLimits",
    "SandboxResult",
    "build_handoff_argv",
    "build_probe_argv",
    "build_run_argv",
    "canonical_run_argv",
    "handoff_owner",
    "mount_source",
    "recover",
    "recover_run_dirs",
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

#: Least time allowed for one ownership handoff (:func:`_handoff_timeout`).
_HANDOFF_TIMEOUT_S: Final = 300.0

#: Handoff time allowed per second of ``wall_seconds``. A back handoff walks
#: at most the entry limit, unless the call just wrote more, which it then
#: deletes; what one call can write grows with its wall time (and, for
#: directories, with its memory: their dentries and inodes are charged to the
#: container). The deletion's cost is mostly reading back directory blocks
#: the call's memory limit pushed out of the page cache, so it is bound by
#: the disk's IOPS, not by CPU. Measured on dm-devbox under load
#: (2026-10-27), at the production limits (60 s, 2,048 MB): a call writing
#: files built 1.7 million, deleted in 163 s; a call nesting directories
#: (``mkdir`` + ``chdir``) built a chain 910,000 deep, deleted in about
#: 390 s. 20 s per second (1,200 s) leaves a margin of about 3 over the
#: slower. That is a measured margin over the shapes tried, on this disk, not
#: a proof that agent code cannot reach the bound; reaching it is a harness
#: fault (``SandboxError``), which voids the run.
_HANDOFF_SECONDS_PER_WALL_SECOND: Final = 20.0

#: Upper bound on a recovery handoff (:func:`recover`): after a session was
#: killed, the agent's container may have run far past ``wall_seconds``.
_RECOVERY_TIMEOUT_S: Final = 1800.0

#: How long a removed container may take to disappear, and the polling step.
_GONE_TIMEOUT_S = 30.0
_GONE_POLL_S = 0.5

#: The only capabilities the handoff helper holds: chown, and chmod/traverse
#: whatever modes the code left, and strip the code's ACLs.
_HANDOFF_CAPS: Final = ("CHOWN", "DAC_OVERRIDE", "FOWNER")

#: The helper program's source, run by ``python -I -B -c`` in the helper.
_HELPER_SOURCE: Final = (
    Path(__file__).with_name("_handoff_helper.py").read_text(encoding="utf-8")
)

#: SHA-256 of the helper program as sent to the container. The helper lives
#: in ``src/``, not in the image, so the image tag does not name it; an AG-o
#: run records this beside its limits (:mod:`sciagent.investigation.runner`).
HANDOFF_HELPER_SHA256: Final = hashlib.sha256(
    _HELPER_SOURCE.encode("utf-8")
).hexdigest()

#: Longest ``max_work_path_bytes``. A path must stay well inside ``PATH_MAX``
#: (4096) with the run directory in front of it, and the depth it allows
#: (512 directories) well inside Python's recursion limit (1,000):
#: ``shutil.rmtree`` recurses once per level (Python 3.12), and the run
#: directory is removed with it after the run.
_MAX_WORK_PATH_BYTES: Final = 1024

_OWNER_RE: Final = re.compile(r"[0-9]+:[0-9]+")

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
    ``max_work_entries`` and ``max_work_path_bytes`` bound what ``work/`` may
    hold after a call (entries, and the longest path relative to ``work/``);
    past either, the back handoff empties ``work/`` and stderr says so
    (module docstring).
    """

    wall_seconds: float = 60
    memory_mb: int = 2048
    cpus: float = 1.0
    pids: int = 256
    output_bytes: int = 20_000
    max_file_mb: int = 256
    max_work_entries: int = 100_000
    max_work_path_bytes: int = 1024

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
        if self.max_work_entries < 1:
            raise SandboxConfigError(
                f"max_work_entries must be >= 1, got {self.max_work_entries}"
            )
        if not 1 <= self.max_work_path_bytes <= _MAX_WORK_PATH_BYTES:
            raise SandboxConfigError(
                f"max_work_path_bytes must be in [1, {_MAX_WORK_PATH_BYTES}], "
                f"got {self.max_work_path_bytes}"
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


def handoff_owner(
    *,
    platform: str,
    operating_system: str,
    security_options: list[str],
    uid: int,
    gid: int,
) -> str | None:
    """Return the in-container ``uid:gid`` of the host user, or ``None``.

    ``None`` means no ownership handoff is needed: Docker Desktop (any host OS)
    maps bind-mounted files to the host user through its VM's file sharing,
    whichever uid writes them in the container. On native Linux,
    rootless Docker maps container uid 0 to the host user, so ``"0:0"``;
    rootful Docker shares the host's uids, so the host user's own ids (never
    ``0:0`` for a non-root host user: container root there is real root).

    ``operating_system`` and ``security_options`` are ``docker info``'s
    ``OperatingSystem`` and ``SecurityOptions``; ``platform`` is
    ``sys.platform``.
    """
    if platform != "linux" or "Docker Desktop" in operating_system:
        return None
    if "name=rootless" in security_options:
        return "0:0"
    return f"{uid}:{gid}"


def build_handoff_argv(
    *,
    docker: str,
    name: str,
    image: str,
    work_dir: Path,
    owner: str,
    limits: SandboxLimits,
) -> list[str]:
    """Return the ``docker run`` command that hands ``work/`` to ``owner``.

    The helper runs a fixed program (:mod:`._handoff_helper`, passed to
    ``python -I -B -c``) as container root holding only ``CHOWN``,
    ``DAC_OVERRIDE`` and ``FOWNER``, with no network, a read-only root,
    ``no-new-privileges``, ``/`` as working directory and only ``work/``
    mounted. It applies the entry and path limits of ``limits``, then gives
    everything to ``owner`` (``lchown``), strips POSIX ACLs and sets
    ``u+rwX,go-w``. No agent code runs in it, and it starts only after the
    agent's container is confirmed gone, so nothing can swap a path under it;
    no symbolic link is followed. Hard links cannot reach outside ``work/``:
    it is a mount of its own. ``-I`` keeps ``/work`` off ``sys.path``.

    Raises:
        SandboxConfigError: ``owner`` is not numeric ``uid:gid``.
        SandboxPathError: ``work_dir`` cannot be mounted (:func:`mount_source`).
    """
    if _OWNER_RE.fullmatch(owner) is None:
        raise SandboxConfigError(f"owner must be numeric uid:gid, got {owner!r}")
    argv = [
        docker,
        "run",
        "--rm",
        "--name",
        name,
        "--pull",
        "never",
        "--network",
        "none",
        "--read-only",
        "--memory",
        "256m",
        "--memory-swap",
        "256m",
        "--cpus",
        "1.0",
        "--pids-limit",
        "64",
        # One directory per path level stays open while counting.
        "--ulimit",
        "nofile=4096:4096",
        "--cap-drop",
        "ALL",
    ]
    for cap in _HANDOFF_CAPS:
        argv += ["--cap-add", cap]
    argv += [
        "--security-opt",
        "no-new-privileges",
        "--user",
        "0:0",
        "--workdir",
        "/",
        "--mount",
        f"type=bind,source={mount_source(work_dir)},target=/work",
        image,
        "python",
        "-I",
        "-B",
        "-c",
        _HELPER_SOURCE,
        owner,
        str(limits.max_work_entries),
        str(limits.max_work_path_bytes),
        "/work",
    ]
    return argv


def build_probe_argv(
    *, docker: str, name: str, image: str, probe_dir: Path
) -> list[str]:
    """Return the ``docker run`` command of the platform probe.

    The probe runs as the agent does (uid 65534, no capability, no network, a
    read-only root) and only creates ``/probe/p`` in ``probe_dir``, so the
    file's owner on the host shows how this daemon maps container uid 65534
    (:func:`_check_platform`).

    Raises:
        SandboxPathError: ``probe_dir`` cannot be mounted (:func:`mount_source`).
    """
    return [
        docker,
        "run",
        "--rm",
        "--name",
        name,
        "--pull",
        "never",
        "--network",
        "none",
        "--read-only",
        "--memory",
        "64m",
        "--memory-swap",
        "64m",
        "--cpus",
        "1.0",
        "--pids-limit",
        "16",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        SANDBOX_UID,
        "--workdir",
        "/",
        "--mount",
        f"type=bind,source={mount_source(probe_dir)},target=/probe",
        image,
        "touch",
        "/probe/p",
    ]


def _handoff_timeout(limits: SandboxLimits) -> float:
    """Time allowed for one handoff of a call under ``limits``."""
    return max(
        _HANDOFF_TIMEOUT_S, _HANDOFF_SECONDS_PER_WALL_SECOND * limits.wall_seconds
    )


def _host_uid() -> int:
    """The host user's uid, which owns ``work/`` between calls (native Linux)."""
    return os.getuid()


def _docker(
    docker: str, args: list[str], *, timeout: float
) -> subprocess.CompletedProcess[bytes]:
    """Run one Docker CLI command and capture its output.

    Raises:
        SandboxUnavailableError: the CLI is not on PATH.
        subprocess.TimeoutExpired: left to the caller, which knows what a
            timeout means for its command.
    """
    try:
        return subprocess.run(
            [docker, *args], capture_output=True, timeout=timeout, check=False
        )
    except FileNotFoundError as exc:
        raise SandboxUnavailableError(
            f"Docker CLI {docker!r} not found on PATH; the python sandbox needs Docker"
        ) from exc


def _stderr(proc: subprocess.CompletedProcess[bytes]) -> str:
    return proc.stderr.decode("utf-8", "replace").strip()


def _probe_handoff_owner(docker: str) -> str | None:
    """Ask the daemon which handoff, if any, this host needs.

    Raises:
        SandboxUnavailableError: Docker cannot be reached.
        SandboxPlatformError: the daemon is not one the sandbox knows how to
            hand ``work/`` around on (not running Linux containers, or an
            answer that cannot tell rootless from rootful).
        SandboxError: ``docker info`` failed or is unreadable.
    """
    if sys.platform != "linux":
        return None
    try:
        proc = _docker(
            docker, ["info", "--format", "{{json .}}"], timeout=_REAP_TIMEOUT_S
        )
    except subprocess.TimeoutExpired as exc:
        raise SandboxUnavailableError("docker info did not answer") from exc
    if proc.returncode != 0:
        check_docker(docker)  # raises SandboxUnavailableError if the daemon is down
        raise SandboxError(f"docker info failed: {_stderr(proc)}")
    try:
        info = json.loads(proc.stdout)
    except ValueError as exc:
        raise SandboxError(f"cannot read docker info: {exc}") from exc
    if not isinstance(info, dict):
        raise SandboxError("docker info is not a JSON object")
    os_type = info.get("OSType")
    if os_type != "linux":
        raise SandboxPlatformError(
            f"the Docker daemon runs {os_type!r} containers; the sandbox image "
            "needs a daemon running linux containers"
        )
    operating_system = info.get("OperatingSystem")
    security_options = info.get("SecurityOptions")
    if not isinstance(operating_system, str) or not isinstance(security_options, list):
        raise SandboxError(
            "docker info lacks OperatingSystem (a string) or SecurityOptions (a list)"
        )
    options: list[str] = []
    for option in security_options:
        if not isinstance(option, str):
            raise SandboxPlatformError(
                f"docker info's SecurityOptions holds a non-string entry "
                f"{option!r}: cannot tell rootless from rootful Docker"
            )
        options.append(option)
    return handoff_owner(
        platform=sys.platform,
        operating_system=operating_system,
        security_options=options,
        uid=os.getuid(),
        gid=os.getgid(),
    )


def _check_platform(owner: str | None, *, file_uid: int, host_uid: int) -> None:
    """Check detection against the probe: who owns what uid 65534 wrote.

    ``owner`` is :func:`handoff_owner`'s answer and ``file_uid`` the host
    owner of a file a container wrote as uid 65534 (:func:`build_probe_argv`).
    Docker Desktop (``None``) shows it as the host user; rootless Docker
    (``"0:0"`` for a non-root host user) as a subordinate uid -- not the host
    user, root or 65534; rootful Docker as 65534 itself.

    Raises:
        SandboxPlatformError: ``file_uid`` contradicts ``owner``.
    """
    nobody = int(SANDBOX_UID.split(":")[0])
    if owner is None:
        kind, ok = "Docker Desktop", file_uid == host_uid
        expected = f"the host user (uid {host_uid})"
    elif owner == "0:0" and host_uid != 0:
        kind, ok = "rootless Docker", file_uid not in (host_uid, 0, nobody)
        expected = "a subordinate uid"
    else:
        kind, ok = "rootful Docker", file_uid == nobody
        expected = f"uid {nobody} itself"
    if not ok:
        raise SandboxPlatformError(
            f"docker info points to {kind}, under which a file written by "
            f"container uid {nobody} is owned by {expected}; this daemon gave it "
            f"to host uid {file_uid}, so the ownership handoff would be wrong"
        )


def _verify_platform(docker: str, image: str, run_dir: Path, owner: str | None) -> None:
    """Run the platform probe in a scratch directory of ``run_dir``.

    The scratch directory is neither ``data/`` nor ``work/``, so no agent
    container ever sees it, and it is removed before this returns.

    Raises:
        SandboxPlatformError: the probe contradicts ``owner``, failed, or its
            file never reached the host (a daemon that does not share this
            host's filesystem, such as a remote ``DOCKER_HOST``).
        SandboxImageError: the image is not present.
        SandboxUnavailableError: Docker cannot be reached.
    """
    probe = Path(tempfile.mkdtemp(prefix="probe-", dir=run_dir))
    try:
        probe.chmod(0o777)  # uid 65534 must be able to create the file
        name = f"sciagent-probe-{uuid.uuid4().hex[:20]}"
        argv = build_probe_argv(docker=docker, name=name, image=image, probe_dir=probe)
        try:
            proc = _docker(docker, argv[1:], timeout=_REAP_TIMEOUT_S)
        except subprocess.TimeoutExpired as exc:
            _remove(docker, name)
            _confirm_gone(docker, name)
            raise SandboxPlatformError("the platform probe did not finish") from exc
        if proc.returncode != 0:
            stderr = _stderr(proc)
            if "No such image" in stderr:
                raise SandboxImageError(
                    f"sandbox image {image} is not present; call ensure_image() "
                    f"first:\n{stderr}"
                )
            check_docker(docker)
            raise SandboxPlatformError(
                f"the platform probe failed (exit {proc.returncode}):\n{stderr}"
            )
        try:
            file_uid = (probe / "p").lstat().st_uid
        except FileNotFoundError as exc:
            raise SandboxPlatformError(
                "the platform probe ran but its file did not reach the host: "
                "the daemon does not share this host's filesystem"
            ) from exc
        _check_platform(owner, file_uid=file_uid, host_uid=_host_uid())
    finally:
        try:
            (probe / "p").unlink(missing_ok=True)
            probe.rmdir()
        except OSError as exc:
            raise SandboxError(f"cannot remove the platform probe: {exc}") from exc


def _remove(docker: str, name: str) -> None:
    """Force-remove a container (kill, then delete); best effort.

    A ``docker rm -f`` that times out is not an error here: whether the
    container is gone is what :func:`_confirm_gone` establishes, and only it
    raises.
    """
    try:
        _docker(docker, ["rm", "-f", name], timeout=_REAP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return


def _exists(docker: str, name: str) -> bool:
    """Whether a container of this name or id exists (``docker inspect``).

    Raises:
        SandboxUnavailableError: the daemon is down.
        SandboxError: ``docker inspect`` gave no clear answer.
    """
    try:
        proc = _docker(
            docker,
            ["inspect", "--type", "container", "--format", "{{.Id}}", name],
            timeout=_REAP_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise SandboxError(f"docker inspect {name} did not answer") from exc
    if proc.returncode == 0:
        return True
    stderr = _stderr(proc)
    if "No such container" in stderr or "No such object" in stderr:
        return False
    check_docker(docker)
    raise SandboxError(f"cannot tell whether container {name} is gone: {stderr}")


def _confirm_gone(docker: str, name: str) -> None:
    """Return once ``docker inspect`` shows the container gone; else raise.

    A container that still exists is removed again (``docker rm -f``) and
    polled for up to ``_GONE_TIMEOUT_S``: ``--rm`` deletes a container shortly
    after its client returns, not necessarily before.

    Raises:
        SandboxError: the container is still there after the deadline.
    """
    deadline = time.monotonic() + _GONE_TIMEOUT_S
    while _exists(docker, name):
        _remove(docker, name)
        if not _exists(docker, name):
            return
        if time.monotonic() >= deadline:
            raise SandboxError(
                f"container {name} is still there after docker rm -f; work/ "
                "was not handed back, since its code may still be running"
            )
        time.sleep(_GONE_POLL_S)


def _containers_mounting(docker: str, path: Path) -> list[str]:
    """Ids of every container, running or not, with ``path`` as a mount source."""
    try:
        proc = _docker(
            docker,
            ["ps", "-aq", "--no-trunc", "--filter", f"volume={mount_source(path)}"],
            timeout=_REAP_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise SandboxError("docker ps did not answer") from exc
    if proc.returncode != 0:
        check_docker(docker)
        raise SandboxError(f"docker ps failed: {_stderr(proc)}")
    return sorted(proc.stdout.decode("utf-8", "replace").split())


def _handoff(
    docker: str,
    image: str,
    work_dir: Path,
    owner: str,
    limits: SandboxLimits,
    *,
    timeout: float,
) -> bool:
    """Hand ``work_dir`` to ``owner`` in a helper container.

    Returns whether the helper emptied ``work/`` for exceeding ``limits``.

    Raises:
        SandboxImageError: the image is not present.
        SandboxUnavailableError: Docker cannot be reached.
        SandboxError: the helper failed, timed out, or printed something other
            than its status.
    """
    name = f"sciagent-own-{uuid.uuid4().hex[:20]}"
    argv = build_handoff_argv(
        docker=docker,
        name=name,
        image=image,
        work_dir=work_dir,
        owner=owner,
        limits=limits,
    )
    try:
        proc = _docker(docker, argv[1:], timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _remove(docker, name)
        _confirm_gone(docker, name)
        raise SandboxError(
            f"handing work/ to {owner} took longer than {timeout:.0f} s"
        ) from exc
    if proc.returncode != 0:
        stderr = _stderr(proc)
        if "No such image" in stderr:
            raise SandboxImageError(
                f"sandbox image {image} is not present; call "
                f"ensure_image() first:\n{stderr}"
            )
        check_docker(docker)
        raise SandboxError(
            f"handing work/ to {owner} failed (exit {proc.returncode}):\n{stderr}"
        )
    status = proc.stdout.decode("utf-8", "replace").strip()
    if status not in (HELPER_OK, HELPER_EMPTIED):
        raise SandboxError(
            f"the handoff helper printed {status[:200]!r}, not "
            f"{HELPER_OK!r} or {HELPER_EMPTIED!r}"
        )
    return status == HELPER_EMPTIED


def _check_owned(work_dir: Path) -> None:
    """Raise unless ``work_dir`` belongs to the host user (after a back handoff).

    Raises:
        SandboxError: it does not, or it cannot be read (typed, so the
            caller's ``except SandboxError`` marks the sandbox unusable).
    """
    try:
        uid = work_dir.lstat().st_uid
    except OSError as exc:
        raise SandboxError(f"cannot read work/ after handing it back: {exc}") from exc
    if uid != _host_uid():
        raise SandboxError(
            f"work/ is owned by uid {uid}, not by the host user (uid "
            f"{_host_uid()}), after handing it back"
        )


def recover(
    run_dir: Path,
    *,
    image: str,
    docker: str = "docker",
    limits: SandboxLimits | None = None,
) -> None:
    """Make a run directory's ``work/`` the host user's again, after any failure.

    For cleanup and for a session killed mid-call. Removes every container
    still mounting ``work/`` (an agent container whose client was killed runs
    on, past any wall time) and confirms each gone; then, where there is a
    handoff, hands ``work/`` back with the usual helper (``limits``, default
    :class:`SandboxLimits`, within ``_RECOVERY_TIMEOUT_S`` or a call's
    handoff time, whichever is longer) and checks it.

    Raises:
        SandboxError: a container could not be removed or the handoff failed;
            and whatever :func:`_probe_handoff_owner` raises.
    """
    # Resolved, as :class:`Sandbox` mounts it: ``docker ps --filter volume=``
    # matches the mount source exactly, so a path through a symbolic link
    # would find no container.
    work = run_dir.resolve() / "work"
    for container in _containers_mounting(docker, work):
        _confirm_gone(docker, container)
    left = _containers_mounting(docker, work)
    if left:
        raise SandboxError(f"containers still mount {work}: {', '.join(left)}")
    owner = _probe_handoff_owner(docker)
    if owner is None:
        return
    limits = SandboxLimits() if limits is None else limits
    _handoff(
        docker,
        image,
        work,
        owner,
        limits,
        timeout=max(_RECOVERY_TIMEOUT_S, _handoff_timeout(limits)),
    )
    _check_owned(work)


def recover_run_dirs(root: Path, *, image: str, docker: str = "docker") -> None:
    """:func:`recover` every run directory (one holding ``work/``) under ``root``.

    Tries them all before raising.

    Raises:
        SandboxError: one or more could not be recovered (all are named).
    """
    failures: list[str] = []
    for run_dir in sorted(p for p in root.iterdir() if (p / "work").is_dir()):
        try:
            recover(run_dir, image=image, docker=docker)
        except SandboxError as error:
            failures.append(f"{run_dir}: {type(error).__name__}: {error}")
    if failures:
        raise SandboxError("cannot recover " + "; ".join(failures))


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


def canonical_run_argv(limits: SandboxLimits) -> list[str]:
    """:func:`build_run_argv` for ``limits`` with placeholder Docker binary,
    container name, image, paths and seed (``0``): every flag, mount option
    and environment variable a call runs under, and nothing that varies from
    call to call. An AG-o session's address names it
    (:func:`sciagent.campaign.plan.session_text`); the image is named there
    by its tag.
    """
    return build_run_argv(
        docker="docker",
        name="<name>",
        image="<image>",
        data_dir=Path("/<run>/data"),
        work_dir=Path("/<run>/work"),
        limits=limits,
        seed=0,
        cidfile=Path("/<run>/cid"),
    )


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


def _sweep_special_files(work_dir: Path, owner_uid: int | None = None) -> list[str]:
    """Delete every entry under ``work_dir`` that is not a regular file or directory.

    Returns the removed paths, relative and POSIX-style, in sorted order. With
    ``owner_uid`` (after a back handoff), every entry must belong to it, or
    :class:`~sciagent.sandbox.errors.SandboxError` is raised before anything
    is removed from that directory.

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
    # Iterative, not recursive: ``work/`` is up to 512 levels deep after a
    # back handoff, and deeper where there is none (Docker Desktop applies
    # no limit), so a walk per level could reach Python's recursion limit.
    pending: list[tuple[Path, str]] = [(work_dir, "")]
    while pending:
        directory, rel = pending.pop()
        with os.scandir(directory) as it:
            names = sorted(e.name for e in it)
        entries: list[tuple[Path, str, os.stat_result]] = []
        for base in names:
            path = directory / base
            st = os.lstat(path)
            if owner_uid is not None and st.st_uid != owner_uid:
                raise SandboxError(
                    f"work/{rel}{base} is owned by uid {st.st_uid}, not by the "
                    f"host user (uid {owner_uid}), after handing work/ back"
                )
            entries.append((path, f"{rel}{base}", st))
        for path, name, st in entries:
            attrs = getattr(st, "st_file_attributes", 0)
            reparse = bool(attrs & _FILE_ATTRIBUTE_REPARSE_POINT)
            if not reparse and stat.S_ISDIR(st.st_mode):
                pending.append((path, f"{name}/"))
            elif reparse or not stat.S_ISREG(st.st_mode):
                if attrs & _FILE_ATTRIBUTE_DIRECTORY:
                    os.rmdir(path)
                else:
                    os.unlink(path)
                removed.append(name)
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
        # Probed on the first call, so constructing a Sandbox needs no Docker.
        self._owner: str | None = None
        self._owner_probed = False
        # Why the sandbox refuses calls, once a back handoff has failed.
        self._broken: str | None = None
        # Whether work/ may be out of the host user's hands: set before the
        # forward handoff, cleared once a back handoff is confirmed.
        self._handed_away = False

    @property
    def last_container_name(self) -> str | None:
        """The container name of the most recent call (for leak checks)."""
        return self._last_container_name

    def run(self, code: str) -> SandboxResult:
        """Run ``code`` with ``python -`` in a fresh container; return what it did.

        Raises:
            SandboxUnavailableError: Docker cannot be reached.
            SandboxImageError: ``image`` is not present locally (runs never pull).
            SandboxPlatformError: platform detection is contradicted by the
                daemon (module docstring; checked on the first call).
            SandboxError: Docker failed to create the container for another
                reason; a handoff failed, or the agent's container could not
                be confirmed gone before the back handoff; or an earlier call
                left the sandbox unusable.
        """
        if self._broken is not None:
            raise SandboxError(
                f"the sandbox is unusable since an earlier call: {self._broken}"
            )
        owner = self._platform_owner()
        # A unique name so a timed-out container can be removed by name. The
        # name never reaches the result, so its randomness cannot move output.
        name = f"sciagent-sbx-{uuid.uuid4().hex[:20]}"
        self._last_container_name = name
        emptied = False
        if owner is None:
            result = self._run(code, name)
        else:
            # One try for both handoffs: a forward handoff that failed part
            # way, or a harness fault in the call, still hands work/ back.
            try:
                self._handed_away = True
                emptied = self._handoff(SANDBOX_UID)
                result = self._run(code, name)
            finally:
                emptied = self._hand_back(owner, name) or emptied
        # After the handoff back: the host may now unlink anywhere in work/.
        removed = _sweep_special_files(
            self.work_dir, None if owner is None else _host_uid()
        )
        notes = "".join(
            f"[sciagent sandbox: removed non-regular file work/{r}]\n" for r in removed
        )
        if emptied:
            notes += (
                f"[sciagent sandbox: work/ was emptied: more than "
                f"{self.limits.max_work_entries} entries, or a path longer than "
                f"{self.limits.max_work_path_bytes} bytes]\n"
            )
        return dataclasses.replace(result, stderr=result.stderr + notes)

    def recover(self) -> None:
        """Make ``work/`` the host user's again after any failure (:func:`recover`).

        For the investigation runner's cleanup. A no-op, asking nothing of
        Docker, unless a call left ``work/`` handed away (its back handoff
        failed or never ran); a clean run's cleanup then cannot fail on a
        Docker hiccup. It does not make a sandbox whose back handoff failed
        usable again.
        """
        if not self._handed_away:
            return
        recover(self.run_dir, image=self.image, docker=self.docker, limits=self.limits)
        self._handed_away = False

    def _platform_owner(self) -> str | None:
        """The handoff owner, probed and checked against the daemon on first use."""
        if not self._owner_probed:
            owner = _probe_handoff_owner(self.docker)
            if sys.platform == "linux":
                _verify_platform(self.docker, self.image, self.run_dir, owner)
            self._owner = owner
            self._owner_probed = True
        return self._owner

    def _hand_back(self, owner: str, name: str) -> bool:
        """Confirm the agent's container gone, hand ``work/`` back, check it.

        Returns whether the helper emptied ``work/``. Any failure leaves the
        sandbox unusable for further calls.
        """
        try:
            _confirm_gone(self.docker, name)
            emptied = self._handoff(owner)
            _check_owned(self.work_dir)
        except SandboxError as error:
            self._broken = f"{type(error).__name__}: {error}"
            raise
        self._handed_away = False
        return emptied

    def _run(self, code: str, name: str) -> SandboxResult:
        """One container call, without the ownership handoff or the sweep."""
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
                _remove(self.docker, name)
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
        return SandboxResult(
            stdout=out.text("stdout"),
            stderr=err.text("stderr"),
            exit_code=TIMEOUT_EXIT_CODE if timed_out else rc,
            timed_out=timed_out,
            truncated=out.overflow or err.overflow,
            duration_s=duration,
        )

    def _handoff(self, owner: str) -> bool:
        """Hand ``work/`` to ``owner``; return whether it was emptied."""
        return _handoff(
            self.docker,
            self.image,
            self.work_dir,
            owner,
            self.limits,
            timeout=_handoff_timeout(self.limits),
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

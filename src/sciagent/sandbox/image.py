"""The sandbox image: a content-addressed tag, built on demand.

The tag is ``sciagent-sandbox:<16 hex>``, the head of a SHA-256 over the
Dockerfile and ``requirements.lock`` (line endings normalised to LF, so a CRLF
checkout names the same image). Both files pin everything that goes into the
image -- base and uv by digest, wheels by hash -- so the tag names the
environment the agent's code ran in, and the tool layer records it in the
transcript beside every result.

Building needs the network (it pulls the base image, uv and the wheels);
running never does. :func:`ensure_image` builds only when the tag is absent.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from sciagent.sandbox.errors import SandboxImageError, SandboxUnavailableError

__all__ = [
    "IMAGE_REPOSITORY",
    "SANDBOX_CONTEXT",
    "check_docker",
    "ensure_image",
    "image_tag",
]

#: Repository part of every sandbox image tag.
IMAGE_REPOSITORY = "sciagent-sandbox"

#: The build context in this checkout: ``<repo>/docker/sandbox``.
SANDBOX_CONTEXT = Path(__file__).resolve().parents[3] / "docker" / "sandbox"

#: The files the tag is computed over, in a fixed order. Exactly the files the
#: Dockerfile reads from its context.
_HASHED = ("Dockerfile", "requirements.lock")

#: Generous: a cold build downloads ~100 MB of wheels.
_BUILD_TIMEOUT_S = 1800.0
_PROBE_TIMEOUT_S = 30.0


def image_tag(context: Path = SANDBOX_CONTEXT) -> str:
    """Return ``sciagent-sandbox:<hash>`` for the build context at ``context``.

    Pure in the bytes of the hashed files: no Docker call.

    Raises:
        SandboxImageError: a hashed file is missing from ``context``.
    """
    h = hashlib.sha256()
    for name in _HASHED:
        path = context / name
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise SandboxImageError(
                f"sandbox build context lacks {path}: {exc}"
            ) from exc
        data = data.replace(b"\r\n", b"\n")
        h.update(name.encode() + b"\0")
        h.update(len(data).to_bytes(8, "big"))
        h.update(data)
    return f"{IMAGE_REPOSITORY}:{h.hexdigest()[:16]}"


def check_docker(docker: str = "docker") -> str:
    """Return the Docker server version, or raise if Docker cannot be reached.

    Raises:
        SandboxUnavailableError: the ``docker`` binary is not on PATH, or the
            daemon does not answer (Docker Desktop not running).
    """
    try:
        proc = subprocess.run(
            [docker, "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            timeout=_PROBE_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError as exc:
        raise SandboxUnavailableError(
            f"Docker CLI {docker!r} not found on PATH; the python sandbox needs "
            "Docker (Docker Desktop on Windows)"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SandboxUnavailableError(
            f"Docker daemon did not answer within {_PROBE_TIMEOUT_S:.0f} s"
        ) from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise SandboxUnavailableError(
            f"Docker daemon is not reachable (is Docker Desktop running?): {detail}"
        )
    return proc.stdout.decode("utf-8", "replace").strip()


def ensure_image(context: Path = SANDBOX_CONTEXT, *, docker: str = "docker") -> str:
    """Return the sandbox image tag, building the image first if it is absent.

    Raises:
        SandboxUnavailableError: Docker cannot be reached.
        SandboxImageError: the context is incomplete or the build failed.
    """
    tag = image_tag(context)
    check_docker(docker)
    present = subprocess.run(
        [docker, "image", "inspect", "--format", "{{.Id}}", tag],
        capture_output=True,
        timeout=_PROBE_TIMEOUT_S,
        check=False,
    )
    if present.returncode == 0:
        return tag
    try:
        built = subprocess.run(
            [docker, "build", "--progress", "plain", "--tag", tag, str(context)],
            capture_output=True,
            timeout=_BUILD_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SandboxImageError(
            f"building {tag} took longer than {_BUILD_TIMEOUT_S:.0f} s"
        ) from exc
    if built.returncode != 0:
        tail = built.stderr.decode("utf-8", "replace")[-4000:]
        raise SandboxImageError(f"docker build of {tag} failed:\n{tail}")
    return tag

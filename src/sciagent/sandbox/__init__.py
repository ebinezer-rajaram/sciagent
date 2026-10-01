"""The isolated Python sandbox behind the open agent's ``python`` tool (SPEC §4.0).

See :mod:`sciagent.sandbox.runner` for the isolation model and the
determinism contract, :mod:`sciagent.sandbox.image` for the image, and
:mod:`sciagent.sandbox.dataset` for how collected data reaches ``/data``.
"""

from __future__ import annotations

from sciagent.sandbox.dataset import write_dataset
from sciagent.sandbox.errors import (
    DatasetConflictError,
    SandboxConfigError,
    SandboxError,
    SandboxImageError,
    SandboxPathError,
    SandboxUnavailableError,
)
from sciagent.sandbox.image import check_docker, ensure_image, image_tag
from sciagent.sandbox.runner import (
    TIMEOUT_EXIT_CODE,
    Sandbox,
    SandboxLimits,
    SandboxResult,
    build_run_argv,
    mount_source,
    result_digest,
)

__all__ = [
    "TIMEOUT_EXIT_CODE",
    "DatasetConflictError",
    "Sandbox",
    "SandboxConfigError",
    "SandboxError",
    "SandboxImageError",
    "SandboxLimits",
    "SandboxPathError",
    "SandboxResult",
    "SandboxUnavailableError",
    "build_run_argv",
    "check_docker",
    "ensure_image",
    "image_tag",
    "mount_source",
    "result_digest",
    "write_dataset",
]

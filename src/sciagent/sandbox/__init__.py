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
    SandboxPlatformError,
    SandboxUnavailableError,
)
from sciagent.sandbox.image import check_docker, ensure_image, image_id, image_tag
from sciagent.sandbox.runner import (
    HANDOFF_HELPER_SHA256,
    TIMEOUT_EXIT_CODE,
    Sandbox,
    SandboxLimits,
    SandboxResult,
    build_handoff_argv,
    build_probe_argv,
    build_run_argv,
    handoff_owner,
    mount_source,
    recover,
    recover_run_dirs,
    result_digest,
)

__all__ = [
    "HANDOFF_HELPER_SHA256",
    "TIMEOUT_EXIT_CODE",
    "DatasetConflictError",
    "Sandbox",
    "SandboxConfigError",
    "SandboxError",
    "SandboxImageError",
    "SandboxLimits",
    "SandboxPathError",
    "SandboxPlatformError",
    "SandboxResult",
    "SandboxUnavailableError",
    "build_handoff_argv",
    "build_probe_argv",
    "build_run_argv",
    "check_docker",
    "ensure_image",
    "handoff_owner",
    "image_id",
    "image_tag",
    "mount_source",
    "recover",
    "recover_run_dirs",
    "result_digest",
    "write_dataset",
]

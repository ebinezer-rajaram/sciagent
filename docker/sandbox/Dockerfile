# syntax=docker/dockerfile:1
#
# The AG-o Python sandbox (SPEC §4.0, the `python` tool).
#
# Built by sciagent.sandbox.ensure_image(), which tags it
# sciagent-sandbox:<sha256 of this file + requirements.lock>, so the tag names
# the exact bytes that produced the image and is recorded in every transcript.
# Building needs the network (base image, uv, wheels); running never does.
#
# Everything is pinned: the base image and uv by digest, the wheels by hash
# (requirements.lock, --require-hashes). uv is bind-mounted for the install
# step only and is not in the final image. Bytecode is compiled at build time:
# the container root is read-only and PYTHONDONTWRITEBYTECODE is set, so
# without it every call would recompile statsmodels from source (~5 s).
FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

COPY requirements.lock /tmp/requirements.lock
RUN --mount=from=ghcr.io/astral-sh/uv@sha256:0f36cb9361a3346885ca3677e3767016687b5a170c1a6b88465ec14aefec90aa,source=/uv,target=/bin/uv \
    uv pip install --system --no-cache --compile-bytecode --require-hashes -r /tmp/requirements.lock \
    && rm /tmp/requirements.lock \
    && mkdir /data /work

WORKDIR /work
# No ENTRYPOINT/CMD: the host passes the full command and environment per call.

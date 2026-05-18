# syntax=docker/dockerfile:1.9

# ---------- Stage 1: builder ----------
# Start from the same python:3.12-slim base as the runtime stage so the
# venv we build here is binary-compatible with where it'll run. We then
# layer uv on top via its official standalone installer (pulled from
# astral.sh CDN — avoids ghcr.io auth issues on some networks).
FROM python:3.12-slim-bookworm AS builder

# Install uv via pip (already present in the python:3.12-slim image).
# The standalone shell installer would be ~2x faster but requires curl,
# which the slim image doesn't ship. Pip works fine; the builder stage
# is discarded anyway.
RUN pip install --no-cache-dir uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Install dependencies in their own layer, separate from app code.
# - --mount=type=cache reuses uv's download cache across builds (saves
#   ~30s on rebuilds when deps haven't changed).
# - --mount=type=bind avoids COPYing pyproject.toml / uv.lock into the
#   image; they're only needed transiently for the sync step.
# - --no-install-project skips installing the local package, which is
#   what pyproject.toml's `[tool.uv] package = false` already implies.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --locked --no-install-project

# Copy app source. The venv at /app/.venv already has all deps; we don't
# run uv sync again because `package = false` means there's no project
# package to install.
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

# ---------- Stage 2: runtime ----------
# Clean python:3.12-slim — no uv, no build toolchain, no apt indexes.
# Roughly 80 MB base vs ~250 MB for the builder.
FROM python:3.12-slim-bookworm AS runtime

# tini = tiny init. Makes PID 1 a proper init that forwards signals to
# uvicorn and reaps zombie child processes (e.g. K8s exec probes).
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini \
 && rm -rf /var/lib/apt/lists/*

# Run as a non-root user. Most production K8s clusters reject root
# containers via Pod Security admission — bake the constraint in now.
RUN groupadd --system --gid 1001 app \
 && useradd --system --uid 1001 --gid app --shell /sbin/nologin app

WORKDIR /app

# Bring the venv + source over in a single layer, owned by the app user.
COPY --from=builder --chown=app:app /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER app

# Documentation-only — actual port publishing happens at `docker run`
# or via the K8s Service.
EXPOSE 5181

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5181"]

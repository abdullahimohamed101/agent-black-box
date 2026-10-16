# syntax=docker/dockerfile:1
# Build context is the repository root: docker build -f apps/api/Dockerfile .
# The API depends on packages/event-schema, so the repo layout is reproduced under /repo.
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS build
COPY --from=ghcr.io/astral-sh/uv:0.12@sha256:3af4716e991d6956a41e573eab705d0ee08500cd829ed30293eb8472f372c65a /uv /usr/local/bin/uv
WORKDIR /repo
COPY packages/event-schema packages/event-schema
COPY apps/api/pyproject.toml apps/api/uv.lock apps/api/
WORKDIR /repo/apps/api
# Optional extras (ADR-170): `--build-arg UV_SYNC_ARGS="--extra otel"` adds OpenTelemetry tracing.
# The default image has none of it.
ARG UV_SYNC_ARGS=""
# Dependencies first (cached), then the application source.
RUN uv sync --frozen --no-dev --no-editable --no-install-project $UV_SYNC_ARGS
COPY apps/api/src src
RUN uv sync --frozen --no-dev --no-editable $UV_SYNC_ARGS

FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
# Backup and restore (ADR-162) shell out to pg_dump/pg_restore and, for encrypted bundles, openssl. The client's
# major version must be at least the server's (the stock stack runs PostgreSQL 16; this base image's client is newer).
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client openssl \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 abb
# Same absolute path as the build stage: the virtualenv's scripts embed it.
WORKDIR /repo/apps/api
COPY --from=build --chown=abb:abb /repo/apps/api/.venv .venv
COPY --chown=abb:abb apps/api/migrations migrations
COPY --chown=abb:abb apps/api/alembic.ini ./
# Artifacts (ADR-030): a volume mounted here inherits this ownership when first created.
RUN mkdir -p /data/artifacts && chown abb:abb /data/artifacts
ENV PATH="/repo/apps/api/.venv/bin:$PATH" PYTHONUNBUFFERED=1 ABB_ARTIFACT_DIR=/data/artifacts
USER abb
EXPOSE 8000
CMD ["uvicorn", "abb_api.main:app_from_env", "--factory", "--host", "0.0.0.0", "--port", "8000"]

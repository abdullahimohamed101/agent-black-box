# syntax=docker/dockerfile:1
# Build context is the repository root: docker build -f apps/api/Dockerfile .
# The API depends on packages/event-schema, so the repo layout is reproduced under /repo.
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
WORKDIR /repo
COPY packages/event-schema packages/event-schema
COPY apps/api/pyproject.toml apps/api/uv.lock apps/api/
WORKDIR /repo/apps/api
# Dependencies first (cached), then the application source.
RUN uv sync --frozen --no-dev --no-editable --no-install-project
COPY apps/api/src src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
RUN useradd --create-home --uid 10001 abb
# Same absolute path as the build stage: the virtualenv's scripts embed it.
WORKDIR /repo/apps/api
COPY --from=build --chown=abb:abb /repo/apps/api/.venv .venv
COPY --chown=abb:abb apps/api/migrations migrations
COPY --chown=abb:abb apps/api/alembic.ini ./
ENV PATH="/repo/apps/api/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER abb
EXPOSE 8000
CMD ["uvicorn", "abb_api.main:app_from_env", "--factory", "--host", "0.0.0.0", "--port", "8000"]

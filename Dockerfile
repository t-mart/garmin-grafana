# syntax=docker/dockerfile:1

FROM python:3.14-slim-trixie AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python:3.14-slim-trixie
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    TOKEN_DIR=/data/tokens
RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --create-home app \
    && mkdir /data \
    && chown app:app /data
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY LICENSE /app/LICENSE
USER app
ENTRYPOINT ["garmin-grafana"]
CMD ["sync"]

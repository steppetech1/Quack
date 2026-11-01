# quack-backend (ТЗ Фазы 3 WP9.1, Д-§5.1): api, projector, workers, migrate and ops commands are
# different commands of this one image. Build platform linux/amd64 (CI: --platform).

FROM ghcr.io/astral-sh/uv:0.9.5-python3.12-bookworm-slim AS build
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
# Dependencies strictly by uv.lock, without the dev and loadtest groups.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-default-groups --no-install-project
COPY alembic.ini ./
COPY migrations ./migrations
COPY quack ./quack
# loadtest/ holds meta-mock (profile `loadtest` of compose); locust is not installed.
COPY loadtest ./loadtest
# Д-§5.1: the (type, schema_version) pairs and rules@N of this build.
RUN /app/.venv/bin/python -m quack.cli ops event-registry --out /app/event_registry.json

FROM python:3.12-slim-bookworm
RUN groupadd --system --gid 10001 quack \
    && useradd --system --uid 10001 --gid quack --home-dir /app --shell /usr/sbin/nologin quack
WORKDIR /app
COPY --from=build --chown=quack:quack /app /app
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER quack
ENTRYPOINT ["python", "-m", "quack.cli"]
CMD ["api"]

# quack-compute (ТЗ Фазы 3 WP9.1, Д-§5.1): isolated symbolic checks, no network, no secrets.

FROM ghcr.io/astral-sh/uv:0.9.5-python3.12-bookworm-slim AS build
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY quack_compute ./quack_compute

FROM python:3.12-slim-bookworm
RUN groupadd --system --gid 10001 quack \
    && useradd --system --uid 10001 --gid quack --home-dir /app --shell /usr/sbin/nologin quack
WORKDIR /app
COPY --from=build --chown=quack:quack /app /app
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER quack
EXPOSE 8001
CMD ["uvicorn", "quack_compute.app:app", "--host", "0.0.0.0", "--port", "8001", \
     "--timeout-graceful-shutdown", "5"]

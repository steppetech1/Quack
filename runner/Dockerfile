# quack-runner (documentation/идея-редактор-кода-и-запуск.md): learner code in a sandbox.
# Build context: runner/ (`docker build runner`). No network, no secrets.
#
# The supervisor runs as root on purpose: it drops each test process to a slot uid
# (20001..20004). compose takes every capability away except SETUID, SETGID and KILL, sets
# no-new-privileges and a read-only root, and mounts each slot directory as a tmpfs owned by
# its uid with mode 700.

FROM ghcr.io/astral-sh/uv:0.9.5-python3.12-bookworm-slim AS build
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY quack_runner ./quack_runner

FROM python:3.12-slim-bookworm AS runtime
WORKDIR /app
COPY --from=build /app /app
# Slot directories for a plain `docker run`; compose mounts a tmpfs over each.
RUN for n in 1 2 3 4; do \
        uid=$((20000 + n)); \
        mkdir -p /work/slot-$n && chown $uid:$uid /work/slot-$n && chmod 700 /work/slot-$n; \
    done \
    && chmod -R a+rX /app
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    RUNNER_SANDBOX=1 \
    RUNNER_PYTHON=/usr/local/bin/python3.12 \
    RUNNER_WORK_DIR=/work
EXPOSE 8002
CMD ["uvicorn", "quack_runner.app:app", "--host", "0.0.0.0", "--port", "8002", \
     "--timeout-graceful-shutdown", "5"]

# CI: the same image plus the dev dependencies and the tests (`pytest -m sandbox`).
FROM build AS build-dev
RUN uv sync --frozen --no-install-project

FROM runtime AS test
COPY --from=build-dev /app/.venv /app/.venv
COPY pyproject.toml ./
COPY tests ./tests
CMD ["pytest", "-q"]

# quack-compute (ТЗ Фазы 3 WP9.1, ТЗ Фазы 4 WP24, Д-§5.1): SymPy, pint, PyMuPDF, OpenCV,
# Playwright with Chromium; no network, no secrets, unprivileged user.

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
# Т-§1.2, Д-§5.1: Chromium of Playwright and its system libraries, shared by all users.
ENV PATH=/app/.venv/bin:$PATH \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && chmod -R a+rX /opt/ms-playwright
# Д-§4.2: compute writes temporary files only to /srv/quack/tmp (a volume in compose).
RUN mkdir -p /srv/quack/tmp && chown quack:quack /srv/quack/tmp
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TMPDIR=/srv/quack/tmp \
    COMPUTE_TMP_DIR=/srv/quack/tmp
USER quack
EXPOSE 8001
CMD ["uvicorn", "quack_compute.app:app", "--host", "0.0.0.0", "--port", "8001", \
     "--timeout-graceful-shutdown", "5"]

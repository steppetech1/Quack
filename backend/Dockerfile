# quack-backend (ТЗ Фазы 3 WP9.1, Д-§5.1): api, projector, workers, migrate and ops commands are
# different commands of this one image. Build platform linux/amd64 (CI: --platform).

# dbmate (Т-§1.2, Д-§5.1): a Go binary pinned by checksum, the same version as in CI and ct.sh.
FROM debian:bookworm-slim AS dbmate
ARG DBMATE_VERSION=v2.36.0
ARG DBMATE_SHA256=47e284b3d8cbad1ba5f090495aa05afd1bbd5f35e2ed5577aad06da74ce780ce
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl     && curl -fsSL -o /usr/local/bin/dbmate        "https://github.com/amacneil/dbmate/releases/download/${DBMATE_VERSION}/dbmate-linux-amd64"     && echo "${DBMATE_SHA256}  /usr/local/bin/dbmate" | sha256sum -c -     && chmod 0755 /usr/local/bin/dbmate

FROM ghcr.io/astral-sh/uv:0.9.5-python3.12-bookworm-slim AS build
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
# Dependencies strictly by uv.lock, without the dev and loadtest groups.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-default-groups --no-install-project
# dbmate migrations of Phase 4 (WP1.1); the Alembic ones are not in the image (D-C-160).
COPY db ./db
COPY quack ./quack
# loadtest/ holds meta-mock (profile `loadtest` of compose); locust is not installed.
COPY loadtest ./loadtest
# Д-§5.1: the (type, schema_version) pairs and rules@N of this build.
RUN /app/.venv/bin/python -m quack.cli ops event-registry --out /app/event_registry.json

FROM python:3.12-slim-bookworm
# Д-§5.1: `pg_dump` 16 (the PGDG client; Debian bookworm ships 15) and `age` for `ops backup`.
RUN apt-get update     && apt-get install -y --no-install-recommends ca-certificates curl gnupg age     && install -d /usr/share/postgresql-common/pgdg     && curl --retry 5 --retry-delay 3 --retry-connrefused -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc         -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc     && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main"         > /etc/apt/sources.list.d/pgdg.list     && apt-get update     && apt-get install -y --no-install-recommends postgresql-client-16     && apt-get purge -y --auto-remove curl gnupg     && rm -rf /var/lib/apt/lists/*
RUN groupadd --system --gid 10001 quack \
    && useradd --system --uid 10001 --gid quack --home-dir /app --shell /usr/sbin/nologin quack
WORKDIR /app
COPY --from=build --chown=quack:quack /app /app
COPY --from=dbmate /usr/local/bin/dbmate /usr/local/bin/dbmate
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER quack
ENTRYPOINT ["python", "-m", "quack.cli"]
CMD ["api"]

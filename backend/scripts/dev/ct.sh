#!/bin/sh
# Run backend tests in Linux (uv, Python 3.12 image) against the own infrastructure K of
# scripts/dev/test_infra.sh. Works from any worktree: the backend of this script's checkout is
# copied into the container, so the working tree is never written.
#   scripts/dev/ct.sh K tests/artifacts tests/test_contracts.py
#   scripts/dev/ct.sh K            the whole suite (~8 min; once per wave)
# dbmate is pinned as in CI (Д-§5.2): version and checksum below.
set -eu
k=${1:?K 1..9}
shift
backend=$(cd "$(dirname "$0")/../.." && pwd)
case "$(uname -s)" in MINGW* | MSYS* | CYGWIN*) backend=$(cygpath -m "$backend") ;; esac
DBMATE_VERSION=v2.36.0
DBMATE_SHA256=47e284b3d8cbad1ba5f090495aa05afd1bbd5f35e2ed5577aad06da74ce780ce

MSYS_NO_PATHCONV=1 docker run --rm --add-host=host.docker.internal:host-gateway \
  -v "$backend:/src:ro" -v quack-ct-uv:/root/.cache/uv \
  -e QUACK_TEST_REQUIRE_INFRA=1 \
  -e "QUACK_TEST_POSTGRES_DSN=postgresql://quack:quack@host.docker.internal:56${k}01/quack_test" \
  -e "QUACK_TEST_NEO4J_URI=bolt://host.docker.internal:56${k}02" \
  -e "QUACK_TEST_REDIS_URL=redis://host.docker.internal:56${k}03/0" \
  ghcr.io/astral-sh/uv:python3.12-bookworm-slim sh -c "
    set -e
    apt-get -qq update >/dev/null && apt-get -qq install -y curl >/dev/null
    curl -fsSL -o /usr/local/bin/dbmate https://github.com/amacneil/dbmate/releases/download/$DBMATE_VERSION/dbmate-linux-amd64
    echo '$DBMATE_SHA256  /usr/local/bin/dbmate' | sha256sum -c - >/dev/null && chmod +x /usr/local/bin/dbmate
    cp -r /src /app && cd /app && rm -rf .venv && uv sync -q --frozen
    uv run pytest -q -p no:cacheprovider $*"

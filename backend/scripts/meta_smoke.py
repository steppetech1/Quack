"""Live smoke of the Meta API: one call of every Phase 3 role through the gateway
(ТЗ Фазы 3 WP5.7, DoD п.6a). Not part of `pytest` or CI.

Run from `backend/` against a migrated Postgres:

    META_API_KEY=... META_API_BASE_URL=... POSTGRES_DSN=... uv run python scripts/meta_smoke.py
"""

from __future__ import annotations


def main() -> int:
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())

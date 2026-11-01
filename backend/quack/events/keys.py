"""Deterministic idempotency keys of code and agent events (Т-§2.3, law 5; ТЗ Ф4 WP2.1).

A key is derived from the source, so a retry of the same work writes the same key and
`append_events` returns the stored event instead of a duplicate. Learner actions use the UUID
the client sends (Т-§2.3) and have no template here. Templates named by the documents:
observation, verdict, episode, embedding (WP2.1), «Сегодня» (WP17.2). `check.failed` pairs with
the verdict of the same part (Phase 4 decision D-A-108). Owners add templates for their types
here when they write them ([ОРИЕНТИР] WP2.1).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import date
from typing import Any
from uuid import UUID

from quack.events.hashing import canonical_json


def observation_key(episode_seq: int, extractor: str, trigger_seq: int) -> str:
    """`observation.extracted` (Т-§2.3): `obs:{episode_seq}:{extractor@N}:{trigger_seq}`."""
    return f"obs:{episode_seq}:{extractor}:{trigger_seq}"


def verdict_key(answer_seq: int, part: str) -> str:
    """`verdict.recorded`: one verdict per part (Т-§6.1, WP11.4)."""
    return f"verdict:{answer_seq}:{part}"


def check_failed_key(answer_seq: int, part: str) -> str:
    """`check.failed` of a part (Т-§22.7)."""
    return f"check_failed:{answer_seq}:{part}"


def episode_key(session_id: UUID, to_seq: int) -> str:
    """`episode.closed` (WP7.2)."""
    return f"episode:{session_id}:{to_seq}"


def embedding_key(node_kind: str, node_id: UUID, model: str) -> str:
    """`embedding.ready` (WP2.1)."""
    return f"emb:{node_kind}:{node_id}:{model}"


def today_key(domain_id: UUID, day: date, items: Sequence[dict[str, Any]]) -> str:
    """`today.shown` (WP17.2): `today:{domain_id}:{date}:{hash of the items}`."""
    digest = hashlib.sha256(canonical_json({"items": list(items)})).hexdigest()
    return f"today:{domain_id}:{day.isoformat()}:{digest}"

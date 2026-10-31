"""Redis client and key/channel templates (spec §6)."""

from __future__ import annotations

from uuid import UUID

from redis.asyncio import Redis

from quack.config import Settings

PROJECTOR_WAKEUP_CHANNEL = "projector:wakeup"


def create_redis(settings: Settings) -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)


def projector_applied_channel(learner_id: UUID) -> str:
    return f"projector:applied:{learner_id}"


def session_events_channel(session_id: UUID) -> str:
    return f"session:events:{session_id}"


def turn_lock_key(session_id: UUID) -> str:
    return f"lock:turn:{session_id}"


def presence_key(learner_id: UUID) -> str:
    return f"presence:learner:{learner_id}"


def ratelimit_key(scope: str, key: str) -> str:
    return f"ratelimit:{scope}:{key}"


# --- stream C (Phase 3 §6.3, seam S8) ---


def llm_pressure_key(model: str) -> str:
    """Pressure flag of a model (Т-§22.5, WP5.3); `model` is the model ID."""
    return f"llm:pressure:{model}"


def llm_circuit_key(model: str) -> str:
    """Circuit breaker of a model, visible to all processes (Т-§11.9, WP5.5)."""
    return f"llm:circuit:{model}"

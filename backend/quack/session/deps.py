"""FastAPI dependencies: Bearer JWT, tombstone check and turn rate limit (ТЗ §2.1)."""

from __future__ import annotations

from uuid import UUID

from fastapi import Request

from quack.api.app import AppResources
from quack.compute_client.client import ComputeClient
from quack.config import Settings
from quack.llm.gateway import LLMGateway


def get_settings_dep(request: Request) -> Settings:
    raise NotImplementedError


def get_llm_gateway(request: Request) -> LLMGateway:
    raise NotImplementedError


def get_compute_client(request: Request) -> ComputeClient:
    raise NotImplementedError


async def current_learner_id(request: Request) -> UUID:
    """Decode `Authorization: Bearer` (HS256, `sub` -> learner_id); 401 for tombstoned learners."""
    raise NotImplementedError


async def enforce_turn_rate_limit(
    resources: AppResources, settings: Settings, learner_id: UUID
) -> None:
    """20 turns per minute per learner; 429 RATE_LIMITED, fail-open when Redis is down."""
    raise NotImplementedError

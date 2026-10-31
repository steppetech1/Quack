"""LLMGateway.generate_structured (spec §8.3, Т-§11, Т-§12.4).

Order: prompt + hash → outbox replay → token budgets → semaphore → provider call with JSON
Schema → validation (one repair retry, Т-§11.1) → ledger + outbox + session tokens.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import httpx
import orjson
from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis
from sqlalchemy import text

from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.events.hashing import canonical_json
from quack.llm.errors import (
    LLMBudgetExceededError,
    LLMSchemaError,
    LLMUnavailableError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
)
from quack.llm.provider import LLMProvider, ProviderRequest, ProviderResponse, ReasoningEffort
from quack.logging import get_logger
from quack.prompts.registry import PromptRegistry, PromptTemplate

log = get_logger(__name__)


@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    latency_ms: int


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    value: T
    from_cache: bool
    prompt_hash: str
    model: str
    usage: LLMUsage | None


@dataclass(frozen=True)
class _CallContext:
    role: str
    prompt: PromptTemplate
    model: str
    learner_id: UUID
    session_id: UUID | None
    turn_id: UUID | None
    idempotency_key: str


class LLMGateway:
    def __init__(
        self,
        session_factory: SessionFactory,
        provider: LLMProvider,
        settings: Settings,
        prompts: PromptRegistry | None = None,
        *,
        redis: Redis | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.provider = provider
        self.settings = settings
        self.prompts = prompts or PromptRegistry()
        self.redis = redis
        # Pauses between retries; replaced in tests to record them (WP5.2).
        self.sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
        self.semaphore = asyncio.Semaphore(settings.llm_semaphore_limit)

    async def aclose(self) -> None:
        """Release the provider's HTTP client at process stop (§4.2, seam S1)."""
        close = getattr(self.provider, "aclose", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result
            return
        client = getattr(self.provider, "client", None)
        if isinstance(client, httpx.AsyncClient):
            await client.aclose()

    async def generate_structured[T: BaseModel](
        self,
        role: str,
        prompt_version: int,
        response_model: type[T],
        variables: dict[str, Any],
        learner_id: UUID,
        session_id: UUID | None,
        turn_id: UUID | None,
        idempotency_key: str,
        temperature: float = 0.0,
        *,
        prompt_role: str | None = None,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> LLMResult[T]:
        """Phase 3 (§6.6): `prompt_role` takes the template of `prompt_role@prompt_version`
        while model, limits, ledger and outbox stay with `role` (WP5.4); `reasoning_effort`
        overrides the role effort (WP6.5). Both are Stage I: not implemented yet."""
        if prompt_role is not None or reasoning_effort is not None:
            raise NotImplementedError("prompt_role / reasoning_effort: WP5.4, WP6.5")
        prompt = self.prompts.load(role, prompt_version)
        rendered = prompt.render(variables)
        model = self.settings.model_for_role(role)
        schema = response_model.model_json_schema()
        ctx = _CallContext(role, prompt, model, learner_id, session_id, turn_id, idempotency_key)

        cached = await self._completed_outbox(learner_id, idempotency_key)
        if cached is not None:
            return LLMResult(
                value=response_model.model_validate(cached[0]),
                from_cache=True,
                prompt_hash=prompt.sha256,
                model=cached[1],
                usage=None,
            )
        await self._check_budgets(learner_id, session_id)
        request_hash = hashlib.sha256(
            canonical_json(
                {
                    "role": role,
                    "prompt_version": prompt_version,
                    "prompt_hash": prompt.sha256,
                    "prompt": rendered,
                    "schema": schema,
                    "model": model,
                    "temperature": temperature,
                }
            )
        ).hexdigest()
        await self._mark_pending(ctx, request_hash)

        request = ProviderRequest(
            model=model,
            prompt=rendered,
            json_schema=schema,
            schema_name=response_model.__name__,
            temperature=temperature,
            timeout_sec=self.settings.llm_timeout_sec,
        )
        totals = [0, 0, 0, 0]  # input, output, cached, latency
        async with self.semaphore:
            response = await self._call(ctx, request, totals)
            value, error = _parse(response_model, response.content)
            if value is None:
                await self._ledger(ctx, response, totals[3], "schema_error", "schema")
                repair = ProviderRequest(**{**request.__dict__, "repair_hint": error})
                response = await self._call(ctx, repair, totals)
                value, error = _parse(response_model, response.content)
                if value is None:
                    await self._ledger(ctx, response, totals[3], "schema_error", "schema")
                    await self._finish_outbox(ctx, "failed", None, totals)
                    raise LLMSchemaError(f"{prompt.ref}: response does not match schema: {error}")
        await self._ledger(ctx, response, totals[3], "ok", None)
        await self._finish_outbox(ctx, "completed", value.model_dump(mode="json"), totals)
        usage = LLMUsage(totals[0], totals[1], totals[2], totals[3])
        return LLMResult(value, False, prompt.sha256, response.model, usage)

    # --- steps ---

    async def _completed_outbox(
        self, learner_id: UUID, key: str
    ) -> tuple[dict[str, Any], str] | None:
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT status, response_json, model FROM core.llm_outbox "
                        "WHERE learner_id = :l AND idempotency_key = :k"
                    ),
                    {"l": learner_id, "k": key},
                )
            ).first()
        if row is None or row.status != "completed" or row.response_json is None:
            return None
        return dict(row.response_json), str(row.model)

    async def _check_budgets(self, learner_id: UUID, session_id: UUID | None) -> None:
        async with self.session_factory() as session:
            daily = int(
                (
                    await session.execute(
                        text(
                            "SELECT COALESCE(SUM(input_tokens + output_tokens), 0) "
                            "FROM ops.llm_ledger WHERE learner_id = :l AND created_at >= "
                            "(date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')"
                        ),
                        {"l": learner_id},
                    )
                ).scalar_one()
            )
            limit = self.settings.daily_token_limit_per_learner
            if daily >= limit:
                raise LLMBudgetExceededError("daily", daily, limit)
            if session_id is not None:
                used = (
                    await session.execute(
                        text("SELECT tokens_used FROM core.sessions WHERE id = :s"),
                        {"s": session_id},
                    )
                ).scalar_one_or_none()
                limit = self.settings.session_token_limit
                if used is not None and int(used) >= limit:
                    raise LLMBudgetExceededError("session", int(used), limit)

    async def _mark_pending(self, ctx: _CallContext, request_hash: str) -> None:
        async with self.session_factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO core.llm_outbox (id, learner_id, idempotency_key, role, "
                    "prompt_version, model, status, request_hash, created_at, updated_at) VALUES "
                    "(:id, :l, :k, :role, :v, :model, 'pending', :h, now(), now()) "
                    "ON CONFLICT (learner_id, idempotency_key) DO UPDATE SET status = 'pending', "
                    "request_hash = EXCLUDED.request_hash, model = EXCLUDED.model, "
                    "updated_at = now()"
                ),
                {
                    "id": uuid4(),
                    "l": ctx.learner_id,
                    "k": ctx.idempotency_key,
                    "role": ctx.role,
                    "v": ctx.prompt.version,
                    "model": ctx.model,
                    "h": request_hash,
                },
            )

    async def _call(
        self, ctx: _CallContext, request: ProviderRequest, totals: list[int]
    ) -> ProviderResponse:
        started = time.monotonic()
        try:
            async with asyncio.timeout(request.timeout_sec):
                response = await self.provider.complete_structured(request)
        except (ProviderUnavailableError, TimeoutError) as exc:
            latency = int((time.monotonic() - started) * 1000)
            code = "rate_limited" if isinstance(exc, ProviderRateLimitedError) else "unavailable"
            if isinstance(exc, TimeoutError):
                code = "timeout"
            empty = ProviderResponse("", 0, 0, 0, ctx.model)
            await self._ledger(ctx, empty, latency, "error", code)
            await self._finish_outbox(ctx, "failed", None, totals)
            log.warning("llm_unavailable", role=ctx.role, code=code)
            raise LLMUnavailableError(f"{ctx.prompt.ref}: provider {code}") from exc
        latency = int((time.monotonic() - started) * 1000)
        totals[0] += response.input_tokens
        totals[1] += response.output_tokens
        totals[2] += response.cached_tokens
        totals[3] += latency
        return response

    async def _ledger(
        self,
        ctx: _CallContext,
        response: ProviderResponse,
        latency_ms: int,
        status: str,
        error_code: str | None,
    ) -> None:
        async with self.session_factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO ops.llm_ledger (id, learner_id, session_id, turn_id, role, "
                    "prompt_version, prompt_hash, model, input_tokens, output_tokens, "
                    "cached_tokens, latency_ms, status, error_code, created_at) VALUES "
                    "(:id, :l, :s, :t, :role, :v, :h, :model, :i, :o, :c, :lat, :status, :err, "
                    "now())"
                ),
                {
                    "id": uuid4(),
                    "l": ctx.learner_id,
                    "s": ctx.session_id,
                    "t": ctx.turn_id,
                    "role": ctx.role,
                    "v": ctx.prompt.version,
                    "h": ctx.prompt.sha256,
                    "model": response.model or ctx.model,
                    "i": response.input_tokens,
                    "o": response.output_tokens,
                    "c": response.cached_tokens,
                    "lat": latency_ms,
                    "status": status,
                    "err": error_code,
                },
            )

    async def _finish_outbox(
        self,
        ctx: _CallContext,
        status: str,
        response_json: dict[str, Any] | None,
        totals: list[int],
    ) -> None:
        usage = {"input_tokens": totals[0], "output_tokens": totals[1], "cached_tokens": totals[2]}
        async with self.session_factory() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE core.llm_outbox SET status = :status, "
                    "response_json = CAST(:resp AS JSONB), usage_json = CAST(:usage AS JSONB), "
                    "updated_at = now() WHERE learner_id = :l AND idempotency_key = :k"
                ),
                {
                    "status": status,
                    "resp": orjson.dumps(response_json).decode() if response_json else None,
                    "usage": orjson.dumps(usage).decode(),
                    "l": ctx.learner_id,
                    "k": ctx.idempotency_key,
                },
            )
            spent = totals[0] + totals[1]
            if ctx.session_id is not None and spent:
                await session.execute(
                    text(
                        "UPDATE core.sessions SET tokens_used = tokens_used + :t "
                        "WHERE id = :s AND learner_id = :l"
                    ),
                    {"t": spent, "s": ctx.session_id, "l": ctx.learner_id},
                )


def _parse[T: BaseModel](model: type[T], content: str) -> tuple[T | None, str]:
    try:
        return model.model_validate_json(content), ""
    except ValidationError as exc:
        return None, str(exc)

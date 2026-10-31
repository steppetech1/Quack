"""LLMGateway.generate_structured (spec §8.3, Т-§11, Т-§12.4; ТЗ Фазы 3 WP5.1–5.6).

Order: prompt + hash → outbox replay → token budgets → semaphore of the model → attempts
(retries on failures, 429 with the pressure flag, 400 without retry, one retry of a cut
answer, circuit breaker) → validation (one repair, Т-§11.1) → ledger + outbox + session
tokens. Every attempt writes a ledger row; short-circuited calls do not.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import math
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
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
    LLMError,
    LLMRateLimitedError,
    LLMSchemaError,
    LLMUnavailableError,
    ProviderBadRequestError,
    ProviderIncompleteError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
    UnavailableReason,
)
from quack.llm.pressure import (
    circuit_open_in_redis,
    low_remaining,
    open_circuit_in_redis,
    parse_retry_after,
    set_pressure_flag,
)
from quack.llm.provider import LLMProvider, ProviderRequest, ProviderResponse, ReasoningEffort
from quack.llm.strict_schema import to_strict_json_schema
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


# Roles of the hot path: a 429 is never waited out (WP5.2), admission is bounded (WP5.3).
HOT_PATH_ROLES = frozenset({"teacher", "teacher_fallback"})


@dataclass
class _Circuit:
    """Breaker of one model in this process (WP5.5): consecutive attempt failures, the
    monotonic time until which it is open (0 = closed), a probe in flight."""

    failures: int = 0
    open_until: float = 0.0
    probing: bool = False


def _latency(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class _CallContext:
    role: str
    prompt: PromptTemplate
    model: str
    learner_id: UUID
    session_id: UUID | None
    turn_id: UUID | None
    idempotency_key: str
    # WP5.7: the effort of the call, written to the ledger.
    reasoning_effort: str | None = None


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
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._circuits: dict[str, _Circuit] = {}

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
        """`prompt_role` takes the template of `prompt_role@prompt_version` while model,
        limits, ledger and outbox stay with `role` (WP5.4); `reasoning_effort` overrides the
        role effort (WP6.5)."""
        prompt = self.prompts.load(prompt_role or role, prompt_version)
        rendered = prompt.render(variables)
        model = self.settings.model_for_role(role)
        schema = response_model.model_json_schema()

        cached = await self._completed_outbox(learner_id, idempotency_key)
        if cached is not None:
            return LLMResult(
                value=response_model.model_validate(cached[0]),
                from_cache=True,
                prompt_hash=prompt.sha256,
                model=cached[1],
                usage=None,
            )
        exempt = role in self.settings.session_budget_exempt_roles
        await self._check_budgets(learner_id, None if exempt else session_id)
        effort = reasoning_effort or self.settings.llm_role_reasoning.get(role)
        ctx = _CallContext(
            role, prompt, model, learner_id, session_id, turn_id, idempotency_key, effort
        )
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
                    "reasoning_effort": effort,
                }
            )
        ).hexdigest()
        await self._mark_pending(ctx, request_hash)

        request = ProviderRequest(
            model=model,
            prompt=rendered,
            # WP5.7 (Т-§11.1): the strict schema goes to `text.format`; the answer is still
            # checked by `model_validate_json`.
            json_schema=to_strict_json_schema(response_model),
            schema_name=response_model.__name__,
            temperature=temperature,
            timeout_sec=self.settings.llm_role_timeout_s.get(role, self.settings.llm_timeout_sec),
            reasoning_effort=effort,
            max_output_tokens=self.settings.llm_role_max_output_tokens.get(role),
            prompt_cache_key=f"{prompt_role or role}@{prompt_version}",
        )
        totals = [0, 0, 0, 0]  # input, output, cached, latency
        try:
            async with self._admitted(role, model):
                response = await self._attempts(ctx, request, totals)
                value, error = _parse(response_model, response.content)
                if value is None:
                    await self._ledger(ctx, response, totals[3], "schema_error", "schema")
                    repair = replace(request, repair_hint=error)
                    response = await self._attempts(ctx, repair, totals)
                    value, error = _parse(response_model, response.content)
                    if value is None:
                        await self._ledger(ctx, response, totals[3], "schema_error", "schema")
                        raise LLMSchemaError(
                            f"{prompt.ref}: response does not match schema: {error}"
                        )
        except LLMError:
            await self._finish_outbox(ctx, "failed", None, totals)
            raise
        await self._ledger(ctx, response, totals[3], "ok", None)
        await self._finish_outbox(ctx, "completed", value.model_dump(mode="json"), totals)
        usage = LLMUsage(totals[0], totals[1], totals[2], totals[3])
        return LLMResult(value, False, prompt.sha256, response.model, usage)

    # --- admission: semaphores per model (WP5.1) ---

    def _semaphore(self, model: str) -> asyncio.Semaphore:
        semaphore = self._semaphores.get(model)
        if semaphore is None:
            limits = self.settings.llm_concurrency.get(self.settings.process_role, {})
            limit = limits.get(model, self.settings.llm_concurrency_default)
            semaphore = self._semaphores[model] = asyncio.Semaphore(limit)
        return semaphore

    @asynccontextmanager
    async def _admitted(self, role: str, model: str) -> AsyncIterator[None]:
        """The teacher waits for a slot no longer than `teacher_admission_wait_ms`, then the
        turn goes to `teacher_fallback` (WP5.3); other roles wait."""
        semaphore = self._semaphore(model)
        if role in HOT_PATH_ROLES:
            try:
                async with asyncio.timeout(self.settings.teacher_admission_wait_ms / 1000):
                    await semaphore.acquire()
            except TimeoutError as exc:
                log.warning("llm_admission_timeout", role=role, model=model)
                raise LLMRateLimitedError(f"{role}: no free slot of {model}") from exc
        else:
            await semaphore.acquire()
        try:
            yield
        finally:
            semaphore.release()

    # --- attempts: retries, 429, 400, cut answers, circuit (WP5.2, WP5.3, WP5.5) ---

    async def _attempts(
        self, ctx: _CallContext, request: ProviderRequest, totals: list[int]
    ) -> ProviderResponse:
        settings = self.settings
        started = time.monotonic()
        failures = rate_limited = 0
        cut_retried = False
        while True:
            await self._admit_circuit(ctx)
            attempt_started = time.monotonic()
            try:
                async with asyncio.timeout(request.timeout_sec):
                    response = await self.provider.complete_structured(request)
            except ProviderRateLimitedError as exc:
                self._circuit_neutral(ctx.model)
                ttl = await self._rate_limited(ctx, exc, _latency(attempt_started))
                if ctx.role in HOT_PATH_ROLES or rate_limited >= settings.llm_rate_limit_retries:
                    raise LLMRateLimitedError(
                        f"{ctx.prompt.ref}: rate limited", retry_after_sec=ttl
                    ) from exc
                rate_limited += 1
                pause = ttl
                if pause is None:
                    base = settings.llm_rate_limit_base_ms / 1000 * 2 ** (rate_limited - 1)
                    pause = random.uniform(0.5, 1.5) * base
                if pause > settings.llm_rate_limit_max_wait_s:
                    raise LLMRateLimitedError(
                        f"{ctx.prompt.ref}: retry-after above the wait limit", retry_after_sec=ttl
                    ) from exc
                await self.sleep(pause)
                continue
            except ProviderBadRequestError as exc:
                self._circuit_neutral(ctx.model)
                await self._error_ledger(ctx, _latency(attempt_started), "error", "bad_request")
                log.warning("llm_bad_request", role=ctx.role, model=ctx.model, body=exc.body)
                raise LLMUnavailableError(
                    f"{ctx.prompt.ref}: bad request", reason="bad_request"
                ) from exc
            except ProviderIncompleteError as exc:
                self._circuit_neutral(ctx.model)
                await self._error_ledger(ctx, _latency(attempt_started), "incomplete", "incomplete")
                limit = request.max_output_tokens
                cap = settings.max_output_tokens_cap
                if cut_retried or limit is None or limit >= cap:
                    raise LLMSchemaError(
                        f"{ctx.prompt.ref}: answer cut by the output limit", reason="incomplete"
                    ) from exc
                cut_retried = True
                request = replace(request, max_output_tokens=min(limit * 2, cap))
                continue
            except (ProviderUnavailableError, TimeoutError) as exc:
                code: UnavailableReason = (
                    "timeout" if isinstance(exc, TimeoutError) else "unavailable"
                )
                await self._error_ledger(ctx, _latency(attempt_started), "error", code)
                await self._circuit_failure(ctx.model)
                log.warning("llm_unavailable", role=ctx.role, code=code)
                if failures >= settings.llm_retry_attempts:
                    raise LLMUnavailableError(
                        f"{ctx.prompt.ref}: provider {code}", reason=code
                    ) from exc
                failures += 1
                pause = random.uniform(0, settings.llm_retry_base_ms / 1000 * 2 ** (failures - 1))
                if time.monotonic() - started + pause > request.timeout_sec:
                    raise LLMUnavailableError(
                        f"{ctx.prompt.ref}: provider {code}", reason=code
                    ) from exc
                await self.sleep(pause)
                continue
            self._circuit_success(ctx.model)
            latency = _latency(attempt_started)
            totals[0] += response.input_tokens
            totals[1] += response.output_tokens
            totals[2] += response.cached_tokens
            totals[3] += latency
            if low_remaining(response.headers, settings.pressure_remaining_share):
                await set_pressure_flag(
                    self.redis, ctx.model, settings.pressure_header_ttl_s, "headers", _now()
                )
            return response

    async def _rate_limited(
        self, ctx: _CallContext, exc: ProviderRateLimitedError, latency_ms: int
    ) -> float | None:
        """Ledger row, the whole 429 in the log (Т-§11.8), the pressure flag; returns the
        `retry-after` in seconds (number or HTTP date) or `None`."""
        await self._error_ledger(ctx, latency_ms, "error", "rate_limited")
        raw = next((v for k, v in exc.headers.items() if k.lower() == "retry-after"), None)
        now = _now()
        ttl = parse_retry_after(raw, now) if raw is not None else exc.retry_after_sec
        log.warning(
            "llm_rate_limited",
            role=ctx.role,
            model=ctx.model,
            status=429,
            retry_after=raw,
            ratelimit={
                k: v for k, v in exc.headers.items() if k.lower().startswith("x-ratelimit-")
            },
            body=exc.body,
        )
        flag_ttl = math.ceil(ttl) if ttl is not None else self.settings.pressure_default_ttl_s
        await set_pressure_flag(self.redis, ctx.model, flag_ttl, "429", now)
        return ttl

    async def _admit_circuit(self, ctx: _CallContext) -> None:
        """Short-circuit while the breaker of the model is open; after `circuit_open_s` let
        exactly one probe through (WP5.5). Short-circuited calls are logged, not ledgered."""
        circuit = self._circuits.setdefault(ctx.model, _Circuit())
        now = time.monotonic()
        blocked = circuit.open_until > now or (circuit.open_until > 0 and circuit.probing)
        if not blocked and circuit.open_until == 0:
            blocked = await circuit_open_in_redis(self.redis, ctx.model)
        if blocked:
            log.warning("llm_circuit_open", role=ctx.role, model=ctx.model)
            raise LLMUnavailableError(f"{ctx.prompt.ref}: circuit open", reason="circuit_open")
        if circuit.open_until > 0:
            circuit.probing = True

    def _circuit_success(self, model: str) -> None:
        self._circuits[model] = _Circuit()

    def _circuit_neutral(self, model: str) -> None:
        """429, 400 and cut answers are not failures of the breaker; a probe ends without a
        verdict and the next call probes again."""
        self._circuits.setdefault(model, _Circuit()).probing = False

    async def _circuit_failure(self, model: str) -> None:
        circuit = self._circuits.setdefault(model, _Circuit())
        circuit.failures += 1
        if circuit.probing or circuit.failures >= self.settings.circuit_failures:
            open_s = self.settings.circuit_open_s
            self._circuits[model] = _Circuit(open_until=time.monotonic() + open_s)
            log.warning("llm_circuit_opened", model=model, open_s=open_s)
            await open_circuit_in_redis(self.redis, model, open_s, _now())

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

    async def _error_ledger(
        self, ctx: _CallContext, latency_ms: int, status: str, code: str
    ) -> None:
        empty = ProviderResponse("", 0, 0, 0, ctx.model)
        await self._ledger(ctx, empty, latency_ms, status, code)

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
                    "cached_tokens, cached_input_tokens, reasoning_tokens, reasoning_effort, "
                    "latency_ms, status, error_code, created_at) VALUES "
                    "(:id, :l, :s, :t, :role, :v, :h, :model, :i, :o, :c, :ci, :r, :effort, "
                    ":lat, :status, :err, now())"
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
                    "ci": response.cached_tokens,
                    "r": response.reasoning_tokens,
                    "effort": ctx.reasoning_effort,
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

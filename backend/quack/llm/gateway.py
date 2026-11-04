"""LLMGateway.generate_structured (spec §8.3, Т-§11, Т-§12.4; ТЗ Фазы 3 WP5.1–5.6).

Order: prompt + hash → dollar budgets → semaphore of the model → attempts (retries on
failures, 429 with the pressure flag, 400 without retry, one retry of a cut answer, circuit
breaker) → validation (one repair, Т-§11.1) → a row of `ops.llm_usage` per attempt. The call is
not deduplicated: a repeated job may pay again, event keys keep the facts single (law 5,
D-B-212). Short-circuited calls write no row.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import math
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis

from quack.config import Settings
from quack.db import Db
from quack.llm.errors import (
    LLMRateLimitedError,
    LLMSchemaError,
    LLMUnavailableError,
    ProviderBadRequestError,
    ProviderIncompleteError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
    UnavailableReason,
)
from quack.llm.media import (
    BoxPrompt,
    Illustration,
    IllustrationRequest,
    ImageResponse,
    MaskEncoding,
    MediaProvider,
    Segmentation,
    SegmentRequest,
    TranscribeMode,
    TranscribeRequest,
    Transcription,
    parse_segmentation,
)
from quack.llm.pressure import (
    circuit_open_in_redis,
    low_remaining,
    open_circuit_in_redis,
    parse_retry_after,
    set_pressure_flag,
)
from quack.llm.provider import (
    ImageInput,
    LLMProvider,
    ProviderRequest,
    ProviderResponse,
    ReasoningEffort,
)
from quack.llm.streaming import (
    StreamCompleted,
    StreamEvent,
    StreamingLLMProvider,
    StreamRequest,
)
from quack.llm.strict_schema import to_strict_json_schema
from quack.llm.tracing import NullTracer, Tracer, TraceRecord, learner_pseudonym
from quack.llm.usage import UsageRow, call_cost_usd, check_usd_budgets, insert_usage
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
    # WP5.7: the effort of the call, written to the usage row.
    reasoning_effort: str | None = None
    # WP10.4: rows written so far for this call; a row after the first is a retry.
    rows: list[int] = field(default_factory=lambda: [0])


class LLMGateway:
    def __init__(
        self,
        provider: LLMProvider,
        settings: Settings,
        prompts: PromptRegistry | None = None,
        *,
        db: Db,
        redis: Redis | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        # WP10.6: a trace of each attempt; without a tracer — none (tests, `loadtest`).
        self.tracer: Tracer = tracer or NullTracer()
        # WP10.4 (D-B-197): every attempt is a row of `ops.llm_usage` with its cost; the
        # budgets are in dollars (WP1.3 step 4, D-B-212).
        self.db = db
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
        try:
            await self.tracer.aclose()
        except Exception:
            log.warning("llm_tracer_close_failed")
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
        check: Callable[[T], str | None] | None = None,
        hard: bool = False,
        images: Sequence[ImageInput] = (),
    ) -> LLMResult[T]:
        """`idempotency_key` is kept for callers: the gateway does not deduplicate, a repeat
        pays again (D-B-212). `prompt_role` takes the template of `prompt_role@prompt_version`
        while model, limits and usage stay with `role` (WP5.4); `reasoning_effort` overrides the
        role effort (WP6.5); `hard` takes the role's `hard_model` (Т-§11.1, D-B-166);
        `images` go after the prompt text (Т-§11.1 «Spark с изображением», D-B-170).

        `check` finds what the schema cannot (an unknown label, WP14.1): its error text goes
        back to the model once through the repair path; if the second answer is wrong again or
        does not parse, the first valid answer is returned and the caller drops what is wrong.
        No second repair after a schema repair: one repair call per request."""
        prompt = self.prompts.load(prompt_role or role, prompt_version)
        rendered = prompt.render(variables)
        model = self.settings.model_for_role(role, hard=hard)

        exempt = role in self.settings.session_budget_exempt_roles
        await self._admit_budget(role, learner_id, None if exempt else session_id)
        effort = reasoning_effort or self.settings.effort_for_role(role)
        ctx = _CallContext(
            role, prompt, model, learner_id, session_id, turn_id, idempotency_key, effort
        )
        request = ProviderRequest(
            model=model,
            prompt=rendered,
            # WP5.7 (Т-§11.1): the strict schema goes to `text.format`; the answer is still
            # checked by `model_validate_json`.
            json_schema=to_strict_json_schema(response_model),
            schema_name=response_model.__name__,
            temperature=temperature,
            timeout_sec=self.settings.timeout_for_role(role),
            reasoning_effort=effort,
            max_output_tokens=self.settings.max_output_tokens_for_role(role),
            prompt_cache_key=f"{prompt_role or role}@{prompt_version}",
            images=tuple(images),
        )
        totals = [0, 0, 0, 0]  # input, output, cached, latency
        async with self._admitted(role, model):
            response = await self._attempts(ctx, request, totals)
            value, error = _parse(response_model, response.content)
            problem = None
            if value is None:
                await self._record(ctx, response, totals[3], "schema_error", "schema")
                repair = replace(request, repair_hint=error)
                response = await self._attempts(ctx, repair, totals)
                value, error = _parse(response_model, response.content)
                if value is None:
                    await self._record(ctx, response, totals[3], "schema_error", "schema")
                    raise LLMSchemaError(f"{prompt.ref}: response does not match schema: {error}")
            elif check is not None and (problem := check(value)):
                await self._record(ctx, response, totals[3], "schema_error", "label")
                first, first_response = value, response
                response = await self._attempts(ctx, replace(request, repair_hint=problem), totals)
                again, _ = _parse(response_model, response.content)
                if again is None:
                    await self._record(ctx, response, totals[3], "schema_error", "schema")
                    value, response = first, first_response
                else:
                    value = again
        await self._record(ctx, response, totals[3], "ok", None)
        usage = LLMUsage(totals[0], totals[1], totals[2], totals[3])
        return LLMResult(value, False, prompt.sha256, response.model, usage)

    # --- streamed steps of the teacher turn (ТЗ Ф4 WP10.2–10.3, D-B-152) ---

    def stream_request(
        self,
        role: str,
        prompt_version: int,
        response_model: type[BaseModel],
        variables: dict[str, Any],
        *,
        prompt_role: str | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        tools: Sequence[Mapping[str, Any]] = (),
    ) -> tuple[StreamRequest, PromptTemplate]:
        """The first step of a streamed call: the rendered prompt, the strict schema, model,
        effort, limits and cache key of `role` — as `generate_structured` builds them."""
        prompt = self.prompts.load(prompt_role or role, prompt_version)
        request = ProviderRequest(
            model=self.settings.model_for_role(role),
            prompt=prompt.render(variables),
            json_schema=to_strict_json_schema(response_model),
            schema_name=response_model.__name__,
            temperature=0.0,
            timeout_sec=self.settings.timeout_for_role(role),
            reasoning_effort=reasoning_effort or self.settings.effort_for_role(role),
            max_output_tokens=self.settings.max_output_tokens_for_role(role),
            prompt_cache_key=f"{prompt_role or role}@{prompt_version}",
        )
        return StreamRequest(request, tools=tuple(tools)), prompt

    def streaming(
        self,
        role: str,
        prompt: PromptTemplate,
        *,
        learner_id: UUID,
        session_id: UUID | None,
        turn_id: UUID | None,
    ) -> StreamingLLMProvider:
        """A provider of streamed steps for the turn graph: each step passes the breaker and
        the model slot (the teacher waits no longer than `teacher_admission_wait_ms`), gets a
        usage row and updates the breaker and the pressure flag (WP5 of Phase 3)."""
        if not hasattr(self.provider, "stream_structured"):
            raise TypeError(f"{type(self.provider).__name__} does not stream")
        model = self.settings.model_for_role(role)
        key = f"stream:{turn_id or uuid4()}"
        ctx = _CallContext(role, prompt, model, learner_id, session_id, turn_id, key)
        return _GatewayStream(self, ctx)

    # --- non-text roles (ТЗ Ф4 WP10.1, D-B-187) ---

    async def segment(
        self,
        image: ImageInput,
        *,
        learner_id: UUID,
        session_id: UUID | None = None,
        turn_id: UUID | None = None,
        concept: str | None = None,
        box: BoxPrompt | None = None,
        mask_encoding: MaskEncoding = "lossless",
        include_confidence: bool = True,
    ) -> LLMResult[Segmentation]:
        """Role `segmenter` (`sam-3.1`): objects of the image by a short name `concept` or by
        a `box` (exactly one of them), with boxes, masks and confidence (up to 16)."""
        if (concept is None) == (box is None):
            raise ValueError("segment: exactly one of concept and box")
        provider = self._media_provider()
        model = self.settings.model_for_role("segmenter")

        def build(timeout: float) -> SegmentRequest:
            return SegmentRequest(
                model, image, timeout, concept, box, mask_encoding, include_confidence
            )

        response, usage = await self._media_call(
            "segmenter",
            learner_id,
            session_id,
            turn_id,
            lambda timeout: provider.segment(build(timeout)),
        )
        value = parse_segmentation(response.content, mask_encoding)
        return LLMResult(value, False, _media_hash("segmenter"), response.model, usage)

    async def transcribe(
        self,
        wav: bytes,
        *,
        learner_id: UUID,
        session_id: UUID | None = None,
        turn_id: UUID | None = None,
        mode: TranscribeMode = "PUSH_TO_TALK",
        language_bias: str | None = None,
        keywords: Sequence[str] = (),
    ) -> LLMResult[Transcription]:
        """Role `voice` (`muse-voice-transcribe-1.0`): the transcript of a WAV (mono, 16-bit
        PCM, 16 or 24 kHz, up to 10 min and 32 MB); the caller converts the browser audio."""
        provider = self._media_provider()
        model = self.settings.model_for_role("voice")
        response, usage = await self._media_call(
            "voice",
            learner_id,
            session_id,
            turn_id,
            lambda timeout: provider.transcribe(
                TranscribeRequest(model, wav, timeout, mode, language_bias, tuple(keywords))
            ),
        )
        value = Transcription(
            text=response.content,
            audio_duration_ms=response.audio_duration_ms,
            turns=list(response.turns),
        )
        return LLMResult(value, False, _media_hash("voice"), response.model, usage)

    async def illustrate(
        self,
        prompt: str,
        *,
        learner_id: UUID,
        session_id: UUID | None = None,
        turn_id: UUID | None = None,
        size: str | None = None,
    ) -> LLMResult[Illustration]:
        """Role `illustrator` (`muse-image-1.0`): a conceptual illustration, not mathematics
        (П-4.5); bytes of the image or a reference."""
        provider = self._media_provider()
        model = self.settings.model_for_role("illustrator")
        response, usage = await self._media_call(
            "illustrator",
            learner_id,
            session_id,
            turn_id,
            lambda timeout: provider.illustrate(IllustrationRequest(model, prompt, timeout, size)),
        )
        value = Illustration(image=response.image, url=response.url, mime=response.mime)
        return LLMResult(value, False, _media_hash("illustrator"), response.model, usage)

    def _media_provider(self) -> MediaProvider:
        if not all(hasattr(self.provider, n) for n in ("segment", "transcribe", "illustrate")):
            raise TypeError(f"{type(self.provider).__name__} has no non-text roles")
        return self.provider  # type: ignore[return-value]

    async def _media_call[R: ProviderResponse](
        self,
        role: str,
        learner_id: UUID,
        session_id: UUID | None,
        turn_id: UUID | None,
        invoke: Callable[[float], Awaitable[R]],
    ) -> tuple[R, LLMUsage]:
        """The path of a non-text call: budgets, slot of the model, attempts (retries, 429 with
        the pressure flag, 400 without retry, breaker), one usage row per attempt. There is no
        prompt and no schema repair: a repeated call is a repeated charge."""
        model = self.settings.model_for_role(role)
        timeout = self.settings.timeout_for_role(role)
        exempt = role in self.settings.session_budget_exempt_roles
        await self._admit_budget(role, learner_id, None if exempt else session_id)
        template = PromptTemplate(role, 0, "", _media_hash(role))
        ctx = _CallContext(role, template, model, learner_id, session_id, turn_id, "")
        request = ProviderRequest(
            model=model,
            prompt="",
            json_schema={},
            schema_name=role,
            temperature=0.0,
            timeout_sec=timeout,
        )
        totals = [0, 0, 0, 0]
        async with self._admitted(role, model):
            response = await self._attempts_with(
                ctx, request, totals, lambda _request: invoke(timeout)
            )
        await self._record(ctx, response, totals[3], "ok", None)
        return response, LLMUsage(totals[0], totals[1], totals[2], totals[3])

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
        return await self._attempts_with(ctx, request, totals, self.provider.complete_structured)

    async def _attempts_with[R: ProviderResponse](
        self,
        ctx: _CallContext,
        request: ProviderRequest,
        totals: list[int],
        invoke: Callable[[ProviderRequest], Awaitable[R]],
    ) -> R:
        settings = self.settings
        started = time.monotonic()
        failures = rate_limited = 0
        cut_retried = False
        while True:
            await self._admit_circuit(ctx)
            attempt_started = time.monotonic()
            try:
                async with asyncio.timeout(request.timeout_sec):
                    response = await invoke(request)
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
                await self._record_error(ctx, _latency(attempt_started), "error", "bad_request")
                log.warning("llm_bad_request", role=ctx.role, model=ctx.model, body=exc.body)
                raise LLMUnavailableError(
                    f"{ctx.prompt.ref}: bad request", reason="bad_request"
                ) from exc
            except ProviderIncompleteError as exc:
                self._circuit_neutral(ctx.model)
                await self._record_error(ctx, _latency(attempt_started), "incomplete", "incomplete")
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
                await self._record_error(ctx, _latency(attempt_started), "error", code)
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
        await self._record_error(ctx, latency_ms, "error", "rate_limited")
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
        exactly one probe through (WP5.5). Short-circuited calls are logged, not recorded."""
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

    async def _admit_budget(self, role: str, learner_id: UUID, session_id: UUID | None) -> None:
        await check_usd_budgets(self.db, self.settings, role, learner_id, session_id)

    async def _record_error(
        self, ctx: _CallContext, latency_ms: int, status: str, code: str
    ) -> None:
        empty = ProviderResponse("", 0, 0, 0, ctx.model)
        await self._record(ctx, empty, latency_ms, status, code)

    async def _record(
        self,
        ctx: _CallContext,
        response: ProviderResponse,
        latency_ms: int,
        status: str,
        error_code: str | None,
    ) -> None:
        retried = ctx.rows[0] > 0
        ctx.rows[0] += 1
        await self._usage(ctx, response, latency_ms, status, retried)
        await self._trace(ctx, response, latency_ms, status, error_code)

    async def _trace(
        self,
        ctx: _CallContext,
        response: ProviderResponse,
        latency_ms: int,
        status: str,
        error_code: str | None,
    ) -> None:
        """WP10.6: a trace of the attempt after its usage row. A failure of the tracer — a log
        line, never an error of the call."""
        try:
            await self.tracer.trace(
                TraceRecord(
                    role=ctx.role,
                    prompt=ctx.prompt.ref,
                    pseudonym=learner_pseudonym(
                        self.settings.langsmith_pseudonym_ns, ctx.learner_id
                    ),
                    model=response.model or ctx.model,
                    status=status,
                    error_code=error_code,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    cached_tokens=response.cached_tokens,
                    reasoning_tokens=response.reasoning_tokens,
                    latency_ms=latency_ms,
                    ended_at=_now(),
                )
            )
        except Exception:
            log.warning("llm_trace_failed", role=ctx.role)

    async def _usage(
        self,
        ctx: _CallContext,
        response: ProviderResponse,
        latency_ms: int,
        status: str,
        retried: bool,
    ) -> None:
        """A row of `ops.llm_usage` with the cost (Т-§11.4, D-B-197). Voice has no tokens: the
        price is by the audio length; the illustrator: by the picture (D-B-198)."""
        model = response.model or ctx.model
        audio_ms = int(getattr(response, "audio_duration_ms", 0))
        images = 1 if isinstance(response, ImageResponse) and status == "ok" else 0
        cost = call_cost_usd(
            self.settings,
            model,
            input_tokens=response.input_tokens,
            cached_tokens=response.cached_tokens,
            output_tokens=response.output_tokens,
            audio_ms=audio_ms,
            images=images,
        )
        row = UsageRow(
            learner_id=ctx.learner_id,
            session_id=ctx.session_id,
            role=ctx.role,
            model=model,
            prompt_version=ctx.prompt.ref,
            effort=ctx.reasoning_effort,
            input_tokens=response.input_tokens,
            cached_input_tokens=response.cached_tokens,
            output_tokens=response.output_tokens,
            reasoning_tokens=response.reasoning_tokens,
            cost_usd=cost,
            incomplete=status == "incomplete",
            retried=retried,
            latency_ms=latency_ms,
            status=status,
        )
        await insert_usage(self.db, row.learner_id, row)


class _GatewayStream:
    """`StreamingLLMProvider` over the gateway. A provider error before the first event of a
    step is retried like `_attempts` (unavailable, timeout; a 429 of the hot path is not); after
    the first event nothing is retried — the learner may have seen it. A cut step is passed
    on: the turn graph retries it with the doubled limit (Т-§11.1)."""

    def __init__(self, gateway: LLMGateway, ctx: _CallContext) -> None:
        self._gw = gateway
        self._ctx = ctx

    async def stream_structured(self, request: StreamRequest) -> AsyncIterator[StreamEvent]:
        gw, settings = self._gw, self._gw.settings
        provider: StreamingLLMProvider = self._gw.provider  # type: ignore[assignment]
        ctx = replace(
            self._ctx,
            model=request.base.model,
            reasoning_effort=request.base.reasoning_effort,
            rows=[0],
        )
        failures = 0
        while True:
            await gw._admit_circuit(ctx)  # pyright: ignore[reportPrivateUsage]
            started = time.monotonic()
            deadline = started + request.base.timeout_sec
            yielded = False
            try:
                async with gw._admitted(ctx.role, ctx.model):  # pyright: ignore[reportPrivateUsage]
                    events = provider.stream_structured(request).__aiter__()
                    try:
                        while True:
                            async with asyncio.timeout(max(deadline - time.monotonic(), 0)):
                                try:
                                    event = await anext(events)
                                except StopAsyncIteration:
                                    break
                            if isinstance(event, StreamCompleted):
                                await self._completed(ctx, event, _latency(started))
                            yielded = True
                            yield event
                    finally:
                        aclose = getattr(events, "aclose", None)
                        if aclose is not None:
                            await aclose()
                return
            except ProviderRateLimitedError as exc:
                gw._circuit_neutral(ctx.model)  # pyright: ignore[reportPrivateUsage]
                ttl = await gw._rate_limited(ctx, exc, _latency(started))  # pyright: ignore[reportPrivateUsage]
                raise LLMRateLimitedError(
                    f"{ctx.prompt.ref}: rate limited", retry_after_sec=ttl
                ) from exc
            except ProviderBadRequestError as exc:
                gw._circuit_neutral(ctx.model)  # pyright: ignore[reportPrivateUsage]
                await gw._record_error(ctx, _latency(started), "error", "bad_request")  # pyright: ignore[reportPrivateUsage]
                log.warning("llm_bad_request", role=ctx.role, model=ctx.model, body=exc.body)
                raise LLMUnavailableError(
                    f"{ctx.prompt.ref}: bad request", reason="bad_request"
                ) from exc
            except (ProviderUnavailableError, TimeoutError) as exc:
                code: UnavailableReason = (
                    "timeout" if isinstance(exc, TimeoutError) else "unavailable"
                )
                await gw._record_error(ctx, _latency(started), "error", code)  # pyright: ignore[reportPrivateUsage]
                await gw._circuit_failure(ctx.model)  # pyright: ignore[reportPrivateUsage]
                log.warning("llm_unavailable", role=ctx.role, code=code)
                if yielded or failures >= settings.llm_retry_attempts:
                    raise LLMUnavailableError(
                        f"{ctx.prompt.ref}: provider {code}", reason=code
                    ) from exc
                failures += 1
                await gw.sleep(
                    random.uniform(0, settings.llm_retry_base_ms / 1000 * 2 ** (failures - 1))
                )

    async def _completed(self, ctx: _CallContext, end: StreamCompleted, latency_ms: int) -> None:
        gw = self._gw
        usage = end.usage
        response = ProviderResponse(
            content="",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_tokens=usage.cached_tokens,
            model=end.model or ctx.model,
            reasoning_tokens=usage.reasoning_tokens,
        )
        if end.status == "incomplete":
            gw._circuit_neutral(ctx.model)  # pyright: ignore[reportPrivateUsage]
            await gw._record(ctx, response, latency_ms, "incomplete", "incomplete")  # pyright: ignore[reportPrivateUsage]
            return
        gw._circuit_success(ctx.model)  # pyright: ignore[reportPrivateUsage]
        await gw._record(ctx, response, latency_ms, "ok", None)  # pyright: ignore[reportPrivateUsage]
        if low_remaining(end.headers, gw.settings.pressure_remaining_share):
            await set_pressure_flag(
                gw.redis, ctx.model, gw.settings.pressure_header_ttl_s, "headers", _now()
            )


def _media_hash(role: str) -> str:
    """`prompt_hash` of the usage row of a non-text role: there is no prompt (version 0)."""
    return hashlib.sha256(f"{role}@media".encode()).hexdigest()


def _parse[T: BaseModel](model: type[T], content: str) -> tuple[T | None, str]:
    try:
        return model.model_validate_json(content), ""
    except ValidationError as exc:
        return None, str(exc)

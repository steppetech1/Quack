import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any, Protocol, TypeVar, runtime_checkable

import httpx
import openai
from openai import AsyncOpenAI
from pydantic import BaseModel
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app import keys
from app.config import Settings
from app.errors import LLMUnavailable
from app.llm.structured import (
    parse_structured_output,
    structured_validation_retry_message,
)
from app.schemas.chat import StreamEvent, TextDelta, ToolCall
from app.schemas.llm import (
    LLMMessage,
    LLMResult,
    LLMStatus,
    LLMUsage,
    ModelSlot,
    ToolCallOut,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def _decode(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


@runtime_checkable
class LLMLike(Protocol):
    async def complete(
        self,
        messages: list[LLMMessage],
        slot: ModelSlot,
        tools: list[dict] | None = None,
        temperature: float | None = None,
        response_format: dict | None = None,
    ) -> LLMResult: ...

    async def stream(
        self,
        messages: list[LLMMessage],
        slot: ModelSlot,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]: ...

    async def structured(
        self, messages: list[LLMMessage], schema: type[T], slot: ModelSlot
    ) -> T: ...

    def last_usage(self) -> LLMUsage | None: ...

    async def status(self) -> LLMStatus: ...


class LLMClient:
    def __init__(self, settings: Settings, redis: Redis) -> None:
        self._settings = settings
        self._redis = redis
        self._client = AsyncOpenAI(
            base_url=settings.LLM_BASE_URL,
            # The SDK refuses an empty key at construction; the app must still
            # start and say "assistant down" (§6.3), so the placeholder is
            # never sent: `_keyless` stops every call first.
            api_key=settings.LLM_API_KEY.get_secret_value() or "unset",
            max_retries=0,
        )
        self._last_usage: LLMUsage | None = None

    @property
    def _keyless(self) -> bool:
        return not self._settings.LLM_API_KEY.get_secret_value()

    def last_usage(self) -> LLMUsage | None:
        """Tokens of the most recent call on this client.

        `structured` и `stream` возвращают разобранный результат, а не
        `LLMResult`, поэтому расход токенов иначе был бы виден только как
        оценка по прайсу. Значение перезаписывается каждым вызовом —
        читать сразу после интересующего.
        """
        return self._last_usage

    @staticmethod
    def _usage_of(response: Any) -> LLMUsage | None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return None
        return LLMUsage(
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
        )

    def _model_for(self, slot: ModelSlot) -> str:
        if slot == "chat":
            return self._settings.MODEL_CHAT
        return self._settings.MODEL_BULK

    def _timeout_for(self, slot: ModelSlot) -> httpx.Timeout:
        seconds = (
            self._settings.LLM_TIMEOUT_CHAT_S
            if slot == "chat"
            else self._settings.LLM_TIMEOUT_BULK_S
        )
        return httpx.Timeout(seconds)

    def _reasoning_for(self, slot: ModelSlot) -> str | None:
        if slot == "chat":
            return self._settings.LLM_REASONING_CHAT
        return self._settings.LLM_REASONING_BULK

    def _thinking_for(self, slot: ModelSlot) -> str | None:
        if slot == "chat":
            return self._settings.LLM_THINKING_CHAT
        return self._settings.LLM_THINKING_BULK

    def _rate_limit_wait_s(self, slot: ModelSlot) -> float:
        return 5.0 if slot == "chat" else 30.0

    def _to_openai_messages(self, messages: list[LLMMessage]) -> list[dict]:
        result: list[dict] = []
        for message in messages:
            entry: dict = {"role": message.role}
            if message.content is not None:
                entry["content"] = message.content
            if message.tool_calls is not None:
                entry["tool_calls"] = [
                    {
                        "id": call.call_id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.args),
                        },
                    }
                    for call in message.tool_calls
                ]
            if message.tool_call_id is not None:
                entry["tool_call_id"] = message.tool_call_id
            if message.name is not None:
                entry["name"] = message.name
            result.append(entry)
        return result

    async def _chat_is_busy(self) -> bool:
        """Phase 4 (§10.4): the live chat has priority over background work.

        Two independent counters already split the provider's budget; this
        adds the courtesy on top — while the chat is near its own limit, or
        the breaker is degraded, `bulk` waits instead of competing. The chat
        never waits for `bulk`.
        """
        try:
            status = _decode(await self._redis.get(keys.llm_status()))
            if status == "degraded":
                return True
            raw = await self._redis.get(keys.llm_ratelimit("chat"))
        except RedisError:
            return False
        if raw is None:
            return False
        try:
            used = int(_decode(raw) or 0)
        except ValueError:
            return False
        share = self._settings.LLM_BULK_YIELD_SHARE
        return used >= share * self._settings.LLM_RPM_CHAT

    async def _acquire(self, slot: ModelSlot) -> None:
        limit = (
            self._settings.LLM_RPM_CHAT
            if slot == "chat"
            else self._settings.LLM_RPM_BULK
        )
        key = keys.llm_ratelimit(slot)
        deadline = time.monotonic() + self._rate_limit_wait_s(slot)
        while True:
            if slot == "bulk" and await self._chat_is_busy():
                if time.monotonic() >= deadline:
                    raise LLMUnavailable("rate limit")
                await asyncio.sleep(0.1)
                continue
            try:
                count = await self._redis.incr(key)
                if count == 1:
                    await self._redis.expire(key, 60)
            except RedisError as exc:
                logger.warning("redis unavailable for llm rate limit: %s", exc)
                return
            if count <= limit:
                return
            try:
                await self._redis.decr(key)
            except RedisError as exc:
                logger.warning("redis unavailable for llm rate limit: %s", exc)
                return
            if time.monotonic() >= deadline:
                raise LLMUnavailable("rate limit")
            await asyncio.sleep(0.1)

    def _is_breaker_error(self, exc: Exception) -> bool:
        connection_errors = (
            openai.APITimeoutError,
            openai.APIConnectionError,
            openai.RateLimitError,
        )
        if isinstance(exc, connection_errors):
            return True
        # A refused key leaves the provider unusable for everyone: it must
        # open the breaker, or /health says ok where nothing answers.
        return isinstance(exc, openai.APIStatusError) and (
            exc.status_code >= 500 or exc.status_code in (401, 403)
        )

    def _is_retryable_error(self, exc: Exception) -> bool:
        if isinstance(exc, openai.APITimeoutError | openai.RateLimitError):
            return True
        return isinstance(exc, openai.APIStatusError) and exc.status_code >= 500

    async def _check_breaker(self) -> None:
        if self._settings.LLM_FORCE_DOWN or self._keyless:
            raise LLMUnavailable("llm unavailable (forced down)")
        try:
            status = await self._redis.get(keys.llm_status())
        except RedisError as exc:
            logger.warning("redis unavailable for llm breaker check: %s", exc)
            return
        if _decode(status) == "down":
            raise LLMUnavailable("llm unavailable")

    async def _record(
        self, exc: Exception | None, slot: ModelSlot | None = None
    ) -> None:
        if self._settings.LLM_FORCE_DOWN:
            return
        # Phase 4 (§10.4): a 429 on the background slot is *our* limit, not
        # the provider failing — counting it would let bulk work trip the
        # breaker and take the chat down with it. 5xx and timeouts still do.
        if (
            slot == "bulk"
            and exc is not None
            and isinstance(exc, openai.RateLimitError)
        ):
            return
        try:
            probe_active = await self._redis.get(keys.llm_probe()) is not None
        except RedisError as redis_exc:
            logger.warning("redis unavailable for llm breaker record: %s", redis_exc)
            return

        if exc is None:
            if probe_active:
                try:
                    await self._redis.delete(keys.llm_probe())
                    await self._redis.delete(keys.llm_status())
                except RedisError as redis_exc:
                    logger.warning(
                        "redis unavailable for llm breaker record: %s", redis_exc
                    )
            return

        if not self._is_breaker_error(exc):
            return

        try:
            if probe_active:
                await self._redis.set(keys.llm_status(), "down", ex=120)
                await self._redis.set(keys.llm_probe(), "1")
                return

            count = await self._redis.incr(keys.llm_errors())
            if count == 1:
                await self._redis.expire(keys.llm_errors(), 60)
            if count >= 3:
                await self._redis.set(keys.llm_status(), "down", ex=120)
                await self._redis.set(keys.llm_probe(), "1")
                await self._redis.delete(keys.llm_errors())
        except RedisError as redis_exc:
            logger.warning("redis unavailable for llm breaker record: %s", redis_exc)

    async def _create_completion(self, slot: ModelSlot, kwargs: dict) -> Any:
        try:
            return await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            if slot == "bulk" and self._is_retryable_error(exc):
                await asyncio.sleep(1.0)
                return await self._client.chat.completions.create(**kwargs)
            raise

    def _completion_kwargs(
        self,
        messages: list[LLMMessage],
        slot: ModelSlot,
        *,
        stream: bool,
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
        temperature: float | None = None,
        response_format: dict | None = None,
    ) -> dict:
        kwargs: dict = {
            "model": self._model_for(slot),
            "messages": self._to_openai_messages(messages),
            "tools": tools,
            "stream": stream,
            "timeout": self._timeout_for(slot),
        }
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        if temperature is not None:
            kwargs["temperature"] = temperature
        if response_format is not None:
            kwargs["response_format"] = response_format
        reasoning_effort = self._reasoning_for(slot)
        if reasoning_effort is not None:
            kwargs["reasoning_effort"] = reasoning_effort
        thinking = self._thinking_for(slot)
        if thinking is not None:
            kwargs["extra_body"] = {"thinking": {"type": thinking}}
        return kwargs

    async def complete(
        self,
        messages: list[LLMMessage],
        slot: ModelSlot,
        tools: list[dict] | None = None,
        temperature: float | None = None,
        response_format: dict | None = None,
    ) -> LLMResult:
        await self._check_breaker()
        await self._acquire(slot)
        kwargs = self._completion_kwargs(
            messages,
            slot,
            stream=False,
            tools=tools,
            temperature=temperature,
            response_format=response_format,
        )
        try:
            response = await self._create_completion(slot, kwargs)
        except Exception as exc:
            await self._record(exc, slot)
            raise
        await self._record(None, slot)

        choice = response.choices[0]
        tool_calls: list[ToolCallOut] = []
        for call in choice.message.tool_calls or []:
            try:
                args = json.loads(call.function.arguments)
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    "malformed tool_call arguments: %s", call.function.arguments
                )
                args = {}
            tool_calls.append(
                ToolCallOut(call_id=call.id, name=call.function.name, args=args)
            )

        usage = self._usage_of(response)
        self._last_usage = usage

        return LLMResult(
            text=choice.message.content or "",
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=choice.finish_reason,
        )

    async def stream(
        self,
        messages: list[LLMMessage],
        slot: ModelSlot,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        await self._check_breaker()
        await self._acquire(slot)
        kwargs = self._completion_kwargs(messages, slot, stream=True, tools=tools)

        def build_tool_call(entry: dict[str, Any]) -> ToolCall:
            args = json.loads(entry["args"]) if entry["args"] else {}
            return ToolCall(
                type="tool_call",
                tool=entry["name"],
                args=args,
                call_id=entry["call_id"],
            )

        self._last_usage = None
        yielded_any = False
        retried = False
        pending: dict[int, dict[str, Any]]
        emitted: set[int]

        while True:
            pending = {}
            emitted = set()
            try:
                response = await self._client.chat.completions.create(**kwargs)
                async for chunk in response:
                    usage = getattr(chunk, "usage", None)
                    if usage is not None:
                        self._last_usage = LLMUsage(
                            prompt_tokens=usage.prompt_tokens,
                            completion_tokens=usage.completion_tokens,
                        )
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    delta = choice.delta
                    if delta.content:
                        yielded_any = True
                        yield TextDelta(type="text_delta", text=delta.content)
                    # Together (DeepSeek) sends the reasoning block as a separate
                    # delta field, not delta.content — discard it here so the
                    # internal monologue never reaches text_full/history/observer
                    # window (docs/tz/provider.md "Что не работает").
                    reasoning_chunk = getattr(
                        delta, "reasoning_content", None
                    ) or getattr(delta, "reasoning", None)
                    if reasoning_chunk:
                        pass
                    for tool_call_delta in delta.tool_calls or []:
                        index = tool_call_delta.index
                        entry = pending.setdefault(
                            index, {"call_id": None, "name": None, "args": ""}
                        )
                        if tool_call_delta.id:
                            entry["call_id"] = tool_call_delta.id
                        if tool_call_delta.function:
                            if tool_call_delta.function.name:
                                entry["name"] = tool_call_delta.function.name
                            if tool_call_delta.function.arguments:
                                entry["args"] += tool_call_delta.function.arguments
                    if choice.finish_reason == "tool_calls":
                        for index, entry in pending.items():
                            if index not in emitted:
                                yielded_any = True
                                yield build_tool_call(entry)
                                emitted.add(index)
            except Exception as exc:
                if (
                    not yielded_any
                    and not retried
                    and slot == "bulk"
                    and self._is_retryable_error(exc)
                ):
                    retried = True
                    await asyncio.sleep(1.0)
                    continue
                await self._record(exc, slot)
                raise
            break

        for index, entry in pending.items():
            if index not in emitted:
                yield build_tool_call(entry)
                emitted.add(index)

        await self._record(None, slot)

    def _response_format_for(self, schema: type[BaseModel]) -> dict:
        json_schema: dict = {
            "name": schema.__name__,
            "schema": schema.model_json_schema(),
        }
        if self._settings.LLM_STRICT_SCHEMA:
            json_schema["strict"] = True
        return {"type": "json_schema", "json_schema": json_schema}

    async def structured(
        self, messages: list[LLMMessage], schema: type[T], slot: ModelSlot
    ) -> T:
        mode = self._settings.LLM_STRUCTURED_MODE
        if mode == "tool":
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "emit",
                        "parameters": schema.model_json_schema(),
                    },
                }
            ]
            tool_choice = {"type": "function", "function": {"name": "emit"}}
            response_format = None
        elif mode == "json_object":
            tools = None
            tool_choice = None
            response_format = {"type": "json_object"}
            # The provider takes no schema here, only the word "json" and an example.
            messages = [
                *messages,
                LLMMessage(
                    role="system",
                    content=(
                        "Answer with one JSON object and nothing else, valid for this "
                        f"JSON schema: {json.dumps(schema.model_json_schema())}"
                    ),
                ),
            ]
        else:
            tools = None
            tool_choice = None
            response_format = self._response_format_for(schema)

        current_messages = list(messages)
        last_raw_text = ""

        for _attempt in range(2):
            await self._check_breaker()
            await self._acquire(slot)
            kwargs = self._completion_kwargs(
                current_messages,
                slot,
                stream=False,
                tools=tools,
                tool_choice=tool_choice,
                response_format=response_format,
            )
            try:
                response = await self._create_completion(slot, kwargs)
            except Exception as exc:
                await self._record(exc, slot)
                raise
            await self._record(None, slot)
            self._last_usage = self._usage_of(response)

            choice = response.choices[0]
            if mode == "tool":
                calls = choice.message.tool_calls or []
                raw_text = (
                    calls[0].function.arguments
                    if calls
                    else (choice.message.content or "")
                )
            else:
                raw_text = choice.message.content or ""

            parsed, error = parse_structured_output(raw_text, schema)
            if parsed is not None:
                return parsed

            last_raw_text = raw_text
            current_messages = [
                *current_messages,
                structured_validation_retry_message(error or ""),
            ]

        logger.warning("structured output failed: %s", last_raw_text[:200])
        raise LLMUnavailable("structured output failed")

    async def status(self) -> LLMStatus:
        if self._settings.LLM_FORCE_DOWN or self._keyless:
            return "down"
        try:
            status_value = _decode(await self._redis.get(keys.llm_status()))
            if status_value == "down":
                return "down"
            if await self._redis.get(keys.llm_probe()) is not None:
                return "degraded"
            if await self._redis.get(keys.llm_errors()) is not None:
                return "degraded"
            return "ok"
        except RedisError as exc:
            logger.warning("redis unavailable for llm status, reporting ok: %s", exc)
            return "ok"

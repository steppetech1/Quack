"""LLM provider test doubles (ТЗ Фазы 3 §8.1).

- `FakeLLMProvider`: queue of responses in call order (moved from `tests/domains/conftest.py`).
- `ScriptedLLMProvider`: response chosen by `schema_name`, not by order, because background
  jobs in e2e tests run concurrently; failure kinds follow the provider interface of WP5.7
  (seam S12).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import orjson
from pydantic import BaseModel

from quack.llm.errors import (
    ProviderBadRequestError,
    ProviderIncompleteError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
)
from quack.llm.provider import ProviderRequest, ProviderResponse

type QueuedLLMItem = dict[str, Any] | BaseModel | str | Exception


class FakeLLMProvider:
    """In-memory `LLMProvider` test double recording `ProviderRequest` calls and returning
    queued structured responses (`dict`, `BaseModel`, JSON `str`, or raising `Exception`).
    """

    def __init__(self, responses: Sequence[QueuedLLMItem] | None = None) -> None:
        self.responses: list[QueuedLLMItem] = list(responses or [])
        self.calls: list[ProviderRequest] = []

    def enqueue(self, *items: QueuedLLMItem) -> None:
        self.responses.extend(items)

    async def complete_structured(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        if not self.responses:
            raise AssertionError(
                f"FakeLLMProvider queue is empty for schema {request.schema_name!r}"
            )
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, BaseModel):
            content = item.model_dump_json()
        elif isinstance(item, dict):
            content = orjson.dumps(item).decode()
        else:
            content = item
        return ProviderResponse(
            content=content,
            input_tokens=100,
            output_tokens=40,
            cached_tokens=0,
            model=request.model,
        )


class UnavailableLLMProvider:
    """`LLMProvider` that is always down (`ProviderUnavailableError`): the default of
    `build_app_resources`, like a deployment without a reachable model API."""

    def __init__(self) -> None:
        self.calls: list[ProviderRequest] = []

    async def complete_structured(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        raise ProviderUnavailableError("test provider is unavailable")


type Responder = Callable[[ProviderRequest], QueuedLLMItem]


@dataclass
class ScriptedFailure:
    """A failure injected for calls matching `model` and/or `schema_name` (`None` = any).

    `kind` values (§8.1): `rate_limited` (with `retry_after`: seconds, HTTP date or `None`,
    sent as the raw `retry-after` header), `unavailable`, `bad_request`, `incomplete`,
    `invalid_json`, `commentary_only` (only a `phase: commentary` message, no answer),
    `ratelimit_headers` (a normal answer whose `x-ratelimit-remaining-*` headers are
    `remaining_share` of the limits). `times` = how many matching calls are affected.
    """

    kind: str
    model: str | None = None
    schema_name: str | None = None
    times: int = 1
    retry_after: str | None = None
    remaining_share: float | None = None


# Failure kinds of §8.1 over the provider interface of WP5.7 (seam S12).
SUPPORTED_FAILURES = frozenset(
    {
        "unavailable",
        "rate_limited",
        "bad_request",
        "incomplete",
        "invalid_json",
        "commentary_only",
        "ratelimit_headers",
    }
)
# Limits reported by `ratelimit_headers`; the remaining part is `remaining_share` of them.
SCRIPTED_REQUEST_LIMIT = 1000
SCRIPTED_TOKEN_LIMIT = 1_000_000


def _default_responses() -> dict[str, QueuedLLMItem]:
    """Valid, data-agnostic answers for the nine output schemas of Phase 2 (§8.1)."""
    return {
        "StructurerOutput": {
            "topics": [
                {"slug": "basics", "name": "Basics", "description": "Starting point"},
                {"slug": "next", "name": "Next steps", "description": "Builds on basics"},
            ],
            "skills": [
                {"slug": "basics-1", "topic_slug": "basics", "name": "Core idea"},
                {"slug": "next-1", "topic_slug": "next", "name": "Applying the idea"},
            ],
            "prereqs": [{"from_topic_slug": "basics", "to_topic_slug": "next"}],
            "skill_deps": [],
            "misconceptions": [],
        },
        "DiagnosticOutput": {
            "prompt_text": "Which option is right?",
            "options": ["A", "B", "C"],
            "correct_answer": "A",
            "is_correct": True,
            "score": 1.0,
            "tested_depth": 0.5,
        },
        "CuratorOutput": {
            "promotions": [],
            "skill_merges": [],
            "topic_merges": [],
            "misconception_merges": [],
        },
        "TeacherOutput": {
            "reply_text": "Let us practise this step by step.",
            "pedagogical_move": "assign_task",
        },
        "ExtractorOutput": {"observations": [], "misconceptions": []},
        "ExtractorOutputV2": {"entries": []},
        "TaskGenOutput": {
            "statement": "Pick the right option.",
            "options": [
                {"label": "A", "text": "Right", "correct": True, "catches": None},
                {"label": "B", "text": "Wrong", "correct": False, "catches": "slip"},
            ],
            "answer": {"type": "choice", "value": "A", "units": None},
            "solution": "A is right.",
            "hints": ["Read the options.", "Compare them.", "A is close."],
            "tests": [{"skill": "S1", "level": 1}],
            "method_named": True,
            "variant_of": None,
            "form": None,
            "pair_task": None,
        },
        "EvaluatorOutput": {
            "items": [],
            "verdict": "correct",
            "confidence": 1.0,
            "misconception": None,
            "note": "Correct.",
        },
        "ArtifactGenOutput": {"title": "Notes", "text": "# Notes\n\nScripted text."},
    }


def _retry_after_seconds(raw: str | None) -> float | None:
    """Seconds of a numeric `retry-after`; an HTTP date or no header gives `None`."""
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _failed_content(failure: ScriptedFailure) -> str:
    """Raise the provider error of `failure`, or return the broken answer text."""
    if failure.kind == "unavailable":
        raise ProviderUnavailableError("scripted: unavailable")
    if failure.kind == "rate_limited":
        headers = {} if failure.retry_after is None else {"retry-after": failure.retry_after}
        raise ProviderRateLimitedError(_retry_after_seconds(failure.retry_after), headers=headers)
    if failure.kind == "bad_request":
        raise ProviderBadRequestError('{"error": {"message": "scripted bad request"}}')
    if failure.kind == "incomplete":
        raise ProviderIncompleteError('{"reply_text": "cut')
    if failure.kind == "commentary_only":
        return ""
    return "{not json"


def _ratelimit_headers(remaining_share: float) -> dict[str, str]:
    return {
        "x-ratelimit-limit-requests": str(SCRIPTED_REQUEST_LIMIT),
        "x-ratelimit-remaining-requests": str(int(SCRIPTED_REQUEST_LIMIT * remaining_share)),
        "x-ratelimit-limit-tokens": str(SCRIPTED_TOKEN_LIMIT),
        "x-ratelimit-remaining-tokens": str(int(SCRIPTED_TOKEN_LIMIT * remaining_share)),
    }


def _content(item: QueuedLLMItem) -> str:
    if isinstance(item, Exception):
        raise item
    if isinstance(item, BaseModel):
        return item.model_dump_json()
    if isinstance(item, dict):
        return orjson.dumps(item).decode()
    return item


@dataclass
class Hold:
    arrived: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass
class ScriptedLLMProvider:
    """`LLMProvider` answering by `request.schema_name` with default responders for all
    output schemas, a call journal and failure injection by model or schema (§8.1).

    Background jobs in e2e tests run concurrently, so the answer never depends on the call
    order. A schema without a responder fails the test loudly."""

    responders: dict[str, Responder] = field(default_factory=dict)
    failures: list[ScriptedFailure] = field(default_factory=list)
    calls: list[ProviderRequest] = field(default_factory=list)
    gates: dict[str, Hold] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for schema_name, item in _default_responses().items():
            self.responders.setdefault(schema_name, lambda _request, item=item: item)

    def respond(self, schema_name: str, responder: Responder) -> None:
        """Override the default responder of one schema."""
        self.responders[schema_name] = responder

    def hold(self, schema_name: str) -> Hold:
        """Calls of `schema_name` wait until `release` is set; `arrived` is set when the first
        held call reaches the gate (race tests)."""
        gate = self.gates[schema_name] = Hold()
        return gate

    def fail(self, failure: ScriptedFailure) -> None:
        """Inject a failure for matching calls."""
        if failure.kind not in SUPPORTED_FAILURES:
            raise ValueError(f"unknown failure kind {failure.kind!r}")
        self.failures.append(failure)

    def calls_for(
        self, *, model: str | None = None, schema_name: str | None = None
    ) -> list[ProviderRequest]:
        """Journal of calls filtered by model and/or schema."""
        return [
            c
            for c in self.calls
            if (model is None or c.model == model)
            and (schema_name is None or c.schema_name == schema_name)
        ]

    def _take_failure(self, request: ProviderRequest) -> ScriptedFailure | None:
        for failure in self.failures:
            if failure.times <= 0:
                continue
            if failure.model not in (None, request.model):
                continue
            if failure.schema_name not in (None, request.schema_name):
                continue
            failure.times -= 1
            return failure
        return None

    async def complete_structured(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        gate = self.gates.get(request.schema_name)
        if gate is not None:
            gate.arrived.set()
            await gate.release.wait()
        failure = self._take_failure(request)
        headers: dict[str, str] = {}
        output_items: tuple[dict[str, Any], ...] = ()
        if failure is not None and failure.kind != "ratelimit_headers":
            content = _failed_content(failure)
            if failure.kind == "commentary_only":
                output_items = (
                    {
                        "type": "message",
                        "phase": "commentary",
                        "content": [{"type": "output_text", "text": "Thinking it over."}],
                    },
                )
        else:
            if failure is not None:
                headers = _ratelimit_headers(failure.remaining_share or 0.0)
            responder = self.responders.get(request.schema_name)
            if responder is None:
                raise AssertionError(
                    f"ScriptedLLMProvider: no responder for {request.schema_name!r}"
                )
            content = _content(responder(request))
        return ProviderResponse(
            content=content,
            input_tokens=100,
            output_tokens=40,
            cached_tokens=0,
            model=request.model,
            output_items=output_items,
            headers=headers,
        )

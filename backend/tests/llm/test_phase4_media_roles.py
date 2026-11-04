"""ТЗ Ф4 WP10.1 (третий пункт, D-B-187): the non-text roles `segmenter`, `voice`, `illustrator`
through the gateway — the request body, the parsed typed answer and the ledger row of each.
The provider is the real `ResponsesClient` over `httpx2.MockTransport` (no external service)."""

from __future__ import annotations

import base64
from typing import Any
from uuid import UUID, uuid4

import httpx2
import orjson

from quack.config import Settings
from quack.db import Db
from quack.llm.gateway import LLMGateway
from quack.llm.media import BoxPrompt
from quack.llm.provider import HttpLLMProvider, ImageInput
from quack.llm.responses_client import ResponsesClient

PNG = ImageInput("image/png", b"\x89PNG-photo")
SAM_TEXT = (
    "<0f><|box;x1=10;y1=20;x2=110;y2=220;w=640;h=480;c=0.91|>"
    "<|mask;x=0;y=0;c=0.91;data=220,110,~ab;c;d|>"
    "<|box;x1=5;y1=6;x2=7;y2=8;w=640;h=480;c=0.5|>"
)
PIXEL = base64.b64encode(b"png-bytes").decode()


def _answer(body: dict[str, Any]) -> httpx2.Response:
    return httpx2.Response(200, json=body, headers={"x-ratelimit-remaining-tokens": "900"})


class _Meta:
    """The Meta API double: routes by path, keeps what came in."""

    def __init__(self) -> None:
        self.seen: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.seen.append(request)
        path = request.url.path
        if path.endswith("/responses"):
            return _answer(
                {
                    "model": "sam-3.1",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": SAM_TEXT}],
                        }
                    ],
                    "usage": {"input_tokens": 300, "output_tokens": 12},
                }
            )
        if path.endswith("/asr/transcribe"):
            return _answer(
                {
                    "sessionId": "s1",
                    "transcript": "минус b плюс корень",
                    "audioDurationMs": 4200,
                    "turns": [
                        {
                            "turnId": "t1",
                            "startMs": 0,
                            "endMs": 4200,
                            "transcript": "x",
                            "speaker": None,
                        }
                    ],
                }
            )
        return _answer({"model": "muse-image-1.0", "data": [{"b64_json": PIXEL}]})


def _gateway(db: Db, meta: _Meta) -> LLMGateway:
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(meta))
    client = ResponsesClient("http://meta.test/v1", "key", http_client=http)
    provider = HttpLLMProvider("http://meta.test/v1", "key")
    provider._impl = client  # pyright: ignore[reportPrivateUsage]
    return LLMGateway(provider, Settings(env="test"), db=db)


async def _rows(db: Db, learner_id: UUID) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT role, model, prompt_version, status, input_tokens, output_tokens "
        "FROM ops.llm_usage WHERE learner_id = %(l)s ORDER BY id",
        {"l": learner_id},
    )


async def test_segmenter_request_parsed_answer_and_usage(pg_db: Db) -> None:
    meta, learner_id = _Meta(), uuid4()
    gw = _gateway(pg_db, meta)

    result = await gw.segment(
        PNG, learner_id=learner_id, box=BoxPrompt(1, 2, 30, 40, 640, 480), include_confidence=True
    )

    body = orjson.loads(meta.seen[0].content)
    assert body["model"] == "sam-3.1" and body["store"] is False
    parts = body["input"][0]["content"]
    assert parts[0] == {
        "type": "input_text",
        "text": "<0f>1<|box;x1=1;y1=2;x2=30;y2=40;w=640;h=480|>",
    }
    assert parts[1]["type"] == "input_image" and parts[1]["image_url"].startswith("data:image/png")
    assert body["metadata"] == {"mask_encoding": "lossless", "include_confidence": "true"}
    first, second = result.value.objects
    assert first.box is not None and (first.box.x2, first.box.y2) == (110, 220)
    assert first.mask is not None and (first.mask.height, first.mask.width) == (220, 110)
    assert (first.mask.marker, first.mask.data, first.confidence) == ("~", "ab;c;d", 0.91)
    assert second.mask is None and second.index == 1
    [row] = await _rows(pg_db, learner_id)
    assert (row["role"], row["model"], row["prompt_version"], row["status"]) == (
        "segmenter",
        "sam-3.1",
        "segmenter@0",
        "ok",
    )
    assert (row["input_tokens"], row["output_tokens"]) == (300, 12)


async def test_voice_request_parsed_answer_and_usage(pg_db: Db) -> None:
    meta, learner_id = _Meta(), uuid4()
    gw = _gateway(pg_db, meta)

    result = await gw.transcribe(b"RIFF-wav", learner_id=learner_id, language_bias="ru")

    [request] = meta.seen
    assert request.url.path == "/v1/asr/transcribe"
    assert request.headers["authorization"] == "Bearer key"
    assert request.headers["content-type"].startswith("multipart/form-data")
    assert b'"audioEncoding":"WAV"' in request.content and b"RIFF-wav" in request.content
    assert b'"model":"muse-voice-transcribe-1.0"' in request.content
    assert result.value.text == "минус b плюс корень" and result.value.audio_duration_ms == 4200
    assert [turn.turn_id for turn in result.value.turns] == ["t1"]
    [row] = await _rows(pg_db, learner_id)
    assert (row["role"], row["model"], row["status"]) == (
        "voice",
        "muse-voice-transcribe-1.0",
        "ok",
    )


async def test_illustrator_request_parsed_answer_and_usage(pg_db: Db) -> None:
    meta, learner_id = _Meta(), uuid4()
    gw = _gateway(pg_db, meta)

    result = await gw.illustrate(
        "a river delta, watercolor", learner_id=learner_id, size="1024x1024"
    )

    [request] = meta.seen
    assert request.url.path == "/v1/images/generations"
    assert orjson.loads(request.content) == {
        "model": "muse-image-1.0",
        "prompt": "a river delta, watercolor",
        "n": 1,
        "response_format": "b64_json",
        "size": "1024x1024",
    }
    assert result.value.image == b"png-bytes" and result.value.url is None
    [row] = await _rows(pg_db, learner_id)
    assert (row["role"], row["model"], row["status"]) == ("illustrator", "muse-image-1.0", "ok")

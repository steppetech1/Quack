"""Non-text roles of the gateway (ТЗ Ф4 WP10.1, third bullet; D-B-187): `segmenter`
(`sam-3.1`), `voice` (`muse-voice-transcribe-1.0`), `illustrator` (`muse-image-1.0`).

Requests and typed results of the provider seam `MediaProvider`, and the parser of the
special-token answer of `sam-3.1` (WP0 п.3д, `PHASE4_NOTES.md`). The forms of the requests come
from the Meta documentation read on 2 Nov 2026 without a call; every field marked
[ПРОВЕРИТЬ на стенде] is checked by the manual run of DoD п.6a.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from quack.llm.provider import ImageInput, ProviderResponse

# `sam-3.1` returns up to 16 objects per request (documentation).
MAX_SEGMENTS = 16

type MaskEncoding = Literal["lossless", "one_bit"]
type TranscribeMode = Literal["PUSH_TO_TALK", "ENDPOINTING", "DIARIZATION"]


# --- typed results (public, returned by the gateway) ---


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class SegmentBoxOut(_Frozen):
    """Box of an object, absolute pixels of the photo."""

    x1: float
    y1: float
    x2: float
    y2: float


class SegmentMask(_Frozen):
    """Mask as `sam-3.1` returns it: `data` is the base85 payload of `height` x `width`;
    `marker` is the character before the payload (`~` or `!`, empty if none); lossless or
    one-bit is the request's `mask_encoding`. The payload is not decoded in the gateway:
    the decoding is checked on the stand [ПРОВЕРИТЬ на стенде]."""

    height: int
    width: int
    marker: str
    data: str
    x: int = 0
    y: int = 0


class SegmentedObject(_Frozen):
    frame: int
    index: int
    box: SegmentBoxOut | None
    mask: SegmentMask | None
    confidence: float | None


class Segmentation(_Frozen):
    """Objects found on the image (empty: no match); a photo is one frame (`frame` 0)."""

    objects: list[SegmentedObject]
    mask_encoding: MaskEncoding


class TranscriptTurn(_Frozen):
    turn_id: str
    start_ms: int
    end_ms: int
    text: str
    speaker: str | None = None


class Transcription(_Frozen):
    """`text` — the transcript; `turns` is empty in `PUSH_TO_TALK`."""

    text: str
    audio_duration_ms: int
    turns: list[TranscriptTurn]


class Illustration(_Frozen):
    """`image` — bytes of the picture, or `url` — a reference (the provider chooses)."""

    image: bytes | None
    url: str | None
    mime: str


# --- requests and responses of the provider seam ---


@dataclass(frozen=True)
class BoxPrompt:
    """A box of the photo in absolute pixels plus the size of the image: the input token is
    `<0f>1<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..|>`; `w`/`h` are taken as the image size
    [ПРОВЕРИТЬ на стенде]."""

    x1: int
    y1: int
    x2: int
    y2: int
    image_width: int
    image_height: int


@dataclass(frozen=True)
class SegmentRequest:
    """`POST /responses` of `sam-3.1`: a short name of the object **or** a box, never both."""

    model: str
    image: ImageInput
    timeout_sec: float
    concept: str | None = None
    box: BoxPrompt | None = None
    mask_encoding: MaskEncoding = "lossless"
    include_confidence: bool = True


@dataclass(frozen=True)
class TranscribeRequest:
    """`POST /asr/transcribe`, multipart: `request` (JSON) and `audio` (WAV, mono, 16-bit PCM,
    16 or 24 kHz, up to 10 minutes and 32 MB)."""

    model: str
    wav: bytes
    timeout_sec: float
    mode: TranscribeMode = "PUSH_TO_TALK"
    language_bias: str | None = None
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class IllustrationRequest:
    """`POST /images/generations` of `muse-image-1.0` [ПРОВЕРИТЬ на стенде]: the documents give
    no form of this call, the nearest rule is the OpenAI-compatible images endpoint."""

    model: str
    prompt: str
    timeout_sec: float
    size: str | None = None


@dataclass(frozen=True)
class TranscribeResponse(ProviderResponse):
    """Transcript answer: `content` is the transcript. The API reports no tokens; the audio
    length is in `audio_duration_ms`."""

    audio_duration_ms: int = 0
    turns: tuple[TranscriptTurn, ...] = ()


@dataclass(frozen=True)
class ImageResponse(ProviderResponse):
    """Image answer: bytes (`b64_json`) or a reference (`url`), at least one of them."""

    image: bytes | None = None
    url: str | None = None
    mime: str = "image/png"


class MediaProvider(Protocol):
    """The provider seam of the non-text roles; implemented by `ResponsesClient`. Errors are
    those of `LLMProvider` (429, 400, 5xx/timeout/connection, incomplete)."""

    async def segment(self, request: SegmentRequest) -> ProviderResponse: ...

    async def transcribe(self, request: TranscribeRequest) -> TranscribeResponse: ...

    async def illustrate(self, request: IllustrationRequest) -> ImageResponse: ...


# --- parser of the `sam-3.1` answer ---

_TOKENS = re.compile(r"<(\d+)f>|<\|(box|mask);(.*?)\|>", re.DOTALL)
_BOX_FIELDS = ("x1", "y1", "x2", "y2", "w", "h", "c")


def _fields(body: str, positional: Sequence[str]) -> dict[str, str]:
    """`key=value;key=value` or positional `v;v;v` (order of `positional`)."""
    out: dict[str, str] = {}
    free = iter(positional)
    for item in body.split(";"):
        if "=" in item:
            key, _, value = item.partition("=")
            out[key.strip()] = value.strip()
        elif item.strip():
            name = next(free, None)
            if name is not None:
                out[name] = item.strip()
    return out


def _number(value: str | None) -> float | None:
    try:
        return None if value is None else float(value)
    except ValueError:
        return None


def _box(body: str) -> tuple[SegmentBoxOut | None, float | None]:
    fields = _fields(body, _BOX_FIELDS)
    corners = [_number(fields.get(key)) for key in ("x1", "y1", "x2", "y2")]
    confidence = _number(fields.get("c"))
    if any(value is None for value in corners):
        return None, confidence
    x1, y1, x2, y2 = (float(value) for value in corners if value is not None)
    return SegmentBoxOut(x1=x1, y1=y1, x2=x2, y2=y2), confidence


def _mask(body: str) -> tuple[SegmentMask | None, float | None]:
    # The payload may contain `;`, so the header is cut before `data=`.
    head, sep, data = body.partition("data=")
    if not sep:
        return None, None
    fields = _fields(head.rstrip(";"), ())
    height, _, rest = data.partition(",")
    width, _, payload = rest.partition(",")
    try:
        size = (int(height), int(width))
    except ValueError:
        return None, None
    marker = payload[:1] if payload[:1] in ("~", "!") else ""
    mask = SegmentMask(
        height=size[0],
        width=size[1],
        marker=marker,
        data=payload[len(marker) :],
        x=int(_number(fields.get("x")) or 0),
        y=int(_number(fields.get("y")) or 0),
    )
    return mask, _number(fields.get("c"))


def parse_segmentation(text: str, mask_encoding: MaskEncoding = "lossless") -> Segmentation:
    """Special tokens of `output_text` → objects. A `box` token starts an object, the `mask`
    token after it belongs to it; a mask without a preceding box is an object without a box.
    Frame markers `<Nf>` set `frame`. Empty text — no objects. Unknown fragments are skipped."""
    objects: list[SegmentedObject] = []
    frame = 0
    current: dict[str, object] | None = None
    counts: dict[int, int] = {}

    def flush() -> None:
        nonlocal current
        if current is not None and len(objects) < MAX_SEGMENTS:
            objects.append(SegmentedObject.model_validate(current))
        current = None

    def start() -> dict[str, object]:
        index = counts.get(frame, 0)
        counts[frame] = index + 1
        return {"frame": frame, "index": index, "box": None, "mask": None, "confidence": None}

    for token in _TOKENS.finditer(text):
        if token.group(1) is not None:
            flush()
            frame = int(token.group(1))
        elif token.group(2) == "box":
            flush()
            current = start()
            current["box"], current["confidence"] = _box(token.group(3))
        else:
            if current is None or current["mask"] is not None:
                flush()
                current = start()
            current["mask"], confidence = _mask(token.group(3))
            if current["confidence"] is None:
                current["confidence"] = confidence
    flush()
    return Segmentation(objects=objects, mask_encoding=mask_encoding)


def box_input_tokens(box: BoxPrompt) -> str:
    """The `input_text` of a request by box (`<0f>1<|box;…|>`, WP0 п.3д)."""
    return (
        f"<0f>1<|box;x1={box.x1};y1={box.y1};x2={box.x2};y2={box.y2};"
        f"w={box.image_width};h={box.image_height}|>"
    )

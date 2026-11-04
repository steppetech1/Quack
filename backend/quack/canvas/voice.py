"""Voice by segments (ТЗ Ф4 WP21.6; Т-§22.8, П-14.1; D-C-152).

The browser records an utterance and sends the segment with its start and end by the device
clock. The segment is transcribed by the role `voice` (`muse-voice-transcribe-1.0`, a plain
request) and recorded as `voice.transcribed {text, started_at_ms, ended_at_ms}`; the link with
the lines of the canvas is made at reading, by this time (`voice_at_line`, WP14.4). The audio
is handled in memory and written nowhere: not to the store, not to the log.

The model takes a WAV: mono, 16-bit PCM, 16 or 24 kHz. A decoder of the compressed audio of a
browser (WebM, Ogg, MP4) is a dependency outside ТЗ Ф4 §4.6, so the client sends WAV (it has the
PCM of the microphone) and the server only brings any PCM WAV to the form the model takes with
the standard library: stereo is averaged, 8, 24 and 32 bits become 16, a rate other than 16 or
24 kHz becomes 16 kHz. Anything else is `UploadRejected` (415) saying so.
"""

from __future__ import annotations

import asyncio
import io
import sys
import wave
from array import array
from dataclasses import dataclass
from itertools import accumulate
from uuid import UUID

from redis.asyncio import Redis

from quack.db import Db
from quack.events.event_types import VoiceTranscribed
from quack.events.log import NewEvent, append_events
from quack.llm.gateway import LLMGateway
from quack.storage.uploads import UploadRejected

# Limits of the endpoint of the model (documentation, WP0 п.3д), not tunable parameters.
MAX_AUDIO_S = 600
MODEL_RATES = (16000, 24000)
FALLBACK_RATE = 16000

_PCM, _EXTENSIBLE = 1, 0xFFFE
_WAY_OUT = "audio must be a WAV: PCM, mono or stereo; convert WebM, Ogg or MP4 on the device"

_SESSION = (
    "SELECT 1 AS found FROM core.events WHERE learner_id = %(learner_id)s "
    "AND type = 'session.started' AND payload ->> 'session_id' = %(session_id)s LIMIT 1"
)
_RECORDED = (
    "SELECT seq, payload FROM core.events WHERE learner_id = %(learner_id)s "
    "AND idempotency_key = %(key)s"
)


class SessionNotFound(Exception):
    """No such session of this learner: 404."""


class InvalidSegment(ValueError):
    """The segment ends before it starts: 422."""


@dataclass(frozen=True)
class Voice:
    """The answer for a segment; `seq` is `None` when nothing was heard (no event)."""

    text: str
    started_at_ms: int
    ended_at_ms: int
    seq: int | None


def _reject(message: str) -> UploadRejected:
    return UploadRejected("type", message)


def normalize_wav(data: bytes) -> bytes:
    """Any PCM WAV → mono, 16-bit, 16 or 24 kHz. `UploadRejected`: not a PCM WAV or empty
    (415), longer than `MAX_AUDIO_S` (413)."""
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise _reject(_WAY_OUT)
    if int.from_bytes(data[20:22], "little") not in (_PCM, _EXTENSIBLE):
        raise _reject(f"{_WAY_OUT}; float samples are not taken")
    try:
        with wave.open(io.BytesIO(data), "rb") as source:
            channels, width, rate = (
                source.getnchannels(),
                source.getsampwidth(),
                source.getframerate(),
            )
            frames = source.getnframes()
            if frames == 0 or rate <= 0:
                raise _reject("the audio is empty")
            if frames / rate > MAX_AUDIO_S:
                raise UploadRejected("size", f"audio is longer than {MAX_AUDIO_S} s")
            raw = source.readframes(frames)
    except (wave.Error, EOFError) as exc:
        raise _reject(f"{_WAY_OUT} ({exc})") from exc
    if width not in (1, 2, 3, 4):
        raise _reject(f"{_WAY_OUT}; {width * 8}-bit samples are not taken")
    mono = _downmix(_samples16(raw, width), channels)
    out_rate = rate if rate in MODEL_RATES else FALLBACK_RATE
    if out_rate != rate:
        mono = _resample(mono, rate, out_rate)
    if sys.byteorder == "big":
        mono.byteswap()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(out_rate)
        out.writeframes(mono.tobytes())
    return buffer.getvalue()


def _samples16(raw: bytes, width: int) -> array[int]:
    """Little-endian PCM of `width` bytes → native 16-bit samples."""
    if width == 1:  # unsigned
        return array("h", [(byte - 128) << 8 for byte in raw])
    if width == 2:
        samples = array("h")
        samples.frombytes(raw[: len(raw) // 2 * 2])
        if sys.byteorder == "big":
            samples.byteswap()
        return samples
    if width == 3:  # widen to 32 bits with a zero low byte
        count = len(raw) // 3
        wide = bytearray(count * 4)
        trimmed = raw[: count * 3]
        wide[1::4], wide[2::4], wide[3::4] = trimmed[0::3], trimmed[1::3], trimmed[2::3]
        raw = bytes(wide)
    samples32 = array("i")
    samples32.frombytes(raw[: len(raw) // 4 * 4])
    if sys.byteorder == "big":
        samples32.byteswap()
    return array("h", [sample >> 16 for sample in samples32])


def _downmix(samples: array[int], channels: int) -> array[int]:
    if channels == 1:
        return samples
    usable = len(samples) // channels * channels
    columns = [samples[k:usable:channels] for k in range(channels)]
    return array("h", [sum(frame) // channels for frame in zip(*columns, strict=True)])


def _resample(samples: array[int], rate: int, target: int) -> array[int]:
    """Down: the mean of the window of the old samples under one new sample (a plain low-pass
    before decimation); up: linear interpolation."""
    count = len(samples) * target // rate
    ratio = rate / target
    if rate > target:
        total = [0, *accumulate(samples)]
        out: list[int] = []
        for i in range(count):
            start = int(i * ratio)
            end = max(start + 1, min(int((i + 1) * ratio), len(samples)))
            out.append((total[end] - total[start]) // (end - start))
        return array("h", out)
    last = len(samples) - 1
    out = []
    for i in range(count):
        position = i * ratio
        left = int(position)
        right = min(left + 1, last)
        out.append(round(samples[left] + (samples[right] - samples[left]) * (position - left)))
    return array("h", out)


async def transcribe_segment(
    db: Db,
    llm: LLMGateway,
    *,
    learner_id: UUID,
    session_id: UUID,
    audio: bytes,
    started_at_ms: int,
    ended_at_ms: int,
    redis: Redis | None = None,
) -> Voice:
    """`SessionNotFound`, `InvalidSegment`, `UploadRejected` (the audio), and the errors of the
    gateway (the model is down or over budget: nothing is written). The key of the event is the
    session and the time of the segment (law 5): the same segment again gives the recorded
    answer and calls nothing."""
    found = await db.fetch_one(_SESSION, {"learner_id": learner_id, "session_id": str(session_id)})
    if found is None:
        raise SessionNotFound(f"session {session_id} not found")
    if ended_at_ms < started_at_ms:
        raise InvalidSegment("the segment ends before it starts")
    key = f"voice.transcribed:{session_id}:{started_at_ms}:{ended_at_ms}"
    recorded = await db.fetch_one(_RECORDED, {"learner_id": learner_id, "key": key})
    if recorded is not None:
        text = str(recorded["payload"]["text"])
        return Voice(text, started_at_ms, ended_at_ms, int(recorded["seq"]))
    wav = await asyncio.to_thread(normalize_wav, audio)
    heard = await llm.transcribe(wav, learner_id=learner_id, session_id=session_id)
    text = heard.value.text.strip()
    if not text:
        return Voice("", started_at_ms, ended_at_ms, None)
    event = NewEvent(
        type="voice.transcribed",
        payload=VoiceTranscribed(text=text, started_at_ms=started_at_ms, ended_at_ms=ended_at_ms),
        actor="learner",
        idempotency_key=key,
        session_id=session_id,
    )
    (stored,) = await append_events(db, learner_id, [event], redis=redis)
    return Voice(text, started_at_ms, ended_at_ms, stored.seq)

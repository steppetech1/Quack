"""WP21.6: the voice by segments (ТЗ Ф4 WP21.6; Т-§22.8, П-14.1; D-C-152; `T-CV7`).

`POST /v1/sessions/{id}/voice`: a WAV segment and its start and end by the device clock →
role `voice` → `voice.transcribed {text, started_at_ms, ended_at_ms}`. The audio lives in memory
only. The WAV is brought to what `muse-voice-transcribe-1.0` takes (mono, 16-bit, 16 or 24 kHz)
by the standard library; the compressed audio of a browser (WebM, Ogg, MP4) is converted to
WAV by the client — a decoder would be a dependency outside ТЗ Ф4 §4.6.
"""

from __future__ import annotations

import io
import math
import struct
import wave
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from quack.api.app import create_app
from quack.api.auth import SESSION_COOKIE
from quack.auth.sessions import open_session
from quack.canvas.voice import MAX_AUDIO_S, normalize_wav
from quack.config import Settings
from quack.db import Db
from quack.events.log import NewEvent, append_events
from quack.llm.errors import ProviderUnavailableError
from quack.storage.uploads import UploadRejected
from tests.db.fixtures import create_learner
from tests.support.llm import ScriptedLLMProvider
from tests.support.resources import build_app_resources
from tests.support.storage import InMemoryObjectStore


def make_wav(
    samples: list[list[int]],
    *,
    rate: int = 16000,
    width: int = 2,
) -> bytes:
    """A PCM WAV; `samples[i]` is the frame `i` (one value per channel), in the range of the
    16-bit samples, scaled to `width` bytes."""
    channels = len(samples[0])
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(width)
        out.setframerate(rate)
        frames = bytearray()
        for frame in samples:
            for value in frame:
                if width == 1:
                    frames += bytes([(value >> 8) + 128])
                elif width == 2:
                    frames += struct.pack("<h", value)
                elif width == 3:
                    frames += (value << 8).to_bytes(3, "little", signed=True)
                else:
                    frames += struct.pack("<i", value << 16)
        out.writeframes(bytes(frames))
    return buffer.getvalue()


def read_wav(data: bytes) -> tuple[int, int, int, list[int]]:
    """Channels, sample width, rate and the 16-bit samples of a WAV."""
    with wave.open(io.BytesIO(data), "rb") as source:
        raw = source.readframes(source.getnframes())
        shape = (source.getnchannels(), source.getsampwidth(), source.getframerate())
    return (*shape, list(struct.unpack(f"<{len(raw) // 2}h", raw)))


def sine(count: int, rate: int, hz: float = 440.0) -> list[int]:
    return [round(8000 * math.sin(2 * math.pi * hz * i / rate)) for i in range(count)]


def test_a_wav_the_model_takes_is_kept() -> None:
    wav = make_wav([[v] for v in sine(1600, 16000)])
    assert read_wav(normalize_wav(wav)) == (1, 2, 16000, sine(1600, 16000))
    wav24 = make_wav([[v] for v in sine(2400, 24000)], rate=24000)
    assert read_wav(normalize_wav(wav24))[:3] == (1, 2, 24000)


def test_stereo_is_averaged_to_mono() -> None:
    wav = make_wav([[100, 300], [-100, -300], [7, 8]])
    channels, width, rate, samples = read_wav(normalize_wav(wav))
    assert (channels, width, rate) == (1, 2, 16000)
    assert samples == [200, -200, 7]  # the mean rounds down


@pytest.mark.parametrize("width", [1, 3, 4])
def test_other_sample_widths_become_16_bit(width: int) -> None:
    values = [4096, -4096, 0, 8192]
    wav = make_wav([[v] for v in values], width=width)
    channels, out_width, rate, samples = read_wav(normalize_wav(wav))
    assert (channels, out_width, rate) == (1, 2, 16000)
    assert samples == values


@pytest.mark.parametrize("rate", [44100, 48000, 8000])
def test_other_rates_are_resampled_to_16_khz(rate: int) -> None:
    seconds = 0.5
    wav = make_wav([[v] for v in sine(int(rate * seconds), rate)], rate=rate)
    _, _, out_rate, samples = read_wav(normalize_wav(wav))
    assert out_rate == 16000
    assert abs(len(samples) - 8000) <= 1
    # A 440 Hz tone stays a tone: the peak of the amplitude survives.
    assert 7000 < max(samples) <= 8000


@pytest.mark.parametrize(
    "head",
    [
        b"\x1a\x45\xdf\xa3" + b"\x00" * 40,  # WebM / Matroska
        b"OggS" + b"\x00" * 40,  # Ogg
        b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 40,  # MP4
        b"ID3" + b"\x00" * 40,  # MP3
        b"not audio at all",
    ],
)
def test_compressed_audio_is_rejected_as_415_with_the_way_out(head: bytes) -> None:
    with pytest.raises(UploadRejected) as raised:
        normalize_wav(head)
    assert raised.value.reason == "type" and raised.value.status_code == 415
    assert "WAV" in str(raised.value)


def test_float_wav_and_an_empty_wav_are_rejected() -> None:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
    with pytest.raises(UploadRejected) as empty:
        normalize_wav(buffer.getvalue())
    assert empty.value.reason == "type"
    pcm = make_wav([[1], [2]])
    float_header = pcm[:20] + struct.pack("<H", 3) + pcm[22:]  # format tag 3 = IEEE float
    with pytest.raises(UploadRejected) as floating:
        normalize_wav(float_header)
    assert floating.value.reason == "type"


def test_a_segment_longer_than_the_model_takes_is_413() -> None:
    frames = (MAX_AUDIO_S + 1) * 100  # 100 Hz is enough for the header to say how long it is
    wav = make_wav([[0]] * frames, rate=100)
    with pytest.raises(UploadRejected) as raised:
        normalize_wav(wav)
    assert raised.value.reason == "size" and raised.value.status_code == 413


# --- the route (no external service: the provider is the scripted double) ---


WAV = make_wav([[v, v] for v in sine(4800, 48000)], rate=48000)
SPOKEN = "минус b плюс корень"


async def _session(db: Db, learner: UUID) -> UUID:
    session_id = uuid4()
    started = NewEvent(
        type="session.started",
        payload={"session_id": str(session_id), "domain_id": str(uuid4()), "intent": "learn"},
        actor="learner",
        idempotency_key=str(uuid4()),
    )
    await append_events(db, learner, [started])
    return session_id


async def _cookie(db: Db, settings: Settings, learner: UUID, **scope: object) -> dict[str, str]:
    token = await open_session(
        db,
        settings,
        learner_id=learner,
        now=datetime.now(UTC),
        **scope,  # type: ignore[arg-type]
    )
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


@pytest.fixture
def store() -> InMemoryObjectStore:
    return InMemoryObjectStore()


@pytest.fixture
def provider() -> ScriptedLLMProvider:
    return ScriptedLLMProvider(transcript=SPOKEN)


@pytest.fixture
async def client(
    pg_db: Db,
    settings: Settings,
    store: InMemoryObjectStore,
    provider: ScriptedLLMProvider,
) -> AsyncIterator[httpx.AsyncClient]:
    resources = build_app_resources(
        settings, None, None,
        db=pg_db, provider=provider, storage=store,
    )  # fmt: skip
    app = create_app(settings, resources=resources)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as test_client:
        yield test_client


def _form(start: int = 1000, end: int = 5200) -> dict[str, str]:
    return {"started_at_ms": str(start), "ended_at_ms": str(end)}


async def _voice_events(db: Db, learner: UUID) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT payload, session_id, actor FROM core.events WHERE learner_id = %(learner_id)s "
        "AND type = 'voice.transcribed' ORDER BY seq",
        {"learner_id": learner},
    )
    return list(rows)


async def test_voice_segment_not_stored(
    pg_db: Db,
    settings: Settings,
    client: httpx.AsyncClient,
    store: InMemoryObjectStore,
    provider: ScriptedLLMProvider,
) -> None:
    """T-CV7: the segment is transcribed by the role `voice` and recorded as
    `voice.transcribed` with its time; the model gets the WAV of the form it takes; the audio is
    kept nowhere — the store is empty and the event holds the text and the time only."""
    learner = await create_learner(pg_db)
    session_id = await _session(pg_db, learner)
    laptop = await _cookie(pg_db, settings, learner)

    answer = await client.post(
        f"/v1/sessions/{session_id}/voice",
        data=_form(),
        files={"audio": ("segment.wav", WAV, "audio/wav")},
        headers=laptop,
    )
    assert answer.status_code == 201, answer.text
    body = answer.json()
    assert body["text"] == SPOKEN and body["started_at_ms"] == 1000
    assert body["ended_at_ms"] == 5200 and isinstance(body["seq"], int)

    (call,) = provider.transcribe_calls
    assert call.model == "muse-voice-transcribe-1.0" and call.mode == "PUSH_TO_TALK"
    channels, width, rate, samples = read_wav(call.wav)
    assert (channels, width, rate) == (1, 2, 16000) and len(samples) == 1600

    (event,) = await _voice_events(pg_db, learner)
    assert event["payload"] == {"text": SPOKEN, "started_at_ms": 1000, "ended_at_ms": 5200}
    assert event["session_id"] == session_id and event["actor"] == "learner"
    assert store.objects == {}
    stored = await pg_db.fetch_all(
        "SELECT 1 AS found FROM core.events WHERE learner_id = %(learner_id)s "
        "AND payload::text LIKE %(riff)s",
        {"learner_id": learner, "riff": "%RIFF%"},
    )
    assert stored == []


async def test_the_same_segment_again_costs_nothing(
    pg_db: Db, settings: Settings, client: httpx.AsyncClient, provider: ScriptedLLMProvider
) -> None:
    """Law 5: the key is the session and the time of the segment; a retry gives the recorded
    answer without a second call of the model."""
    learner = await create_learner(pg_db)
    session_id = await _session(pg_db, learner)
    laptop = await _cookie(pg_db, settings, learner)
    path = f"/v1/sessions/{session_id}/voice"

    async def send(**form: str) -> httpx.Response:
        return await client.post(
            path, data=form, files={"audio": ("s.wav", WAV, "audio/wav")}, headers=laptop
        )

    first = await send(**_form())
    provider.transcript = "something else"
    again = await send(**_form())
    assert again.status_code == 201 and again.json() == first.json()
    assert len(provider.transcribe_calls) == 1
    other = await send(**_form(6000, 7000))
    assert other.json()["text"] == "something else" and len(provider.transcribe_calls) == 2
    assert len(await _voice_events(pg_db, learner)) == 2


async def test_silence_writes_no_event(
    pg_db: Db, settings: Settings, client: httpx.AsyncClient, provider: ScriptedLLMProvider
) -> None:
    learner = await create_learner(pg_db)
    session_id = await _session(pg_db, learner)
    provider.transcript = "  "
    answer = await client.post(
        f"/v1/sessions/{session_id}/voice",
        data=_form(),
        files={"audio": ("s.wav", WAV, "audio/wav")},
        headers=await _cookie(pg_db, settings, learner),
    )
    assert answer.status_code == 201
    assert answer.json() == {
        "text": "", "started_at_ms": 1000, "ended_at_ms": 5200, "seq": None,
    }  # fmt: skip
    assert await _voice_events(pg_db, learner) == []


async def test_voice_refusals_leave_no_trace(
    pg_db: Db, settings: Settings, client: httpx.AsyncClient, provider: ScriptedLLMProvider
) -> None:
    """Compressed audio — 415 `UPLOAD_REJECTED` before any call; the end before the start —
    422; a session of another learner — 404; a companion — 403 (T-CP1); the model down — 503
    `LLM_DEGRADED`. No refusal writes an event."""
    learner = await create_learner(pg_db)
    stranger = await create_learner(pg_db)
    session_id = await _session(pg_db, learner)
    laptop = await _cookie(pg_db, settings, learner)
    path = f"/v1/sessions/{session_id}/voice"
    audio = {"audio": ("s.wav", WAV, "audio/wav")}

    webm = await client.post(
        path,
        data=_form(),
        files={"audio": ("s.webm", b"\x1a\x45\xdf\xa3" + b"\x00" * 64, "audio/webm")},
        headers=laptop,
    )
    assert webm.status_code == 415 and webm.json()["error"]["code"] == "UPLOAD_REJECTED"

    backwards = await client.post(path, data=_form(5000, 4000), files=audio, headers=laptop)
    assert backwards.status_code == 422
    assert backwards.json()["error"]["code"] == "VALIDATION_ERROR"

    foreign = await client.post(
        path, data=_form(), files=audio, headers=await _cookie(pg_db, settings, stranger)
    )
    assert foreign.status_code == 404 and foreign.json()["error"]["code"] == "NOT_FOUND"

    scope = {"scope": "companion", "canvas_id": uuid4()}
    paired = await _cookie(pg_db, settings, learner, **scope)
    companion = await client.post(path, data=_form(), files=audio, headers=paired)
    assert companion.status_code == 403

    provider.media_error = ProviderUnavailableError("down")
    down = await client.post(path, data=_form(), files=audio, headers=laptop)
    assert down.status_code == 503 and down.json()["error"]["code"] == "LLM_DEGRADED"

    assert await _voice_events(pg_db, learner) == []
    assert await _voice_events(pg_db, stranger) == []


async def test_voice_over_the_upload_limit_is_413_before_any_call(
    pg_db: Db, settings: Settings, store: InMemoryObjectStore, provider: ScriptedLLMProvider
) -> None:
    """Т-§19.8: a segment larger than `upload_max_mb` is 413 `UPLOAD_REJECTED`, as a photo, and
    is never decoded or sent to the model; no event is written."""
    limited = settings.model_copy(update={"upload_max_mb": 1})
    resources = build_app_resources(
        limited, None, None,
        db=pg_db, provider=provider, storage=store,
    )  # fmt: skip
    app = create_app(limited, resources=resources)
    learner = await create_learner(pg_db)
    session_id = await _session(pg_db, learner)
    laptop = await _cookie(pg_db, limited, learner)
    big = WAV + b"\x00" * (1024 * 1024)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as test_client:
        answer = await test_client.post(
            f"/v1/sessions/{session_id}/voice",
            data=_form(),
            files={"audio": ("s.wav", big, "audio/wav")},
            headers=laptop,
        )
    assert answer.status_code == 413 and answer.json()["error"]["code"] == "UPLOAD_REJECTED"
    assert provider.transcribe_calls == []
    assert await _voice_events(pg_db, learner) == []

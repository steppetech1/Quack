"""WP19: object storage and uploads (ТЗ Ф4 WP19, §8.11 `T-ST1`–`T-ST3`; Т-§3.3, Т-§19.8,
Т-§20.7, Д-§8.4)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from quack.api.errors import install_error_handlers
from quack.storage.keys import (
    canvas_chunk_key,
    export_key,
    material_original_key,
    material_page_key,
    new_ulid,
    photo_crop_key,
    photo_original_key,
    thumbnail_key,
    viz_key,
)
from quack.storage.objects import S3ObjectStore, StorageUnavailable
from quack.storage.uploads import UploadRejected, detect_upload

ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")

PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n"
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 "
EXE = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00"


# --- T-ST1: keys ---------------------------------------------------------------------------


def test_ulid_is_crockford_and_sorted_by_time() -> None:
    early = new_ulid(datetime(2026, 1, 1, tzinfo=UTC))
    late = new_ulid(datetime(2026, 1, 2, tzinfo=UTC))
    assert ULID.match(early) and ULID.match(late)
    assert early < late
    assert new_ulid() != new_ulid()


def test_keys_layout_and_random_part() -> None:
    """T-ST1: every key is under `learners/{id}/` (Т-§3.3); keys of objects written more than
    once per id carry a ULID; a repeated upload gives a new key."""
    learner = uuid4()
    prefix = f"learners/{learner}/"
    material, session, photo, export, viz, canvas = (uuid4() for _ in range(6))

    assert material_original_key(learner, material, "pdf") == (
        f"{prefix}materials/{material}/original.pdf"
    )
    assert material_page_key(learner, material, 3) == f"{prefix}materials/{material}/pages/3.webp"
    random_part = {
        canvas_chunk_key(learner, session, canvas, 7): f"{prefix}canvas/{session}/{canvas}/7-",
        photo_original_key(learner, photo): f"{prefix}photos/{photo}/original-",
        photo_crop_key(learner, photo, 2): f"{prefix}photos/{photo}/crop_2-",
        export_key(learner, export): f"{prefix}exports/{export}-",
        viz_key(learner, viz): f"{prefix}viz/{viz}-",
        thumbnail_key(learner, canvas, "b1"): f"{prefix}thumbnails/{canvas}/b1-",
    }
    suffixes = {
        "canvas": ".bin",
        "photos": ".webp",
        "exports": ".zip",
        "viz": ".html",
        "thumbnails": ".webp",
    }
    for key, head in random_part.items():
        assert key.startswith(head), key
        suffix = suffixes[key.removeprefix(prefix).split("/")[0]]
        assert key.endswith(suffix), key
        assert ULID.match(key.removeprefix(head).removesuffix(suffix)), key

    # A repeated upload of the same photo or chunk is a new object (immutable keys, Т-§3.3).
    assert photo_original_key(learner, photo) != photo_original_key(learner, photo)
    assert canvas_chunk_key(learner, session, canvas, 7) != canvas_chunk_key(
        learner, session, canvas, 7
    )


# --- T-ST2: uploads by content --------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "kind"), [(PDF, "pdf"), (PNG, "png"), (JPEG, "jpeg"), (WEBP, "webp")]
)
def test_upload_type_by_content(data: bytes, kind: str) -> None:
    """T-ST2: the type comes from the signature, never from the name (Т-§19.8)."""
    assert detect_upload(data, max_mb=50).kind == kind


def test_upload_name_does_not_matter() -> None:
    """T-ST2: a PDF named `.png` is a PDF; an `.exe` named `.pdf` is rejected."""
    assert detect_upload(PDF, max_mb=50, filename="lecture.png").kind == "pdf"
    with pytest.raises(UploadRejected) as caught:
        detect_upload(EXE, max_mb=50, filename="lecture.pdf")
    assert caught.value.reason == "type" and caught.value.status_code == 415


def test_upload_over_limit_is_rejected() -> None:
    """T-ST2: over `upload_max_mb` → `UPLOAD_REJECTED` (413)."""
    data = PDF + b"\0" * (1024 * 1024)
    with pytest.raises(UploadRejected) as caught:
        detect_upload(data, max_mb=1)
    assert caught.value.reason == "size" and caught.value.status_code == 413
    assert detect_upload(data, max_mb=2).kind == "pdf"


def test_upload_kind_outside_the_allowed_set_is_rejected() -> None:
    """An endpoint takes only its kinds (a canvas photo is WebP, Т-§22.6)."""
    with pytest.raises(UploadRejected) as caught:
        detect_upload(PDF, max_mb=50, allowed=frozenset({"webp"}))
    assert caught.value.reason == "type"
    assert detect_upload(WEBP, max_mb=50, allowed=frozenset({"webp"})).content_type == "image/webp"


# --- T-ST3 and errors over HTTP -------------------------------------------------------------


def _app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.post("/upload")
    async def upload() -> None:  # pyright: ignore[reportUnusedFunction]
        detect_upload(EXE, max_mb=50)

    @app.post("/store")
    async def store() -> None:  # pyright: ignore[reportUnusedFunction]
        raise StorageUnavailable("connection refused")

    return app


async def test_errors_map_to_upload_rejected_and_service_unavailable() -> None:
    """`UPLOAD_REJECTED` (415/413, ТЗ Ф4 §4.5) and `SERVICE_UNAVAILABLE` with a clear message
    when storage is down (Т-§20.7, T-ST3)."""
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        rejected = await client.post("/upload")
        down = await client.post("/store")
    assert rejected.status_code == 415
    assert rejected.json()["error"]["code"] == "UPLOAD_REJECTED"
    assert down.status_code == 503
    body = down.json()["error"]
    assert body["code"] == "SERVICE_UNAVAILABLE"
    assert "storage" in body["message"]


def _store(endpoint: str = "http://127.0.0.1:9") -> S3ObjectStore:
    return S3ObjectStore(
        endpoint=endpoint, access_key="key", secret_key="secret", bucket="quack-learners"
    )


async def test_s3_store_down_is_storage_unavailable() -> None:
    """T-ST3: no answer from the storage → `StorageUnavailable`, never a driver error. One
    call: botocore retries a refused connection with its default backoff (D-C-109)."""
    store = _store()
    try:
        with pytest.raises(StorageUnavailable):
            await store.put("learners/x/a.bin", b"x", "application/octet-stream")
    finally:
        await store.aclose()


async def test_s3_unset_endpoint_is_storage_unavailable_not_a_crash() -> None:
    """An unset `s3_endpoint` (empty string, the config default) makes aiobotocore reject the
    client itself with a bare `ValueError` on first use; that must also become
    `StorageUnavailable` (Т-§20.7), not propagate and crash the caller (the canvas WS, D-C-241
    diagnosis)."""
    store = _store(endpoint="")
    try:
        with pytest.raises(StorageUnavailable):
            await store.put("learners/x/a.bin", b"x", "application/octet-stream")
        with pytest.raises(StorageUnavailable):
            await store.get("learners/x/a.bin")
        with pytest.raises(StorageUnavailable):
            await store.list_objects("learners/x/")
        with pytest.raises(StorageUnavailable):
            await store.delete_keys(["learners/x/a.bin"])
        with pytest.raises(StorageUnavailable):  # was a bare ValueError: a 500 on material pages
            await store.presigned_get_url("learners/x/a.bin", 300)
    finally:
        await store.aclose()


async def test_presigned_get_is_path_style_with_ttl() -> None:
    """Д-§8.4: Contabo needs path-style; the link lives `object_url_ttl_s`."""
    store = _store("https://eu2.contabostorage.com")
    try:
        url = await store.presigned_get_url("learners/x/photos/p/original-1.webp", 300)
    finally:
        await store.aclose()
    parts = urlsplit(url)
    assert parts.netloc == "eu2.contabostorage.com"
    assert parts.path == "/quack-learners/learners/x/photos/p/original-1.webp"
    assert parse_qs(parts.query)["X-Amz-Expires"] == ["300"]

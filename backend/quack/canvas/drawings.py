"""Drawings of a sheet photo (ТЗ Ф4 WP22.3 step 5; Т-§22.6, Т-§16.3 п.4; D-C-153).

The recognizer marks an area of the sheet as a drawing and gives its approximate box as shares
of the image. This module turns that into what the next calls need:

- `webp_size` — the size in pixels of the aligned photo, read from the WebP header: `sam-3.1`
  takes a box in absolute pixels and the size of the image (`BoxPrompt`), and compute
  `/v1/image/align` does not report it. The backend has no image library (ТЗ Ф4 §4.6).
- `pixel_box` — the box of the area in pixels.
- `drawing_mask` — the `mask_b64` of compute `/v1/image/clean-drawing`: a grayscale image of
  the size of the photo, white where the drawing is. The `data` of a `SegmentMask` is base85 as
  the model sent it (D-B-187); its decoded form is not documented ([ПРОВЕРИТЬ на стенде], DoD
  п.6a), so three forms are read — an image file (goes as it is), `H x W` bytes, the same
  deflated — and anything else gives the box of the area instead of a mask.
"""

from __future__ import annotations

import base64
import struct
import zlib

from quack.canvas.recognition import SheetBox
from quack.llm.media import BoxPrompt, SegmentedObject

_IMAGE_SIGNATURES = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"RIFF")
# 0 stays 0, anything else is the drawing.
_TO_MASK = bytes([0] + [255] * 255)


def webp_size(data: bytes) -> tuple[int, int] | None:
    """Width and height of a WebP (lossy, lossless or extended); `None` — not a WebP header."""
    if len(data) < 21 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    kind = data[12:16]
    if kind == b"VP8X" and len(data) >= 30:
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return width, height
    if kind == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if kind == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
        width, height = struct.unpack("<HH", data[26:30])
        return width & 0x3FFF, height & 0x3FFF
    return None


def pixel_box(box: SheetBox, size: tuple[int, int]) -> BoxPrompt:
    """The shares of the sheet box → the box of the `segmenter` prompt in pixels."""
    width, height = size

    def clamp(value: float, limit: int) -> int:
        return min(max(round(value * limit), 0), limit)

    return BoxPrompt(
        clamp(box.left, width),
        clamp(box.top, height),
        clamp(box.right, width),
        clamp(box.bottom, height),
        width,
        height,
    )


def drawing_mask(found: SegmentedObject | None, area: BoxPrompt, size: tuple[int, int]) -> str:
    """`mask_b64` for `/v1/image/clean-drawing`: base64 of the mask the model drew, or of the
    box of the object, or — no object, no readable mask — of the box of the area."""
    width, height = size
    if found is not None and found.mask is not None:
        raw = _payload(found.mask.data)
        if raw is not None and raw.startswith(_IMAGE_SIGNATURES):
            return base64.b64encode(raw).decode()
        pixels = None if raw is None else _pixels(raw, found.mask.height * found.mask.width)
        if pixels is not None:
            rows = _place(pixels, found.mask.width, found.mask.x, found.mask.y, size)
            return base64.b64encode(_png(width, height, rows)).decode()
    if found is not None and found.box is not None:
        box = found.box
        corners = (round(box.x1), round(box.y1), round(box.x2), round(box.y2))
    else:
        corners = (area.x1, area.y1, area.x2, area.y2)
    return base64.b64encode(_png(width, height, _rectangle(corners, size))).decode()


def _payload(data: str) -> bytes | None:
    try:
        return base64.b85decode(data)
    except ValueError:
        return None


def _pixels(raw: bytes, count: int) -> bytes | None:
    """`count` bytes of the mask: the payload itself or its deflated form."""
    if len(raw) == count:
        return raw
    try:
        inflated = zlib.decompress(raw)
    except zlib.error:
        return None
    return inflated if len(inflated) == count else None


def _place(pixels: bytes, mask_width: int, x: int, y: int, size: tuple[int, int]) -> list[bytes]:
    """The rows of the whole photo with the mask laid at (`x`, `y`) and clipped to the photo."""
    width, height = size
    blank = bytes(width)
    rows = [blank] * height
    for index in range(len(pixels) // mask_width):
        top = y + index
        if not 0 <= top < height:
            continue
        line = pixels[index * mask_width : (index + 1) * mask_width].translate(_TO_MASK)
        left = max(x, 0)
        line = line[left - x : width - x]
        rows[top] = (bytes(left) + line + blank)[:width]
    return rows


def _rectangle(corners: tuple[int, int, int, int], size: tuple[int, int]) -> list[bytes]:
    width, height = size
    x1, y1, x2, y2 = corners
    x1, x2 = min(max(x1, 0), width), min(max(x2, 0), width)
    inside = bytes(x1) + bytes([255]) * max(x2 - x1, 0) + bytes(width - max(x1, x2))
    blank = bytes(width)
    return [inside if y1 <= row < y2 else blank for row in range(height)]


def _png(width: int, height: int, rows: list[bytes]) -> bytes:
    """An 8-bit grayscale PNG (the standard library has no writer)."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(kind + body)
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    scanlines = b"".join(b"\x00" + row for row in rows)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(scanlines))
        + chunk(b"IEND", b"")
    )

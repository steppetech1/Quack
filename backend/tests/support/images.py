"""Bytes of images for tests: WebP headers of a known size and a reader of the grayscale PNG
masks of `quack.canvas.drawings` (WP22.3 step 5)."""

from __future__ import annotations

import struct
import zlib


def webp_vp8x(width: int, height: int) -> bytes:
    """A WebP header of the extended form: the canvas size is the 24-bit fields minus one."""
    body = b"WEBPVP8X" + struct.pack("<I", 10) + b"\x00\x00\x00\x00"
    body += (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    return b"RIFF" + struct.pack("<I", len(body)) + body


def webp_vp8l(width: int, height: int) -> bytes:
    bits = (width - 1) | ((height - 1) << 14)
    chunk = b"\x2f" + struct.pack("<I", bits)
    body = b"WEBPVP8L" + struct.pack("<I", len(chunk)) + chunk
    return b"RIFF" + struct.pack("<I", len(body)) + body


def webp_vp8(width: int, height: int) -> bytes:
    chunk = b"\x00\x00\x00\x9d\x01\x2a" + struct.pack("<HH", width, height)
    body = b"WEBPVP8 " + struct.pack("<I", len(chunk)) + chunk
    return b"RIFF" + struct.pack("<I", len(body)) + body


def decode_png(data: bytes) -> tuple[int, int, list[bytes]]:
    """Width, height and rows of an 8-bit grayscale PNG made by `drawing_mask`."""
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    pos, width, height, idat = 8, 0, 0, b""
    while pos < len(data):
        (size,) = struct.unpack(">I", data[pos : pos + 4])
        kind, payload = data[pos + 4 : pos + 8], data[pos + 8 : pos + 8 + size]
        if kind == b"IHDR":
            width, height, depth, color = struct.unpack(">IIBB", payload[:10])
            assert (depth, color) == (8, 0)
        elif kind == b"IDAT":
            idat += payload
        pos += 12 + size
    raw = zlib.decompress(idat)
    stride = width + 1
    assert len(raw) == stride * height
    assert all(raw[row * stride] == 0 for row in range(height))
    return width, height, [raw[row * stride + 1 : (row + 1) * stride] for row in range(height)]

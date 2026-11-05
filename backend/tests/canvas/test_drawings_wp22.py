"""WP22.3 step 5: the helpers of drawings — the size of the aligned photo and the mask for
compute `/v1/image/clean-drawing` (ТЗ Ф4 WP22.3; Т-§22.6, Т-§16.3 п.4; D-C-153)."""

from __future__ import annotations

import base64

from quack.canvas.drawings import drawing_mask, pixel_box, webp_size
from quack.canvas.recognition import SheetBox
from quack.llm.media import SegmentBoxOut, SegmentedObject, SegmentMask
from tests.support.images import decode_png, webp_vp8, webp_vp8l, webp_vp8x


def test_webp_size_reads_the_three_forms_of_the_header() -> None:
    assert webp_size(webp_vp8x(640, 480)) == (640, 480)
    assert webp_size(webp_vp8l(1024, 768)) == (1024, 768)
    assert webp_size(webp_vp8(800, 600)) == (800, 600)
    assert webp_size(b"RIFF\x00\x00\x00\x00WEBPVP8 aligned") is None
    assert webp_size(b"") is None
    assert webp_size(b"\x89PNG\r\n\x1a\n") is None


def test_pixel_box_scales_the_shares_of_the_sheet() -> None:
    box = pixel_box(SheetBox(left=0.1, top=0.25, right=0.5, bottom=0.75), (200, 400))
    assert (box.x1, box.y1, box.x2, box.y2) == (20, 100, 100, 300)
    assert (box.image_width, box.image_height) == (200, 400)
    whole = pixel_box(SheetBox(left=0.0, top=0.0, right=1.0, bottom=1.0), (200, 400))
    assert (whole.x1, whole.y1, whole.x2, whole.y2) == (0, 0, 200, 400)


def _object(
    mask: SegmentMask | None, box: tuple[float, float, float, float] | None = None
) -> SegmentedObject:
    out = None if box is None else SegmentBoxOut(x1=box[0], y1=box[1], x2=box[2], y2=box[3])
    return SegmentedObject(frame=0, index=0, box=out, mask=mask, confidence=0.9)


def test_mask_of_raw_pixels_is_put_where_the_model_says() -> None:
    """The base85 payload of `H x W` bytes (non-zero = the drawing) lands at `x`, `y`."""
    pixels = bytes([0, 1, 1, 0, 0, 7])  # 2 x 3
    mask = SegmentMask(
        height=2, width=3, marker="~", data=base64.b85encode(pixels).decode(), x=1, y=1
    )
    size, fallback = (5, 4), pixel_box(SheetBox(left=0, top=0, right=1, bottom=1), (5, 4))
    png = base64.b64decode(drawing_mask(_object(mask), fallback, size))
    width, height, rows = decode_png(png)
    assert (width, height) == (5, 4)
    assert rows == [
        bytes([0, 0, 0, 0, 0]),
        bytes([0, 0, 255, 255, 0]),
        bytes([0, 0, 0, 255, 0]),
        bytes([0, 0, 0, 0, 0]),
    ]


def test_mask_in_an_image_file_goes_as_it_is() -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"whatever"
    mask = SegmentMask(height=1, width=1, marker="", data=base64.b85encode(png).decode())
    out = drawing_mask(
        _object(mask), pixel_box(SheetBox(left=0, top=0, right=1, bottom=1), (4, 4)), (4, 4)
    )
    assert base64.b64decode(out) == png


def test_unreadable_mask_falls_back_to_the_box_of_the_model_or_of_the_recognizer() -> None:
    size = (6, 4)
    recognizer_box = pixel_box(SheetBox(left=0.5, top=0.0, right=1.0, bottom=0.5), size)
    broken = SegmentMask(height=2, width=2, marker="", data="not base85 \x00")
    # The box of `sam-3.1` wins over the box of the recognizer.
    _, _, rows = decode_png(
        base64.b64decode(drawing_mask(_object(broken, (0, 0, 2, 2)), recognizer_box, size))
    )
    assert rows == [bytes([255, 255, 0, 0, 0, 0])] * 2 + [bytes(6)] * 2
    # No object at all (no match): the box of the recognizer.
    _, _, rows = decode_png(base64.b64decode(drawing_mask(None, recognizer_box, size)))
    assert rows == [bytes([0, 0, 0, 255, 255, 255])] * 2 + [bytes(6)] * 2
    # A payload whose length is not `H x W` is not a pixel mask: the box again.
    short = SegmentMask(height=2, width=2, marker="", data=base64.b85encode(b"abc").decode())
    _, _, rows = decode_png(base64.b64decode(drawing_mask(_object(short), recognizer_box, size)))
    assert rows[0] == bytes([0, 0, 0, 255, 255, 255])

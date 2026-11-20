"""A lecture in PowerPoint is a material: the type is decided by the content of the zip (a
presentation has `ppt/presentation.xml`), never by the name; other zips and fakes are refused."""

from __future__ import annotations

import io
import zipfile

import pytest

from quack.materials.library import clean_title
from quack.storage.uploads import UploadRejected, detect_upload


def _zip(*names: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, "<x/>")
    return buffer.getvalue()


def test_a_presentation_is_recognised_by_its_structure() -> None:
    found = detect_upload(_zip("[Content_Types].xml", "ppt/presentation.xml"), max_mb=50)
    assert found.kind == "pptx"
    assert found.extension == "pptx"
    assert found.content_type.endswith("presentationml.presentation")


@pytest.mark.parametrize(
    "data",
    [
        _zip("[Content_Types].xml", "word/document.xml"),  # a .docx
        _zip("[Content_Types].xml", "xl/workbook.xml"),  # an .xlsx
        _zip("ppt/presentation.xml"),  # no content types: not an Office package
        b"PK\x03\x04 not really a zip",
        b"plain text named lecture.pptx",
    ],
)
def test_other_zips_and_fakes_are_refused_whatever_their_name(data: bytes) -> None:
    with pytest.raises(UploadRejected) as raised:
        detect_upload(data, max_mb=50, filename="lecture.pptx")
    assert raised.value.reason == "type" and raised.value.status_code == 415


def test_the_photo_route_still_takes_only_its_own_kind() -> None:
    from quack.canvas.photos import PHOTO_KINDS

    deck = _zip("[Content_Types].xml", "ppt/presentation.xml")
    with pytest.raises(UploadRejected):
        detect_upload(deck, max_mb=50, allowed=PHOTO_KINDS)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("lecture 3.pptx", "lecture 3.pptx"),
        ("C:\\Users\\me\\Desktop\\dynamics.pdf", "dynamics.pdf"),
        ("/tmp/../etc/notes.pdf", "notes.pdf"),
        ("bad\x00na\x07me.pdf", "badname.pdf"),
        ("   ", None),
        ("", None),
        (None, None),
        ("x" * 300 + ".pdf", "x" * 120),
    ],
)
def test_the_name_of_an_upload_is_cleaned_for_display(
    raw: str | None, expected: str | None
) -> None:
    assert clean_title(raw) == expected

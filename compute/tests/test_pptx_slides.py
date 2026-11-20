"""`POST /v1/pptx/slides`: the text of a presentation by slides, read from the zip with the standard
library. The file is untrusted: what is not a presentation, is too large unpacked or carries a
DTD is refused or skipped, never executed."""

from __future__ import annotations

import base64
import io
import zipfile

import pytest

from quack_compute import work
from quack_compute.contracts import PptxSlidesRequest

NS = (
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"'
)
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _shape(text: str, kind: str | None = None) -> str:
    holder = f'<p:ph type="{kind}"/>' if kind else ""
    return (
        '<p:sp><p:nvSpPr><p:cNvPr id="1" name="s"/><p:cNvSpPr/>'
        f"<p:nvPr>{holder}</p:nvPr></p:nvSpPr>"
        f"<p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp>"
    )


def _slide(*shapes: str) -> str:
    return (
        f'<?xml version="1.0"?><p:sld {NS}><p:cSld><p:spTree>'
        + "".join(shapes)
        + "</p:spTree></p:cSld></p:sld>"
    )


def _rels(*targets: tuple[str, str, str]) -> str:
    items = "".join(
        f'<Relationship Id="{rid}" Type="{REL_TYPE}/{kind}" Target="{target}"/>'
        for rid, kind, target in targets
    )
    return f'<?xml version="1.0"?><Relationships xmlns="{REL_NS}">{items}</Relationships>'


def _pptx(parts: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        for name, body in parts.items():
            archive.writestr(name, body)
    return buffer.getvalue()


def _read(data: bytes) -> list[tuple[int, str, str, str]]:
    request = PptxSlidesRequest(pptx_b64=base64.b64encode(data).decode())
    return [(s.slide, s.title, s.text, s.notes) for s in work.pptx_slides(request).slides]


def _deck() -> bytes:
    table = (
        "<p:graphicFrame><a:graphic><a:graphicData><a:tbl>"
        "<a:tr><a:tc><a:txBody><a:p><a:r><a:t>surface</a:t></a:r></a:p></a:txBody></a:tc>"
        "<a:tc><a:txBody><a:p><a:r><a:t>mu</a:t></a:r></a:p></a:txBody></a:tc></a:tr>"
        "<a:tr><a:tc><a:txBody><a:p><a:r><a:t>wood</a:t></a:r></a:p></a:txBody></a:tc>"
        "<a:tc><a:txBody><a:p><a:r><a:t>0.35</a:t></a:r></a:p></a:txBody></a:tc></a:tr>"
        "</a:tbl></a:graphicData></a:graphic></p:graphicFrame>"
    )
    group = f"<p:grpSp>{_shape('inside a group')}</p:grpSp>"
    formula = (
        '<p:sp><p:nvSpPr><p:cNvPr id="2" name="f"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:txBody>'
        "<a:p><a:r><a:t>Energy: </a:t></a:r><m:oMath><m:r><m:t>E=mc^2</m:t></m:r></m:oMath></a:p>"
        "</p:txBody></p:sp>"
    )
    notes = (
        f'<?xml version="1.0"?><p:notes {NS}><p:cSld><p:spTree>'
        + _shape("slide image", "sldImg")
        + _shape("Stress the NET force", "body")
        + _shape("2", "sldNum")
        + "</p:spTree></p:cSld></p:notes>"
    )
    presentation = (
        f'<?xml version="1.0"?><p:presentation {NS}><p:sldIdLst>'
        '<p:sldId id="256" r:id="rId2"/><p:sldId id="257" r:id="rId1"/>'
        "</p:sldIdLst></p:presentation>"
    )
    return _pptx(
        {
            "ppt/presentation.xml": presentation,
            # the order of the list, not of the file names: rId2 (slide2.xml) comes first
            "ppt/_rels/presentation.xml.rels": _rels(
                ("rId1", "slide", "slides/slide1.xml"), ("rId2", "slide", "slides/slide2.xml")
            ),
            "ppt/slides/slide1.xml": _slide(
                _shape("Second in the deck", "title"), _shape("bullet one"), table, group
            ),
            "ppt/slides/slide2.xml": _slide(
                _shape("Opening", "ctrTitle"), _shape("3", "sldNum"), formula
            ),
            "ppt/slides/_rels/slide1.xml.rels": _rels(
                ("rId1", "notesSlide", "../notesSlides/notesSlide1.xml")
            ),
            "ppt/notesSlides/notesSlide1.xml": notes,
        }
    )


def test_slides_come_in_presentation_order_with_titles_text_tables_formulas_and_notes() -> None:
    slides = _read(_deck())

    assert [s[0] for s in slides] == [1, 2]
    _, title, text, notes = slides[0]
    assert (title, text, notes) == ("Opening", "Energy: E=mc^2", "")  # slide numbers are not text
    _, title2, text2, notes2 = slides[1]
    assert title2 == "Second in the deck"
    assert text2.splitlines() == ["bullet one", "surface | mu", "wood | 0.35", "inside a group"]
    assert notes2 == "Stress the NET force"  # not the slide image, not the number


def test_without_a_slide_list_the_numbers_of_the_names_give_the_order() -> None:
    data = _pptx(
        {
            "ppt/presentation.xml": f'<?xml version="1.0"?><p:presentation {NS}/>',
            "ppt/slides/slide10.xml": _slide(_shape("ten", "title")),
            "ppt/slides/slide2.xml": _slide(_shape("two", "title")),
        }
    )
    assert [s[1] for s in _read(data)] == ["two", "ten"]


def test_a_file_that_is_not_a_presentation_is_refused() -> None:
    with pytest.raises(ValueError):
        _read(b"not a zip at all")
    with pytest.raises(ValueError):
        _read(_pptx({"word/document.xml": "<w/>"}))  # a zip, but no presentation.xml


def test_a_zip_bomb_is_refused_before_anything_is_read() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("ppt/presentation.xml", f"<p:presentation {NS}/>")
        for i in range(9):
            archive.writestr(f"ppt/media/big{i}.bin", b"\0" * (8 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="too large"):
        _read(buffer.getvalue())


def test_a_slide_with_a_dtd_is_skipped_not_parsed() -> None:
    evil = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]>' + _slide(_shape("&a;", "title"))
    data = _pptx(
        {
            "ppt/presentation.xml": f'<?xml version="1.0"?><p:presentation {NS}/>',
            "ppt/slides/slide1.xml": evil,
            "ppt/slides/slide2.xml": _slide(_shape("fine", "title")),
        }
    )
    slides = _read(data)
    assert [s[1] for s in slides] == ["fine"]

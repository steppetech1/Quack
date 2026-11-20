"""Job bodies of the WP24 endpoints; each runs in a pool process (`TaskPool.run`).

PyMuPDF (`pdf_pages`) and OpenCV (`image_*`), Т-§1.2. Rendering lives in
`quack_compute.renderer` (one Chromium per service). Images go out as WebP with the
encoder's default quality; the numbers of the algorithms come from `ComputeSettings` (D-C-110).
"""

from __future__ import annotations

import base64
import io
import posixpath
import re
import zipfile
from xml.etree import ElementTree

import cv2
import numpy as np
import pymupdf

from quack_compute.contracts import (
    AlignResponse,
    CleanDrawingRequest,
    ImageRequest,
    ImageResponse,
    PdfPage,
    PdfPagesRequest,
    PdfPagesResponse,
    PptxSlide,
    PptxSlidesRequest,
    PptxSlidesResponse,
    RenderStrokesRequest,
)
from quack_compute.settings import ComputeSettings

type Image = np.ndarray


def _decode(b64: str, flags: int = cv2.IMREAD_COLOR) -> Image:
    data = np.frombuffer(base64.b64decode(b64), dtype=np.uint8)
    image = cv2.imdecode(data, flags)
    if image is None:
        raise ValueError("not an image")
    return image


def _webp(image: Image) -> str:
    ok, encoded = cv2.imencode(".webp", image)
    if not ok:
        raise ValueError("WebP encoding failed")
    return base64.b64encode(encoded.tobytes()).decode()


def pdf_pages(request: PdfPagesRequest) -> PdfPagesResponse:
    """Text and an image of every page (WP20.2); the image at PyMuPDF's default resolution."""
    document = pymupdf.open(stream=base64.b64decode(request.pdf_b64), filetype="pdf")
    try:
        pages = []
        for index in range(document.page_count):
            page = document[index]
            number = index + 1
            pixmap = page.get_pixmap()
            image = _decode(base64.b64encode(pixmap.tobytes("png")).decode())
            pages.append(
                PdfPage(page=number, text=str(page.get_text()), image_webp_b64=_webp(image))
            )
    finally:
        document.close()
    return PdfPagesResponse(pages=pages)


# --- PPTX (the slides of a presentation) ---------------------------------------------------------
# A .pptx is a zip of XML. The text is read with the standard library (no new dependency, nothing
# is executed, no macro or external link is followed). The file is untrusted: the number of
# entries and the size of what is read are bounded (a zip bomb costs a refusal, not the memory).

_PPTX_MAX_ENTRIES = 4000
_PPTX_MAX_PART_BYTES = 8 * 1024 * 1024
_PPTX_MAX_TOTAL_BYTES = 64 * 1024 * 1024
_NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
_A = "{" + _NS["a"] + "}"
_P = "{" + _NS["p"] + "}"
_M = "{" + _NS["m"] + "}"
_R = "{" + _NS["r"] + "}"
_TITLE_TYPES = {"title", "ctrTitle"}
_SKIPPED_PLACEHOLDERS = {"sldNum", "dt", "ftr", "sldImg", "hdr"}
_SLIDE_NUMBER = re.compile(r"slide(\d+)\.xml$")


class _Package:
    """Reads parts of the zip under the limits above."""

    def __init__(self, data: bytes) -> None:
        self._zip = zipfile.ZipFile(io.BytesIO(data))
        infos = self._zip.infolist()
        if len(infos) > _PPTX_MAX_ENTRIES:
            raise ValueError("pptx has too many parts")
        if sum(i.file_size for i in infos) > _PPTX_MAX_TOTAL_BYTES:
            raise ValueError("pptx is too large when unpacked")
        self.names = {i.filename for i in infos}

    def xml(self, name: str) -> ElementTree.Element | None:
        if name not in self.names:
            return None
        with self._zip.open(name) as handle:
            raw = handle.read(_PPTX_MAX_PART_BYTES + 1)
        if len(raw) > _PPTX_MAX_PART_BYTES:
            raise ValueError(f"pptx part {name} is too large")
        if b"<!DOCTYPE" in raw[:2048].upper():
            return None  # a DTD has no place in a presentation
        return ElementTree.fromstring(raw)


def _rels(package: _Package, part: str) -> dict[str, tuple[str, str]]:
    """Relationship id -> (type, absolute path of the target) of a part."""
    folder, name = posixpath.split(part)
    root = package.xml(posixpath.join(folder, "_rels", name + ".rels"))
    out: dict[str, tuple[str, str]] = {}
    if root is None:
        return out
    for rel in root.findall("rel:Relationship", _NS):
        if rel.get("TargetMode") == "External":
            continue
        target = rel.get("Target") or ""
        if target.startswith("/"):
            path = target.lstrip("/")
        else:
            path = posixpath.normpath(posixpath.join(folder, target))
        out[rel.get("Id") or ""] = (rel.get("Type") or "", path)
    return out


def _paragraph_text(paragraph: ElementTree.Element) -> str:
    """Runs, fields, line breaks and the text of formulas (`m:t`) of one `a:p`, in order."""
    parts: list[str] = []
    for node in paragraph.iter():
        if node.tag in (_A + "t", _M + "t") and node.text:
            parts.append(node.text)
        elif node.tag == _A + "br":
            parts.append("\n")
    return "".join(parts).strip()


def _placeholder_type(shape: ElementTree.Element) -> str | None:
    holder = shape.find("p:nvSpPr/p:nvPr/p:ph", _NS)
    return None if holder is None else (holder.get("type") or "body")


def _table_rows(table: ElementTree.Element) -> list[str]:
    rows = []
    for row in table.findall("a:tr", _NS):
        cells = []
        for cell in row.findall("a:tc", _NS):
            texts = [t for p in cell.findall(".//a:p", _NS) if (t := _paragraph_text(p))]
            cells.append(" ".join(texts))
        if any(cells):
            rows.append(" | ".join(cells))
    return rows


def _slide_text(root: ElementTree.Element) -> tuple[str, str]:
    """(title, the other text) of a slide in the order of its shapes; groups are entered."""
    titles: list[str] = []
    body: list[str] = []

    def visit(container: ElementTree.Element) -> None:
        for child in container:
            if child.tag == _P + "sp":
                kind = _placeholder_type(child)
                if kind in _SKIPPED_PLACEHOLDERS:
                    continue
                lines = [t for p in child.findall(".//a:p", _NS) if (t := _paragraph_text(p))]
                if kind in _TITLE_TYPES:
                    titles.extend(lines)
                else:
                    body.extend(lines)
            elif child.tag == _P + "graphicFrame":
                tables = child.findall(".//a:tbl", _NS)
                for table in tables:
                    body.extend(_table_rows(table))
                if not tables:  # a text box in a frame, SmartArt text that sits in the slide
                    body.extend(
                        t for p in child.findall(".//a:p", _NS) if (t := _paragraph_text(p))
                    )
            elif child.tag == _P + "grpSp":
                visit(child)

    tree = root.find("p:cSld/p:spTree", _NS)
    if tree is not None:
        visit(tree)
    return " ".join(titles).strip(), "\n".join(body).strip()


def _notes_text(package: _Package, slide_part: str) -> str:
    for kind, path in _rels(package, slide_part).values():
        if not kind.endswith("/notesSlide"):
            continue
        root = package.xml(path)
        if root is None:
            continue
        lines: list[str] = []
        for shape in root.iter(_P + "sp"):
            if _placeholder_type(shape) == "body":
                lines.extend(t for p in shape.findall(".//a:p", _NS) if (t := _paragraph_text(p)))
        return "\n".join(lines).strip()
    return ""


def _slide_parts(package: _Package) -> list[str]:
    """The slide parts in the order of the presentation (`sldIdLst`); a package without that list
    falls back to the numbers in the names."""
    presentation = package.xml("ppt/presentation.xml")
    if presentation is None:
        raise ValueError("not a presentation")
    rels = _rels(package, "ppt/presentation.xml")
    ordered = []
    for node in presentation.findall("p:sldIdLst/p:sldId", _NS):
        rid = node.get(_R + "id")
        if rid and rid in rels:
            ordered.append(rels[rid][1])
    if ordered:
        return ordered
    numbered = []
    for name in package.names:
        match = _SLIDE_NUMBER.search(name)
        if name.startswith("ppt/slides/") and match:
            numbered.append((int(match[1]), name))
    return [name for _, name in sorted(numbered)]


def pptx_slides(request: PptxSlidesRequest) -> PptxSlidesResponse:
    """Title, text and speaker notes of every slide (a presentation from a lecture); a file that
    is not a presentation or is too large is a `ValueError` (the job fails for good)."""
    try:
        package = _Package(base64.b64decode(request.pptx_b64))
    except zipfile.BadZipFile as exc:
        raise ValueError("not a presentation") from exc
    slides: list[PptxSlide] = []
    for number, part in enumerate(_slide_parts(package), 1):
        root = package.xml(part)
        if root is None:
            continue
        title, text = _slide_text(root)
        notes = _notes_text(package, part)
        slides.append(PptxSlide(slide=number, title=title, text=text, notes=notes))
    return PptxSlidesResponse(slides=slides)


def _ordered(corners: Image) -> Image:
    """Corners as top-left, top-right, bottom-right, bottom-left."""
    points = corners.reshape(4, 2).astype(np.float32)
    sums = points.sum(axis=1)
    diffs = np.diff(points, axis=1).ravel()
    return np.array(
        [
            points[np.argmin(sums)],
            points[np.argmin(diffs)],
            points[np.argmax(sums)],
            points[np.argmax(diffs)],
        ],
        dtype=np.float32,
    )


def _sheet_corners(image: Image, settings: ComputeSettings) -> Image | None:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    kernel = settings.align_blur_kernel
    edges = cv2.Canny(
        cv2.GaussianBlur(gray, (kernel, kernel), 0),
        settings.align_canny_low,
        settings.align_canny_high,
    )
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_area = settings.align_min_sheet_area_share * image.shape[0] * image.shape[1]
    for contour in sorted(contours, key=cv2.contourArea, reverse=True):
        if cv2.contourArea(contour) < min_area:
            return None
        polygon = cv2.approxPolyDP(
            contour, settings.align_contour_epsilon_share * cv2.arcLength(contour, True), True
        )
        if len(polygon) == 4:
            return _ordered(polygon)
    return None


def image_align(request: ImageRequest, settings: ComputeSettings) -> AlignResponse:
    """Straighten the sheet by its contour; no contour — the photo as is (WP22.3 п.1)."""
    image = _decode(request.image_b64)
    corners = _sheet_corners(image, settings)
    if corners is None:
        return AlignResponse(aligned=False, image_webp_b64=_webp(image))
    tl, tr, br, bl = corners
    width = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    height = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    target = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float32
    )
    matrix = cv2.getPerspectiveTransform(corners, target)
    aligned = cv2.warpPerspective(image, matrix, (width, height))
    return AlignResponse(aligned=True, image_webp_b64=_webp(aligned))


def image_clean_drawing(request: CleanDrawingRequest) -> ImageResponse:
    """The drawing without its paper (Т-§16.3 п.4): ink by Otsu's threshold stays opaque,
    the paper and everything outside the `segmenter` mask become transparent."""
    image = _decode(request.image_b64)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    if request.mask_b64 is not None:
        mask = _decode(request.mask_b64, cv2.IMREAD_GRAYSCALE)
        if mask.shape != gray.shape:
            mask = cv2.resize(mask, (gray.shape[1], gray.shape[0]))
        ink = cv2.bitwise_and(ink, mask)
    cleaned = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
    cleaned[:, :, 3] = ink
    return ImageResponse(image_webp_b64=_webp(cleaned))


def image_render_strokes(request: RenderStrokesRequest, settings: ComputeSettings) -> ImageResponse:
    """Pen strokes of one line as dark lines on white, `width` × `height` (WP21.3)."""
    canvas = np.full((request.height, request.width), 255, dtype=np.uint8)
    thickness = settings.stroke_thickness_px
    for stroke in request.strokes:
        if not stroke.points:
            continue  # a stroke without a point draws nothing
        points = np.array([[round(x), round(y)] for x, y in stroke.points], dtype=np.int32)
        if len(points) == 1:
            cv2.circle(canvas, tuple(int(v) for v in points[0]), thickness, 0, -1)
        else:
            cv2.polylines(canvas, [points], False, 0, thickness, cv2.LINE_AA)
    return ImageResponse(image_webp_b64=_webp(canvas))

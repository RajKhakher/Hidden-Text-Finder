"""PDF scanner.

How it decides whether text is visible:

1. It reads every piece of text on the page together with how it is drawn: font size,
   colour, opacity, position and "render mode" (PDFs can draw text as invisible).
2. It renders the page to a picture, the same way a PDF viewer would, and looks at the
   pixels in and around each piece of text:
   * the most common colour in the text's box is its background; if the text colour is almost
     the same as the background (low contrast), people can't see it;
   * if no pixel in the box has moved from the background towards the text colour, nothing of
     the text is showing: something is covering it or it is clipped away.
3. It reads the page's drawing order, so it knows when a shape or picture was painted after
   (on top of) a piece of text. That catches white boxes over text and failed redactions
   (a black box over black text).
4. It switches on any layers that are turned off and looks for text that only appears then.
5. Scanned pages (a big picture with an invisible text layer on top) are reported once as
   a note, because that invisible layer is normal for scanners.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pymupdf

from .ai_score import REPORT_THRESHOLD, score_text
from .models import Finding, ScanError, max_severity
from .techniques import CATALOG, make_finding
from .unicode_scan import scan_invisibles

RENDER_ZOOM = 3.0            # render at 216 dpi so small text still leaves visible pixels
SAME_COLOUR_CONTRAST = 1.12  # below this contrast ratio the text blends into its background
FAINT_CONTRAST = 1.6         # below this it is very faint (1.0 = identical, 21 = black on white)
MIN_INK_PIXELS = 3           # visible text changes at least a few pixels of its box
TINY_PT = 2.0
SMALL_PT = 3.0
UNIFORM_BACKGROUND = 0.5     # share of the box that must be one colour to call it a flat background
COVERS = 0.9                 # a shape "covers" text when it spans 90% of the text's box
FILL_TYPES = {"fill-path", "fill-image", "fill-imgmask", "fill-shade"}
OCR_IMAGE_COVERAGE = 0.5
OCR_HIDDEN_SHARE = 0.6

# When one piece of text is hidden in several ways, the first of these names the finding.
PRIORITY = [
    "pdf.hidden_layer", "pdf.invisible_mode", "pdf.transparent", "pdf.off_page",
    "pdf.same_colour", "pdf.covered", "pdf.tiny", "pdf.small", "pdf.partly_off_page", "pdf.faint",
]
OCR_LIKE = {"pdf.invisible_mode", "pdf.covered", "pdf.tiny", "pdf.small", "pdf.faint", "pdf.same_colour"}


@dataclass
class _Span:
    page: int
    index: int                      # position among the page's non-blank text pieces
    text: str
    bbox: pymupdf.Rect              # unrotated page coordinates (what annotations use)
    reasons: dict[str, dict[str, Any]] = field(default_factory=dict)
    where: str = ""                 # "top", "middle" or "bottom" of the page, as seen on screen
    view: pymupdf.Rect | None = None
    size: float = 10.0
    first: pymupdf.Rect | None = None   # box of the first and last character, used to join pieces
    last: pymupdf.Rect | None = None


def _where(view: pymupdf.Rect, prect: pymupdf.Rect) -> str:
    if view.x1 <= prect.x0 or view.x0 >= prect.x1 or view.y1 <= prect.y0 or view.y0 >= prect.y1:
        return "outside the page"
    centre = (view.y0 + view.y1) / 2
    third = prect.height / 3
    if centre < prect.y0 + third:
        return "top of the page"
    if centre < prect.y0 + 2 * third:
        return "middle of the page"
    return "bottom of the page"


def _attach_context(spans: list[_Span]) -> None:
    """For each hidden piece, remember the nearest visible text so people can find the spot."""
    visible = [s for s in spans if not s.reasons and s.view is not None and len(s.text.strip()) >= 4]
    for span in spans:
        if not span.reasons or span.view is None or not visible:
            continue
        centre = (span.view.y0 + span.view.y1) / 2
        nearest = min(visible, key=lambda v: abs((v.view.y0 + v.view.y1) / 2 - centre))  # type: ignore[union-attr]
        snippet = re.sub(r"\s+", " ", nearest.text).strip()
        span.reasons[next(iter(span.reasons))].setdefault("near_visible_text", snippet[:70])


# ---------------------------------------------------------------- colour helpers

def _to_rgb(color: Any) -> tuple[float, float, float]:
    if isinstance(color, (int, float)):
        return (float(color),) * 3
    color = tuple(color or ())
    if len(color) == 1:
        return (color[0],) * 3
    if len(color) == 3:
        return tuple(float(c) for c in color)  # type: ignore[return-value]
    if len(color) == 4:  # CMYK, common in print-ready PDFs
        c, m, y, k = color
        return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    return (0.0, 0.0, 0.0)


def _luminance(rgb: tuple[float, float, float]) -> float:
    def channel(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    """WCAG contrast ratio: 1.0 means identical colours, 21 means black on white."""
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _hex(rgb: tuple[float, float, float] | np.ndarray) -> str:
    return "#" + "".join(f"{int(round(max(0.0, min(1.0, float(v))) * 255)):02x}" for v in rgb)


def _extent(box: Any, dx: float, dy: float) -> tuple[float, float]:
    """Where a character box starts and ends along the writing direction."""
    values = [x * dx + y * dy for x in (box[0], box[2]) for y in (box[1], box[3])]
    return min(values), max(values)


def _span_text(span: dict[str, Any]) -> str:
    """The characters of a text piece, with word spaces put back.

    Many PDFs (everything made with LaTeX, for example) contain no space characters at all:
    words are simply placed apart. A gap wider than 15% of the font size counts as a space.
    """
    size = float(span.get("size") or 1.0) or 1.0
    dx, dy = span.get("dir") or (1.0, 0.0)
    out: list[str] = []
    prev_end: float | None = None
    prev_line: float | None = None
    for ch in span["chars"]:
        cp, box = ch[0], ch[3]
        if cp is None or cp < 0 or (cp < 32 and cp != 9):
            continue
        char = chr(cp)
        start, end = _extent(box, dx, dy)
        origin = ch[2]
        line = -origin[0] * dy + origin[1] * dx      # position across the writing direction
        if prev_end is not None and out and not char.isspace():
            new_line = (prev_line is not None and abs(line - prev_line) > 0.5 * size) or start < prev_end - 2 * size
            if new_line:
                if len(out) >= 2 and out[-1] == "-" and out[-2].isalpha() and char.isalpha():
                    out.pop()                        # word hyphenated at the end of a line
                elif not out[-1].isspace():
                    out.append(" ")
            elif not out[-1].isspace() and start - prev_end > 0.15 * size:
                out.append(" ")
        out.append(char)
        prev_end, prev_line = end, line
    return "".join(out)


def _char_boxes(span: dict[str, Any]) -> tuple[pymupdf.Rect | None, pymupdf.Rect | None]:
    boxes = [pymupdf.Rect(ch[3]) for ch in span["chars"] if ch[0] is not None and ch[0] > 32]
    return (boxes[0], boxes[-1]) if boxes else (None, None)


# ---------------------------------------------------------------- per-page analysis

def _render(page: pymupdf.Page) -> np.ndarray:
    pix = page.get_pixmap(matrix=pymupdf.Matrix(RENDER_ZOOM, RENDER_ZOOM), alpha=False,
                          colorspace=pymupdf.csRGB)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    return img[:, :, :3]


def _dominant(pixels: np.ndarray) -> tuple[tuple[float, float, float], float]:
    """Most common colour among the pixels (0-1 RGB) and the share of pixels that have it."""
    q = pixels // 16
    keys = q[:, 0] * 256 + q[:, 1] * 16 + q[:, 2]
    counts = np.bincount(keys, minlength=4096)
    top = int(counts.argmax())
    mean = pixels[keys == top].mean(axis=0) / 255.0
    return (float(mean[0]), float(mean[1]), float(mean[2])), counts[top] / len(keys)


def _pixel_box(rect: pymupdf.Rect, img: np.ndarray) -> tuple[int, int, int, int]:
    r = rect * pymupdf.Matrix(RENDER_ZOOM, RENDER_ZOOM)
    x0, y0 = max(int(math.floor(r.x0)), 0), max(int(math.floor(r.y0)), 0)
    x1, y1 = min(int(math.ceil(r.x1)), img.shape[1]), min(int(math.ceil(r.y1)), img.shape[0])
    return x0, y0, x1, y1


class _Fills:
    """Filled shapes and pictures in drawing order, to tell what is painted before/after some text."""

    def __init__(self, page: pymupdf.Page):
        rows = []
        try:
            for seq, (kind, box) in enumerate(page.get_bboxlog()):
                if kind in FILL_TYPES:
                    rows.append((seq, *box))
        except Exception:  # noqa: BLE001 - older PyMuPDF or damaged content: fall back to pixels only
            rows = []
        self.rows = np.array(rows, dtype=float) if rows else np.zeros((0, 5))

    def covering(self, bbox: pymupdf.Rect, seq: int, *, after: bool) -> bool:
        area = bbox.width * bbox.height
        if area <= 0 or not len(self.rows):
            return False
        rows = self.rows[self.rows[:, 0] > seq] if after else self.rows[self.rows[:, 0] < seq]
        if not len(rows):
            return False
        w = np.clip(np.minimum(rows[:, 3], bbox.x1) - np.maximum(rows[:, 1], bbox.x0), 0, None)
        h = np.clip(np.minimum(rows[:, 4], bbox.y1) - np.maximum(rows[:, 2], bbox.y0), 0, None)
        return bool(((w * h) / area >= COVERS).any())


def _render_bare(doc: pymupdf.Document, pno: int) -> tuple[np.ndarray | None, set[tuple]]:
    """Render the page with every piece of text removed (shapes and pictures stay).

    Comparing this with the normal rendering shows exactly which pixels each piece of text
    changes, and what colour is really underneath it. Returns (None, set()) if the text can't
    be removed, plus the keys of any text that survived removal.
    """
    try:
        single = pymupdf.open()
        single.insert_pdf(doc, from_page=pno, to_page=pno)
        page = single[0]
        page.add_redact_annot(page.mediabox + (-20000, -20000, 20000, 20000), fill=False)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                              graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                              text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        leftover = {_key(_span_text(s), pymupdf.Rect(s["bbox"])) for s in page.get_texttrace()
                    if _span_text(s).strip()}
        return _render(page), leftover
    except Exception:  # noqa: BLE001 - damaged or unusual page: fall back to single-render checks
        return None, set()


def _pixel_checks(span: _Span, view: pymupdf.Rect, img: np.ndarray, bare: np.ndarray | None,
                  rgb: tuple[float, float, float], seq: int, fills: _Fills) -> None:
    x0, y0, x1, y1 = _pixel_box(view, img)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return
    region = img[y0:y1, x0:x1].reshape(-1, 3).astype(np.int16)
    text_px = np.array([round(c * 255) for c in rgb], dtype=np.int16)

    if bare is not None and bare.shape == img.shape:
        # Two renderings: with the text and without it. The difference is the text's own mark.
        base = bare[y0:y1, x0:x1].reshape(-1, 3).astype(np.int16)
        bg, share = _dominant(base)
        ink = int((np.abs(region - base).max(axis=1) > 24).sum())
        solid_glyph = False
    else:
        # One rendering only: the most common colour in the box stands in for the background,
        # and the colour just around the box tells a solid glyph (like █) from hidden text.
        bg, share = _dominant(region)
        bg_px = np.array([round(c * 255) for c in bg], dtype=np.int16)
        gap = int(np.abs(text_px - bg_px).max())
        from_bg = np.abs(region - bg_px).max(axis=1)
        to_text = np.abs(region - text_px).max(axis=1)
        ink = int(((from_bg >= max(20, 0.15 * gap)) & (to_text < gap)).sum()) if gap > 0 else 0
        pad = max(1.5, 0.25 * view.height)
        ex0, ey0, ex1, ey1 = _pixel_box(view + (-pad, -pad, pad, pad), img)
        outer = img[ey0:ey1, ex0:ex1].astype(np.int16)
        mask = np.ones(outer.shape[:2], dtype=bool)
        mask[y0 - ey0:y1 - ey0, x0 - ex0:x1 - ex0] = False
        ring = outer[mask]
        ring_bg = _dominant(ring)[0] if len(ring) >= 8 else bg
        solid_glyph = (share >= 0.9 and contrast_ratio(rgb, ring_bg) >= FAINT_CONTRAST
                       and not fills.covering(span.bbox, seq, after=False))

    ratio = contrast_ratio(rgb, bg)
    details: dict[str, Any] = {"text_colour": _hex(rgb), "background_colour": _hex(bg), "contrast_ratio": round(ratio, 2)}

    # Single stray glyphs (accents, quotes) often draw outside their nominal box, so a "nothing
    # shows" verdict is only trusted for real words.
    letters = len(re.sub(r"\s", "", span.text))

    # 1. Something painted on top of the text.
    if fills.covering(span.bbox, seq, after=True):
        if ink < MIN_INK_PIXELS and letters >= 2:
            if ratio < SAME_COLOUR_CONTRAST and max(bg) < 0.3:
                details["note"] = "a dark box is drawn over dark text: this is what a failed redaction looks like"
            else:
                details["note"] = "a shape or picture is painted on top of the text"
            span.reasons["pdf.covered"] = details
        return  # the text shows through whatever is on top (for example a see-through highlight)

    # 2. Text colour against its background.
    if ratio < FAINT_CONTRAST:
        if solid_glyph:
            return  # a character like █ fills its own box but stands out from its surroundings
        if ratio < SAME_COLOUR_CONTRAST and share >= UNIFORM_BACKGROUND:
            if max(bg) < 0.3 and fills.covering(span.bbox, seq, after=False):
                details["note"] = "dark text on a dark box: this is what a failed redaction looks like"
            span.reasons["pdf.same_colour"] = details
        else:
            if share < UNIFORM_BACKGROUND:
                details["note"] = "the background here is a picture or gradient, so the colour check is less certain"
            span.reasons["pdf.faint"] = details
        return

    # 3. Nothing of the text shows at all (for example it is clipped away). Look a little wider than
    #    the box first, because italic and decorative letters can lean outside it.
    if ink < MIN_INK_PIXELS and letters >= 3:
        if bare is not None and bare.shape == img.shape:
            grow = 0.3 * max(view.height, 1.0)
            gx0, gy0, gx1, gy1 = _pixel_box(view + (-grow, 0, grow, 0), img)
            wider = np.abs(img[gy0:gy1, gx0:gx1].astype(np.int16) - bare[gy0:gy1, gx0:gx1].astype(np.int16))
            if int((wider.max(axis=2) > 24).sum()) >= MIN_INK_PIXELS:
                return
        details["note"] = "none of the text's pixels are drawn (it may be clipped away)"
        span.reasons["pdf.covered"] = details


def _analyse_page(page: pymupdf.Page, pno: int) -> tuple[list[_Span], list[tuple]]:
    """Return the page's text pieces (with any hiding reasons) and their identity keys."""
    trace = page.get_texttrace()
    if not any(_span_text(raw).strip() for raw in trace):
        return [], []  # no text on this page (for example a scan without a text layer)
    img = _render(page)
    bare, leftover = _render_bare(page.parent, pno)
    fills = _Fills(page)
    prect = page.rect
    rotation = page.rotation_matrix
    spans: list[_Span] = []
    keys: list[tuple] = []
    for raw in trace:
        text = _span_text(raw)
        if not text.strip():
            continue
        bbox = pymupdf.Rect(raw["bbox"])
        keys.append(_key(text, bbox))
        first, last = _char_boxes(raw)
        span = _Span(page=pno, index=len(spans), text=text, bbox=bbox, size=float(raw["size"] or 1.0),
                     first=first, last=last)
        spans.append(span)
        size = float(raw["size"])
        opacity = float(raw.get("opacity", 1.0))
        mode = int(raw.get("type", 0))
        rgb = _to_rgb(raw.get("color"))

        if mode == 3:
            span.reasons["pdf.invisible_mode"] = {"render_mode": 3}
        if opacity <= 0.05:
            span.reasons["pdf.transparent"] = {"opacity": round(opacity, 2)}
        if size < TINY_PT:
            span.reasons["pdf.tiny"] = {"font_size_pt": round(size, 2)}
        elif size < SMALL_PT:
            span.reasons["pdf.small"] = {"font_size_pt": round(size, 2)}

        view = bbox * rotation  # where the text lands on the rendered (rotated, cropped) page
        span.view, span.where = view, _where(view, prect)
        outside = (view.x1 <= prect.x0 or view.x0 >= prect.x1 or view.y1 <= prect.y0 or view.y0 >= prect.y1)
        if outside:
            span.reasons["pdf.off_page"] = {"position": [round(v, 1) for v in view],
                                            "page_size": [round(prect.width, 1), round(prect.height, 1)]}
            continue
        visible_part = view & prect
        area = abs(view.width * view.height)
        if area > 0 and abs(visible_part.width * visible_part.height) / area < 0.5:
            span.reasons["pdf.partly_off_page"] = {"position": [round(v, 1) for v in view]}

        if mode != 3 and opacity > 0.05 and size >= SMALL_PT:
            usable_bare = None if keys[-1] in leftover else bare
            _pixel_checks(span, visible_part, img, usable_bare, rgb, int(raw.get("seqno", -1)), fills)
    return spans, keys


def _key(text: str, bbox: pymupdf.Rect) -> tuple:
    return (text, round(bbox.x0), round(bbox.y0), round(bbox.x1), round(bbox.y1))


def _image_coverage(page: pymupdf.Page) -> float:
    prect = page.rect
    page_area = prect.width * prect.height or 1.0
    covered = 0.0
    for info in page.get_image_info():
        r = pymupdf.Rect(info["bbox"]) & prect
        if not r.is_empty:
            covered += r.width * r.height
    return min(1.0, covered / page_area)


# ---------------------------------------------------------------- hidden layers

def _has_hidden_layers(doc: pymupdf.Document) -> bool:
    try:
        return any(not info.get("on", True) for info in (doc.get_ocgs() or {}).values())
    except Exception:
        return False


def _all_layers_on_copy(doc: pymupdf.Document) -> pymupdf.Document | None:
    """Open a copy of the PDF with every optional-content layer switched on."""
    try:
        copy = pymupdf.open("pdf", doc.tobytes())
        catalog = copy.pdf_catalog()
        copy.xref_set_key(catalog, "OCProperties/D/OFF", "[]")
        copy.xref_set_key(catalog, "OCProperties/D/BaseState", "/ON")
        return pymupdf.open("pdf", copy.tobytes())
    except Exception:
        return None


# ---------------------------------------------------------------- grouping into findings

def _primary(reasons: dict[str, Any]) -> str:
    for tid in PRIORITY:
        if tid in reasons:
            return tid
    return next(iter(reasons))


def _join(spans: list[_Span]) -> str:
    """Join text pieces the way a reader would: no space inside a word that changes font,
    a space between words, and words hyphenated at the end of a line put back together."""
    out = ""
    prev: _Span | None = None
    for span in spans:
        piece = span.text.strip()
        if not piece:
            continue
        if prev is None or not out:
            out = piece
        else:
            same_line = (prev.last is not None and span.first is not None
                         and span.first.y0 < prev.last.y1 and span.first.y1 > prev.last.y0)
            if same_line:
                gap = span.first.x0 - prev.last.x1  # type: ignore[union-attr]
                out += ("" if gap <= 0.15 * max(prev.size, span.size) else " ") + piece
            elif re.search(r"[A-Za-z]-$", out) and piece[:1].isalpha():
                out = out[:-1] + piece      # "IG-" + "NORE" -> "IGNORE"
            else:
                out += " " + piece
        prev = span
    return re.sub(r"\s+", " ", out).strip()


def _near(a: pymupdf.Rect, b: pymupdf.Rect) -> bool:
    """True when b is on the same line as a or on one of the next couple of lines."""
    line = max(a.height, b.height, 1.0)
    return b.y0 <= a.y1 + 2.5 * line and b.y1 >= a.y0 - 2.5 * line


def _group(spans: list[_Span], order_base: int) -> list[Finding]:
    groups: list[list[_Span]] = []
    for span in spans:
        if not span.reasons:
            continue
        prev = groups[-1][-1] if groups else None
        if (prev is not None and prev.page == span.page and prev.index + 1 == span.index
                and _primary(prev.reasons) == _primary(span.reasons) and _near(prev.bbox, span.bbox)):
            groups[-1].append(span)
        else:
            groups.append([span])
    findings = []
    for n, group in enumerate(groups):
        primary = _primary(group[0].reasons)
        all_ids: list[str] = []
        for s in group:
            for tid in s.reasons:
                if tid not in all_ids:
                    all_ids.append(tid)
        severity = max_severity(*(CATALOG[t].severity for t in all_ids))
        details = dict(group[0].reasons[primary])
        if len(group) > 1:
            details["pieces_of_text"] = len(group)
        also = [CATALOG[t].title for t in all_ids if t != primary]
        location = f"page {group[0].page + 1}" + (f", {group[0].where}" if group[0].where else "")
        findings.append(make_finding(
            primary, _join(group), location, severity=severity, details=details,
            also=also, page=group[0].page, rects=[[round(v, 2) for v in s.bbox] for s in group],
            order=order_base + n,
        ))
    return findings


# ---------------------------------------------------------------- main entry point

def scan_pdf(data: bytes) -> tuple[list[Finding], dict[str, Any], list[str]]:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - any parser error means "not a readable PDF"
        raise ScanError(f"this doesn't look like a readable PDF ({exc})") from exc
    if doc.needs_pass:
        raise ScanError("the PDF is password-protected; open it with the password and save an unlocked copy")
    if not doc.is_pdf:
        raise ScanError("this file is not a PDF")

    findings: list[Finding] = []
    notes: list[str] = []
    stats: dict[str, Any] = {"pages": doc.page_count, "text_pieces": 0, "characters": 0}
    seen_keys: set[tuple] = set()

    for pno in range(doc.page_count):
        page = doc[pno]
        order_base = (pno + 1) * 100_000
        spans, keys = _analyse_page(page, pno)
        seen_keys.update(keys)
        stats["text_pieces"] += len(spans)
        stats["characters"] += sum(len(s.text) for s in spans)

        # Scanned page? (big picture + mostly invisible text) -> one note instead of hundreds of hits.
        total_chars = sum(len(s.text) for s in spans)
        hidden_chars = sum(len(s.text) for s in spans
                           if s.reasons and set(s.reasons) <= OCR_LIKE)
        if (total_chars and hidden_chars / total_chars >= OCR_HIDDEN_SHARE
                and _image_coverage(page) >= OCR_IMAGE_COVERAGE):
            ocr_spans = [s for s in spans if s.reasons and set(s.reasons) <= OCR_LIKE]
            text = _join(ocr_spans)
            findings.append(make_finding(
                "pdf.ocr_layer", text if len(text) <= 600 else text[:600] + " …", f"page {pno + 1}",
                details={"characters_in_text_layer": len(text)}, page=pno, order=order_base,
            ))
            for s in ocr_spans:
                s.reasons = {}
        _attach_context(spans)
        findings.extend(_group(spans, order_base + 1))

        page_text = page.get_text("text")
        findings.extend(scan_invisibles(page_text, f"page {pno + 1}", order=order_base + 90_000))

        # Hidden annotations that still carry text.
        try:
            for annot in page.annots() or []:
                flags = annot.flags
                hidden = flags & (pymupdf.PDF_ANNOT_IS_HIDDEN | pymupdf.PDF_ANNOT_IS_NO_VIEW)
                content = (annot.info.get("content") or "").strip()
                if hidden and content:
                    findings.append(make_finding(
                        "pdf.hidden_annotation", content, f"page {pno + 1}", page=pno,
                        rects=[[round(v, 2) for v in annot.rect]], order=order_base + 95_000,
                        details={"annotation_type": annot.type[1]},
                    ))
        except Exception:  # noqa: BLE001 - damaged annotations shouldn't stop the scan
            pass

    # Text that only appears once switched-off layers are turned on.
    if _has_hidden_layers(doc):
        layered = _all_layers_on_copy(doc)
        if layered is not None:
            for pno in range(layered.page_count):
                lpage = layered[pno]
                hidden_spans: list[_Span] = []
                for raw in lpage.get_texttrace():
                    text = _span_text(raw)
                    if not text.strip():
                        continue
                    bbox = pymupdf.Rect(raw["bbox"])
                    if _key(text, bbox) in seen_keys:
                        continue
                    view = bbox * lpage.rotation_matrix
                    first, last = _char_boxes(raw)
                    hidden_spans.append(_Span(page=pno, index=len(hidden_spans), text=text, bbox=bbox,
                                              reasons={"pdf.hidden_layer": {"layer": raw.get("layer") or "(unnamed)"}},
                                              where=_where(view, lpage.rect), view=view,
                                              size=float(raw["size"] or 1.0), first=first, last=last))
                findings.extend(_group(hidden_spans, (pno + 1) * 100_000 + 50_000))
        names = [info.get("name") for info in doc.get_ocgs().values() if not info.get("on", True)]
        notes.append(f"The PDF has layers that are switched off: {', '.join(n or '(unnamed)' for n in names)}.")

    # Document properties that read like instructions to an AI.
    meta = doc.metadata or {}
    for key in ("title", "subject", "keywords", "author", "creator"):
        value = (meta.get(key) or "").strip()
        if value and score_text(value).score >= REPORT_THRESHOLD:
            findings.append(make_finding("pdf.metadata", value, f"document property '{key}'", order=10**9))
    try:
        xmp = doc.get_xml_metadata() or ""
    except Exception:  # noqa: BLE001
        xmp = ""
    xmp_text = re.sub(r"<[^>]+>", " ", xmp)
    xmp_text = re.sub(r"\s+", " ", xmp_text).strip()
    already = {f.text for f in findings if f.technique == "pdf.metadata"}
    if xmp_text and score_text(xmp_text).score >= REPORT_THRESHOLD and not any(a in xmp_text for a in already):
        findings.append(make_finding("pdf.metadata", xmp_text[:600], "XMP metadata", order=10**9 + 1))

    try:
        attachments = doc.embfile_names()
    except Exception:  # noqa: BLE001
        attachments = []
    if attachments:
        notes.append(f"The PDF has {len(attachments)} attached file(s): {', '.join(attachments[:10])}. "
                     "Attachments were not scanned.")
    findings.sort(key=lambda f: f.order)
    return findings, stats, notes

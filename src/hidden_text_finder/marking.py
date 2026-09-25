"""Make copies of documents with the hidden text pointed out.

* PDF: each hidden item gets a red numbered box and a note (click or hover to read the hidden
  text). Items outside the page get their note in the top-left corner. The original file
  is never changed.
* Word: hidden text is made visible (dark red on a yellow highlight, normal size) so you can
  read it in place.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from .docx_scan import reveal_docx
from .models import Finding, ScanResult

RED = (0.8, 0.0, 0.0)
MIN_BOX = 8.0
LABEL_SIZE = 7.0
# Tricks that leave the text (at least faintly) visible: box it, but don't paint over it.
VISIBLE_ENOUGH = {"pdf.faint", "pdf.partly_off_page", "pdf.small"}


def _reveal_box(page: pymupdf.Page, box: pymupdf.Rect, text: str) -> pymupdf.Rect:
    """Rectangle (unrotated page coordinates) big enough to print the hidden text readably.

    It stays inside the width of the hidden text itself (blank on the page, since the text is
    invisible) and wraps onto more lines, so it doesn't cover neighbouring columns.
    """
    prect = page.rect
    char_width = LABEL_SIZE * 0.52
    width = min(max(box.width, 140.0), prect.x1 - box.x0 - 8)
    if box.width < 140 and len(text) * char_width + 8 < width:
        width = max(box.width, len(text) * char_width + 8, 60.0)
    per_line = max(1, int((width - 6) / char_width))
    lines = min(10, -(-len(text) // per_line))
    height = max(box.height, lines * (LABEL_SIZE + 2.2) + 4)
    top = box.y0 if box.y0 + height < prect.y1 - 4 else prect.y1 - 4 - height
    return pymupdf.Rect(box.x0, top, box.x0 + width, top + height)


def _note(n: int, finding: Finding) -> str:
    return (f"Hidden Text Finder #{n}: {finding.title} ({finding.severity})\n\n"
            f"Hidden text:\n{finding.text}\n\n{finding.explanation}")


def mark_pdf(data: bytes, findings: list[Finding]) -> bytes:
    doc = pymupdf.open(stream=data, filetype="pdf")
    margin_slots: dict[int, int] = {}
    marked = 0
    for n, finding in enumerate(findings, start=1):
        if finding.page is None or finding.page >= doc.page_count:
            continue
        page = doc[finding.page]
        prect = page.rect
        rects = [pymupdf.Rect(r) for r in finding.rects]
        union = pymupdf.Rect(rects[0]) if rects else None
        for r in rects[1:]:
            union |= r
        view = union * page.rotation_matrix if union is not None else None
        on_page = view is not None and view.intersects(prect) and (view & prect).width > 0.1
        if on_page and union is not None:
            box = union + (-2, -2, 2, 2)
            if box.width < MIN_BOX:
                box.x1 = box.x0 + MIN_BOX
            if box.height < MIN_BOX:
                box.y1 = box.y0 + MIN_BOX
            paint_text = page.rotation == 0 and finding.technique not in VISIBLE_ENOUGH
            if paint_text:
                # Print the hidden text on top of where it hides, so the marked copy reads on its own.
                shown = f"#{n}  {finding.text}"
                if len(shown) > 300:
                    shown = shown[:297] + "..."
                box = _reveal_box(page, box, shown)
                label = page.add_freetext_annot(box, shown, fontsize=LABEL_SIZE, text_color=(0.55, 0, 0),
                                                fill_color=(1.0, 0.95, 0.6), border_width=0)
                label.set_info(title=f"Hidden Text Finder #{n}")  # a free-text box shows its content
                label.update()
            annot = page.add_rect_annot(box)
            annot.set_colors(stroke=RED)
            annot.set_border(width=1.2)
            annot.set_info(title="Hidden Text Finder", content=_note(n, finding))
            annot.update()
            if not paint_text:
                tag = page.add_freetext_annot(pymupdf.Rect(box.x0, box.y0 - 10, box.x0 + 24, box.y0),
                                              f"#{n}", fontsize=LABEL_SIZE, text_color=RED)
                tag.update()
            label_point = pymupdf.Point(box.x1 + 2, box.y0)
            if (label_point * page.rotation_matrix).x > prect.x1 - 20:
                label_point = pymupdf.Point(box.x0 - 20, box.y0)
        else:
            slot = margin_slots.get(finding.page, 0)
            margin_slots[finding.page] = slot + 1
            visual = pymupdf.Point(prect.x0 + 6, prect.y0 + 30 + slot * 20)
            label_point = visual * page.derotation_matrix
            if page.rotation == 0:
                where = "outside the page" if finding.rects else "on this page"
                shown = f"#{n} ({where}): {finding.text}"
                if len(shown) > 160:
                    shown = shown[:157] + "..."
                strip = pymupdf.Rect(prect.x0 + 26, visual.y, prect.x1 - 26, visual.y + 11)
                label = page.add_freetext_annot(strip, shown, fontsize=LABEL_SIZE, text_color=(0.55, 0, 0),
                                                fill_color=(1.0, 0.95, 0.6), border_width=0)
                label.set_info(title=f"Hidden Text Finder #{n}")
                label.update()
        text_annot = page.add_text_annot(label_point, _note(n, finding), icon="Note")
        text_annot.set_colors(stroke=RED)
        text_annot.set_info(title=f"Hidden Text Finder #{n}")
        text_annot.update()
        marked += 1

    if marked:
        first = doc[0]
        prect = first.rect
        banner_visual = pymupdf.Rect(prect.x0 + 30, prect.y0 + 4, prect.x1 - 30, prect.y0 + 22)
        banner = first.add_freetext_annot(
            banner_visual * first.derotation_matrix,
            f"Hidden Text Finder marked {marked} hidden item(s) with red boxes. Open the note icons to read them.",
            fontsize=8, text_color=RED, fill_color=(1.0, 0.97, 0.85), border_width=0.8,
            rotate=first.rotation,
        )
        banner.update()
    return doc.tobytes(garbage=1, deflate=True)


def marked_copy(data: bytes, result: ScanResult) -> tuple[bytes, str] | None:
    """Return (content, suggested file name) for a marked/revealed copy, or None if not applicable."""
    stem = Path(result.file_name).stem
    if all(f.severity == "info" for f in result.findings):
        return None  # only notes (such as a scanner's text layer): nothing worth marking
    if result.file_type == "pdf":
        if not any(f.page is not None for f in result.findings):
            return None
        return mark_pdf(data, result.findings), f"{stem}.marked.pdf"
    if result.file_type == "docx":
        if not any(f.details.get("runs") and f.technique != "docx.tracked_deletion" for f in result.findings):
            return None
        return reveal_docx(data, result.findings), f"{stem}.revealed.docx"
    return None

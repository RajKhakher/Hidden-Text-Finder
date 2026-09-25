"""Catalogue of hiding tricks the finder knows about, each with a plain-English explanation.

Keeping every explanation in one place means the command line, the HTML report and
the web app all describe a trick the same way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import Finding


@dataclass(frozen=True)
class Technique:
    id: str
    title: str
    severity: str
    explanation: str
    family: str  # "Text", "PDF", "Word", "Web page", "Markdown"


CATALOG: dict[str, Technique] = {}


def _add(tid: str, family: str, title: str, severity: str, explanation: str) -> None:
    CATALOG[tid] = Technique(tid, title, severity, explanation, family)


# ---------------------------------------------------------------- any text
_add("unicode.tag_smuggling", "Text", "Hidden message in invisible 'tag' characters", "high",
     "Unicode has a set of 'tag' characters that mirror the normal A-Z letters but are drawn as nothing "
     "at all. A sentence written with them is invisible on screen, yet many AI models read it like normal "
     "text. The finder decodes it so you can read it.")
_add("unicode.variation_selectors", "Text", "Message hidden in emoji 'variation selectors'", "high",
     "Variation selectors are invisible characters that normally tell a device which style of emoji to "
     "draw, one at a time. A long chain of them can secretly carry a whole message, often attached to a "
     "single emoji or letter. The finder decodes the chain.")
_add("unicode.zero_width", "Text", "Invisible (zero-width) characters", "medium",
     "Characters such as the 'zero-width space' take up no room, so you can't see them, but they are still "
     "part of the text a computer reads. They are used to hide messages, to secretly watermark text, or to "
     "split words so that filters miss them.")
_add("unicode.bidi", "Text", "Text-direction control characters", "medium",
     "These invisible characters switch the direction text is displayed in (they exist for Arabic and "
     "Hebrew). Misused, they make text appear in a different order from how it is actually stored, so what "
     "you see is not what a computer reads.")
_add("unicode.soft_hyphen", "Text", "Soft hyphens inside words", "low",
     "A soft hyphen is invisible unless a word breaks at the end of a line. Normal in typeset text, but "
     "also used to split words so keyword filters miss them.")

# ---------------------------------------------------------------- PDF
_add("pdf.same_colour", "PDF", "Text the same colour as its background", "high",
     "The text is drawn in the same colour as whatever is behind it (for example white text on a white "
     "page), so it looks like empty space. Copy-paste and AI tools read the letters, not the colours, so "
     "they still see it.")
_add("pdf.faint", "PDF", "Very faint text", "medium",
     "The text colour is so close to the background colour that it is hard or impossible to see at normal "
     "zoom. (Light watermarks also land here; those are usually harmless.)")
_add("pdf.covered", "PDF", "Text hidden under a shape or picture", "high",
     "The text is on the page, but something is drawn on top of it (such as a white box or an image), so "
     "you can't see it. Text extraction ignores whatever is drawn on top, so AI tools still read it.")
_add("pdf.invisible_mode", "PDF", "Text drawn in 'invisible' mode", "high",
     "PDFs have a setting that tells the viewer to draw text as nothing. Scanners use it on purpose to make "
     "scanned pages searchable; anywhere else it is a red flag.")
_add("pdf.transparent", "PDF", "Fully transparent text", "high",
     "The text is drawn at 0% opacity, like writing with clear ink.")
_add("pdf.tiny", "PDF", "Microscopic text", "high",
     "The font is under 2 points (normal text is 10-12), so a whole sentence shrinks to a speck or a thin "
     "line, if it shows at all.")
_add("pdf.small", "PDF", "Very small text", "medium",
     "The font is 2-3 points, too small to read without zooming right in.")
_add("pdf.off_page", "PDF", "Text placed outside the page", "high",
     "The text is positioned beyond the edges of the visible page, so no viewer shows it, but it is still "
     "stored in the file.")
_add("pdf.partly_off_page", "PDF", "Text running off the edge of the page", "medium",
     "Most of this text sits outside the visible page area.")
_add("pdf.hidden_layer", "PDF", "Text in a switched-off layer", "high",
     "PDFs can have layers that can be turned on and off, like in Photoshop. This text is in a layer that "
     "is off, so viewers don't show it, but common text extractors (including ones AI apps use) still "
     "pull it out.")
_add("pdf.ocr_layer", "PDF", "Invisible scanner text layer", "info",
     "This page looks like a scanned image with an invisible text layer on top, which scanners add so you "
     "can search and copy the text. That is normal. The finder can't check that the text layer matches the "
     "picture, so give it a quick look if the document matters.")
_add("pdf.hidden_annotation", "PDF", "Hidden annotation", "medium",
     "An annotation (a comment or note) is set to 'hidden', so viewers don't show it, but its text is "
     "stored in the file.")
_add("pdf.metadata", "PDF", "Instruction-like text in document properties", "medium",
     "The document's properties (title, subject, keywords...) aren't shown on the page, but some AI tools "
     "read them.")

# ---------------------------------------------------------------- Word
_add("docx.hidden_font", "Word", "Text marked 'Hidden' in Word", "high",
     "Word has a 'Hidden' font setting. Hidden text isn't shown or printed unless someone turns on 'Show "
     "hidden text', but tools that pull text out of the file, including many AI tools, still read it.")
_add("docx.same_colour", "Word", "Text the same colour as its background", "high",
     "The text colour matches its background (for example white text on a white page), so it looks like "
     "empty space.")
_add("docx.faint", "Word", "Very faint text", "medium",
     "The text colour is so close to the background colour that it is hard or impossible to see.")
_add("docx.tiny", "Word", "Microscopic text", "high",
     "The font size is under 2 points (normal text is 10-12), so it shows up as a speck or a thin line.")
_add("docx.small", "Word", "Very small text", "medium",
     "The font size is 2-3 points, too small to read without zooming right in.")
_add("docx.squeezed", "Word", "Squeezed text", "high",
     "The characters are squashed to a small fraction of their normal width, so a whole sentence shrinks "
     "to a thin line.")
_add("docx.web_hidden", "Word", "Text hidden in web view", "medium",
     "Marked to be hidden when the document is shown as a web page.")
_add("docx.tracked_deletion", "Word", "Deleted text still stored in the file", "low",
     "With Track Changes on, deleted text stays inside the file. Word only shows it in review mode, but "
     "some text extractors include it.")
_add("docx.comment", "Word", "Reviewer comment", "info",
     "Comments appear in Word's margin, not in the body text, but they are stored in the file and some "
     "tools read them.")
_add("docx.alt_text", "Word", "Instruction-like picture description", "medium",
     "Pictures can carry a description ('alt text') for screen readers. It isn't shown on the page, but AI "
     "tools often read it.")
_add("docx.metadata", "Word", "Instruction-like text in document properties", "medium",
     "The document's properties (title, subject, keywords...) aren't shown on the page, but some AI tools "
     "read them.")

# ---------------------------------------------------------------- web pages
_add("html.display_none", "Web page", "Hidden with CSS 'display: none'", "high",
     "The page's styling tells the browser not to draw this part at all. It's still in the page's code, "
     "which scrapers and AI tools read.")
_add("html.visibility_hidden", "Web page", "Hidden with CSS 'visibility: hidden'", "high",
     "The element keeps its space on the page but is drawn invisibly.")
_add("html.opacity_zero", "Web page", "Fully transparent", "high",
     "The element (or its text colour) is fully see-through.")
_add("html.tiny_font", "Web page", "Zero or microscopic font size", "high",
     "The font size is 0 or a couple of pixels, so the text has no visible size.")
_add("html.small_font", "Web page", "Very small font size", "medium",
     "The font is only a few pixels tall, too small to read normally.")
_add("html.same_colour", "Web page", "Text the same colour as its background", "high",
     "The text colour matches the background colour, so it blends in.")
_add("html.faint", "Web page", "Very faint text", "medium",
     "The text colour is so close to the background colour that it is hard to see.")
_add("html.off_screen", "Web page", "Moved off-screen", "high",
     "The element is pushed thousands of pixels past the edge of the page, where nobody scrolls.")
_add("html.clipped", "Web page", "Clipped or shrunk to nothing", "high",
     "The element is cut down to zero size (for example width 0 with overflow hidden, a zero-size clip, "
     "or scale(0)), so nothing shows.")
_add("html.hidden_attribute", "Web page", "Hidden with the 'hidden' attribute", "high",
     "HTML's built-in 'hidden' attribute stops the browser from showing the element.")
_add("html.screen_reader_only", "Web page", "Screen-reader-only text", "low",
     "This text is visually hidden but kept for screen readers used by blind and low-vision people. That "
     "is usually legitimate accessibility work, so just check the wording.")
_add("html.template", "Web page", "Unrendered template content", "medium",
     "Content inside a <template> tag is never shown unless a script inserts it into the page.")
_add("html.comment", "Web page", "Hidden comment", "low",
     "Comments (<!-- like this -->) never appear on the page, but they're part of the file that scrapers "
     "and AI tools read.")
_add("html.attribute", "Web page", "Instruction-like text in an attribute", "medium",
     "Text in attributes such as alt, title or aria-label, or in meta tags, isn't shown as normal page "
     "text, but AI tools often read it.")

# ---------------------------------------------------------------- Markdown
_add("md.comment", "Markdown", "Hidden Markdown comment", "low",
     "Lines like [//]: # (...) are a Markdown trick for comments: they never appear when the Markdown is "
     "displayed, but they're in the file.")


def make_finding(technique_id: str, text: str, location: str, *, severity: str | None = None,
                 details: dict[str, Any] | None = None, also: list[str] | None = None,
                 page: int | None = None, rects: list[list[float]] | None = None,
                 order: int = 0) -> Finding:
    tech = CATALOG[technique_id]
    return Finding(
        technique=technique_id,
        title=tech.title,
        severity=severity or tech.severity,
        text=text,
        location=location,
        explanation=tech.explanation,
        also=list(also or []),
        details=dict(details or {}),
        page=page,
        rects=list(rects or []),
        order=order,
    )

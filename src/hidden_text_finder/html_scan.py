"""Web page (HTML) scanner.

The page is parsed like a (very small) browser would: it reads the CSS in <style> blocks and
in style="..." attributes, works out which rules apply to which element, and then decides for
every piece of text whether a person would actually see it.

There are two kinds of hiding:

* Hiding a whole element and everything inside it (display: none, the hidden attribute,
  opacity 0, moving it off-screen, clipping it to zero size). Nothing inside can escape.
* Hiding at the level of the text (white text on a white background, a font size of 0,
  visibility: hidden). Inner elements can undo these, so each piece of text is judged using
  the styles that apply to it.

Limits: external stylesheets and JavaScript are not loaded, and only simple CSS is understood.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

import tinycss2
from bs4 import BeautifulSoup, Comment, Doctype, NavigableString, Tag
from bs4.element import CData, Declaration, ProcessingInstruction
from tinycss2 import color3

from .ai_score import REPORT_THRESHOLD, score_text
from .models import Finding
from .pdf_scan import contrast_ratio
from .techniques import make_finding
from .unicode_scan import scan_invisibles

SKIP_TEXT_IN = {"script", "style", "noscript", "head", "title", "meta", "link", "svg", "math", "iframe", "object"}
SR_ONLY_CLASSES = {"sr-only", "visually-hidden", "visuallyhidden", "screen-reader-text", "sr_only",
                   "screenreader-only", "a11y-hidden", "u-hidden-visually", "hidden-accessible",
                   "element-invisible", "screen-reader-only", "assistive-text", "vh"}
TINY_PX = 3.0
SMALL_PX = 5.0
SAME_COLOUR_CONTRAST = 1.12
FAINT_CONTRAST = 1.6
FONT_KEYWORDS = {"xx-small": 9, "x-small": 10, "small": 13, "medium": 16, "large": 18, "x-large": 24,
                 "xx-large": 32, "xxx-large": 48}
ATTRIBUTES_TO_CHECK = ("alt", "title", "aria-label", "aria-description", "placeholder", "summary")
META_NAMES = {"description", "keywords", "author", "subject", "abstract", "og:description", "og:title",
              "twitter:description", "twitter:title", "citation_abstract", "dc.description"}

Colour = tuple[float, float, float, float]


# ---------------------------------------------------------------- CSS helpers

def _declarations(source: Any) -> list[tuple[str, str, bool]]:
    out = []
    for decl in tinycss2.parse_declaration_list(source, skip_comments=True, skip_whitespace=True):
        if decl.type == "declaration":
            out.append((decl.lower_name, tinycss2.serialize(decl.value).strip().lower(), bool(decl.important)))
    return out


def _split_selectors(prelude: str) -> list[str]:
    parts, depth, current = [], 0, ""
    for ch in prelude:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += ch
    if current.strip():
        parts.append(current.strip())
    return parts


def _specificity(selector: str) -> tuple[int, int, int]:
    no_strings = re.sub(r"\"[^\"]*\"|'[^']*'", "", selector)
    ids = len(re.findall(r"#[\w-]+", no_strings))
    classes = len(re.findall(r"\.[\w-]+|\[[^\]]*\]|:(?!:)[\w-]+", no_strings))
    types = len(re.findall(r"(?:^|[\s>+~])([a-zA-Z][\w-]*)", no_strings))
    return ids, classes, types


def _stylesheet_rules(css: str) -> list[tuple[str, list[tuple[str, str, bool]]]]:
    rules = []

    def walk(items: list[Any]) -> None:
        for rule in items:
            if rule.type == "qualified-rule":
                rules.append((tinycss2.serialize(rule.prelude).strip(), _declarations(rule.content)))
            elif rule.type == "at-rule" and rule.lower_at_keyword in ("media", "supports", "layer") and rule.content:
                condition = tinycss2.serialize(rule.prelude).lower()
                if rule.lower_at_keyword == "media" and "print" in condition and "screen" not in condition:
                    continue
                walk(tinycss2.parse_rule_list(rule.content, skip_comments=True, skip_whitespace=True))

    walk(tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True))
    return rules


def _px(value: str, font_px: float, *, percent_of: float | None = None) -> float | None:
    """Convert a CSS length to pixels (None if we can't tell)."""
    value = value.strip().split()[0] if value.strip() else ""
    if value in ("0", "-0", "+0"):
        return 0.0
    match = re.fullmatch(r"([+-]?\d*\.?\d+)(px|pt|em|rem|%|pc|in|cm|mm|ex|ch|vw|vh)?", value)
    if not match:
        return None
    number, unit = float(match.group(1)), match.group(2) or "px"
    factors = {"px": 1, "pt": 4 / 3, "pc": 16, "in": 96, "cm": 96 / 2.54, "mm": 96 / 25.4}
    if unit in factors:
        return number * factors[unit]
    if unit in ("em", "ex", "ch"):
        return number * font_px * (1 if unit == "em" else 0.5)
    if unit == "rem":
        return number * 16
    if unit == "%":
        return None if percent_of is None else number * percent_of / 100
    if unit == "vw":
        return number * 12.8  # assume a 1280 px wide window
    if unit == "vh":
        return number * 8.0   # assume an 800 px tall window
    return None


def _font_px(value: str, parent_px: float) -> float | None:
    value = value.strip()
    if value in FONT_KEYWORDS:
        return float(FONT_KEYWORDS[value])
    if value == "smaller":
        return parent_px * 0.83
    if value == "larger":
        return parent_px * 1.2
    return _px(value, parent_px, percent_of=parent_px)


def _colour(value: str) -> Colour | None | str:
    try:
        parsed = color3.parse_color(value)
    except Exception:  # noqa: BLE001
        return None
    if parsed == "currentColor":
        return "currentColor"
    if parsed is None:
        return None
    return (parsed.red, parsed.green, parsed.blue, parsed.alpha)


def _background(style: dict[str, str]) -> Colour | None | str:
    """Solid background colour, 'image' when a picture is used, or None if not set."""
    value = style.get("background-color")
    shorthand = style.get("background", "")
    if "url(" in shorthand or "gradient(" in shorthand or "url(" in style.get("background-image", ""):
        return "image"
    candidates = [value] if value else []
    if shorthand:
        for token in tinycss2.parse_component_value_list(shorthand):
            if token.type in ("ident", "hash", "function"):
                candidates.append(tinycss2.serialize([token]))
    for candidate in candidates:
        colour = _colour(candidate)
        if isinstance(colour, tuple):
            return colour
    return None


# ---------------------------------------------------------------- style computation

@dataclass
class _Ctx:
    hide: tuple[str, Tag, str] | None = None   # (technique, element that hides, css that does it)
    visibility_hidden: str | None = None       # css text when visibility is hidden
    font_px: float = 16.0
    font_css: str = ""
    colour: Colour = (0.0, 0.0, 0.0, 1.0)
    background: tuple[float, float, float] | None = (1.0, 1.0, 1.0)
    opacity: float = 1.0


def _is_sr_only(el: Tag, style: dict[str, str]) -> bool:
    classes = {c.lower() for c in el.get("class", [])}
    if classes & SR_ONLY_CLASSES:
        return True
    sizes = [_px(style.get(p, "auto"), 16) for p in ("width", "height")]
    tiny_box = all(size is not None and size <= 1 for size in sizes)
    return style.get("position") == "absolute" and tiny_box and style.get("overflow") == "hidden"


def _element_hiding(el: Tag, style: dict[str, str], ctx: _Ctx) -> tuple[str, str] | None:
    """Ways of hiding an element together with everything inside it."""
    if el.has_attr("hidden"):
        return "html.hidden_attribute", "hidden attribute"
    if style.get("display") == "none":
        return "html.display_none", "display: none"
    if el.name == "template":
        return "html.template", "<template> element"
    if el.name == "dialog" and not el.has_attr("open"):
        return "html.display_none", "closed <dialog> element"
    opacity = style.get("opacity")
    if opacity:
        try:
            value = float(opacity.rstrip("%")) / (100 if opacity.endswith("%") else 1)
            if ctx.opacity * value <= 0.05:
                return "html.opacity_zero", f"opacity: {opacity}"
        except ValueError:
            pass
    font = ctx.font_px
    position = style.get("position", "static")
    if position in ("absolute", "fixed", "relative"):
        for prop in ("left", "top", "right"):
            if prop in style:
                px = _px(style[prop], font)
                pct = re.fullmatch(r"(-?\d+(?:\.\d+)?)%", style[prop])
                if (px is not None and (px <= -500 or (prop == "left" and px >= 5000))) or (pct and float(pct.group(1)) <= -100):
                    return "html.off_screen", f"position: {position}; {prop}: {style[prop]}"
    for prop in ("margin-left", "margin-top"):
        px = _px(style.get(prop, ""), font) if prop in style else None
        if px is not None and px <= -1000:
            return "html.off_screen", f"{prop}: {style[prop]}"
    transform = style.get("transform", "")
    translate = re.search(r"translate[xy]?\(\s*(-?\d+(?:\.\d+)?)(px|em|rem|%)", transform)
    if translate and float(translate.group(1)) <= (-100 if translate.group(2) == "%" else -1000):
        return "html.off_screen", f"transform: {transform}"
    if re.search(r"scale[xy]?\(\s*0*\.?0+\s*[,)]", transform) or re.search(r"matrix\(\s*0\s*,\s*0\s*,\s*0\s*,\s*0", transform):
        return "html.clipped", f"transform: {transform}"
    clip = style.get("clip", "")
    if re.fullmatch(r"rect\(\s*(0|1)(px)?\s*,?\s*(0|1)(px)?\s*,?\s*(0|1)(px)?\s*,?\s*(0|1)(px)?\s*\)", clip):
        return "html.clipped", f"clip: {clip}"
    clip_path = style.get("clip-path", "")
    if re.search(r"inset\(\s*(50|100)%|circle\(\s*0|polygon\(\s*0(px)?\s+0(px)?\s*(,\s*0(px)?\s+0(px)?\s*)*\)", clip_path):
        return "html.clipped", f"clip-path: {clip_path}"
    if style.get("overflow") in ("hidden", "clip"):
        for prop in ("width", "height", "max-width", "max-height"):
            px = _px(style[prop], font) if prop in style else None
            if px is not None and px <= 1:
                return "html.clipped", f"{prop}: {style[prop]}; overflow: {style['overflow']}"
    indent = _px(style.get("text-indent", ""), font) if "text-indent" in style else None
    if indent is not None and indent <= -500:
        return "html.off_screen", f"text-indent: {style['text-indent']}"
    return None


def _compute_styles(soup: BeautifulSoup) -> dict[int, dict[str, str]]:
    declared: dict[int, list[tuple[tuple, str, str]]] = {}
    order = 0
    css_text = "\n".join(tag.get_text() for tag in soup.find_all("style"))
    for prelude, decls in _stylesheet_rules(css_text):
        for selector in _split_selectors(prelude):
            try:
                matches = soup.select(selector)
            except Exception:  # noqa: BLE001 - pseudo-elements and exotic selectors aren't supported
                continue
            spec = _specificity(selector)
            for el in matches:
                for name, value, important in decls:
                    declared.setdefault(id(el), []).append(((important, 0, spec, order), name, value))
                    order += 1
    for el in soup.find_all(style=True):
        for name, value, important in _declarations(el.get("style", "")):
            declared.setdefault(id(el), []).append(((important, 1, (0, 0, 0), order), name, value))
            order += 1
    computed: dict[int, dict[str, str]] = {}
    for key, entries in declared.items():
        style: dict[str, str] = {}
        for _, name, value in sorted(entries, key=lambda e: e[0]):
            style[name] = value
        computed[key] = style
    return computed


def _contexts(soup: BeautifulSoup, styles: dict[int, dict[str, str]]) -> dict[int, _Ctx]:
    contexts: dict[int, _Ctx] = {}
    root_ctx = _Ctx()
    stack: list[tuple[Tag, _Ctx]] = [(soup, root_ctx)]
    while stack:
        el, parent_ctx = stack.pop()
        style = styles.get(id(el), {}) if el is not soup else {}
        ctx = replace(parent_ctx)
        if el is not soup:
            if ctx.hide is None:
                hidden = _element_hiding(el, style, parent_ctx)
                if hidden:
                    technique, css = hidden
                    if technique in ("html.clipped", "html.off_screen") and _is_sr_only(el, style):
                        technique = "html.screen_reader_only"
                    ctx.hide = (technique, el, css)
            visibility = style.get("visibility")
            if visibility in ("hidden", "collapse"):
                ctx.visibility_hidden = f"visibility: {visibility}"
            elif visibility == "visible":
                ctx.visibility_hidden = None
            if "font-size" in style:
                px = _font_px(style["font-size"], parent_ctx.font_px)
                if px is not None:
                    ctx.font_px, ctx.font_css = px, f"font-size: {style['font-size']}"
            if "color" in style:
                colour = _colour(style["color"])
                if isinstance(colour, tuple):
                    ctx.colour = colour
            background = _background(style)
            if background == "image":
                ctx.background = None
            elif isinstance(background, tuple) and background[3] > 0.05:
                base = parent_ctx.background or (1.0, 1.0, 1.0)
                a = background[3]
                ctx.background = tuple(background[i] * a + base[i] * (1 - a) for i in range(3))  # type: ignore[assignment]
            if "opacity" in style:
                try:
                    op = style["opacity"]
                    ctx.opacity *= float(op.rstrip("%")) / (100 if op.endswith("%") else 1)
                except ValueError:
                    pass
        contexts[id(el)] = ctx
        for child in reversed([c for c in el.children if isinstance(c, Tag)]):
            stack.append((child, ctx))
    return contexts


def _describe(el: Tag) -> str:
    label = f"<{el.name}"
    if el.get("id"):
        label += f" id=\"{el['id']}\""
    if el.get("class"):
        label += f" class=\"{' '.join(el['class'])}\""
    label += ">"
    if getattr(el, "sourceline", None):
        label += f" on line {el.sourceline}"
    return label


def _text_level(ctx: _Ctx) -> tuple[str, dict[str, Any]] | None:
    if ctx.visibility_hidden:
        return "html.visibility_hidden", {"css": ctx.visibility_hidden}
    if ctx.font_px < TINY_PX:
        return "html.tiny_font", {"css": ctx.font_css, "font_size_px": round(ctx.font_px, 2)}
    if ctx.colour[3] <= 0.05:
        return "html.opacity_zero", {"css": "transparent text colour"}
    if ctx.background is not None:
        a = ctx.colour[3]
        text = tuple(ctx.colour[i] * a + ctx.background[i] * (1 - a) for i in range(3))
        ratio = contrast_ratio(text, ctx.background)  # type: ignore[arg-type]
        details = {"text_colour": _hex(text), "background_colour": _hex(ctx.background),
                   "contrast_ratio": round(ratio, 2)}
        if ratio < SAME_COLOUR_CONTRAST:
            return "html.same_colour", details
        if ratio < FAINT_CONTRAST:
            return "html.faint", details
    if ctx.font_px < SMALL_PX:
        return "html.small_font", {"css": ctx.font_css, "font_size_px": round(ctx.font_px, 2)}
    return None


def _hex(rgb: tuple[float, ...]) -> str:
    return "#" + "".join(f"{int(round(max(0.0, min(1.0, v)) * 255)):02x}" for v in rgb[:3])


def _skip_text(node: NavigableString) -> bool:
    if isinstance(node, (Comment, Doctype, CData, Declaration, ProcessingInstruction)):
        return True
    parent = node.parent
    while parent is not None:
        if parent.name in SKIP_TEXT_IN:
            return True
        parent = parent.parent
    return False


# ---------------------------------------------------------------- main entry point

def scan_html(source: str, *, unicode: bool = True, order_base: int = 0) -> tuple[list[Finding], dict[str, Any], list[str]]:
    soup = BeautifulSoup(source, "html.parser")
    styles = _compute_styles(soup)
    contexts = _contexts(soup, styles)
    findings: list[Finding] = []
    notes: list[str] = []
    stats = {"elements": len(soup.find_all(True)), "text_nodes": 0, "characters": 0}

    groups: list[dict[str, Any]] = []
    order = order_base
    for node in soup.descendants:
        if not isinstance(node, NavigableString) or _skip_text(node):
            continue
        text = str(node)
        if not text.strip():
            continue
        stats["text_nodes"] += 1
        stats["characters"] += len(text)
        order += 1
        parent = node.parent
        ctx = contexts.get(id(parent), _Ctx())
        if ctx.hide:
            technique, root, css = ctx.hide
            key = (technique, id(root))
            details = {"css": css, "element": _describe(root)}
            location = _describe(root)
        else:
            level = _text_level(ctx)
            if level is None:
                groups.append({"visible": True})
                if unicode:
                    findings.extend(scan_invisibles(text, _describe(parent), order=order))
                continue
            technique, details = level
            key = (technique, None)
            details = dict(details, element=_describe(parent))
            location = _describe(parent)
        if unicode:
            findings.extend(scan_invisibles(text, _describe(parent), order=order))
        last = groups[-1] if groups else None
        if last and not last.get("visible") and last["key"] == key:
            last["parts"].append(text)
        else:
            groups.append({"key": key, "technique": technique, "parts": [text], "details": details,
                           "location": location, "order": order})

    for group in groups:
        if group.get("visible"):
            continue
        text = re.sub(r"\s+", " ", " ".join(group["parts"])).strip()
        findings.append(make_finding(group["technique"], text, group["location"], details=group["details"],
                                     order=group["order"]))

    # Comments: instruction-like ones get their own finding, the rest are summarised.
    plain_comments = []
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        text = str(comment).strip()
        if not text or text.startswith("[if") or text.startswith("<![endif]"):
            continue
        parent = comment.parent if isinstance(comment.parent, Tag) else None
        location = f"comment inside {_describe(parent)}" if parent is not None and parent.name != "[document]" else "comment"
        if score_text(text).score >= REPORT_THRESHOLD:
            findings.append(make_finding("html.comment", text, location, order=order_base + 10**6))
        else:
            plain_comments.append(text)
    if plain_comments:
        preview = " | ".join(c[:120] for c in plain_comments[:5])
        findings.append(make_finding(
            "html.comment", preview, f"{len(plain_comments)} comment(s) in the page source",
            severity="info" if all(len(c) < 40 for c in plain_comments) else "low",
            details={"comments": len(plain_comments)}, order=order_base + 10**6 + 1,
        ))

    # Attributes and meta tags that read like instructions.
    for el in soup.find_all(True):
        values: list[tuple[str, str]] = []
        for attr in ATTRIBUTES_TO_CHECK:
            if el.get(attr):
                values.append((attr, str(el.get(attr))))
        if el.name == "meta" and (el.get("name", "").lower() in META_NAMES or el.get("property", "").lower() in META_NAMES):
            values.append((f"meta {el.get('name') or el.get('property')}", str(el.get("content", ""))))
        if el.name == "input" and el.get("type", "").lower() == "hidden" and el.get("value"):
            values.append(("hidden input value", str(el.get("value"))))
        for attr, value in values:
            if value.strip() and score_text(value).score >= REPORT_THRESHOLD:
                findings.append(make_finding("html.attribute", value.strip(), f"{attr} of {_describe(el)}",
                                             order=order_base + 2 * 10**6))

    links = [tag.get("href") for tag in soup.find_all("link", rel=lambda r: r and "stylesheet" in r)]
    if links:
        notes.append(f"The page loads {len(links)} external stylesheet(s); those weren't downloaded, so "
                     "text hidden only by them can be missed.")
    if soup.find("script"):
        notes.append("The page contains scripts; scripts can change what is shown and weren't run.")
    findings.sort(key=lambda f: f.order)
    return findings, stats, notes

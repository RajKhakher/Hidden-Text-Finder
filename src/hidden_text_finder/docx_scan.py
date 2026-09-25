"""Word (.docx) scanner.

A .docx file is a zip folder of XML files. The body text lives in word/document.xml as
paragraphs (<w:p>) made of "runs" (<w:r>): stretches of text that share the same formatting.
Formatting can be set in four layers, and the later ones win:

    document defaults  ->  paragraph style  ->  character style  ->  the run's own settings

Someone hiding text can do it at any layer (for example a paragraph style called "Normal"
that quietly makes text white), so the scanner works out the final formatting of every run
before checking it.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any

from lxml import etree

from .ai_score import REPORT_THRESHOLD, score_text
from .models import Finding, ScanError, max_severity
from .pdf_scan import contrast_ratio
from .techniques import CATALOG, make_finding
from .unicode_scan import scan_invisibles

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = "{%s}" % W_NS
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"

HIGHLIGHT_COLOURS = {
    "yellow": "FFFF00", "green": "00FF00", "cyan": "00FFFF", "magenta": "FF00FF", "blue": "0000FF",
    "red": "FF0000", "darkBlue": "000080", "darkCyan": "008080", "darkGreen": "008000",
    "darkMagenta": "800080", "darkRed": "800000", "darkYellow": "808000", "darkGray": "808080",
    "lightGray": "C0C0C0", "black": "000000", "white": "FFFFFF",
}
THEME_DEFAULTS = {"background1": "FFFFFF", "bg1": "FFFFFF", "light1": "FFFFFF",
                  "text1": "000000", "tx1": "000000", "dark1": "000000"}

SAME_COLOUR_CONTRAST = 1.12
FAINT_CONTRAST = 1.6
PRIORITY = ["docx.hidden_font", "docx.same_colour", "docx.squeezed", "docx.tiny", "docx.small",
            "docx.web_hidden", "docx.faint", "docx.tracked_deletion"]


def _local(tag: Any) -> str:
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def _on(el: etree._Element) -> bool:
    """Word on/off switches: <w:vanish/> means on; w:val="0"/"false"/"off" means off."""
    return el.get(W + "val", "true").lower() not in ("0", "false", "off")


def _hex_to_rgb(value: str | None) -> tuple[float, float, float] | None:
    if not value or not re.fullmatch(r"[0-9A-Fa-f]{6}", value):
        return None
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _apply_rpr(props: dict[str, Any], rpr: etree._Element | None) -> None:
    if rpr is None:
        return
    for child in rpr:
        name = _local(child.tag)
        if name in ("vanish", "webHidden", "specVanish"):
            props[name] = _on(child)
        elif name == "color":
            val = child.get(W + "val")
            theme = child.get(W + "themeColor")
            if val and val.lower() != "auto":
                props["color"] = val
            elif theme in THEME_DEFAULTS:
                props["color"] = THEME_DEFAULTS[theme]
            elif val and val.lower() == "auto":
                props["color"] = "auto"
        elif name == "sz":
            try:
                props["sz"] = int(child.get(W + "val", "0"))
            except ValueError:
                pass
        elif name == "highlight":
            props["highlight"] = child.get(W + "val")
        elif name == "shd":
            fill = child.get(W + "fill")
            if fill and fill.lower() != "auto":
                props["shd"] = fill
        elif name == "w":
            try:
                props["scale"] = int(child.get(W + "val", "100"))
            except ValueError:
                pass


class _Styles:
    def __init__(self, xml: bytes | None):
        self.styles: dict[str, etree._Element] = {}
        self.default_paragraph: str | None = None
        self.defaults_rpr: etree._Element | None = None
        if not xml:
            return
        root = etree.fromstring(xml)
        self.defaults_rpr = root.find(f"{W}docDefaults/{W}rPrDefault/{W}rPr")
        for style in root.iter(W + "style"):
            sid = style.get(W + "styleId")
            if not sid:
                continue
            self.styles[sid] = style
            if style.get(W + "type") == "paragraph" and style.get(W + "default") in ("1", "true"):
                self.default_paragraph = sid

    def chain(self, sid: str | None) -> list[etree._Element]:
        out: list[etree._Element] = []
        seen: set[str] = set()
        while sid and sid in self.styles and sid not in seen:
            seen.add(sid)
            style = self.styles[sid]
            out.append(style)
            based = style.find(W + "basedOn")
            sid = based.get(W + "val") if based is not None else None
        return list(reversed(out))

    def run_props(self, run: etree._Element, para: etree._Element | None) -> dict[str, Any]:
        props: dict[str, Any] = {}
        _apply_rpr(props, self.defaults_rpr)
        pstyle = None
        if para is not None:
            ps = para.find(f"{W}pPr/{W}pStyle")
            pstyle = ps.get(W + "val") if ps is not None else self.default_paragraph
        for style in self.chain(pstyle):
            _apply_rpr(props, style.find(W + "rPr"))
        rpr = run.find(W + "rPr")
        rstyle = rpr.find(W + "rStyle") if rpr is not None else None
        if rstyle is not None:
            for style in self.chain(rstyle.get(W + "val")):
                _apply_rpr(props, style.find(W + "rPr"))
        if rpr is not None:
            direct = etree.Element(W + "rPr")
            for child in rpr:
                if _local(child.tag) != "rPrChange":  # formatting history, not current formatting
                    direct.append(_shallow_copy(child))
            _apply_rpr(props, direct)
        return props

    def paragraph_shading(self, para: etree._Element | None) -> str | None:
        if para is None:
            return None
        fill = None
        ps = para.find(f"{W}pPr/{W}pStyle")
        for style in self.chain(ps.get(W + "val") if ps is not None else self.default_paragraph):
            shd = style.find(f"{W}pPr/{W}shd")
            if shd is not None and (shd.get(W + "fill") or "auto").lower() != "auto":
                fill = shd.get(W + "fill")
        shd = para.find(f"{W}pPr/{W}shd")
        if shd is not None and (shd.get(W + "fill") or "auto").lower() != "auto":
            fill = shd.get(W + "fill")
        return fill


def _shallow_copy(el: etree._Element) -> etree._Element:
    copy = etree.Element(el.tag, attrib=dict(el.attrib))
    return copy


def _ancestor(el: etree._Element, localname: str) -> etree._Element | None:
    parent = el.getparent()
    while parent is not None:
        if _local(parent.tag) == localname:
            return parent
        parent = parent.getparent()
    return None


def _in_fallback(el: etree._Element) -> bool:
    parent = el.getparent()
    while parent is not None:
        if parent.tag == "{%s}Fallback" % MC_NS:
            return True
        parent = parent.getparent()
    return False


def _run_text(run: etree._Element) -> str:
    parts = []
    for child in run:
        name = _local(child.tag)
        if name in ("t", "delText"):
            parts.append(child.text or "")
        elif name == "tab":
            parts.append("\t")
        elif name in ("br", "cr"):
            parts.append("\n")
        elif name == "noBreakHyphen":
            parts.append("-")
    return "".join(parts)


@dataclass
class _Run:
    part: str
    run_index: int
    para_key: tuple[str, int]
    seq: int                       # position among non-blank runs, for grouping neighbours
    text: str
    reasons: dict[str, dict[str, Any]] = field(default_factory=dict)


def _check_run(run: etree._Element, para: etree._Element | None, styles: _Styles,
               page_bg: str) -> dict[str, dict[str, Any]]:
    props = styles.run_props(run, para)
    reasons: dict[str, dict[str, Any]] = {}
    if props.get("vanish") or props.get("specVanish"):
        reasons["docx.hidden_font"] = {}
    elif props.get("webHidden"):
        reasons["docx.web_hidden"] = {}
    size_pt = props.get("sz", 22) / 2
    if size_pt < 2:
        reasons["docx.tiny"] = {"font_size_pt": size_pt}
    elif size_pt < 3:
        reasons["docx.small"] = {"font_size_pt": size_pt}
    scale = props.get("scale", 100)
    if scale <= 20:
        reasons["docx.squeezed"] = {"character_width_percent": scale}
    colour = props.get("color")
    text_rgb = _hex_to_rgb(colour) if colour and colour != "auto" else None
    if text_rgb is not None:
        background = None
        highlight = props.get("highlight")
        if highlight and highlight != "none":
            background = HIGHLIGHT_COLOURS.get(highlight)
        background = background or props.get("shd") or styles.paragraph_shading(para)
        if background is None:
            cell = _ancestor(run, "tc")
            shd = cell.find(f"{W}tcPr/{W}shd") if cell is not None else None
            if shd is not None and (shd.get(W + "fill") or "auto").lower() != "auto":
                background = shd.get(W + "fill")
        background = background or page_bg
        bg_rgb = _hex_to_rgb(background) or (1.0, 1.0, 1.0)
        ratio = contrast_ratio(text_rgb, bg_rgb)
        details = {"text_colour": "#" + colour.lower(), "background_colour": "#" + background.lower(),
                   "contrast_ratio": round(ratio, 2)}
        if ratio < SAME_COLOUR_CONTRAST:
            reasons["docx.same_colour"] = details
        elif ratio < FAINT_CONTRAST:
            reasons["docx.faint"] = details
    return reasons


def _primary(reasons: dict[str, Any]) -> str:
    for tid in PRIORITY:
        if tid in reasons:
            return tid
    return next(iter(reasons))


def _part_label(part: str) -> str:
    name = part.split("/")[-1].removesuffix(".xml")
    if name == "document":
        return ""
    match = re.fullmatch(r"(header|footer)(\d*)", name)
    if match:
        return f"{match.group(1)} {match.group(2)}".strip()
    return {"footnotes": "footnotes", "endnotes": "endnotes"}.get(name, name)


def scan_docx(data: bytes) -> tuple[list[Finding], dict[str, Any], list[str]]:
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        # Password-protected .docx files (and old .doc files) are stored in this older container.
        raise ScanError("the document is password-protected or in the old .doc format; "
                        "save an unprotected .docx copy first")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        names = set(zf.namelist())
    except zipfile.BadZipFile as exc:
        raise ScanError("this doesn't look like a Word .docx file (it isn't a valid zip package)") from exc
    if "word/document.xml" not in names:
        raise ScanError("this zip file isn't a Word document (no word/document.xml inside)")

    styles = _Styles(zf.read("word/styles.xml") if "word/styles.xml" in names else None)
    body_root = etree.fromstring(zf.read("word/document.xml"))
    page_bg = "FFFFFF"
    bg_el = body_root.find(W + "background")
    if bg_el is not None and _hex_to_rgb(bg_el.get(W + "color")):
        page_bg = bg_el.get(W + "color")

    parts = ["word/document.xml"]
    parts += sorted(n for n in names if re.fullmatch(r"word/(header|footer)\d*\.xml", n))
    parts += [n for n in ("word/footnotes.xml", "word/endnotes.xml") if n in names]

    findings: list[Finding] = []
    notes: list[str] = []
    stats = {"paragraphs": 0, "runs": 0, "characters": 0}
    all_runs: list[_Run] = []
    para_text: dict[tuple[str, int], str] = {}
    para_labels: dict[tuple[str, int], str] = {}
    seq = 0

    for part in parts:
        root = body_root if part == "word/document.xml" else etree.fromstring(zf.read(part))
        paragraphs = list(root.iter(W + "p"))
        index_of = {id(p): i for i, p in enumerate(paragraphs)}
        stats["paragraphs"] += len(paragraphs)
        label = _part_label(part)
        for p in paragraphs:
            key = (part, index_of[id(p)])
            where = f"paragraph {index_of[id(p)] + 1}"
            if _ancestor(p, "txbxContent") is not None:
                where += " (in a text box)"
            elif _ancestor(p, "tc") is not None:
                where += " (in a table)"
            para_labels[key] = f"{label}, {where}" if label else where
        for run_index, run in enumerate(root.iter(W + "r")):
            if _in_fallback(run):
                continue  # duplicate copy of a text box kept for old Word versions
            text = _run_text(run)
            if not text.strip():
                continue
            para = _ancestor(run, "p")
            key = (part, index_of.get(id(para), -1)) if para is not None else (part, -1)
            para_text[key] = para_text.get(key, "") + text
            stats["runs"] += 1
            stats["characters"] += len(text)
            item = _Run(part, run_index, key, seq, text)
            seq += 1
            if _ancestor(run, "del") is not None:
                item.reasons["docx.tracked_deletion"] = {}
            else:
                item.reasons.update(_check_run(run, para, styles, page_bg))
            all_runs.append(item)

    # Group neighbouring hidden runs of the same kind into one finding.
    groups: list[list[_Run]] = []
    for item in all_runs:
        if not item.reasons:
            continue
        prev = groups[-1][-1] if groups else None
        if (prev is not None and prev.seq + 1 == item.seq and prev.part == item.part
                and abs(prev.para_key[1] - item.para_key[1]) <= 1
                and _primary(prev.reasons) == _primary(item.reasons)):
            groups[-1].append(item)
        else:
            groups.append([item])
    visible_text: dict[tuple[str, int], str] = {}
    for item in all_runs:
        if not item.reasons:
            visible_text[item.para_key] = visible_text.get(item.para_key, "") + item.text
    for n, group in enumerate(groups):
        primary = _primary(group[0].reasons)
        ids: list[str] = []
        for item in group:
            ids += [t for t in item.reasons if t not in ids]
        severity = max_severity(*(CATALOG[t].severity for t in ids))
        details = dict(group[0].reasons[primary])
        part, index = group[0].para_key
        near = ""
        for back in range(0, 6):  # this paragraph, or the closest visible one above it
            near = visible_text.get((part, index - back), "").strip()
            if near:
                break
        if near:
            near = re.sub(r"\s+", " ", near)
            details["near_visible_text"] = near[:70] + ("…" if len(near) > 70 else "")
        details["runs"] = [[item.part, item.run_index] for item in group]
        text = re.sub(r"\s+", " ", "".join(item.text for item in group)).strip()
        findings.append(make_finding(
            primary, text, para_labels.get(group[0].para_key, "document"), severity=severity,
            details=details, also=[CATALOG[t].title for t in ids if t != primary],
            order=group[0].seq,
        ))

    for key, text in para_text.items():
        findings.extend(scan_invisibles(text, para_labels.get(key, "document"), order=10**6 + key[1]))

    # Comments stored alongside the document.
    if "word/comments.xml" in names:
        croot = etree.fromstring(zf.read("word/comments.xml"))
        for comment in croot.iter(W + "comment"):
            text = " ".join(_run_text(r) for r in comment.iter(W + "r")).strip()
            if text:
                author = comment.get(W + "author") or "unknown author"
                findings.append(make_finding("docx.comment", re.sub(r"\s+", " ", text),
                                             f"comment by {author}", order=2 * 10**6))

    # Picture descriptions (alt text) that read like instructions.
    for el in body_root.iter():
        if _local(el.tag) == "docPr":
            for attr in ("descr", "title"):
                value = (el.get(attr) or "").strip()
                if value and score_text(value).score >= REPORT_THRESHOLD:
                    findings.append(make_finding("docx.alt_text", value,
                                                 f"picture '{el.get('name') or 'unnamed'}'", order=3 * 10**6))

    # Document properties that read like instructions.
    for prop_part in ("docProps/core.xml", "docProps/custom.xml", "docProps/app.xml"):
        if prop_part not in names:
            continue
        proot = etree.fromstring(zf.read(prop_part))
        for el in proot.iter():
            if not isinstance(el.tag, str) or len(el):
                continue
            value = (el.text or "").strip()
            if value and score_text(value).score >= REPORT_THRESHOLD:
                prop_name = _local(el.tag)
                if prop_name.startswith("vt") or prop_name in ("lpwstr", "lpstr"):
                    parent = el.getparent()
                    prop_name = parent.get("name") or prop_name if parent is not None else prop_name
                findings.append(make_finding("docx.metadata", value, f"document property '{prop_name}'",
                                             order=4 * 10**6))

    if any(n.startswith("word/embeddings/") for n in names):
        notes.append("The document contains embedded files or objects; those were not scanned.")
    if "word/vbaProject.bin" in names:
        notes.append("The document contains macros (VBA code); macros were not examined.")
    findings.sort(key=lambda f: f.order)
    return findings, stats, notes


# ---------------------------------------------------------------- revealed copy

# Word requires the formatting elements inside <w:rPr> in this order.
_RPR_ORDER = [
    "rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike", "outline",
    "shadow", "emboss", "imprint", "noProof", "snapToGrid", "vanish", "webHidden", "color", "spacing",
    "w", "kern", "position", "sz", "szCs", "highlight", "u", "effect", "bdr", "shd", "fitText",
    "vertAlign", "rtl", "cs", "em", "lang", "eastAsianLayout", "specVanish", "oMath",
]


def _set_rpr(rpr: etree._Element, name: str, value: str | None) -> None:
    for old in rpr.findall(W + name):
        rpr.remove(old)
    el = etree.Element(W + name)
    if value is not None:
        el.set(W + "val", value)
    rank = _RPR_ORDER.index(name)
    for i, child in enumerate(rpr):
        child_name = _local(child.tag)
        if child_name in _RPR_ORDER and _RPR_ORDER.index(child_name) > rank:
            rpr.insert(i, el)
            return
    rpr.append(el)


def reveal_docx(data: bytes, findings: list[Finding]) -> bytes:
    """Return a copy of the document with every flagged run made visible (dark red on yellow)."""
    targets: dict[str, set[int]] = {}
    for finding in findings:
        if finding.technique == "docx.tracked_deletion":
            continue
        for part, index in finding.details.get("runs", []):
            targets.setdefault(part, set()).add(int(index))
    source = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            content = source.read(info.filename)
            if info.filename in targets:
                root = etree.fromstring(content)
                runs = list(root.iter(W + "r"))
                for index in targets[info.filename]:
                    if index >= len(runs):
                        continue
                    run = runs[index]
                    rpr = run.find(W + "rPr")
                    if rpr is None:
                        rpr = etree.Element(W + "rPr")
                        run.insert(0, rpr)
                    for name in ("vanish", "webHidden", "specVanish"):
                        _set_rpr(rpr, name, "0")
                    _set_rpr(rpr, "color", "C00000")
                    _set_rpr(rpr, "w", "100")
                    _set_rpr(rpr, "sz", "20")
                    _set_rpr(rpr, "szCs", "20")
                    _set_rpr(rpr, "highlight", "yellow")
                    for old in rpr.findall(W + "shd"):
                        rpr.remove(old)
                content = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            target.writestr(info, content)
    return out.getvalue()

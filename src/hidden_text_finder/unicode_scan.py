"""Finds invisible Unicode characters, and decodes messages smuggled inside them.

Three smuggling tricks are decoded, not just detected:

* Unicode "tag" characters (U+E0020 to U+E007E) mirror printable ASCII one-to-one but are
  drawn as nothing. We subtract 0xE0000 to get the hidden letters back.
* Variation selectors (U+FE00-FE0F and U+E0100-E01EF) are 256 invisible characters, so a
  chain of them can stand for a chain of bytes (0-255). We turn them back into bytes and
  then into text.
* Zero-width characters used as binary (for example zero-width space = 0 and zero-width
  non-joiner = 1). We try the common layouts and keep the result only if it reads as text.

Some invisible characters have honest jobs, so they are skipped in those places: a byte
order mark at the very start, joiners between emoji (the family emoji is several emoji glued
with zero-width joiners), joiners in Indic and Arabic-script text (Hindi and Gujarati use them
to control how letters combine), zero-width spaces in Thai/Khmer/Lao/Burmese text (which has no
spaces between words), a single variation selector after an emoji, flag emoji built from tags,
and direction marks in right-to-left text.
"""

from __future__ import annotations

from .models import Finding
from .techniques import make_finding

TAG_START, TAG_END = 0xE0000, 0xE007F
CANCEL_TAG = 0xE007F
BLACK_FLAG = 0x1F3F4

BIDI_CONTROLS = {
    0x202A: "LRE", 0x202B: "RLE", 0x202C: "PDF", 0x202D: "LRO", 0x202E: "RLO",
    0x2066: "LRI", 0x2067: "RLI", 0x2068: "FSI", 0x2069: "PDI",
}
BIDI_MARKS = {0x200E: "LRM", 0x200F: "RLM", 0x061C: "ALM"}
ZERO_WIDTH = {
    0x200B: "ZWSP", 0x200C: "ZWNJ", 0x200D: "ZWJ", 0x2060: "WJ", 0xFEFF: "ZWNBSP",
    0x180E: "MVS", 0x2061: "FUNC", 0x2062: "TIMES", 0x2063: "SEP", 0x2064: "PLUS",
    0x034F: "CGJ", 0x115F: "HCF", 0x1160: "HJF", 0x3164: "HANGUL-FILLER", 0xFFA0: "HW-HANGUL-FILLER",
    0x2800: "BRAILLE-BLANK", 0x17B4: "KHMER-AQ", 0x17B5: "KHMER-AA",
    0x206A: "ISS", 0x206B: "ASS", 0x206C: "IAFS", 0x206D: "AAFS", 0x206E: "NADS", 0x206F: "NODS",
    **{cp: "MUSIC-FORMAT" for cp in range(0x1D173, 0x1D17B)},
}
SOFT_HYPHEN = 0x00AD

FULL_NAMES = {
    "ZWSP": "zero width space", "ZWNJ": "zero width non-joiner", "ZWJ": "zero width joiner",
    "WJ": "word joiner", "ZWNBSP": "zero width no-break space (byte order mark)",
    "MVS": "Mongolian vowel separator", "FUNC": "invisible function application",
    "TIMES": "invisible times", "SEP": "invisible separator", "PLUS": "invisible plus",
    "CGJ": "combining grapheme joiner", "HCF": "Hangul choseong filler", "HJF": "Hangul jungseong filler",
    "HANGUL-FILLER": "Hangul filler", "HW-HANGUL-FILLER": "halfwidth Hangul filler",
    "BRAILLE-BLANK": "Braille pattern blank", "KHMER-AQ": "Khmer inherent vowel AQ",
    "KHMER-AA": "Khmer inherent vowel AA", "LRM": "left-to-right mark", "RLM": "right-to-left mark",
    "ALM": "Arabic letter mark", "LRE": "left-to-right embedding", "RLE": "right-to-left embedding",
    "PDF": "pop directional formatting", "LRO": "left-to-right override", "RLO": "right-to-left override",
    "LRI": "left-to-right isolate", "RLI": "right-to-left isolate", "FSI": "first strong isolate",
    "PDI": "pop directional isolate", "SHY": "soft hyphen", "MUSIC-FORMAT": "invisible musical formatting",
    "ISS": "inhibit symmetric swapping", "ASS": "activate symmetric swapping",
    "IAFS": "inhibit Arabic form shaping", "AAFS": "activate Arabic form shaping",
    "NADS": "national digit shapes", "NODS": "nominal digit shapes",
}


# ---------------------------------------------------------------- character classes

def _is_tag(cp: int) -> bool:
    return TAG_START <= cp <= TAG_END


def _is_vs(cp: int) -> bool:
    return 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF


def _vs_value(cp: int) -> int:
    return cp - 0xFE00 if cp <= 0xFE0F else cp - 0xE0100 + 16


def _is_emoji(cp: int) -> bool:
    return (0x1F000 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF or 0x2300 <= cp <= 0x23FF
            or 0x2B00 <= cp <= 0x2BFF or 0x2194 <= cp <= 0x21AA or cp in (0x00A9, 0x00AE, 0x203C, 0x2049,
            0x2122, 0x2139, 0x3030, 0x303D, 0x3297, 0x3299, 0x20E3))


def _script(cp: int) -> str:
    if (0x0900 <= cp <= 0x0DFF or 0xA8E0 <= cp <= 0xA8FF or 0x1CD0 <= cp <= 0x1CFF
            or 0x11000 <= cp <= 0x110CF):
        return "indic"
    if (0x0590 <= cp <= 0x08FF or 0xFB1D <= cp <= 0xFDFF or 0xFE70 <= cp <= 0xFEFC):
        return "rtl"
    if (0x0E00 <= cp <= 0x0EFF or 0x1000 <= cp <= 0x109F or 0x1780 <= cp <= 0x17FF
            or 0x1A20 <= cp <= 0x1AAF):
        return "sea"  # Thai, Lao, Burmese, Khmer, Tai Tham: no spaces between words
    if 0x3040 <= cp <= 0x30FF or 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF:
        return "cjk"
    if 0x1100 <= cp <= 0x11FF or 0xAC00 <= cp <= 0xD7AF or 0x3130 <= cp <= 0x318F:
        return "hangul"
    if 0x2800 <= cp <= 0x28FF:
        return "braille"
    if _is_emoji(cp):
        return "emoji"
    return "other"


def _is_invisible(cp: int) -> bool:
    return (cp in ZERO_WIDTH or cp in BIDI_CONTROLS or cp in BIDI_MARKS or cp == SOFT_HYPHEN
            or _is_tag(cp) or _is_vs(cp))


def _neighbours(text: str, i: int) -> tuple[int | None, int | None]:
    """Nearest visible characters before and after position i."""
    prev = next_ = None
    j = i - 1
    while j >= 0:
        cp = ord(text[j])
        if not _is_invisible(cp):
            prev = cp
            break
        j -= 1
    j = i + 1
    while j < len(text):
        cp = ord(text[j])
        if not _is_invisible(cp):
            next_ = cp
            break
        j += 1
    return prev, next_


def _legit_zero_width(text: str, i: int, cp: int) -> bool:
    if cp == 0xFEFF and i == 0:
        return True
    prev, next_ = _neighbours(text, i)
    scripts = {_script(c) for c in (prev, next_) if c is not None}
    if cp == 0x200D:  # zero width joiner
        if prev is not None and next_ is not None and _is_emoji(prev) and _is_emoji(next_):
            return True
        return bool(scripts & {"indic", "rtl"})
    if cp == 0x200C:  # zero width non-joiner
        return bool(scripts & {"indic", "rtl"})
    if cp == 0x200B:  # zero width space
        return bool(scripts & {"sea", "cjk"})
    if cp in (0x115F, 0x1160):
        return "hangul" in scripts
    if cp in (0x17B4, 0x17B5):
        return "sea" in scripts
    if cp == 0x2800:
        return "braille" in scripts
    return False


def _is_flag_sequence(text: str, start: int, end: int) -> bool:
    """Tag characters used honestly: a subdivision flag such as England, Scotland or Wales."""
    if start == 0 or ord(text[start - 1]) != BLACK_FLAG:
        return False
    body = [ord(c) for c in text[start:end]]
    if not body or body[-1] != CANCEL_TAG or len(body) > 8:
        return False
    return all(0xE0030 <= c <= 0xE0039 or 0xE0061 <= c <= 0xE007A for c in body[:-1])


# ---------------------------------------------------------------- decoders

def decode_tags(run: str) -> str:
    return "".join(chr(ord(c) - 0xE0000) for c in run if 0xE0020 <= ord(c) <= 0xE007E)


def decode_variation_selectors(run: str) -> bytes:
    return bytes(_vs_value(ord(c)) for c in run if _is_vs(ord(c)))


def _looks_like_text(s: str) -> bool:
    s = s.strip("\x00").strip()
    if len(s) < 2:
        return False
    good = sum(1 for ch in s if ch.isalnum() or ch in " .,:;!?'\"-()/@#&+=_\n")
    return good / len(s) >= 0.9 and any(ch.isalpha() for ch in s)


def _bytes_to_text(data: bytes) -> str | None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return text if _looks_like_text(text) else None


def _bits_to_text(bits: str) -> str | None:
    for width in (8, 7):
        usable = len(bits) - len(bits) % width
        if usable < width * 2:
            continue
        raw = bytes(int(bits[k:k + width], 2) for k in range(0, usable, width))
        text = _bytes_to_text(raw)
        if text:
            return text
    return None


def decode_zero_width_binary(cps: list[int]) -> str | None:
    """Try the common 'zero-width characters as ones and zeros' layouts."""
    if len(cps) < 16:
        return None
    distinct = sorted(set(cps))
    if len(distinct) == 2:
        for zero, one in ((distinct[0], distinct[1]), (distinct[1], distinct[0])):
            text = _bits_to_text("".join("0" if c == zero else "1" for c in cps))
            if text:
                return text
    if len(distinct) == 3:  # two symbols for bits plus one separating the letters
        for sep in distinct:
            bit_symbols = [d for d in distinct if d != sep]
            groups: list[list[int]] = [[]]
            for c in cps:
                if c == sep:
                    groups.append([])
                else:
                    groups[-1].append(c)
            groups = [g for g in groups if g]
            if len(groups) < 2:
                continue
            for zero, one in (bit_symbols, bit_symbols[::-1]):
                chars = []
                for g in groups:
                    bits = "".join("0" if c == zero else "1" for c in g)
                    value = int(bits, 2)
                    if len(bits) > 21 or value > 0x10FFFF:
                        break
                    chars.append(chr(value))
                else:
                    text = "".join(chars)
                    if _looks_like_text(text):
                        return text
    return None


# ---------------------------------------------------------------- display helpers

def _short_name(cp: int) -> str | None:
    if cp in ZERO_WIDTH:
        return ZERO_WIDTH[cp]
    if cp in BIDI_CONTROLS:
        return BIDI_CONTROLS[cp]
    if cp in BIDI_MARKS:
        return BIDI_MARKS[cp]
    if cp == SOFT_HYPHEN:
        return "SHY"
    return None


def reveal(text: str, only_suspicious: bool = True) -> str:
    """Return text with invisible characters shown as visible markers like ⟦ZWSP⟧."""
    has_rtl = any(_script(ord(c)) == "rtl" for c in text)
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        cp = ord(text[i])
        if _is_tag(cp):
            j = i
            while j < n and _is_tag(ord(text[j])):
                j += 1
            if only_suspicious and _is_flag_sequence(text, i, j):
                out.append(text[i:j])
            else:
                out.append(f"⟦hidden tag text: {decode_tags(text[i:j])}⟧")
            i = j
            continue
        if _is_vs(cp):
            j = i
            while j < n and _is_vs(ord(text[j])):
                j += 1
            if j - i == 1 and only_suspicious:
                out.append(text[i])
            else:
                decoded = _bytes_to_text(decode_variation_selectors(text[i:j]))
                label = f"⟦{j - i} variation selectors"
                out.append(label + (f": {decoded}⟧" if decoded else "⟧"))
            i = j
            continue
        name = _short_name(cp)
        if name is None:
            out.append(text[i])
        elif only_suspicious and (
            (cp in ZERO_WIDTH and _legit_zero_width(text, i, cp))
            or ((cp in BIDI_MARKS or cp in BIDI_CONTROLS) and has_rtl)
        ):
            out.append(text[i])
        else:
            out.append(f"⟦{name}⟧")
        i += 1
    return "".join(out)


def _snippet(text: str, first: int, last: int, pad: int = 60, limit: int = 400) -> str:
    start = max(0, first - pad)
    end = min(len(text), last + pad + 1)
    piece = reveal(text[start:end])
    if len(piece) > limit:
        piece = piece[: limit // 2] + " … " + piece[-limit // 2:]
    return ("…" if start > 0 else "") + piece.strip() + ("…" if end < len(text) else "")


# ---------------------------------------------------------------- main entry point

_CONTROL = dict.fromkeys([c for c in range(32) if c not in (9, 10, 13)] + [0x7F])


def scan_invisibles(text: str, location: str, order: int = 0) -> list[Finding]:
    """Find invisible characters in one block of text (a line, paragraph or page)."""
    findings: list[Finding] = []
    if not text:
        return findings
    # Control characters are junk from fonts without a character map, not hidden text.
    text = text.translate(_CONTROL)
    has_rtl = any(_script(ord(c)) == "rtl" for c in text)
    zero_width: list[tuple[int, int]] = []
    bidi: list[tuple[int, int]] = []
    soft_hyphens: list[int] = []
    i, n = 0, len(text)
    while i < n:
        cp = ord(text[i])
        if _is_tag(cp):
            j = i
            while j < n and _is_tag(ord(text[j])):
                j += 1
            if not _is_flag_sequence(text, i, j):
                decoded = decode_tags(text[i:j])
                findings.append(make_finding(
                    "unicode.tag_smuggling",
                    decoded.strip() or f"({j - i} tag characters that don't spell anything)",
                    location, order=order,
                    details={"hidden_characters": j - i, "context": _snippet(text, i, j - 1)},
                ))
            i = j
            continue
        if _is_vs(cp):
            j = i
            while j < n and _is_vs(ord(text[j])):
                j += 1
            count = j - i
            if count >= 2:
                data = decode_variation_selectors(text[i:j])
                decoded = _bytes_to_text(data)
                findings.append(make_finding(
                    "unicode.variation_selectors",
                    decoded if decoded else f"({count} selectors; raw bytes: {data[:48].hex(' ')})",
                    location, order=order,
                    severity="high" if count >= 4 else "medium",
                    details={"hidden_characters": count, "decoded": bool(decoded),
                             "context": _snippet(text, i, j - 1)},
                ))
            i = j
            continue
        if cp in BIDI_CONTROLS:
            bidi.append((i, cp))
        elif cp in BIDI_MARKS:
            if not has_rtl:
                zero_width.append((i, cp))
        elif cp in ZERO_WIDTH:
            if not _legit_zero_width(text, i, cp):
                zero_width.append((i, cp))
        elif cp == SOFT_HYPHEN:
            soft_hyphens.append(i)
        i += 1

    if zero_width:
        counts: dict[str, int] = {}
        for _, cp in zero_width:
            label = f"{_short_name(cp)} ({FULL_NAMES.get(_short_name(cp) or '', 'invisible')})"
            counts[label] = counts.get(label, 0) + 1
        context = _snippet(text, zero_width[0][0], zero_width[-1][0])
        decoded = decode_zero_width_binary([cp for _, cp in zero_width])
        details = {"invisible_characters": len(zero_width), "by_type": counts, "context": context}
        if decoded:
            details["decoded_from_binary"] = True
            findings.append(make_finding("unicode.zero_width", decoded, location, severity="high",
                                         details=details, order=order))
        else:
            findings.append(make_finding("unicode.zero_width", context, location, details=details,
                                         order=order))
    if bidi and not has_rtl:
        counts = {}
        for _, cp in bidi:
            label = f"{BIDI_CONTROLS[cp]} ({FULL_NAMES[BIDI_CONTROLS[cp]]})"
            counts[label] = counts.get(label, 0) + 1
        findings.append(make_finding(
            "unicode.bidi", _snippet(text, bidi[0][0], bidi[-1][0]), location, order=order,
            details={"direction_controls": len(bidi), "by_type": counts,
                     "as_stored": text[max(0, bidi[0][0] - 40): bidi[-1][0] + 40].translate(
                         {cp: None for cp in BIDI_CONTROLS})},
        ))
    if soft_hyphens:
        findings.append(make_finding(
            "unicode.soft_hyphen", _snippet(text, soft_hyphens[0], soft_hyphens[-1]), location,
            order=order, details={"soft_hyphens": len(soft_hyphens)},
        ))
    return findings

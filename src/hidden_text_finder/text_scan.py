"""Plain text and Markdown scanner."""

from __future__ import annotations

import re
from typing import Any

from .models import Finding
from .techniques import make_finding
from .unicode_scan import scan_invisibles

HTML_COMMENT = re.compile(r"<!--(.*?)-->", re.S)
# Markdown "comment" lines: [//]: # (text)  [comment]: # "text"  [//]: <> (text)
MD_COMMENT = re.compile(r"^[ \t]*\[(?://|comment|_|#)?\]:\s*(?:#|<>)\s*(?:\((.*?)\)|\"(.*?)\"|'(.*?)')[ \t]*$",
                        re.M | re.I)
HTML_TAG = re.compile(r"<[a-zA-Z!]")
# Markdown shows code literally, so tags or comments written inside code are just text.
FENCED_CODE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})[^\n]*\n.*?(?:^[ \t]{0,3}\1[ \t]*$|\Z)", re.M | re.S)
INLINE_CODE = re.compile(r"(`+)(?!`).+?(?<!`)\1(?!`)", re.S)


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _blank(match: re.Match[str]) -> str:
    return re.sub(r"[^\n]", " ", match.group(0))  # keep line breaks so line numbers stay right


def mask_code(text: str) -> str:
    """Replace Markdown code blocks and `code spans` with spaces, keeping line numbers."""
    return INLINE_CODE.sub(_blank, FENCED_CODE.sub(_blank, text))


def scan_plain_text(text: str, *, markdown: bool = False) -> tuple[list[Finding], dict[str, Any], list[str]]:
    findings: list[Finding] = []
    notes: list[str] = []
    # Windows ends lines with "\r\n" and old Macs with "\r". Turn both into "\n" first, otherwise
    # the Markdown rules below (which look for the end of a line) miss things in Windows files.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.splitlines()
    stats = {"lines": len(lines), "characters": len(text)}
    for number, line in enumerate(lines, start=1):
        findings.extend(scan_invisibles(line, f"line {number}", order=number * 10))

    if markdown:
        # Invisible characters matter even inside code (think Trojan Source), so the check above
        # used the raw text. Comments and HTML only count outside code.
        prose = mask_code(text)
        for match in HTML_COMMENT.finditer(prose):
            body = match.group(1).strip()
            if body:
                line = _line_of(prose, match.start())
                findings.append(make_finding("html.comment", re.sub(r"\s+", " ", body), f"comment on line {line}",
                                             order=line * 10 + 1))
        for match in MD_COMMENT.finditer(prose):
            body = next((g for g in match.groups() if g), "").strip()
            if body:
                line = _line_of(prose, match.start())
                findings.append(make_finding("md.comment", body, f"line {line}", order=line * 10 + 2))
        # Inline HTML inside Markdown can hide text with CSS, so run the web page checks on it.
        if HTML_TAG.search(HTML_COMMENT.sub("", prose)):
            from .html_scan import scan_html

            html_findings, _, _ = scan_html(prose, unicode=False)
            for finding in html_findings:
                if finding.technique == "html.comment":
                    continue  # already reported above with line numbers
                line_match = re.search(r"on line (\d+)", finding.location)
                finding.order = int(line_match.group(1)) * 10 + 3 if line_match else 10**9
                findings.append(finding)
    findings.sort(key=lambda f: f.order)
    return findings, stats, notes

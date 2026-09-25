"""Entry point that picks the right scanner for a file and builds the final ScanResult."""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import zipfile
from pathlib import Path

from .ai_score import score_text
from .models import (AI_INSTRUCTION_THRESHOLD, SEVERITY_RANK, Finding, ScanError, ScanResult,
                     bump_severity, max_severity)
from .version import __version__

SUPPORTED_EXTENSIONS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".html": "html", ".htm": "html", ".xhtml": "html",
    ".md": "markdown", ".markdown": "markdown",
    ".txt": "text",
}
TYPE_LABELS = {"pdf": "PDF", "docx": "Word document", "html": "Web page", "markdown": "Markdown", "text": "Plain text"}
MAX_BYTES = 200 * 1024 * 1024


def detect_type(data: bytes, filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in SUPPORTED_EXTENSIONS:
        return SUPPORTED_EXTENSIONS[ext]
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:2] == b"PK":
        try:
            if "word/document.xml" in zipfile.ZipFile(io.BytesIO(data)).namelist():
                return "docx"
        except zipfile.BadZipFile:
            pass
    head = data[:2048].lower()
    if b"<html" in head or b"<!doctype html" in head:
        return "html"
    if ext in (".doc",):
        raise ScanError("old .doc files aren't supported; save the file as .docx first")
    try:
        data[:4096].decode("utf-8")
        return "text"
    except UnicodeDecodeError:
        raise ScanError(f"unsupported file type '{ext or 'unknown'}'") from None


def decode_text(data: bytes) -> str:
    if data[:3] == b"\xef\xbb\xbf":
        return data[3:].decode("utf-8", errors="replace")
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


# Findings that exist only because their wording scored as instruction-like. Their base severity
# already reflects that, so a middling score doesn't raise them a second time.
REPORTED_FOR_WORDING = {"html.attribute", "docx.alt_text", "docx.metadata", "pdf.metadata"}


def apply_ai_scores(findings: list[Finding]) -> None:
    """Score every finding for instruction-like wording and raise its severity to match."""
    for finding in findings:
        texts = [finding.text, str(finding.details.get("context", ""))]
        result = score_text(" ".join(t for t in texts if t))
        finding.ai_score = result.score
        finding.ai_reasons = result.reasons
        if result.score >= AI_INSTRUCTION_THRESHOLD:
            finding.severity = max_severity(finding.severity, "high")
        elif result.score >= 25 and finding.technique not in REPORTED_FOR_WORDING:
            finding.severity = min(bump_severity(finding.severity), "high", key=SEVERITY_RANK.__getitem__)


def sort_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: (-SEVERITY_RANK[f.severity], -int(f.is_ai_instruction), f.order))


def scan_bytes(data: bytes, filename: str) -> ScanResult:
    """Scan a file that is already in memory (used by the web app)."""
    result = ScanResult(
        file_name=Path(filename).name,
        file_type="unknown",
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        scanned_at=dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        tool_version=__version__,
    )
    try:
        if len(data) > MAX_BYTES:
            raise ScanError(f"the file is larger than {MAX_BYTES // (1024 * 1024)} MB")
        kind = detect_type(data, filename)
        result.file_type = kind
        if kind == "pdf":
            from .pdf_scan import scan_pdf
            findings, stats, notes = scan_pdf(data)
        elif kind == "docx":
            from .docx_scan import scan_docx
            findings, stats, notes = scan_docx(data)
        elif kind == "html":
            from .html_scan import scan_html
            findings, stats, notes = scan_html(decode_text(data))
        else:
            from .text_scan import scan_plain_text
            findings, stats, notes = scan_plain_text(decode_text(data), markdown=(kind == "markdown"))
    except ScanError as exc:
        result.error = str(exc)
        return result
    apply_ai_scores(findings)
    result.findings = sort_findings(findings)
    result.stats = stats
    result.notes = notes
    return result


def scan_file(path: str | Path) -> ScanResult:
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        return ScanResult(
            file_name=path.name, file_type="unknown", size_bytes=0, sha256="",
            scanned_at=dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
            tool_version=__version__, error=f"couldn't read the file ({exc.strerror or exc})",
        )
    return scan_bytes(data, path.name)


def iter_supported_files(paths: list[str | Path], recursive: bool = True) -> list[Path]:
    """Expand folders into the supported files they contain."""
    files: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            pattern = "**/*" if recursive else "*"
            files += sorted(p for p in path.glob(pattern)
                            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
                            and ".marked." not in p.name and ".revealed." not in p.name)
        else:
            files.append(path)
    return files

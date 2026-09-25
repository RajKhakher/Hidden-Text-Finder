"""Data structures shared by every scanner: a Finding (one hidden item) and a ScanResult (one file)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SEVERITIES = ("info", "low", "medium", "high")
SEVERITY_RANK = {name: rank for rank, name in enumerate(SEVERITIES)}

# An "AI-instruction score" at or above this means the hidden text reads like a
# command aimed at an AI tool (for example "ignore all previous instructions").
AI_INSTRUCTION_THRESHOLD = 50


def max_severity(*severities: str) -> str:
    return max(severities, key=SEVERITY_RANK.__getitem__)


def bump_severity(severity: str, steps: int = 1) -> str:
    rank = min(len(SEVERITIES) - 1, SEVERITY_RANK[severity] + steps)
    return SEVERITIES[rank]


class ScanError(Exception):
    """Raised when a file can't be scanned (wrong type, damaged, password-protected...)."""


@dataclass
class Finding:
    technique: str                 # machine id, e.g. "pdf.same_colour"
    title: str                     # short human title
    severity: str                  # info | low | medium | high
    text: str                      # the hidden text, as a computer (or AI) reads it
    location: str                  # human-readable place, e.g. "page 2" or "paragraph 14"
    explanation: str               # plain-English explanation of the trick
    also: list[str] = field(default_factory=list)       # other tricks applied to the same text
    details: dict[str, Any] = field(default_factory=dict)
    page: int | None = None        # 0-based page number (PDF only)
    rects: list[list[float]] = field(default_factory=list)  # PDF boxes, unrotated page coordinates
    ai_score: int = 0              # 0-100: how much the text reads like an instruction to an AI
    ai_reasons: list[str] = field(default_factory=list)
    order: int = 0                 # position in the document, used to keep reports in reading order

    @property
    def is_ai_instruction(self) -> bool:
        return self.ai_score >= AI_INSTRUCTION_THRESHOLD

    def to_dict(self) -> dict[str, Any]:
        return {
            "technique": self.technique,
            "title": self.title,
            "severity": self.severity,
            "text": self.text,
            "location": self.location,
            "explanation": self.explanation,
            "also": list(self.also),
            "details": dict(self.details),
            "page": None if self.page is None else self.page + 1,
            "ai_score": self.ai_score,
            "ai_reasons": list(self.ai_reasons),
            "looks_like_ai_instruction": self.is_ai_instruction,
        }


@dataclass
class ScanResult:
    file_name: str
    file_type: str
    size_bytes: int
    sha256: str
    scanned_at: str
    tool_version: str
    findings: list[Finding] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def counts(self) -> dict[str, int]:
        counts = {name: 0 for name in SEVERITIES}
        for finding in self.findings:
            counts[finding.severity] += 1
        return counts

    @property
    def ai_instruction_count(self) -> int:
        return sum(1 for f in self.findings if f.is_ai_instruction)

    @property
    def verdict(self) -> tuple[str, str]:
        """(code, sentence) summarising the file."""
        if self.error:
            return "error", f"Could not scan: {self.error}"
        counts = self.counts
        if self.ai_instruction_count:
            n = self.ai_instruction_count
            return "ai_instructions", f"Hidden instructions aimed at AI found ({n} item{'s' if n != 1 else ''})"
        if counts["high"]:
            n = counts["high"] + counts["medium"]
            return "hidden_text", f"Hidden text found ({n} item{'s' if n != 1 else ''})"
        if counts["medium"]:
            n = counts["medium"]
            return "check", f"Possibly hidden text: {n} item{'s' if n != 1 else ''} to check"
        if counts["low"]:
            return "minor", "Only minor items found (probably harmless, worth a glance)"
        return "clean", "No hidden text found"

    def worst_severity(self) -> str | None:
        if not self.findings:
            return None
        return max_severity(*(f.severity for f in self.findings))

    def to_dict(self) -> dict[str, Any]:
        code, sentence = self.verdict
        return {
            "file_name": self.file_name,
            "file_type": self.file_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "scanned_at": self.scanned_at,
            "tool_version": self.tool_version,
            "verdict": code,
            "verdict_text": sentence,
            "counts": self.counts,
            "stats": dict(self.stats),
            "notes": list(self.notes),
            "error": self.error,
            "findings": [f.to_dict() for f in self.findings],
        }

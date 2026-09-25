"""Scores how much a piece of text reads like an instruction aimed at an AI tool.

This is a transparent keyword-and-pattern check, not an AI model: every point comes
from a named pattern, and the matched reasons are shown in the report. That makes it
easy to explain and easy to extend, but it can miss cleverly worded instructions and
it can occasionally flag an innocent sentence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_FLAGS = re.IGNORECASE | re.MULTILINE


@dataclass(frozen=True)
class _Pattern:
    weight: int
    reason: str
    regex: re.Pattern[str]


def _p(weight: int, reason: str, *patterns: str) -> _Pattern:
    return _Pattern(weight, reason, re.compile("|".join(f"(?:{p})" for p in patterns), _FLAGS))


PATTERNS: list[_Pattern] = [
    _p(60, "tells an AI to ignore or override its earlier instructions",
       r"\b(ignore|disregard|forget|override|bypass)\b[^.\n]{0,40}?\b(previous|prior|above|earlier|preceding|"
       r"all|any|other|original|system|your)\b[^.\n]{0,25}?\b(instructions?|prompts?|directions?|rules|"
       r"guidelines|context|constraints)\b"),
    _p(45, "asks for a positive review or a high rating",
       r"\bpositive\s+review\s+only\b",
       r"\b(give|write|provide|leave|output|produce)\b[^.\n]{0,20}?\b(positive|favou?rable|glowing|good|high|"
       r"excellent|strong|perfect)\b[^.\n]{0,15}?\b(reviews?|ratings?|scores?|evaluations?|assessments?|"
       r"feedback|recommendations?|marks?|grades?)\b"),
    _p(40, "tries to steer a hiring, grading or acceptance decision",
       r"\b(recommend(ing)?|accept(ing)?|hire|hiring|shortlist(ing)?|select(ing)?|advance|approve|pass)\b"
       r"[^.\n]{0,25}?\b(this|the)\s+(candidate|applicant|paper|submission|manuscript|resume|cv|student|"
       r"essay|proposal|assignment|application)\b",
       r"\b(exceptionally|extremely|highly|perfectly|uniquely)\s+(well[- ])?(qualified|suited)\b",
       r"\bstrong(ly)?\s+accept\b",
       r"\b(impactful\s+contributions?|exceptional\s+novelty|methodological\s+rigou?r)\b",
       r"\b(recommend|suggest|choose|pick|praise|promote|rank)\b[^.\n]{0,40}?\b(only|first|above\s+all|"
       r"instead|highest|top)\b"),
    _p(35, "tells the reader what to tell the user",
       r"\b(tell|inform|remind|advise|convince|persuade)\s+(the\s+)?(user|reader|customer|human|buyer|shopper)s?\b"),
    _p(35, "asks the reader to keep quiet about something",
       r"\b(do\s+not|don't|never|avoid)\s+(mention(ing)?|reveal(ing)?|disclos(e|ing)|point(ing)?\s+out|"
       r"highlight(ing)?|flag(ging)?|say(ing)?|note|tell(ing)?|discuss(ing)?)\b"),
    _p(35, "is addressed to AI reviewers, screeners or agents",
       r"\b(for|to|attention|note\s+to)\s*:?\s*(any\s+|all\s+)?(the\s+)?(llms?|ai|a\.i\.|language\s+models?|"
       r"automated|ai[- ]based)\s+(reviewers?|readers?|agents?|screeners?|systems?|tools?|assistants?|"
       r"models?|evaluators?|graders?)\b"),
    _p(30, "mentions an AI model or language model directly",
       r"\b(llms?|large\s+language\s+models?|language\s+models?|chat\s?gpt|gpt-?\d[\w.]*|openai|"
       r"claude|gemini|copilot|llama|mistral|deepseek)\b",
       r"\bai\s+(reviewers?|assistants?|models?|systems?|agents?|tools?|screeners?|screening)\b"),
    _p(30, "talks about prompts or hidden instructions",
       r"\b(system\s+prompt|new\s+instructions?|hidden\s+instructions?|prompt\s+injection|"
       r"developer\s+mode|jailbreak)\b",
       r"\binstructions?\s+(for|to)\s+(the\s+)?(ai|llm|model|assistant|reviewer|bot)s?\b"),
    _p(25, "gives the reader a role or persona",
       r"\byou\s+are\s+(now\s+)?(a|an|the)\b",
       r"\bact\s+as\s+(a|an|the)\b",
       r"\bpretend\s+(to\s+be|you\s+are)\b",
       r"\bas\s+an\s+ai\b", r"\bas\s+a\s+language\s+model\b", r"\bfrom\s+now\s+on\b"),
    _p(25, "asks for a specific score, grade or rank",
       r"\b(rate|score|grade|mark|rank)\b[^.\n]{0,30}?\b(10|ten|100|5|five)\s*(/|out\s+of)\s*(10|100|5)\b",
       r"\b(full|maximum|top|highest|perfect)\s+(marks|score|rating|grade)\b"),
    _p(20, "is phrased as a direct command",
       r"^\W*(please\s+)?((always|never|only|just)\s+)?(ignore|disregard|forget|summari[sz]e|respond|reply|answer|"
       r"output|print|write|say|include|recommend|rate|give|praise|state|emphasi[sz]e|describe|pretend|act|"
       r"send|delete|forward|reveal|call|visit|click|open)\b"),
]


@dataclass(frozen=True)
class AIScore:
    score: int
    reasons: list[str]


# Text that is visible in the file's structure but not on the page (alt text, document properties,
# comments) is only reported when it reads like an instruction. That needs stronger evidence than
# one keyword (for example a picture description that merely *mentions* "hidden instructions"),
# so it takes two signals or one strong one.
REPORT_THRESHOLD = 40


def score_text(text: str) -> AIScore:
    """Return a 0-100 score and the reasons behind it."""
    if not text or not text.strip():
        return AIScore(0, [])
    total = 0
    reasons: list[str] = []
    for pattern in PATTERNS:
        if pattern.regex.search(text):
            total += pattern.weight
            reasons.append(pattern.reason)
    return AIScore(min(100, total), reasons)


def describe(score: int) -> str:
    if score >= 50:
        return "Very likely an instruction aimed at AI"
    if score >= 25:
        return "Possibly an instruction aimed at AI"
    if score > 0:
        return "Some instruction-like wording"
    return "No instruction-like wording"

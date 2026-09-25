"""Reports: JSON (for scripts), a self-contained HTML page (for people), and terminal output."""

from __future__ import annotations

import copy
import datetime as dt
import html
import json
from typing import Iterable

from .ai_score import describe
from .models import SEVERITY_RANK, ScanResult
from .scanner import TYPE_LABELS
from .version import __version__

SEVERITY_LABEL = {"high": "High", "medium": "Medium", "low": "Low", "info": "Note"}
VERDICT_ICON = {"ai_instructions": "⚠", "hidden_text": "⚠", "check": "?", "minor": "•", "clean": "✓", "error": "✕"}


def filter_results(results: Iterable[ScanResult], min_severity: str) -> list[ScanResult]:
    """Copies of the results keeping only findings at or above min_severity."""
    out = []
    for result in results:
        clone = copy.copy(result)
        clone.findings = [f for f in result.findings if SEVERITY_RANK[f.severity] >= SEVERITY_RANK[min_severity]]
        out.append(clone)
    return out


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _size(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} bytes"


def _stats_line(result: ScanResult) -> str:
    s = result.stats
    parts = []
    if "pages" in s:
        parts.append(f"{s['pages']} page{'s' if s['pages'] != 1 else ''}")
    if "paragraphs" in s:
        parts.append(f"{s['paragraphs']} paragraphs")
    if "elements" in s:
        parts.append(f"{s['elements']} HTML elements")
    if "lines" in s:
        parts.append(f"{s['lines']} lines")
    if "characters" in s:
        parts.append(f"{s['characters']:,} characters of text")
    return ", ".join(parts)


# ---------------------------------------------------------------- JSON

def to_json(results: list[ScanResult]) -> str:
    payload = {
        "tool": "hidden-text-finder",
        "version": __version__,
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "files": [r.to_dict() for r in results],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------- HTML

CSS = """
:root{--bg:#f5f6f8;--card:#fff;--text:#1c2230;--muted:#5a6374;--border:#dde1e8;--code:#f1f3f6;
--high:#b3261e;--high-bg:#fdecea;--medium:#9a5b00;--medium-bg:#fff3dc;--low:#1f5fbf;--low-bg:#e7effc;
--info:#566070;--info-bg:#eceff3;--ai:#6b21a8;--ai-bg:#f3e8ff;--ok:#1b7f3b;--ok-bg:#e6f5ea}
@media (prefers-color-scheme:dark){:root{--bg:#11141a;--card:#1a1f28;--text:#e7eaf0;--muted:#9ba4b4;
--border:#2c3342;--code:#232a36;--high:#ff8a80;--high-bg:#3b1e1e;--medium:#ffc266;--medium-bg:#3a2b12;
--low:#8ab4ff;--low-bg:#1b2a45;--info:#a3acbb;--info-bg:#262c37;--ai:#d8a8ff;--ai-bg:#2e1e3f;--ok:#7fd99a;--ok-bg:#16301f}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:980px;margin:0 auto;padding:28px 16px 56px}
.brand{font-weight:700;letter-spacing:.02em;color:var(--muted);font-size:13px;text-transform:uppercase}
h1{margin:4px 0 2px;font-size:28px}
.sub{color:var(--muted);margin:0 0 20px}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px 20px;margin:0 0 18px}
table.overview{width:100%;border-collapse:collapse;font-size:14px}
table.overview th,table.overview td{padding:8px 6px;border-bottom:1px solid var(--border);text-align:left;vertical-align:top}
table.overview th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.03em}
table.overview td.n{text-align:right;font-variant-numeric:tabular-nums}
.file h2{margin:0 0 6px;font-size:20px;word-break:break-all}
.verdict{display:inline-block;padding:6px 12px;border-radius:999px;font-weight:600;margin:4px 0 12px}
.v-ai_instructions{background:var(--ai-bg);color:var(--ai)}
.v-hidden_text{background:var(--high-bg);color:var(--high)}
.v-check{background:var(--medium-bg);color:var(--medium)}
.v-minor{background:var(--low-bg);color:var(--low)}
.v-clean{background:var(--ok-bg);color:var(--ok)}
.v-error{background:var(--info-bg);color:var(--info)}
dl.meta{display:grid;grid-template-columns:max-content 1fr;gap:4px 14px;margin:0 0 12px;font-size:13.5px}
dl.meta dt{color:var(--muted)}
dl.meta dd{margin:0;word-break:break-all}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12.5px}
.notes{background:var(--info-bg);border-radius:8px;padding:8px 12px;margin:0 0 14px;font-size:13.5px}
.notes p{margin:2px 0}
.finding{border:1px solid var(--border);border-left:5px solid var(--info);border-radius:10px;padding:14px 16px;margin:12px 0}
.finding.sev-high{border-left-color:var(--high)}
.finding.sev-medium{border-left-color:var(--medium)}
.finding.sev-low{border-left-color:var(--low)}
.fhead{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px 10px}
.fhead h3{margin:0;font-size:16px;flex:1 1 260px}
.num{color:var(--muted);font-weight:700;font-variant-numeric:tabular-nums}
.badge{font-size:12px;font-weight:700;padding:2px 8px;border-radius:999px;text-transform:uppercase;letter-spacing:.03em}
.badge.high{background:var(--high-bg);color:var(--high)}
.badge.medium{background:var(--medium-bg);color:var(--medium)}
.badge.low{background:var(--low-bg);color:var(--low)}
.badge.info{background:var(--info-bg);color:var(--info)}
.where{color:var(--muted);font-size:13.5px}
.ai-flag{background:var(--ai-bg);color:var(--ai);border-radius:8px;padding:6px 10px;margin:10px 0 0;font-size:13.5px}
.label{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em;margin:12px 0 4px;font-weight:600}
pre.hidden{background:var(--code);border-radius:8px;padding:10px 12px;margin:0;white-space:pre-wrap;word-break:break-word;
font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13.5px}
p.explain{margin:10px 0 0;color:var(--text)}
p.also{margin:6px 0 0;color:var(--muted);font-size:13.5px}
details{margin-top:10px}
summary{cursor:pointer;color:var(--muted);font-size:13.5px}
details dl{display:grid;grid-template-columns:max-content 1fr;gap:3px 12px;font-size:13px;margin:8px 0 0}
details dt{color:var(--muted)}
details dd{margin:0;word-break:break-word}
.empty{color:var(--ok);font-weight:600}
footer{color:var(--muted);font-size:13px}
footer h2{font-size:15px;color:var(--text);margin:0 0 6px}
footer ul{margin:6px 0 0;padding-left:20px}
@media (max-width:600px){main{padding:18px 12px 40px}.card{padding:14px}h1{font-size:23px}
table.overview th:nth-child(2),table.overview td:nth-child(2){display:none}}
"""


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _details_html(finding) -> str:
    rows = []
    for key, value in finding.details.items():
        if key in ("runs",):
            continue
        if isinstance(value, dict):
            value = ", ".join(f"{k}: {v}" for k, v in value.items())
        elif isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        rows.append(f"<dt>{_esc(key.replace('_', ' '))}</dt><dd>{_esc(value)}</dd>")
    rows.append(f"<dt>technique id</dt><dd class='mono'>{_esc(finding.technique)}</dd>")
    rows.append(f"<dt>AI-instruction score</dt><dd>{finding.ai_score}/100 ({_esc(describe(finding.ai_score))})</dd>")
    if finding.ai_reasons:
        rows.append(f"<dt>why</dt><dd>{_esc('; '.join(finding.ai_reasons))}</dd>")
    return "<details><summary>Technical details</summary><dl>" + "".join(rows) + "</dl></details>"


def _finding_html(n: int, finding) -> str:
    parts = [f"<article class='finding sev-{finding.severity}'>",
             "<div class='fhead'>",
             f"<span class='num'>#{n}</span>",
             f"<span class='badge {finding.severity}'>{SEVERITY_LABEL[finding.severity]}</span>",
             f"<h3>{_esc(finding.title)}</h3>",
             f"<span class='where'>{_esc(finding.location)}</span>",
             "</div>"]
    if finding.is_ai_instruction:
        reasons = "; ".join(finding.ai_reasons)
        parts.append(f"<div class='ai-flag'>⚠ Reads like an instruction aimed at an AI "
                     f"(score {finding.ai_score}/100): {_esc(reasons)}.</div>")
    parts.append("<div class='label'>Hidden text</div>")
    parts.append(f"<pre class='hidden'>{_esc(finding.text)}</pre>")
    parts.append(f"<p class='explain'>{_esc(finding.explanation)}</p>")
    if finding.also:
        parts.append(f"<p class='also'>Also hidden by: {_esc(', '.join(finding.also))}</p>")
    parts.append(_details_html(finding))
    parts.append("</article>")
    return "".join(parts)


def to_html(results: list[ScanResult], title: str = "Hidden-Text Finder report") -> str:
    out = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>{_esc(title)}</title><style>{CSS}</style></head><body><main>",
        "<header><div class='brand'>Hidden-Text Finder</div>",
        "<h1>Scan report</h1>",
        f"<p class='sub'>Generated {_now()} · version {__version__} · {len(results)} file"
        f"{'s' if len(results) != 1 else ''} scanned</p></header>",
    ]
    if len(results) > 1:
        out.append("<section class='card'><table class='overview'><thead><tr><th>File</th><th>Type</th>"
                   "<th>Result</th><th>High</th><th>Medium</th><th>Low</th></tr></thead><tbody>")
        for i, r in enumerate(results, 1):
            code, sentence = r.verdict
            c = r.counts
            out.append(f"<tr><td><a href='#file{i}'>{_esc(r.file_name)}</a></td>"
                       f"<td>{_esc(TYPE_LABELS.get(r.file_type, r.file_type))}</td>"
                       f"<td>{VERDICT_ICON[code]} {_esc(sentence)}</td>"
                       f"<td class='n'>{c['high']}</td><td class='n'>{c['medium']}</td><td class='n'>{c['low']}</td></tr>")
        out.append("</tbody></table></section>")

    for i, r in enumerate(results, 1):
        code, sentence = r.verdict
        out.append(f"<section class='card file' id='file{i}'><h2>{_esc(r.file_name)}</h2>")
        out.append(f"<div class='verdict v-{code}'>{VERDICT_ICON[code]} {_esc(sentence)}</div>")
        out.append("<dl class='meta'>")
        out.append(f"<dt>Type</dt><dd>{_esc(TYPE_LABELS.get(r.file_type, r.file_type))}</dd>")
        out.append(f"<dt>Size</dt><dd>{_esc(_size(r.size_bytes))}</dd>")
        if r.stats:
            out.append(f"<dt>Contents</dt><dd>{_esc(_stats_line(r))}</dd>")
        out.append(f"<dt>SHA-256</dt><dd class='mono'>{_esc(r.sha256)}</dd>")
        out.append(f"<dt>Scanned</dt><dd>{_esc(r.scanned_at)}</dd></dl>")
        if r.notes:
            out.append("<div class='notes'>" + "".join(f"<p>ℹ {_esc(n)}</p>" for n in r.notes) + "</div>")
        if r.error:
            out.append(f"<p>{_esc(r.error)}</p>")
        elif not r.findings:
            out.append("<p class='empty'>Nothing hidden was found in this file.</p>")
        for n, finding in enumerate(r.findings, 1):
            out.append(_finding_html(n, finding))
        out.append("</section>")

    out.append(
        "<footer class='card'><h2>How to read this report</h2><ul>"
        "<li><b>High</b>: text a person can't see at all, or hidden text that reads like an instruction to an AI.</li>"
        "<li><b>Medium</b>: text that is very hard to see, or invisible characters inside words.</li>"
        "<li><b>Low</b> and <b>Note</b>: usually harmless, but worth a glance (for example screen-reader labels or scanner text layers).</li>"
        "<li>The AI-instruction score is a transparent keyword check, not an AI model. It can miss cleverly worded "
        "instructions and occasionally flag innocent text.</li>"
        "<li>Not checked: text inside pictures, external stylesheets and scripts of web pages, and attachments.</li>"
        "<li>The SHA-256 fingerprint identifies the exact file that was scanned; change one byte and it changes completely.</li>"
        "</ul></footer></main></body></html>"
    )
    return "".join(out)


# ---------------------------------------------------------------- terminal

def print_results(results: list[ScanResult], *, details: bool = False, quiet: bool = False,
                  console=None) -> None:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    console = console or Console()
    colours = {"high": "bold red", "medium": "yellow", "low": "cyan", "info": "dim"}
    verdict_style = {"ai_instructions": "bold magenta", "hidden_text": "bold red", "check": "yellow", "minor": "cyan",
                     "clean": "bold green", "error": "bold yellow"}

    if not quiet:
        for r in results:
            code, sentence = r.verdict
            header = Text.assemble((f"{VERDICT_ICON[code]} {sentence}", verdict_style[code]))
            sub = f"{TYPE_LABELS.get(r.file_type, r.file_type)} · {_stats_line(r)}" if r.stats else ""
            body = Text.assemble(header, ("\n" + sub) if sub else "", style="")
            for note in r.notes:
                body.append(f"\nℹ {note}", style="dim")
            console.print(Panel(body, title=f"[bold]{r.file_name}[/bold]", title_align="left", expand=True))
            if not r.findings:
                continue
            if details:
                for n, f in enumerate(r.findings, 1):
                    head = Text.assemble((f"#{n} ", "dim"), (SEVERITY_LABEL[f.severity].upper(), colours[f.severity]),
                                         (f"  {f.title}", "bold"), (f"  ({f.location})", "dim"))
                    console.print(head)
                    console.print(Text("   Hidden text: ", style="dim") + Text(f.text))
                    if f.is_ai_instruction:
                        console.print(Text(f"   Reads like an instruction aimed at an AI ({f.ai_score}/100): "
                                           + "; ".join(f.ai_reasons), style="magenta"))
                    console.print(Text("   " + f.explanation, style="dim"))
                    if f.also:
                        console.print(Text("   Also hidden by: " + ", ".join(f.also), style="dim"))
                    console.print()
                continue
            table = Table(box=box.SIMPLE_HEAD, expand=True, show_lines=False, pad_edge=False)
            table.add_column("#", justify="right", style="dim", no_wrap=True)
            table.add_column("Level", no_wrap=True)
            table.add_column("What", ratio=3)
            table.add_column("Where", ratio=2)
            table.add_column("Hidden text", ratio=5)
            table.add_column("AI?", justify="right", no_wrap=True)
            for n, f in enumerate(r.findings, 1):
                text = f.text if len(f.text) <= 140 else f.text[:137] + "..."
                ai = Text(f"{f.ai_score}", style="bold magenta") if f.is_ai_instruction else Text(
                    f"{f.ai_score}" if f.ai_score else "-", style="dim")
                table.add_row(str(n), Text(SEVERITY_LABEL[f.severity], style=colours[f.severity]),
                              f.title, f.location, text, ai)
            console.print(table)
    if len(results) > 1 or quiet:
        summary = Table(title="Summary", box=box.SIMPLE_HEAD, expand=False)
        summary.add_column("File")
        summary.add_column("Result")
        for level in ("high", "medium", "low"):
            summary.add_column(SEVERITY_LABEL[level], justify="right")
        for r in results:
            code, sentence = r.verdict
            c = r.counts
            summary.add_row(r.file_name, Text(sentence, style=verdict_style[code]),
                            str(c["high"]), str(c["medium"]), str(c["low"]))
        console.print(summary)

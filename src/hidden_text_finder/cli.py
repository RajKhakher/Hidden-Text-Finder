"""Command-line interface.

Examples:
    htf paper.pdf
    htf resume.docx page.html notes.md --html report.html
    htf submissions/ --json results.json --marked marked/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .models import SEVERITIES, SEVERITY_RANK
from .scanner import SUPPORTED_EXTENSIONS, iter_supported_files, scan_file
from .version import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="htf",
        description="Find text that people can't see but AI tools still read "
                    "(PDF, Word .docx, HTML, Markdown and plain text).",
        epilog="Exit codes: 0 = nothing at or above --fail-on, 1 = hidden text found, 2 = a file couldn't be scanned.",
    )
    parser.add_argument("paths", nargs="+", metavar="PATH", help="files or folders to scan (folders are searched recursively)")
    parser.add_argument("--html", metavar="FILE", help="write a readable HTML report to FILE")
    parser.add_argument("--json", metavar="FILE", help="write a machine-readable JSON report to FILE ('-' for stdout)")
    parser.add_argument("--marked", metavar="DIR",
                        help="write marked copies to DIR: PDFs get red boxes around hidden text, "
                             "Word files get hidden text made visible")
    parser.add_argument("--min-severity", choices=SEVERITIES, default="info",
                        help="leave out findings below this level (default: info, i.e. show everything)")
    parser.add_argument("--fail-on", choices=[*SEVERITIES, "never"], default="high",
                        help="exit with code 1 if anything at or above this level is found (default: high)")
    parser.add_argument("--details", action="store_true", help="show a plain-English explanation under each finding")
    parser.add_argument("--quiet", action="store_true", help="only print the summary table")
    parser.add_argument("--no-color", action="store_true", help="plain text output without colours")
    parser.add_argument("--version", action="version", version=f"hidden-text-finder {__version__}")
    return parser


def _utf8_output() -> None:
    """Windows consoles and CI logs may default to an old code page that can't print ⚠ or ⟦ ⟧."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream.encoding and stream.encoding.lower().replace("-", "") != "utf8":
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _utf8_output()
    from rich.console import Console

    from .report import filter_results, print_results, to_html, to_json

    console = Console(no_color=args.no_color, highlight=False, stderr=args.json == "-")
    files = iter_supported_files(args.paths)
    missing = [p for p in files if not p.exists()]
    for path in missing:
        console.print(f"[yellow]Not found:[/yellow] {path}")
    files = [p for p in files if p.exists()]
    if not files:
        console.print("[yellow]No supported files to scan.[/yellow] Supported: " + ", ".join(sorted(SUPPORTED_EXTENSIONS)))
        return 2

    results = []
    with console.status("Scanning...", spinner="dots") if len(files) > 1 else _Null():
        for path in files:
            results.append(scan_file(path))
    results = filter_results(results, args.min_severity)

    print_results(results, details=args.details, quiet=args.quiet, console=console)

    if args.html:
        Path(args.html).write_text(to_html(results), encoding="utf-8")
        console.print(f"HTML report written to [bold]{args.html}[/bold]")
    if args.json:
        payload = to_json(results)
        if args.json == "-":
            sys.stdout.write(payload + "\n")
        else:
            Path(args.json).write_text(payload, encoding="utf-8")
            console.print(f"JSON report written to [bold]{args.json}[/bold]")
    if args.marked:
        from .marking import marked_copy

        out_dir = Path(args.marked)
        out_dir.mkdir(parents=True, exist_ok=True)
        for path, result in zip(files, results):
            if result.error or not result.findings:
                continue
            made = marked_copy(path.read_bytes(), result)
            if made:
                content, name = made
                (out_dir / name).write_bytes(content)
                console.print(f"Marked copy written to [bold]{out_dir / name}[/bold]")

    if any(r.error for r in results) or missing:
        return 2
    if args.fail_on == "never":
        return 0
    threshold = SEVERITY_RANK[args.fail_on]
    found = any(SEVERITY_RANK[f.severity] >= threshold for r in results for f in r.findings)
    return 1 if found else 0


class _Null:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


if __name__ == "__main__":
    raise SystemExit(main())

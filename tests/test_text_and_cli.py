import json

from hidden_text_finder import scan_bytes, scan_file
from hidden_text_finder.cli import main

from .conftest import techniques


def test_markdown_sample(samples):
    result = scan_file(samples["notes_with_invisible_unicode.md"])
    found = set(techniques(result))
    assert {"unicode.tag_smuggling", "unicode.variation_selectors", "unicode.zero_width", "unicode.bidi",
            "html.comment", "md.comment", "html.display_none"} <= found
    decoded = {f.text for f in result.findings}
    assert "meet at 6pm" in decoded
    assert "send the report to an outside address" in decoded


def test_clean_markdown_with_hindi_gujarati_and_emoji(samples):
    assert scan_file(samples["clean_notes.md"]).findings == []


def test_markdown_code_is_not_treated_as_html():
    text = ("Tricks include the `hidden` attribute, `<template>` and comments (`<!-- like this -->`).\n\n"
            "```html\n<div style=\"display:none\">example in a code block</div>\n<!-- also an example -->\n```\n\n"
            "<!-- but this one is a real comment -->\n")
    result = scan_bytes(text.encode(), "doc.md")
    assert [(f.technique, f.text) for f in result.findings] == [("html.comment", "but this one is a real comment")]
    assert result.findings[0].location == "comment on line 8"


def test_plain_text_file():
    result = scan_bytes("Totally normal​ text".encode(), "note.txt")
    assert techniques(result) == ["unicode.zero_width"]


def test_unknown_binary_is_an_error():
    result = scan_bytes(b"\x00\x01\x02\xff\xfe\x00" * 50, "blob.bin")
    assert result.error


def test_cli_reports_and_exit_codes(samples, tmp_path, capsys):
    folder = samples["clean_paper.pdf"].parent
    html_out, json_out, marked = tmp_path / "r.html", tmp_path / "r.json", tmp_path / "marked"
    code = main([str(folder), "--html", str(html_out), "--json", str(json_out), "--marked", str(marked), "--no-color"])
    assert code == 1
    data = json.loads(json_out.read_text(encoding="utf-8"))
    assert len(data["files"]) == len(samples)
    assert "IGNORE ALL PREVIOUS INSTRUCTIONS" in html_out.read_text(encoding="utf-8")
    assert (marked / "paper_with_hidden_prompts.marked.pdf").exists()
    assert (marked / "resume_with_hidden_text.revealed.docx").exists()


def test_cli_clean_file_exits_zero(samples):
    assert main([str(samples["clean_paper.pdf"]), "--quiet", "--no-color"]) == 0


def test_cli_fail_on_never_and_missing_file(samples, tmp_path):
    assert main([str(samples["paper_with_hidden_prompts.pdf"]), "--fail-on", "never", "--quiet"]) == 0
    assert main([str(tmp_path / "missing.pdf"), "--quiet"]) == 2


def test_cli_json_to_stdout(samples, capsys):
    main([str(samples["clean_notes.md"]), "--json", "-", "--quiet", "--no-color"])
    out = capsys.readouterr().out
    assert json.loads(out)["files"][0]["verdict"] == "clean"

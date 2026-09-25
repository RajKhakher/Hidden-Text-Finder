# Sample files

Every sample is harmless: the hidden sentences are made-up test phrases. A couple are copied from
cases reported in the news, such as hidden "give a positive review only" prompts in research papers.
Rebuild the generated ones with `python samples/make_samples.py`.

| File | What's hidden in it |
|---|---|
| `paper_with_hidden_prompts.pdf` | white text, 0.8 pt text, text under a white box, text outside the page, "invisible" render mode, a switched-off layer, transparent text, a faint watermark, an instruction in the document properties |
| `latex_paper_with_white_text.pdf` | built with pdflatex from the `.tex` file next to it: `\color{white}` prompts (the trick used in the reported papers) and black text in a black box |
| `resume_with_hidden_text.docx` | Word's Hidden font, white keyword stuffing, 1 pt text, a paragraph *style* that makes text white, 1%-width text, a tracked deletion, a comment, an instruction in the document properties |
| `resume_exported_by_libreoffice.pdf` | the resume above saved as PDF by LibreOffice |
| `webpage_with_hidden_text.html` | `display:none`, white text, `font-size:0`, off-screen text, `opacity:0`, `clip-path`, the `hidden` attribute, an HTML comment, a meta description, invisible tag characters, zero-width spaces |
| `notes_with_invisible_unicode.md` | tag-character and variation-selector messages, a zero-width binary message, zero-width spaces inside a word, a right-to-left override in code, HTML and Markdown comments, a hidden `<span>` |
| `scanned_letter.pdf` | nothing suspicious: a scanned page with a normal invisible OCR layer (reported as a note) |
| `clean_paper.pdf`, `clean_resume.docx`, `clean_page.html`, `clean_notes.md` | nothing: tricky-but-normal layouts (white titles on dark banners, shaded table headers, tiny footnotes, a screen-reader label, Hindi and Gujarati joiners, emoji families, flag emoji) that should *not* raise alarms |

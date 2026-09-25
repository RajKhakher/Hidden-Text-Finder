# How Hidden-Text Finder works

This page explains every check in plain language, from the big idea down to the numbers used.

## The big idea

People and AI tools read the same document in two different ways:

- **A person sees pixels.** A viewer draws the page and your eyes read the result.
- **An AI tool reads characters.** Before a PDF, Word file or web page reaches a language model,
  a program pulls the *text* out of the file: the list of letters stored inside it. Colour,
  size and position are thrown away.

Hiding text means putting letters into the file that never turn into visible pixels.
The finder's job is to compare the two views: *what characters are stored* against *what a
person would actually see*. Anything stored but not seen is reported.

## Severity levels

| Level | Meaning |
|---|---|
| **High** | A person can't see it at all, or the hidden text reads like an instruction to an AI |
| **Medium** | Very hard to see (faint or tiny), or invisible characters inside words |
| **Low** | Usually harmless but worth a glance (screen-reader labels, soft hyphens, deleted text kept by Track Changes) |
| **Note** | Normal things you may still want to know about (a scanner's text layer, comments) |

Any hidden text with an AI-instruction score of 50 or more is raised to **High**. A score of 25–49
raises it by one level.

---

## PDFs

### What is inside a PDF page

A PDF page is a list of drawing instructions carried out in order, a bit like a recipe:

```
set colour white
move to (55, 262)
show text "IGNORE ALL PREVIOUS INSTRUCTIONS..."
set colour black
fill rectangle (50, 588) to (545, 606)
...
```

Later instructions paint over earlier ones, the way a later brush stroke covers an earlier
one. Every "show text" instruction also stores the characters themselves, and that is what
copy-paste and AI tools read.

### Step 1: read every piece of text and how it is drawn

Using PyMuPDF's *text trace*, the finder gets, for every piece of text:

- **Font size in points.** A point is 1/72 of an inch; body text is usually 10–12 pt.
  Below 2 pt is **microscopic** (high). 2–3 pt is **very small** (medium).
- **Colour.** Print-ready files often store colour as CMYK (cyan, magenta, yellow, black ink).
  The finder converts that to on-screen RGB first.
- **Opacity.** 0% opacity means **fully transparent**, like writing with clear ink.
- **Render mode.** PDF has a setting that literally means "draw this text as nothing"
  (mode 3). Scanners use it on purpose (see step 6). Anywhere else it's a red flag.
- **Position.** If the text lies entirely outside the page's visible area (the *crop box*),
  no viewer will ever show it: **text outside the page**.

### Step 2: render the page twice and compare

The finder draws the page as a picture at 216 dots per inch. It then draws it a **second time with every
piece of text removed** (shapes and pictures stay). Comparing the two pictures answers two questions for each
piece of text:

1. **Did this text change any pixels?** Visible text changes hundreds or thousands of pixels
   in its box. White text on a white page, or text under a white box, changes **none**.
2. **What colour is really behind it?** The second picture has no text in it, so the most common
   colour in the text's box there is the true background. The text's own letters can't
   confuse this. (Without this, a solid character like █ would look "the same colour as its
   background", because it fills its own box.)

### Step 3: contrast, measured the way accessibility guidelines do

"Same colour" is measured with the **contrast ratio** from WCAG, the web accessibility
guidelines:

1. Turn each colour into its **relative luminance**: how bright it looks to a human eye.
   Green counts most, blue least, and the scale is adjusted because eyes don't respond to light
   in a straight line.
2. Contrast ratio = (brighter luminance + 0.05) / (darker luminance + 0.05).

The result runs from **1.0** (identical colours) to **21** (black on white).

| Text on a white page | Contrast | Finder says |
|---|---|---|
| white `#ffffff` | 1.00 | same colour as background (high) |
| near-white `#f7f7f7` | 1.07 | same colour as background (high) |
| `#f2f2f2` | 1.12 | the line between the two levels |
| light watermark grey `#dbdbdb` | 1.38 | very faint (medium) |
| cyan `#00ffff` | 1.25 | very faint (medium) |
| mid grey `#777777` | 4.48 | fine |
| black | 21.0 | fine |

Below **1.12** is reported as *the same colour as its background* (high). Below **1.6** is
*very faint* (medium). Light watermarks land in "very faint", which is correct: they are hard
to see, even if usually harmless.

### Step 4: what was painted on top (and failed redactions)

PyMuPDF can list everything on the page **in drawing order** with its position. If a
filled shape or picture drawn *after* a piece of text covers at least 90% of it, and the text
changed no pixels, the text is **hidden under a shape or picture**.

This also catches a classic forensic mistake, the **failed redaction**. Someone "redacts" a
document by drawing a black box over black text. On screen it looks censored, but the
words are still in the file and anyone can copy them out. When the box is dark and the text is
dark, the report says so.

If the text *does* change pixels despite something on top (for example a see-through yellow
highlight), it's visible and nothing is reported.

### Step 5: switched-off layers

PDFs can have **layers** ("optional content") that can be switched on and off, like layers
in Photoshop. Text in a layer that is off is invisible in viewers. However, common extractors
such as `pypdf`, which many AI apps use, still pull it out. The finder makes a copy of
the PDF with every layer switched on, reads the text again, and reports anything that only
appears then.

### Step 6: scanned pages

A scanned document is a picture of each page. Scanners add an **invisible text layer** (render
mode 3, see step 1) on top so you can search and copy the words. That is normal, so when a page
is mostly covered by a picture and most of its text is invisible, the finder reports **one note**
for the page instead of hundreds of alarms. The finder can't yet check that the invisible layer
matches the picture (see the roadmap).

### Step 7: rebuilding words

Many PDFs, including everything made with LaTeX, contain **no space characters**. Words are just
placed apart. Without care, hidden text comes out as `IGNOREALLPREVIOUSINSTRUCTIONS` and the
AI-instruction check can't read it. The finder puts spaces back where the gap between letters is
wider than 15% of the font size. It treats a jump back to the left margin as a line break, and
rejoins words hyphenated at the end of a line (`IG-` + `NORE` → `IGNORE`).

### Also checked

- **Hidden annotations:** notes or comments whose "hidden" flag is set but which still hold text.
- **Document properties:** title, subject and keywords. These aren't on the page, and some AI
  tools read them, so they are reported only when they read like instructions.
- **Attachments** are listed (not scanned).

---

## Word documents (.docx)

### What is inside a .docx

A .docx file is a **zip folder of XML files**. Rename one to `.zip` and open it to see them. The
body is in `word/document.xml`. It is made of paragraphs (`<w:p>`), which are made of **runs**
(`<w:r>`): stretches of text that share the same formatting.

### The four formatting layers

A run's final look comes from four layers. Each later layer overrides the earlier ones:

```
document defaults  →  paragraph style  →  character style  →  the run's own settings
```

Someone hiding text can do it at any layer. For example, a paragraph style called "Quiet
Note" can quietly make every paragraph using it white. So the finder works out each run's
**final** formatting before checking it.

### The checks

| Setting in the XML | What it means | Finder says |
|---|---|---|
| `<w:vanish/>` | Word's **Hidden** font setting: not shown or printed unless "Show hidden text" is on | hidden in Word (high) |
| `<w:color w:val="FFFFFF"/>` | text colour, compared against the highlight, the run's shading, the paragraph's shading, the table cell's shading, and finally the page colour | same colour / very faint |
| `<w:sz w:val="2"/>` | font size in **half-points**, so 2 means 1 pt | microscopic (high) |
| `<w:w w:val="1"/>` | character width as a percentage: 1% squashes a sentence into a line | squeezed (high) |
| `<w:webHidden/>` | hidden only when shown as a web page | medium |
| `<w:del>` … `<w:delText>` | text deleted with Track Changes on, still stored in the file | low |

Comments, picture descriptions ("alt text") and document properties are also read. Alt text
and properties are reported only when they read like instructions.

### The revealed copy

`--marked` writes a copy in which every flagged run is set to visible, dark red on yellow, at
normal size and width. Word is strict about the **order** of formatting elements inside a
run, so the finder inserts each setting in the position Word's format expects. Otherwise Word
would say the file is damaged.

---

## Web pages

### How CSS hides things

A web page is HTML (the content) plus CSS (the styling rules). CSS can hide text in two
different ways, and the difference matters:

1. **Hiding a whole element and everything inside it.** `display: none`, the `hidden`
   attribute, `opacity: 0`, moving it thousands of pixels off-screen, clipping it to zero size,
   or `transform: scale(0)`. Nothing inside can escape.
2. **Hiding at the level of the text.** White text on a white background, `font-size: 0`,
   `visibility: hidden`. An inner element *can* undo these: a container with `font-size: 0` whose
   children set `font-size: 14px` is a common, harmless layout trick. So each piece of text is
   judged by the styles that actually apply to it.

### A small CSS engine

The finder reads CSS from `<style>` blocks and `style="..."` attributes, finds which elements each
rule applies to, and decides which rule wins when several apply to the same element:

- **`!important` wins.**
- Then **inline `style=""`** beats rules in `<style>` blocks.
- Then **more specific selectors** win: an `#id` beats a `.class`, which beats a tag name.
- Then **the later rule** wins.

Rules for printing (`@media print`) are ignored because they don't affect the screen.

### Screen-reader text

Websites often hide text visually on purpose for **screen readers** used by blind and
low-vision people. A link might carry "(opens in a new tab)" that sighted users don't need. This
uses a well-known CSS pattern and class names like `sr-only`, so the finder reports it as **low**:
worth a glance, but usually good practice.

### Also checked

HTML comments (`<!-- ... -->`), and `alt`, `title` and `aria-label` attributes, `<meta
name="description">` and hidden form inputs whose text reads like an instruction.

**Not checked:** stylesheets loaded from other files, and JavaScript. Scripts can change a
page after it loads, and the finder doesn't run them. The report says when a page has either.

---

## Invisible Unicode characters

Every character in a computer is a numbered **code point** in the Unicode standard. For
example, `A` is U+0041. Some code points are deliberately invisible.

### Zero-width characters

The **zero-width space** (U+200B), **joiner** (U+200D), **non-joiner** (U+200C), **word joiner**
(U+2060) and similar characters take up no room on screen. Put inside a word (`pas⟦ZWSP⟧sword`, shown here with a visible marker)
they break keyword filters. A long run of them can carry a whole message in binary, for example
zero-width space = 0 and non-joiner = 1. The finder tries the common layouts and shows the decoded
message when one reads as text.

Some of these characters have honest jobs, and the finder skips them there:

- **Hindi, Gujarati and other Indic scripts** use the joiner and non-joiner to control how
  letters combine (क्‍ष vs क्ष).
- **Emoji families and professions** are several emoji glued together with joiners (👩‍💻).
- **Thai, Lao, Khmer and Burmese** don't put spaces between words, so zero-width spaces mark
  where lines may break.
- A **byte order mark** (U+FEFF) at the very start of a file is normal.

### Tag characters: a hidden copy of the alphabet

Unicode has a block of **tag characters** (U+E0020 to U+E007E) that mirror the printable ASCII
characters one-to-one. None of them are drawn. A sentence written with them is completely
invisible, yet many AI models read it as normal text. Decoding is simple subtraction:

```
hidden character U+E0049  →  0xE0049 − 0xE0000 = 0x49  →  "I"
```

The finder decodes the whole run and shows the message. The one honest use is emoji
flags for regions like Scotland: 🏴 followed by a short tag code. These are recognised and skipped.

### Variation selectors: data hidden in an emoji

**Variation selectors** normally tell a device which style of emoji to draw, one at a time
(❤ vs ❤️). There are exactly 256 of them, so each one can stand for one byte, and a chain of them
attached to a single emoji can carry a whole hidden message. The finder turns the chain back into
bytes and then into text.

### Text-direction tricks ("Trojan Source")

Characters such as the **right-to-left override** (U+202E) exist for Arabic and Hebrew. Misused
in other text, especially source code, they make the text **display** in a different order from
how it is **stored**. What you see is not what the computer runs or reads. These are reported
unless the text actually contains right-to-left script.

---

## The AI-instruction score

Hidden text isn't automatically an attack: it might be keywords, a watermark or leftover
notes. The score (0–100) estimates how much the hidden text reads like **an instruction aimed
at an AI**. Every point comes from a named pattern, and the matched reasons are shown in the report:

| Points | Pattern |
|---|---|
| 60 | tells an AI to ignore or override its earlier instructions |
| 45 | asks for a positive review or a high rating |
| 40 | tries to steer a hiring, grading or acceptance decision |
| 35 | asks the reader to keep quiet about something |
| 35 | tells the reader what to tell the user |
| 35 | is addressed to AI reviewers, screeners or agents |
| 30 | mentions an AI model or language model directly |
| 30 | talks about prompts or hidden instructions |
| 25 | gives the reader a role or persona ("you are a…") |
| 25 | asks for a specific score, grade or rank ("10/10") |
| 20 | is phrased as a direct command |

The points add up and are capped at 100. **50 or more** counts as *very likely an instruction
aimed at AI*.

Picture descriptions, document properties and page comments aren't hidden text as such. They are
only reported when they score **40 or more**, meaning two signals or one strong one. A description
that merely *mentions* "hidden instructions" doesn't count as an instruction.

This is deliberately not an AI model. It works offline, gives the same answer every time,
and every point can be explained. The trade-off is that a cleverly reworded instruction can slip
past it. That is why the score only **ranks** findings: hidden text is always reported, whatever
it scores.

---

## What it can't see (yet)

- Text inside images, including checking a scanned page's text layer against its picture.
- Web page content added by JavaScript or hidden by external stylesheets.
- PowerPoint and Excel files, emails, and attachments inside PDFs.
- Instructions written in languages other than English (the score only knows English patterns;
  the hiding checks work for any language).

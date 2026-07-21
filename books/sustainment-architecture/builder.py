#!/usr/bin/env python3
"""
Build a single-page, book-styled HTML document from _index.yaml + chapter .adoc files.

Content is laid out as a stack of fixed-size page "sheets" (8in x 10in,
matching Crafting Interpreters' trim size), with a 1in left / 0.5in right /
66pt top / 30pt bottom margin. There is no browser layout engine available
here, so page breaks are decided by a text-length heuristic (estimated
wrapped line count per block) rather than exact rendered height -- treat the
fit as approximate, not pixel-perfect.

Each page's content box is split into a 4.5in running-text column, a 0.25in
gap, and a 1.75in margin column. Three ways to put something in that
column:
  - `[[margin: some note]]`, written anywhere inside a paragraph -- a
    short, plain aside, no reference marker.
  - `[[sidenote: some note]]`, same but leaves a small superscript number
    at that point in the text, matched by the same number on the note.
    Numbering restarts at 1 for each chapter.
  - For anything longer, or with an image, define a named block on its own
    lines, then reference its id from wherever it belongs in the body:

        [[sidenote:my-note]]
        --
        As long as it needs to be, with its own paragraphs.

        image::images/diagram.png[Alt text]
        --

        ...later, in a paragraph... this claim [[ref:my-note]] needs care.

    `[[margin:id]]` works the same way for an unnumbered block. The id can
    be referenced from anywhere in the chapter (definitions don't need to
    come before their reference), and a sidenote's number is assigned the
    first time it's actually referenced, not where it's defined.
All of the above are stripped from the main text before pagination
estimates that paragraph's line count, so they never throw it off.

Every chapter always starts on its own fresh page, title pushed down to
about a quarter of the page with its chapter number shown large in the
margin column.

Usage:
    python3 builder.py [--index _index.yaml] [--out book.html] [--watch]
"""

import argparse
import html
import math
import re
import struct
import time
from pathlib import Path

import yaml

BOOK_DIR = Path(__file__).resolve().parent

ADMONITIONS = {"NOTE", "TIP", "IMPORTANT", "WARNING", "CAUTION"}

ROMAN = [
    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
    (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
    (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
]

# --- Page geometry: Crafting Interpreters trim size (8in x 10in) -----------
DPI = 96
PT_PER_IN = 72

PAGE_WIDTH_IN = 8
PAGE_HEIGHT_IN = 10
MARGIN_LEFT_IN = 1
MARGIN_RIGHT_IN = 0.5
MARGIN_TOP_PT = 66
MARGIN_BOTTOM_PT = 30

MARGIN_TOP_IN = MARGIN_TOP_PT / PT_PER_IN
MARGIN_BOTTOM_IN = MARGIN_BOTTOM_PT / PT_PER_IN

CONTENT_WIDTH_PX = (PAGE_WIDTH_IN - MARGIN_LEFT_IN - MARGIN_RIGHT_IN) * DPI
CONTENT_HEIGHT_PX = (PAGE_HEIGHT_IN - MARGIN_TOP_IN - MARGIN_BOTTOM_IN) * DPI

# Two-column split of the content box: running text | gap | margin notes.
# MAIN_WIDTH_IN + GAP_WIDTH_IN + MARGIN_WIDTH_IN must equal the content
# width above (4.5 + 0.25 + 1.75 = 6.5in) -- headings span the full content
# width, but paragraphs/lists/admonitions are constrained to MAIN_WIDTH_IN,
# leaving the margin column free for [[margin: ...]] notes.
MAIN_WIDTH_IN = 4.5
GAP_WIDTH_IN = 0.25
MARGIN_WIDTH_IN = 1.75

MAIN_WIDTH_PX = MAIN_WIDTH_IN * DPI
MARGIN_WIDTH_PX = MARGIN_WIDTH_IN * DPI

# Every chapter starts on its own fresh page, title pushed down and its
# number shown large in the margin column -- classic book chapter-opener
# treatment. Specified in mm (top of page to top of title; title to first
# paragraph), like a real print spec, then converted to the units used
# elsewhere here.
MM_PER_IN = 25.4
CHAPTER_TITLE_TOP_MM = 70   # page top -> title top
CHAPTER_TITLE_GAP_MM = 45   # title -> first paragraph

CHAPTER_TITLE_TOP_IN = CHAPTER_TITLE_TOP_MM / MM_PER_IN
CHAPTER_TITLE_GAP_IN = CHAPTER_TITLE_GAP_MM / MM_PER_IN

# The title's own margin-top is measured from inside the sheet's content
# box (i.e. after its 66pt top padding), so back that padding out of the
# page-top-relative 70mm figure above.
CHAPTER_TITLE_MARGIN_TOP_IN = CHAPTER_TITLE_TOP_IN - MARGIN_TOP_IN
CHAPTER_TITLE_MARGIN_TOP_PX = CHAPTER_TITLE_MARGIN_TOP_IN * DPI
CHAPTER_TITLE_GAP_PX = CHAPTER_TITLE_GAP_IN * DPI

# --- Height estimator (approximate, no real text metrics available) --------
LINE_HEIGHT = 1.65
AVG_CHAR_WIDTH_EM = 0.5

# Body text size isn't chosen directly -- it's solved for so that exactly
# TARGET_LINES_PER_PAGE lines of body text (at LINE_HEIGHT above) fill a
# standard page's content height. This also drives the CSS root font-size
# below, so rem-based sizes in the stylesheet stay in sync.
TARGET_LINES_PER_PAGE = 53
BASE_FONT_PX = CONTENT_HEIGHT_PX / (TARGET_LINES_PER_PAGE * LINE_HEIGHT)
BASE_FONT_PT = BASE_FONT_PX * PT_PER_IN / DPI

# rem multipliers, matching the CSS rules below for each tag.
FONT_REM = {
    "part-header": 2.0,
    "chapter-title": 2.2,
    "h4": 1.6,
    "h5": 1.4,
    "h6": 1.2,
    "p": 1.0,
    "li": 1.0,
    "admonition": 1.0,
    "marginnote": 0.8,
}
FONT_PX = {tag: rem * BASE_FONT_PX for tag, rem in FONT_REM.items()}

# (top, bottom) space in rem, matching the CSS margin/padding rules for each
# tag below, converted to px the same way the browser converts rem so the
# estimator stays proportional if BASE_FONT_PT ever changes.
EXTRA_REM = {
    "part-header": (0, 1.25),      # part-eyebrow + h2 margin-bottom
    "chapter-title": (4.5, 1.0),   # margin-top ; margin-bottom
    "h4": (2.0, 0.75),
    "h5": (1.75, 0.6),
    "h6": (1.5, 0.5),
    "p": (0, 1.0),
    "li-block": (0, 1.0),
    "admonition": (2.4, 2.4),      # margin + padding, top and bottom
    "hr": (2.5, 2.5),
    "marginnote": (0, 1.0),
}
EXTRA_PX = {tag: (top * BASE_FONT_PX, bottom * BASE_FONT_PX) for tag, (top, bottom) in EXTRA_REM.items()}
LI_ITEM_EXTRA_PX = 0.35 * BASE_FONT_PX

# --- Table of Contents: two columns, spanning as many pages as it needs ----
CONTENT_WIDTH_IN = PAGE_WIDTH_IN - MARGIN_LEFT_IN - MARGIN_RIGHT_IN
TOC_COLUMN_GAP_IN = 0.4
TOC_COLUMN_WIDTH_IN = (CONTENT_WIDTH_IN - TOC_COLUMN_GAP_IN) / 2
TOC_COLUMN_WIDTH_PX = TOC_COLUMN_WIDTH_IN * DPI


def get_image_dimensions(path):
    """Return (width, height) in pixels for a PNG/GIF/JPEG file, or None if
    unreadable or unrecognized. Pure stdlib -- no Pillow dependency, just
    enough header-sniffing to get an aspect ratio for the pagination
    estimator and for sizing the fallback if something's missing."""
    try:
        with open(path, "rb") as f:
            head = f.read(32)
    except OSError:
        return None

    if head[:8] == b"\x89PNG\r\n\x1a\n" and len(head) >= 24:
        w, h = struct.unpack(">II", head[16:24])
        return w, h

    if head[:6] in (b"GIF87a", b"GIF89a") and len(head) >= 10:
        w, h = struct.unpack("<HH", head[6:10])
        return w, h

    if head[:2] == b"\xff\xd8":
        try:
            with open(path, "rb") as f:
                f.seek(2)
                while True:
                    marker = f.read(2)
                    if len(marker) < 2 or marker[0] != 0xFF:
                        return None
                    code = marker[1]
                    if code in (0xD8, 0x01) or 0xD0 <= code <= 0xD7:
                        continue
                    seg_len_bytes = f.read(2)
                    if len(seg_len_bytes) < 2:
                        return None
                    seg_len = struct.unpack(">H", seg_len_bytes)[0]
                    if code in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                                0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                        data = f.read(5)
                        if len(data) < 5:
                            return None
                        h, w = struct.unpack(">HH", data[1:5])
                        return w, h
                    f.seek(seg_len - 2, 1)
        except (OSError, struct.error):
            return None

    return None


def to_roman(n):
    result = []
    for value, symbol in ROMAN:
        count, n = divmod(n, value)
        result.append(symbol * count)
    return "".join(result)


def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "section"


def unique_slug(text, seen):
    base = slugify(text)
    slug = base
    i = 2
    while slug in seen:
        slug = f"{base}-{i}"
        i += 1
    seen.add(slug)
    return slug


def apply_inline(text):
    """Escape HTML, then apply a small subset of AsciiDoc inline markup."""
    text = html.escape(text, quote=False)
    text = re.sub(r"\*(\S(?:.*?\S)?)\*", r"<strong>\1</strong>", text)
    text = re.sub(r"_(\S(?:.*?\S)?)_", r"<em>\1</em>", text)
    text = re.sub(r"`(\S(?:.*?\S)?)`", r"<code>\1</code>", text)
    return text


def close_list(stack, out):
    while stack:
        depth, tag = stack.pop()
        out.append(f"</{tag}>")


NOTE_RE = re.compile(r"\[\[(margin|sidenote|ref|index):\s*(.*?)\]\]")
NAMED_NOTE_START_RE = re.compile(r"^\[\[(margin|sidenote):([\w-]+)\]\]$")


def split_named_notes(lines):
    """Pull `[[margin:id]]` / `[[sidenote:id]]` `--`-delimited block
    definitions out of a chapter's lines, wherever they appear (they don't
    need to precede their first reference). Returns the remaining lines
    with those blocks removed, plus a dict of id -> (kind, inner_lines)."""
    remaining = []
    defs = {}
    i, n = 0, len(lines)
    while i < n:
        m = NAMED_NOTE_START_RE.match(lines[i].strip())
        if m and i + 1 < n and lines[i + 1].strip() == "--":
            kind, note_id = m.groups()
            i += 2
            inner = []
            while i < n and lines[i].strip() != "--":
                inner.append(lines[i])
                i += 1
            i += 1  # skip closing --
            defs[note_id] = (kind, inner)
            continue
        remaining.append(lines[i])
        i += 1
    return remaining, defs


def make_note_extractor(named_notes):
    """Returns an extract_notes(text) function with its own sidenote
    counter, so numbering restarts at 1 for each chapter (one extractor is
    built per parse_chapter call). named_notes is the id -> block dict
    produced by split_named_notes; a sidenote-kind entry's number is
    assigned the first time [[ref:id]] actually resolves it, not at
    definition time, so numbering follows reading order."""
    sidenote_counter = [0]

    def next_number():
        sidenote_counter[0] += 1
        return sidenote_counter[0]

    def extract_notes(text):
        """Pull `[[margin: ...]]`, `[[sidenote: ...]]`, and `[[ref:id]]`
        markers out of paragraph text. A sidenote (inline or referenced)
        leaves a placeholder token (safe from html-escaping and the
        bold/italic/code regexes) in the main text, to be swapped for its
        superscript reference number after apply_inline has run; a margin
        note leaves nothing behind. Returns (main_text, placeholders,
        note_blocks), where each note_block is (kind, payload, number):
        kind is "margin"/"sidenote" for inline notes (payload = the note's
        own text) or "named" for a [[ref:id]] (payload = the id)."""
        notes = []
        placeholders = {}

        def repl(m):
            kind, payload = m.group(1), m.group(2).strip()

            if kind == "ref":
                entry = named_notes.get(payload)
                if entry is None:
                    return ""  # unknown id -- drop silently
                if entry["kind"] == "sidenote":
                    if entry["number"] is None:
                        entry["number"] = next_number()
                    token = f"SIDENOTE{entry['number']}"
                    placeholders[token] = f'<sup class="sidenote-ref">{entry["number"]}</sup>'
                    notes.append(("named", payload, entry["number"]))
                    return token
                notes.append(("named", payload, None))
                return ""

            if kind == "sidenote":
                number = next_number()
                token = f"SIDENOTE{number}"
                placeholders[token] = f'<sup class="sidenote-ref">{number}</sup>'
                notes.append((kind, payload, number))
                return token

            notes.append((kind, payload, None))
            return ""

        stripped = NOTE_RE.sub(repl, text)
        stripped = re.sub(r"\s{2,}", " ", stripped).strip()
        return stripped, placeholders, notes

    return extract_notes


# Wrap width for each tag's line-count estimate. Headings aren't
# width-constrained in the CSS (they span the full content box), so they
# fall back to CONTENT_WIDTH_PX; everything else wraps at its own column
# width.
WRAP_WIDTH_PX = {
    "p": MAIN_WIDTH_PX,
    "li": MAIN_WIDTH_PX,
    "admonition": MAIN_WIDTH_PX,
    "marginnote": MARGIN_WIDTH_PX,
}


def text_lines(text_len, font_px, width_px):
    chars_per_line = max(10, width_px / (font_px * AVG_CHAR_WIDTH_EM))
    return max(1, math.ceil(text_len / chars_per_line))


def estimate_image_height_px(block, width_px):
    """Rendered height of an `image::` block at a given display width,
    from its real aspect ratio if the file's dimensions can be read."""
    dims = get_image_dimensions(BOOK_DIR / block["src"]) if block.get("src") else None
    if dims:
        w, h = dims
        return width_px * (h / w) + 16
    return 150.0  # fallback if the file is missing or unrecognized


MARGINNOTE_FONT_PX = FONT_PX["marginnote"]


def estimate_margin_group_height(sub_blocks):
    """Sum estimated heights of blocks nested inside a named margin/sidenote
    block (from a `[[ref:id]]`). They render inside .marginnote, which sets
    a smaller font-size that nested <p>/<li> inherit, and wrap at the
    narrower margin-column width -- so this re-estimates each sub-block at
    that scale rather than reusing block_height_px's main-column numbers."""
    total = 0.0
    for b in sub_blocks:
        tag = b["tag"]
        if tag == "image":
            total += estimate_image_height_px(b, MARGIN_WIDTH_PX)
        elif tag == "hr":
            top, bottom = EXTRA_PX["hr"]
            total += (top + bottom) * 0.6
        elif tag in ("ul", "ol"):
            top, bottom = EXTRA_PX["li-block"]
            sub_total = top + bottom
            for item_len in b["items"]:
                lines = text_lines(item_len, MARGINNOTE_FONT_PX, MARGIN_WIDTH_PX)
                sub_total += lines * MARGINNOTE_FONT_PX * LINE_HEIGHT + LI_ITEM_EXTRA_PX * 0.8
            total += sub_total
        else:
            lines = text_lines(b.get("text_len", 0), MARGINNOTE_FONT_PX, MARGIN_WIDTH_PX)
            total += lines * MARGINNOTE_FONT_PX * LINE_HEIGHT + MARGINNOTE_FONT_PX * 0.8
    return total


def block_height_px(block, is_chapter_start=False):
    """Estimated rendered height in px, including margins/padding. This is a
    text-length heuristic, not real browser layout -- see module docstring."""
    tag = block["tag"]

    if tag == "index-term":
        return 0.0

    if tag == "marginnote" and "margin_group" in block:
        return estimate_margin_group_height(block["margin_group"])

    if tag == "image":
        return estimate_image_height_px(block, MAIN_WIDTH_PX)

    if tag == "chapter-title" and is_chapter_start:
        lines = text_lines(block["text_len"], FONT_PX["chapter-title"], CONTENT_WIDTH_PX)
        return CHAPTER_TITLE_MARGIN_TOP_PX + lines * FONT_PX["chapter-title"] * LINE_HEIGHT + CHAPTER_TITLE_GAP_PX

    if tag == "hr":
        top, bottom = EXTRA_PX["hr"]
        return top + bottom

    if tag in ("ul", "ol"):
        top, bottom = EXTRA_PX["li-block"]
        total = top + bottom
        for item_len in block["items"]:
            lines = text_lines(item_len, FONT_PX["li"], MAIN_WIDTH_PX)
            total += lines * FONT_PX["li"] * LINE_HEIGHT + LI_ITEM_EXTRA_PX
        return total

    font_px = FONT_PX.get(tag, FONT_PX["p"])
    width_px = WRAP_WIDTH_PX.get(tag, CONTENT_WIDTH_PX)
    lines = text_lines(block["text_len"], font_px, width_px)
    top, bottom = EXTRA_PX.get(tag, EXTRA_PX["p"])
    return top + lines * font_px * LINE_HEIGHT + bottom


# The first ToC page's heading reuses a chapter title's top position (70mm
# push) but not its 45mm bottom gap -- see .toc-page .chapter-title, which
# shrinks that to 1.5rem since the two-column list can start right after.
# Only the first ToC page shows this heading, so only its budget needs to
# account for it.
TOC_HEADING_GAP_PX = 1.5 * BASE_FONT_PX
TOC_HEADING_HEIGHT_PX = (
    CHAPTER_TITLE_MARGIN_TOP_PX
    + text_lines(len("Table of Contents"), FONT_PX["chapter-title"], CONTENT_WIDTH_PX)
    * FONT_PX["chapter-title"] * LINE_HEIGHT
    + TOC_HEADING_GAP_PX
)


def paginate(blocks, content_height_px):
    """Greedily group blocks into page-sized chunks. Margin notes float
    beside the running text rather than adding to its vertical flow, so
    main-column and margin-column usage are tracked separately -- a page
    breaks when either column would overflow. A single block larger than a
    page is still placed alone rather than dropped or split.

    Every chapter-title forces a fresh page (its own chapter-opener page),
    except when it's the very first chapter directly under an unnumbered
    part's own header, which stays on that header's page instead."""
    pages = []
    current = []
    main_used = 0.0
    margin_used = 0.0
    for block in blocks:
        starts_chapter = block["tag"] == "chapter-title"
        if starts_chapter:
            page_is_bare_part_header = len(current) == 1 and current[0]["tag"] == "part-header"
            if current and not page_is_bare_part_header:
                pages.append(current)
                current = []
                main_used = 0.0
                margin_used = 0.0

        h = block_height_px(block, is_chapter_start=starts_chapter)
        is_margin = block["tag"] == "marginnote"
        next_main = main_used if is_margin else main_used + h
        next_margin = margin_used + h if is_margin else margin_used
        if current and (next_main > content_height_px or next_margin > content_height_px):
            pages.append(current)
            current = []
            main_used = 0.0
            margin_used = 0.0
            next_main = 0.0 if is_margin else h
            next_margin = h if is_margin else 0.0
        current.append(block)
        main_used = next_main
        margin_used = next_margin
    if current:
        pages.append(current)
    return pages


def render_sheet(page, page_number=None):
    """Render one page's blocks into a .sheet div. A page that opens on a
    chapter (skipping a leading part-header, if present) gets the
    chapter-opener treatment: extra class + a chapter-number marker."""
    content_html = "".join(b["html"] for b in page)
    footer = f'<div class="page-number">{page_number}</div>' if page_number is not None else ""

    opening_chapter = None
    for b in page:
        if b["tag"] == "part-header":
            continue
        if b["tag"] == "chapter-title":
            opening_chapter = b
        break

    if opening_chapter is None:
        return f'<div class="sheet">{content_html}{footer}</div>'

    number = opening_chapter.get("chapter_number")
    marker = f'<div class="chapter-number">{number}</div>' if number is not None else ""
    return f'<div class="sheet chapter-start">{content_html}{marker}{footer}</div>'


def convert_body(lines, heading_base, seen_ids, apply_lede=True):
    """Convert AsciiDoc body lines (no leading document title) into a list
    of block dicts: {"tag", "html", "text_len"} (or "items" for lists).
    apply_lede is False for the recursive calls that parse a named note
    block's own content -- drop caps are a chapter-opener treatment, not
    something a nested aside should get too."""
    lines, named_defs = split_named_notes(lines)
    named_notes = {}
    for note_id, (kind, inner_lines) in named_defs.items():
        sub_blocks = convert_body(inner_lines, heading_base, seen_ids, apply_lede=False)
        named_notes[note_id] = {"kind": kind, "sub_blocks": sub_blocks, "number": None}

    blocks = []
    list_stack = []  # list of (depth, tag, items, texts)
    para_buf = []
    admonition = None
    extract_notes = make_note_extractor(named_notes)

    def flush_para():
        nonlocal para_buf, admonition
        if not para_buf:
            return
        joined = " ".join(line.strip() for line in para_buf)
        para_buf = []
        if admonition:
            label = admonition
            admonition = None
            blocks.append({
                "tag": "admonition",
                "text_len": len(joined),
                "html": (
                    f'<div class="admonition {label.lower()}">'
                    f'<span class="admonition-label">{label.title()}</span> '
                    f"{apply_inline(joined)}</div>"
                ),
            })
        else:
            main_text, placeholders, notes = extract_notes(joined)
            main_html = apply_inline(main_text)
            for token, snippet in placeholders.items():
                main_html = main_html.replace(token, snippet)
            blocks.append({
                "tag": "p",
                "text_len": len(main_text),
                "html": f"<p>{main_html}</p>",
            })
            for kind, payload, number in notes:
                if kind == "named":
                    entry = named_notes[payload]
                    sub_html = "".join(b["html"] for b in entry["sub_blocks"])
                    lead = f'<sup class="sidenote-number">{number}</sup> ' if entry["kind"] == "sidenote" else ""
                    blocks.append({
                        "tag": "marginnote",
                        "margin_group": entry["sub_blocks"],
                        "html": f'<div class="marginnote">{lead}{sub_html}</div>',
                    })
                elif kind == "sidenote":
                    note_html = (
                        f'<div class="marginnote">'
                        f'<sup class="sidenote-number">{number}</sup> '
                        f"{apply_inline(payload)}</div>"
                    )
                    blocks.append({"tag": "marginnote", "text_len": len(payload), "html": note_html})
                elif kind == "index":
                    # Invisible: no marker in the text, no floated note --
                    # just registers that this term occurs on whichever
                    # page this block lands on (see build()'s page-number
                    # pass), for the back-of-book Index.
                    blocks.append({"tag": "index-term", "term": payload, "html": ""})
                else:
                    note_html = f'<div class="marginnote">{apply_inline(payload)}</div>'
                    blocks.append({"tag": "marginnote", "text_len": len(payload), "html": note_html})

    def close_lists():
        while list_stack:
            depth, tag, items_html, items_len = list_stack.pop()
            blocks.append({
                "tag": tag,
                "items": items_len,
                "html": f"<{tag}>{''.join(items_html)}</{tag}>",
            })

    for raw in lines:
        line = raw.rstrip("\n")
        stripped = line.strip()

        if stripped == "":
            flush_para()
            close_lists()
            continue

        heading_match = re.match(r"^(=+)\s+(.*)$", stripped)
        if heading_match:
            flush_para()
            close_lists()
            level = len(heading_match.group(1))
            title_text = heading_match.group(2).strip()
            tag = f"h{min(heading_base + level, 6)}"
            anchor = unique_slug(title_text, seen_ids)
            blocks.append({
                "tag": tag,
                "text_len": len(title_text),
                "title": title_text,
                "anchor": anchor,
                "html": f'<{tag} id="{anchor}">{apply_inline(title_text)}</{tag}>',
            })
            continue

        if stripped == "'''":
            flush_para()
            close_lists()
            blocks.append({"tag": "hr", "html": "<hr/>"})
            continue

        image_match = re.match(r"^image::(\S+)\[(.*?)\]$", stripped)
        if image_match:
            flush_para()
            close_lists()
            src, alt = image_match.groups()
            blocks.append({
                "tag": "image",
                "src": src,
                "html": f'<img src="{html.escape(src, quote=True)}" alt="{html.escape(alt, quote=True)}">',
            })
            continue

        list_match = re.match(r"^(\*+|\.+)\s+(.*)$", stripped)
        if list_match:
            flush_para()
            marker, content = list_match.groups()
            depth = len(marker)
            tag = "ol" if marker[0] == "." else "ul"
            item_text = content.strip()

            while list_stack and list_stack[-1][0] > depth:
                d, t, items_html, items_len = list_stack.pop()
                blocks.append({"tag": t, "items": items_len, "html": f"<{t}>{''.join(items_html)}</{t}>"})
            if not list_stack or list_stack[-1][0] < depth:
                list_stack.append([depth, tag, [], []])
            elif list_stack[-1][1] != tag:
                d, t, items_html, items_len = list_stack.pop()
                blocks.append({"tag": t, "items": items_len, "html": f"<{t}>{''.join(items_html)}</{t}>"})
                list_stack.append([depth, tag, [], []])

            list_stack[-1][2].append(f"<li>{apply_inline(item_text)}</li>")
            list_stack[-1][3].append(len(item_text))
            continue

        admon_match = re.match(r"^(NOTE|TIP|IMPORTANT|WARNING|CAUTION):\s*(.*)$", stripped)
        if admon_match and not para_buf:
            admonition = admon_match.group(1)
            para_buf.append(admon_match.group(2))
            continue

        para_buf.append(stripped)

    flush_para()
    close_lists()

    if apply_lede and blocks and blocks[0]["tag"] == "p":
        blocks[0]["html"] = blocks[0]["html"].replace("<p>", '<p class="lede">', 1)

    return blocks


def parse_chapter(path):
    """Return (title, blocks) for a chapter file. Tolerates empty/stub files."""
    if not path.exists():
        placeholder = [{"tag": "p", "text_len": 0, "html": f'<p class="missing">[missing file: {html.escape(path.name)}]</p>'}]
        return path.stem, placeholder

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    title = None
    body_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "":
            continue
        m = re.match(r"^=\s+(.*)$", stripped)
        if m:
            title = m.group(1).strip()
            body_start = i + 1
        break

    if title is None:
        title = path.stem.replace("_", " ").title()

    seen_ids = set()
    blocks = convert_body(lines[body_start:], heading_base=3, seen_ids=seen_ids)
    if not blocks:
        blocks = [{"tag": "p", "text_len": 0, "html": '<p class="missing">[chapter not yet written]</p>'}]
    return title, blocks


CSS = """
:root {
  --text: #2b2822;
  --muted: #6b6357;
  --canvas: #ddd4c2;
  --paper: #fffdf7;
  --rule: #d8cfbe;
  --accent: #8a5a34;
}

* { box-sizing: border-box; }

html {
  /* Heading sizes below are in rem, which is relative to this root size,
     not to body's font-size. Keep them equal so the type scale stays
     in proportion to the running text. This value is computed in
     builder.py (BASE_FONT_PT, solved for TARGET_LINES_PER_PAGE lines per
     page) and substituted in below -- keep both in sync. */
  font-size: __BASE_FONT_PT__pt;
}

body {
  margin: 0;
  padding: 0;
  background: var(--canvas);
  color: var(--text);
  font-family: "Palatino Linotype", "Book Antiqua", Palatino, Georgia, "Times New Roman", serif;
  font-size: 1rem;
  line-height: 1.65;
}

.page {
  max-width: none;
  margin: 0 auto;
  padding: 4rem 1rem 6rem;
}

/* Every page of the book is a fixed-size sheet of paper (Crafting
   Interpreters' 8in x 10in trim) resting on the canvas background, with a
   1in/0.5in/66pt/30pt margin baked in as padding. */
.sheet {
  width: 8in;
  min-height: 10in;
  box-sizing: border-box;
  padding: 66pt 0.5in 30pt 1in;
  margin: 0 auto 2.5rem;
  background: var(--paper);
  border-radius: 2px;
  box-shadow:
    0 1px 2px rgba(43, 40, 34, 0.09),
    0 20px 40px -16px rgba(43, 40, 34, 0.32);
  position: relative;
}
.sheet > :first-child {
  margin-top: 0 !important;
}

.title-page {
  text-align: left;
  display: flex;
  flex-direction: column;
  justify-content: center;
  transform: translateY(-8%);
}
.title-page h1 {
  font-size: 5.5rem;
  font-weight: 400;
  letter-spacing: 0.02em;
  line-height: 1.05;
  margin-bottom: 0.5rem;
}
.title-page .subtitle {
  font-style: italic;
  color: var(--muted);
  font-size: 1.15rem;
}
.title-page .author {
  margin-top: 2rem;
  font-size: 1.1rem;
}
.title-page .edition {
  margin-top: 3rem;
  font-size: 0.9rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--muted);
}

.part-divider {
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}
.part-divider-label {
  text-transform: uppercase;
  letter-spacing: 0.3em;
  font-size: 1rem;
  color: var(--accent);
  margin-bottom: 1.25rem;
}
.part-divider-number {
  font-size: 6rem;
  font-weight: 700;
  line-height: 1;
  margin-bottom: 1.5rem;
}
.part-divider-title {
  font-size: 1.4rem;
  font-style: italic;
  color: var(--muted);
}
.part-divider-summary {
  max-width: 4in;
  margin-top: 2rem;
  font-size: 0.95rem;
  line-height: 1.6;
  text-align: left;
  color: var(--text);
}
.part-divider-summary p {
  margin: 0 0 1em;
}

.dedication {
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}
.dedication p {
  max-width: 4in;
  font-style: italic;
  font-size: 1.1rem;
  line-height: 1.7;
  color: var(--muted);
  margin: 0 0 1em;
}

/* Table of Contents spans as many sheets as it needs (paginate_toc in
   builder.py decides the split), each laid out in two columns. */
.toc-page nav.toc {
  columns: 2;
  column-gap: 0.4in;
}
nav.toc ol {
  list-style: none;
  padding: 0;
  margin: 0 0 1.75rem;
}
nav.toc > ol > li {
  margin-bottom: 1.5rem;
  break-inside: avoid;
}
nav.toc .part-label {
  display: block;
  font-weight: bold;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
nav.toc ul {
  list-style: none;
  padding-left: 0;
  margin: 0.4rem 0 0;
}
nav.toc ul li {
  padding: 0.15rem 0;
  font-weight: bold;
}
nav.toc a {
  color: var(--text);
  text-decoration: none;
  border-bottom: 1px solid transparent;
}
nav.toc ul li a {
  font-weight: bold;
}
nav.toc a:hover {
  border-bottom-color: var(--accent);
}

/* Third ToC level: a chapter's own == sub-headings, numbered with a
   dotted leader running to their page number. Not indented, per the
   "don't indent the ToC" rule above -- distinguished by weight/size only. */
.toc-subheadings {
  list-style: none;
  padding-left: 0;
  margin: 0.2rem 0 0;
}
.toc-subheadings li {
  font-weight: normal;
  padding: 0.1rem 0;
}
.toc-line {
  display: flex;
  align-items: baseline;
  color: var(--text);
  text-decoration: none;
}
.toc-line:hover .toc-label {
  border-bottom: 1px solid var(--accent);
}
.toc-num {
  color: var(--muted);
  margin-right: 0.4em;
  flex-shrink: 0;
}
.toc-label {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.toc-leader {
  flex: 1 1 auto;
  min-width: 0.5em;
  border-bottom: 1px dotted var(--rule);
  margin: 0 0.3em;
}
.toc-pagenum {
  color: var(--muted);
  flex-shrink: 0;
}

/* Back-of-book Index: reuses the ToC's two-column .toc-page layout, but
   with its own tighter, alphabetized-list styling. Higher specificity
   than nav.toc > ol > li so its own spacing wins over the ToC's. */
nav.toc .index-list > li {
  margin-bottom: 0;
  break-inside: avoid;
}
.index-letter {
  font-weight: 700;
  text-transform: uppercase;
  color: var(--accent);
  margin: 0.9rem 0 0.2rem !important;
  break-after: avoid;
}
.index-term {
  display: flex;
  justify-content: space-between;
  gap: 0.5rem;
  font-weight: normal;
  padding: 0.05rem 0;
}
.index-pages {
  color: var(--muted);
  flex-shrink: 0;
}

.part-eyebrow {
  display: block;
  text-align: center;
  text-transform: uppercase;
  letter-spacing: 0.2em;
  font-size: 0.85rem;
  color: var(--accent);
  margin-bottom: 0.5rem;
}
.sheet h2 {
  text-align: center;
  font-weight: 400;
  font-size: 2rem;
  margin: 0 0 0.75rem;
  clear: both;
}

.chapter-title {
  font-weight: 700;
  font-size: 2.2rem;
  margin: 4.5rem 0 1rem;
  clear: both;
}
/* Chapter-opener page: 70mm from the top of the page to the top of the
   title, 45mm from the title down to the first paragraph, and the chapter
   number shown large in the margin column. The !importants beat
   .sheet > :first-child's own margin-top:0 !important reset (higher
   specificity here wins the tie). Title's margin-top is measured from
   inside the sheet's content box (i.e. after its own 66pt top padding),
   so back that padding out of the page-top-relative 70mm figure. */
.chapter-start .chapter-title {
  margin-top: calc(70mm - 66pt) !important;
  margin-bottom: 45mm !important;
}
/* Table of Contents heading reuses the chapter-opener treatment for its
   position, but doesn't need that much space before its own content --
   just enough breathing room before the two-column list starts. Same
   specificity as the rule above; being later here is what wins the tie. */
.toc-page .chapter-title {
  margin-bottom: 1.5rem !important;
}
/* Positioned (not floated) relative to .sheet's own content box, so it
   always lands beside the chapter title regardless of where the chapter's
   content later grows -- a float here would only be free to rise as high
   as its point of insertion, which is wherever it happens to sit in the
   DOM among the chapter's other blocks.
   `top` is measured from .sheet's padding box, i.e. the sheet's outer
   edge before its own padding is applied -- which is exactly page-top,
   so it can use the 70mm figure directly (no need to add the padding
   back, unlike the title's own margin-top above). */
.chapter-number {
  position: absolute;
  top: 70mm;
  right: 0;
  width: 1.75in;
  font-size: 3rem;
  font-weight: 700;
  line-height: 1;
  text-align: center;
  color: var(--accent);
}

/* Page number footer -- bottom is measured from .sheet's padding box
   (its outer edge), same reasoning as .chapter-number's top. */
.page-number {
  position: absolute;
  bottom: 12pt;
  left: 0;
  right: 0;
  text-align: center;
  font-size: 0.8rem;
  color: var(--muted);
}
.sheet h4 { font-size: 1.6rem; font-weight: 700; color: var(--text); margin: 2rem 0 0.75rem; clear: both; }
.sheet h5 { font-size: 1.4rem; font-weight: 700; color: var(--text); margin: 1.75rem 0 0.6rem; clear: both; }
.sheet h6 { font-size: 1.2rem; font-weight: 700; color: var(--text); margin: 1.5rem 0 0.5rem; clear: both; }
/* "<chapter>.<n>" prefix on a chapter's own == sub-headings, matching the
   same numbering shown for it in the table of contents. */
.subheading-num {
  color: var(--accent);
}

.lede::first-letter {
  float: left;
  font-size: 3.4em;
  line-height: 0.8;
  padding: 0.05em 0.08em 0 0;
  color: var(--accent);
  font-weight: bold;
}

/* Running text sits in a fixed 4.5in column; the remaining 1.75in (with a
   0.25in gap) is left for .marginnote to float into. */
.sheet > p,
.sheet > ul,
.sheet > ol,
.sheet > .admonition,
.sheet > img {
  width: 4.5in;
}
.sheet > img {
  height: auto;
  display: block;
  margin: 0 0 1rem;
  clear: both;
}

p { text-align: justify; hyphens: auto; margin: 0 0 1rem; }

ul, ol { padding-left: 1.4rem; margin: 0 0 1rem; }
li { margin-bottom: 0.35rem; }

.marginnote {
  float: right;
  clear: right;
  width: 1.75in;
  margin: 0 0 1rem;
  font-size: 0.8rem;
  line-height: 1.4;
  color: var(--muted);
  text-align: left;
  hyphens: auto;
}
.marginnote img {
  width: 100%;
  height: auto;
  display: block;
  margin: 0.3rem 0;
}
.marginnote p {
  margin: 0 0 0.6rem;
}

/* [[sidenote: ...]] reference marker left in the running text. */
.sidenote-ref {
  color: var(--accent);
  font-weight: 700;
  margin-left: 0.05em;
}
/* Leading number on the matching note over in the margin column. */
.sidenote-number {
  color: var(--accent);
  font-weight: 700;
  margin-right: 0.2em;
}

hr {
  border: none;
  border-top: 1px solid var(--rule);
  margin: 2.5rem auto;
  width: 40%;
}

code {
  font-family: "SFMono-Regular", Consolas, Menlo, monospace;
  font-size: 0.9em;
  background: rgba(128, 128, 128, 0.12);
  padding: 0.1em 0.3em;
  border-radius: 3px;
}

.admonition {
  margin: 1.5rem 0;
  padding: 0.9rem 1.1rem;
  border-left: 4px solid var(--accent);
  background: rgba(138, 90, 52, 0.08);
  font-style: normal;
  text-align: left;
}
.admonition-label {
  font-weight: bold;
  text-transform: uppercase;
  font-size: 0.8rem;
  letter-spacing: 0.08em;
  color: var(--accent);
  margin-right: 0.4em;
}

.missing {
  color: var(--muted);
  font-style: italic;
}

@media (max-width: 600px) {
  .page { padding: 1.5rem 0; }
  .sheet {
    width: 100%;
    min-height: 0;
    border-radius: 0;
    padding: 2rem 1.25rem;
  }
}

@media print {
  @page { size: 8in 10in; margin: 0; }
  body { background: #fff; color: #000; }
  .page { padding: 0; }
  .toc-section { display: none; }
  .sheet {
    box-shadow: none;
    border-radius: 0;
    margin: 0;
    break-before: page;
  }
}
"""
CSS = CSS.replace("__BASE_FONT_PT__", f"{BASE_FONT_PT:.4f}")


def estimate_toc_entry_height(part_label_text, chapters):
    """Estimated height (px) of one part's block in the ToC, wrapping at a
    single ToC column's width -- used to decide how many part-blocks fit
    on a ToC page before it needs to spill onto another. chapters is a
    list of (chapter_title, subheading_titles) tuples."""
    font_px = FONT_PX["p"]
    height = 0.0
    height += text_lines(len(part_label_text), font_px, TOC_COLUMN_WIDTH_PX) * font_px * LINE_HEIGHT
    height += 1.5 * BASE_FONT_PX  # nav.toc > ol > li margin-bottom
    for chapter_title, subheading_titles in chapters:
        lines = text_lines(len(chapter_title), font_px, TOC_COLUMN_WIDTH_PX)
        height += lines * font_px * LINE_HEIGHT + 0.3 * BASE_FONT_PX  # 0.15rem padding, top+bottom
        # Subheading lines are single-line (nowrap + ellipsis in the CSS).
        for _ in subheading_titles:
            height += font_px * LINE_HEIGHT + 0.2 * BASE_FONT_PX
    return height


def paginate_two_column(entries, content_height_px, first_page_heading_height_px=0.0):
    """entries: list of (html, height_px). Packs them into pages sized for
    two columns (2x content height), with the first page's budget reduced
    to leave room for a heading (used by both the ToC and the Index)."""
    pages = []
    current = []
    used = 0.0
    budget = 2 * content_height_px - first_page_heading_height_px
    for entry_html, h in entries:
        if current and used + h > budget:
            pages.append(current)
            current = []
            used = 0.0
            budget = 2 * content_height_px
        current.append(entry_html)
        used += h
    if current:
        pages.append(current)
    return pages


def paginate_toc(entries, content_height_px):
    return paginate_two_column(entries, content_height_px, TOC_HEADING_HEIGHT_PX)


def build(index_path: Path, out_path: Path):
    data = yaml.safe_load(index_path.read_text(encoding="utf-8"))
    title = data.get("title", "Untitled")
    subtitle = data.get("subtitle")
    author = data.get("author")
    edition = data.get("edition")
    parts = data.get("parts", [])

    seen_ids = set()
    toc_entries = []
    parts_html = []
    recommended_reading_by_part = []  # (part_title, entries) for parts that have any
    index_entries = []  # (term, page_num) for every [[index:term]] occurrence

    numbered_index = 0
    chapter_number = 0
    # Page numbers shown in the ToC and stamped on each sheet's footer.
    # Fudged: counted from the start of Part I's content, not the true
    # absolute page including the title page and the ToC's own pages
    # (which isn't known until the ToC itself is laid out, further down) --
    # good enough to navigate by, not print-production accurate.
    page_counter = 1
    for part in parts:
        part_title = part.get("title", "Untitled Part")
        chapters = part.get("chapters", [])
        is_numbered = part_title.strip().lower() not in {"introduction", "conclusion"}

        part_id = unique_slug(part_title, seen_ids)
        eyebrow = None
        if is_numbered:
            numbered_index += 1
            eyebrow = f"Part {to_roman(numbered_index)}"

        chapters_info = []  # per chapter: id, title, subheadings [(title, anchor)]
        # Numbered parts get a dedicated divider sheet (below) announcing the
        # part, so the title doesn't need to repeat atop the first content
        # page too. Unnumbered sections (Introduction, Conclusion) have no
        # divider page, so their title heads their own first content page.
        if is_numbered:
            flow = []
        else:
            flow = [{
                "tag": "part-header",
                "text_len": len(part_title),
                "html": f"<h2>{apply_inline(part_title)}</h2>",
            }]

        for chapter_file in chapters:
            chapter_path = BOOK_DIR / chapter_file
            chapter_title, chapter_blocks = parse_chapter(chapter_path)
            chapter_id = unique_slug(chapter_title, seen_ids)
            chapter_number += 1

            # Number this chapter's own == sub-headings as "<chapter>.<n>",
            # both in the heading itself and (via chapters_info below, read
            # by the ToC block further down) in the table of contents.
            subheading_counter = 0
            for b in chapter_blocks:
                if b["tag"] == "h5":
                    subheading_counter += 1
                    number_label = f"{chapter_number}.{subheading_counter}"
                    b["html"] = (
                        f'<h5 id="{b["anchor"]}">'
                        f'<span class="subheading-num">{number_label}</span> '
                        f'{apply_inline(b["title"])}</h5>'
                    )

            chapters_info.append({
                "chapter_id": chapter_id,
                "chapter_title": chapter_title,
                "chapter_number": chapter_number,
                "subheadings": [(b["title"], b["anchor"]) for b in chapter_blocks if b["tag"] == "h5"],
            })
            flow.append({
                "tag": "chapter-title",
                "text_len": len(chapter_title),
                "chapter_number": chapter_number,
                "chapter_id": chapter_id,
                "html": f'<h3 class="chapter-title" id="{chapter_id}">{apply_inline(chapter_title)}</h3>',
            })
            flow.extend(chapter_blocks)

        divider_html = ""
        if is_numbered:
            summary_raw = (part.get("summary") or "").strip()
            summary_html = ""
            if summary_raw:
                summary_blocks = convert_body(summary_raw.splitlines(), heading_base=3, seen_ids=seen_ids, apply_lede=False)
                summary_html = f'<div class="part-divider-summary">{"".join(b["html"] for b in summary_blocks)}</div>'
            divider_html = (
                '<div class="sheet part-divider">'
                '<span class="part-divider-label">Part</span>'
                f'<div class="part-divider-number">{to_roman(numbered_index)}</div>'
                f'<div class="part-divider-title">{apply_inline(part_title)}</div>'
                f'{summary_html}'
                "</div>"
            )
            page_counter += 1  # divider page itself stays unnumbered, but still occupies a page

        pages = paginate(flow, CONTENT_HEIGHT_PX)

        chapter_page_num = {}
        subheading_page_num = {}
        sheets_html_parts = []
        for page_blocks in pages:
            for b in page_blocks:
                if b["tag"] == "chapter-title":
                    chapter_page_num[b["chapter_id"]] = page_counter
                elif b["tag"] == "h5" and "anchor" in b:
                    subheading_page_num[b["anchor"]] = page_counter
                elif b["tag"] == "index-term":
                    index_entries.append((b["term"], page_counter))
            sheets_html_parts.append(render_sheet(page_blocks, page_counter))
            page_counter += 1
        sheets_html = "".join(sheets_html_parts)
        parts_html.append(f'<section class="part-group" id="{part_id}">{divider_html}{sheets_html}</section>')

        recommended = part.get("recommended_reading") or []
        if recommended:
            recommended_reading_by_part.append((part_title, recommended))

        toc_chapter_items = []
        for info in chapters_info:
            subheading_lines = []
            for n, (sub_title, anchor) in enumerate(info["subheadings"], start=1):
                page_num = subheading_page_num.get(anchor, "")
                subheading_lines.append(
                    f'<li><a class="toc-line" href="#{anchor}">'
                    f'<span class="toc-num">{info["chapter_number"]}.{n}</span>'
                    f'<span class="toc-label">{apply_inline(sub_title)}</span>'
                    '<span class="toc-leader"></span>'
                    f'<span class="toc-pagenum">{page_num}</span>'
                    "</a></li>"
                )
            subheadings_html = f'<ol class="toc-subheadings">{"".join(subheading_lines)}</ol>' if subheading_lines else ""
            chapter_label = f'Chapter {info["chapter_number"]}: {info["chapter_title"]}'
            toc_chapter_items.append(
                f'<li><a href="#{info["chapter_id"]}">{apply_inline(chapter_label)}</a>{subheadings_html}</li>'
            )

        # "PART I. Welcome" on one line -- .part-label's text-transform:
        # uppercase renders it as "PART I. WELCOME".
        part_label_text = f"{eyebrow}. {part_title}" if eyebrow else part_title
        toc_entry_html = (
            "<li>"
            + f'<a class="part-label" href="#{part_id}">{apply_inline(part_label_text)}</a>'
            + f'<ul>{"".join(toc_chapter_items)}</ul>'
            + "</li>"
        )
        toc_entries.append((
            toc_entry_html,
            estimate_toc_entry_height(
                part_label_text,
                [
                    (f'Chapter {info["chapter_number"]}: {info["chapter_title"]}', [s[0] for s in info["subheadings"]])
                    for info in chapters_info
                ],
            ),
        ))

    toc_pages = paginate_toc(toc_entries, CONTENT_HEIGHT_PX)
    toc_sheets_html = "".join(
        f'<div class="sheet toc-page{" chapter-start" if i == 0 else ""}">'
        + ('<h3 class="chapter-title">Table of Contents</h3>' if i == 0 else "")
        + f'<nav class="toc"><ol>{"".join(page_items)}</ol></nav>'
        + "</div>"
        for i, page_items in enumerate(toc_pages)
    )

    # --- Front matter: Dedication, Acknowledgements (after the ToC) --------
    dedication_text = (data.get("dedication") or "").strip()
    dedication_html = ""
    if dedication_text:
        dedication_blocks = convert_body(dedication_text.splitlines(), heading_base=3, seen_ids=seen_ids, apply_lede=False)
        dedication_html = '<div class="sheet dedication">' + "".join(b["html"] for b in dedication_blocks) + "</div>"

    acknowledgements_text = (data.get("acknowledgements") or "").strip()
    acknowledgements_html = ""
    if acknowledgements_text:
        ack_flow = [{
            "tag": "chapter-title",
            "text_len": len("Acknowledgements"),
            "html": '<h3 class="chapter-title" id="acknowledgements">Acknowledgements</h3>',
        }]
        ack_flow.extend(convert_body(acknowledgements_text.splitlines(), heading_base=3, seen_ids=seen_ids, apply_lede=False))
        acknowledgements_html = "".join(render_sheet(p) for p in paginate(ack_flow, CONTENT_HEIGHT_PX))

    front_matter_html = dedication_html + acknowledgements_html

    # --- Back matter: Recommended Reading, Bibliography, Index -------------
    recommended_reading_html = ""
    if recommended_reading_by_part:
        rr_flow = [{
            "tag": "chapter-title",
            "text_len": len("Recommended Reading"),
            "html": '<h3 class="chapter-title" id="recommended-reading">Recommended Reading</h3>',
        }]
        for part_title, entries in recommended_reading_by_part:
            anchor = unique_slug(f"recommended-{part_title}", seen_ids)
            rr_flow.append({
                "tag": "h5",
                "text_len": len(part_title),
                "title": part_title,
                "anchor": anchor,
                "html": f'<h5 id="{anchor}">{apply_inline(part_title)}</h5>',
            })
            items_html, items_len = [], []
            for entry in entries:
                book_title = entry.get("title", "")
                entry_author = entry.get("author", "")
                note = entry.get("note", "")
                line = f"<em>{apply_inline(book_title)}</em>"
                if entry_author:
                    line += f" &mdash; {apply_inline(entry_author)}"
                if note:
                    line += f"<br>{apply_inline(note)}"
                items_html.append(f"<li>{line}</li>")
                items_len.append(len(book_title) + len(entry_author) + len(note))
            rr_flow.append({"tag": "ul", "items": items_len, "html": f'<ul>{"".join(items_html)}</ul>'})

        rr_sheets = []
        for page_blocks in paginate(rr_flow, CONTENT_HEIGHT_PX):
            rr_sheets.append(render_sheet(page_blocks, page_counter))
            page_counter += 1
        recommended_reading_html = "".join(rr_sheets)

    bibliography_html = ""
    bibliography = data.get("bibliography") or []
    if bibliography:
        sorted_bib = sorted(bibliography, key=lambda e: (e.get("author") or "").lower())
        items_html, items_len = [], []
        for entry in sorted_bib:
            entry_author = entry.get("author", "")
            book_title = entry.get("title", "")
            year = entry.get("year", "")
            publisher = entry.get("publisher", "")
            line = f"{apply_inline(entry_author)}. <em>{apply_inline(book_title)}</em>."
            if publisher:
                line += f" {apply_inline(str(publisher))},"
            if year:
                line += f" {year}."
            items_html.append(f"<li>{line}</li>")
            items_len.append(len(entry_author) + len(book_title) + len(str(publisher)) + len(str(year)))
        bib_flow = [
            {
                "tag": "chapter-title",
                "text_len": len("Bibliography"),
                "html": '<h3 class="chapter-title" id="bibliography">Bibliography</h3>',
            },
            {"tag": "ul", "items": items_len, "html": f'<ul>{"".join(items_html)}</ul>'},
        ]
        bib_sheets = []
        for page_blocks in paginate(bib_flow, CONTENT_HEIGHT_PX):
            bib_sheets.append(render_sheet(page_blocks, page_counter))
            page_counter += 1
        bibliography_html = "".join(bib_sheets)

    index_html = ""
    if index_entries:
        term_pages = {}
        for term, page_num in index_entries:
            term_pages.setdefault(term, set()).add(page_num)
        sorted_terms = sorted(term_pages.keys(), key=lambda t: t.lower())

        idx_font_px = FONT_PX["p"]
        idx_entries_for_pagination = []
        current_letter = None
        for term in sorted_terms:
            letter = term[0].upper() if term else "#"
            if letter != current_letter:
                current_letter = letter
                idx_entries_for_pagination.append((
                    f'<li class="index-letter">{letter}</li>',
                    1.2 * BASE_FONT_PX * LINE_HEIGHT,
                ))
            pages_str = ", ".join(str(p) for p in sorted(term_pages[term]))
            term_html = (
                '<li class="index-term">'
                f'<span class="index-label">{apply_inline(term)}</span>'
                f'<span class="index-pages">{pages_str}</span>'
                "</li>"
            )
            lines = text_lines(len(term) + len(pages_str), idx_font_px, TOC_COLUMN_WIDTH_PX)
            idx_entries_for_pagination.append((term_html, lines * idx_font_px * LINE_HEIGHT + 0.2 * BASE_FONT_PX))

        idx_sheets = []
        for i, page_items in enumerate(paginate_two_column(idx_entries_for_pagination, CONTENT_HEIGHT_PX, TOC_HEADING_HEIGHT_PX)):
            classes = "sheet toc-page" + (" chapter-start" if i == 0 else "")
            heading = '<h3 class="chapter-title" id="index">Index</h3>' if i == 0 else ""
            idx_sheets.append(
                f'<div class="{classes}">{heading}<nav class="toc"><ol class="index-list">{"".join(page_items)}</ol></nav>'
                f'<div class="page-number">{page_counter}</div></div>'
            )
            page_counter += 1
        index_html = "".join(idx_sheets)

    back_matter_html = recommended_reading_html + bibliography_html + index_html

    html_doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="page">

<div class="sheet title-page">
  <h1>{apply_inline(title)}</h1>
  {f'<p class="subtitle">{apply_inline(subtitle)}</p>' if subtitle else ''}
  {f'<p class="author">{apply_inline(str(author))}</p>' if author else ''}
  {f'<p class="edition">{apply_inline(str(edition))}</p>' if edition else ''}
</div>

<section class="toc-section" id="contents">
  {toc_sheets_html}
</section>

{front_matter_html}

{''.join(parts_html)}

{back_matter_html}

</div>
</body>
</html>
"""
    out_path.write_text(html_doc, encoding="utf-8")
    print(f"Wrote {out_path}")


def collect_watched_paths(index_path: Path):
    """All files a rebuild currently depends on: the index itself, plus every
    chapter file it lists. Re-read fresh each call so newly added chapters
    get picked up as soon as the index changes."""
    paths = {index_path}
    try:
        data = yaml.safe_load(index_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return paths
    for part in data.get("parts", []) or []:
        for chapter_file in part.get("chapters", []) or []:
            paths.add(BOOK_DIR / chapter_file)
    return paths


def snapshot(paths):
    snap = {}
    for path in paths:
        try:
            snap[path] = path.stat().st_mtime
        except FileNotFoundError:
            snap[path] = None
    return snap


def try_build(index_path: Path, out_path: Path):
    try:
        build(index_path, out_path)
    except Exception as exc:
        print(f"Build error: {exc}")


def watch(index_path: Path, out_path: Path, interval: float = 0.5):
    print(f"Watching {index_path.name} and its chapters for changes (Ctrl+C to stop)...")
    try_build(index_path, out_path)
    last_snapshot = snapshot(collect_watched_paths(index_path))
    try:
        while True:
            time.sleep(interval)
            current_snapshot = snapshot(collect_watched_paths(index_path))
            if current_snapshot != last_snapshot:
                last_snapshot = current_snapshot
                try_build(index_path, out_path)
    except KeyboardInterrupt:
        print("\nStopped watching.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", default="_index.yaml", help="Path to the book's YAML structure file")
    parser.add_argument("--out", default="book.html", help="Output HTML file path")
    parser.add_argument("--watch", action="store_true", help="Rebuild automatically whenever the index or a chapter file changes")
    parser.add_argument("--interval", type=float, default=0.5, help="Polling interval in seconds for --watch (default: 0.5)")
    args = parser.parse_args()

    index_path = (BOOK_DIR / args.index).resolve()
    out_path = (BOOK_DIR / args.out).resolve()

    if not index_path.exists():
        raise SystemExit(f"Index file not found: {index_path}")

    if args.watch:
        watch(index_path, out_path, interval=args.interval)
    else:
        build(index_path, out_path)


if __name__ == "__main__":
    main()

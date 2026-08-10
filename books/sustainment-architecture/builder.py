#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pyyaml",
# ]
# ///
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
MARGIN_TOP_PX = MARGIN_TOP_IN * DPI

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

# Code blocks and tables aren't text-wrapped -- their height is driven by
# line/row count, not estimated word-wrap, so they don't need FONT_REM /
# WRAP_WIDTH_PX entries; these constants match the CSS below directly.
CODE_FONT_PX = 0.82 * BASE_FONT_PX
CODE_LINE_HEIGHT = 1.4
CODE_BLOCK_EXTRA_PX = 1.5 * BASE_FONT_PX + 1.0 * BASE_FONT_PX  # padding (top+bottom) + margin-bottom

TABLE_FONT_PX = 0.92 * BASE_FONT_PX
TABLE_ROW_PADDING_PX = 0.7 * BASE_FONT_PX  # cell padding, top+bottom
TABLE_EXTRA_PX = 1.0 * BASE_FONT_PX  # margin-bottom

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
    text = text.replace("->", "→").replace("<-", "←").replace("=>", "⇒")  # arrow glyphs
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


def render_list_html(tag, entries):
    """Render a (possibly nested) list. Each entry's `sub`, if present, is
    a {"tag", "entries"} for a nested list rendered inside that <li>."""
    items = []
    for e in entries:
        sub_html = render_list_html(e["sub"]["tag"], e["sub"]["entries"]) if e["sub"] else ""
        items.append(f"<li>{e['html']}{sub_html}</li>")
    return f"<{tag}>{''.join(items)}</{tag}>"


def list_group_height(tag, entries, font_px, width_px, item_extra_px):
    """Estimated height of a (possibly nested) list at a given font/width
    scale -- recurses into each entry's nested sub-list, if any."""
    top, bottom = EXTRA_PX["li-block"]
    total = top + bottom
    for e in entries:
        lines = text_lines(e["text_len"], font_px, width_px)
        total += lines * font_px * LINE_HEIGHT + item_extra_px
        if e["sub"]:
            total += list_group_height(e["sub"]["tag"], e["sub"]["entries"], font_px, width_px, item_extra_px)
    return total


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
            total += list_group_height(tag, b["entries"], MARGINNOTE_FONT_PX, MARGIN_WIDTH_PX, LI_ITEM_EXTRA_PX * 0.8)
        elif tag == "code-block":
            total += CODE_BLOCK_EXTRA_PX + b["line_count"] * CODE_FONT_PX * CODE_LINE_HEIGHT
        elif tag == "table":
            total += TABLE_EXTRA_PX + b["row_count"] * (TABLE_FONT_PX * LINE_HEIGHT + TABLE_ROW_PADDING_PX)
        else:
            lines = text_lines(b.get("text_len", 0), MARGINNOTE_FONT_PX, MARGIN_WIDTH_PX)
            total += lines * MARGINNOTE_FONT_PX * LINE_HEIGHT + MARGINNOTE_FONT_PX * 0.8
    return total


def estimate_blockquote_height(sub_blocks):
    """Sum estimated heights of paragraphs nested inside a `>` blockquote,
    at the main column's width and full body font size (unlike a margin
    note, a blockquote isn't shrunk)."""
    total = 0.5 * BASE_FONT_PX  # top+bottom padding
    for b in sub_blocks:
        tag = b["tag"]
        if tag in ("ul", "ol"):
            top, bottom = EXTRA_PX["li-block"]
            sub_total = top + bottom
            for item_len in b["items"]:
                lines = text_lines(item_len, FONT_PX["li"], MAIN_WIDTH_PX)
                sub_total += lines * FONT_PX["li"] * LINE_HEIGHT + LI_ITEM_EXTRA_PX
            total += sub_total
        else:
            lines = text_lines(b.get("text_len", 0), FONT_PX["p"], MAIN_WIDTH_PX)
            total += lines * FONT_PX["p"] * LINE_HEIGHT + FONT_PX["p"] * 0.6
    return total + BASE_FONT_PX  # margin-bottom


def block_height_px(block, is_chapter_start=False):
    """Estimated rendered height in px, including margins/padding. This is a
    text-length heuristic, not real browser layout -- see module docstring."""
    tag = block["tag"]

    if tag == "index-term":
        return 0.0

    if tag == "marginnote" and "margin_group" in block:
        return estimate_margin_group_height(block["margin_group"])

    if tag == "blockquote":
        return estimate_blockquote_height(block["quote_group"])

    if tag == "image":
        return estimate_image_height_px(block, MAIN_WIDTH_PX)

    if tag == "code-block":
        return CODE_BLOCK_EXTRA_PX + block["line_count"] * CODE_FONT_PX * CODE_LINE_HEIGHT

    if tag == "table":
        return TABLE_EXTRA_PX + block["row_count"] * (TABLE_FONT_PX * LINE_HEIGHT + TABLE_ROW_PADDING_PX)

    if tag == "chapter-title" and is_chapter_start:
        lines = text_lines(block["text_len"], FONT_PX["chapter-title"], CONTENT_WIDTH_PX)
        return CHAPTER_TITLE_MARGIN_TOP_PX + lines * FONT_PX["chapter-title"] * LINE_HEIGHT + CHAPTER_TITLE_GAP_PX

    if tag == "hr":
        top, bottom = EXTRA_PX["hr"]
        return top + bottom

    if tag in ("ul", "ol"):
        return list_group_height(tag, block["entries"], FONT_PX["li"], MAIN_WIDTH_PX, LI_ITEM_EXTRA_PX)

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
        if is_margin:
            # Where this note "belongs" vertically: how far the main
            # column has filled by this point, i.e. right by whatever it's
            # annotating -- resolved against other notes on this page in
            # resolve_marginnote_positions() once the page is complete.
            block["anchor_top_px"] = main_used
        current.append(block)
        main_used = next_main
        margin_used = next_margin
    if current:
        pages.append(current)
    return pages


MARGINNOTE_GAP_PX = 1.0 * BASE_FONT_PX  # matches the old float-based margin-bottom


def resolve_marginnote_positions(page_blocks):
    """Assign each margin note on a page a final, non-overlapping top
    position, in document order: a note can't render higher than its own
    anchor point, but if an earlier note on the same page is still tall
    enough to reach that far down, this one stacks below it instead."""
    last_bottom = 0.0
    for block in page_blocks:
        if block["tag"] != "marginnote":
            continue
        top = max(block["anchor_top_px"], last_bottom)
        block["resolved_top_px"] = top
        last_bottom = top + block_height_px(block) + MARGINNOTE_GAP_PX


def render_sheet(page, page_number=None):
    """Render one page's blocks into a .sheet div. A page that opens on a
    chapter (skipping a leading part-header, if present) gets the
    chapter-opener treatment: extra class + a chapter-number marker."""
    resolve_marginnote_positions(page)
    content_html_parts = []
    for b in page:
        if b["tag"] == "marginnote":
            top_px = MARGIN_TOP_PX + b["resolved_top_px"]
            content_html_parts.append(
                b["html"].replace('<div class="marginnote">', f'<div class="marginnote" style="top:{top_px:.1f}px">', 1)
            )
        else:
            content_html_parts.append(b["html"])
    content_html = "".join(content_html_parts)
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
    list_stack = []  # list of [depth, tag, entries]; entries are {"text_len", "html", "sub"}
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

    def pop_list():
        """Pop the deepest open list. If a shallower list is still open,
        the popped list nests inside that list's last <li> (AsciiDoc's
        `**` under `*` etc.); otherwise it's finished and becomes its own
        top-level block."""
        depth, tag, entries = list_stack.pop()
        if list_stack:
            list_stack[-1][2][-1]["sub"] = {"tag": tag, "entries": entries}
        else:
            blocks.append({
                "tag": tag,
                "entries": entries,
                "html": render_list_html(tag, entries),
            })

    def close_lists():
        while list_stack:
            pop_list()

    i = 0
    n = len(lines)
    pending_attrs = {}
    while i < n:
        raw = lines[i]
        line = raw.rstrip("\n")
        stripped = line.strip()

        if stripped == "":
            flush_para()
            close_lists()
            pending_attrs = {}
            i += 1
            continue

        source_attr_match = re.match(r"^\[source(?:,\s*([\w+-]*))?\]$", stripped)
        if source_attr_match:
            pending_attrs["source_lang"] = source_attr_match.group(1) or ""
            i += 1
            continue

        if re.match(r'^\[(%header|options="header")\]$', stripped):
            pending_attrs["table_header"] = True
            i += 1
            continue

        # Any other bracket attribute line (e.g. [cols="1,1"]) -- consumed
        # silently so it doesn't leak into paragraph text as literal text.
        if re.match(r"^\[.*\]$", stripped):
            i += 1
            continue

        code_fence_match = re.match(r"^-{4,}$", stripped)
        if code_fence_match:
            flush_para()
            close_lists()
            lang = pending_attrs.get("source_lang", "")
            pending_attrs = {}
            i += 1
            code_lines = []
            while i < n and not re.match(r"^-{4,}$", lines[i].strip()):
                code_lines.append(lines[i].rstrip("\n"))
                i += 1
            i += 1  # skip closing ----
            escaped = html.escape("\n".join(code_lines), quote=False)
            lang_class = f' class="language-{html.escape(lang)}"' if lang else ""
            blocks.append({
                "tag": "code-block",
                "line_count": len(code_lines) or 1,
                "html": f"<pre><code{lang_class}>{escaped}</code></pre>",
            })
            continue

        if stripped == "|===":
            flush_para()
            close_lists()
            has_header = bool(pending_attrs.get("table_header"))
            pending_attrs = {}
            i += 1
            table_lines = []
            while i < n and lines[i].strip() != "|===":
                table_lines.append(lines[i])
                i += 1
            i += 1  # skip closing |===
            rows = []
            for table_line in table_lines:
                table_line_stripped = table_line.strip()
                if not table_line_stripped or not table_line_stripped.startswith("|"):
                    continue
                cells = [c.strip() for c in table_line_stripped.split("|")[1:]]
                rows.append(cells)
            row_htmls = []
            for row_index, cells in enumerate(rows):
                cell_tag = "th" if (has_header and row_index == 0) else "td"
                cells_html = "".join(f"<{cell_tag}>{apply_inline(c)}</{cell_tag}>" for c in cells)
                row_htmls.append(f"<tr>{cells_html}</tr>")
            blocks.append({
                "tag": "table",
                "row_count": len(rows),
                "html": f"<table>{''.join(row_htmls)}</table>",
            })
            continue

        if stripped.startswith(">"):
            flush_para()
            close_lists()
            quote_lines = []
            while i < n:
                s = lines[i].strip()
                if not s.startswith(">"):
                    break
                content = s[1:]
                if content.startswith(" "):
                    content = content[1:]
                quote_lines.append(content)
                i += 1
            quote_blocks = convert_body(quote_lines, heading_base, seen_ids, apply_lede=False)
            blocks.append({
                "tag": "blockquote",
                "quote_group": quote_blocks,
                "html": f'<blockquote>{"".join(b["html"] for b in quote_blocks)}</blockquote>',
            })
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
            i += 1
            continue

        if stripped == "'''":
            flush_para()
            close_lists()
            blocks.append({"tag": "hr", "html": "<hr/>"})
            i += 1
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
            i += 1
            continue

        list_match = re.match(r"^(\*+|\.+)\s+(.*)$", stripped)
        if list_match:
            flush_para()
            marker, content = list_match.groups()
            depth = len(marker)
            tag = "ol" if marker[0] == "." else "ul"
            item_text = content.strip()

            while list_stack and list_stack[-1][0] > depth:
                pop_list()
            if not list_stack or list_stack[-1][0] < depth:
                list_stack.append([depth, tag, []])
            elif list_stack[-1][1] != tag:
                pop_list()
                list_stack.append([depth, tag, []])

            list_stack[-1][2].append({"text_len": len(item_text), "html": apply_inline(item_text), "sub": None})
            i += 1
            continue

        admon_match = re.match(r"^(NOTE|TIP|IMPORTANT|WARNING|CAUTION):\s*(.*)$", stripped)
        if admon_match and not para_buf:
            admonition = admon_match.group(1)
            para_buf.append(admon_match.group(2))
            i += 1
            continue

        para_buf.append(stripped)
        i += 1

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
  padding: 7rem 1rem 6rem;
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
  /* Several soft, off-center, semi-transparent blobs layered over a base
     diagonal fill -- greens and one warm earthy tone, overlapping so the
     seams blend rather than reading as a single centered gradient. Meant
     to feel like a shifting, amorphous mass rather than a flat fill. */
  background:
    radial-gradient(ellipse 90% 70% at 50% 45%, rgba(74, 122, 90, 0.25) 0%, transparent 65%),
    radial-gradient(ellipse 75% 55% at 12% 88%, rgba(122, 94, 58, 0.35) 0%, transparent 75%),
    radial-gradient(ellipse 70% 60% at 78% 85%, rgba(16, 30, 20, 0.65) 0%, transparent 72%),
    radial-gradient(ellipse 65% 55% at 82% 15%, rgba(35, 82, 58, 0.55) 0%, transparent 68%),
    radial-gradient(ellipse 55% 45% at 18% 22%, rgba(58, 107, 74, 0.6) 0%, transparent 70%),
    linear-gradient(155deg, #1a3a28 0%, #102117 100%);
  color: #ffffff;
}
.title-page h1 {
  font-size: 5.5rem;
  font-weight: 400;
  letter-spacing: 0.02em;
  line-height: 1.05;
  margin-bottom: 0.5rem;
  color: #ffffff;
}
.title-page .subtitle {
  font-style: italic;
  color: #cfe3d6;
  font-size: 1.15rem;
}
.title-page .author {
  margin-top: 2rem;
  font-size: 1.1rem;
  color: #ffffff;
}
.title-page .edition {
  margin-top: 3rem;
  font-size: 0.9rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #a9c7b5;
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

/* Plain legal boilerplate, bottom-anchored rather than centered like the
   Dedication -- reads as a document, not a piece of writing. */
.copyright-page {
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  text-align: left;
}
.copyright-page p {
  font-size: 0.8rem;
  line-height: 1.6;
  color: var(--muted);
  margin: 0 0 0.75rem;
}

/* Table of Contents spans as many sheets as it needs (paginate_toc in
   builder.py decides the split), each laid out in two columns. Multi-column
   layout only actually fills column 1 then spills into column 2 if the
   container has an explicit height to balance against -- left at auto
   height, browsers just stack everything in column 1 and leave column 2
   empty, which is what was happening here. __TOC_COLUMN_HEIGHT_PX__ is
   substituted below to match CONTENT_HEIGHT_PX, the same per-column budget
   paginate_two_column() already assumed when deciding what fits per page. */
.toc-page nav.toc {
  columns: 2;
  column-gap: 0.4in;
  column-fill: auto;
  height: __TOC_COLUMN_HEIGHT_PX__px;
}
/* Flat entries -- a part's label and each chapter are separate,
   independently-paginated blocks (see build()'s ToC construction), not a
   nested list, so a long part's chapters can flow across columns/pages. */
.toc-part-label {
  margin: 0 0 0.5rem;
  break-inside: avoid;
}
.toc-part-label .part-label {
  display: block;
  font-weight: bold;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.toc-chapter {
  margin: 0 0 1rem;
  break-inside: avoid;
}
.toc-chapter > a {
  font-weight: bold;
}
nav.toc a {
  color: var(--text);
  text-decoration: none;
  border-bottom: 1px solid transparent;
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
  white-space: normal;
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
   with its own tighter, alphabetized-list styling. */
.index-list {
  list-style: none;
  padding: 0;
  margin: 0;
}
.index-list > li {
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
   back, unlike the title's own margin-top above). `right` is measured
   from that same outer edge too, so it needs the sheet's own 0.5in right
   padding added back, or it overflows past the true content edge. */
.chapter-number {
  position: absolute;
  top: 70mm;
  right: 0.5in;
  width: 1.75in;
  font-size: 3rem;
  font-weight: 700;
  line-height: 1;
  text-align: center;
  color: var(--accent);
}

/* Page number footer -- bottom/left/right are all measured from .sheet's
   padding box (its outer edge), so they need its own padding (1in left,
   0.5in right) added back to center within the content box rather than
   the whole physical sheet. */
.page-number {
  position: absolute;
  bottom: 12pt;
  left: 1in;
  right: 0.5in;
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
.sheet > img,
.sheet > pre,
.sheet > table,
.sheet > blockquote {
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

/* Positioned (not floated), same reasoning as .chapter-number: a float can
   only rise as high as its point of insertion in the flow, and has to
   stack below any earlier still-tall float in the same column, so its
   rendered position can drift arbitrarily far from what it's actually
   annotating. `top` is set inline per-note by render_sheet(), computed
   from the main column's actual height at that note's reference point
   (see resolve_marginnote_positions). `right` is measured from .sheet's
   outer edge (its padding box), so it needs the sheet's own 0.5in right
   padding added back -- otherwise the note's box sits 0.5in too far
   right and its left-aligned, wrapping text overflows past the true
   content edge. */
.marginnote {
  position: absolute;
  right: 0.5in;
  width: 1.75in;
  margin: 0;
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

pre {
  overflow-x: auto;
  border-left: 2px solid #d0d0d0;
  border-right: 2px solid #d0d0d0;
  padding: 0.75rem 1rem;
  margin: 0 0 1rem;
}
pre code {
  font-size: 0.82rem;
  line-height: 1.4;
  background: none;
  padding: 0;
  border-radius: 0;
  white-space: pre;
}

table {
  border-collapse: collapse;
  margin: 0 0 1rem;
  font-size: 0.92rem;
}
th, td {
  border: 1px solid var(--rule);
  padding: 0.35rem 0.5rem;
  text-align: left;
  vertical-align: top;
}
th {
  font-weight: 700;
  background: rgba(138, 90, 52, 0.08);
}

blockquote {
  margin: 0 0 1rem;
  padding: 0.25rem 0 0.25rem 1rem;
  border-left: 3px solid var(--accent);
  font-style: italic;
  color: var(--muted);
}
blockquote p {
  margin: 0 0 0.6rem;
}
blockquote p:last-child {
  margin-bottom: 0;
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
CSS = CSS.replace("__TOC_COLUMN_HEIGHT_PX__", f"{CONTENT_HEIGHT_PX:.2f}")


def estimate_toc_part_label_height(part_label_text):
    """Estimated height (px) of a part's own label line in the ToC -- its
    own pagination entry, not bundled with its chapters, so a long part's
    chapter list can flow across columns/pages instead of the whole part
    being packed as one oversized, unsplittable block."""
    font_px = FONT_PX["p"]
    lines = text_lines(len(part_label_text), font_px, TOC_COLUMN_WIDTH_PX)
    return lines * font_px * LINE_HEIGHT + 0.5 * BASE_FONT_PX  # .toc-part-label margin-bottom


def estimate_toc_chapter_height(chapter_label, subheading_titles):
    """Estimated height (px) of one chapter's ToC entry (its title plus its
    nested, numbered sub-heading lines), wrapping at a single ToC column's
    width. This is the pagination unit -- a chapter and its own
    sub-headings stay together, but different chapters (even under the
    same part) can land on different columns/pages."""
    font_px = FONT_PX["p"]
    lines = text_lines(len(chapter_label), font_px, TOC_COLUMN_WIDTH_PX)
    height = lines * font_px * LINE_HEIGHT + 1.0 * BASE_FONT_PX  # .toc-chapter margin-bottom
    if subheading_titles:
        height += 0.2 * BASE_FONT_PX  # .toc-subheadings margin-top
    # A subheading's label doesn't get the full column width -- its row also
    # holds the "N.N" number, the dotted leader (shrunk to its 0.5em
    # min-width once the label is long enough to need it) and the page
    # number, all in the same flex row. Reserve roughly their combined
    # width (~5.5em) so long titles are budgeted the extra line(s) they'll
    # actually wrap onto instead of one line to a truncation ellipsis.
    label_width_px = max(TOC_COLUMN_WIDTH_PX - 5.5 * font_px, 10)
    for sub_title in subheading_titles:
        sub_lines = text_lines(len(sub_title), font_px, label_width_px)
        height += sub_lines * font_px * LINE_HEIGHT + 0.2 * BASE_FONT_PX  # .toc-subheadings li padding
    return height


def paginate_two_column(entries, content_height_px, first_page_heading_height_px=0.0):
    """entries: list of (html, height_px). Packs them into pages of two
    real columns, simulating column-fill:auto's actual behavior: column 1
    fills up to content_height_px, then column 2 the same way; an entry
    that doesn't fit in either bumps to the next column or, if column 2 is
    also full, starts a new page. A fixed column-count/height in CSS means
    overflowing content doesn't wrap onto a new page on its own -- the
    browser just adds extra columns past the page's physical width, so
    this has to get the fit right rather than just summing total height
    across both columns as one pool (which ignores that an entry can't be
    split, so some space is "wasted" at each column break -- summing as
    one pool overcounts how much actually fits).

    A small safety margin on top, since these are text-length heuristic
    heights, not exact browser layout."""
    usable_height_px = content_height_px * 0.95
    pages = []
    current_page = []
    col_index = 0
    col_used = 0.0
    col_capacity = usable_height_px - first_page_heading_height_px

    for entry_html, h in entries:
        if col_used > 0 and col_used + h > col_capacity:
            if col_index == 0:
                col_index = 1
                col_used = 0.0
                col_capacity = usable_height_px
            else:
                pages.append(current_page)
                current_page = []
                col_index = 0
                col_used = 0.0
                col_capacity = usable_height_px
        current_page.append(entry_html)
        col_used += h

    if current_page:
        pages.append(current_page)
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

        # Each part's label and each chapter (with its own sub-headings) is
        # its own flat pagination entry -- not nested/bundled as one giant
        # per-part block -- so a long part's chapter list can flow across
        # columns and pages instead of forcing the whole part onto one page.
        # "PART I. Welcome" on one line -- .part-label's text-transform:
        # uppercase renders it as "PART I. WELCOME".
        part_label_text = f"{eyebrow}. {part_title}" if eyebrow else part_title
        part_label_html = f'<div class="toc-part-label"><a class="part-label" href="#{part_id}">{apply_inline(part_label_text)}</a></div>'
        toc_entries.append((part_label_html, estimate_toc_part_label_height(part_label_text)))

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
            chapter_html = f'<div class="toc-chapter"><a href="#{info["chapter_id"]}">{apply_inline(chapter_label)}</a>{subheadings_html}</div>'
            subheading_titles = [s[0] for s in info["subheadings"]]
            toc_entries.append((chapter_html, estimate_toc_chapter_height(chapter_label, subheading_titles)))

    toc_pages = paginate_toc(toc_entries, CONTENT_HEIGHT_PX)
    toc_sheets_html = "".join(
        f'<div class="sheet toc-page{" chapter-start" if i == 0 else ""}">'
        + ('<h3 class="chapter-title">Table of Contents</h3>' if i == 0 else "")
        + f'<nav class="toc">{"".join(page_items)}</nav>'
        + "</div>"
        for i, page_items in enumerate(toc_pages)
    )

    # --- Copyright page (right after the title page, before the ToC) -------
    copyright_text = (data.get("copyright") or "").strip()
    copyright_html = ""
    if copyright_text:
        copyright_blocks = convert_body(copyright_text.splitlines(), heading_base=3, seen_ids=seen_ids, apply_lede=False)
        copyright_html = '<div class="sheet copyright-page">' + "".join(b["html"] for b in copyright_blocks) + "</div>"

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

    preface_path = BOOK_DIR / "preface.adoc"
    preface_html = ""
    if preface_path.exists():
        preface_title, preface_blocks = parse_chapter(preface_path)
        preface_id = unique_slug(preface_title, seen_ids)
        preface_flow = [{
            "tag": "chapter-title",
            "text_len": len(preface_title),
            "html": f'<h3 class="chapter-title" id="{preface_id}">{apply_inline(preface_title)}</h3>',
        }]
        preface_flow.extend(preface_blocks)
        preface_html = "".join(render_sheet(p) for p in paginate(preface_flow, CONTENT_HEIGHT_PX))

    bio_path = BOOK_DIR / "bio.adoc"
    bio_html = ""
    if bio_path.exists():
        bio_title, bio_blocks = parse_chapter(bio_path)
        bio_id = unique_slug(bio_title, seen_ids)
        bio_flow = [{
            "tag": "chapter-title",
            "text_len": len(bio_title),
            "html": f'<h3 class="chapter-title" id="{bio_id}">{apply_inline(bio_title)}</h3>',
        }]
        bio_flow.extend(bio_blocks)
        bio_html = "".join(render_sheet(p) for p in paginate(bio_flow, CONTENT_HEIGHT_PX))

    front_matter_html = dedication_html + acknowledgements_html + preface_html + bio_html

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
            rr_entries = []
            for entry in entries:
                book_title = entry.get("title", "")
                entry_author = entry.get("author", "")
                note = entry.get("note", "")
                line = f"<em>{apply_inline(book_title)}</em>"
                if entry_author:
                    line += f" &mdash; {apply_inline(entry_author)}"
                if note:
                    line += f"<br>{apply_inline(note)}"
                rr_entries.append({
                    "text_len": len(book_title) + len(entry_author) + len(note),
                    "html": line,
                    "sub": None,
                })
            rr_flow.append({"tag": "ul", "entries": rr_entries, "html": render_list_html("ul", rr_entries)})

        rr_sheets = []
        for page_blocks in paginate(rr_flow, CONTENT_HEIGHT_PX):
            rr_sheets.append(render_sheet(page_blocks, page_counter))
            page_counter += 1
        recommended_reading_html = "".join(rr_sheets)

    bibliography_html = ""
    bibliography = data.get("bibliography") or []
    if bibliography:
        sorted_bib = sorted(bibliography, key=lambda e: (e.get("author") or "").lower())
        bib_entries = []
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
            bib_entries.append({
                "text_len": len(entry_author) + len(book_title) + len(str(publisher)) + len(str(year)),
                "html": line,
                "sub": None,
            })
        bib_flow = [
            {
                "tag": "chapter-title",
                "text_len": len("Bibliography"),
                "html": '<h3 class="chapter-title" id="bibliography">Bibliography</h3>',
            },
            {"tag": "ul", "entries": bib_entries, "html": render_list_html("ul", bib_entries)},
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

{copyright_html}

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

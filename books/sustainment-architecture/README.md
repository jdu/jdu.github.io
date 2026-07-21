# Book builder

`builder.py` turns `_index.yaml` + a folder of `.adoc` chapter files into a
single self-contained `book.html` file: a stack of fixed-size page "sheets"
(8in x 10in, matching Crafting Interpreters' trim size) laid out like a
real, paginated book, with a two-column margin for sidenotes, chapter
openers, a table of contents, front/back matter, and a real index.

There's no browser layout engine involved in building it -- page breaks are
decided by a text-length heuristic (estimated wrapped-line count per
block), not exact rendered height. Treat page breaks as approximate, not
pixel-perfect, especially for very short or very long chapters.

## Requirements

- Python 3
- PyYAML (`pip install pyyaml`)

## Usage

```
python3 builder.py                          # reads _index.yaml, writes book.html
python3 builder.py --index _index.yaml --out book.html
python3 builder.py --watch                  # rebuilds whenever _index.yaml or a
                                             # referenced chapter file changes
python3 builder.py --watch --interval 0.2   # polling interval in seconds (default 0.5)
```

Open `book.html` directly in a browser. It's a single file with no external
dependencies (fonts, JS, etc.), so it works offline and can be printed or
exported to PDF -- the print stylesheet sets `@page { size: 8in 10in }` to
match the on-screen sheets.

## File layout

```
_index.yaml         book structure and metadata (see below)
000_001.adoc         chapter files, one per file, named however you like --
001_002.adoc         _index.yaml's chapters: lists are the source of truth
...
images/              images referenced by image:: (see below)
builder.py
book.html            generated -- overwritten on every build
```

## `_index.yaml`

```yaml
title: Book Title
subtitle: An optional subtitle          # shown under the title on the title page
edition: 2026                           # currently unused by the builder, informational

dedication: |
  A short dedication. Rendered as its own centered, unnumbered page after
  the table of contents.

acknowledgements: |
  Prose, one or more paragraphs. Rendered as its own page (chapter-title
  styled) after the dedication, also unnumbered.

bibliography:
  - author: "Last, First"
    title: "Book Title"
    year: 2020
    publisher: "Publisher Name"
  # sorted alphabetically by author when rendered

parts:
  - title: Part Title
    summary: |
      Shown on this part's divider page, under the part title.
    chapters:
      - 000_001.adoc            # paths are relative to this file's directory
      - 000_002.adoc
    recommended_reading:        # optional; omit or leave empty to skip
      - title: "Book Title"
        author: "Author Name"
        note: "One line on why it's relevant."   # optional
  - title: ...
    ...
```

Notes:

- A part named exactly `introduction` or `conclusion` (case-insensitive)
  is treated as **unnumbered**: no roman-numeral divider page, no "Part N"
  prefix in the table of contents, and its title heads its first chapter's
  own page instead. Every other part gets a dedicated divider page ("PART
  I", etc.) and rolls up into the Recommended Reading section if it has
  one.
- Parts, chapters, and their sub-headings are numbered automatically in
  reading order -- there's nothing to number by hand.
- Page numbers shown in the table of contents and stamped on each page's
  footer are counted from the start of the first part's content, not the
  true absolute page including the title page and the table of contents
  itself (whose own length isn't known until it's laid out). They're
  internally consistent -- what's on the footer matches what the ToC
  says -- just not print-production accurate.

## Chapter files (`.adoc`)

A small AsciiDoc-like subset, line-oriented:

```asciidoc
= Chapter Title

Regular paragraph text. *bold*, _italic_, and `code` are supported inline.
Paragraphs can wrap across multiple source lines; a blank line ends one.

== A sub-heading

* A bullet list
* Second item
** Nested item

. A numbered list item
. Second item

NOTE: TIP/IMPORTANT/WARNING/CAUTION also work the same way -- a callout
box, first line same as the paragraph, ends at the next blank line.

image::images/diagram.png[Alt text]

'''
(a horizontal rule)
```

The chapter's own title (the single `=` line) is used both as the visible
chapter title and in the table of contents. `==` sub-headings become the
third level in the ToC (numbered, with a dotted leader to their page
number) -- see below.

## Margin notes and sidenotes

Every content page has a 4.5in main text column and a 1.75in margin
column (with a 0.25in gap between them). Three ways to put something in
the margin, from simplest to most flexible:

**1. A short margin note**, written anywhere inside a paragraph:

```
Some claim in the text. [[margin: A short aside about that claim.]] The
sentence continues normally.
```

No visible marker is left in the main text -- the note just floats in the
margin near that paragraph.

**2. A short sidenote** -- same, but numbered:

```
Some claim in the text. [[sidenote: Explains the claim.]] The sentence continues.
```

Leaves a small superscript number at that point in the text, matched by
the same number on the note in the margin. Numbering restarts at 1 for
each chapter.

**3. A longer note, or one with an image** -- define it as a named block
on its own lines (anywhere in the chapter -- it doesn't need to come
before its first reference), then pull it into the text with `[[ref:id]]`:

```
[[sidenote:my-note]]
--
As long as it needs to be, with its own paragraphs.

image::images/diagram.png[Alt text]
--

...later, in a paragraph... this claim [[ref:my-note]] needs care.
```

`[[margin:id]]` works the same way for an unnumbered block. A sidenote's
number is assigned the first time `[[ref:id]]` actually resolves it, not
where it's defined, so numbering still follows reading order.

All three forms are stripped out of the main text before pagination
estimates that paragraph's line count, so they never throw off page
breaks.

## Index

Write `[[index:term]]` anywhere inside a paragraph to register that this
page should appear under `term` in the back-of-book Index:

```
Discussion of governance structures. [[index:governance]] More text.
```

It's invisible in the running text -- like a margin note, it's stripped
out before rendering. Every occurrence across the whole book is collected
with its real page number, deduplicated per page, and rendered as a
standard alphabetized, letter-grouped, two-column index at the very end
of the book. There's no index unless you add `[[index:...]]` markers
somewhere -- none exist in the chapters by default.

## Chapter openers, page numbers, and the ToC

Every chapter always starts on its own fresh page: the title is pushed
down 70mm from the top of the page, the chapter's number is shown large
in the margin column, and the first paragraph starts 45mm below the
title. The table of contents lists Parts (ALL CAPS), Chapters (bold), and
each chapter's own `==` sub-headings (numbered, with a dotted leader
running to a page number) -- not indented, distinguished by weight/case
only.

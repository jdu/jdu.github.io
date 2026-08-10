#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pyyaml",
# ]
# ///
"""
Build docs/bookshelf.html from docs/bookshelf_v2.yaml.

Books are organized by author for easy editing: `books:` is a list of
one-key mappings, `Surname, First: [book, book, ...]`, so every book by the
same person lives together regardless of which shelf it's on. Each book is
`title` + `section` (matching a `name` in the top-level `sections` list) +
optional `order` (used to render non-alphabetical sections in a specific
order; defaults to file order) + optional `note` (e.g. "reread") + optional
`author` (overrides the displayed author text -- for a multi-author book,
the top-level key is just the primary author, and this holds the full
original credit).

A section can set `alphabetical: true` to render as a physical shelf
instead of in `order`: its books are grouped and sorted automatically by
author surname, under every letter A-Z, shown even when empty. A section
can also set `description`, shown under its heading.

Usage:
    python3 bookshelf_builder.py [--yaml bookshelf_v2.yaml] [--out bookshelf.html]
"""

import argparse
import html
import string
from pathlib import Path

import yaml

DOCS_DIR = Path(__file__).resolve().parent


def author_surname(author_key):
    """Sort/group key: the part before the comma in "Surname, First", or
    the whole key if it's a single word ("Various", "Herodotus")."""
    if "," in author_key:
        return author_key.split(",", 1)[0].strip().lower()
    return author_key.lower()


def display_author(author_key, book):
    """Text to show after "~ ": the book's own override if it has one
    (multi-author books), otherwise the author key reversed from
    "Surname, First" to "First Last" (a single-word key passes through)."""
    if book.get("author"):
        return book["author"]
    if "," in author_key:
        surname, given = author_key.split(",", 1)
        return f"{given.strip()} {surname.strip()}"
    return author_key


def flatten_books(data):
    """Yields (author_key, book_dict) for every book under every author."""
    for entry in data.get("books", []) or []:
        for author_key, books in entry.items():
            for book in books or []:
                yield author_key, book


def render_book(author_key, book):
    text = html.escape(book["title"])
    shown_author = display_author(author_key, book)
    if shown_author and author_key != "Unknown":
        text += f" ~ {html.escape(shown_author)}"
    if book.get("note"):
        text += f" [{html.escape(book['note'])}]"
    return f"<li>{text}</li>\n"


def render_books_in_order(entries):
    ordered = sorted(entries, key=lambda kb: kb[1].get("order", 0))
    return "".join(render_book(k, b) for k, b in ordered)


def render_books_alphabetical(entries):
    def letter(kb):
        key = author_surname(kb[0])
        return key[0].upper() if key and key[0].isalpha() else "#"

    groups = {}
    for kb in entries:
        groups.setdefault(letter(kb), []).append(kb)
    for group in groups.values():
        group.sort(key=lambda kb: author_surname(kb[0]))

    parts = []
    for letter_ch in string.ascii_uppercase:
        parts.append(f'<li class="heading">{letter_ch}</li>\n')
        parts.extend(render_book(k, b) for k, b in groups.get(letter_ch, []))
    return "".join(parts)


def render_section(section, all_books):
    entries = [(k, b) for k, b in all_books if b.get("section") == section["name"]]
    if not entries:
        return ""
    items = render_books_alphabetical(entries) if section.get("alphabetical") else render_books_in_order(entries)
    description_html = f"  <p>{html.escape(section['description'])}</p>\n\n" if section.get("description") else ""
    return (
        "<section>\n"
        f"  <h2>{html.escape(section['title'])}</h2>\n\n"
        f"{description_html}"
        f"  <ul>\n{items}</ul>\n"
        "</section>\n\n"
    )


def build(yaml_path: Path, out_path: Path):
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    title = data.get("title", "Bookshelf")
    subtitle = data.get("subtitle")
    all_books = list(flatten_books(data))
    sections_html = "".join(render_section(s, all_books) for s in data.get("sections", []))

    html_doc = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>{html.escape(title)}</title>
    <link rel="stylesheet" href="static/css/tufte.css" />
    <script src="static/js/theme.js"></script>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
  </head>

  <body>
    <article>
      <a href="index.html">◁ Back</a>
      <h1>{html.escape(title)}</h1>
      {f'<p class="subtitle">{html.escape(subtitle)}</p>' if subtitle else ''}

      {sections_html}
    </article>
  </body>
</html>
"""
    out_path.write_text(html_doc, encoding="utf-8")
    print(f"Wrote {out_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yaml", default="bookshelf_v2.yaml", help="Path to bookshelf_v2.yaml")
    parser.add_argument("--out", default="bookshelf.html", help="Output HTML file path")
    args = parser.parse_args()

    yaml_path = (DOCS_DIR / args.yaml).resolve()
    out_path = (DOCS_DIR / args.out).resolve()

    if not yaml_path.exists():
        raise SystemExit(f"YAML file not found: {yaml_path}")

    build(yaml_path, out_path)


if __name__ == "__main__":
    main()

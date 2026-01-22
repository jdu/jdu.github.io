# /// script
# dependencies = [
#   "jinja2",
# ]
# ///

from __future__ import annotations

from typing import Optional, Protocol
import os
import re
import subprocess
from pathlib import Path
import json
from enum import Enum
from dataclasses import dataclass, field
from lxml import etree

from jinja2 import Environment, FileSystemLoader, StrictUndefined

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# [SECTION] Const / Statistics
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

TEMPLATE_PATH = Path(__file__).parent / "template"

ENV = Environment(
    loader=FileSystemLoader(str(TEMPLATE_PATH)),
    undefined=StrictUndefined,
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
)


class ArticleType(Enum):
    POST = "POST"
    PAGE = "PAGE"


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# [SECTION] Traits
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


class Renderable(Protocol):
    def render(self) -> str: ...
    def get_output_name(self) -> str: ...


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# [SECTION] Structs
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


@dataclass
class TocItem:
    label: str
    ident: str
    level: int


@dataclass
class Article:
    title: str
    file_path: str
    content_raw: str
    content_converted: str
    kind: ArticleType

    def render(self):
        pass

    def get_output_name(self) -> str:
        out_name = self.file_path.split("/")[-1].replace(".adoc", ".html")

        return out_name


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# [SECTION] General Utilities
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def _get_pages() -> list[str]:
    """
    Get the list of available pages
    """
    return []


def _get_posts() -> list[Article]:
    """
    Get the list of available posts
    """
    posts = os.listdir("./test/posts")
    posts = [f"./test/posts/{x}" for x in posts if x.endswith(".adoc")]
    posts = list(sorted(posts))
    fmt_posts = [
        (
            x,
            _load_file_content(x),
        )
        for x in posts
    ]

    parsed_posts = [
        Article(
            _parse_title(x[1]),
            x[0],
            x[1],
            _convert_to_xml(x[0]),
            ArticleType.POST,
        )
        for x in fmt_posts
    ]

    return parsed_posts


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# [SECTION] Parser Combinator for AsciiDoc-like files
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# [SECTION] Page / Post Utilities
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def _convert_to_html(source: str) -> str:
    adoc_path = str(Path(source).resolve())

    proc = subprocess.run(
        ["asciidoctor", "-s", "-o", "-", adoc_path],
        check=True,
        capture_output=True,
        text=True,
    )

    return proc.stdout


def _convert_to_xml(source: str) -> str:
    adoc_path = str(Path(source).resolve())

    proc = subprocess.run(
        ["asciidoctor", "-b", "docbook5", "-o", "-", adoc_path],
        check=True,
        capture_output=True,
        text=True,
    )

    return proc.stdout


def _load_file_content(file_name: str) -> str:
    result = None
    with open(file_name, "r") as f:
        result = f.read()

    return result if not None else "No Title"


def _parse_toc(content: str) -> list[TocItem]:
    """
    Get the table of contents items from the
    body content
    """
    return []


def _parse_content(xml_content: str) -> str:
    blocks = []
    article_metadata = {}

    s = xml_content.lstrip("\ufeff").lstrip()
    _illegal_xml10 = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")

    xml_str = _illegal_xml10.sub("", s)
    parser = etree.XMLParser(recover=True, huge_tree=True)
    tree = etree.fromstring(xml_str.encode("utf-8"), parser=parser)
    articles = tree.xpath("//*[local-name()='article']")

    NS = {"db": "http://docbook.org/ns/docbook"}

    if not articles:
        raise ValueError("No <article> found")

    def local(el) -> str:
        return etree.QName(el).localname

    # TODO: Need to track section levels

    for child in articles:
        if local(child) == "info":
            continue

        for el in child.iter():
            tag = local(el)

            match tag:
                case "title":
                    text = " ".join(" ".join(el.itertext()).split())
                    blocks.append(
                        {
                            "kind": "TITLE",
                            "content": text,
                        }
                    )
                case "simpara":
                    text = " ".join(" ".join(el.itertext()).split())
                    blocks.append(
                        {
                            "kind": "PARA",
                            "content": text,
                        }
                    )
                case "section":
                    blocks.append(
                        {
                            "kind": "SECTION_START",
                            "content": text,
                        }
                    )
                case "link":
                    text = " ".join(" ".join(el.itertext()).split())
                    blocks.append(
                        {
                            "kind": "LINK",
                            "content": text,
                        }
                    )

                case _:
                    print(tag)
                    continue

    return ""


def _parse_title(content: str | None) -> str:
    """
    Parse the title from a raw content string
    """
    if content is None:
        return "No Title"

    for line in content:
        if line.startswith("= "):
            title = line.replace("= ", "")
            return title

    return "No Title"


def output_posts(posts: list[Article]) -> None:
    tmpl = ENV.get_template("tmpl.page.html")
    for item in posts:
        out_name = item.get_output_name()
        _parse_content(item.content_converted)
        output = tmpl.render(
            {
                "content": item.content_converted,
                "page_title": item.title,
            }
        )
        out_path = Path(f"./dist/posts/{out_name}")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="UTF-8")


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# [SECTION] Entrypoint
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def main():
    posts = _get_posts()
    output_posts(posts)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3

import os
from subprocess import PIPE
import subprocess
import signal
import shutil
import logging
import sys

import jinja2
from jinja2 import Environment, FileSystemLoader, select_autoescape

logging.basicConfig(level=logging.DEBUG)

OUTPUT_FOLDER = "docs"

# Create the main output folder in case it doesn't exist
if not os.path.exists(OUTPUT_FOLDER):
    logging.info("Creating output folder: {}".format(OUTPUT_FOLDER))
    os.makedirs(OUTPUT_FOLDER)


def _create_output_path(path_to_create: str):
    """ Create a path if it does not exist """
    print(path_to_create)
    if not os.path.exists(path_to_create):
        os.makedirs(path_to_create)

    return True

j_env = Environment(
    loader=FileSystemLoader("template")
)

def _call_adoc(content):
    p = subprocess.Popen(
        ["asciidoctor -a data-uri -sq -o - -"],
        shell=True,
        stdin=PIPE,
        stdout=PIPE,
        universal_newlines=True,
        bufsize=-1,
    )
    outs, errors = p.communicate(content)

    p.kill()

    return outs


class Page:

    def __init__(self, file_path):
        content = None
        self.file_path = file_path
        with open(self.file_path, "r") as f:
            content = f.read()

        self.raw_content = content
        self.title = self.raw_content.splitlines()[0].replace("= ", "")
        self.content = _call_adoc(content)
        self.content_type = None
        self.raw_file_name = file_path.split("/")[-1]
        self.mod_file_name = self.raw_file_name.replace(".adoc", ".html")

        self.url = None
        if "pages" in self.file_path:
            self.content_type = "PAGE"
            self.url = f"/pages/{self.mod_file_name}"
        elif "posts" in self.file_path:
            self.content_type = "POST"
            self.url = f"/posts/{self.mod_file_name}"
        elif "research" in self.file_path:
            self.content_type = "RESEARCH"
            self.url = f"/research/{self.mod_file_name}"
        elif "reading" in self.file_path:
            self.content_type = "READING"
            self.url = f"/reading/{self.mod_file_name}"

        self.toc = []
        self._get_contents()

    @staticmethod
    def _create_id(item):
        ident = item.replace(" ", "_")
        ident = "_" + ident
        ident = ident.replace("'", "")
        ident = ident.replace("’", "")
        ident = ident.replace("*", "")
        ident = ident.lower()
        ident = ident.replace("_/_", "_")
        ident = ident.replace("(", "")
        ident = ident.replace(")", "")
        ident = ident.replace("/", "")
        ident = ident.replace("`", "")
        ident = ident.replace("!", "")
        return ident


    def _get_contents(self):
        """
            Build up a list of toc items
        """
        raw_toc = []
        toc = []
        for row in self.raw_content.splitlines():
            if row.startswith("="):
                raw_toc.append(row)

        counter_a = 0
        counter_b = 0
        counter_c = 0
        for item in raw_toc:
            if item.startswith("= "):
                toc.append(("0", item.replace("= ", "")))
            elif item.startswith("== "):
                counter_a += 1
                counter_b = 0
                counter_c = 0
                toc.append((counter_a, item.replace("== ", "")))
            elif item.startswith("=== "):
                counter_b += 1
                counter_c = 0
                toc.append((f"{counter_a}.{counter_b}", item.replace("=== ", "")))
            elif item.startswith("==== "):
                counter_c += 1
                toc.append((f"{counter_a}.{counter_b}.{counter_c}", item.replace("==== ", "")))

        toc = list(map(lambda x: (x[0], x[1].replace("*", ""), Page._create_id(x[1])), toc))

        self.toc = toc

    def render(self):
        logging.info(f"Rendering: {self.file_path}")
        title = self.raw_content.splitlines()[0].replace("= ", "")
        out_path = self.file_path.replace(".adoc", ".html")
        out_path = out_path.replace("src", "docs")
        folder_out = os.path.dirname(os.path.abspath(out_path))
        _create_output_path(folder_out)

        tmpl = None
        print(self.content_type)

        match self.content_type:
            case "PAGE":
                tmpl = j_env.get_template("page.html")
            case "POST":
                tmpl = j_env.get_template("post.html")
            case "RESEARCH":
                tmpl = j_env.get_template("post.html")
            case "READING":
                tmpl = j_env.get_template("post.html")

        with open(out_path, "w") as f:
            f.write(tmpl.render(
                page_content=self.content,
                page_title=title,
                toc=self.toc[1:]
            ))


def _render_index(posts, reading):
    posts = list(sorted(posts, key=lambda x: x.raw_file_name, reverse=True))
    reading = list(sorted(reading, key=lambda x: x.raw_file_name, reverse=True))
    tmpl = j_env.get_template("index.html")
    output_path = f"{OUTPUT_FOLDER}/index.html"
    output = tmpl.render(
        posts=posts,
        reading=reading,
    )
    print(output_path)
    with open(output_path, "w") as f:
        f.write(output)

    logging.info("Rendered index")


def run():
    pages = []
    for (dirpath, dirnames, filenames) in os.walk("src"):
        for filename in filenames:
            if filename.endswith("adoc"):
                if "xxxx" not in filename:
                    pg = Page(os.path.join(dirpath, filename))
                    pg.render()
                    pages.append(pg)


    _render_index(
        posts=list(filter(lambda x: x.content_type == "POST", pages)),
        reading=list(filter(lambda x: x.content_type == "READING", pages)),
    )

    _create_output_path(f"{OUTPUT_FOLDER}/images/")
    shutil.rmtree("docs/images/")
    shutil.copytree("src/posts/images/", "docs/images/")


if __name__ == "__main__":
    run()

"""
lex.bg HTML → Markdown converter.

Converts HTML documents scraped from lex.bg into Markdown that follows
the unified legal-document Markdown conventions (see ``md_conventions.md``).

This is the **source-specific** step.  The resulting ``.md`` file is then
fed into the universal ``parser_markdown.py`` to produce the final JSON.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag, NavigableString


# ── regex helpers ─────────────────────────────────────────────────────────

RE_ARTICLE_NUM = re.compile(r"^Чл\.\s*(\d+[а-я]?)\.")
RE_PARAGRAPH = re.compile(r"^\((\d+)\)\s*")
RE_POINT = re.compile(r"^(\d+[а-я]?)\.\s+")
RE_SUBPOINT = re.compile(r"^(\d+\.\d+)\.\s+")
RE_SUB_SUBPOINT = re.compile(r"^(\d+\.\d+\.\d+)\.\s+")
RE_TRIPLE_LETTER = re.compile(r"^([а-я]{3})\)\s*")
RE_DOUBLE_LETTER = re.compile(r"^([а-я]{2})\)\s*")
RE_LETTER = re.compile(r"^([а-я])\)\s*")
RE_LATIN_LETTER = re.compile(r"^([a-z])\)\s*")
RE_CLAUSE = re.compile(r"^§\s*(\d+[а-я]?)\.")

RE_CHAPTER = re.compile(r"^Глава\s+", re.IGNORECASE)
RE_PARTITION = re.compile(r"^Дял\s+", re.IGNORECASE)
RE_SECTION_PREFIX = re.compile(r"^Раздел\s+", re.IGNORECASE)
RE_SUBSECTION = re.compile(r"^Подраздел\s+", re.IGNORECASE)
RE_PART_ALLCAPS = re.compile(r"^Част\s+", re.IGNORECASE)

# Provision-like headings
RE_PROVISION = re.compile(
    r"(?:допълнителн|особен|преходн|заключителн).*разпоредб",
    re.IGNORECASE,
)

# ── CSS class map ─────────────────────────────────────────────────────────

CSS_CLASS_MAP: dict[str, str] = {
    "TitleDocument": "_title",
    "PreHistory": "_pre_history",
    "HistoryOfDocument": "_history",
    "Heading": "_heading",
    "Section": "section",
    "Article": "article",
    "TransitionalFinalEdicts": "provision",
    "FinalEdicts": "provision",
    "AdditionalEdicts": "provision",
    "FinalEdictsArticle": "clause",
}

# ── text helpers ──────────────────────────────────────────────────────────


def _text(el: Tag) -> str:
    """Clean text from an element."""
    t = el.get_text(separator=" ", strip=True)
    return re.sub(r"[ \t]+", " ", t).strip()


def _inner_html(el: Tag) -> str:
    return "".join(str(c) for c in el.children).strip()


def _is_noise(div: Tag) -> bool:
    el_id = div.get("id", "")
    if isinstance(el_id, str) and el_id.startswith("buttons_"):
        return True
    style = div.get("style", "")
    if isinstance(style, str) and "margin-top" in style and not _text(div):
        return True
    return False


def _first_css(div: Tag) -> str:
    classes = div.get("class", [])
    if isinstance(classes, list) and classes:
        return classes[0]
    return classes if isinstance(classes, str) else ""


# ── main converter ────────────────────────────────────────────────────────


class LexbgHtmlToMarkdown:
    """Convert a lex.bg HTML file to the canonical Markdown format."""

    def convert(self, file_path: str | Path) -> str:
        """Return the Markdown string for *file_path*."""
        path = Path(file_path)
        html = path.read_text(encoding="utf-8")
        self._soup = BeautifulSoup(html, "lxml")
        lines: list[str] = []

        self._convert_metadata(lines)
        self._convert_body(lines)

        return "\n".join(lines) + "\n"

    # ── metadata ──

    def _convert_metadata(self, lines: list[str]) -> None:
        title_div = self._soup.find("div", class_="TitleDocument")
        if title_div:
            lines.append(f"# {_text(title_div)}")
            lines.append("")

        pre_hist = self._soup.find("div", class_="PreHistory")
        if pre_hist:
            lines.append(f"<!-- explanation: {_text(pre_hist)} -->")
            lines.append("")

        hist_div = self._soup.find("div", class_="HistoryOfDocument")
        if hist_div:
            lines.append(f"<!-- history: {_text(hist_div)} -->")
            lines.append("")

    # ── body ──

    def _convert_body(self, lines: list[str]) -> None:
        container = (
            self._soup.find("div", class_="boxi")
            or self._soup.find("div", class_="content")
            or self._soup.body
        )
        if container is None:
            return

        for child in container.children:
            if not isinstance(child, Tag) or child.name != "div":
                continue
            self._convert_div(child, lines)

    def _convert_div(self, div: Tag, lines: list[str]) -> None:
        css = _first_css(div)

        # Skip metadata (already handled) and noise
        if css in ("TitleDocument", "PreHistory", "HistoryOfDocument"):
            return
        if _is_noise(div):
            return

        mapped = CSS_CLASS_MAP.get(css)
        if mapped is None:
            return

        if mapped == "_title":
            return  # already handled
        if mapped == "_pre_history":
            return
        if mapped == "_history":
            return

        if mapped == "_heading":
            self._heading_to_md(div, lines)
        elif mapped == "section":
            self._section_to_md(div, lines)
        elif mapped == "article":
            self._article_to_md(div, lines)
        elif mapped == "provision":
            self._provision_to_md(div, lines)
        elif mapped == "clause":
            self._clause_to_md(div, lines)

    # ── structural headings ──

    def _heading_to_md(self, div: Tag, lines: list[str]) -> None:
        text = _text(div)
        if not text:
            return

        # Provision-type heading
        if RE_PROVISION.search(text):
            lines.append("")
            lines.append(f"## PROVISION: {text}")
            lines.append("")
            return

        # "Част ..." → Part (level 2)
        if RE_PART_ALLCAPS.match(text):
            lines.append("")
            lines.append(f"## PART: {text}")
            lines.append("")
            return

        # "Дял ..." → Partition (level 3)
        if RE_PARTITION.match(text):
            lines.append("")
            lines.append(f"### PARTITION: {text}")
            lines.append("")
            return

        # "Глава ..." → Chapter (level 3)
        if RE_CHAPTER.match(text):
            lines.append("")
            lines.append(f"### CHAPTER: {text}")
            lines.append("")
            return

        # ALL-CAPS text → Part
        alpha = [c for c in text if c.isalpha()]
        if alpha and sum(1 for c in alpha if c.isupper()) / len(alpha) > 0.7:
            lines.append("")
            lines.append(f"## PART: {text}")
            lines.append("")
            return

        # Fallback: generic heading
        lines.append("")
        lines.append(f"#### HEADING: {text}")
        lines.append("")

    def _section_to_md(self, div: Tag, lines: list[str]) -> None:
        text = _text(div)
        if not text:
            return

        if RE_SUBSECTION.match(text):
            lines.append("")
            lines.append(f"##### SUBSECTION: {text}")
            lines.append("")
        else:
            lines.append("")
            lines.append(f"#### SECTION: {text}")
            lines.append("")

    # ── article ──

    def _article_to_md(self, div: Tag, lines: list[str]) -> None:
        # Check for EU legislation block
        title_p = div.find("p", class_="Title")
        if title_p and "Релевантни актове" in _text(title_p):
            self._eu_legislation_to_md(div, lines)
            return

        content_divs = self._get_content_divs(div)
        if not content_divs:
            return

        lines.append("")

        for i, cdiv in enumerate(content_divs):
            text = _text(cdiv)
            if not text:
                continue

            if i == 0:
                # First div — should start with "Чл. N."
                m = RE_ARTICLE_NUM.match(text)
                if m:
                    # Keep article header as-is — the MD parser will handle it
                    lines.append(text)
                else:
                    lines.append(text)
            else:
                lines.append(text)

        lines.append("")

    # ── provision / clause ──

    def _provision_to_md(self, div: Tag, lines: list[str]) -> None:
        text = _text(div)
        if text:
            lines.append("")
            lines.append(f"## PROVISION: {text}")
            lines.append("")

    def _clause_to_md(self, div: Tag, lines: list[str]) -> None:
        content_divs = self._get_content_divs(div)
        if not content_divs:
            return

        lines.append("")
        for cdiv in content_divs:
            text = _text(cdiv)
            if text:
                lines.append(text)
        lines.append("")

    # ── EU legislation ──

    def _eu_legislation_to_md(self, div: Tag, lines: list[str]) -> None:
        text = _text(div)
        if text:
            lines.append("")
            lines.append(f"<!-- eu_legislation: {text} -->")
            lines.append("")

    # ── helpers ──

    def _get_content_divs(self, parent: Tag) -> list[Tag]:
        """Get all meaningful content <div>s from an article/clause element.

        Handles lxml's DOM rewrite where <div>s inside <p> are moved out.
        """
        divs: list[Tag] = []
        for child in parent.children:
            if isinstance(child, Tag) and child.name == "div" and not _is_noise(child):
                text = _text(child)
                if text:
                    divs.append(child)
        return divs


# ── CLI ──

def main() -> None:
    import argparse, sys

    ap = argparse.ArgumentParser(
        description="Convert lex.bg HTML to Markdown."
    )
    ap.add_argument("file", help="Path to the HTML file.")
    ap.add_argument("-o", "--output", help="Output .md file (default: stdout).")
    args = ap.parse_args()

    converter = LexbgHtmlToMarkdown()
    md = converter.convert(args.file)

    if args.output:
        Path(args.output).write_text(md, encoding="utf-8")
        print(f"→ {args.output}", file=sys.stderr)
    else:
        print(md)


if __name__ == "__main__":
    main()

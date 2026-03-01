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

import sys
import urllib.request

from bs4 import BeautifulSoup, Tag, NavigableString
from convertors.table_normalizer import normalize_tables_in_html


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

    # Default source identifier for lex.bg documents
    SOURCE = "lex.bg"

    def convert(
        self,
        file_path: str | Path,
        *,
        source: str = "",
        doc_id: str = "",
        images_dir: str | Path | None = None,
    ) -> str:
        """Return the Markdown string for *file_path*.

        Parameters
        ----------
        source : str
            Document source identifier written into the MD.
            Defaults to ``self.SOURCE`` (``"lex.bg"``).
        doc_id : str
            Short document identifier (e.g. ``"НК"``).  If empty,
            the parser will derive one from the title.
        images_dir : path, optional
            Directory to download content images into.  When set,
            images are referenced as ``images/{doc_stem}/{file}``;
            when *None*, original URLs are kept.
        """
        path = Path(file_path)
        self._doc_stem = path.stem
        self._images_dir = Path(images_dir) if images_dir else None
        self._image_counter = 0

        html = path.read_text(encoding="utf-8")
        # Normalize tables before parsing (merges split def+defFix tables)
        html = normalize_tables_in_html(html)
        self._soup = BeautifulSoup(html, "lxml")
        self._preprocess_soup()

        lines: list[str] = []
        self._convert_metadata(lines, source=source or self.SOURCE, doc_id=doc_id)
        self._convert_body(lines)

        return "\n".join(lines) + "\n"

    # ── preprocessing ──

    def _preprocess_soup(self) -> None:
        """Remove lex.bg UI elements (icon images, javascript links)."""
        # Remove 18×18 navigation icon images
        for img in self._soup.find_all("img"):
            if img.get("width") == "18" and img.get("height") == "18":
                img.decompose()

        # Remove javascript links
        for a in self._soup.find_all("a"):
            href = a.get("href", "")
            onclick = a.get("onclick", "")
            if "javascript:" in href or "javascript:" in onclick:
                a.decompose()

    # ── metadata ──

    def _convert_metadata(
        self, lines: list[str], *, source: str, doc_id: str,
    ) -> None:
        # Source & doc_id — always first so the MD file is self-contained
        lines.append(f"<!-- source: {source} -->")
        if doc_id:
            lines.append(f"<!-- doc_id: {doc_id} -->")
        lines.append("")

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
            lines.append(f"## {text}")
            lines.append("")
            return

        # "Част ..." → Part (level 2)
        if RE_PART_ALLCAPS.match(text):
            lines.append("")
            lines.append(f"## {text}")
            lines.append("")
            return

        # "Дял ..." → Partition (level 3)
        if RE_PARTITION.match(text):
            lines.append("")
            lines.append(f"### {text}")
            lines.append("")
            return

        # "Глава ..." → Chapter (level 3)
        if RE_CHAPTER.match(text):
            lines.append("")
            lines.append(f"### {text}")
            lines.append("")
            return

        # ALL-CAPS text → Part
        alpha = [c for c in text if c.isalpha()]
        if alpha and sum(1 for c in alpha if c.isupper()) / len(alpha) > 0.7:
            lines.append("")
            lines.append(f"## {text}")
            lines.append("")
            return

        # Fallback: generic heading
        lines.append("")
        lines.append(f"#### {text}")
        lines.append("")

    def _section_to_md(self, div: Tag, lines: list[str]) -> None:
        text = _text(div)
        if not text:
            return

        if RE_SUBSECTION.match(text):
            lines.append("")
            lines.append(f"##### {text}")
            lines.append("")
        else:
            lines.append("")
            lines.append(f"#### {text}")
            lines.append("")

    # ── article ──

    def _article_to_md(self, div: Tag, lines: list[str]) -> None:
        # Check for EU legislation block
        title_p = div.find("p", class_="Title")
        if title_p and "Релевантни актове" in _text(title_p):
            self._eu_legislation_to_md(div, lines)
            return

        lines.append("")
        self._emit_content_children(div, lines)
        lines.append("")

    # ── provision / clause ──

    def _provision_to_md(self, div: Tag, lines: list[str]) -> None:
        text = _text(div)
        if text:
            lines.append("")
            lines.append(f"## {text}")
            lines.append("")

    def _clause_to_md(self, div: Tag, lines: list[str]) -> None:
        lines.append("")
        self._emit_content_children(div, lines)
        lines.append("")

    # ── EU legislation ──

    # Category label → markdown tag
    _EU_CATEGORIES: dict[str, str] = {
        "Директиви:": "EU_DIRECTIVE",
        "Регламенти:": "EU_REGULATION",
        "Решения:": "EU_DECISION",
        "Други актове:": "EU_OTHER_ACT",
    }

    def _eu_legislation_to_md(self, div: Tag, lines: list[str]) -> None:
        """Emit structured Markdown for the EU-legislation block.

        Produces::

            ## EU_LEGISLATION: Релевантни актове …
            ### EU_DIRECTIVE: Директиви
            ДИРЕКТИВА …
            ### EU_REGULATION: Регламенти
            РЕГЛАМЕНТ …
        """
        title_p = div.find("p", class_="Title")
        title = _text(title_p) if title_p else "Релевантни актове от Европейското законодателство"

        lines.append("")
        lines.append(f"## {title}")
        lines.append("")

        current_tag: str | None = None

        for child in div.children:
            if not isinstance(child, Tag):
                continue
            # Skip the title <p> and buttons
            if child.name == "p":
                continue
            if child.name == "br":
                continue
            if child.name != "div":
                continue

            text = _text(child)
            if not text:
                continue

            # Check if this is a category header ("Директиви:", etc.)
            matched = False
            for label, tag in self._EU_CATEGORIES.items():
                if text == label or text.rstrip(":") + ":" == label:
                    current_tag = tag
                    lines.append(f"### {text.rstrip(':')}")
                    lines.append("")
                    matched = True
                    break
            if matched:
                continue

            # Regular item under the current category
            lines.append(text)

        lines.append("")

    # ── content emission helpers ──

    def _emit_content_children(self, parent: Tag, lines: list[str]) -> None:
        """Emit Markdown for all content children: text divs, tables, images."""
        for child in parent.children:
            if not isinstance(child, Tag):
                continue

            # Direct <table> child → emit as raw HTML block
            if child.name == "table":
                lines.append(str(child).strip())
                continue

            # Only process <div> children beyond this point
            if child.name != "div":
                continue

            if _is_noise(child):
                continue

            # Image-only div → download image and emit MD reference
            img = self._find_content_image(child)
            if img is not None and not _text(child).strip():
                md_ref = self._download_and_ref_image(img)
                if md_ref:
                    lines.append(md_ref)
                continue

            # Div wrapping a table → emit inner table(s) as raw HTML
            if child.find("table"):
                for tbl in child.find_all("table", recursive=False):
                    lines.append(str(tbl).strip())
                continue

            # Regular text div
            text = _text(child)
            if text:
                lines.append(text)

    def _find_content_image(self, el: Tag) -> Tag | None:
        """Return the first content image in *el*, or *None*.

        Navigation icons (18×18) are already removed in preprocessing.
        """
        for img in el.find_all("img"):
            if img.get("src"):
                return img
        return None

    def _download_and_ref_image(self, img: Tag) -> str | None:
        """Download *img* and return a Markdown image reference.

        If ``images_dir`` was set, the image is saved locally and the
        reference uses a relative path.  Otherwise the original URL is kept.
        """
        src = img.get("src", "")
        if not src:
            return None

        alt = img.get("alt") or img.get("title") or "image"

        if self._images_dir:
            self._image_counter += 1
            url_path = src.split("?")[0]
            ext = Path(url_path).suffix or ".png"
            filename = f"image_{self._image_counter:03d}{ext}"

            self._images_dir.mkdir(parents=True, exist_ok=True)
            local_path = self._images_dir / filename
            try:
                urllib.request.urlretrieve(src, str(local_path))
            except Exception as exc:
                print(
                    f"  Warning: could not download {src}: {exc}",
                    file=sys.stderr,
                )

            rel_path = f"images/{self._doc_stem}/{filename}"
            return f"![{alt}]({rel_path})"

        # No images_dir — keep original URL
        return f"![{alt}]({src})"


# ── CLI ──

def main() -> None:
    import argparse, sys

    ap = argparse.ArgumentParser(
        description="Convert lex.bg HTML to Markdown."
    )
    ap.add_argument("file", help="Path to the HTML file.")
    ap.add_argument("-o", "--output", help="Output .md file (default: stdout).")
    ap.add_argument("-s", "--source", default="",
                    help="Source identifier (default: lex.bg).")
    ap.add_argument("-d", "--doc-id", default="",
                    help="Document id (e.g. 'НК').")
    args = ap.parse_args()

    converter = LexbgHtmlToMarkdown()
    # Determine images directory (next to output, if output is specified)
    images_dir = None
    if args.output:
        out_parent = Path(args.output).parent
        doc_stem = Path(args.file).stem
        images_dir = out_parent / "images" / doc_stem
    md = converter.convert(
        args.file,
        source=args.source,
        doc_id=args.doc_id,
        images_dir=images_dir,
    )

    if args.output:
        Path(args.output).write_text(md, encoding="utf-8")
        print(f"→ {args.output}", file=sys.stderr)
    else:
        print(md)


if __name__ == "__main__":
    main()

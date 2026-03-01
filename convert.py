#!/usr/bin/env python3
"""
CLI entry point for the two-step legal-document conversion pipeline.

Pipeline
--------
1. **Source → Markdown** — a source-specific converter (e.g.
   ``converter_lexbg.py`` for lex.bg HTML) produces a Markdown file that
   follows the unified Markdown conventions.
2. **Markdown → JSON** — the universal ``parser_markdown.py`` transforms
   the Markdown into the final hierarchical JSON.

Usage
-----
    # Full pipeline: HTML → MD → JSON (source & doc_id embedded in MD)
    python convert.py --file input_docs/document.html --source lex.bg --doc-id НК

    # All HTML files in input_docs/ (source defaults to lex.bg):
    python convert.py --doc-id НК

    # Without --doc-id: derived automatically from the document title
    python convert.py --file input_docs/document.html

    # MD-only (source & doc_id read from the .md file itself):
    python convert.py --file output_md/document.md --md-only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from converter_lexbg import LexbgHtmlToMarkdown
from parser_markdown import MarkdownParser

INPUT_DIR = Path("input_docs")
OUTPUT_MD_DIR = Path("output_md")
OUTPUT_JSON_DIR = Path("output_json")

# Source-specific converters (add new sources here)
SOURCE_CONVERTERS: dict[str, type] = {
    "lex.bg": LexbgHtmlToMarkdown,
}


def convert_html_to_md(
    file_path: Path,
    source: str,
    doc_id: str,
) -> Path:
    """Step 1: Convert a source file to Markdown.

    *source* and *doc_id* are embedded into the MD as HTML comments
    so that the file is self-contained.

    Returns the path to the generated .md file.
    """
    converter_cls = SOURCE_CONVERTERS.get(source)
    if converter_cls is None:
        print(
            f"Error: no converter registered for source '{source}'. "
            f"Available: {', '.join(SOURCE_CONVERTERS)}",
            file=sys.stderr,
        )
        sys.exit(1)

    converter = converter_cls()
    images_dir = OUTPUT_MD_DIR / "images" / file_path.stem
    md_text = converter.convert(
        file_path, source=source, doc_id=doc_id, images_dir=images_dir,
    )

    OUTPUT_MD_DIR.mkdir(parents=True, exist_ok=True)
    md_path = OUTPUT_MD_DIR / (file_path.stem + ".md")
    md_path.write_text(md_text, encoding="utf-8")
    print(f"  [1/2] MD  → {md_path}")
    return md_path


def convert_md_to_json(
    md_path: Path,
    source: str = "",
    doc_id: str = "",
) -> Path:
    """Step 2: Parse Markdown and produce the unified JSON.

    *source* and *doc_id* are optional overrides — the parser
    will read them from the MD file if not provided.

    Returns the path to the generated .json file.
    """
    parser = MarkdownParser()
    result = parser.parse(md_path, source=source, doc_id=doc_id)

    OUTPUT_JSON_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUTPUT_JSON_DIR / (md_path.stem + ".json")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"  [2/2] JSON → {json_path}")
    return json_path


def convert_file(
    file_path: Path,
    source: str = "",
    doc_id: str = "",
    *,
    md_only: bool = False,
) -> None:
    """Run the full (or partial) pipeline for a single file."""
    print(f"Processing: {file_path}")

    if md_only or file_path.suffix == ".md":
        # Skip step 1 — assume the file is already Markdown
        md_path = file_path
    else:
        md_path = convert_html_to_md(file_path, source or "lex.bg", doc_id)

    convert_md_to_json(md_path, source, doc_id)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Convert legal documents to the unified JSON format "
                    "(two-step pipeline: Source → Markdown → JSON)."
    )
    ap.add_argument(
        "--file",
        type=str,
        default=None,
        help="Path to a single source file (HTML or MD). "
             "If omitted, all .html files in input_docs/ are processed.",
    )
    ap.add_argument(
        "--source",
        type=str,
        default="lex.bg",
        help="Document source identifier (default: lex.bg).",
    )
    ap.add_argument(
        "--doc-id",
        type=str,
        default="",
        help="Document identifier (e.g. 'НК'). If omitted, read from MD "
             "or derived from the title.",
    )
    ap.add_argument(
        "--md-only",
        action="store_true",
        help="Skip source→MD conversion (input is already Markdown).",
    )

    args = ap.parse_args()

    if args.file:
        path = Path(args.file)
        if not path.exists():
            print(f"Error: file not found: {path}", file=sys.stderr)
            sys.exit(1)
        convert_file(path, args.source, args.doc_id, md_only=args.md_only)
    else:
        html_files = sorted(INPUT_DIR.glob("*.html"))
        if not html_files:
            print(f"No .html files found in {INPUT_DIR}/", file=sys.stderr)
            sys.exit(1)
        for path in html_files:
            convert_file(path, args.source, args.doc_id)

    print("Done.")


if __name__ == "__main__":
    main()

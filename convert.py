#!/usr/bin/env python3
"""
CLI entry point for converting legal documents to the unified JSON format.

Usage
-----
    # Convert a single file:
    python convert.py --file input_docs/document.html --source lex.bg --doc-id НК

    # Convert all HTML files in input_docs/:
    python convert.py --source lex.bg --doc-id НК
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from parser_html import HtmlParser

INPUT_DIR = Path("input_docs")
OUTPUT_DIR = Path("output_json")


def convert_file(file_path: Path, source: str, doc_id: str) -> None:
    """Parse a single HTML file and write the JSON output."""
    print(f"Processing: {file_path}")

    parser = HtmlParser()
    result = parser.parse(file_path, source=source, doc_id=doc_id)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_name = file_path.stem + ".json"
    out_path = OUTPUT_DIR / out_name

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"  → {out_path}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Convert legal documents to the unified JSON format."
    )
    ap.add_argument(
        "--file",
        type=str,
        default=None,
        help="Path to a single HTML file. If omitted, all .html files in "
             "input_docs/ are processed.",
    )
    ap.add_argument(
        "--source",
        type=str,
        required=True,
        help="Document source identifier (e.g. 'lex.bg').",
    )
    ap.add_argument(
        "--doc-id",
        type=str,
        required=True,
        help="Document identifier (e.g. 'НК').",
    )

    args = ap.parse_args()

    if args.file:
        path = Path(args.file)
        if not path.exists():
            print(f"Error: file not found: {path}", file=sys.stderr)
            sys.exit(1)
        convert_file(path, args.source, args.doc_id)
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

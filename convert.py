#!/usr/bin/env python3
"""
CLI entry point for converting Markdown legal documents to unified JSON.

Usage
-----
    # Single file:
    python convert.py --file input_md/document.md

    # With explicit source & doc-id overrides:
    python convert.py --file input_md/document.md --source lex.bg --doc-id НК

    # All .md files in input_md/:
    python convert.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from parser_markdown import MarkdownParser

INPUT_DIR = Path("input_md")
OUTPUT_JSON_DIR = Path("output_json")


def convert_md_to_json(
    md_path: Path,
    source: str = "",
    doc_id: str = "",
) -> Path:
    """Parse a Markdown file and produce the unified JSON.

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

    print(f"  JSON → {json_path}")
    return json_path


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Convert Markdown legal documents to the unified JSON format."
    )
    ap.add_argument(
        "--file",
        type=str,
        default=None,
        help="Path to a single .md file. "
             "If omitted, all .md files in input_md/ are processed.",
    )
    ap.add_argument(
        "--source",
        type=str,
        default="",
        help="Document source identifier (override). "
             "If omitted, read from the MD file.",
    )
    ap.add_argument(
        "--doc-id",
        type=str,
        default="",
        help="Document identifier (e.g. 'НК'). If omitted, read from MD "
             "or derived from the title.",
    )

    args = ap.parse_args()

    if args.file:
        path = Path(args.file)
        if not path.exists():
            print(f"Error: file not found: {path}", file=sys.stderr)
            sys.exit(1)
        print(f"Processing: {path}")
        convert_md_to_json(path, args.source, args.doc_id)
    else:
        md_files = sorted(INPUT_DIR.glob("*.md"))
        if not md_files:
            print(f"No .md files found in {INPUT_DIR}/", file=sys.stderr)
            sys.exit(1)
        for path in md_files:
            print(f"Processing: {path}")
            convert_md_to_json(path, args.source, args.doc_id)

    print("Done.")


if __name__ == "__main__":
    main()

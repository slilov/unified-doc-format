"""
Universal Markdown → JSON parser for the unified legal document format.

Reads Markdown files produced by source-specific converters (e.g.
``converter_lexbg.py``) and builds the hierarchical Node tree that is
serialised to JSON.

This is the **format-independent** step — it does not know (or care)
which HTML/PDF source produced the Markdown.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from config import (
    NodeType,
    HIERARCHY_ORDER,
    ARTICLE_INTERNAL_HIERARCHY,
    PROVISION_INTERNAL_HIERARCHY,
    ordinal_to_number,
    normalize_structural_text,
)
from models import Node, ItemCounter, generate_uids
from base_parser import BaseParser


# ── Regex patterns for Markdown elements ──────────────────────────────────

# Structural headings (from Markdown heading markers)
RE_MD_PART = re.compile(r"^##\s+PART:\s*(.+)", re.DOTALL)
RE_MD_CHAPTER = re.compile(r"^###\s+CHAPTER:\s*(.+)", re.DOTALL)
RE_MD_PARTITION = re.compile(r"^###\s+PARTITION:\s*(.+)", re.DOTALL)
RE_MD_SECTION = re.compile(r"^####\s+SECTION:\s*(.+)", re.DOTALL)
RE_MD_SUBSECTION = re.compile(r"^#####\s+SUBSECTION:\s*(.+)", re.DOTALL)
RE_MD_HEADING = re.compile(r"^####\s+HEADING:\s*(.+)", re.DOTALL)
RE_MD_PROVISION = re.compile(r"^##\s+PROVISION:\s*(.+)", re.DOTALL)

# Title (level 1 heading)
RE_MD_TITLE = re.compile(r"^#\s+(.+)")

# Metadata comments
RE_EXPLANATION = re.compile(r"^<!--\s*explanation:\s*(.+?)\s*-->$", re.DOTALL)
RE_HISTORY = re.compile(r"^<!--\s*history:\s*(.+?)\s*-->$", re.DOTALL)
RE_EU_LEGISLATION = re.compile(r"^<!--\s*eu_legislation:\s*(.+?)\s*-->$", re.DOTALL)

# Article-level elements (inside plain text lines)
RE_ARTICLE_NUM = re.compile(r"^Чл\.\s*(\d+[а-я]?)\.\s*(.*)", re.DOTALL)
RE_PARAGRAPH = re.compile(r"^\((\d+)\)\s*(.*)", re.DOTALL)
RE_CLAUSE = re.compile(r"^§\s*(\d+[а-я]?)\.\s*(.*)", re.DOTALL)

# Article-internal fine-grained elements
RE_SUB_SUBPOINT = re.compile(r"^(\d+\.\d+\.\d+)\.\s+(.*)", re.DOTALL)
RE_SUBPOINT = re.compile(r"^(\d+\.\d+)\.\s+(.*)", re.DOTALL)
RE_POINT = re.compile(r"^(\d+[а-я]?)\.\s+(.*)", re.DOTALL)
RE_TRIPLE_LETTER = re.compile(r"^([а-я]{3})\)\s*(.*)", re.DOTALL)
RE_DOUBLE_LETTER = re.compile(r"^([а-я]{2})\)\s*(.*)", re.DOTALL)
RE_LETTER = re.compile(r"^([а-я])\)\s*(.*)", re.DOTALL)
RE_LATIN_LETTER = re.compile(r"^([a-z])\)\s*(.*)", re.DOTALL)

# Dots separator line (omitted articles in provisions)
RE_DOTS = re.compile(r"^\.\s*\.\s*\.\s*\.\s*")

# ── Heading number extraction ────────────────────────────────────────────

# "Глава първа. TITLE" → ordinal="първа", title="TITLE"
RE_CHAPTER_NUM = re.compile(
    r"^Глава\s+(.+?)\.\s*(.*)", re.IGNORECASE | re.DOTALL
)
# "Дял първи. TITLE"
RE_PARTITION_NUM = re.compile(
    r"^Дял\s+(.+?)\.\s*(.*)", re.IGNORECASE | re.DOTALL
)
# "Раздел I. TITLE" or "Раздел първи. TITLE"
RE_SECTION_NUM = re.compile(
    r"^Раздел\s+(.+?)\.\s*(.*)", re.IGNORECASE | re.DOTALL
)
# "Подраздел I. TITLE"
RE_SUBSECTION_NUM = re.compile(
    r"^Подраздел\s+(.+?)\.\s*(.*)", re.IGNORECASE | re.DOTALL
)
# "Част N. TITLE"
RE_PART_NUM = re.compile(
    r"^Част\s+(.+?)(?:\.\s*(.*))?$", re.IGNORECASE | re.DOTALL
)


def _extract_heading_item(
    text: str,
    pattern: re.Pattern,
) -> tuple[str | None, str]:
    """Extract the numeric item and remaining title from a structural heading.

    Returns (item_str_or_None, full_title_for_display).
    """
    m = pattern.match(text.strip())
    if not m:
        return None, text.strip()
    raw_ordinal = m.group(1).strip()
    rest = (m.group(2) or "").strip() if m.lastindex >= 2 else ""
    num = ordinal_to_number(raw_ordinal)
    if num is None:
        # Try as a plain digit
        if raw_ordinal.isdigit():
            num = raw_ordinal
        else:
            num = raw_ordinal  # keep as-is
    return num, text.strip()


# ══════════════════════════════════════════════════════════════════════════
# MarkdownParser
# ══════════════════════════════════════════════════════════════════════════


class MarkdownParser(BaseParser):
    """Parse a Markdown file and produce the unified JSON structure."""

    def __init__(self) -> None:
        self._lines: list[str] = []
        self._title: str = ""
        self._explanation: str = ""
        self._history: str = ""
        self._eu_legislation: str = ""

    # ── BaseParser interface ──────────────────────────────────────────────

    def parse(
        self,
        file_path: str | Path,
        source: str,
        doc_id: str,
    ) -> dict[str, Any]:
        """Override to inject eu_legislation discovered during tree parsing."""
        result = super().parse(file_path, source, doc_id)
        # eu_legislation is a comment at the end of the file, discovered
        # during _build_tree (after _extract_metadata already ran).
        if self._eu_legislation and "eu_legislation" not in result["metadata"]:
            result["metadata"]["eu_legislation"] = self._eu_legislation
        return result

    def _extract_metadata(self) -> dict[str, Any]:
        self._lines = self._file_path.read_text(encoding="utf-8").splitlines()
        self._parse_front_matter()
        meta: dict[str, Any] = {"title": self._title}
        if self._explanation:
            meta["explanation"] = self._explanation
        if self._history:
            meta["history"] = self._history
        return meta

    def _extract_document_tree(self) -> list[Node]:
        return self._build_tree()

    # ── front-matter parsing ──────────────────────────────────────────────

    def _parse_front_matter(self) -> None:
        """Extract title, explanation, history from the top of the file."""
        for line in self._lines:
            stripped = line.strip()
            if not stripped:
                continue

            m = RE_MD_TITLE.match(stripped)
            if m:
                self._title = m.group(1).strip()
                continue

            m = RE_EXPLANATION.match(stripped)
            if m:
                self._explanation = m.group(1).strip()
                continue

            m = RE_HISTORY.match(stripped)
            if m:
                self._history = m.group(1).strip()
                continue

            # Stop at first structural element
            if stripped.startswith("##"):
                break
            # Stop at first article line
            if RE_ARTICLE_NUM.match(stripped):
                break

    # ── tree building ─────────────────────────────────────────────────────

    def _build_tree(self) -> list[Node]:
        """Walk the Markdown lines and build the Node tree."""
        counter = ItemCounter()
        root_nodes: list[Node] = []

        # Stack for structural nesting: each entry is (node, rank_index)
        # rank_index = position in HIERARCHY_ORDER (0 = part … 6 = article)
        struct_stack: list[tuple[Node, int]] = []

        # Current provision node (provisions live at root level)
        current_provision: Node | None = None

        i = 0
        while i < len(self._lines):
            line = self._lines[i]
            stripped = line.strip()

            # Skip blank lines
            if not stripped:
                i += 1
                continue

            # ── metadata lines (already extracted) ──
            if RE_MD_TITLE.match(stripped):
                i += 1
                continue
            if RE_EXPLANATION.match(stripped):
                i += 1
                continue
            if RE_HISTORY.match(stripped):
                i += 1
                continue

            # ── EU legislation comment ──
            m = RE_EU_LEGISLATION.match(stripped)
            if m:
                self._eu_legislation = m.group(1).strip()
                i += 1
                continue

            # ── structural heading: PROVISION ──
            m = RE_MD_PROVISION.match(stripped)
            if m:
                title_text = normalize_structural_text(m.group(1))
                item = counter.next_item(NodeType.PROVISION)
                node = Node(
                    type=NodeType.PROVISION,
                    title=title_text,
                    item=item,
                )
                # If a PART is on the stack, provision is a child of that PART.
                # Pop down to the PART level (remove chapters/sections/etc.)
                while struct_stack and struct_stack[-1][1] > 0:
                    struct_stack.pop()

                if struct_stack and struct_stack[-1][1] == 0:
                    # Attach under the current PART
                    struct_stack[-1][0].children.append(node)
                else:
                    root_nodes.append(node)

                current_provision = node
                # Do NOT clear struct_stack — the PART context must persist
                # so subsequent chapters can still attach to it.
                i += 1
                continue

            # ── structural heading: PART ──
            m = RE_MD_PART.match(stripped)
            if m:
                raw_title = normalize_structural_text(m.group(1))
                item_num, title_text = _extract_heading_item(raw_title, RE_PART_NUM)
                if item_num is None:
                    item_num = counter.next_item(NodeType.PART)
                else:
                    counter._counters[NodeType.PART] = int(item_num)
                counter.reset_lower(NodeType.PART)
                node = Node(
                    type=NodeType.PART,
                    title=title_text,
                    item=str(item_num),
                )
                current_provision = None
                self._push_structural(node, 0, struct_stack, root_nodes, counter)
                i += 1
                continue

            # ── structural heading: CHAPTER ──
            m = RE_MD_CHAPTER.match(stripped)
            if m:
                current_provision = None
                raw_title = normalize_structural_text(m.group(1))
                item_num, title_text = _extract_heading_item(raw_title, RE_CHAPTER_NUM)
                if item_num is None:
                    item_num = counter.next_item(NodeType.CHAPTER)
                counter.reset_lower(NodeType.CHAPTER)
                node = Node(
                    type=NodeType.CHAPTER,
                    title=title_text,
                    item=str(item_num),
                )
                self._push_structural(node, 2, struct_stack, root_nodes, counter)
                i += 1
                continue

            # ── structural heading: PARTITION ──
            m = RE_MD_PARTITION.match(stripped)
            if m:
                current_provision = None
                raw_title = normalize_structural_text(m.group(1))
                item_num, title_text = _extract_heading_item(raw_title, RE_PARTITION_NUM)
                if item_num is None:
                    item_num = counter.next_item(NodeType.PARTITION)
                counter.reset_lower(NodeType.PARTITION)
                node = Node(
                    type=NodeType.PARTITION,
                    title=title_text,
                    item=str(item_num),
                )
                self._push_structural(node, 1, struct_stack, root_nodes, counter)
                i += 1
                continue

            # ── structural heading: SECTION ──
            m = RE_MD_SECTION.match(stripped)
            if m:
                current_provision = None
                raw_title = normalize_structural_text(m.group(1))
                item_num, title_text = _extract_heading_item(raw_title, RE_SECTION_NUM)
                if item_num is None:
                    item_num = counter.next_item(NodeType.SECTION)
                counter.reset_lower(NodeType.SECTION)
                node = Node(
                    type=NodeType.SECTION,
                    title=title_text,
                    item=str(item_num),
                )
                self._push_structural(node, 3, struct_stack, root_nodes, counter)
                i += 1
                continue

            # ── structural heading: SUBSECTION ──
            m = RE_MD_SUBSECTION.match(stripped)
            if m:
                current_provision = None
                raw_title = normalize_structural_text(m.group(1))
                item_num, title_text = _extract_heading_item(raw_title, RE_SUBSECTION_NUM)
                if item_num is None:
                    item_num = counter.next_item(NodeType.SUBSECTION)
                counter.reset_lower(NodeType.SUBSECTION)
                node = Node(
                    type=NodeType.SUBSECTION,
                    title=title_text,
                    item=str(item_num),
                )
                self._push_structural(node, 4, struct_stack, root_nodes, counter)
                i += 1
                continue

            # ── structural heading: HEADING ──
            m = RE_MD_HEADING.match(stripped)
            if m:
                current_provision = None
                raw_title = normalize_structural_text(m.group(1))
                item = counter.next_item(NodeType.HEADING)
                node = Node(
                    type=NodeType.HEADING,
                    title=raw_title,
                    item=item,
                )
                self._push_structural(node, 5, struct_stack, root_nodes, counter)
                i += 1
                continue

            # ── dots separator (skip in provisions) ──
            if RE_DOTS.match(stripped):
                i += 1
                continue

            # ── Clause (§ N.) — used inside provisions ──
            m = RE_CLAUSE.match(stripped)
            if m:
                clause_item = m.group(1)
                clause_rest = m.group(2).strip()
                i = self._parse_clause(
                    clause_item, clause_rest, i,
                    current_provision, struct_stack, root_nodes, counter,
                )
                continue

            # ── Article (Чл. N.) ──
            m = RE_ARTICLE_NUM.match(stripped)
            if m:
                art_item = m.group(1)
                art_rest = m.group(2).strip()
                i = self._parse_article(
                    art_item, art_rest, i,
                    current_provision, struct_stack, root_nodes, counter,
                )
                continue

            # ── standalone paragraph outside article (provision body text) ──
            m = RE_PARAGRAPH.match(stripped)
            if m and current_provision is not None:
                # Provision may have standalone paragraphs (e.g. "(ОБН. - ДВ, ...)")
                # Treat as plain content of the provision
                if current_provision.content:
                    current_provision.content += "\n" + stripped
                else:
                    current_provision.content = stripped
                i += 1
                continue

            # ── plain text line (e.g. provision body, un-numbered content) ──
            if current_provision is not None and not struct_stack:
                # Attach to the current provision as content
                if current_provision.content:
                    current_provision.content += "\n" + stripped
                else:
                    current_provision.content = stripped
            i += 1

        return root_nodes

    # ── structural stack management ───────────────────────────────────────

    @staticmethod
    def _push_structural(
        node: Node,
        rank: int,
        stack: list[tuple[Node, int]],
        fallback_list: list[Node],
        counter: ItemCounter,
    ) -> None:
        """Push *node* onto the structural stack, attaching it to the right parent.

        Pops anything from the stack that is at the same rank or lower
        (higher number), then attaches *node* as a child of the new top
        of stack (or *fallback_list* if the stack is empty).
        """
        # Pop nodes of equal or lower rank
        while stack and stack[-1][1] >= rank:
            stack.pop()

        if stack:
            stack[-1][0].children.append(node)
        else:
            fallback_list.append(node)

        stack.append((node, rank))

    # ── article parsing ───────────────────────────────────────────────────

    def _parse_article(
        self,
        art_item: str,
        first_line_rest: str,
        start_idx: int,
        current_provision: Node | None,
        struct_stack: list[tuple[Node, int]],
        root_nodes: list[Node],
        counter: ItemCounter,
    ) -> int:
        """Parse an article and all its internal elements.

        Returns the index of the next line to process.
        """
        art_node = Node(
            type=NodeType.ARTICLE,
            item=art_item,
            title=f"Чл. {art_item}",
        )

        # Attach to parent structural node or root
        if struct_stack:
            struct_stack[-1][0].children.append(art_node)
        elif current_provision is not None:
            current_provision.children.append(art_node)
        else:
            root_nodes.append(art_node)

        # The rest of the first line may contain an inline paragraph:
        # "Чл. 1. (1) Some text" → paragraph (1) with content "Some text"
        # or just article text without paragraph:
        # "Чл. 94. Разпоредбите ..." → article content
        remaining = first_line_rest

        # Collect all lines belonging to this article (until next structural
        # element, next article, or a blank-line-separated boundary)
        art_lines: list[str] = []
        if remaining:
            art_lines.append(remaining)

        i = start_idx + 1
        while i < len(self._lines):
            line = self._lines[i]
            stripped = line.strip()

            # Stop conditions
            if not stripped:
                # Blank line — peek ahead: if next non-blank is structural or
                # a new article, stop; otherwise consume
                j = i + 1
                while j < len(self._lines) and not self._lines[j].strip():
                    j += 1
                if j >= len(self._lines):
                    break
                next_stripped = self._lines[j].strip()
                if (next_stripped.startswith("##") or
                        RE_ARTICLE_NUM.match(next_stripped) or
                        RE_CLAUSE.match(next_stripped) or
                        RE_EU_LEGISLATION.match(next_stripped) or
                        RE_DOTS.match(next_stripped)):
                    break
                # Otherwise it's a continuation (multi-paragraph article)
                i += 1
                continue

            # Structural markers end the article
            if (stripped.startswith("##") or
                    RE_EU_LEGISLATION.match(stripped) or
                    RE_DOTS.match(stripped)):
                break

            # A new article or clause ends this one
            if RE_ARTICLE_NUM.match(stripped) or RE_CLAUSE.match(stripped):
                break

            art_lines.append(stripped)
            i += 1

        # Now parse the article's internal structure
        self._parse_article_internals(art_node, art_lines, counter)

        return i

    def _parse_article_internals(
        self,
        art_node: Node,
        lines: list[str],
        counter: ItemCounter,
    ) -> None:
        """Parse paragraphs, points, letters etc. inside an article."""
        if not lines:
            return

        internal_counter = ItemCounter()

        # Check if the article uses numbered paragraphs
        has_paragraphs = any(RE_PARAGRAPH.match(l) for l in lines)

        if not has_paragraphs:
            # Simple article — all content, no internal sub-elements
            # But it may still have points (N.) and letters (а))
            content_lines: list[str] = []
            child_lines: list[str] = []
            for l in lines:
                if (RE_POINT.match(l) or RE_LETTER.match(l) or
                        RE_LATIN_LETTER.match(l)):
                    child_lines.append(l)
                else:
                    if child_lines:
                        # Process accumulated child lines under a virtual paragraph
                        child_lines.append(l)
                    else:
                        content_lines.append(l)

            if content_lines and not child_lines:
                art_node.content = "\n".join(content_lines)
            elif content_lines or child_lines:
                all_lines = content_lines + child_lines
                self._parse_flat_elements(art_node, all_lines, internal_counter)
        else:
            # Article with numbered paragraphs
            self._parse_paragraphed(art_node, lines, internal_counter)

    def _parse_paragraphed(
        self,
        parent: Node,
        lines: list[str],
        counter: ItemCounter,
    ) -> None:
        """Parse lines that contain (N) paragraph markers."""
        current_para: Node | None = None
        current_para_lines: list[str] = []

        def flush_para():
            nonlocal current_para, current_para_lines
            if current_para is not None:
                self._parse_flat_elements(
                    current_para, current_para_lines, ItemCounter()
                )
                current_para_lines = []

        for line in lines:
            m = RE_PARAGRAPH.match(line)
            if m:
                flush_para()
                para_item = m.group(1)
                para_rest = m.group(2).strip()
                current_para = Node(
                    type=NodeType.PARAGRAPH,
                    item=para_item,
                )
                parent.children.append(current_para)
                if para_rest:
                    current_para_lines.append(para_rest)
            elif current_para is not None:
                current_para_lines.append(line)
            else:
                # Lines before first paragraph — treat as article content
                if parent.content:
                    parent.content += "\n" + line
                else:
                    parent.content = line

        flush_para()

    def _parse_flat_elements(
        self,
        parent: Node,
        lines: list[str],
        counter: ItemCounter,
    ) -> None:
        """Parse points, sub-points, letters etc. from flat lines.

        Builds a nested hierarchy: point → letter → double_letter, etc.
        """
        content_parts: list[str] = []
        current_point: Node | None = None
        current_letter: Node | None = None

        def flush_content():
            nonlocal content_parts
            if content_parts:
                parent.content = "\n".join(content_parts)
                content_parts = []

        for line in lines:
            # ── sub_subpoint (1.1.1.) ──
            m = RE_SUB_SUBPOINT.match(line)
            if m:
                flush_content()
                node = Node(
                    type=NodeType.SUB_SUBPOINT,
                    item=m.group(1),
                    content=m.group(2).strip() or None,
                )
                target = current_letter or current_point or parent
                target.children.append(node)
                continue

            # ── subpoint (1.1.) ──
            m = RE_SUBPOINT.match(line)
            if m:
                flush_content()
                node = Node(
                    type=NodeType.SUBPOINT,
                    item=m.group(1),
                    content=m.group(2).strip() or None,
                )
                target = current_point or parent
                target.children.append(node)
                continue

            # ── point (N.) ──
            m = RE_POINT.match(line)
            if m:
                flush_content()
                current_letter = None
                current_point = Node(
                    type=NodeType.POINT,
                    item=m.group(1),
                    content=m.group(2).strip() or None,
                )
                parent.children.append(current_point)
                continue

            # ── triple letter (ааа)) ──
            m = RE_TRIPLE_LETTER.match(line)
            if m:
                flush_content()
                node = Node(
                    type=NodeType.TRIPLE_LETTER,
                    item=m.group(1),
                    content=m.group(2).strip() or None,
                )
                target = current_letter or current_point or parent
                target.children.append(node)
                continue

            # ── double letter (аа)) ──
            m = RE_DOUBLE_LETTER.match(line)
            if m:
                flush_content()
                node = Node(
                    type=NodeType.DOUBLE_LETTER,
                    item=m.group(1),
                    content=m.group(2).strip() or None,
                )
                target = current_point or parent
                target.children.append(node)
                current_letter = node
                continue

            # ── letter (а)) ──
            m = RE_LETTER.match(line)
            if m:
                flush_content()
                current_letter = Node(
                    type=NodeType.LETTER,
                    item=m.group(1),
                    content=m.group(2).strip() or None,
                )
                target = current_point or parent
                target.children.append(current_letter)
                continue

            # ── latin letter (a)) ──
            m = RE_LATIN_LETTER.match(line)
            if m:
                flush_content()
                node = Node(
                    type=NodeType.LATIN_LETTER,
                    item=m.group(1),
                    content=m.group(2).strip() or None,
                )
                target = current_point or parent
                target.children.append(node)
                continue

            # ── plain content line ──
            if current_point is not None or current_letter is not None:
                # Continuation of the last point/letter
                target_node = current_letter or current_point
                if target_node.content:
                    target_node.content += " " + line
                else:
                    target_node.content = line
            else:
                content_parts.append(line)

        flush_content()

    # ── clause parsing ────────────────────────────────────────────────────

    def _parse_clause(
        self,
        clause_item: str,
        first_line_rest: str,
        start_idx: int,
        current_provision: Node | None,
        struct_stack: list[tuple[Node, int]],
        root_nodes: list[Node],
        counter: ItemCounter,
    ) -> int:
        """Parse a provision clause (§ N.) and its internal elements.

        Returns the index of the next line to process.
        """
        clause_node = Node(
            type=NodeType.CLAUSE,
            item=clause_item,
            title=f"§ {clause_item}",
        )

        # Attach to provision or root
        if current_provision is not None:
            current_provision.children.append(clause_node)
        elif struct_stack:
            struct_stack[-1][0].children.append(clause_node)
        else:
            root_nodes.append(clause_node)

        # Collect lines belonging to this clause
        clause_lines: list[str] = []
        if first_line_rest:
            clause_lines.append(first_line_rest)

        i = start_idx + 1
        while i < len(self._lines):
            line = self._lines[i]
            stripped = line.strip()

            if not stripped:
                j = i + 1
                while j < len(self._lines) and not self._lines[j].strip():
                    j += 1
                if j >= len(self._lines):
                    break
                next_stripped = self._lines[j].strip()
                if (next_stripped.startswith("##") or
                        RE_ARTICLE_NUM.match(next_stripped) or
                        RE_CLAUSE.match(next_stripped) or
                        RE_EU_LEGISLATION.match(next_stripped) or
                        RE_DOTS.match(next_stripped)):
                    break
                i += 1
                continue

            if (stripped.startswith("##") or
                    RE_EU_LEGISLATION.match(stripped) or
                    RE_DOTS.match(stripped)):
                break

            if RE_ARTICLE_NUM.match(stripped) or RE_CLAUSE.match(stripped):
                break

            clause_lines.append(stripped)
            i += 1

        # Parse clause internals (same structure as article)
        self._parse_article_internals(clause_node, clause_lines, counter)

        return i


# ══════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════


def main() -> None:
    import argparse
    import sys

    ap = argparse.ArgumentParser(
        description="Parse a Markdown file into the unified legal-document JSON."
    )
    ap.add_argument("file", help="Path to the Markdown file.")
    ap.add_argument("-o", "--output", help="Output JSON file (default: stdout).")
    ap.add_argument(
        "-s", "--source", default="lex.bg",
        help="Document source identifier (default: lex.bg).",
    )
    ap.add_argument(
        "-d", "--doc-id", default="",
        help="Document id (default: derived from title).",
    )
    args = ap.parse_args()

    parser = MarkdownParser()
    result = parser.parse(args.file, source=args.source, doc_id=args.doc_id or "")

    # If doc_id was not given, derive from title
    if not args.doc_id:
        title = result.get("metadata", {}).get("title", "")
        # Use acronym or short form
        doc_id = _derive_doc_id(title)
        # Re-generate UIDs with the proper doc_id
        tree = parser._extract_document_tree()
        generate_uids(tree, args.source, doc_id)
        result["document"] = [n.to_dict() for n in tree]
        result["table_of_contents"] = parser._generate_toc(tree)

    out = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"→ {args.output}", file=sys.stderr)
    else:
        print(out)


def _derive_doc_id(title: str) -> str:
    """Derive a short doc_id from the document title.

    'НАКАЗАТЕЛЕН КОДЕКС' → 'НК'
    'ЗАКОН ЗА ЗАДЪЛЖЕНИЯТА И ДОГОВОРИТЕ' → 'ЗЗД'
    """
    stop_words = {"НА", "ЗА", "И", "В", "ОТ", "ПО", "СЪС", "КЪМ", "ПРИ"}
    words = title.upper().split()
    initials = [w[0] for w in words if w not in stop_words and w[0].isalpha()]
    return "".join(initials) if initials else title[:10]


if __name__ == "__main__":
    main()

"""
HTML parser for Bulgarian legal documents scraped from lex.bg.

Implements ``BaseParser`` to convert flat HTML into the unified hierarchical
JSON format.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag, NavigableString

from base_parser import BaseParser
from config import (
    CSS_CLASS_MAP,
    HIERARCHY_ORDER,
    ARTICLE_INTERNAL_HIERARCHY,
    NodeType,
    ordinal_to_number,
    normalize_structural_text,
)
from models import Node, ItemCounter


# ── regex patterns for article-internal parsing ───────────────────────────

# Matches article number: "Чл. 1.", "Чл. 12а.", "Чл. 213б."
RE_ARTICLE_NUM = re.compile(r"^Чл\.\s*(\d+[а-я]?)\.")

# Matches paragraph (алинея): "(1) ...", "(2) ..."
RE_PARAGRAPH = re.compile(r"^\((\d+)\)\s*")

# Matches point (точка): "1. ...", "1а. ...", "12б. ..."
RE_POINT = re.compile(r"^(\d+[а-я]?)\.\s+")

# Matches subpoint: "1.1. ...", "2.3. ..."
RE_SUBPOINT = re.compile(r"^(\d+\.\d+)\.\s+")

# Matches sub_subpoint: "1.1.1. ..."
RE_SUB_SUBPOINT = re.compile(r"^(\d+\.\d+\.\d+)\.\s+")

# Matches triple letter (ааа), ббб)) — checked before double and single
RE_TRIPLE_LETTER = re.compile(r"^([а-я]{3})\)\s*")

# Matches double letter (аа), бб))
RE_DOUBLE_LETTER = re.compile(r"^([а-я]{2})\)\s*")

# Matches single Cyrillic letter (а), б), в))
RE_LETTER = re.compile(r"^([а-я])\)\s*")

# Matches Latin letter (a), b), c))
RE_LATIN_LETTER = re.compile(r"^([a-z])\)\s*")

# Matches clause (§): "§ 1.", "§ 2а."
RE_CLAUSE = re.compile(r"^§\s*(\d+[а-я]?)\.")

# Matches repealed marker: "(Отм. - ДВ, ...)" anywhere in text
RE_REPEALED = re.compile(r"\(Отм\.\s*-\s*ДВ")

# Matches "Глава" prefix in heading text
RE_CHAPTER = re.compile(r"^Глава\s+", re.IGNORECASE)

# Matches "Дял" prefix in heading text
RE_PARTITION = re.compile(r"^Дял\s+", re.IGNORECASE)

# Matches "Раздел" prefix in section/heading text
RE_SECTION_PREFIX = re.compile(r"^Раздел\s+", re.IGNORECASE)

# Matches "Подраздел" prefix
RE_SUBSECTION = re.compile(r"^Подраздел\s+", re.IGNORECASE)


# ── helpers ───────────────────────────────────────────────────────────────


def _get_text(el: Tag) -> str:
    """Extract cleaned text from an element (strip, collapse whitespace)."""
    text = el.get_text(separator=" ", strip=True)
    # Collapse multiple whitespace (but keep single newlines for readability)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _get_inner_html(el: Tag) -> str:
    """Return the inner HTML of an element as a string."""
    return "".join(str(c) for c in el.children).strip()


def _is_noise_div(el: Tag) -> bool:
    """Check if a <div> is noise (buttons placeholder, empty spacer, etc.)."""
    el_id = el.get("id", "")
    if isinstance(el_id, str) and el_id.startswith("buttons_"):
        return True
    # Empty spacer divs (margin-top/margin-bottom with no text)
    style = el.get("style", "")
    if isinstance(style, str) and "margin-top" in style and not _get_text(el):
        return True
    return False


def _extract_item_from_text(text: str, node_type: str) -> tuple[str | None, str, str]:
    """Try to extract an item number from the beginning of *text*.

    Returns ``(item, remaining_title, remaining_content)`` where:
    * *item* is the extracted number/identifier or ``None``
    * *remaining_title* is the title portion (for structural elements)
    * *remaining_content* is the content text (after the item marker)
    """
    # Map node types to their prefix regex
    _PREFIX_MAP = {
        NodeType.CHAPTER: RE_CHAPTER,
        NodeType.PARTITION: RE_PARTITION,
        NodeType.SECTION: RE_SECTION_PREFIX,
        NodeType.SUBSECTION: RE_SUBSECTION,
    }
    regex = _PREFIX_MAP.get(node_type)
    if regex is not None:
        m = regex.match(text)
        if m:
            rest = text[m.end():]
            parts = re.split(r"\.\s*", rest, maxsplit=1)
            raw_ordinal = parts[0].strip() if parts else rest.strip()
            title = parts[1].strip() if len(parts) > 1 else None
            # Convert ordinal word / Roman numeral → Arabic number
            numeric = ordinal_to_number(raw_ordinal)
            item = numeric if numeric else raw_ordinal
            if title:
                title = normalize_structural_text(title)
            return item, title or "", ""

    return None, text, ""


# ── HtmlParser ────────────────────────────────────────────────────────────


class HtmlParser(BaseParser):
    """Parser for lex.bg HTML documents."""

    def __init__(self) -> None:
        super().__init__()
        self._soup: BeautifulSoup | None = None
        self._counter = ItemCounter()

    # ── BaseParser interface ──

    def _extract_metadata(self) -> dict[str, Any]:
        assert self._soup is not None
        meta: dict[str, Any] = {
            "source": self._source,
            "doc_id": self._doc_id,
        }

        title_div = self._soup.find("div", class_="TitleDocument")
        if title_div:
            meta["title"] = _get_text(title_div)

        pre_hist = self._soup.find("div", class_="PreHistory")
        if pre_hist:
            meta["effective_date"] = _get_text(pre_hist)

        hist_div = self._soup.find("div", class_="HistoryOfDocument")
        if hist_div:
            meta["amendments"] = _get_text(hist_div)

        return meta

    def _extract_document_tree(self) -> list[Node]:
        assert self._soup is not None
        self._counter = ItemCounter()

        # Gather top-level content divs (children of .boxi.boxinb or .content)
        container = (
            self._soup.find("div", class_="boxi")
            or self._soup.find("div", class_="content")
            or self._soup.body
        )
        if container is None:
            return []

        top_divs: list[Tag] = []
        for child in container.children:
            if isinstance(child, Tag) and child.name == "div":
                top_divs.append(child)

        # Phase 1: Convert flat divs to flat list of Nodes
        flat_nodes: list[Node] = []
        for div in top_divs:
            node = self._div_to_node(div)
            if node is not None:
                flat_nodes.append(node)

        # Phase 2: Reconstruct hierarchy using stack
        tree = self._build_hierarchy(flat_nodes)

        return tree

    # ── override parse to read the HTML first ──

    def parse(
        self,
        file_path: str | Path,
        source: str,
        doc_id: str,
    ) -> dict[str, Any]:
        path = Path(file_path)
        html = path.read_text(encoding="utf-8")
        self._soup = BeautifulSoup(html, "lxml")
        return super().parse(file_path, source, doc_id)

    # ── Phase 1: flat div → Node conversion ──

    def _div_to_node(self, div: Tag) -> Node | None:
        """Convert a single top-level ``<div>`` to a ``Node``, or ``None``
        if the div is noise / metadata (already extracted separately)."""
        css_class = self._get_css_class(div)

        # Skip metadata divs (handled in _extract_metadata)
        if css_class in ("TitleDocument", "PreHistory", "HistoryOfDocument"):
            return None

        # Skip buttons-noise divs
        if _is_noise_div(div):
            return None

        mapped = CSS_CLASS_MAP.get(css_class)
        if mapped is None:
            # Unknown class — skip
            return None

        # --- Heading (ambiguous: part / partition / chapter / provision) ---
        if mapped == "_heading_ambiguous":
            return self._parse_heading(div)

        # --- Section ---
        if mapped == NodeType.SECTION:
            return self._parse_section(div)

        # --- Article ---
        if mapped == NodeType.ARTICLE:
            return self._parse_article(div)

        # --- Provision (TransitionalFinalEdicts / FinalEdicts / AdditionalEdicts) ---
        if mapped == NodeType.PROVISION:
            return self._parse_provision(div)

        # --- Clause (FinalEdictsArticle → § paragraph) ---
        if mapped == NodeType.CLAUSE:
            return self._parse_clause_div(div)

        return None

    # ── Heading resolution ──

    def _parse_heading(self, div: Tag) -> Node:
        """Resolve a ``class="Heading"`` div into ``part``, ``partition``,
        ``chapter``, ``provision``, or generic ``heading``."""
        text = _get_text(div)

        # "Допълнителна разпоредба" / "Допълнителни разпоредби" / "Особена разпоредба"
        lower = text.lower()
        if ("допълнителн" in lower and "разпоредб" in lower) or \
           ("особена" in lower and "разпоредб" in lower):
            self._counter.reset_lower(NodeType.PROVISION)
            item = self._counter.next_item(NodeType.PROVISION)
            return Node(
                type=NodeType.PROVISION,
                title=text,
                item=item,
                embed_at_this_level=False,
            )

        # "Преходни разпоредби" / "Преходни и Заключителни разпоредби"
        if "преходн" in lower and "разпоредб" in lower:
            self._counter.reset_lower(NodeType.PROVISION)
            item = self._counter.next_item(NodeType.PROVISION)
            return Node(
                type=NodeType.PROVISION,
                title=text,
                item=item,
                embed_at_this_level=False,
            )

        # "Дял ..." → partition
        if RE_PARTITION.match(text):
            node_type = NodeType.PARTITION
            extracted_item, title, _ = _extract_item_from_text(text, node_type)
            self._counter.reset_lower(node_type)
            item = extracted_item or self._counter.next_item(node_type)
            return Node(
                type=node_type,
                title=title or text,
                item=item,
                embed_at_this_level=False,
            )

        # "Глава ..." → chapter
        if RE_CHAPTER.match(text):
            node_type = NodeType.CHAPTER
            extracted_item, title, _ = _extract_item_from_text(text, node_type)
            self._counter.reset_lower(node_type)
            item = extracted_item or self._counter.next_item(node_type)
            return Node(
                type=node_type,
                title=title or text,
                item=item,
                embed_at_this_level=False,
            )

        # ALL-CAPS text without "Глава"/"Дял"/"Раздел" → part
        # Heuristic: mostly uppercase (Cyrillic)
        alpha_chars = [c for c in text if c.isalpha()]
        if alpha_chars and sum(1 for c in alpha_chars if c.isupper()) / len(alpha_chars) > 0.7:
            node_type = NodeType.PART
            self._counter.reset_lower(node_type)
            item = self._counter.next_item(node_type)
            return Node(
                type=node_type,
                title=text,
                item=item,
                embed_at_this_level=False,
            )

        # Fallback: generic heading
        item = self._counter.next_item(NodeType.HEADING)
        return Node(
            type=NodeType.HEADING,
            title=text,
            item=item,
            embed_at_this_level=False,
        )

    # ── Section ──

    def _parse_section(self, div: Tag) -> Node:
        text = _get_text(div)
        node_type = NodeType.SECTION

        # Check for subsection
        if RE_SUBSECTION.match(text):
            node_type = NodeType.SUBSECTION

        extracted_item, title, _ = _extract_item_from_text(text, node_type)
        self._counter.reset_lower(node_type)
        item = extracted_item or self._counter.next_item(node_type)

        return Node(
            type=node_type,
            title=title or text,
            item=item,
            embed_at_this_level=False,
        )

    # ── Article ──

    def _parse_article(self, div: Tag) -> Node:
        """Parse an ``<div class="Article">`` including its internal children
        (paragraphs, points, letters)."""
        # Get all content divs inside <p class="buttons"> or direct children
        content_divs = self._get_article_content_divs(div)

        # Check for EU legislation block (special case)
        title_p = div.find("p", class_="Title")
        if title_p and "Релевантни актове" in _get_text(title_p):
            return self._parse_eu_legislation(div)

        if not content_divs:
            item = self._counter.next_item(NodeType.ARTICLE)
            return Node(
                type=NodeType.ARTICLE,
                item=item,
                embed_at_this_level=False,
            )

        # Extract article number from first div
        first_text = _get_text(content_divs[0])
        first_html = _get_inner_html(content_divs[0])
        m = RE_ARTICLE_NUM.match(first_text)

        if m:
            art_num = m.group(1)
            self._counter.reset_lower(NodeType.ARTICLE)
        else:
            art_num = self._counter.next_item(NodeType.ARTICLE)

        # Check if entire article is repealed
        is_repealed = False
        if len(content_divs) == 1 and RE_REPEALED.search(first_text):
            is_repealed = True

        # Build article node
        article_node = Node(
            type=NodeType.ARTICLE,
            title=f"Чл. {art_num}.",
            item=art_num,
            embed_at_this_level=False,
        )
        if is_repealed:
            # Extract the full text as content for repealed articles
            content_after_num = first_text[m.end():].strip() if m else first_text
            article_node.content = content_after_num
            article_node.html_content = first_html
            article_node.embed_at_this_level = bool(content_after_num)
            article_node.metadata = {"repealed": True}
            return article_node

        # Parse internal structure (paragraphs, points, letters)
        children = self._parse_article_internals(content_divs, art_num)
        article_node.children = children

        # If article has no children but has text after "Чл. N." (no paragraph numbers)
        if not children and content_divs:
            combined_text = " ".join(_get_text(d) for d in content_divs)
            content_after_num = combined_text
            if m:
                content_after_num = combined_text[m.end():].strip()
            article_node.content = content_after_num
            combined_html = "".join(_get_inner_html(d) for d in content_divs)
            article_node.html_content = combined_html
            article_node.embed_at_this_level = bool(content_after_num)

        return article_node

    def _get_article_content_divs(self, article_div: Tag) -> list[Tag]:
        """Extract the actual content ``<div>``s from an Article element,
        skipping ``<p class="buttons">``, comments, ``<br>``, spacers."""
        divs: list[Tag] = []

        # Try inside <p class="buttons"> first (regular Article)
        p_buttons = article_div.find("p", class_="buttons")
        container = p_buttons if p_buttons else article_div

        for child in container.children:
            if isinstance(child, Tag):
                if child.name == "div" and not _is_noise_div(child):
                    text = _get_text(child)
                    if text:  # skip empty divs
                        divs.append(child)
                # Skip <br>, <p class="Title">, comments, etc.
        return divs

    def _parse_article_internals(
        self, content_divs: list[Tag], art_num: str
    ) -> list[Node]:
        """Parse the flat list of ``<div>``s inside an article into a
        hierarchical tree of paragraphs, points, letters."""
        flat: list[Node] = []
        counter = ItemCounter()
        first_div = True

        for div in content_divs:
            text = _get_text(div)
            html = _get_inner_html(div)

            if first_div:
                first_div = False
                # Strip "Чл. N." prefix from first div
                m = RE_ARTICLE_NUM.match(text)
                if m:
                    text = text[m.end():].strip()
                    # Also strip the <b>Чл. N.</b> from html
                    b_tag = div.find("b")
                    if b_tag:
                        b_tag.decompose()
                    html = _get_inner_html(div).strip()

            node = self._classify_article_child(text, html, counter)
            if node:
                flat.append(node)

        # Build internal hierarchy
        return self._build_article_hierarchy(flat)

    def _classify_article_child(
        self, text: str, html: str, counter: ItemCounter
    ) -> Node | None:
        """Classify a single text block inside an article."""
        if not text:
            return None

        is_repealed = bool(RE_REPEALED.search(text))

        # Check sub_subpoint first (1.1.1.)
        m = RE_SUB_SUBPOINT.match(text)
        if m:
            item = m.group(1)
            content = text[m.end():].strip()
            node = Node(
                type=NodeType.SUB_SUBPOINT, item=item,
                content=content, html_content=html,
                embed_at_this_level=bool(content),
            )
            if is_repealed:
                node.metadata = {"repealed": True}
            return node

        # Check subpoint (1.1.)
        m = RE_SUBPOINT.match(text)
        if m:
            item = m.group(1)
            content = text[m.end():].strip()
            node = Node(
                type=NodeType.SUBPOINT, item=item,
                content=content, html_content=html,
                embed_at_this_level=bool(content),
            )
            if is_repealed:
                node.metadata = {"repealed": True}
            return node

        # Check paragraph (алинея): (N)
        m = RE_PARAGRAPH.match(text)
        if m:
            item = m.group(1)
            counter.reset_lower(NodeType.PARAGRAPH)
            content = text[m.end():].strip()
            node = Node(
                type=NodeType.PARAGRAPH, item=item,
                content=content, html_content=html,
                embed_at_this_level=bool(content),
            )
            if is_repealed:
                node.metadata = {"repealed": True}
            return node

        # Check point (точка): N. or Nа.
        m = RE_POINT.match(text)
        if m:
            item = m.group(1)
            counter.reset_lower(NodeType.POINT)
            content = text[m.end():].strip()
            node = Node(
                type=NodeType.POINT, item=item,
                content=content, html_content=html,
                embed_at_this_level=bool(content),
            )
            if is_repealed:
                node.metadata = {"repealed": True}
            return node

        # Check triple letter
        m = RE_TRIPLE_LETTER.match(text)
        if m:
            item = m.group(1)
            content = text[m.end():].strip()
            return Node(
                type=NodeType.TRIPLE_LETTER, item=item,
                content=content, html_content=html,
                embed_at_this_level=bool(content),
            )

        # Check double letter
        m = RE_DOUBLE_LETTER.match(text)
        if m:
            item = m.group(1)
            content = text[m.end():].strip()
            return Node(
                type=NodeType.DOUBLE_LETTER, item=item,
                content=content, html_content=html,
                embed_at_this_level=bool(content),
            )

        # Check single Cyrillic letter
        m = RE_LETTER.match(text)
        if m:
            item = m.group(1)
            content = text[m.end():].strip()
            return Node(
                type=NodeType.LETTER, item=item,
                content=content, html_content=html,
                embed_at_this_level=bool(content),
            )

        # Check Latin letter
        m = RE_LATIN_LETTER.match(text)
        if m:
            item = m.group(1)
            content = text[m.end():].strip()
            return Node(
                type=NodeType.LATIN_LETTER, item=item,
                content=content, html_content=html,
                embed_at_this_level=bool(content),
            )

        # Fallback: treat as text content (part of parent)
        # This handles article text without paragraph markers
        # Return None — caller should merge this into parent
        return None

    # ── article-internal hierarchy builder ──

    def _build_article_hierarchy(self, flat: list[Node]) -> list[Node]:
        """Nest flat article-internal nodes into a proper tree.

        Strategy: iterate left-to-right, maintain a stack.
        Points nest under the most recent paragraph, letters under
        the most recent point, etc.
        """
        if not flat:
            return []

        root: list[Node] = []
        stack: list[Node] = []  # current nesting path

        for node in flat:
            level = self._internal_level(node.type)

            # Pop stack until we find a suitable parent
            while stack and self._internal_level(stack[-1].type) >= level:
                stack.pop()

            if stack:
                stack[-1].children.append(node)
            else:
                root.append(node)

            stack.append(node)

        return root

    @staticmethod
    def _internal_level(node_type: str) -> int:
        """Return the nesting depth for an article-internal type.

        Lower number = higher rank (paragraph is above point, etc.)."""
        order = ARTICLE_INTERNAL_HIERARCHY
        if node_type in order:
            return order.index(node_type)
        return len(order)  # unknown types go to the bottom

    # ── Provision ──

    def _parse_provision(self, div: Tag) -> Node:
        """Parse TransitionalFinalEdicts / FinalEdicts / AdditionalEdicts."""
        text = _get_text(div)
        self._counter.reset_lower(NodeType.PROVISION)
        item = self._counter.next_item(NodeType.PROVISION)

        return Node(
            type=NodeType.PROVISION,
            title=text,
            item=item,
            embed_at_this_level=False,
        )

    # ── Clause (FinalEdictsArticle) ──

    def _parse_clause_div(self, div: Tag) -> Node:
        """Parse a ``<div class="FinalEdictsArticle">`` as a clause (§)."""
        content_divs: list[Tag] = []
        for child in div.children:
            if isinstance(child, Tag) and child.name == "div":
                text = _get_text(child)
                if text:
                    content_divs.append(child)

        if not content_divs:
            item = self._counter.next_item(NodeType.CLAUSE)
            return Node(type=NodeType.CLAUSE, item=item, embed_at_this_level=False)

        # Check first div for § number or history line
        first_text = _get_text(content_divs[0])
        first_html = _get_inner_html(content_divs[0])

        # Some FinalEdictsArticles start with (ОБН. - ДВ, ...) line
        history_text = None
        start_idx = 0
        if first_text.startswith("(ОБН") or first_text.startswith("(Обн"):
            history_text = first_text
            start_idx = 1
            if len(content_divs) > 1:
                first_text = _get_text(content_divs[1])
                first_html = _get_inner_html(content_divs[1])

        m = RE_CLAUSE.match(first_text)
        if m:
            clause_num = m.group(1)
        else:
            clause_num = self._counter.next_item(NodeType.CLAUSE)

        # Combine remaining text as content
        remaining_divs = content_divs[start_idx:]
        content_parts = []
        html_parts = []
        for d in remaining_divs:
            t = _get_text(d)
            # Strip "§ N." prefix from first content div
            if d == remaining_divs[0] and m:
                t = t[m.end():].strip()
            content_parts.append(t)
            html_parts.append(_get_inner_html(d))

        content = " ".join(content_parts).strip()
        html_content = " ".join(html_parts).strip()

        node = Node(
            type=NodeType.CLAUSE,
            title=f"§ {clause_num}.",
            item=str(clause_num),
            content=content or None,
            html_content=html_content or None,
            embed_at_this_level=bool(content),
        )

        if history_text:
            node.metadata = {"provision_history": history_text}

        return node

    # ── EU legislation ──

    def _parse_eu_legislation(self, div: Tag) -> Node:
        """Parse the EU legislation references block."""
        text = _get_text(div)
        item = self._counter.next_item(NodeType.EU_LEGISLATION_RELEVANCE)

        return Node(
            type=NodeType.EU_LEGISLATION_RELEVANCE,
            title="Релевантни актове от Европейското законодателство",
            item=item,
            content=text,
            html_content=_get_inner_html(div),
            embed_at_this_level=bool(text),
        )

    # ── Phase 2: hierarchy reconstruction from flat list ──

    def _build_hierarchy(self, flat_nodes: list[Node]) -> list[Node]:
        """Reconstruct the document hierarchy from a flat list of nodes
        using a stack-based algorithm.

        Structural elements (part → partition → chapter → section →
        subsection → heading → article) nest naturally.
        Provisions and their clauses also nest properly.
        """
        root: list[Node] = []
        # Stack entries: (node, rank) where rank comes from HIERARCHY_ORDER
        stack: list[tuple[Node, int]] = []

        # Track whether we're inside a provision section
        current_provision: Node | None = None

        for node in flat_nodes:
            rank = self._structural_rank(node.type)

            # Special handling for provisions and clauses
            if node.type == NodeType.PROVISION:
                # Provisions are top-level (or under the root)
                # First, close any previous provision
                current_provision = node
                # Pop stack entirely for provisions (they're top-level sections)
                stack.clear()
                root.append(node)
                stack.append((node, rank))
                continue

            if node.type == NodeType.CLAUSE:
                # Clauses go under the current provision
                if current_provision is not None:
                    current_provision.children.append(node)
                else:
                    root.append(node)
                continue

            if node.type == NodeType.EU_LEGISLATION_RELEVANCE:
                root.append(node)
                continue

            # For structural elements: pop stack until we find a parent with
            # a lower rank (= higher in hierarchy)
            while stack and stack[-1][1] >= rank:
                stack.pop()

            if stack:
                stack[-1][0].children.append(node)
            else:
                root.append(node)
                current_provision = None  # Reset provision context

            stack.append((node, rank))

        return root

    @staticmethod
    def _structural_rank(node_type: str) -> int:
        """Return the rank for a structural type (lower = higher level)."""
        if node_type in HIERARCHY_ORDER:
            return HIERARCHY_ORDER.index(node_type)
        return len(HIERARCHY_ORDER)

    # ── utilities ──

    @staticmethod
    def _get_css_class(div: Tag) -> str:
        """Get the first CSS class of a div element."""
        classes = div.get("class", [])
        if isinstance(classes, list) and classes:
            return classes[0]
        if isinstance(classes, str):
            return classes
        return ""

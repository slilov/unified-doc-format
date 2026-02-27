"""
Abstract base parser for the unified legal document format.

Concrete subclasses (``HtmlParser``, future ``PdfParser``) implement the
document-specific extraction logic while inheriting common UID generation
and table-of-contents generation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from config import TOC_TYPES
from models import Node, generate_uids


class BaseParser(ABC):
    """Base class for all document parsers."""

    # ── public entry point ──

    def parse(
        self,
        file_path: str | Path,
        source: str,
        doc_id: str,
    ) -> dict[str, Any]:
        """Parse *file_path* and return the unified JSON structure.

        Returns
        -------
        dict with keys ``metadata``, ``table_of_contents``, ``document``.
        """
        self._file_path = Path(file_path)
        self._source = source
        self._doc_id = doc_id

        metadata = self._extract_metadata()
        document_tree = self._extract_document_tree()

        # Assign UIDs to every node
        generate_uids(document_tree, source, doc_id)

        toc = self._generate_toc(document_tree)

        return {
            "metadata": metadata,
            "table_of_contents": toc,
            "document": [n.to_dict() for n in document_tree],
        }

    # ── abstract methods ── (to be implemented by subclasses)

    @abstractmethod
    def _extract_metadata(self) -> dict[str, Any]:
        """Extract document-level metadata (title, date, amendments, …)."""
        ...

    @abstractmethod
    def _extract_document_tree(self) -> list[Node]:
        """Build the hierarchical document tree from the source file."""
        ...

    # ── concrete helpers ──

    @staticmethod
    def _generate_toc(tree: list[Node]) -> list[dict[str, Any]]:
        """Recursively generate ``table_of_contents`` from the document tree.

        Includes only structural levels defined in ``TOC_TYPES`` (down to
        ``article``).  Does **not** descend into article-internal elements
        (paragraphs, points, letters, …).
        """

        def _walk(nodes: list[Node]) -> list[dict[str, Any]]:
            entries: list[dict[str, Any]] = []
            for node in nodes:
                if node.type not in TOC_TYPES:
                    continue
                entry: dict[str, Any] = {
                    "uid": node.uid,
                    "type": node.type,
                }
                if node.title is not None:
                    entry["title"] = node.title
                if node.item is not None:
                    entry["item"] = node.item
                child_entries = _walk(node.children)
                if child_entries:
                    entry["children"] = child_entries
                entries.append(entry)
            return entries

        return _walk(tree)

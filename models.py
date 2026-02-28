"""
Data models for the unified legal document format.

Contains the Node dataclass and the ItemCounter utility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from config import (
    HIERARCHY_ORDER,
    ARTICLE_INTERNAL_HIERARCHY,
    PROVISION_INTERNAL_HIERARCHY,
    UID_LEVEL_SEP,
    UID_TYPE_ITEM_SEP,
)


# ── Node ──────────────────────────────────────────────────────────────────


@dataclass
class Node:
    """A single element in the legal document tree.

    Field names follow the user specification exactly.
    JSON keys use hyphens (``html-content``, ``embed-at-this-level``).
    """

    type: str
    uid: str = ""
    title: str | None = None
    item: str | None = None
    content: str | None = None
    html_content: str | None = None
    embed_at_this_level: bool = False
    metadata: dict[str, Any] | None = None
    children: list[Node] = field(default_factory=list)

    # ── serialisation ──

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready dictionary.

        * ``html_content`` → ``html-content``
        * ``embed_at_this_level`` → ``embed-at-this-level``
        * Fields that are ``None`` are omitted (except ``children``).
        """
        d: dict[str, Any] = {"uid": self.uid, "type": self.type}

        if self.title is not None:
            d["title"] = self.title
        if self.item is not None:
            d["item"] = self.item
        if self.content is not None:
            d["content"] = self.content
        if self.html_content is not None:
            d["html-content"] = self.html_content

        d["embed-at-this-level"] = self.embed_at_this_level

        if self.metadata is not None:
            d["metadata"] = self.metadata
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]

        return d


# ── ItemCounter ───────────────────────────────────────────────────────────


class ItemCounter:
    """Maintains per-type auto-increment counters for ``item`` generation.

    Rules
    -----
    * Elements with an explicit numeric ``item`` extracted from text use that
      value directly (the counter is **not** advanced).
    * Elements with ordinal-word numbers (e.g. "Глава първа") or no number at
      all receive the **next** auto-incremented value for their ``type``.
    * When an element of a higher structural rank is encountered, counters for
      **all** lower ranks are reset to 0.
    """

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    # ── public API ──

    def next_item(self, node_type: str) -> str:
        """Increment and return the next auto-generated item for *node_type*."""
        self._counters.setdefault(node_type, 0)
        self._counters[node_type] += 1
        return str(self._counters[node_type])

    def reset_lower(self, node_type: str) -> None:
        """Reset counters for all types ranked below *node_type*.

        Works for both the structural hierarchy and the article-internal
        hierarchy independently.
        """
        for hierarchy in (
            HIERARCHY_ORDER,
            ARTICLE_INTERNAL_HIERARCHY,
            PROVISION_INTERNAL_HIERARCHY,
        ):
            if node_type in hierarchy:
                idx = hierarchy.index(node_type)
                for lower_type in hierarchy[idx + 1:]:
                    self._counters[lower_type] = 0

    def peek(self, node_type: str) -> int:
        """Return the current counter value without incrementing."""
        return self._counters.get(node_type, 0)


# ── UID helpers ───────────────────────────────────────────────────────────


def _ensure_str_value(val: str) -> str:
    """Ensure enum members are converted to their string value.

    ``str(NodeType.PART)`` returns ``'NodeType.PART'`` on Python < 3.11,
    but we always want the plain value ``'part'``.
    """
    if hasattr(val, "value"):
        return str(val.value)
    return str(val)


def build_uid(
    source: str,
    doc_id: str,
    ancestor_pairs: list[tuple[str, str]],
    node_type: str,
    node_item: str,
) -> str:
    """Build a UID string from its components.

    Format::

        source___doc_id___type1__item1___type2__item2___...___typeN__itemN

    Parameters
    ----------
    source : str
        Document source (e.g. ``"lex.bg"``).
    doc_id : str
        Document identifier (e.g. ``"НК"``).
    ancestor_pairs : list of (type, item)
        Each ancestor's ``(type, item)`` from root to immediate parent.
    node_type : str
        This node's type.
    node_item : str
        This node's item.
    """
    parts = [source, doc_id]
    for t, i in ancestor_pairs:
        parts.append(f"{_ensure_str_value(t)}{UID_TYPE_ITEM_SEP}{i}")
    parts.append(f"{_ensure_str_value(node_type)}{UID_TYPE_ITEM_SEP}{node_item}")
    return UID_LEVEL_SEP.join(parts)


def generate_uids(
    nodes: list[Node],
    source: str,
    doc_id: str,
    ancestor_pairs: list[tuple[str, str]] | None = None,
) -> None:
    """Recursively assign ``uid`` to every node in the tree **in-place**."""
    if ancestor_pairs is None:
        ancestor_pairs = []

    for node in nodes:
        item = node.item or "0"
        node_type_str = _ensure_str_value(node.type)
        node.uid = build_uid(source, doc_id, ancestor_pairs, node_type_str, item)
        if node.children:
            child_ancestors = ancestor_pairs + [(node_type_str, item)]
            generate_uids(node.children, source, doc_id, child_ancestors)

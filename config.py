"""
Configuration for the unified legal document format converter.

Contains NodeType enum, hierarchy definitions, CSS class mappings,
and UID format constants.
"""

from enum import Enum


class NodeType(str, Enum):
    """All possible types of nodes in a legal document tree.

    Values sourced from law_hierarchy_template_v2.json.
    """

    # --- Document-level structural types ---
    EXPLANATION = "explanation"
    HISTORY = "history"
    PREAMBLE_HEADING = "preamble_heading"
    PREAMBLE = "preamble"

    # --- Structural hierarchy (top-down) ---
    PART = "part"
    PARTITION = "partition"
    CHAPTER = "chapter"
    SECTION = "section"
    SUBSECTION = "subsection"
    HEADING = "heading"

    # --- Article and internal hierarchy ---
    ARTICLE = "article"
    PARAGRAPH = "paragraph"
    POINT = "point"
    SUBPOINT = "subpoint"
    SUB_SUBPOINT = "sub_subpoint"
    LETTER = "letter"
    DOUBLE_LETTER = "double_letter"
    TRIPLE_LETTER = "triple_letter"
    LATIN_LETTER = "latin_letter"
    CAPITAL_SECTION = "capital_section"
    ROMAN_SECTION = "roman_section"

    # --- Special container / content types ---
    POINTS_CONTAINER = "points_container"
    TEXT_BLOCK = "text_block"
    TEXT_ELEMENT = "text_element"
    IMAGE = "image"
    HTML_BLOCK = "html_block"
    DELIMITER = "delimiter"

    # --- Provision types (transitional/final/supplementary) ---
    PROVISION = "provision"
    PROVISION_HEADING = "provision_heading"
    PROVISION_HISTORY = "provision_history"
    CLAUSE = "clause"

    # --- EU legislation types ---
    EU_LEGISLATION_RELEVANCE = "EU_legislation_relevance"
    EU_DIRECTIVE = "EU_directive"
    EU_DIRECTIVE_ITEM = "EU_directive_item"
    EU_REGULATION = "EU_regulation"
    EU_REGULATION_ITEM = "EU_regulation_item"
    EU_DECISION = "EU_decision"
    EU_DECISION_ITEM = "EU_decision_item"
    EU_OTHER_ACT = "EU_other_act"
    EU_OTHER_ACT_ITEM = "EU_other_act_item"

    # --- Appendix types ---
    APPENDIX = "appendix"
    APPENDIX_TITLE = "appendix_title"
    APPENDIX_SUBTITLE = "appendix_subtitle"
    APPENDIX_SECTION = "appendix_section"
    APPENDIX_NOTE = "appendix_note"
    SIMPLE_POINT = "simple_point"


# ---------------------------------------------------------------------------
# Hierarchy orders — used for stack-based reconstruction and counter resets
# ---------------------------------------------------------------------------

HIERARCHY_ORDER: list[str] = [
    NodeType.PART,
    NodeType.PARTITION,
    NodeType.CHAPTER,
    NodeType.SECTION,
    NodeType.SUBSECTION,
    NodeType.HEADING,
    NodeType.ARTICLE,
]

ARTICLE_INTERNAL_HIERARCHY: list[str] = [
    NodeType.PARAGRAPH,
    NodeType.POINT,
    NodeType.SUBPOINT,
    NodeType.SUB_SUBPOINT,
    NodeType.LETTER,
    NodeType.DOUBLE_LETTER,
    NodeType.TRIPLE_LETTER,
    NodeType.LATIN_LETTER,
]

# Provision internal hierarchy (§ clauses)
PROVISION_INTERNAL_HIERARCHY: list[str] = [
    NodeType.CLAUSE,
    NodeType.PARAGRAPH,
    NodeType.POINT,
    NodeType.LETTER,
    NodeType.DOUBLE_LETTER,
    NodeType.TRIPLE_LETTER,
]

# Structural types that appear in table_of_contents
TOC_TYPES: set[str] = {
    NodeType.PART,
    NodeType.PARTITION,
    NodeType.CHAPTER,
    NodeType.SECTION,
    NodeType.SUBSECTION,
    NodeType.HEADING,
    NodeType.ARTICLE,
    NodeType.PROVISION,
}

# ---------------------------------------------------------------------------
# CSS class → NodeType mapping for lex.bg HTML documents
# ---------------------------------------------------------------------------

CSS_CLASS_MAP: dict[str, str] = {
    "TitleDocument": "_title_document",      # special handling
    "PreHistory": "_pre_history",            # special handling
    "HistoryOfDocument": "_history_document", # special handling
    "Heading": "_heading_ambiguous",         # resolved by text content
    "Section": NodeType.SECTION,
    "Article": NodeType.ARTICLE,
    "TransitionalFinalEdicts": NodeType.PROVISION,
    "FinalEdicts": NodeType.PROVISION,
    "AdditionalEdicts": NodeType.PROVISION,
    "FinalEdictsArticle": NodeType.CLAUSE,
}

# ---------------------------------------------------------------------------
# UID format constants
# ---------------------------------------------------------------------------

UID_LEVEL_SEP = "___"   # separator between hierarchy levels
UID_TYPE_ITEM_SEP = "__" # separator between type and item within a level


# ---------------------------------------------------------------------------
# Document types (top-level type of the legal act)
# ---------------------------------------------------------------------------

class DocType(str, Enum):
    CONSTITUTION = "constitution"
    LAW = "law"
    CODE = "code"
    REGULATION = "regulation"
    ORDINANCE = "ordinance"
    IMPLEMENTING_REGULATION = "implementing_regulation"
    LEGAL_ACT = "legal_act"

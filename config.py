"""
Configuration for the unified legal document format converter.

Contains NodeType enum, hierarchy definitions, CSS class mappings,
UID format constants, and Bulgarian-specific text utilities.
"""

import re
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
# UID format constants
# ---------------------------------------------------------------------------

UID_LEVEL_SEP = "___"   # separator between hierarchy levels
UID_TYPE_ITEM_SEP = "__" # separator between type and item within a level


# ---------------------------------------------------------------------------
# Bulgarian ordinal-word → number mapping
# (Sourced from law-hierarchy/patterns.py CHAPTER_PATTERNS / SECTION_PATTERNS)
# ---------------------------------------------------------------------------

# Feminine ordinals (used with "Глава", "Част")
_ORDINALS_FEMININE: dict[str, str] = {
    "първа": "1", "втора": "2", "трета": "3", "четвърта": "4",
    "пета": "5", "шеста": "6", "седма": "7", "осма": "8",
    "девета": "9", "десета": "10", "единадесета": "11",
    "дванадесета": "12", "тринадесета": "13", "четиринадесета": "14",
    "петнадесета": "15", "шестнадесета": "16", "седемнадесета": "17",
    "осемнадесета": "18", "деветнадесета": "19", "двадесета": "20",
    "двадесет и първа": "21", "двадесет и втора": "22",
    "двадесет и трета": "23", "двадесет и четвърта": "24",
    "двадесет и пета": "25", "двадесет и шеста": "26",
    "двадесет и седма": "27", "двадесет и осма": "28",
    "двадесет и девета": "29", "тридесета": "30",
}

# Masculine ordinals (used with "Дял", "Раздел", "Подраздел")
_ORDINALS_MASCULINE: dict[str, str] = {
    "първи": "1", "втори": "2", "трети": "3", "четвърти": "4",
    "пети": "5", "шести": "6", "седми": "7", "осми": "8",
    "девети": "9", "десети": "10", "единадесети": "11",
    "дванадесети": "12", "тринадесети": "13", "четиринадесети": "14",
    "петнадесети": "15", "шестнадесети": "16", "седемнадесети": "17",
    "осемнадесети": "18", "деветнадесети": "19", "двадесети": "20",
    "двадесет и първи": "21", "двадесет и втори": "22",
    "двадесет и трети": "23", "двадесет и четвърти": "24",
    "двадесет и пети": "25", "двадесет и шести": "26",
    "двадесет и седми": "27", "двадесет и осми": "28",
    "двадесет и девети": "29", "тридесети": "30",
}

# Roman numeral → Arabic number
_ROMAN_MAP: dict[str, int] = {
    "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5,
    "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10,
    "XI": 11, "XII": 12, "XIII": 13, "XIV": 14, "XV": 15,
    "XVI": 16, "XVII": 17, "XVIII": 18, "XIX": 19, "XX": 20,
    "XXI": 21, "XXII": 22, "XXIII": 23, "XXIV": 24, "XXV": 25,
    "XXVI": 26, "XXVII": 27, "XXVIII": 28, "XXIX": 29, "XXX": 30,
    # Cyrillic lookalikes often used in Bulgarian legal texts
    "І": 1, "ІІ": 2, "ІІІ": 3, "ІV": 4,
    "VІ": 6, "VІІ": 7, "VІІІ": 8, "ІХ": 9,
    "ХІ": 11, "ХІІ": 12, "ХІІІ": 13, "ХІV": 14, "ХV": 15,
}

# Combined lookup (case-insensitive key → Arabic number string)
_ALL_ORDINALS: dict[str, str] = {}
for _d in (_ORDINALS_FEMININE, _ORDINALS_MASCULINE):
    for _k, _v in _d.items():
        _ALL_ORDINALS[_k.casefold()] = _v
        _ALL_ORDINALS[_k.upper()] = _v

def ordinal_to_number(text: str) -> str | None:
    """Convert a Bulgarian ordinal word or Roman numeral to an Arabic number.

    Returns the number as a string, or ``None`` if *text* is not recognised.

    Examples
    --------
    >>> ordinal_to_number("първа")
    '1'
    >>> ordinal_to_number("III")
    '3'
    >>> ordinal_to_number("ХІV")
    '14'
    """
    clean = text.strip().rstrip(".")
    # Try ordinal words first
    result = _ALL_ORDINALS.get(clean.casefold()) or _ALL_ORDINALS.get(clean)
    if result:
        return result
    # Try Roman numerals
    roman_val = _ROMAN_MAP.get(clean)
    if roman_val is not None:
        return str(roman_val)
    return None


# ---------------------------------------------------------------------------
# Text normalization (ported from law-hierarchy/tree_builder.py)
# ---------------------------------------------------------------------------

def normalize_structural_text(text: str) -> str:
    """Clean structural heading text.

    * Fix soft-hyphenation artifacts (``"ДО- СТЪП"`` → ``"ДОСТЪП"``).
    * Separate glued ALL-CAPS prepositions (``"ПРОЦЕСУАЛЕНЗАКОННИК"``).
    * Collapse whitespace.

    Ported from ``law-hierarchy/tree_builder.py:_normalize_structural_text``.
    """
    t = (text or "").strip()
    if not t:
        return t

    # Fix soft-hyphenation artifacts
    t = re.sub(r"([А-Яа-я]{2,})-\s+([А-Яа-я]{2,})", r"\1\2", t)

    # Collapse whitespace
    t = re.sub(r"\s+", " ", t).strip()

    return t

"""
Table normalizer for lex.bg legal documents.

Handles the common case where a single visual table in lex.bg HTML is 
split into multiple HTML tables:
- One empty table with class="def" (placeholder)
- One or more tables with class="defFix" containing the actual data

This module provides functions to:
1. Detect and merge consecutive tables into a single normalized table
2. Handle complex headers with colspan (cells spanning multiple columns)
3. Generate clean HTML output for the merged table
"""

import re
import logging
from typing import Optional, List, Dict, Any, Tuple
from bs4 import BeautifulSoup, Tag, NavigableString
from dataclasses import dataclass, field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class NormalizedCell:
    """Represents a cell in a normalized table."""
    text: str
    colspan: int = 1
    rowspan: int = 1
    width: Optional[int] = None  # Width in pixels
    align: str = "left"  # 'left', 'center', 'right'
    is_header: bool = False
    # Border flags from CSS class (bit3=L, bit2=R, bit1=T, bit0=B)
    has_bottom_border: bool = True
    has_top_border: bool = False
    border_code: int = 0  # Original border code from CSS class
    
    def to_html(self) -> str:
        """Generate HTML for this cell."""
        tag = "th" if self.is_header else "td"
        attrs = []
        if self.colspan > 1:
            attrs.append(f'colspan="{self.colspan}"')
        if self.rowspan > 1:
            attrs.append(f'rowspan="{self.rowspan}"')
        
        # Build style attribute
        styles = []
        
        # Add width if specified (enables text wrapping)
        if self.width:
            styles.append(f"width: {self.width}px")
        
        # Header cells are always centered, data cells use their alignment
        effective_align = "center" if self.is_header else self.align
        if effective_align != "left":
            styles.append(f"text-align: {effective_align}")
        
        # Add inline border styles based on border_code
        # bit 0 (1) = BOTTOM, bit 1 (2) = TOP, bit 2 (4) = RIGHT, bit 3 (8) = LEFT
        if self.border_code > 0:
            border_style = "1px solid"
            if self.border_code & 8:  # LEFT
                styles.append(f"border-left: {border_style}")
            if self.border_code & 4:  # RIGHT
                styles.append(f"border-right: {border_style}")
            if self.border_code & 2:  # TOP
                styles.append(f"border-top: {border_style}")
            if self.border_code & 1:  # BOTTOM
                styles.append(f"border-bottom: {border_style}")
        
        if styles:
            attrs.append(f'style="{"; ".join(styles)}"')
        
        attr_str = " " + " ".join(attrs) if attrs else ""
        return f"<{tag}{attr_str}>{self.text}</{tag}>"


@dataclass
class NormalizedRow:
    """Represents a row in a normalized table."""
    cells: List[NormalizedCell] = field(default_factory=list)
    is_header: bool = False
    is_column_numbers: bool = False  # Row with column numbers (1, 2, 3...)
    
    def to_html(self) -> str:
        """Generate HTML for this row."""
        cells_html = "".join(cell.to_html() for cell in self.cells)
        return f"<tr>{cells_html}</tr>"


@dataclass
class NormalizedTable:
    """Represents a fully normalized table."""
    rows: List[NormalizedRow] = field(default_factory=list)
    title: Optional[str] = None  # Title before the table
    notes: Optional[str] = None  # Notes after the table
    
    def to_html(self, include_title: bool = True, include_notes: bool = True) -> str:
        """Generate clean HTML for this table."""
        parts = []
        
        # Find header rows and data rows
        header_rows = []
        data_rows = []
        
        for row in self.rows:
            if row.is_header or row.is_column_numbers:
                header_rows.append(row)
            else:
                data_rows.append(row)
        
        # Build table HTML - no border attribute, borders are set inline on cells
        table_parts = ['<table cellspacing="0" cellpadding="5" style="border-collapse: collapse;">']
        
        # Add title as caption (centered relative to table width)
        if include_title and self.title:
            table_parts.append(f'<caption style="text-align: center; font-weight: bold; padding-bottom: 5px;">{self.title}</caption>')
        
        if header_rows:
            table_parts.append("<thead>")
            for row in header_rows:
                # Include all rows, even empty ones - they are needed for rowspan
                table_parts.append(row.to_html())
            table_parts.append("</thead>")
        
        if data_rows:
            table_parts.append("<tbody>")
            for row in data_rows:
                # Include all rows - empty rows might be needed for rowspan consistency
                table_parts.append(row.to_html())
            table_parts.append("</tbody>")
        
        table_parts.append("</table>")
        parts.append("\n".join(table_parts))
        
        if include_notes and self.notes:
            parts.append("<br/>")
            parts.append(f"<div>{self.notes}</div>")
        
        return "\n".join(parts)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        # Extract simple header and data representations
        headers = []
        data_rows = []
        column_numbers = None
        
        for row in self.rows:
            if row.is_column_numbers:
                column_numbers = [cell.text for cell in row.cells]
            elif row.is_header:
                headers.append([cell.text for cell in row.cells])
            else:
                data_rows.append([cell.text for cell in row.cells])
        
        result = {
            "headers": headers,
            "rows": data_rows
        }
        if self.title:
            result["title"] = self.title
        if self.notes:
            result["notes"] = self.notes
        if column_numbers:
            result["column_numbers"] = column_numbers
        
        return result


class TableNormalizer:
    """Normalizes lex.bg tables by merging split tables and handling colspan/rowspan."""
    
    # CSS class prefixes that indicate cell alignment
    # L = left, C = center, R = right, J = justify (based on lex.bg ciela.css)
    ALIGN_PREFIXES = {
        'L': 'left',
        'C': 'center',
        'R': 'right',
        'J': 'justify'
    }
    
    # Border encoding from lex.bg ciela.css (https://lex.bg/assets/ciela/ciela.css):
    # CSS classes follow the pattern: <alignment><border_code>
    # Example: L4, C12, R5, J0
    #
    # The number (border_code) is a 4-bit flag encoding which borders are visible:
    #   bit3 (8) = LEFT border
    #   bit2 (4) = RIGHT border
    #   bit1 (2) = TOP border
    #   bit0 (1) = BOTTOM border
    #
    # Complete border code table:
    # +------+--------+------+-------+-----+--------+---------------------------+
    # | Code | Binary | LEFT | RIGHT | TOP | BOTTOM | Description               |
    # +------+--------+------+-------+-----+--------+---------------------------+
    # |  0   |  0000  |  -   |   -   |  -  |   -    | No borders                |
    # |  1   |  0001  |  -   |   -   |  -  |   ✓    | Bottom only               |
    # |  2   |  0010  |  -   |   -   |  ✓  |   -    | Top only                  |
    # |  3   |  0011  |  -   |   -   |  ✓  |   ✓    | Top + Bottom              |
    # |  4   |  0100  |  -   |   ✓   |  -  |   -    | Right only                |
    # |  5   |  0101  |  -   |   ✓   |  -  |   ✓    | Right + Bottom            |
    # |  6   |  0110  |  -   |   ✓   |  ✓  |   -    | Right + Top               |
    # |  7   |  0111  |  -   |   ✓   |  ✓  |   ✓    | Right + Top + Bottom      |
    # |  8   |  1000  |  ✓   |   -   |  -  |   -    | Left only                 |
    # |  9   |  1001  |  ✓   |   -   |  -  |   ✓    | Left + Bottom             |
    # | 10   |  1010  |  ✓   |   -   |  ✓  |   -    | Left + Top                |
    # | 11   |  1011  |  ✓   |   -   |  ✓  |   ✓    | Left + Top + Bottom       |
    # | 12   |  1100  |  ✓   |   ✓   |  -  |   -    | Left + Right              |
    # | 13   |  1101  |  ✓   |   ✓   |  -  |   ✓    | Left + Right + Bottom     |
    # | 14   |  1110  |  ✓   |   ✓   |  ✓  |   -    | Left + Right + Top        |
    # | 15   |  1111  |  ✓   |   ✓   |  ✓  |   ✓    | All borders (full box)    |
    # +------+--------+------+-------+-----+--------+---------------------------+
    #
    # Common patterns in lex.bg tables:
    # - First column cells typically use codes 8-15 (have LEFT border)
    # - Middle/last column cells typically use codes 0-7 (no LEFT border)
    # - Header cells without BOTTOM border (even codes) span multiple rows
    # - Last row of a section has BOTTOM border (odd codes)
    
    def __init__(self):
        """Initialize the normalizer."""
        pass
    
    def normalize_html(self, html_content: str) -> str:
        """Normalize all tables in HTML content.
        
        Finds groups of consecutive tables (def + defFix) and merges them
        into single normalized tables.
        
        Also handles "hidden bordered" tables - tables with border="0" but
        with cells that have CSS border codes > 0 (e.g., C14, L5).
        
        Also cleans up inline styles that specify black color for borders/text,
        which is redundant and causes rendering issues.
        
        Args:
            html_content: HTML string containing tables
            
        Returns:
            HTML string with normalized tables
        """
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Clean up black color from inline styles (common in ciela2014 format)
        self._clean_black_color_styles(soup)
        
        # Remove wrapper tables (ciela2014 tables containing only one inner table)
        self._unwrap_ciela2014_wrapper_tables(soup)
        
        # Process table groups (def + defFix)
        self._normalize_tables_in_soup(soup)
        
        # Process hidden bordered tables (border="0" but cells have CSS borders)
        self._normalize_hidden_bordered_tables(soup)
        
        return str(soup)
    
    def _clean_black_color_styles(self, soup: BeautifulSoup) -> None:
        """Remove explicit black color specifications from inline styles.
        
        Tables with tableformat="ciela2014" often have inline styles like:
        - BORDER-TOP: black 1pt solid
        - color: black
        
        Since black is the default, these are redundant and can cause issues.
        This method removes the "black" color specification from such styles.
        Also removes "windowtext" which is a Windows system color that renders as black.
        
        Args:
            soup: BeautifulSoup object to modify in place
        """
        # Find all elements with style attribute
        elements_with_style = soup.find_all(style=True)
        
        for element in elements_with_style:
            style = element.get('style', '')
            if not style:
                continue
            
            # Remove "black" from border specifications
            # e.g., "BORDER-TOP: black 1pt solid" -> "BORDER-TOP: 1pt solid"
            # Handle both "black 1pt solid" and "1pt solid black"
            new_style = re.sub(r'\bblack\s+', '', style, flags=re.IGNORECASE)
            new_style = re.sub(r'\s+black\b', '', new_style, flags=re.IGNORECASE)
            
            # Remove "windowtext" (Windows system color that renders as black)
            # e.g., "BORDER-BOTTOM: windowtext 1pt solid" -> "BORDER-BOTTOM: 1pt solid"
            new_style = re.sub(r'\bwindowtext\s+', '', new_style, flags=re.IGNORECASE)
            new_style = re.sub(r'\s+windowtext\b', '', new_style, flags=re.IGNORECASE)
            
            # Also handle color: black
            new_style = re.sub(r'color\s*:\s*black\s*;?', '', new_style, flags=re.IGNORECASE)
            
            # Remove empty color values which can cause rendering issues
            # These appear when the original document had color stripped but the property remained
            # Handles: COLOR:; COLOR: ; COLOR:" COLOR:' COLOR: (at end) COLOR:> etc.
            # The pattern matches COLOR: followed by optional space and then ; or quote/bracket or end of string
            new_style = re.sub(r'color\s*:\s*(?=;|["\'>]|$)', '', new_style, flags=re.IGNORECASE)
            
            # Remove BACKGROUND: white which causes visibility issues in dark mode
            # Tables with white background make text invisible when the editor/viewer uses dark theme
            new_style = re.sub(r'background\s*:\s*white\s*;?', '', new_style, flags=re.IGNORECASE)
            
            # Remove near-white background colors (#fefefe, #ffffff, etc.)
            # These also cause visibility issues in dark mode
            # Matches #fefefe, #fff, #ffffff, #FEFEFE, etc.
            new_style = re.sub(r'background\s*:\s*#[fF][eEfF][fF]?[eEfF]?[fF]?[eEfF]?\s*;?', '', new_style, flags=re.IGNORECASE)
            
            # Remove MARGIN-LEFT from tables (causes misalignment with titles)
            # e.g., "MARGIN-LEFT: 2.85pt" or "MARGIN-LEFT: 1.4pt"
            new_style = re.sub(r'MARGIN-LEFT\s*:\s*[\d.]+\s*pt\s*;?', '', new_style, flags=re.IGNORECASE)
            
            # Clean up any resulting double spaces or leading/trailing semicolons
            new_style = re.sub(r'\s+', ' ', new_style)
            new_style = re.sub(r';\s*;', ';', new_style)
            new_style = new_style.strip(' ;')
            
            if new_style:
                element['style'] = new_style
            else:
                del element['style']

    def _unwrap_ciela2014_wrapper_tables(self, soup: BeautifulSoup) -> None:
        """Remove wrapper tables and clean up ciela2014 format tables.
        
        In ciela2014 format, tables are often wrapped in an outer table like:
        <table tableformat="ciela2014">
          <tbody>
            <tr>
              <td>
                <p>Title</p>
                <table>actual data table</table>
              </td>
            </tr>
          </tbody>
        </table>
        
        This method:
        1. Detects wrapper tables and extracts their content
        2. Removes the tableformat="ciela2014" attribute from all tables
        
        Args:
            soup: BeautifulSoup object to modify in place
        """
        # Find all ciela2014 tables (process in reverse to handle nested tables correctly)
        ciela_tables = list(soup.find_all('table', attrs={'tableformat': 'ciela2014'}))
        
        for wrapper_table in ciela_tables:
            # First, remove the tableformat attribute
            if wrapper_table.has_attr('tableformat'):
                del wrapper_table['tableformat']
            
            # Check if this is a wrapper table:
            # - All direct tds have no visible borders (or all borders = medium none)
            # - Each td contains either text content or inner tables
            
            tbody = wrapper_table.find('tbody')
            if not tbody:
                continue
            
            direct_trs = [child for child in tbody.children if isinstance(child, Tag) and child.name == 'tr']
            if not direct_trs:
                continue
            
            # Check all direct tds in all rows
            all_tds_are_wrappers = True
            all_content = []
            
            for tr in direct_trs:
                direct_tds = [child for child in tr.children if isinstance(child, Tag) and child.name == 'td']
                
                # Wrapper tables typically have one td per row
                if len(direct_tds) != 1:
                    all_tds_are_wrappers = False
                    break
                
                single_td = direct_tds[0]
                
                # Check if the td has no visible borders (all "medium none" or no border specs)
                style = single_td.get('style', '')
                has_visible_border = False
                if style:
                    # Check for any border that is not "medium none"
                    border_pattern = r'BORDER-(TOP|RIGHT|BOTTOM|LEFT)\s*:\s*([^;]+)'
                    borders = re.findall(border_pattern, style, re.IGNORECASE)
                    for _, border_value in borders:
                        if 'medium none' not in border_value.lower() and border_value.strip():
                            # Check if it has actual border (e.g., "1pt solid")
                            if re.search(r'\d+\s*pt', border_value, re.IGNORECASE):
                                has_visible_border = True
                                break
                
                if has_visible_border:
                    all_tds_are_wrappers = False
                    break
                
                # Collect content from this td
                all_content.extend(list(single_td.children))
            
            if not all_tds_are_wrappers:
                continue
            
            # Check if there's at least one inner table in the collected content
            has_inner_table = False
            for elem in all_content:
                if isinstance(elem, Tag):
                    if elem.name == 'table' or elem.find('table'):
                        has_inner_table = True
                        break
            
            if not has_inner_table:
                continue
            
            # Get the width from the first td to preserve layout
            first_tr = direct_trs[0]
            first_tds = [child for child in first_tr.children if isinstance(child, Tag) and child.name == 'td']
            td_width = None
            if first_tds:
                first_td = first_tds[0]
                # Try to get width from style or width attribute
                style = first_td.get('style', '')
                width_match = re.search(r'WIDTH\s*:\s*([\d.]+\s*pt)', style, re.IGNORECASE)
                if width_match:
                    td_width = width_match.group(1)
                elif first_td.get('width'):
                    td_width = f"{first_td.get('width')}px"
            
            # Create a wrapper div to preserve width and centering
            if td_width:
                wrapper_div = soup.new_tag('div')
                wrapper_div['style'] = f'width: {td_width}; max-width: 100%;'
                
                # Move all content into the wrapper div
                for elem in all_content:
                    if isinstance(elem, Tag):
                        wrapper_div.append(elem.extract())
                    elif isinstance(elem, NavigableString) and str(elem).strip():
                        wrapper_div.append(elem.extract())
                
                # Insert the wrapper div before the table
                wrapper_table.insert_before(wrapper_div)
            else:
                # No width found, just extract content directly
                for elem in all_content:
                    if isinstance(elem, Tag):
                        wrapper_table.insert_before(elem.extract())
                    elif isinstance(elem, NavigableString) and str(elem).strip():
                        wrapper_table.insert_before(elem.extract())
            
            # Remove the empty wrapper table
            wrapper_table.decompose()
        
        # Also remove tableformat attribute from any remaining tables
        for table in soup.find_all('table', attrs={'tableformat': True}):
            del table['tableformat']

    def _normalize_tables_in_soup(self, soup: BeautifulSoup) -> None:
        """Find and normalize table groups in a BeautifulSoup object.
        
        Modifies the soup in place.
        """
        # Find all table groups (def followed by defFix tables)
        processed_tables = set()
        
        # Find all tables with class "def" or "defFix"
        all_tables = soup.find_all('table', class_=lambda c: c and ('def' in c or 'defFix' in c))
        
        i = 0
        while i < len(all_tables):
            table = all_tables[i]
            
            if id(table) in processed_tables:
                i += 1
                continue
            
            # Check if this is a "def" table (empty placeholder)
            classes = table.get('class', [])
            if 'def' in classes and 'defFix' not in classes:
                # Find all consecutive defFix tables
                table_group = self._find_table_group(table)
                
                if table_group:
                    # Merge the tables
                    normalized = self._merge_tables(table_group)
                    
                    if normalized:
                        # Generate new HTML and replace
                        # Include title if present
                        new_html = normalized.to_html(include_title=True, include_notes=False)
                        new_soup = BeautifulSoup(new_html, 'html.parser')
                        
                        # Find all elements (title div + br + table)
                        new_elements = list(new_soup.children)
                        
                        if new_elements:
                            # Insert new elements before the def table
                            # First, find the correct position
                            for i, elem in enumerate(new_elements):
                                if i == 0:
                                    table.insert_before(elem)
                                else:
                                    new_elements[i-1].insert_after(elem)
                            
                            # Remove the original tables in the group
                            for t in table_group:
                                # Remove <br/> elements between tables
                                prev_sibling = t.previous_sibling
                                while prev_sibling and isinstance(prev_sibling, NavigableString) and not prev_sibling.strip():
                                    prev_sibling = prev_sibling.previous_sibling
                                if prev_sibling and prev_sibling.name == 'br':
                                    prev_sibling.decompose()
                                t.decompose()
                            
                            # Mark all tables in group as processed
                            for t in table_group:
                                processed_tables.add(id(t))
                    else:
                        # Form table - keep separate but apply inline border styles
                        self._apply_inline_borders_to_tables(table_group)
                        # Mark tables as processed so they're not touched again
                        for t in table_group:
                            processed_tables.add(id(t))
            
            i += 1
    
    def _apply_inline_borders_to_tables(self, tables: List[Tag]) -> None:
        """Apply inline border styles and widths to form tables based on CSS class codes.
        
        When form tables are kept separate (not merged), we still need to convert
        the CSS class border codes (L4, C12, R5, etc.) to inline styles so they
        render correctly in markdown. Also preserves cell widths as inline styles
        and adds border-collapse to prevent gaps between borders.
        
        Args:
            tables: List of table elements to process
        """
        for table in tables:
            # Skip the empty def table
            if 'defFix' not in table.get('class', []):
                continue
            
            # Add border-collapse to prevent gaps between cell borders
            existing_table_style = table.get('style', '')
            if 'border-collapse' not in existing_table_style:
                if existing_table_style:
                    table['style'] = f"{existing_table_style}; border-collapse: collapse"
                else:
                    table['style'] = "border-collapse: collapse"
            
            for cell in table.find_all(['td', 'th']):
                classes = cell.get('class', [])
                border_code = 0
                
                for cls in classes:
                    if cls and len(cls) >= 2 and cls[0] in self.ALIGN_PREFIXES:
                        num_part = cls[1:]
                        if num_part.isdigit():
                            border_code = int(num_part)
                            break
                
                # Collect styles to add
                new_styles = []
                
                # Add width from attribute if present
                width = cell.get('width')
                if width:
                    # Convert to pixels if it's a number
                    if width.isdigit():
                        new_styles.append(f"width: {width}px")
                    else:
                        new_styles.append(f"width: {width}")
                
                # Add border styles based on border code
                if border_code > 0:
                    # bit 0 (1) = BOTTOM, bit 1 (2) = TOP, bit 2 (4) = RIGHT, bit 3 (8) = LEFT
                    border_style = "1px solid"
                    
                    if border_code & 8:  # LEFT
                        new_styles.append(f"border-left: {border_style}")
                    if border_code & 4:  # RIGHT
                        new_styles.append(f"border-right: {border_style}")
                    if border_code & 2:  # TOP
                        new_styles.append(f"border-top: {border_style}")
                    if border_code & 1:  # BOTTOM
                        new_styles.append(f"border-bottom: {border_style}")
                
                if new_styles:
                    # Merge with existing style if present
                    existing_style = cell.get('style', '')
                    styles_str = '; '.join(new_styles)
                    if existing_style:
                        cell['style'] = f"{existing_style}; {styles_str}"
                    else:
                        cell['style'] = styles_str

    def _apply_inline_styles_to_single_table(self, table: Tag) -> None:
        """Apply inline styles (borders, alignment, width) to a single table based on CSS classes.
        
        This is a fallback for tables that can't be merged or normalized as data tables.
        It converts CSS class codes (C0, L0, R0, C14, etc.) to inline styles for:
        - Border styles based on border code (bits 0-3)
        - Text alignment based on prefix (L=left, C=center, R=right)
        - Cell widths from width attribute
        
        Args:
            table: The table element to process
        """
        # Add border-collapse to prevent gaps between cell borders
        existing_table_style = table.get('style', '')
        if 'border-collapse' not in existing_table_style:
            if existing_table_style:
                table['style'] = f"{existing_table_style}; border-collapse: collapse"
            else:
                table['style'] = "border-collapse: collapse"
        
        for cell in table.find_all(['td', 'th']):
            classes = cell.get('class', [])
            border_code = 0
            alignment = None
            
            for cls in classes:
                if cls and len(cls) >= 2 and cls[0] in self.ALIGN_PREFIXES:
                    # Extract alignment from prefix
                    prefix = cls[0]
                    if prefix == 'L':
                        alignment = 'left'
                    elif prefix == 'C':
                        alignment = 'center'
                    elif prefix == 'R':
                        alignment = 'right'
                    
                    # Extract border code from number
                    num_part = cls[1:]
                    if num_part.isdigit():
                        border_code = int(num_part)
                    break
            
            # Collect styles to add
            new_styles = []
            
            # Add width from attribute if present
            width = cell.get('width')
            if width:
                # Convert to pixels if it's a number
                if width.isdigit():
                    new_styles.append(f"width: {width}px")
                else:
                    new_styles.append(f"width: {width}")
            
            # Add text alignment
            if alignment:
                new_styles.append(f"text-align: {alignment}")
            
            # Add border styles based on border code
            if border_code > 0:
                # bit 0 (1) = BOTTOM, bit 1 (2) = TOP, bit 2 (4) = RIGHT, bit 3 (8) = LEFT
                border_style = "1px solid"
                
                if border_code & 8:  # LEFT
                    new_styles.append(f"border-left: {border_style}")
                if border_code & 4:  # RIGHT
                    new_styles.append(f"border-right: {border_style}")
                if border_code & 2:  # TOP
                    new_styles.append(f"border-top: {border_style}")
                if border_code & 1:  # BOTTOM
                    new_styles.append(f"border-bottom: {border_style}")
            
            if new_styles:
                # Merge with existing style if present
                existing_style = cell.get('style', '')
                styles_str = '; '.join(new_styles)
                if existing_style:
                    cell['style'] = f"{existing_style}; {styles_str}"
                else:
                    cell['style'] = styles_str

    def _normalize_hidden_bordered_tables(self, soup: BeautifulSoup) -> None:
        """Find and normalize tables with border="0" but cells with CSS borders.
        
        These are tables that appear borderless at the table level but have
        individual cells with border codes > 0 in their CSS classes (e.g., C14, L5).
        
        Also processes tables with CSS class alignment codes (C0, L0, R0) that
        need inline styles for proper rendering.
        
        Common in form templates where small data tables are embedded.
        
        Modifies the soup in place.
        """
        # Find all tables with defFix class
        all_tables = soup.find_all('table', class_='defFix')
        
        for table in all_tables:
            # Skip tables that already have border attribute != 0 (already processed)
            border_attr = table.get('border', '')
            if border_attr and border_attr != '0':
                continue
            
            # Check if this table has cells with CSS border codes > 0
            if self._has_hidden_borders(table):
                # This is a "hidden bordered" table - try to normalize it as data table
                self._normalize_single_hidden_bordered_table(table)
            else:
                # Table may still have CSS class codes (C0, L0, R0) that need inline styles
                # Apply inline styles for width and alignment
                self._apply_inline_styles_to_single_table(table)
    
    def _has_hidden_borders(self, table: Tag) -> bool:
        """Check if a table has cells with CSS border codes > 0.
        
        Args:
            table: A table element
            
        Returns:
            True if any cell has a non-zero border code in its CSS class
        """
        for cell in table.find_all(['td', 'th']):
            classes = cell.get('class', [])
            for cls in classes:
                if cls and len(cls) >= 2 and cls[0] in self.ALIGN_PREFIXES:
                    num_part = cls[1:]
                    if num_part.isdigit() and int(num_part) > 0:
                        return True
        return False
    
    def _normalize_single_hidden_bordered_table(self, table: Tag) -> None:
        """Normalize a single hidden bordered table in place.
        
        Extracts rows, applies normalization (colspan, rowspan for headers),
        and replaces the table with a properly bordered version.
        
        If the table doesn't look like a data table, falls back to just
        applying inline styles for borders, widths, and alignment.
        
        Args:
            table: The table element to normalize
        """
        # Extract rows from this single table
        rows = self._extract_rows(table)
        
        if not rows:
            # No rows extracted - just apply inline styles
            self._apply_inline_styles_to_single_table(table)
            return
        
        # Check if this looks like a data table (has multiple columns and structured data)
        if not self._is_data_table(rows):
            # Not a data table - just apply inline styles for borders and alignment
            self._apply_inline_styles_to_single_table(table)
            return
        
        # Analyze and normalize
        normalized = self._analyze_and_normalize(rows)
        
        if not normalized or not normalized.rows:
            # Normalization failed - fall back to inline styles
            self._apply_inline_styles_to_single_table(table)
            return
        
        # Generate new HTML with border="1"
        new_html = normalized.to_html(include_title=False, include_notes=False)
        new_soup = BeautifulSoup(new_html, 'html.parser')
        new_table = new_soup.find('table')
        
        if new_table:
            # Replace the original table with the normalized one
            table.replace_with(new_table)
    
    def _is_data_table(self, rows: List[NormalizedRow]) -> bool:
        """Check if rows represent a data table (not just form layout).
        
        Data tables typically have:
        - Multiple rows
        - Consistent column count
        - Header-like first row(s)
        
        Args:
            rows: List of extracted rows
            
        Returns:
            True if this looks like a data table
        """
        if len(rows) < 2:
            return False
        
        # Check if there are cells with non-zero border codes (indicating structure)
        has_border_codes = False
        for row in rows:
            for cell in row.cells:
                if cell.border_code > 0:
                    has_border_codes = True
                    break
            if has_border_codes:
                break
        
        if not has_border_codes:
            return False
        
        # Check for consistent column structure
        # Data tables should have similar column counts
        col_counts = [len(row.cells) for row in rows]
        if not col_counts:
            return False
        
        # Allow some variation (due to colspan)
        max_cols = max(col_counts)
        min_cols = min(col_counts)
        
        # If all rows have same number of cells, it's likely a data table
        if max_cols == min_cols and max_cols >= 2:
            return True
        
        # If variation is reasonable, still consider it a data table
        if min_cols >= 2 and max_cols <= min_cols * 2:
            return True
        
        return False

    def _find_table_group(self, def_table: Tag) -> List[Tag]:
        """Find a group of consecutive tables starting with a def table.
        
        A table group consists of:
        - One empty table with class="def" 
        - One or more tables with class="defFix"
        
        Tables are consecutive if separated only by whitespace or <br/> tags.
        
        Args:
            def_table: The starting "def" table
            
        Returns:
            List of tables in the group (including the def table)
        """
        tables = [def_table]
        current = def_table.next_sibling
        
        while current:
            # Skip whitespace and <br/> tags
            if isinstance(current, NavigableString):
                if current.strip():
                    # Non-whitespace text - end of group
                    break
                current = current.next_sibling
                continue
            
            if current.name == 'br':
                current = current.next_sibling
                continue
            
            # Check if it's a defFix table
            if current.name == 'table':
                classes = current.get('class', [])
                if 'defFix' in classes:
                    tables.append(current)
                    current = current.next_sibling
                    continue
            
            # Any other element ends the group
            break
        
        return tables if len(tables) > 1 else []
    
    def _is_title_table(self, table: Tag) -> bool:
        """Check if a table is just a title (single column spanning full width).
        
        Title tables typically have:
        - Only 1-2 rows
        - Only 1 column
        - Full width (usually 600+ pixels)
        - Contains text that looks like a title
        - All cells have border code 0 (no borders)
        
        Args:
            table: A table element
            
        Returns:
            True if this appears to be a title-only table
        """
        rows = table.find_all('tr')
        if len(rows) > 3:  # Title tables typically have 1-2 rows (title + maybe empty row)
            return False
        
        # Check each row
        for row in rows:
            cells = row.find_all(['td', 'th'])
            if len(cells) != 1:
                return False
            
            cell = cells[0]
            
            # Check if cell has borders - if so, it's not a title table
            # Border code is encoded in CSS class like L4, C12, R5
            classes = cell.get('class', [])
            for cls in classes:
                if cls and len(cls) >= 2 and cls[0] in 'LCR':
                    num_part = cls[1:]
                    if num_part.isdigit() and int(num_part) > 0:
                        # Has borders - not a title table
                        return False
            
            # Check if it's a full-width cell
            width_attr = cell.get('width', '')
            if width_attr:
                match = re.match(r'(\d+)', width_attr)
                if match:
                    width = int(match.group(1))
                    if width < 500:  # Title tables typically span 600+ pixels
                        return False
        
        return True
    
    def _extract_table_title(self, table: Tag) -> Optional[str]:
        """Extract title text from a title table.
        
        Args:
            table: A title table element
            
        Returns:
            Title text or None (multiple lines are separated by <br/>)
        """
        rows = table.find_all('tr')
        texts = []
        for row in rows:
            cells = row.find_all(['td', 'th'])
            for cell in cells:
                text = self._clean_text(cell.get_text())
                if text:
                    texts.append(text)
        # Join with <br/> to preserve line breaks in multi-line titles
        return '<br/>'.join(texts) if texts else None
    
    def _is_form_table_group(self, tables: List[Tag]) -> bool:
        """Check if a group of tables represents a form/template.
        
        Form tables have heterogeneous column structure and form keywords.
        
        Args:
            tables: List of defFix table elements
            
        Returns:
            True if this appears to be a form table group
        """
        if len(tables) < 3:
            return False
        
        # Collect cell counts from each table
        cell_counts = []
        all_text = []
        
        for table in tables:
            for tr in table.find_all('tr'):
                cells = tr.find_all('td')
                if cells:
                    cell_counts.append(len(cells))
            # Collect text for keyword detection
            all_text.append(table.get_text())
        
        # Check for heterogeneous structure
        unique_counts = set(cell_counts)
        if len(unique_counts) < 4:
            return False  # Not heterogeneous enough
        
        # Check for form keywords
        combined_text = ' '.join(all_text).upper()
        form_keywords = ['ПРИМЕРНА ФОРМА', 'ПРОТОКОЛ', 'НАРЯД', 'УДОСТОВЕРЕНИЕ', 
                         'ДНЕВНИК', 'ЗАПОВЕД', 'ДЕКЛАРАЦИЯ', 'ФОРМУЛЯР']
        has_form_keywords = any(kw in combined_text for kw in form_keywords)
        
        # Very heterogeneous (5+ different column counts) or heterogeneous with keywords
        if len(unique_counts) >= 5:
            return True
        if len(unique_counts) >= 4 and has_form_keywords:
            return True
        
        return False
    
    def _merge_tables(self, tables: List[Tag]) -> Optional[NormalizedTable]:
        """Merge a group of tables into a single normalized table.
        
        Args:
            tables: List of table elements (def + defFix tables)
            
        Returns:
            NormalizedTable or None if merging fails or tables should stay separate
        """
        if not tables:
            return None
        
        # Skip the empty def table, process only defFix tables
        deffix_tables = [t for t in tables if 'defFix' in t.get('class', [])]
        
        if not deffix_tables:
            return None
        
        # Check if this is a form table group - if so, don't merge, keep separate
        if self._is_form_table_group(deffix_tables):
            return None  # Return None to keep original tables
        
        # Separate title tables from content tables
        title = None
        content_tables = []
        
        for table in deffix_tables:
            if self._is_title_table(table):
                # Extract title, don't include in content
                extracted_title = self._extract_table_title(table)
                if extracted_title and not title:
                    title = extracted_title
            else:
                content_tables.append(table)
        
        if not content_tables:
            return None
        
        # Collect all rows from content tables only
        all_rows: List[NormalizedRow] = []
        
        for table in content_tables:
            rows = self._extract_rows(table)
            all_rows.extend(rows)
        
        if not all_rows:
            return None
        
        # Check if this is a real data table (has cells with border codes > 0)
        # Form layout tables have all cells with border code 0
        has_bordered_cells = False
        for row in all_rows:
            for cell in row.cells:
                if cell.border_code > 0:
                    has_bordered_cells = True
                    break
            if has_bordered_cells:
                break
        
        if not has_bordered_cells:
            # This is a form layout table, not a data table - skip normalization
            return None
        
        # Analyze and normalize the merged rows
        normalized = self._analyze_and_normalize(all_rows)
        
        # Set the title if found
        if title:
            normalized.title = title
        
        return normalized
    
    def _extract_rows(self, table: Tag) -> List[NormalizedRow]:
        """Extract rows from a single table element.
        
        Args:
            table: A table element
            
        Returns:
            List of NormalizedRow objects
        """
        rows = []
        
        for tr in table.find_all('tr'):
            cells = []
            
            for cell in tr.find_all(['td', 'th']):
                # Extract text
                text = self._clean_text(cell.get_text())
                
                # Extract width
                width = None
                width_attr = cell.get('width', '')
                if width_attr:
                    match = re.match(r'(\d+)', width_attr)
                    if match:
                        width = int(match.group(1))
                
                # Detect alignment and border from CSS class (e.g., L4, C12, R5)
                align = 'left'
                border_code = 0
                has_bottom_border = True
                has_top_border = False
                classes = cell.get('class', [])
                for cls in classes:
                    if cls and len(cls) >= 2 and cls[0] in self.ALIGN_PREFIXES:
                        align = self.ALIGN_PREFIXES[cls[0]]
                        # Extract border code from the number part
                        num_part = cls[1:]
                        if num_part.isdigit():
                            border_code = int(num_part)
                            # bit0 (1) = BOTTOM border
                            has_bottom_border = bool(border_code & 1)
                            # bit1 (2) = TOP border
                            has_top_border = bool(border_code & 2)
                        break
                
                # Check for colspan/rowspan
                colspan = int(cell.get('colspan', 1))
                rowspan = int(cell.get('rowspan', 1))
                
                # Determine if header
                is_header = cell.name == 'th'
                
                cells.append(NormalizedCell(
                    text=text,
                    colspan=colspan,
                    rowspan=rowspan,
                    width=width,
                    align=align,
                    is_header=is_header,
                    has_bottom_border=has_bottom_border,
                    has_top_border=has_top_border,
                    border_code=border_code
                ))
            
            if cells:
                rows.append(NormalizedRow(cells=cells))
        
        return rows
    
    def _is_form_table(self, rows: List[NormalizedRow]) -> bool:
        """Check if a table group represents a form/template rather than data table.
        
        Form tables (like Приложение 5 - НАРЯД forms) have characteristics:
        - Many different column counts across rows (heterogeneous structure)
        - No row with column numbers (1, 2, 3...)
        - Often contain keywords like "ПРИМЕРНА ФОРМА", "ПРОТОКОЛ", "НАРЯД"
        
        Args:
            rows: List of NormalizedRow objects
            
        Returns:
            True if this appears to be a form table
        """
        if len(rows) < 5:
            return False
        
        # Check for heterogeneous column structure
        cell_counts = [len(row.cells) for row in rows]
        unique_counts = set(cell_counts)
        
        # Forms typically have many different column counts (1, 2, 3, 4, 5, 7...)
        if len(unique_counts) <= 2:
            return False  # Regular table with consistent structure
        
        # Check for form keywords in first few rows
        form_keywords = ['ПРИМЕРНА ФОРМА', 'ПРОТОКОЛ', 'НАРЯД', 'УДОСТОВЕРЕНИЕ', 
                         'ДНЕВНИК', 'ЗАПОВЕД', 'ДЕКЛАРАЦИЯ', 'ФОРМУЛЯР']
        first_rows_text = ' '.join(cell.text for row in rows[:10] for cell in row.cells).upper()
        has_form_keywords = any(kw in first_rows_text for kw in form_keywords)
        
        # If many different column counts AND form keywords -> it's a form
        if len(unique_counts) >= 4 and has_form_keywords:
            return True
        
        # If very heterogeneous (5+ different column counts) -> likely a form even without keywords
        if len(unique_counts) >= 5:
            return True
        
        return False
    
    def _analyze_and_normalize(self, rows: List[NormalizedRow]) -> NormalizedTable:
        """Analyze rows and create a normalized table with proper colspan/rowspan.
        
        Uses width information to detect cells that span multiple columns.
        Uses border information to detect cells that span multiple rows.
        
        Args:
            rows: List of raw rows extracted from tables
            
        Returns:
            NormalizedTable with proper colspan/rowspan and header detection
        """
        if not rows:
            return NormalizedTable()
        
        # Check if this is a form table - skip colspan normalization for forms
        if self._is_form_table(rows):
            return self._create_simple_table(rows, None)
        
        # Find the row with column numbers (usually like "1", "2", "3", "4")
        col_numbers_idx = None
        for i, row in enumerate(rows):
            texts = [cell.text.strip() for cell in row.cells]
            # Check if this row looks like column numbers
            numeric_cells = sum(1 for t in texts if t.isdigit())
            if numeric_cells >= len(texts) * 0.7 and len(texts) >= 2:
                col_numbers_idx = i
                break
        
        # Find the canonical column widths from the most detailed row
        # This is usually the column numbers row or a data row
        canonical_widths = self._find_canonical_widths(rows, col_numbers_idx)
        
        if not canonical_widths:
            # Fallback: use as-is without colspan normalization
            return self._create_simple_table(rows, col_numbers_idx)
        
        # Normalize each row based on canonical widths (colspan)
        normalized_rows = []
        
        for i, row in enumerate(rows):
            is_header = col_numbers_idx is not None and i < col_numbers_idx
            is_column_numbers = i == col_numbers_idx
            
            normalized_cells = self._normalize_row_cells(row.cells, canonical_widths)
            
            for cell in normalized_cells:
                cell.is_header = is_header
            
            normalized_rows.append(NormalizedRow(
                cells=normalized_cells,
                is_header=is_header,
                is_column_numbers=is_column_numbers
            ))
        
        # Apply rowspan based on border information (for headers only)
        self._apply_rowspan(normalized_rows)
        
        # Apply rowspan in body based on row number grouping
        # DISABLED: Testing without body rowspan merging
        # self._apply_body_rowspan(normalized_rows)
        
        return NormalizedTable(rows=normalized_rows)
    
    def _find_canonical_widths(
        self, 
        rows: List[NormalizedRow], 
        col_numbers_idx: Optional[int]
    ) -> List[int]:
        """Find the canonical column widths for the table.
        
        The canonical widths define the base column structure.
        Usually determined from the column numbers row or the most detailed row.
        
        Args:
            rows: All rows in the table
            col_numbers_idx: Index of the column numbers row (if found)
            
        Returns:
            List of column widths in pixels
        """
        # First, try to use the column numbers row
        if col_numbers_idx is not None and col_numbers_idx < len(rows):
            row = rows[col_numbers_idx]
            widths = [cell.width for cell in row.cells if cell.width]
            if len(widths) == len(row.cells) and len(widths) >= 2:
                return widths
        
        # Otherwise, find the row with the most cells that all have widths
        best_row = None
        best_count = 0
        
        for row in rows:
            widths = [cell.width for cell in row.cells if cell.width]
            if len(widths) == len(row.cells) and len(widths) > best_count:
                best_row = row
                best_count = len(widths)
        
        if best_row:
            return [cell.width for cell in best_row.cells]
        
        return []
    
    def _normalize_row_cells(
        self, 
        cells: List[NormalizedCell], 
        canonical_widths: List[int]
    ) -> List[NormalizedCell]:
        """Normalize cells in a row based on canonical column widths.
        
        Calculates colspan for cells that span multiple canonical columns.
        
        Args:
            cells: Cells in the row
            canonical_widths: Canonical column widths
            
        Returns:
            List of normalized cells with proper colspan
        """
        if not canonical_widths:
            return cells
        
        # Calculate cumulative positions for canonical columns
        canonical_positions = [0]
        total = 0
        for w in canonical_widths:
            total += w
            canonical_positions.append(total)
        
        # Normalize each cell
        normalized = []
        current_pos = 0
        
        for cell in cells:
            cell_width = cell.width or 0
            
            if cell_width == 0:
                # No width info - keep as single column but preserve border info
                normalized.append(NormalizedCell(
                    text=cell.text,
                    colspan=1,
                    rowspan=cell.rowspan,
                    align=cell.align,
                    is_header=cell.is_header,
                    has_bottom_border=cell.has_bottom_border,
                    has_top_border=cell.has_top_border,
                    border_code=cell.border_code
                ))
                # Estimate position
                if normalized:
                    current_pos += canonical_widths[min(len(normalized)-1, len(canonical_widths)-1)]
                continue
            
            # Find which canonical columns this cell spans
            cell_end = current_pos + cell_width
            
            # Find start column
            start_col = self._find_nearest_column(current_pos, canonical_positions)
            # Find end column
            end_col = self._find_nearest_column(cell_end, canonical_positions)
            
            colspan = max(1, end_col - start_col)
            
            normalized.append(NormalizedCell(
                text=cell.text,
                colspan=colspan,
                rowspan=cell.rowspan,
                width=cell_width,
                align=cell.align,
                is_header=cell.is_header,
                has_bottom_border=cell.has_bottom_border,
                has_top_border=cell.has_top_border,
                border_code=cell.border_code
            ))
            
            current_pos = cell_end
        
        return normalized
    
    def _apply_rowspan(self, rows: List[NormalizedRow]) -> None:
        """Apply rowspan based on border information.
        
        Cells without bottom border (has_bottom_border=False) should merge
        with cells in the rows below until a cell with bottom border is found.
        
        IMPORTANT: Rowspan is applied ONLY to header rows (is_header=True).
        Data rows may also lack bottom borders, but this is just styling,
        not an indication that cells should merge.
        
        This modifies the rows in place:
        - Cells that start a rowspan get their rowspan attribute increased
        - Cells that are merged into a rowspan above are marked for removal
        
        Args:
            rows: List of NormalizedRow objects to process
        """
        if len(rows) < 2:
            return
        
        # Find the index of the column numbers row (first non-header, non-empty row)
        col_numbers_idx = None
        for i, row in enumerate(rows):
            if row.is_column_numbers:
                col_numbers_idx = i
                break
        
        # If no column numbers row found, try to find where headers end
        if col_numbers_idx is None:
            for i, row in enumerate(rows):
                if not row.is_header:
                    col_numbers_idx = i
                    break
        
        # If still not found, don't apply rowspan
        if col_numbers_idx is None or col_numbers_idx < 1:
            return
        
        # Only process header rows (rows 0 to col_numbers_idx - 1)
        # Include the column numbers row in the rowspan calculation
        header_end_idx = col_numbers_idx  # rowspan can extend to include col numbers row
        
        num_rows = len(rows)
        
        # Build a grid to track which cells span which positions
        # We need to track by logical column position, considering colspan
        grid = {}  # (row_idx, logical_col) -> (cell_idx, cell)
        
        for row_idx, row in enumerate(rows):
            logical_col = 0
            for cell_idx, cell in enumerate(row.cells):
                # Skip positions already occupied by rowspan from above
                while (row_idx, logical_col) in grid:
                    logical_col += 1
                
                # Place this cell
                grid[(row_idx, logical_col)] = (cell_idx, cell)
                
                # Mark all positions this cell occupies (for colspan)
                for c in range(logical_col + 1, logical_col + cell.colspan):
                    grid[(row_idx, c)] = (cell_idx, cell)
                
                logical_col += cell.colspan
        
        # Now scan for header cells without bottom border and calculate rowspan
        cells_to_remove = set()  # (row_idx, cell_idx) of cells to remove
        cells_processed = set()  # (row_idx, cell_idx) of cells already processed for rowspan
        
        # Find max logical column
        max_col = max(col for (_, col) in grid.keys()) if grid else 0
        
        for logical_col in range(max_col + 1):
            row_idx = 0
            # Only process header rows (before column numbers)
            while row_idx < header_end_idx:
                if (row_idx, logical_col) not in grid:
                    row_idx += 1
                    continue
                
                cell_idx, cell = grid[(row_idx, logical_col)]
                
                # Skip if this cell is already marked for removal
                if (row_idx, cell_idx) in cells_to_remove:
                    row_idx += 1
                    continue
                
                # Skip if this cell was already processed (for cells with colspan > 1)
                if (row_idx, cell_idx) in cells_processed:
                    row_idx += 1
                    continue
                
                # Only apply rowspan to header cells
                if not rows[row_idx].is_header:
                    row_idx += 1
                    continue
                
                # Check if cell has no bottom border - should merge with rows below
                if not cell.has_bottom_border:
                    # Find how many rows to span
                    span = 1
                    merge_row = row_idx + 1
                    merged_text_parts = [cell.text] if cell.text.strip() else []
                    
                    # Can merge with header rows and the column numbers row
                    while merge_row <= header_end_idx and merge_row < num_rows:
                        if (merge_row, logical_col) not in grid:
                            break
                        
                        merge_cell_idx, merge_cell = grid[(merge_row, logical_col)]
                        
                        # Accumulate text from merged cells
                        if merge_cell.text.strip():
                            merged_text_parts.append(merge_cell.text)
                        
                        # Mark this cell for removal
                        cells_to_remove.add((merge_row, merge_cell_idx))
                        
                        span += 1
                        
                        # Stop if this cell has a bottom border
                        if merge_cell.has_bottom_border:
                            break
                        
                        merge_row += 1
                    
                    if span > 1:
                        cell.rowspan = span
                        # Combine text with space separator and remove hyphenation
                        combined_text = ' '.join(merged_text_parts)
                        cell.text = self._remove_hyphenation(combined_text)
                        # Mark this cell as processed
                        cells_processed.add((row_idx, cell_idx))
                        # Skip the rows we just processed
                        row_idx = merge_row
                        continue
                
                # Mark cell as processed even if no rowspan was applied
                cells_processed.add((row_idx, cell_idx))
                row_idx += 1
        
        # Remove cells that were merged
        for row_idx in range(num_rows - 1, -1, -1):
            row = rows[row_idx]
            # Find cell indices to remove for this row
            indices_to_remove = sorted(
                [cell_idx for (r, cell_idx) in cells_to_remove if r == row_idx],
                reverse=True
            )
            for cell_idx in indices_to_remove:
                if cell_idx < len(row.cells):
                    row.cells.pop(cell_idx)
    
    def _apply_body_rowspan(self, rows: List[NormalizedRow]) -> None:
        """Apply rowspan in body rows based on row number grouping.
        
        Algorithm:
        1. Find rows with a number in the first column (e.g., "1.", "2.")
        2. Count subsequent rows with empty first column - these belong to the same group
        3. For each column in the group, check if only ONE cell has text
        4. If yes, merge all cells in that column with rowspan
        
        This handles cases where data is split across multiple rows but logically
        belongs together (common in lex.bg tables).
        
        Args:
            rows: List of NormalizedRow objects to process
        """
        if len(rows) < 2:
            return
        
        # Find where body starts (after header rows)
        body_start_idx = 0
        for i, row in enumerate(rows):
            if not row.is_header and not row.is_column_numbers:
                body_start_idx = i
                break
        else:
            # No body rows found
            return
        
        # Process body rows
        num_rows = len(rows)
        row_idx = body_start_idx
        
        while row_idx < num_rows:
            row = rows[row_idx]
            
            # Skip non-body rows
            if row.is_header or row.is_column_numbers:
                row_idx += 1
                continue
            
            # Check if first cell has a row number (e.g., "1.", "2.", "10.")
            if not row.cells:
                row_idx += 1
                continue
            
            first_cell_text = row.cells[0].text.strip()
            
            # Check if it looks like a row number (digit(s) followed by period)
            if not re.match(r'^\d+\.$', first_cell_text):
                row_idx += 1
                continue
            
            # Found a row with a number. Extract the number value
            current_number = int(first_cell_text.rstrip('.'))
            
            # Count following rows with empty first column
            group_size = 1
            next_row_idx = row_idx + 1
            
            while next_row_idx < num_rows:
                next_row = rows[next_row_idx]
                
                # Stop if this is a header row
                if next_row.is_header or next_row.is_column_numbers:
                    break
                
                # Check if first cell is empty
                if not next_row.cells:
                    break
                
                next_first_text = next_row.cells[0].text.strip()
                
                # Stop if first cell has a number (next group starts)
                if re.match(r'^\d+\.$', next_first_text):
                    break
                
                # Stop if first cell is not empty (has other content)
                if next_first_text:
                    break
                
                # This row belongs to the current group
                group_size += 1
                next_row_idx += 1
            
            # Process the group if it has more than 1 row
            if group_size > 1:
                # Additional check: verify that the next row after the group has
                # a number that is exactly current_number + 1, or we're at the end of table
                # This prevents merging when numbering restarts (e.g., after a note)
                should_merge = False
                row_after_group_idx = row_idx + group_size
                
                if row_after_group_idx >= num_rows:
                    # We're at the end of the table - OK to merge
                    should_merge = True
                else:
                    row_after_group = rows[row_after_group_idx]
                    if row_after_group.cells:
                        after_group_text = row_after_group.cells[0].text.strip()
                        match = re.match(r'^(\d+)\.$', after_group_text)
                        if match:
                            next_number = int(match.group(1))
                            # Check if next number is current + 1
                            if next_number == current_number + 1:
                                should_merge = True
                        # If no number found (empty or other content), check if it's empty
                        # which might mean end of numbered section
                        elif not after_group_text:
                            # Empty cell after group - could be end of section, allow merge
                            should_merge = True
                
                if should_merge:
                    self._merge_body_group(rows, row_idx, group_size)
            
            # Move to next potential group
            row_idx += group_size
    
    def _merge_body_group(self, rows: List[NormalizedRow], start_idx: int, group_size: int) -> None:
        """Merge cells in a body row group based on text content.
        
        Algorithm:
        1. Check each column (EXCEPT the first one) to see if it has only ONE cell with text
        2. If ANY such column qualifies, merge ALL columns with rowspan
        3. The first column is excluded because it always has only one value (the row number)
        
        Args:
            rows: All table rows
            start_idx: Starting row index of the group
            group_size: Number of rows in the group
        """
        # Get the number of columns from the first row of the group
        first_row = rows[start_idx]
        num_cols = len(first_row.cells)
        
        if num_cols < 2:
            return
        
        # Check if ANY column (except the first) has only one cell with text
        has_mergeable_column = False
        
        # Start from column 1 (skip column 0 which is the row number)
        for col_idx in range(1, num_cols):
            cells_with_text_count = 0
            
            for offset in range(group_size):
                row = rows[start_idx + offset]
                if col_idx >= len(row.cells):
                    continue
                
                cell = row.cells[col_idx]
                if cell.text.strip():
                    cells_with_text_count += 1
            
            # If this column has 0 or 1 cells with text, it qualifies
            if cells_with_text_count <= 1:
                has_mergeable_column = True
                break
        
        # If no column qualifies for merge, don't do anything
        if not has_mergeable_column:
            return
        
        # Merge ALL columns since at least one non-first column qualified
        for col_idx in range(num_cols):
            # Collect text from all cells in this column for this group
            all_texts = []
            
            for offset in range(group_size):
                row = rows[start_idx + offset]
                if col_idx >= len(row.cells):
                    continue
                
                cell = row.cells[col_idx]
                text = cell.text.strip()
                if text:
                    all_texts.append(text)
            
            # Merge all cells in this column
            first_cell = first_row.cells[col_idx]
            
            # Set rowspan
            first_cell.rowspan = group_size
            
            # Combine all text
            combined_text = ' '.join(all_texts)
            first_cell.text = self._remove_hyphenation(combined_text)
            
            # Mark cells in subsequent rows for removal
            for offset in range(1, group_size):
                row = rows[start_idx + offset]
                if col_idx < len(row.cells):
                    row.cells[col_idx].rowspan = 0  # Mark for removal
        
        # Remove cells marked with rowspan=0
        for offset in range(1, group_size):
            row = rows[start_idx + offset]
            row.cells = [cell for cell in row.cells if cell.rowspan != 0]

    def _find_nearest_column(self, position: int, canonical_positions: List[int]) -> int:
        """Find the nearest canonical column for a position.
        
        Args:
            position: Position in pixels
            canonical_positions: Cumulative positions of canonical columns
            
        Returns:
            Column index (0-based)
        """
        # Allow some tolerance (5 pixels)
        tolerance = 5
        
        best_col = 0
        best_diff = abs(canonical_positions[0] - position)
        
        for i, pos in enumerate(canonical_positions):
            diff = abs(pos - position)
            if diff < best_diff:
                best_col = i
                best_diff = diff
        
        return best_col

    def _create_simple_table(
        self, 
        rows: List[NormalizedRow], 
        col_numbers_idx: Optional[int]
    ) -> NormalizedTable:
        """Create a simple table without colspan normalization.
        
        Used as fallback when width-based normalization isn't possible.
        """
        normalized_rows = []
        
        for i, row in enumerate(rows):
            is_header = col_numbers_idx is not None and i < col_numbers_idx
            is_column_numbers = i == col_numbers_idx
            
            for cell in row.cells:
                cell.is_header = is_header
            
            normalized_rows.append(NormalizedRow(
                cells=row.cells,
                is_header=is_header,
                is_column_numbers=is_column_numbers
            ))
        
        return NormalizedTable(rows=normalized_rows)
    
    def _clean_text(self, text: str) -> str:
        """Clean text content from HTML element."""
        if not text:
            return ""
        # Normalize whitespace
        text = re.sub(r'\s+', ' ', text)
        return text.strip()
    
    def _remove_hyphenation(self, text: str) -> str:
        """Remove hyphenation from merged text.
        
        When text from multiple rows is merged, words may be split across rows
        with hyphens (syllable breaks). This method joins them back together.
        
        Examples:
            'Опаковка и раз- фасовка' -> 'Опаковка и разфасовка'
            'въздухо-водоне- проницаеми' -> 'въздухо-водонепроницаеми'
            'хо-водонепро- ницаеми' -> 'хо-водонепроницаеми'
        
        Args:
            text: Text that may contain hyphenation breaks
            
        Returns:
            Text with hyphenation removed
        """
        if not text:
            return text
        
        # Pattern: word ending with hyphen, followed by space(s), followed by lowercase letter
        # This matches syllable breaks like "раз- фасовка" but not compound words like "въздухо-водо"
        # The key insight is that syllable breaks have a space after the hyphen
        result = re.sub(r'(\w)- +(\w)', r'\1\2', text)
        
        return result


def normalize_tables_in_html(html_content: str) -> str:
    """Convenience function to normalize tables in HTML content.
    
    Args:
        html_content: HTML string containing tables
        
    Returns:
        HTML string with normalized tables
    """
    normalizer = TableNormalizer()
    return normalizer.normalize_html(html_content)


# Test the normalizer
if __name__ == "__main__":
    import sys
    from pathlib import Path
    
    if len(sys.argv) > 1:
        # Test with a file
        file_path = Path(sys.argv[1])
        if file_path.exists():
            with open(file_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
            
            normalizer = TableNormalizer()
            normalized = normalizer.normalize_html(html_content)
            
            # Write output
            output_path = file_path.with_suffix('.normalized.html')
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(normalized)
            
            print(f"Normalized HTML written to: {output_path}")
        else:
            print(f"File not found: {file_path}")
    else:
        print("Usage: python table_normalizer.py <html_file>")

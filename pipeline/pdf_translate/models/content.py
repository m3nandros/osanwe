"""Content data models for PDF documents."""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Union

from .geometry import BoundingBox


class BlockType(Enum):
    """Types of text blocks in a document."""
    # Title page
    DOCUMENT_TITLE = "document_title"
    AUTHOR_LIST = "author_list"
    AFFILIATION = "affiliation"
    ABSTRACT_LABEL = "abstract_label"
    ABSTRACT_TEXT = "abstract_text"
    KEYWORDS_LABEL = "keywords_label"
    KEYWORDS_TEXT = "keywords_text"
    
    # Metadata (NOT translated)
    DOI = "doi"
    JOURNAL_INFO = "journal_info"
    DATES = "dates"
    COPYRIGHT = "copyright"
    LICENSE = "license"
    CORRESPONDENCE = "correspondence"
    
    # Main content
    SECTION_HEADING = "section_heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    FIGURE_CAPTION = "figure_caption"
    TABLE_CAPTION = "table_caption"
    EQUATION = "equation"
    FOOTNOTE = "footnote"
    
    # References
    REFERENCES_HEADING = "references_heading"
    REFERENCE_ITEM = "reference_item"
    
    # Special sections
    ACKNOWLEDGEMENTS = "acknowledgements"
    AUTHOR_CONTRIBUTIONS = "author_contributions"
    FUNDING = "funding"
    CONFLICTS = "conflicts"
    
    # Header/Footer
    PAGE_HEADER = "page_header"
    PAGE_FOOTER = "page_footer"
    PAGE_NUMBER = "page_number"
    
    UNKNOWN = "unknown"


# Translation rules: what to translate and what to preserve
TRANSLATION_RULES = {
    # TRANSLATE
    BlockType.DOCUMENT_TITLE: True,
    BlockType.ABSTRACT_TEXT: True,
    BlockType.KEYWORDS_TEXT: True,
    BlockType.SECTION_HEADING: True,
    BlockType.PARAGRAPH: True,
    BlockType.LIST_ITEM: True,
    BlockType.FIGURE_CAPTION: True,
    BlockType.TABLE_CAPTION: True,
    BlockType.FOOTNOTE: True,
    BlockType.ACKNOWLEDGEMENTS: True,
    
    # DO NOT TRANSLATE
    BlockType.DOI: False,
    BlockType.JOURNAL_INFO: False,
    BlockType.DATES: False,
    BlockType.COPYRIGHT: False,
    BlockType.LICENSE: False,
    BlockType.CORRESPONDENCE: False,
    BlockType.REFERENCE_ITEM: False,
    BlockType.EQUATION: False,
    BlockType.AUTHOR_LIST: False,
    BlockType.AFFILIATION: False,
    BlockType.PAGE_HEADER: False,
    BlockType.PAGE_FOOTER: False,
    BlockType.PAGE_NUMBER: False,
    
    # OPTIONAL (configurable)
    BlockType.ABSTRACT_LABEL: "optional",
    BlockType.KEYWORDS_LABEL: "optional",
    BlockType.REFERENCES_HEADING: "optional",
}


@dataclass
class TextStyle:
    """Text styling information."""
    font_name: str = ""
    font_size: float = 12.0
    is_bold: bool = False
    is_italic: bool = False
    is_monospace: bool = False
    color: str = "#000000"


@dataclass
class PositionedSpan:
    """A single span of text with precise position and style."""
    text: str
    x: float
    y: float
    font_size: float
    is_bold: bool = False
    is_italic: bool = False
    is_monospace: bool = False
    is_math: bool = False
    color: str = "#000000"
    width: float = 0.0

@dataclass
class TextBlock:
    """A block of text with position and style."""
    text: str
    page_number: int
    bbox: BoundingBox
    style: TextStyle
    block_type: BlockType = BlockType.UNKNOWN
    is_math: bool = False
    confidence: float = 1.0
    origin_x: float = 0.0  # Precise baseline origin X
    origin_y: float = 0.0  # Precise baseline origin Y
    positioned_spans: List[PositionedSpan] = field(default_factory=list)

@dataclass
class AuthorInfo:
    """Author information."""
    name: str
    affiliations: List[int] = field(default_factory=list)  # Indices into affiliations list
    email: Optional[str] = None
    orcid: Optional[str] = None
    is_corresponding: bool = False
    row_index: int = 0  # Row index for grid layout (0-based)
    col_index: int = 0  # Column index within row
    x_position: float = 0.0  # X coordinate in PDF for positioning
    y_baseline: float = 0.0  # Y coordinate (baseline)
    font_size: float = 11.0  # Font size
    spans: List[PositionedSpan] = field(default_factory=list) # Precise spans


@dataclass
class Section:
    """A document section with heading and content."""
    level: int  # 1 = H1, 2 = H2, etc.
    title: str
    content: List[Union[str, "TextBlock", "Section", "Figure", "Table", "Equation", "Reference"]] = field(default_factory=list)
    page_number: int = 0  # Page where section starts


@dataclass
class Figure:
    """A figure with caption and one or more images."""
    number: str  # "1", "2a", etc.
    caption: str
    image_paths: List[str] = field(default_factory=list)
    page_number: int = 0
    bbox: Optional[BoundingBox] = None
    image_bboxes: List[BoundingBox] = field(default_factory=list)


@dataclass
class Table:
    """A table with caption and content."""
    number: str
    caption: str
    headers: List[str] = field(default_factory=list)
    rows: List[List[str]] = field(default_factory=list)
    page_number: int = 0
    bbox: Optional[BoundingBox] = None
    image_path: str = ""


@dataclass
class Equation:
    """A mathematical equation."""
    number: Optional[str] = None  # "(1)", "(2)", etc.
    latex: str = ""  # LaTeX code if available
    is_inline: bool = False
    image_path: Optional[str] = None  # Fallback: equation as image


@dataclass
class Footnote:
    """A footnote."""
    marker: str  # "1", "*", "†", etc.
    text: str
    page_number: int = 0
    baseline_y: float = 0.0
    spans: List[PositionedSpan] = field(default_factory=list) # Precise spans


@dataclass
class Reference:
    """A bibliography reference (NOT translated)."""
    number: str  # "[1]", "1.", etc.
    text: str  # Full reference text


@dataclass
class DocumentContent:
    """Complete document content with semantic structure."""
    # Title page
    title: str = ""
    authors: List[AuthorInfo] = field(default_factory=list)
    affiliations: List[str] = field(default_factory=list)
    abstract: str = ""
    keywords: List[str] = field(default_factory=list)
    
    # Metadata (NOT translated)
    doi: Optional[str] = None
    journal_name: Optional[str] = None
    received_date: Optional[str] = None
    accepted_date: Optional[str] = None
    published_date: Optional[str] = None
    copyright_text: Optional[str] = None
    license_text: Optional[str] = None
    
    # Main content
    sections: List[Section] = field(default_factory=list)
    
    # Floating elements
    figures: List[Figure] = field(default_factory=list)
    tables: List[Table] = field(default_factory=list)
    equations: List[Equation] = field(default_factory=list)
    footnotes: List[Footnote] = field(default_factory=list)
    
    # References (NOT translated)
    references: List[Reference] = field(default_factory=list)
    
    # Additional sections
    acknowledgements: Optional[str] = None
    author_contributions: Optional[str] = None
    funding: Optional[str] = None
    conflicts: Optional[str] = None
    
    # Raw text blocks for debugging/fallback
    raw_blocks: List[TextBlock] = field(default_factory=list)

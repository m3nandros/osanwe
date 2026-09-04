"""Geometry data models for PDF documents."""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class BoundingBox:
    """Bounding box in PDF points (1 pt = 1/72 inch)."""
    x0: float
    y0: float
    x1: float
    y1: float
    
    @property
    def width(self) -> float:
        return self.x1 - self.x0
    
    @property
    def height(self) -> float:
        return self.y1 - self.y0
    
    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2
    
    @property
    def center_y(self) -> float:
        return (self.y0 + self.y1) / 2
    
    @property
    def area(self) -> float:
        return self.width * self.height
    
    def contains(self, other: "BoundingBox") -> bool:
        """Check if this bbox contains another bbox."""
        return (
            self.x0 <= other.x0 and
            self.y0 <= other.y0 and
            self.x1 >= other.x1 and
            self.y1 >= other.y1
        )
    
    def overlaps(self, other: "BoundingBox") -> bool:
        """Check if this bbox overlaps with another."""
        return not (
            self.x1 < other.x0 or
            self.x0 > other.x1 or
            self.y1 < other.y0 or
            self.y0 > other.y1
        )
    
    def to_tuple(self) -> tuple:
        return (self.x0, self.y0, self.x1, self.y1)
    
    @classmethod
    def from_tuple(cls, t: tuple) -> "BoundingBox":
        return cls(x0=t[0], y0=t[1], x1=t[2], y1=t[3])


@dataclass
class PageGeometry:
    """Geometry information for a single page."""
    page_number: int
    width_pt: float
    height_pt: float
    
    margin_top_pt: float
    margin_bottom_pt: float
    margin_left_pt: float
    margin_right_pt: float
    
    num_columns: int = 1
    column_width_pt: float = 0.0
    column_gap_pt: float = 0.0
    
    header_height_pt: float = 0.0
    footer_height_pt: float = 0.0
    
    is_title_page: bool = False
    
    @property
    def content_width(self) -> float:
        """Width of content area (page width minus margins)."""
        return self.width_pt - self.margin_left_pt - self.margin_right_pt
    
    @property
    def content_height(self) -> float:
        """Height of content area (page height minus margins)."""
        return self.height_pt - self.margin_top_pt - self.margin_bottom_pt


@dataclass
class TitlePageSpacing:
    """Precise spacing and positioning values extracted from title page."""
    # Absolute Y coordinates (baselines for text, center for rules)
    # Coordinates are points from top-left of the page
    title_y_pt: float = 0.0
    authors_y_pt: List[float] = field(default_factory=list) # Baseline for each author row
    abstract_y_pt: float = 0.0       # Heading baseline
    abstract_text_y_pt: float = 0.0  # First line baseline
    footnote_text_y_pt: float = 0.0  # First footnote baseline
    footnote_baselines_y_pt: List[float] = field(default_factory=list) # Baselines for each footnote
    
    # Horizontal rules
    # Each rule: {"y": float, "thickness": float, "x0": float, "x1": float}
    rules: List[Dict[str, float]] = field(default_factory=list)
    
    # Header/Footer elements (like the red arXiv text and conference info)
    # Each element: {"text": str, "x": float, "y": float, "font_size": float, "color": str, "dir": Tuple[float, float], "is_bold": bool}
    header_elements: List[Dict] = field(default_factory=list)
    
    # Typography
    title_font_size: float = 17.2
    author_font_size: float = 10.0
    author_leading: float = 12.0
    
    abstract_heading_font_size: float = 12.0
    abstract_font_size: float = 10.0
    abstract_leading: float = 11.0
    abstract_x_pt: float = 108.0
    abstract_width_pt: float = 396.0
    
    footnote_font_size: float = 9.0
    footnote_leading: float = 10.0
    
    # Legacy/Relative fields (kept for fallback where needed)
    header_to_upper_rule_gap: float = 10.0
    header_to_title_gap: float = 36.0  
    title_to_line_gap: float = 12.0
    line_to_authors_gap: float = 55.0
    author_row_gap: float = 16.0
    authors_to_abstract_gap: float = 29.0
    abstract_heading_to_text_gap: float = 16.0
    abstract_to_footnotes_gap: float = 20.0
    footnote_rule_to_text_gap: float = 6.0
    
    upper_line_thickness: float = 0.0
    upper_line_y_pt: float = 0.0
    title_line_thickness: float = 1.0
    footnote_line_thickness: float = 0.4
    footnote_line_width_ratio: float = 0.35
    footnote_rule_y_pt: float = 0.0


@dataclass
class DocumentGeometry:
    """Geometry information for entire document."""
    pages: List[PageGeometry]
    
    paper_format: str = "custom"  # "a4", "letter", "custom"
    paper_width_pt: float = 0.0
    paper_height_pt: float = 0.0
    
    default_num_columns: int = 1
    default_margins: Dict[str, float] = field(default_factory=dict)
    
    # Title page spacing (extracted from PDF)
    title_page_spacing: TitlePageSpacing = field(default_factory=TitlePageSpacing)
    
    # Detected page number baseline (Big Points from top)
    page_number_baseline_bp: float = 0.0
    
    def get_page(self, page_number: int) -> PageGeometry:
        """Get geometry for specific page."""
        for page in self.pages:
            if page.page_number == page_number:
                return page
        raise ValueError(f"Page {page_number} not found")
    
    @property
    def num_pages(self) -> int:
        return len(self.pages)
    
    def detect_paper_format(self) -> str:
        """Detect paper format from dimensions."""
        if not self.pages:
            return "custom"
        
        w, h = self.paper_width_pt, self.paper_height_pt
        
        # A4: 595 × 842 points
        if abs(w - 595) < 5 and abs(h - 842) < 5:
            return "a4"
        
        # Letter: 612 × 792 points
        if abs(w - 612) < 5 and abs(h - 792) < 5:
            return "letter"
        
        return "custom"

"""Visual element data models for PDF documents."""

from dataclasses import dataclass, field
from typing import List, Optional

from .geometry import BoundingBox


@dataclass
class ImageElement:
    """An image extracted from the PDF."""
    page_number: int
    bbox: BoundingBox
    
    image_data: bytes = field(repr=False)
    image_format: str = "png"  # "png", "jpeg", etc.
    dpi: int = 72
    
    element_type: str = "figure"  # "figure", "logo", "icon", "decorative"
    positioning: str = "float"  # "absolute", "float"
    
    extracted_path: Optional[str] = None  # Path to saved file


@dataclass
class LineElement:
    """A line (vector graphic) from the PDF."""
    page_number: int
    x1: float
    y1: float
    x2: float
    y2: float
    
    stroke_width: float = 1.0
    stroke_color: str = "#000000"  # hex color
    
    line_type: str = "horizontal"  # "horizontal", "vertical", "diagonal"
    semantic_role: str = "decorative"  # "title_underline", "section_separator", etc.


@dataclass
class BoxElement:
    """A rectangle/box from the PDF."""
    page_number: int
    bbox: BoundingBox
    
    stroke_width: float = 0.0
    stroke_color: str = "#000000"
    fill_color: Optional[str] = None
    
    semantic_role: str = "decorative"  # "abstract_box", "warning_box", etc.


@dataclass
class HeaderFooterElement:
    """Header or footer content."""
    page_number: int
    position: str  # "header" or "footer"
    content: str
    bbox: BoundingBox
    
    font_size: float = 10.0
    font_family: str = ""
    font_style: str = "normal"  # "normal", "italic", "bold"
    alignment: str = "center"  # "left", "center", "right"
    text_color: str = "#000000"  # Hex color of text
    
    has_page_number: bool = False


@dataclass
class ExtractedElements:
    """All visual elements extracted from a document."""
    images: List[ImageElement] = field(default_factory=list)
    lines: List[LineElement] = field(default_factory=list)
    boxes: List[BoxElement] = field(default_factory=list)
    headers_footers: List[HeaderFooterElement] = field(default_factory=list)
    
    def get_page_images(self, page_number: int) -> List[ImageElement]:
        return [img for img in self.images if img.page_number == page_number]
    
    def get_absolute_images(self) -> List[ImageElement]:
        """Get images that should be absolutely positioned (logos, etc.)."""
        return [img for img in self.images if img.positioning == "absolute"]
    
    def get_float_images(self) -> List[ImageElement]:
        """Get images that should float (figures)."""
        return [img for img in self.images if img.positioning == "float"]

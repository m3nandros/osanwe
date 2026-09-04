"""Detection data models for DocLayout-YOLO and cross-page analysis."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Tuple

from .geometry import BoundingBox


# DocLayout-YOLO class mapping
DOCLAYOUT_YOLO_CLASSES = {
    0: "title",
    1: "plain_text",
    2: "abandon",
    3: "figure",
    4: "figure_caption",
    5: "table",
    6: "table_caption",
    7: "table_footnote",
    8: "isolate_formula",
    9: "formula_caption",
    10: "page_header",
    11: "page_footer",
    12: "page_number",
    13: "abstract",
    14: "reference",
    15: "footnote",
}


@dataclass
class DetectedElement:
    """A single element detected by DocLayout-YOLO."""
    page_number: int
    class_id: int
    class_name: str
    bbox: BoundingBox
    confidence: float  # 0.0 - 1.0
    
    @property
    def area(self) -> float:
        return self.bbox.area
    
    @property
    def aspect_ratio(self) -> float:
        if self.bbox.height > 0:
            return self.bbox.width / self.bbox.height
        return 0.0


@dataclass
class PageDetectionResult:
    """Detection results for a single page."""
    page_number: int
    image_width: int
    image_height: int
    pdf_width_pt: float
    pdf_height_pt: float
    
    elements: List[DetectedElement] = field(default_factory=list)
    
    # Grouped by type for convenience
    @property
    def titles(self) -> List[DetectedElement]:
        return [e for e in self.elements if e.class_name == "title"]
    
    @property
    def paragraphs(self) -> List[DetectedElement]:
        return [e for e in self.elements if e.class_name == "plain_text"]
    
    @property
    def figures(self) -> List[DetectedElement]:
        return [e for e in self.elements if e.class_name == "figure"]
    
    @property
    def figure_captions(self) -> List[DetectedElement]:
        return [e for e in self.elements if e.class_name == "figure_caption"]
    
    @property
    def tables(self) -> List[DetectedElement]:
        return [e for e in self.elements if e.class_name == "table"]
    
    @property
    def table_captions(self) -> List[DetectedElement]:
        return [e for e in self.elements if e.class_name == "table_caption"]
    
    @property
    def formulas(self) -> List[DetectedElement]:
        return [e for e in self.elements if e.class_name in ["isolate_formula", "formula_caption"]]
    
    @property
    def headers(self) -> List[DetectedElement]:
        return [e for e in self.elements if e.class_name == "page_header"]
    
    @property
    def footers(self) -> List[DetectedElement]:
        return [e for e in self.elements if e.class_name == "page_footer"]
    
    @property
    def footnotes(self) -> List[DetectedElement]:
        return [e for e in self.elements if e.class_name in ["footnote", "table_footnote"]]
    
    @property
    def references(self) -> List[DetectedElement]:
        return [e for e in self.elements if e.class_name == "reference"]
    
    @property
    def abstracts(self) -> List[DetectedElement]:
        return [e for e in self.elements if e.class_name == "abstract"]


@dataclass
class DocumentDetectionResult:
    """Detection results for entire document."""
    pages: List[PageDetectionResult] = field(default_factory=list)
    
    @property
    def total_elements(self) -> int:
        return sum(len(p.elements) for p in self.pages)
    
    @property
    def elements_by_type(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for page in self.pages:
            for elem in page.elements:
                counts[elem.class_name] = counts.get(elem.class_name, 0) + 1
        return counts
    
    def get_page(self, page_number: int) -> PageDetectionResult:
        for page in self.pages:
            if page.page_number == page_number:
                return page
        raise ValueError(f"Page {page_number} not found")


class ContinuityType(Enum):
    """Type of content continuation across pages."""
    PARAGRAPH = "paragraph"
    SENTENCE = "sentence"
    FOOTNOTE = "footnote"
    TABLE = "table"
    REFERENCE_LIST = "references"
    FIGURE_CAPTION = "caption"


@dataclass
class CrossPageLink:
    """A link between elements on adjacent pages."""
    # Source element (on previous page)
    source_page: int
    source_element_index: int
    source_element_type: str
    source_text_end: str  # Last ~50 characters
    
    # Target element (on next page)
    target_page: int
    target_element_index: int
    target_element_type: str
    target_text_start: str  # First ~50 characters
    
    continuity_type: ContinuityType = ContinuityType.PARAGRAPH
    confidence: float = 0.0
    indicators: List[str] = field(default_factory=list)


@dataclass
class SemanticUnit:
    """A semantic unit that may span multiple pages."""
    unit_id: str
    unit_type: str  # "paragraph", "section", "footnote", etc.
    
    # List of (page_number, element_index) tuples
    elements: List[Tuple[int, int]] = field(default_factory=list)
    
    full_text: str = ""
    
    continues_from_previous: bool = False
    continues_to_next: bool = False


@dataclass
class CrossPageAnalysisResult:
    """Results of cross-page content analysis."""
    links: List[CrossPageLink] = field(default_factory=list)
    semantic_units: List[SemanticUnit] = field(default_factory=list)
    
    # Map: (page, element_index) -> semantic_unit_id
    element_to_unit: Dict[Tuple[int, int], str] = field(default_factory=dict)
    
    def get_unit_for_element(self, page: int, element_index: int) -> str:
        return self.element_to_unit.get((page, element_index), "")

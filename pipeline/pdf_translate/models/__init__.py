"""Data models for PDF Translation Pipeline."""

from .geometry import BoundingBox, PageGeometry, DocumentGeometry, TitlePageSpacing
from .elements import (
    ImageElement,
    LineElement, 
    BoxElement,
    HeaderFooterElement,
    ExtractedElements,
)
from .content import (
    TextStyle,
    TextBlock,
    BlockType,
    TRANSLATION_RULES,
    AuthorInfo,
    Section,
    Figure,
    Table,
    Equation,
    Footnote,
    Reference,
    DocumentContent,
    PositionedSpan,
)
from .detection import (
    DOCLAYOUT_YOLO_CLASSES,
    DetectedElement,
    PageDetectionResult,
    DocumentDetectionResult,
    ContinuityType,
    CrossPageLink,
    SemanticUnit,
    CrossPageAnalysisResult,
)

__all__ = [
    # Geometry
    "BoundingBox",
    "PageGeometry", 
    "DocumentGeometry",
    "TitlePageSpacing",
    # Elements
    "ImageElement",
    "LineElement",
    "BoxElement",
    "HeaderFooterElement",
    "ExtractedElements",
    # Content
    "TextStyle",
    "TextBlock",
    "BlockType",
    "TRANSLATION_RULES",
    "AuthorInfo",
    "Section",
    "Figure",
    "Table",
    "Equation",
    "Footnote",
    "Reference",
    "DocumentContent",
    "PositionedSpan",
    # Detection
    "DOCLAYOUT_YOLO_CLASSES",
    "DetectedElement",
    "PageDetectionResult",
    "DocumentDetectionResult",
    "ContinuityType",
    "CrossPageLink",
    "SemanticUnit",
    "CrossPageAnalysisResult",
]

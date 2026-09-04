"""Detection modules for PDF layout analysis."""

from .layout_detector import LayoutDetector
from .cross_page_analyzer import CrossPageAnalyzer

__all__ = ["LayoutDetector", "CrossPageAnalyzer"]

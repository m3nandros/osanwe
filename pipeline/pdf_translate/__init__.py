"""
PDF Translation Pipeline with Layout Preservation.

Translates PDF scientific papers while preserving visual layout.
Architecture: PDF → Layout Analysis → LaTeX Template + Translated Text → PDF
"""

from .pipeline import PDFTranslationPipeline

__all__ = ["PDFTranslationPipeline"]

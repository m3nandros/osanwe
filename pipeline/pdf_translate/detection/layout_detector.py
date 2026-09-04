"""
Layout Detector using DocLayout-YOLO.

Detects document structure elements: titles, paragraphs, figures, tables, 
formulas, headers, footers, footnotes, references, etc.
"""

import logging
from pathlib import Path
from typing import Optional, List

import fitz  # PyMuPDF
import numpy as np

from ..models import (
    BoundingBox,
    DetectedElement,
    PageDetectionResult,
    DocumentDetectionResult,
    DOCLAYOUT_YOLO_CLASSES,
)

logger = logging.getLogger(__name__)


class LayoutDetector:
    """
    Document layout detector using DocLayout-YOLO model.
    
    Detects 16 types of document elements with bounding boxes and confidence scores.
    """
    
    def __init__(
        self,
        model_path: Optional[str] = None,
        device: str = "auto"
    ):
        """
        Initialize the layout detector.
        
        Args:
            model_path: Path to YOLO weights. If None, downloads from HuggingFace.
            device: Device to run on ("auto", "cuda", "cpu")
        """
        self.model = None
        self.model_path = model_path
        self.device = device
        self._model_loaded = False
        
    def _ensure_model_loaded(self):
        """Lazy-load the model on first use."""
        if self._model_loaded:
            return
        
        try:
            from doclayout_yolo import YOLOv10
        except ImportError:
            raise ImportError(
                "doclayout-yolo is required. Install with: pip install doclayout-yolo"
            )
        
        # Use provided path or default local model
        if self.model_path is None:
            # Look for model in assets/models/
            import os
            project_root = Path(__file__).parent.parent.parent.parent
            default_model = project_root / "assets" / "models" / "doclayout_yolo_docstructbench.pt"
            
            if default_model.exists():
                self.model_path = str(default_model)
            else:
                raise FileNotFoundError(
                    f"Model not found at {default_model}. "
                    "Please download from https://huggingface.co/juliozhao/DocLayout-YOLO-DocStructBench"
                )
        
        logger.info(f"Loading DocLayout-YOLO model: {self.model_path}")
        self.model = YOLOv10(self.model_path)
        self._model_loaded = True
        logger.info("Model loaded successfully")
    
    def detect_document(
        self,
        doc: fitz.Document,
        confidence_threshold: float = 0.25,
        dpi: int = 150
    ) -> DocumentDetectionResult:
        """
        Detect layout elements in entire document.
        
        Args:
            doc: PyMuPDF Document object
            confidence_threshold: Minimum confidence for detections
            dpi: DPI for page rendering (higher = more accurate but slower)
        
        Returns:
            DocumentDetectionResult with all detected elements
        """
        self._ensure_model_loaded()
        
        page_results = []
        
        for page_num in range(len(doc)):
            page = doc[page_num]
            result = self.detect_page(
                page, 
                page_num, 
                confidence_threshold, 
                dpi
            )
            page_results.append(result)
            
            logger.debug(
                f"Page {page_num}: detected {len(result.elements)} elements"
            )
        
        result = DocumentDetectionResult(pages=page_results)
        
        logger.info(
            f"Document detection complete: {result.total_elements} elements, "
            f"breakdown: {result.elements_by_type}"
        )
        
        return result
    
    def detect_page(
        self,
        page: fitz.Page,
        page_number: int,
        confidence_threshold: float = 0.25,
        dpi: int = 150
    ) -> PageDetectionResult:
        """
        Detect layout elements on a single page.
        
        Args:
            page: PyMuPDF Page object
            page_number: Page number (0-indexed)
            confidence_threshold: Minimum confidence for detections
            dpi: DPI for rendering
        
        Returns:
            PageDetectionResult with detected elements
        """
        self._ensure_model_loaded()
        
        # Render page to image
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat)
        
        # Convert to numpy array
        img = np.frombuffer(pix.samples, dtype=np.uint8)
        img = img.reshape(pix.height, pix.width, pix.n)
        
        # RGBA -> RGB if needed
        if pix.n == 4:
            img = img[:, :, :3]
        
        # Run detection
        results = self.model.predict(img, conf=confidence_threshold, verbose=False)
        
        # Parse results
        elements: List[DetectedElement] = []
        
        # Scale factors for coordinate conversion
        scale_x = page.rect.width / pix.width
        scale_y = page.rect.height / pix.height
        
        for box in results[0].boxes:
            class_id = int(box.cls[0])
            class_name = DOCLAYOUT_YOLO_CLASSES.get(class_id, "unknown")
            
            # Skip "abandon" elements
            if class_name == "abandon":
                continue
            
            # Convert coordinates from image space to PDF points
            x0, y0, x1, y1 = box.xyxy[0].tolist()
            
            bbox = BoundingBox(
                x0=x0 * scale_x,
                y0=y0 * scale_y,
                x1=x1 * scale_x,
                y1=y1 * scale_y
            )
            
            elements.append(DetectedElement(
                page_number=page_number,
                class_id=class_id,
                class_name=class_name,
                bbox=bbox,
                confidence=float(box.conf[0])
            ))
        
        # Sort elements by reading order (top to bottom, left to right)
        elements.sort(key=lambda e: (e.bbox.y0, e.bbox.x0))
        
        return PageDetectionResult(
            page_number=page_number,
            image_width=pix.width,
            image_height=pix.height,
            pdf_width_pt=page.rect.width,
            pdf_height_pt=page.rect.height,
            elements=elements
        )
    
    def detect_from_path(
        self,
        pdf_path: str,
        confidence_threshold: float = 0.25,
        dpi: int = 150
    ) -> DocumentDetectionResult:
        """
        Convenience method to detect from a file path.
        
        Args:
            pdf_path: Path to PDF file
            confidence_threshold: Minimum confidence for detections
            dpi: DPI for rendering
        
        Returns:
            DocumentDetectionResult
        """
        doc = fitz.open(pdf_path)
        try:
            return self.detect_document(doc, confidence_threshold, dpi)
        finally:
            doc.close()

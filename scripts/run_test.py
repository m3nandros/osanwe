#!/usr/bin/env python3
"""Quick test script with explicit output."""

import sys
import logging

# Setup logging to file
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("/tmp/pipeline_test.log"),
        logging.StreamHandler(sys.stderr)
    ]
)

logger = logging.getLogger(__name__)

def main():
    with open("/tmp/pipeline_output.txt", "w") as f:
        f.write("Starting test...\n")
        
        try:
            import fitz
            f.write(f"PyMuPDF: {fitz.version}\n")
            
            # Open PDF
            doc = fitz.open("out_dir/1706.03762/original.pdf")
            f.write(f"PDF pages: {len(doc)}\n")
            
            # Test Layout Detector
            f.write("Loading LayoutDetector...\n")
            from pipeline.pdf_translate.detection import LayoutDetector
            
            detector = LayoutDetector()
            f.write("Running detection (this may download the model)...\n")
            f.flush()
            
            detection_result = detector.detect_document(doc, confidence_threshold=0.25, dpi=150)
            
            f.write(f"Total elements detected: {detection_result.total_elements}\n")
            f.write(f"Elements by type: {detection_result.elements_by_type}\n")
            
            # Test Geometry Extractor
            from pipeline.pdf_translate.extractors import GeometryExtractor
            ge = GeometryExtractor()
            geometry = ge.extract(doc, detection_result)
            f.write(f"Paper format: {geometry.paper_format}\n")
            f.write(f"Margins: {geometry.default_margins}\n")
            f.write(f"Columns: {geometry.default_num_columns}\n")
            
            # Test Content Extractor
            from pipeline.pdf_translate.extractors import ContentExtractor
            ce = ContentExtractor()
            content = ce.extract(doc, geometry, detection_result)
            f.write(f"Title: {content.title[:80] if content.title else 'N/A'}...\n")
            f.write(f"Sections: {len(content.sections)}\n")
            f.write(f"Figures: {len(content.figures)}\n")
            f.write(f"Tables: {len(content.tables)}\n")
            f.write(f"References: {len(content.references)}\n")
            
            doc.close()
            f.write("\nTest completed successfully!\n")
            
        except Exception as e:
            import traceback
            f.write(f"\nERROR: {e}\n")
            f.write(traceback.format_exc())
            return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())

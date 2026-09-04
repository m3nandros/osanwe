
import fitz
import sys
import os

# Add pipeline to path
sys.path.append(os.getcwd())

from pipeline.pdf_translate.extractors.geometry_extractor import GeometryExtractor
from pipeline.pdf_translate.extractors.content_extractor import ContentExtractor
from pipeline.pdf_translate.models.geometry import DocumentGeometry
from pipeline.pdf_translate.models.content import DocumentContent, TextBlock

pdf_path = "out_dir/1706.03762/original.pdf"
doc = fitz.open(pdf_path)
page = doc[0]

geom_extractor = GeometryExtractor()
geometry = geom_extractor.extract(doc)
spacing = geometry.title_page_spacing

print("=== HEADER ELEMENTS ===")
for i, elem in enumerate(spacing.header_elements):
    print(f"{i}: [{elem['text']}] at ({elem['x']:.2f}, {elem['y']:.2f}) color={elem['color']} size={elem['font_size']:.2f}")

content_extractor = ContentExtractor()
# Mock detection result with page 0 empty to force 'scoop up'
from pipeline.pdf_translate.models.detection import DetectionResult, PageDetection
detection_result = DetectionResult(pages=[PageDetection(page_number=0, elements=[]) for _ in range(len(doc))])

content = content_extractor.extract(doc, detection_result, geometry)

print("\n=== PAGE 0 RAW BLOCKS ===")
for i, block in enumerate(content.raw_blocks):
    if block.page_number == 0:
        print(f"Block {i} (type={block.block_type}): [{block.text[:50]}...]")
        if hasattr(block, 'positioned_spans'):
            for j, span in enumerate(block.positioned_spans):
                print(f"  Span {j}: [{span.text}] at ({span.x:.2f}, {span.y:.2f}) size={span.font_size:.2f}")

doc.close()

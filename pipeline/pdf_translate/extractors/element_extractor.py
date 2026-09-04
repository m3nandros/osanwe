"""
Element Extractor.

Extracts visual elements: images, lines, boxes, headers/footers.
"""

import logging
import hashlib
from pathlib import Path
from typing import List, Optional, Tuple

import fitz  # PyMuPDF

from ..models import (
    BoundingBox,
    ImageElement,
    LineElement,
    BoxElement,
    HeaderFooterElement,
    ExtractedElements,
    DocumentGeometry,
    DocumentDetectionResult,
)

logger = logging.getLogger(__name__)


class ElementExtractor:
    """
    Extracts visual elements from PDF documents.
    
    Uses detection results to classify elements (figures vs logos, etc.)
    """
    
    def __init__(self, output_dir: Optional[Path] = None):
        """
        Initialize extractor.
        
        Args:
            output_dir: Directory to save extracted images
        """
        self.output_dir = output_dir
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
    
    def extract(
        self,
        doc: fitz.Document,
        geometry: DocumentGeometry,
        detection_result: Optional[DocumentDetectionResult] = None
    ) -> ExtractedElements:
        """
        Extract all visual elements from document.
        
        Args:
            doc: PyMuPDF Document
            geometry: Document geometry
            detection_result: Detection results for classification
        
        Returns:
            ExtractedElements with images, lines, boxes, headers/footers
        """
        all_images: List[ImageElement] = []
        all_lines: List[LineElement] = []
        all_boxes: List[BoxElement] = []
        all_headers_footers: List[HeaderFooterElement] = []
        
        for page_num in range(len(doc)):
            page = doc[page_num]
            page_geom = geometry.get_page(page_num)
            detection = None
            if detection_result:
                detection = detection_result.pages[page_num]
            
            # Extract embedded images
            images = self._extract_images(page, page_num, detection)
            all_images.extend(images)
            
            # Render detected regions (figures, tables, formulas) as images
            if detection:
                rendered = self._render_detected_regions(page, page_num, detection)
                all_images.extend(rendered)
            
            # Extract lines and boxes
            lines, boxes = self._extract_lines_and_boxes(page, page_num)
            all_lines.extend(lines)
            all_boxes.extend(boxes)
            
            # Extract header/footer content
            hf = self._extract_header_footer(page, page_num, page_geom, detection)
            all_headers_footers.extend(hf)
        
        result = ExtractedElements(
            images=all_images,
            lines=all_lines,
            boxes=all_boxes,
            headers_footers=all_headers_footers
        )
        
        logger.info(
            f"Elements extracted: {len(all_images)} images, "
            f"{len(all_lines)} lines, {len(all_boxes)} boxes, "
            f"{len(all_headers_footers)} headers/footers"
        )
        
        return result
    
    def _extract_images(
        self,
        page: fitz.Page,
        page_number: int,
        detection: Optional = None
    ) -> List[ImageElement]:
        """Extract images from a page."""
        images: List[ImageElement] = []
        
        image_list = page.get_images(full=True)
        
        for img_index, img_info in enumerate(image_list):
            xref = img_info[0]
            
            try:
                # Get image rectangles (position on page)
                image_rects = page.get_image_rects(xref)
                
                for rect in image_rects:
                    # Extract image data
                    base_image = page.parent.extract_image(xref)
                    
                    if not base_image:
                        continue
                    
                    bbox = BoundingBox(
                        x0=rect.x0,
                        y0=rect.y0,
                        x1=rect.x1,
                        y1=rect.y1
                    )
                    
                    # Classify image type
                    element_type = self._classify_image_type(
                        bbox, page.rect, page_number, detection
                    )
                    
                    positioning = "absolute" if element_type in ["logo", "icon"] else "float"
                    
                    # Save image if output_dir specified
                    extracted_path = None
                    if self.output_dir:
                        extracted_path = self._save_image(
                            base_image, page_number, img_index
                        )
                    
                    images.append(ImageElement(
                        page_number=page_number,
                        bbox=bbox,
                        image_data=base_image["image"],
                        image_format=base_image["ext"],
                        dpi=base_image.get("xres", 72),
                        element_type=element_type,
                        positioning=positioning,
                        extracted_path=extracted_path
                    ))
                    
            except Exception as e:
                logger.warning(f"Failed to extract image {xref} on page {page_number}: {e}")
        
        return images
    
    def _classify_image_type(
        self,
        bbox: BoundingBox,
        page_rect: fitz.Rect,
        page_number: int,
        detection: Optional = None
    ) -> str:
        """Classify image type based on position and detection results."""
        # If we have YOLO detection, check if image overlaps with detected figures
        if detection:
            for elem in detection.elements:
                if elem.class_name == "figure" and bbox.overlaps(elem.bbox):
                    return "figure"
        
        # Relative position on page
        rel_x = bbox.x0 / page_rect.width
        rel_y = bbox.y0 / page_rect.height
        rel_width = bbox.width / page_rect.width
        rel_height = bbox.height / page_rect.height
        
        # Logo heuristics (first page, small, top/sides)
        if page_number == 0:
            if rel_y < 0.15 and rel_width < 0.3 and rel_height < 0.1:
                return "logo"
            if (rel_x < 0.1 or rel_x > 0.8) and rel_width < 0.2:
                return "logo"
        
        # Icon heuristics (very small)
        if rel_width < 0.05 and rel_height < 0.05:
            return "icon"
        
        # Default to figure
        return "figure"
    
    def _save_image(
        self,
        image_data: dict,
        page_number: int,
        img_index: int
    ) -> str:
        """Save image to output directory."""
        ext = image_data["ext"]
        data = image_data["image"]
        
        # Generate unique filename
        hash_prefix = hashlib.md5(data).hexdigest()[:8]
        filename = f"img_p{page_number}_{img_index}_{hash_prefix}.{ext}"
        
        filepath = self.output_dir / filename
        filepath.write_bytes(data)
        
        return str(filepath)
    
    def _extract_lines_and_boxes(
        self,
        page: fitz.Page,
        page_number: int
    ) -> Tuple[List[LineElement], List[BoxElement]]:
        """Extract vector graphics (lines and rectangles)."""
        lines: List[LineElement] = []
        boxes: List[BoxElement] = []
        
        try:
            drawings = page.get_drawings()
        except Exception as e:
            logger.warning(f"Failed to get drawings from page {page_number}: {e}")
            return lines, boxes
        
        for path in drawings:
            try:
                rect = path.get("rect")
                if not rect:
                    continue
                
                # Get styling
                stroke_width = path.get("width", 1.0)
                stroke_color = self._color_to_hex(path.get("color"))
                fill_color = self._color_to_hex(path.get("fill"))
                
                # Determine if it's a line or box
                is_horizontal = abs(rect.height) < 3
                is_vertical = abs(rect.width) < 3
                
                if is_horizontal or is_vertical:
                    # It's a line
                    line_type = "horizontal" if is_horizontal else "vertical"
                    semantic_role = self._determine_line_role(rect, page, line_type)
                    
                    lines.append(LineElement(
                        page_number=page_number,
                        x1=rect.x0,
                        y1=rect.y0,
                        x2=rect.x1,
                        y2=rect.y1,
                        stroke_width=stroke_width,
                        stroke_color=stroke_color,
                        line_type=line_type,
                        semantic_role=semantic_role
                    ))
                else:
                    # It's a box/rectangle
                    bbox = BoundingBox(
                        x0=rect.x0, y0=rect.y0,
                        x1=rect.x1, y1=rect.y1
                    )
                    semantic_role = self._determine_box_role(bbox, page)
                    
                    boxes.append(BoxElement(
                        page_number=page_number,
                        bbox=bbox,
                        stroke_width=stroke_width,
                        stroke_color=stroke_color,
                        fill_color=fill_color,
                        semantic_role=semantic_role
                    ))
                    
            except Exception as e:
                logger.debug(f"Failed to process drawing: {e}")
        
        return lines, boxes
    
    def _color_to_hex(self, color) -> str:
        """Convert color to hex string."""
        if color is None:
            return "#000000"
        
        if isinstance(color, (list, tuple)):
            if len(color) >= 3:
                r, g, b = color[:3]
                # Normalize to 0-255 if needed
                if all(0 <= c <= 1 for c in (r, g, b)):
                    r, g, b = int(r * 255), int(g * 255), int(b * 255)
                return f"#{int(r):02x}{int(g):02x}{int(b):02x}"
        
        return "#000000"
    
    def _determine_line_role(
        self,
        rect: fitz.Rect,
        page: fitz.Page,
        line_type: str
    ) -> str:
        """Determine semantic role of a line."""
        page_height = page.rect.height
        rel_y = rect.y0 / page_height
        
        if line_type == "horizontal":
            if rel_y < 0.15:
                return "header_line"
            elif rel_y > 0.9:
                return "footer_line"
            elif rel_y < 0.25:
                return "title_underline"
            else:
                return "section_separator"
        
        return "decorative"
    
    def _determine_box_role(self, bbox: BoundingBox, page: fitz.Page) -> str:
        """Determine semantic role of a box."""
        page_height = page.rect.height
        rel_y = bbox.y0 / page_height
        rel_height = bbox.height / page_height
        
        # Large box near top of first page might be abstract box
        if rel_y < 0.4 and rel_height > 0.1:
            return "abstract_box"
        
        # Sidebar (narrow, tall)
        if bbox.width < 100 and rel_height > 0.3:
            return "sidebar"
        
        return "decorative"
    
    def _extract_header_footer(
        self,
        page: fitz.Page,
        page_number: int,
        page_geom,
        detection: Optional = None
    ) -> List[HeaderFooterElement]:
        """Extract header and footer content."""
        elements: List[HeaderFooterElement] = []
        
        # Extract header - use expanded area on first page to catch conference/copyright text
        header_height = page_geom.margin_top_pt
        if page_number == 0:
            # On first page, extend header area to capture text above title
            header_height = max(header_height, 120)  # At least 120pt
        
        if header_height > 0 or (detection and detection.headers):
            header_rect = fitz.Rect(
                0, 0,
                page.rect.width,
                header_height
            )
            header_text = page.get_text("text", clip=header_rect).strip()
            
            if header_text:
                # Extract text color from header
                text_color = self._extract_text_color(page, header_rect)
                font_size, font_family = self._extract_font_info(page, header_rect)
                
                elements.append(HeaderFooterElement(
                    page_number=page_number,
                    position="header",
                    content=header_text,
                    bbox=BoundingBox.from_tuple(header_rect),
                    alignment=self._detect_alignment(header_text, header_rect, page),
                    text_color=text_color,
                    font_size=font_size,
                    font_family=font_family,
                    has_page_number=self._contains_page_number(header_text, page_number)
                ))
        
        # Extract footer - use expanded area on first page for conference line
        footer_margin = page_geom.margin_bottom_pt
        if page_number == 0:
            # On first page, expand footer area to capture conference/venue line
            footer_margin = max(footer_margin, 70)
        
        if footer_margin > 0 or (detection and detection.footers):
            footer_rect = fitz.Rect(
                0,
                page.rect.height - footer_margin,
                page.rect.width,
                page.rect.height
            )
            footer_text = page.get_text("text", clip=footer_rect).strip()
            
            if footer_text:
                elements.append(HeaderFooterElement(
                    page_number=page_number,
                    position="footer",
                    content=footer_text,
                    bbox=BoundingBox.from_tuple(footer_rect),
                    alignment=self._detect_alignment(footer_text, footer_rect, page),
                    has_page_number=self._contains_page_number(footer_text, page_number)
                ))
        
        return elements
    
    def _detect_alignment(
        self,
        text: str,
        rect: fitz.Rect,
        page: fitz.Page
    ) -> str:
        """Detect text alignment within region."""
        # Get text blocks in region
        blocks = page.get_text("dict", clip=rect)["blocks"]
        
        if not blocks:
            return "center"
        
        # Find text block
        text_block = None
        for b in blocks:
            if b.get("type") == 0:
                text_block = b
                break
        
        if not text_block:
            return "center"
        
        # Check position
        block_x = text_block["bbox"][0]
        block_width = text_block["bbox"][2] - text_block["bbox"][0]
        page_center = page.rect.width / 2
        
        if abs(block_x + block_width/2 - page_center) < 50:
            return "center"
        elif block_x < page.rect.width * 0.3:
            return "left"
        else:
            return "right"
    
    def _contains_page_number(self, text: str, page_number: int) -> bool:
        """Check if text contains the page number."""
        # Check for page number (1-indexed typically)
        return str(page_number + 1) in text or str(page_number) in text
    
    def _extract_text_color(self, page: fitz.Page, rect: fitz.Rect) -> str:
        """Extract dominant text color from a region."""
        try:
            blocks = page.get_text("dict", clip=rect)["blocks"]
            for block in blocks:
                if block.get("type") == 0:  # Text block
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            color = span.get("color", 0)
                            # Convert integer color to hex
                            if isinstance(color, int):
                                r = (color >> 16) & 0xFF
                                g = (color >> 8) & 0xFF
                                b = color & 0xFF
                                return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            pass
        return "#000000"
    
    def _extract_font_info(self, page: fitz.Page, rect: fitz.Rect) -> tuple:
        """Extract font size and family from a region."""
        try:
            blocks = page.get_text("dict", clip=rect)["blocks"]
            for block in blocks:
                if block.get("type") == 0:
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            size = span.get("size", 10.0)
                            font = span.get("font", "")
                            return (size, font)
        except Exception:
            pass
        return (10.0, "")
    
    def _render_detected_regions(
        self,
        page: fitz.Page,
        page_number: int,
        detection
    ) -> List[ImageElement]:
        """Render detected regions (figures, tables, formulas) as images."""
        images: List[ImageElement] = []
        
        # Classes to render as images
        render_classes = ["figure", "table", "isolate_formula"]
        
        for elem in detection.elements:
            if elem.class_name not in render_classes:
                continue
            
            try:
                # Create clip rect with small padding
                padding = 2
                clip_rect = fitz.Rect(
                    elem.bbox.x0 - padding,
                    elem.bbox.y0 - padding,
                    elem.bbox.x1 + padding,
                    elem.bbox.y1 + padding
                )
                
                # Render at 2x resolution for quality
                zoom = 2.0
                mat = fitz.Matrix(zoom, zoom)
                pix = page.get_pixmap(matrix=mat, clip=clip_rect)
                
                # Convert to PNG
                img_data = pix.tobytes("png")
                
                bbox = BoundingBox(
                    x0=elem.bbox.x0,
                    y0=elem.bbox.y0,
                    x1=elem.bbox.x1,
                    y1=elem.bbox.y1
                )
                
                # Save if output_dir specified
                extracted_path = None
                if self.output_dir:
                    # Unique filename based on class and position
                    filename = f"{elem.class_name}_p{page_number}_{int(elem.bbox.y0)}.png"
                    filepath = self.output_dir / filename
                    filepath.write_bytes(img_data)
                    extracted_path = str(filepath)
                
                images.append(ImageElement(
                    page_number=page_number,
                    bbox=bbox,
                    image_data=img_data,
                    image_format="png",
                    dpi=int(72 * zoom),
                    element_type=elem.class_name,
                    positioning="float",
                    extracted_path=extracted_path
                ))
                
            except Exception as e:
                logger.warning(f"Failed to render {elem.class_name} on page {page_number}: {e}")
        
        return images

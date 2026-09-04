"""
Geometry Extractor.

Extracts page dimensions, margins, columns, and title page spacings with high precision.
"""

import logging
import re
from typing import Dict, List, Optional, Tuple

import fitz  # PyMuPDF

from ..models import (
    BoundingBox,
    PageGeometry,
    DocumentGeometry,
    TitlePageSpacing,
    DocumentDetectionResult,
    PageDetectionResult,
)

logger = logging.getLogger(__name__)


class GeometryExtractor:
    """
    Extracts document geometry: page sizes, margins, columns, headers/footers.
    """
    
    def extract(
        self,
        doc: fitz.Document,
        detection_result: Optional[DocumentDetectionResult] = None
    ) -> DocumentGeometry:
        """
        Extract geometry from document.
        """
        pages: List[PageGeometry] = []
        
        for page_num in range(len(doc)):
            page = doc[page_num]
            detection = None
            if detection_result:
                detection = detection_result.pages[page_num]
            
            page_geom = self._extract_page_geometry(page, page_num, detection)
            pages.append(page_geom)
        
        paper_width = pages[0].width_pt if pages else 612
        paper_height = pages[0].height_pt if pages else 792
        paper_format = self._detect_paper_format(paper_width, paper_height)
        
        default_margins = self._calculate_default_margins(pages)
        default_columns = self._calculate_default_columns(pages)
        
        title_spacing = self._extract_title_page_spacing(doc)
        
        # Detect page number baseline
        page_num_baseline = 0.0
        for page_num in range(len(doc)):
            page = doc[page_num]
            blocks = page.get_text("dict")["blocks"]
            height = page.rect.height
            for b in blocks:
                if b.get("type") == 0:
                    text = "".join([s["text"] for l in b["lines"] for s in l["spans"]]).strip()
                    # Look for a small digit block at the bottom
                    if text.isdigit() and len(text) < 3 and b["bbox"][1] > height * 0.9:
                        page_num_baseline = b["lines"][0]["spans"][0]["origin"][1]
                        break
            if page_num_baseline > 0:
                break

        result = DocumentGeometry(
            pages=pages,
            paper_format=paper_format,
            paper_width_pt=paper_width,
            paper_height_pt=paper_height,
            default_num_columns=default_columns,
            default_margins=default_margins,
            title_page_spacing=title_spacing,
            page_number_baseline_bp=page_num_baseline
        )
        
        logger.info(
            f"Geometry extracted: {paper_format} ({paper_width:.0f}x{paper_height:.0f}pt), "
            f"{default_columns} column(s), margins={default_margins}"
        )
        
        return result
    
    def _extract_page_geometry(
        self,
        page: fitz.Page,
        page_number: int,
        detection: Optional[PageDetectionResult]
    ) -> PageGeometry:
        width, height = page.rect.width, page.rect.height
        margins = self._detect_margins(page, detection)
        num_columns, col_width, col_gap = self._detect_columns(page, detection)
        header_height, footer_height = self._detect_header_footer(page, detection)
        is_title_page = self._is_title_page(page_number, detection)
        
        return PageGeometry(
            page_number=page_number, width_pt=width, height_pt=height,
            margin_top_pt=margins["top"], margin_bottom_pt=margins["bottom"],
            margin_left_pt=margins["left"], margin_right_pt=margins["right"],
            num_columns=num_columns, column_width_pt=col_width, column_gap_pt=col_gap,
            header_height_pt=header_height, footer_height_pt=footer_height,
            is_title_page=is_title_page
        )
    
    def _detect_margins(self, page: fitz.Page, detection: Optional[PageDetectionResult]) -> Dict[str, float]:
        page_width, page_height = page.rect.width, page.rect.height
        blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
        text_blocks = [b for b in blocks if b.get("type") == 0]
        drawings = page.get_drawings()
        
        if not text_blocks and not drawings:
            return {"top": 72, "bottom": 72, "left": 72, "right": 72}
        
        # Filter out headers and footers from margin detection
        # Usually headers are in top 10% and footers in bottom 10%
        body_blocks = []
        for b in text_blocks:
            text = "".join([s["text"] for l in b["lines"] for s in l["spans"]]).strip()
            if not text: continue
            
            # Skip potential page numbers and small header/footer snippets
            is_potential_hf = (b["bbox"][1] < page_height * 0.1 or b["bbox"][3] > page_height * 0.9)
            if is_potential_hf and (len(text) < 10 or text.isdigit()):
                continue
            body_blocks.append(b)
            
        if not body_blocks:
            body_blocks = text_blocks # Fallback if filtering was too aggressive
            
        x_positions = [b["bbox"][0] for b in body_blocks if b["bbox"][2] - b["bbox"][0] > 50]
        if x_positions:
            median_x = sorted(x_positions)[len(x_positions) // 2]
            outlier_threshold = median_x - 50
        else:
            outlier_threshold = 0
        
        content_bounds = {"min_x": float('inf'), "min_y": float('inf'), "max_x": float('-inf'), "max_y": float('-inf')}
        for block in body_blocks:
            bbox = block["bbox"]
            if bbox[0] < outlier_threshold and bbox[2] - bbox[0] < 50: continue
            content_bounds["min_x"] = min(content_bounds["min_x"], bbox[0])
            content_bounds["min_y"] = min(content_bounds["min_y"], bbox[1])
            content_bounds["max_x"] = max(content_bounds["max_x"], bbox[2])
            content_bounds["max_y"] = max(content_bounds["max_y"], bbox[3])
        
        for d in drawings:
            for item in d.get("items", []):
                if item[0] == "l":
                    p1, p2 = item[1], item[2]
                    # Only consider long horizontal lines as content separators
                    if abs(p1.y - p2.y) < 2 and abs(p2.x - p1.x) > 100:
                        # Exclude lines that are too close to top/bottom edges (likely rules)
                        if page_height * 0.1 < p1.y < page_height * 0.9:
                            content_bounds["min_x"] = min(content_bounds["min_x"], min(p1.x, p2.x))
                            content_bounds["max_x"] = max(content_bounds["max_x"], max(p1.x, p2.x))
        
        margins = {
            "left": content_bounds["min_x"], "top": content_bounds["min_y"],
            "right": page_width - content_bounds["max_x"], "bottom": page_height - content_bounds["max_y"]
        }
        return {k: max(v, 0) for k, v in margins.items()}
    
    def _detect_columns(self, page: fitz.Page, detection: Optional[PageDetectionResult]) -> Tuple[int, float, float]:
        blocks = page.get_text("dict")["blocks"]
        text_blocks = [b for b in blocks if b.get("type") == 0]
        if len(text_blocks) < 3: return (1, 0, 0)
        left_positions = sorted(set(int(b["bbox"][0]) for b in text_blocks))
        gaps = [(left_positions[i-1], left_positions[i], left_positions[i] - left_positions[i-1]) 
                for i in range(1, len(left_positions)) if left_positions[i] - left_positions[i-1] > 50]
        if gaps:
            col_gap = max(gaps, key=lambda x: x[2])[2]
            col_width = (page.rect.width - 144 - col_gap) / 2
            return (2, col_width, col_gap)
        return (1, 0, 0)
    
    def _detect_header_footer(self, page: fitz.Page, detection: Optional[PageDetectionResult]) -> Tuple[float, float]:
        h, f = 0.0, 0.0
        if detection:
            if detection.headers: h = max(item.bbox.y1 for item in detection.headers)
            if detection.footers: f = detection.pdf_height_pt - min(item.bbox.y0 for item in detection.footers)
        return h, f
    
    def _is_title_page(self, page_number: int, detection: Optional[PageDetectionResult]) -> bool:
        if page_number != 0: return False
        return (len(detection.titles) > 0 or len(detection.abstracts) > 0) if detection else page_number == 0
    
    def _detect_paper_format(self, width: float, height: float) -> str:
        if abs(width - 595) < 10 and abs(height - 842) < 10: return "a4"
        if abs(width - 612) < 10 and abs(height - 792) < 10: return "letter"
        return "custom"
    
    def _calculate_default_margins(self, pages: List[PageGeometry]) -> Dict[str, float]:
        if not pages: return {"top": 72, "bottom": 72, "left": 72, "right": 72}
        body_pages = [p for p in pages if p.page_number > 0] or pages
        def median(vals):
            if not vals: return 72
            s = sorted(vals)
            n = len(s)
            return (s[n//2-1] + s[n//2])/2 if n % 2 == 0 else s[n//2]
        normal = [p for p in body_pages if p.margin_top_pt < 150] or body_pages
        return {
            "top": median([p.margin_top_pt for p in normal]),
            "bottom": median([p.margin_bottom_pt for p in normal]),
            "left": median([p.margin_left_pt for p in normal]),
            "right": median([p.margin_right_pt for p in normal])
        }
    
    def _calculate_default_columns(self, pages: List[PageGeometry]) -> int:
        if not pages: return 1
        counts = {}
        for p in [p for p in pages if not p.is_title_page] or pages:
            counts[p.num_columns] = counts.get(p.num_columns, 0) + 1
        return max(counts, key=counts.get) if counts else 1

    def _extract_title_page_spacing(self, doc: fitz.Document) -> TitlePageSpacing:
        if len(doc) == 0: return TitlePageSpacing()
        page = doc[0]
        spacing = TitlePageSpacing()
        page_height = page.rect.height
        blocks_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
        text_blocks = [b for b in blocks_dict if b.get("type") == 0]
        drawings = page.get_drawings()
        
        # 1. Extract rules (horizontal lines and thin rectangles)
        rules = []
        for d in drawings:
            for item in d.get("items", []):
                # Line
                if item[0] == "l" and abs(item[1].y - item[2].y) < 0.5:
                    rules.append({
                        "y": item[1].y,
                        "thickness": d.get("width", 1.0),
                        "x0": min(item[1].x, item[2].x),
                        "x1": max(item[1].x, item[2].x)
                    })
                # Thin rectangle acting as a line
                elif item[0] == "re" and item[1].height < 5.0:
                    rules.append({
                        "y": item[1].y0 + item[1].height/2,
                        "thickness": item[1].height,
                        "x0": item[1].x0,
                        "x1": item[1].x1
                    })
        rules.sort(key=lambda l: l["y"])
        spacing.rules = rules
        
        # 2. Extract header elements (colored/rotated text outside main flow)
        header_elements = []
        headers, title_b, authors, abs_h, abs_t, footnotes = [], None, [], None, None, []
        
        for b in text_blocks:
            text = "".join([s["text"] for l in b["lines"] for s in l["spans"]]).strip()
            if not text: continue
            
            y0, y1 = b["bbox"][1], b["bbox"][3]
            spans = [s for l in b["lines"] for s in l["spans"]]
            if not spans: continue
            
            # Detect sidebars, headers, and footers on page 0
            is_header_elem = (
                (b["bbox"][0] < 72 or b["bbox"][2] > page.rect.width - 72) and # Tight margin check
                not (page.rect.width * 0.2 < b["bbox"][0] < page.rect.width * 0.8) # Not in center body
            ) or (
                y1 < 100 or # Strict header
                y0 > page_height - 100 # Strict footer
            ) or (
                any(abs(l.get("dir", (1,0))[1]) > 0.1 for l in b["lines"]) # Rotated text (arXiv sidebar)
            )

            # Do NOT treat Abstract or Title as header elements
            if is_header_elem:
                # Heuristic: if it looks like Abstract or Title, it's NOT a header element
                if "abstract" in text.lower() and len(text) < 15: is_header_elem = False
                if y0 > page_height * 0.2 and y1 < page_height * 0.8 and len(text) > 100: is_header_elem = False # Likely Abstract text
                for l in b["lines"]:
                    direction = l.get("dir", (1.0, 0.0))
                    for s in l["spans"]:
                        color_int = s["color"]
                        hex_color = f"{(color_int >> 16) & 0xFF:02x}{(color_int >> 8) & 0xFF:02x}{color_int & 0xFF:02x}"
                        flags = s.get("flags", 0)
                        font_name = s.get("font", "").lower()
                        # Comprehensive bold/italic/weight detection
                        is_bold = bool(flags & 16) or any(k in font_name for k in ["bold", "medi", "black", "heavy", "demi"])
                        is_italic = bool(flags & 2) or any(k in font_name for k in ["ital", "obli"])
                        
                        # Enhanced math detection for header elements
                        math_symbols = r'[=+\-×÷√≤≥≠≈∞∈∉⊂⊃∩∪∑∏∫∂∇±÷·°±∕∖⋇⋈⋉⋊⋋⋌⋏⋎⋐⋑⋒⋓⋔⋕⋖⋗⋘⋙⋚⋛⋜⋝⋞⋟⋠⋡⋢⋣⋤⋥⋦⋧⋨⋩⋪⋫⋬⋭]'
                        math_indicators = ["math", "italic", "symbol", "cmsy", "cmmi", "msbm", "amsfonts"]
                        is_math = any(ind in font_name for ind in math_indicators) or \
                                  re.search(math_symbols, s["text"]) or \
                                  (is_italic and len(s["text"].strip()) == 1 and s["text"].strip().isalpha())
                        
                        header_elements.append({
                            "text": s["text"],
                            "x": s["origin"][0],
                            "y": s["origin"][1],
                            "font_size": s["size"],
                            "color": hex_color,
                            "dir": direction,
                            "is_bold": is_bold,
                            "is_italic": is_italic,
                            "is_math": is_math
                        })
                # Skip header elements from main flow classification
                continue
            
            max_fs = max([s["size"] for s in spans])
            if y1 < 150 and (any(s["color"] != 0 for s in spans) or max_fs < 12.5): headers.append(b)
            elif y0 < 150 and max_fs > 14:
                if title_b is None or max_fs > max([s["size"] for l in title_b["lines"] for s in l["spans"]]): title_b = b
            elif "@" in text or any(k in text.lower() for k in ["university", "research", "google", "institute"]):
                if y0 < page_height * 0.55:
                    # Capture X position for absolute author placement
                    b["x_origin"] = b["lines"][0]["spans"][0]["origin"][0] if b["lines"] and b["lines"][0]["spans"] else b["bbox"][0]
                    authors.append(b)
                else: footnotes.append(b)
            elif "abstract" in text.lower() and len(text) < 15: abs_h = b
            elif y1 > page_height * 0.35 and y1 < page_height * 0.75:
                if abs_t is None or len(text) > 100: abs_t = b
            elif y0 > page_height * 0.6: footnotes.append(b)

        spacing.header_elements = header_elements

        def get_lead(b):
            if not b or len(b["lines"]) < 2: return None
            baselines = [l["spans"][0]["origin"][1] for l in b["lines"] if l.get("spans")]
            if len(baselines) < 2: return None
            # Use median gap for robustness against paragraph skips or markers
            gaps = [baselines[i] - baselines[i-1] for i in range(1, len(baselines))]
            return sorted(gaps)[len(gaps)//2]

        spacing.abstract_leading = get_lead(abs_t) or 11.0
        f_leads = [get_lead(b) for b in footnotes if get_lead(b)]
        if f_leads: spacing.footnote_leading = sum(f_leads) / len(f_leads)
        
        if abs_t:
            spans = [s for l in abs_t["lines"] for s in l["spans"]]
            spacing.abstract_font_size = max([s["size"] for s in spans])
            spacing.abstract_width_pt = abs_t["bbox"][2] - abs_t["bbox"][0]
            spacing.abstract_x_pt = abs_t["bbox"][0]
        if abs_h: spacing.abstract_heading_font_size = max([s["size"] for l in abs_h["lines"] for s in l["spans"]])
        if footnotes:
            fn_sizes = [s["size"] for b in footnotes for l in b["lines"] for s in l["spans"] if s["size"] > 7]
            if fn_sizes: spacing.footnote_font_size = sum(fn_sizes) / len(fn_sizes)

        if title_b:
            spacing.title_y_pt = title_b["lines"][0]["spans"][0]["origin"][1]
            spacing.title_font_size = max([s["size"] for l in title_b["lines"] for s in l["spans"]])
        
        if authors:
            row_groups = []
            sorted_authors = sorted(authors, key=lambda b: b["bbox"][1])
            if sorted_authors:
                current_row = [sorted_authors[0]]
                row_groups.append(current_row)
                for b in sorted_authors[1:]:
                    if b["bbox"][1] - current_row[0]["bbox"][1] < 20: current_row.append(b)
                    else:
                        current_row = [b]
                        row_groups.append(current_row)
            row_baselines = []
            author_sizes = []
            for row in row_groups:
                bls = []
                for b in row:
                    if b["lines"] and b["lines"][0].get("spans"):
                        bls.append(b["lines"][0]["spans"][0]["origin"][1])
                        author_sizes.extend([s["size"] for l in b["lines"] for s in l["spans"]])
                if bls: row_baselines.append(sum(bls) / len(bls))
            spacing.authors_y_pt = row_baselines
            if author_sizes:
                spacing.author_font_size = sum(author_sizes) / len(author_sizes)
            all_leads = [get_lead(b) for b in authors if get_lead(b)]
            if all_leads: spacing.author_leading = sum(all_leads) / len(all_leads)

        if abs_h: spacing.abstract_y_pt = abs_h["lines"][0]["spans"][0]["origin"][1]
        if abs_t: spacing.abstract_text_y_pt = abs_t["lines"][0]["spans"][0]["origin"][1]
        if footnotes:
            # Sort footnotes by Y coordinate
            sorted_fn_blocks = sorted(footnotes, key=lambda x: x["bbox"][1])
            spacing.footnote_text_y_pt = sorted_fn_blocks[0]["lines"][0]["spans"][0]["origin"][1]
            
            # Capture all individual line baselines across all footnote blocks
            all_fn_baselines = []
            for b in sorted_fn_blocks:
                for l in b["lines"]:
                    if l.get("spans"):
                        all_fn_baselines.append(l["spans"][0]["origin"][1])
            spacing.footnote_baselines_y_pt = sorted(list(set(all_fn_baselines)))

        if headers and title_b: spacing.header_to_title_gap = title_b["bbox"][1] - max(b["bbox"][3] for b in headers)
        if rules:
            if headers:
                hb = max(b["bbox"][3] for b in headers)
                ul = [r for r in rules if hb < r["y"] < hb + 100]
                if ul:
                    spacing.upper_line_thickness, spacing.upper_line_y_pt = ul[0]["thickness"], ul[0]["y"]
                    spacing.header_to_upper_rule_gap = ul[0]["y"] - hb
            if title_b:
                tb = title_b["bbox"][3]
                tl = [r for r in rules if tb < r["y"] < tb + 40]
                if tl:
                    spacing.title_line_thickness, spacing.title_to_line_gap = tl[0]["thickness"], tl[0]["y"] - tb
                    if authors: spacing.line_to_authors_gap = min(b["bbox"][1] for b in authors) - tl[0]["y"]
                elif authors: spacing.line_to_authors_gap = min(b["bbox"][1] for b in authors) - tb

        if authors and abs_h: spacing.authors_to_abstract_gap = abs_h["bbox"][1] - max(b["bbox"][3] for b in authors)
        if abs_h and abs_t: spacing.abstract_heading_to_text_gap = abs_t["bbox"][1] - abs_h["bbox"][3]

        bottom_rules = [r for r in rules if r["y"] > page_height * 0.6]
        if bottom_rules:
            spacing.footnote_rule_y_pt, spacing.footnote_line_thickness = bottom_rules[0]["y"], bottom_rules[0]["thickness"]
            spacing.footnote_line_width_ratio = (bottom_rules[0]["x1"] - bottom_rules[0]["x0"]) / (page.rect.width - 144)
            rel_fn = [b for b in footnotes if b["bbox"][1] > bottom_rules[0]["y"]]
            if rel_fn: spacing.footnote_rule_to_text_gap = min(b["bbox"][1] for b in rel_fn) - bottom_rules[0]["y"]
            if abs_t: spacing.abstract_to_footnotes_gap = bottom_rules[0]["y"] - abs_t["bbox"][3]

        return spacing

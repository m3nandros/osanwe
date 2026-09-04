"""
Content Extractor.

Extracts text content with semantic structure using detection results.
"""

import logging
import re
from typing import List, Optional, Dict
from collections import defaultdict

import fitz  # PyMuPDF

from ..models import (
    BoundingBox,
    TextStyle,
    TextBlock,
    BlockType,
    AuthorInfo,
    Section,
    Figure,
    Table,
    Equation,
    Footnote,
    Reference,
    DocumentContent,
    DocumentGeometry,
    DocumentDetectionResult,
    DetectedElement,
    PositionedSpan,
)

logger = logging.getLogger(__name__)


class ContentExtractor:
    """
    Extracts document content with semantic structure.
    
    Uses DocLayout-YOLO detection results to classify text blocks.
    """
    
    # Mapping from YOLO class names to BlockType
    CLASS_TO_BLOCK_TYPE = {
        "title": BlockType.DOCUMENT_TITLE,
        "plain_text": BlockType.PARAGRAPH,
        "abstract": BlockType.ABSTRACT_TEXT,
        "figure_caption": BlockType.FIGURE_CAPTION,
        "table_caption": BlockType.TABLE_CAPTION,
        "isolate_formula": BlockType.EQUATION,
        "formula_caption": BlockType.EQUATION,
        "reference": BlockType.REFERENCE_ITEM,
        "footnote": BlockType.FOOTNOTE,
        "table_footnote": BlockType.FOOTNOTE,
        "page_header": BlockType.PAGE_HEADER,
        "page_footer": BlockType.PAGE_FOOTER,
        "page_number": BlockType.PAGE_NUMBER,
    }
    
    def _extract_drawings(self, page: fitz.Page) -> List[Dict]:
        """Extract horizontal lines and radical signs."""
        drawings = page.get_drawings()
        items = []
        for d in drawings:
            # Check for horizontal rules (fraction bars)
            for item in d.get("items", []):
                if item[0] == "l": # line
                    p1, p2 = item[1], item[2]
                    # Horizontal-ish line
                    if abs(p1.y - p2.y) < 1.0 and abs(p1.x - p2.x) > 3.0:
                        items.append({
                            "type": "rule",
                            "x0": min(p1.x, p2.x),
                            "y": (p1.y + p2.y) / 2,
                            "x1": max(p1.x, p2.x),
                            "width": abs(p1.x - p2.x)
                        })
                elif item[0] == "r": # rectangle (can be a line)
                    rect = item[1]
                    if rect.height < 2.0 and rect.width > 3.0:
                        items.append({
                            "type": "rule",
                            "x0": rect.x0,
                            "y": (rect.y0 + rect.y1) / 2,
                            "x1": rect.x1,
                            "width": rect.width
                        })
                elif item[0] == "p": # path (could be a radical)
                    # Radical signs are complex paths. For now, let's just log their presence
                    # or try to find their bounding box if they look like a checkmark/radical.
                    pass
        return items

    def extract(
        self,
        doc: fitz.Document,
        geometry: DocumentGeometry,
        detection_result: DocumentDetectionResult
    ) -> DocumentContent:
        """
        Extract all content from document.
        
        Args:
            doc: PyMuPDF Document
            geometry: Document geometry
            detection_result: DocLayout-YOLO detection results
        
        Returns:
            DocumentContent with full semantic structure
        """
        content = DocumentContent()
        all_blocks: List[TextBlock] = []
        
        # Get header elements from geometry to avoid double-processing
        header_spans = []
        if geometry.title_page_spacing:
            header_spans = geometry.title_page_spacing.header_elements
        
        # Process each page
        for page_num in range(len(doc)):
            page = doc[page_num]
            page_detection = detection_result.pages[page_num]
            
            # Extract drawings (lines) for this page to help formula reconstruction
            page_drawings = self._extract_drawings(page)
            
            # On page 0, ensure we catch all text blocks (even if YOLO missed them)
            # to achieve pixel-perfect layout reconstruction.
            if page_num == 0:
                detected_bboxes = [e.bbox for e in page_detection.elements]
                fitz_blocks = page.get_text("dict")["blocks"]
                
                new_elements = []
                for fb in fitz_blocks:
                    if fb.get("type") != 0: continue
                    fbbox = BoundingBox(fb["bbox"][0], fb["bbox"][1], fb["bbox"][2], fb["bbox"][3])
                    
                    # If any existing detection covers more than 20% of this block, 
                    # or if this block covers more than 20% of an existing detection, it's a duplicate.
                    is_covered = False
                    f_rect = fitz.Rect(fbbox.x0, fbbox.y0, fbbox.x1, fbbox.y1)
                    f_area = f_rect.width * f_rect.height
                    if f_area == 0: continue
                    
                    for dbbox in detected_bboxes:
                        d_rect = fitz.Rect(dbbox.x0, dbbox.y0, dbbox.x1, dbbox.y1)
                        d_area = d_rect.width * d_rect.height
                        if d_area == 0: continue
                        
                        intersect = f_rect & d_rect
                        if not intersect.is_empty:
                            i_area = intersect.width * intersect.height
                            # More aggressive deduplication: 20% overlap is enough to discard
                            if i_area / f_area > 0.2 or i_area / d_area > 0.2:
                                is_covered = True
                                break
                    
                    if not is_covered:
                        new_elem = DetectedElement(
                            bbox=fbbox,
                            class_name="plain_text",
                            confidence=1.0,
                            page_number=0,
                            class_id=0
                        )
                        new_elements.append(new_elem)
                        detected_bboxes.append(fbbox)
                
                page_detection.elements.extend(new_elements)

            # Extract text for each detected element
            for elem in page_detection.elements:
                # Find drawings that fall within this element's bbox
                elem_drawings = [
                    d for d in page_drawings 
                    if elem.bbox.x0 <= d["x0"] <= elem.bbox.x1 and 
                       elem.bbox.y0 <= d["y"] <= elem.bbox.y1
                ]
                
                text_info = self._extract_text_and_style(
                    page, 
                    elem.bbox, 
                    elem.class_name,
                    drawings=elem_drawings
                )
                if not text_info["text"].strip():
                    continue
                
                # Filter out spans already captured as header_elements on page 0
                if page_num == 0:
                    filtered_spans = []
                    for s in text_info["positioned_spans"]:
                        is_header = False
                        for hs in header_spans:
                            # Increase tolerance to 3.0pt to match assembler deduplication
                            if abs(s.x - hs["x"]) < 3.0 and abs(s.y - hs["y"]) < 3.0:
                                is_header = True
                                break
                            # Also check text-based match if coordinates are close
                            if s.text.strip() == hs["text"].strip() and abs(s.y - hs["y"]) < 5.0:
                                is_header = True
                                break
                        if not is_header:
                            filtered_spans.append(s)
                    
                    if not filtered_spans:
                        continue
                    text_info["positioned_spans"] = filtered_spans
                
                block = TextBlock(
                    text=text_info["text"],
                    bbox=elem.bbox,
                    style=text_info["style"],
                    block_type=self._classify_block(elem, text_info["text"], page_num),
                    page_number=page_num,
                    confidence=elem.confidence,
                    origin_x=text_info["origin_x"],
                    origin_y=text_info["origin_y"],
                    positioned_spans=text_info["positioned_spans"]
                )
                all_blocks.append(block)
        
        # Build document structure from blocks
        content = self._build_document_structure(all_blocks, doc, detection_result)
        content.raw_blocks = all_blocks
        
        logger.info(
            f"Content extracted: {len(content.sections)} sections, "
            f"{len(content.figures)} figures, {len(content.tables)} tables, "
            f"{len(content.references)} references"
        )
        
        return content
    
    def _reconstruct_math_from_spans(
        self, 
        spans: List[PositionedSpan], 
        drawings: List[Dict] = None
    ) -> str:
        """Reconstruct a LaTeX formula from positioned spans with robust layout analysis."""
        if not spans:
            return ""
            
        # 1. Detect fractions and radicals using drawings (horizontal bars)
        if drawings:
            # Sort drawings by width descending to handle nested structures
            sorted_rules = sorted(
                [d for d in drawings if d.get("type") == "rule"], 
                key=lambda d: d["width"], 
                reverse=True
            )
            for rule in sorted_rules:
                # Find spans associated with this rule (numerator/denominator)
                num_spans = [
                    s for s in spans 
                    if s.y < rule["y"] - 0.5 and 
                       rule["x0"] - 2.0 <= s.x <= rule["x1"] + 2.0
                ]
                den_spans = [
                    s for s in spans 
                    if s.y > rule["y"] + 0.5 and 
                       rule["x0"] - 2.0 <= s.x <= rule["x1"] + 2.0
                ]
                
                if num_spans and den_spans:
                    # It's a fraction
                    num_tex = self._reconstruct_math_from_spans(num_spans)
                    den_tex = self._reconstruct_math_from_spans(den_spans)
                    frac_tex = f"\\frac{{{num_tex}}}{{{den_tex}}}"
                    
                    min_x = min(s.x for s in num_spans + den_spans)
                    avg_size = sum(s.font_size for s in num_spans + den_spans) / len(num_spans + den_spans)
                    
                    virtual_span = PositionedSpan(
                        text=frac_tex, x=min_x, y=rule["y"], font_size=avg_size,
                        is_bold=False, is_italic=False, is_monospace=False, is_math=True,
                        color="#000000", width=rule["width"]
                    )
                    processed_ids = {id(s) for s in num_spans + den_spans}
                    spans = [s for s in spans if id(s) not in processed_ids]
                    spans.append(virtual_span)
                    spans.sort(key=lambda s: (s.y, s.x))

        # 2. Detect Operators with arguments (like \sqrt)
        radical_spans = [s for s in spans if s.text == "(cid:112)" or "√" in s.text]
        for rad in radical_spans:
            # More precise argument detection: only spans that are horizontally 
            # within the radical's expected reach and vertically aligned.
            potential_args = [
                s for s in spans 
                if id(s) != id(rad) and 
                   rad.x + 1.0 <= s.x <= rad.x + rad.width + 45.0 and # Increased reach for d_k
                   abs(s.y - rad.y) < rad.font_size * 1.2 # Increased vertical reach for subscripts
            ]
            
            # Refine reach based on actual argument content
            if potential_args:
                sorted_potential = sorted(potential_args, key=lambda s: s.x)
                arg_spans = []
                for i, s in enumerate(sorted_potential):
                    txt = s.text.strip()
                    # delimiters signify end of radical argument
                    if txt in [",", ";", ".", "V", "W", ")"]:
                        break
                    
                    if i > 0:
                        prev = sorted_potential[i-1]
                        gap = s.x - (prev.x + prev.width)
                        # radical arguments are usually tight
                        if gap > rad.font_size * 0.4: # Increased gap for multi-span d_k
                            break
                    arg_spans.append(s)
                
                if arg_spans:
                    arg_tex = self._reconstruct_math_from_spans(arg_spans)
                    # Force capturing d_k etc by ensuring we don't leave fragments
                    rad_tex = f"\\sqrt{{{arg_tex}}}"
                    new_width = max(s.x + s.width for s in arg_spans) - rad.x
                    virtual_span = PositionedSpan(
                        text=rad_tex, x=rad.x, y=rad.y, font_size=rad.font_size,
                        is_bold=False, is_italic=False, is_monospace=False, is_math=True,
                        color=rad.color, width=new_width
                    )
                    processed_ids = {id(s) for s in arg_spans + [rad]}
                    spans = [s for s in spans if id(s) not in processed_ids]
                    spans.append(virtual_span)
                    spans.sort(key=lambda s: (s.y, s.x))

        # 3. Group remaining spans by lines
        lines_map = defaultdict(list)
        for s in spans:
            found_line = False
            for y_key in lines_map.keys():
                if abs(s.y - y_key) < 6.0: 
                    lines_map[y_key].append(s)
                    found_line = True
                    break
            if not found_line:
                lines_map[s.y].append(s)
        
        sorted_y = sorted(lines_map.keys())
        line_results = []
        for y in sorted_y:
            line_spans = sorted(lines_map[y], key=lambda x: x.x)
            if not line_spans: continue
            
            baseline_y = max(line_spans, key=lambda s: s.font_size).y
            parts = []
            
            current_subs = []
            current_sups = []
            
            def assemble_spans(group):
                if not group: return ""
                res_parts = []
                for i, gs in enumerate(group):
                    gt = gs.text.strip()
                    if gt in ["_", "^"]: continue
                    
                    # Space detection within group
                    if i > 0:
                        prev = group[i-1]
                        gap = gs.x - (prev.x + prev.width)
                        # More conservative gap for math tokens
                        if gap > gs.font_size * 0.22: # Increased from 0.18
                            res_parts.append(" ")
                    
                    if "\\" in gt:
                        res_parts.append(gt)
                        continue
                        
                    # CID conversions with better mappings
                    if gt == "(cid:80)": gt = "\\sum"
                    elif gt == "(cid:81)": gt = "\\prod"
                    elif gt == "(cid:82)": gt = "\\int"
                    elif gt == "(cid:82)(cid:82)": gt = "\\iint"
                    elif gt == "(cid:215)": gt = "\\times"
                    elif gt == "(cid:122)": gt = "\\cdot"
                    elif gt == "(cid:100)": gt = "d"
                    elif gt == "(cid:121)": gt = "="
                    elif gt == "(cid:43)": gt = "+"
                    elif gt == "(cid:45)": gt = "-"
                    elif gt == "(cid:107)": gt = "k" # cmmi10 'k'
                    elif gt == "(cid:109)": gt = "m" # cmmi10 'm'
                    elif gt == "(cid:113)": gt = "q" # cmmi10 'q'
                    elif gt == "(cid:40)": gt = "("
                    elif gt == "(cid:41)": gt = ")"
                    elif gt == "(cid:44)": gt = ","
                    elif gt == "...": gt = "\\dots"
                    elif gt == "…": gt = "\\dots"
                    
                    # Split alphanumeric indices like 'z1' -> 'z_1', 'dk' -> 'd_k'
                    if not any(pref in gs.text.lower() for pref in ["cmmi", "cmsy", "msbm"]):
                        # Handle common base-index patterns and matrix products
                        if gt == "dk": gt = "d_{k}"
                        elif gt == "dv": gt = "d_{v}"
                        elif gt == "dm": gt = "d_{m}"
                        elif gt == "dmodel": gt = "d_{\\text{model}}"
                        elif gt == "dff": gt = "d_{\\text{ff}}"
                        elif gt == "head1": gt = "head_{1}"
                        elif gt == "headh": gt = "head_{h}"
                        elif gt == "headi": gt = "head_{i}"
                        elif gt == "headj": gt = "head_{j}"
                        elif gt == "QK": gt = "Q K"
                        elif gt == "QW": gt = "Q W"
                        elif gt == "KW": gt = "K W"
                        elif gt == "VW": gt = "V W"
                        elif gt == "PE": gt = "\\text{PE}"
                        elif gt == "pos": gt = "\\text{pos}"
                        elif gt == "model": gt = "\\text{model}"
                        # Transpose handling: QKT -> Q K^{T}
                        elif "T" in gt and len(gt) > 1 and gt.isupper() and not any(p in gt.lower() for p in ["the", "at", "to"]):
                             gt = gt.replace("T", "^{T}")
                        # Final check for simple variable-index patterns (e.g. z1, zn, d_k)
                        elif re.match(r'^[a-z][\dinmjk\d]$', gt.lower()):
                             gt = f"{gt[0]}_{{{gt[1]}}}"
                        elif re.match(r'^head[1-9hi]$', gt):
                            gt = f"head_{{{gt[-1]}}}"
                    
                    if "\\" in gt:
                        res_parts.append(gt)
                        continue
                        
                    # Heuristic for multi-character text in math
                    operators = ["softmax", "sin", "cos", "max", "log", "Attention", "MultiHead", "Concat", "FFN", "PE", "where", "model", "pos", "step", "num", "warmup", "steps"]
                    is_op = any(op.lower() == gt.lower() for op in operators)
                    
                    if is_op:
                        # Find the actual case-sensitive operator name
                        actual_op = next((op for op in operators if op.lower() == gt.lower()), gt)
                        res_parts.append(f"\\text{{{actual_op}}}")
                        if i + 1 < len(group) and group[i+1].text.strip() not in ["(", "[", "{"]:
                             res_parts.append("\\,")
                    elif len(gt) > 1 and gt.isalpha() and not any(pref in gs.text.lower() for pref in ["cmmi", "cmsy", "msbm"]):
                        res_parts.append(f"\\text{{{gt}}}")
                    else:
                        res_parts.append(gt)
                return "".join(res_parts).strip()

            def flush_sub_sup():
                res = ""
                if current_subs:
                    res += f"_{{{assemble_spans(current_subs)}}}"
                if current_sups:
                    res += f"^{{{assemble_spans(current_sups)}}}"
                return res

            for s in line_spans:
                text = s.text.strip()
                if not text or text in ["_", "^"]: continue
                
                v_offset = baseline_y - s.y
                # Sub/sup detection with font size as primary signal
                max_fs = max(ls.font_size for ls in line_spans)
                
                # If font size is significantly smaller, it's likely a sub/sup
                is_sub = False
                is_sup = False
                
                # More sensitive font size ratio for scientific notation/indices
                if s.font_size < max_fs * 0.95: # Increased sensitivity from 0.92
                    # If it's smaller, any significant offset makes it sub/sup
                    if v_offset < -0.5: is_sub = True # Lowered from 0.6
                    elif v_offset > 0.5: is_sup = True # Lowered from 0.6
                    # Even if offset is tiny, if it's much smaller and we have a base, it might be sub/sup
                    elif parts:
                        # Heuristic: if it's a number after a letter, it's often a subscript even if baseline is close
                        # Also handle single letters like 'n', 'm', 'i' after another letter/number
                        last_part = parts[-1].strip()
                        if (text.isdigit() or text in ['n', 'm', 'i', 'k', 'j']) and last_part and last_part[-1].isalpha():
                             is_sub = True
                else:
                    # Same font size, only if offset is really large
                    if v_offset < -s.font_size * 0.2: is_sub = True # Lowered from 0.22
                    elif v_offset > s.font_size * 0.2: is_sup = True # Lowered from 0.22
                
                if "\\" in s.text: is_sub = is_sup = False
                
                if not is_sub and not is_sup:
                    # New base
                    parts.append(flush_sub_sup())
                    current_subs = []
                    current_sups = []
                    
                    # Check for gap before base
                    if parts:
                        # Find previous span that wasn't empty or a marker
                        prev = next((ls for ls in reversed(line_spans[:line_spans.index(s)]) if ls.text.strip() not in ["", "_", "^"]), None)
                        if prev:
                            gap = s.x - (prev.x + prev.width)
                            # Only add space if gap is significant
                            if gap > s.font_size * 0.25: # Increased from 0.22
                                parts.append(" ")
                            elif gap > s.font_size * 0.15 and re.search(r'[=+×-]', text):
                                # Add thin space before operators
                                parts.append("\\,")
                    
                    parts.append(assemble_spans([s]))
                elif is_sub:
                    current_subs.append(s)
                elif is_sup:
                    current_sups.append(s)
            
            parts.append(flush_sub_sup())
            line_results.append("".join(parts))
            
        return " ".join(line_results).strip()

    def _extract_text_and_style(
        self, 
        page: fitz.Page,
        bbox: BoundingBox,
        class_name: str = "",
        drawings: List[Dict] = None
    ) -> Dict:
        """Extract text, style and precise baseline origin from a bounding box."""
        rect = fitz.Rect(bbox.x0, bbox.y0, bbox.x1, bbox.y1)
        
        # Get low-level spans for styling and positioning
        blocks = page.get_text("dict", clip=rect)["blocks"]
        
        positioned_spans = []
        style = TextStyle()
        origin_x = 0.0
        origin_y = 0.0
        first_overall_span = True
        
        # Math detection heuristics
        math_symbols = r'[=+\×÷√≤≥≠≈∞∈∉⊂⊃∩∪∑∏∫∂∇±÷·°±∕∖⋇⋈⋉⋊⋋⋌⋏⋎⋐⋑⋒⋓⋔⋕⋖⋗⋘⋙⋚⋛⋜⋝⋞⋟⋠⋡⋢⋣⋤⋥⋦⋧⋨⋩⋪⋫⋬⋭]'
        math_font_prefixes = ["cmmi", "cmsy", "msbm", "amsfonts"]
        
        math_spans_count = 0
        total_spans_count = 0
        
        for block in blocks:
            if block.get("type") != 0: continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    span_text = span.get("text", "")
                    sx, sy = span.get("origin", (0, 0))
                    sf_size = span.get("size", 12.0)
                    sflags = span.get("flags", 0)
                    sfont = span.get("font", "").lower()
                    
                    is_bold = bool(sflags & 16) or any(k in sfont for k in ["bold", "medi", "black", "heavy", "demi"])
                    is_italic = bool(sflags & 2) or any(k in sfont for k in ["ital", "obli"])
                    is_monospace = bool(sflags & 8) or any(k in sfont for k in ["mono", "courier", "menlo", "consolas", "fixed", "typewriter", "teletype", "nimbusmono"])
                    
                    is_math = (class_name in ["isolate_formula", "formula_caption"])
                    if not is_math:
                        if any(pref in sfont for pref in math_font_prefixes): is_math = True
                        elif re.fullmatch(math_symbols, span_text.strip()): is_math = True
                        elif is_italic and len(span_text.strip()) == 1 and span_text.strip().isalpha():
                            if span_text.strip().lower() not in ["a", "i"]: is_math = True
                        elif "(cid:" in span_text: is_math = True

                    if not (class_name == "isolate_formula"):
                        if span_text.strip().count(' ') > 1 and not any(pref in sfont for pref in math_font_prefixes): is_math = False
                        if span_text.strip().lower() in ["to", "of", "and", "the", "with", "from", "for", "in", "on", "at", "by", "an", "as", "is", "it", "attention", "transformer", "encoder", "decoder", "this", "that", "these", "those"]:
                            is_math = False
                        if len(span_text.strip()) > 3 and not any(pref in sfont for pref in math_font_prefixes):
                            letters = sum(1 for c in span_text if c.isalpha())
                            if letters > len(span_text.strip()) * 0.8: is_math = False
                    
                    if is_math: math_spans_count += 1
                    total_spans_count += 1
                    
                    color_hex = "#000000"
                    color_int = span.get("color", 0)
                    if isinstance(color_int, int):
                        r, g, b = (color_int >> 16) & 0xFF, (color_int >> 8) & 0xFF, color_int & 0xFF
                        color_hex = f"#{r:02x}{g:02x}{b:02x}"
                    
                    sbbox = span.get("bbox", (0,0,0,0))
                    p_span = PositionedSpan(
                        text=span_text, x=sx, y=sy, font_size=sf_size, is_bold=is_bold,
                        is_italic=is_italic, is_monospace=is_monospace, is_math=is_math,
                        color=color_hex, width=sbbox[2] - sbbox[0]
                    )
                    positioned_spans.append(p_span)
                    if first_overall_span:
                        style.font_name, style.font_size, style.is_bold = span.get("font", ""), sf_size, is_bold
                        style.is_italic, style.is_monospace, style.color = is_italic, is_monospace, color_hex
                        origin_x, origin_y, first_overall_span = sx, sy, False
        
        if not class_name in ["isolate_formula", "formula_caption"] and total_spans_count > 0:
            # More conservative reclassification
            if math_spans_count / total_spans_count > 0.7:
                class_name = "isolate_formula"
                for s in positioned_spans: s.is_math = True

        if class_name in ["isolate_formula", "formula_caption"]:
            final_text = self._reconstruct_math_from_spans(positioned_spans, drawings=drawings)
        else:
            final_text = page.get_text("text", clip=rect, flags=fitz.TEXT_PRESERVE_WHITESPACE).strip()
            
        return {
            "text": final_text, "style": style, "origin_x": origin_x,
            "origin_y": origin_y, "positioned_spans": positioned_spans
        }
    
    def _classify_block(
        self,
        elem: DetectedElement,
        text: str,
        page_num: int
    ) -> BlockType:
        """Classify a text block based on detection and content."""
        # Use YOLO class first
        block_type = self.CLASS_TO_BLOCK_TYPE.get(
            elem.class_name, BlockType.UNKNOWN
        )
        
        # Refine classification with heuristics
        text_lower = text.lower().strip()
        
        # Title elements on page > 0 are section headings, not document title
        if elem.class_name == "title" and page_num > 0:
            return BlockType.SECTION_HEADING
        
        # Check for section headings
        if elem.class_name == "plain_text":
            # On first page, check for author blocks first
            if page_num == 0 and self._looks_like_author_block(text):
                return BlockType.AUTHOR_LIST
            
            # Short text with specific patterns might be headings
            if len(text) < 100 and self._looks_like_heading(text, page_num):
                return BlockType.SECTION_HEADING
            
            # Check for list items
            if re.match(r'^[\d•\-\*]\s', text):
                return BlockType.LIST_ITEM
        
        # Check for special labels
        if page_num == 0:
            if text_lower in ["abstract", "summary", "аннотация"]:
                return BlockType.ABSTRACT_LABEL
            if text_lower.startswith("keywords") or text_lower.startswith("key words"):
                return BlockType.KEYWORDS_LABEL
        
        # Check for references section
        if text_lower in ["references", "bibliography", "литература", "список литературы"]:
            return BlockType.REFERENCES_HEADING
        
        # Check for acknowledgements
        if text_lower.startswith("acknowledgement") or text_lower.startswith("acknowledgment"):
            return BlockType.ACKNOWLEDGEMENTS
        
        # Check for DOI
        if "doi" in text_lower or "10.1" in text:
            return BlockType.DOI
        
        # Check for footnotes (at bottom of page, starting with markers like †, ‡, *, or numbers)
        if elem.class_name in ["plain_text", "footnote"]:
            if re.match(r'^[†‡\*\d]+\s*', text) or text.startswith("†") or text.startswith("‡"):
                # Additional check: footnotes are usually short and at bottom
                if len(text) < 200:
                    return BlockType.FOOTNOTE
        
        return block_type
    
    def _looks_like_heading(self, text: str, page_num: int = 0) -> bool:
        """Check if text looks like a section heading."""
        text = text.strip()
        
        # Numbered headings: "1 Introduction", "3.2 Attention"
        if re.match(r'^\d+(\.\d+)*\.?\s*', text):
            # If it's just a number, it's likely a heading part or page number (if short)
            if re.match(r'^\d+(\.\d+)*\.?$', text) and len(text) < 10:
                return True
            # Number followed by text
            if re.match(r'^\d+(\.\d+)*\.?\s+\w', text):
                return True
        
        # Multi-line numbered heading: "1\nIntroduction" or "2\nBackground"
        lines = text.split('\n')
        if len(lines) == 2:
            first_line = lines[0].strip()
            second_line = lines[1].strip()
            if re.match(r'^\d+(\.\d+)*$', first_line) and len(second_line) < 50:
                return True
        
        # All caps short text
        if text.isupper() and len(text.split()) <= 5:
            return True
        
        # Known section names (case-insensitive)
        known_sections = [
            'introduction', 'background', 'related work', 'methods', 'methodology',
            'results', 'discussion', 'conclusion', 'conclusions', 'abstract',
            'acknowledgements', 'acknowledgments', 'references', 'appendix',
            'experiments', 'evaluation', 'model', 'model architecture', 'training',
            'attention', 'self-attention', 'multi-head attention', 'encoder', 'decoder'
        ]
        text_lower = text.lower().strip()
        if text_lower in known_sections:
            return True
        
        return False
    
    def _looks_like_author_block(self, text: str) -> bool:
        """Check if text looks like an author/affiliation block."""
        text_lower = text.lower()
        text_stripped = text.strip()
        
        # Must be short (author blocks are typically < 150 chars)
        if len(text_stripped) > 150:
            return False
        
        # Contains email - strong indicator
        if '@' in text and '.com' in text_lower or '.edu' in text_lower or '.org' in text_lower:
            return True
        
        # Multi-line with email pattern: name + affiliation + email
        lines = text_stripped.split('\n')
        if 2 <= len(lines) <= 4 and all(len(line) < 50 for line in lines):
            # Check if any line looks like email
            if any('@' in line for line in lines):
                return True
        
        return False
    
    def _build_document_structure(
        self,
        blocks: List[TextBlock],
        doc: fitz.Document,
        detection_result: DocumentDetectionResult
    ) -> DocumentContent:
        """Build structured document content from blocks."""
        content = DocumentContent()
        
        # 1. Page 0 special handling: identify the real Title
        page0_blocks = [b for b in blocks if b.page_number == 0]
        if page0_blocks:
            # Title is typically the block with largest font size * area
            title_candidates = [b for b in page0_blocks if b.style.font_size > 14]
            if title_candidates:
                actual_title_block = max(title_candidates, key=lambda b: b.style.font_size * b.bbox.area)
                content.title = actual_title_block.text
                # Ensure it's marked as title
                actual_title_block.block_type = BlockType.DOCUMENT_TITLE
                
                # Downgrade other 'title' blocks to prevent multiple giant headers
                for b in page0_blocks:
                    if b != actual_title_block and b.block_type == BlockType.DOCUMENT_TITLE:
                        if "@" in b.text or self._looks_like_author_block(b.text):
                            b.block_type = BlockType.AUTHOR_LIST
                        else:
                            b.block_type = BlockType.PARAGRAPH
            else:
                # Fallback to current logic
                title_blocks = [b for b in blocks if b.block_type == BlockType.DOCUMENT_TITLE and b.page_number == 0]
                if title_blocks:
                    content.title = title_blocks[0].text
        
        # Extract authors from AUTHOR_LIST blocks with geometry
        author_blocks = [b for b in blocks if b.block_type == BlockType.AUTHOR_LIST]
        
        # Collect (y, x, block) for all authors
        author_positions = []
        for ab in author_blocks:
            if ab.bbox:
                author_positions.append((ab.bbox.y0, ab.bbox.x0, ab))
        
        # Group by y-coordinate (rows) - cluster within 20pt tolerance
        rows_dict = {}
        for y, x, ab in author_positions:
            # Find or create row
            row_key = None
            for existing_y in rows_dict.keys():
                if abs(y - existing_y) < 20:
                    row_key = existing_y
                    break
            if row_key is None:
                row_key = y
                rows_dict[row_key] = []
            rows_dict[row_key].append((x, ab))
        
        # Sort rows by y, and within each row sort by x
        sorted_row_keys = sorted(rows_dict.keys())
        
        for row_idx, row_y in enumerate(sorted_row_keys):
            # Sort authors in this row by x position
            row_authors = sorted(rows_dict[row_y], key=lambda t: t[0])
            
            for col_idx, (x_pos, ab) in enumerate(row_authors):
                # Parse author block: typically "Name\nAffiliation\nemail"
                lines = ab.text.strip().split('\n')
                name = lines[0].strip() if lines else ""
                
                # IMPROVED MARKER EXTRACTION: Check for markers (*, †, etc.) in spans
                markers = ""
                # Include standard markers and common small asterisk/dagger variants
                marker_symbols = ["*", "†", "‡", "§", "¶", "||", "∗"] 
                
                if hasattr(ab, 'positioned_spans'):
                    for span in ab.positioned_spans:
                        st = span.text.strip()
                        # Markers are often isolate symbols, CID codes, or tiny font
                        if any(m in st for m in marker_symbols) or "(cid:" in st:
                             # If it's a very short span near the name line
                             if len(st) <= 4:
                                 # Convert CID to common marker if known
                                 if "(cid:42)" in st or "(cid:173)" in st: st = "*"
                                 elif "(cid:134)" in st: st = "†"
                                 elif "(cid:135)" in st: st = "‡"
                                 
                                 # Only append if it's a known marker symbol
                                 clean_marker = st.strip()
                                 if any(m in clean_marker for m in marker_symbols):
                                     markers += clean_marker
                
                # Append markers to name if they are not already there
                if markers:
                    # Clean name from existing partial markers to avoid duplicates
                    name_clean = name
                    for m in marker_symbols:
                        name_clean = name_clean.replace(m, "")
                    name = name_clean.strip() + markers
                
                affiliation = ""
                email = ""
                
                # Get precise font size and baseline from the block
                font_size = ab.style.font_size
                # Use captured origins for precise positioning
                x_pos_precise = ab.origin_x
                y_baseline_precise = ab.origin_y
                
                # Find email first
                for line in lines:
                    if '@' in line:
                        email = line.strip()
                        break
                
                # Affiliation is second line ONLY if it doesn't contain @
                if len(lines) > 1:
                    potential_affiliation = lines[1].strip()
                    if '@' not in potential_affiliation:
                        affiliation = potential_affiliation
                
                if name:
                    content.authors.append(AuthorInfo(
                        name=name,
                        affiliations=[affiliation] if affiliation else [],
                        email=email,
                        row_index=row_idx,
                        col_index=col_idx,
                        x_position=x_pos_precise,
                        y_baseline=y_baseline_precise,
                        font_size=font_size,
                        spans=getattr(ab, 'positioned_spans', [])
                    ))
        
        # Extract abstract - look for text after "Abstract" label or ABSTRACT_TEXT blocks
        abstract_blocks = [b for b in blocks if b.block_type == BlockType.ABSTRACT_TEXT]
        if abstract_blocks:
            content.abstract = " ".join(b.text for b in abstract_blocks)
        else:
            # Try to find abstract from first page paragraphs after title
            first_page_paragraphs = [
                b for b in blocks 
                if b.page_number == 0 and b.block_type == BlockType.PARAGRAPH
            ]
            # Abstract is typically the first substantial paragraph after authors
            for p in first_page_paragraphs:
                if len(p.text) > 200 and not self._looks_like_author_block(p.text):
                    content.abstract = p.text
                    break
        
        # Extract keywords
        keyword_blocks = [b for b in blocks if b.block_type == BlockType.KEYWORDS_TEXT]
        for kb in keyword_blocks:
            # Parse keywords (usually comma-separated)
            text = kb.text
            if ":" in text:
                text = text.split(":", 1)[1]
            keywords = [k.strip() for k in text.split(",")]
            content.keywords.extend(keywords)
        
        # Extract figures and tables first to have them ready for insertion
        content.figures = self._extract_figures(blocks, detection_result, doc)
        content.tables = self._extract_tables(blocks, detection_result, doc)
        
        # Build unified sections including figures, tables and references in flow
        content.sections = self._build_sections_unified(blocks, content.abstract, content.figures, content.tables)
        
        # Extract footnotes from classified blocks
        footnote_blocks = [b for b in blocks if b.block_type == BlockType.FOOTNOTE]
        for fb in footnote_blocks:
            content.footnotes.append(Footnote(
                marker="", # Marker is usually part of the text
                text=fb.text,
                page_number=fb.page_number,
                baseline_y=fb.origin_y,
                spans=getattr(fb, 'positioned_spans', [])
            ))
        
        # Fallback: extract footnotes directly from PDF (bottom of pages)
        if not content.footnotes:
            content.footnotes = self._extract_footnotes_from_pdf(doc)
        
        # Extract references
        reference_blocks = [b for b in blocks if b.block_type == BlockType.REFERENCE_ITEM]
        for i, rb in enumerate(reference_blocks):
            content.references.append(Reference(
                number=f"[{i + 1}]",
                text=rb.text
            ))
        
        # Fallback: extract references from PDF if none detected
        if not content.references:
            content.references = self._extract_references_from_pdf(doc)
        
        # Extract acknowledgements
        ack_blocks = [b for b in blocks if b.block_type == BlockType.ACKNOWLEDGEMENTS]
        if ack_blocks:
            content.acknowledgements = " ".join(b.text for b in ack_blocks)
        
        return content
    
    def _build_sections_unified(
        self, 
        blocks: List[TextBlock], 
        abstract_text: str = "",
        figures: List[Figure] = [],
        tables: List[Table] = []
    ) -> List[Section]:
        """Build a unified section hierarchy preserving original document flow."""
        sections: List[Section] = []
        current_section: Optional[Section] = None
        seen_headings: set = set()
        
        # Sort all relevant items by page and Y coordinate to preserve flow
        flow_items = []
        
        # Add text blocks
        for b in blocks:
            if b.page_number == 0: continue
            if abstract_text and b.text.strip() in abstract_text: continue
            
            if b.block_type in [
                BlockType.SECTION_HEADING,
                BlockType.PARAGRAPH,
                BlockType.LIST_ITEM,
                BlockType.REFERENCE_ITEM,
                BlockType.REFERENCES_HEADING,
                BlockType.EQUATION
            ]:
                flow_items.append({
                    "type": "block",
                    "page": b.page_number,
                    "y": b.bbox.y0,
                    "data": b
                })
        
        # Add figures
        for fig in figures:
            flow_items.append({
                "type": "figure",
                "page": fig.page_number,
                "y": fig.bbox.y0,
                "data": fig
            })
            
        # Add tables
        for table in tables:
            flow_items.append({
                "type": "table",
                "page": table.page_number,
                "y": table.bbox.y0 if table.bbox else 0,
                "data": table
            })
            
        # Sort everything by page then Y
        flow_items.sort(key=lambda x: (x["page"], x["y"]))
        
        # References should be collected into a single list
        references_collected = []
        
        # Heading component merging logic
        i = 0
        while i < len(flow_items):
            item = flow_items[i]
            data = item["data"]
            
            # Merge adjacent heading components (e.g., "1" and "Introduction")
            if item["type"] == "block" and data.block_type == BlockType.SECTION_HEADING:
                text = data.text.strip()
                # If this is just a number (e.g., "1"), look ahead for the title
                if re.match(r'^\d+(\.\d+)*\.?$', text) and i + 1 < len(flow_items):
                    next_item = flow_items[i+1]
                    if next_item["type"] == "block" and next_item["page"] == item["page"]:
                        # If next block is close vertically
                        if next_item["y"] - item["y"] < 25:
                            data.text = text + " " + next_item["data"].text.strip()
                            # Update bbox to cover both
                            data.bbox.x1 = max(data.bbox.x1, next_item["data"].bbox.x1)
                            data.bbox.y1 = max(data.bbox.y1, next_item["data"].bbox.y1)
                            flow_items.pop(i + 1)
            
            # Handle the merged or single heading
            if item["type"] == "block" and data.block_type == BlockType.SECTION_HEADING:
                heading_key = data.text.strip().lower()
                if heading_key in seen_headings: 
                    i += 1
                    continue
                seen_headings.add(heading_key)
                
                level = self._determine_heading_level(data.text, data.style)
                current_section = Section(
                    level=level,
                    title=data.text,
                    content=[],
                    page_number=data.page_number
                )
                sections.append(current_section)
                
            elif item["type"] == "block" and data.block_type == BlockType.REFERENCE_ITEM:
                # Store references to be rendered at the end of the document
                # but keep a Reference object in the flow for relative ordering if needed
                ref = Reference(number=f"[{len(references_collected)+1}]", text=data.text)
                references_collected.append(ref)
                if current_section:
                    current_section.content.append(ref)
                else:
                    if not sections: sections.append(Section(level=0, title="", content=[]))
                    sections[-1].content.append(ref)
                    
            elif item["type"] == "block" and data.block_type == BlockType.EQUATION:
                # Try to find an equation number if it's nearby
                eq_num = None
                # Check next few items if it's a short parenthesized number
                search_idx = i + 1
                while search_idx < len(flow_items) and search_idx < i + 4:
                    next_item = flow_items[search_idx]
                    if next_item["type"] == "block" and next_item["page"] == item["page"]:
                        nt = next_item["data"].text.strip()
                        # Match (1), (2a), [1], etc.
                        if re.match(r'^[\(\[]\d+[a-z]?[\)\]]$', nt) and abs(next_item["y"] - item["y"]) < 60:
                            eq_num = nt
                            flow_items.pop(search_idx)
                            break
                    search_idx += 1
                
                # Convert TextBlock to Equation
                eq = Equation(
                    latex=data.text,
                    is_inline=False,
                    number=eq_num
                )
                if current_section:
                    current_section.content.append(eq)
                else:
                    if not sections: sections.append(Section(level=0, title="", content=[]))
                    sections[-1].content.append(eq)
                i += 1
                    
            elif item["type"] == "block" and data.block_type in [BlockType.PARAGRAPH, BlockType.LIST_ITEM]:
                if current_section:
                    current_section.content.append(data)
                else:
                    if not sections: sections.append(Section(level=0, title="", content=[]))
                    sections[-1].content.append(data)
                i += 1
                    
            elif item["type"] == "figure":
                if current_section:
                    current_section.content.append(data)
                else:
                    if not sections: sections.append(Section(level=0, title="", content=[]))
                    sections[-1].content.append(data)
                i += 1
                    
            elif item["type"] == "table":
                if current_section:
                    current_section.content.append(data)
                else:
                    if not sections: sections.append(Section(level=0, title="", content=[]))
                    sections[-1].content.append(data)
                i += 1
            else:
                # Skip unknown types
                i += 1

        return sections

    def _build_sections(self, blocks: List[TextBlock], abstract_text: str = "") -> List[Section]:
        """Build section hierarchy from blocks."""
        sections: List[Section] = []
        current_section: Optional[Section] = None
        seen_headings: set = set()  # Track seen headings to avoid duplicates
        
        # Filter relevant blocks
        relevant_blocks = []
        for b in blocks:
            # Skip ALL blocks from page 0. They are rendered exclusively in _generate_title_page
            # using TikZ absolute positioning for 0.1bp precision.
            if b.page_number == 0:
                continue
            
            # Skip blocks that match abstract text (already on title page)
            if abstract_text and b.text.strip() in abstract_text:
                continue
            
            # Skip paragraphs that look like references (start with [number])
            if re.match(r'^\[\d+\]', b.text.strip()):
                continue
            
            if b.block_type in [
                BlockType.SECTION_HEADING,
                BlockType.PARAGRAPH,
                BlockType.LIST_ITEM
            ]:
                relevant_blocks.append(b)
        
        for block in relevant_blocks:
            if block.block_type == BlockType.SECTION_HEADING:
                # Skip duplicate headings
                heading_key = block.text.strip().lower()
                if heading_key in seen_headings:
                    continue
                seen_headings.add(heading_key)
                
                # Determine section level
                level = self._determine_heading_level(block.text, block.style)
                
                current_section = Section(
                    level=level,
                    title=block.text,
                    content=[],
                    page_number=block.page_number
                )
                sections.append(current_section)
            
            elif block.block_type in [BlockType.PARAGRAPH, BlockType.LIST_ITEM]:
                if current_section:
                    current_section.content.append(block.text)
                else:
                    # Paragraph before any section
                    if not sections:
                        sections.append(Section(level=0, title="", content=[]))
                    sections[-1].content.append(block.text)
        
        return sections
    
    def _determine_heading_level(self, text: str, style: TextStyle) -> int:
        """Determine heading level from text and style."""
        # Check numbered format: "1." = H1, "1.1" = H2, "1.1.1" = H3
        match = re.match(r'^(\d+(?:\.\d+)*)', text)
        if match:
            number = match.group(1)
            dots = number.count('.')
            return dots + 1
        
        # Use font size as fallback
        if style.font_size >= 14:
            return 1
        elif style.font_size >= 12:
            return 2
        else:
            return 3
    
    def _extract_figures(
        self,
        blocks: List[TextBlock],
        detection_result: DocumentDetectionResult,
        doc: fitz.Document
    ) -> List[Figure]:
        """Extract figure captions. Image linking is handled by the pipeline."""
        figures: List[Figure] = []
        seen_captions = {} # (page, normalized_text) -> Figure
        
        caption_blocks = [b for b in blocks if b.block_type == BlockType.FIGURE_CAPTION]
        # Sort captions by page and Y
        caption_blocks.sort(key=lambda b: (b.page_number, b.bbox.y0))

        for i, caption_block in enumerate(caption_blocks):
            # Aggressive deduplication by normalized text and page
            norm_text = re.sub(r'\s+', ' ', caption_block.text.strip().lower())
            # Strip common "Figure X:" prefix for comparison robustness
            norm_text = re.sub(r'^(figure|fig\.?)\s*\d+[:\.]?\s*', '', norm_text)
            
            cap_key = (caption_block.page_number, norm_text)
            if cap_key in seen_captions:
                continue
            
            # Parse figure number from caption
            fig_num = str(i + 1)
            match = re.match(r'(?:Figure|Fig\.?)\s*(\d+[a-z]?)', caption_block.text, re.I)
            if match:
                fig_num = match.group(1)
            
            fig = Figure(
                number=fig_num,
                caption=caption_block.text,
                image_paths=[], # To be filled by _link_images_to_content
                page_number=caption_block.page_number,
                bbox=caption_block.bbox,
                image_bboxes=[] # To be filled by _link_images_to_content
            )
            figures.append(fig)
            seen_captions[cap_key] = fig
        
        return figures

    def _extract_tables(
        self,
        blocks: List[TextBlock],
        detection_result: DocumentDetectionResult,
        doc: fitz.Document
    ) -> List[Table]:
        """Extract tables with captions."""
        tables: List[Table] = []
        seen_captions = set()
        
        caption_blocks = [b for b in blocks if b.block_type == BlockType.TABLE_CAPTION]
        
        for i, caption_block in enumerate(caption_blocks):
            # Deduplicate by text and page
            cap_key = (caption_block.page_number, caption_block.text.strip().lower())
            if cap_key in seen_captions:
                continue
            seen_captions.add(cap_key)
            
            # Parse table number from caption
            table_num = str(i + 1)
            match = re.match(r'Table\s*(\d+)', caption_block.text, re.I)
            if match:
                table_num = match.group(1)
            
            tables.append(Table(
                number=table_num,
                caption=caption_block.text,
                headers=[],
                rows=[],
                page_number=caption_block.page_number,
                bbox=caption_block.bbox
            ))
        
        return tables
    
    def _extract_footnotes_from_pdf(self, doc: fitz.Document) -> List[Footnote]:
        """Extract footnotes directly from PDF by looking at bottom of pages."""
        footnotes = []
        footnote_markers = ["†", "‡", "*", "∗"]
        
        for page_num, page in enumerate(doc):
            page_height = page.rect.height
            # Look at bottom 200pt of page for footnotes (larger area)
            footer_rect = fitz.Rect(0, page_height - 200, page.rect.width, page_height)
            
            # Get full text from footer area
            footer_text = page.get_text("text", clip=footer_rect)
            lines = footer_text.split('\n')
            
            current_footnote = None
            current_marker = None
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                # Check if line starts with footnote marker
                found_marker = None
                for marker in footnote_markers:
                    if line.startswith(marker):
                        found_marker = marker
                        break
                
                if found_marker:
                    # Save previous footnote
                    if current_footnote and current_marker:
                        footnotes.append(Footnote(
                            marker=current_marker,
                            text=current_footnote.strip(),
                            page_number=page_num
                        ))
                    # Start new footnote
                    current_marker = found_marker
                    current_footnote = line
                elif current_footnote:
                    # Stop if this looks like conference/venue line (not part of footnote)
                    if "Conference" in line or "Proceedings" in line or "NIPS" in line or "NeurIPS" in line:
                        # Save current footnote and stop
                        footnotes.append(Footnote(
                            marker=current_marker,
                            text=current_footnote.strip(),
                            page_number=page_num
                        ))
                        current_footnote = None
                        current_marker = None
                    else:
                        # Continue current footnote
                        current_footnote += " " + line
            
            # Save last footnote
            if current_footnote and current_marker:
                footnotes.append(Footnote(
                    marker=current_marker,
                    text=current_footnote.strip(),
                    page_number=page_num
                ))
        
        return footnotes
    
    def _extract_references_from_pdf(self, doc: fitz.Document) -> List[Reference]:
        """Extract references directly from PDF by finding References section."""
        references = []
        in_references = False
        current_ref_text = ""
        current_ref_num = ""
        
        for page_num, page in enumerate(doc):
            text = page.get_text("text")
            lines = text.split('\n')
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                # Check for References heading
                if line.lower() in ["references", "bibliography"]:
                    in_references = True
                    continue
                
                if not in_references:
                    continue
                
                # Stop at next major section (e.g., "Attention Visualizations", "Appendix", etc.)
                # Only stop on specific known section headers that come after references
                stop_headers = ["attention visualizations", "appendix", "supplementary", "figure"]
                if line.lower() in stop_headers or any(line.lower().startswith(h) for h in stop_headers):
                    # Save current reference and stop
                    if current_ref_num and current_ref_text:
                        references.append(Reference(
                            number=f"[{current_ref_num}]",
                            text=current_ref_text.strip()
                        ))
                    in_references = False
                    current_ref_num = ""
                    current_ref_text = ""
                    continue
                
                # Check if this is a new reference (starts with [number])
                ref_match = re.match(r'^\[(\d+)\]\s*(.*)', line)
                if ref_match:
                    # Save previous reference
                    if current_ref_num and current_ref_text:
                        references.append(Reference(
                            number=f"[{current_ref_num}]",
                            text=current_ref_text.strip()
                        ))
                    current_ref_num = ref_match.group(1)
                    current_ref_text = ref_match.group(2)
                elif current_ref_num:
                    # Continue previous reference
                    current_ref_text += " " + line
        
        # Save last reference
        if current_ref_num and current_ref_text:
            references.append(Reference(
                number=f"[{current_ref_num}]",
                text=current_ref_text.strip()
            ))
        
        return references

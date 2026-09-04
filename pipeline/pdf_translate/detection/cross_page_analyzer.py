"""
Cross-Page Semantic Analyzer.

Analyzes content that continues across page boundaries for proper translation context.
"""

import logging
import re
import uuid
from typing import List, Dict, Tuple, Set

import fitz  # PyMuPDF

from ..models import (
    BoundingBox,
    DetectedElement,
    PageDetectionResult,
    DocumentDetectionResult,
    ContinuityType,
    CrossPageLink,
    SemanticUnit,
    CrossPageAnalysisResult,
)

logger = logging.getLogger(__name__)


class CrossPageAnalyzer:
    """
    Analyzes content continuation across page boundaries.
    
    Critical for proper translation: paragraphs split across pages must be
    translated as a single unit to preserve context.
    """
    
    def __init__(self):
        # Patterns for detecting sentence endings
        self.sentence_end_pattern = re.compile(r'[.!?]["\'»)]*\s*$')
        self.sentence_start_pattern = re.compile(r'^[A-ZА-ЯЁ"]')
        
        # Words that typically don't start sentences (continuation indicators)
        self.continuation_words = {
            # English
            'and', 'or', 'but', 'so', 'yet', 'nor', 'for',
            'which', 'that', 'who', 'whom', 'whose', 'where', 'when',
            'however', 'therefore', 'moreover', 'furthermore',
            'although', 'though', 'while', 'whereas',
            # Russian
            'и', 'или', 'но', 'а', 'однако', 'также', 'поэтому',
            'который', 'которая', 'которое', 'которые',
            'что', 'чтобы', 'если', 'хотя', 'потому',
        }
        
        # Element types that commonly continue across pages
        self.continuable_types = {
            'plain_text', 'abstract', 'reference', 'footnote', 
            'table_footnote', 'figure_caption', 'table_caption'
        }
    
    def analyze(
        self,
        detection_result: DocumentDetectionResult,
        doc: fitz.Document
    ) -> CrossPageAnalysisResult:
        """
        Analyze entire document for cross-page continuity.
        
        Args:
            detection_result: Results from LayoutDetector
            doc: PyMuPDF Document for text extraction
        
        Returns:
            CrossPageAnalysisResult with links and semantic units
        """
        links: List[CrossPageLink] = []
        
        # Analyze each pair of adjacent pages
        for page_num in range(len(detection_result.pages) - 1):
            current_page = detection_result.pages[page_num]
            next_page = detection_result.pages[page_num + 1]
            
            page_links = self._analyze_page_pair(
                current_page, next_page, doc, page_num
            )
            links.extend(page_links)
        
        # Build semantic units from links
        semantic_units = self._build_semantic_units(links, detection_result, doc)
        
        # Build element -> unit mapping
        element_to_unit: Dict[Tuple[int, int], str] = {}
        for unit in semantic_units:
            for page, idx in unit.elements:
                element_to_unit[(page, idx)] = unit.unit_id
        
        result = CrossPageAnalysisResult(
            links=links,
            semantic_units=semantic_units,
            element_to_unit=element_to_unit
        )
        
        logger.info(
            f"Cross-page analysis: found {len(links)} links, "
            f"{len(semantic_units)} semantic units"
        )
        
        return result
    
    def _analyze_page_pair(
        self,
        current_page: PageDetectionResult,
        next_page: PageDetectionResult,
        doc: fitz.Document,
        current_page_num: int
    ) -> List[CrossPageLink]:
        """Analyze a pair of adjacent pages for continuity."""
        links: List[CrossPageLink] = []
        
        # Get elements at page boundaries
        bottom_elements = self._get_bottom_elements(current_page)
        top_elements = self._get_top_elements(next_page)
        
        for bottom_idx, bottom_elem in enumerate(bottom_elements):
            if bottom_elem.class_name not in self.continuable_types:
                continue
            
            # Extract text from bottom element
            bottom_text = self._extract_element_text(
                doc[current_page_num], bottom_elem
            )
            
            if not bottom_text or len(bottom_text.strip()) < 10:
                continue
            
            # Check for continuation indicators
            base_indicators = self._check_source_indicators(bottom_text)
            
            if not base_indicators:
                continue  # No signs of continuation
            
            # Find matching element on next page
            for top_idx, top_elem in enumerate(top_elements):
                if top_elem.class_name not in self.continuable_types:
                    continue
                
                top_text = self._extract_element_text(
                    doc[current_page_num + 1], top_elem
                )
                
                if not top_text or len(top_text.strip()) < 10:
                    continue
                
                # Combine indicators
                indicators = list(base_indicators)
                indicators.extend(self._check_target_indicators(top_text, top_elem, bottom_elem))
                
                # Check column alignment
                if self._same_column(bottom_elem, top_elem, current_page):
                    indicators.append("same_column")
                
                # Compute confidence
                confidence = self._compute_confidence(indicators)
                
                if confidence > 0.5:
                    links.append(CrossPageLink(
                        source_page=current_page_num,
                        source_element_index=bottom_idx,
                        source_element_type=bottom_elem.class_name,
                        source_text_end=bottom_text[-50:] if len(bottom_text) > 50 else bottom_text,
                        target_page=current_page_num + 1,
                        target_element_index=top_idx,
                        target_element_type=top_elem.class_name,
                        target_text_start=top_text[:50] if len(top_text) > 50 else top_text,
                        continuity_type=self._determine_continuity_type(
                            bottom_elem, top_elem, indicators
                        ),
                        confidence=confidence,
                        indicators=indicators
                    ))
                    
                    logger.debug(
                        f"Found link: page {current_page_num} -> {current_page_num + 1}, "
                        f"confidence={confidence:.2f}, indicators={indicators}"
                    )
                    break  # Found a match, don't look for more
        
        return links
    
    def _get_bottom_elements(
        self,
        page: PageDetectionResult,
        threshold_ratio: float = 0.15
    ) -> List[DetectedElement]:
        """Get elements in the bottom portion of the page."""
        threshold_y = page.pdf_height_pt * (1 - threshold_ratio)
        
        excluded_types = {"page_header", "page_footer", "page_number"}
        
        bottom = [
            e for e in page.elements
            if e.bbox.y1 > threshold_y and e.class_name not in excluded_types
        ]
        
        # Sort by Y position (bottom to top)
        return sorted(bottom, key=lambda e: -e.bbox.y1)
    
    def _get_top_elements(
        self,
        page: PageDetectionResult,
        threshold_ratio: float = 0.15
    ) -> List[DetectedElement]:
        """Get elements in the top portion of the page."""
        threshold_y = page.pdf_height_pt * threshold_ratio
        
        excluded_types = {"page_header", "page_footer", "page_number"}
        
        top = [
            e for e in page.elements
            if e.bbox.y0 < threshold_y and e.class_name not in excluded_types
        ]
        
        # Sort by Y position (top to bottom)
        return sorted(top, key=lambda e: e.bbox.y0)
    
    def _extract_element_text(
        self,
        page: fitz.Page,
        element: DetectedElement
    ) -> str:
        """Extract text from element's bounding box."""
        rect = fitz.Rect(
            element.bbox.x0,
            element.bbox.y0,
            element.bbox.x1,
            element.bbox.y1
        )
        return page.get_text("text", clip=rect).strip()
    
    def _check_source_indicators(self, text: str) -> List[str]:
        """Check source text for continuation indicators."""
        indicators = []
        text = text.rstrip()
        
        # No sentence-ending punctuation
        if not self.sentence_end_pattern.search(text):
            indicators.append("no_sentence_end")
        
        # Ends with hyphen (word hyphenation)
        if text.endswith('-'):
            indicators.append("hyphenation")
        
        # Ends with comma or semicolon
        if text.endswith(',') or text.endswith(';'):
            indicators.append("ends_with_comma")
        
        return indicators
    
    def _check_target_indicators(
        self,
        text: str,
        target_elem: DetectedElement,
        source_elem: DetectedElement
    ) -> List[str]:
        """Check target text for continuation indicators."""
        indicators = []
        
        # Starts with lowercase
        if text and text[0].islower():
            indicators.append("lowercase_start")
        
        # Starts with continuation word
        first_word = text.split()[0].lower() if text.split() else ""
        if first_word in self.continuation_words:
            indicators.append("continuation_word")
        
        # Same element type
        if source_elem.class_name == target_elem.class_name:
            indicators.append("same_element_type")
        
        return indicators
    
    def _same_column(
        self,
        elem1: DetectedElement,
        elem2: DetectedElement,
        page: PageDetectionResult
    ) -> bool:
        """Check if elements are in the same column."""
        center1_x = elem1.bbox.center_x
        center2_x = elem2.bbox.center_x
        
        # Tolerance: 10% of page width
        tolerance = page.pdf_width_pt * 0.1
        
        return abs(center1_x - center2_x) < tolerance
    
    def _compute_confidence(self, indicators: List[str]) -> float:
        """Compute confidence score from indicators."""
        weights = {
            "no_sentence_end": 0.3,
            "hyphenation": 0.5,
            "ends_with_comma": 0.2,
            "lowercase_start": 0.4,
            "continuation_word": 0.3,
            "same_element_type": 0.2,
            "same_column": 0.1,
        }
        
        score = sum(weights.get(ind, 0) for ind in indicators)
        return min(score, 1.0)
    
    def _determine_continuity_type(
        self,
        source: DetectedElement,
        target: DetectedElement,
        indicators: List[str]
    ) -> ContinuityType:
        """Determine the type of continuation."""
        if source.class_name == "footnote" or source.class_name == "table_footnote":
            return ContinuityType.FOOTNOTE
        elif source.class_name == "table":
            return ContinuityType.TABLE
        elif source.class_name == "reference":
            return ContinuityType.REFERENCE_LIST
        elif source.class_name == "figure_caption":
            return ContinuityType.FIGURE_CAPTION
        elif "no_sentence_end" in indicators:
            return ContinuityType.SENTENCE
        else:
            return ContinuityType.PARAGRAPH
    
    def _build_semantic_units(
        self,
        links: List[CrossPageLink],
        detection_result: DocumentDetectionResult,
        doc: fitz.Document
    ) -> List[SemanticUnit]:
        """Build semantic units from cross-page links using Union-Find."""
        # Union-Find structure
        parent: Dict[Tuple[int, int], Tuple[int, int]] = {}
        
        def find(x: Tuple[int, int]) -> Tuple[int, int]:
            if x not in parent:
                parent[x] = x
            if parent[x] != x:
                parent[x] = find(parent[x])
            return parent[x]
        
        def union(x: Tuple[int, int], y: Tuple[int, int]):
            px, py = find(x), find(y)
            if px != py:
                parent[px] = py
        
        # Union linked elements
        for link in links:
            source = (link.source_page, link.source_element_index)
            target = (link.target_page, link.target_element_index)
            union(source, target)
        
        # Group elements by their root
        groups: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
        for elem in parent:
            root = find(elem)
            if root not in groups:
                groups[root] = []
            groups[root].append(elem)
        
        # Create semantic units
        semantic_units: List[SemanticUnit] = []
        
        for root, elements in groups.items():
            # Sort elements by page, then position
            elements.sort()
            
            # Collect full text
            texts = []
            for page_num, elem_idx in elements:
                page_result = detection_result.get_page(page_num)
                if elem_idx < len(page_result.elements):
                    elem = page_result.elements[elem_idx]
                    text = self._extract_element_text(doc[page_num], elem)
                    texts.append(text)
            
            # Handle hyphenation
            full_text = self._join_hyphenated_text(texts)
            
            # Determine unit type
            first_elem = detection_result.get_page(elements[0][0]).elements[elements[0][1]]
            unit_type = first_elem.class_name
            
            semantic_units.append(SemanticUnit(
                unit_id=str(uuid.uuid4())[:8],
                unit_type=unit_type,
                elements=elements,
                full_text=full_text,
                continues_from_previous=False,  # Could enhance this
                continues_to_next=False
            ))
        
        return semantic_units
    
    def _join_hyphenated_text(self, texts: List[str]) -> str:
        """Join text parts, handling hyphenation at boundaries."""
        if not texts:
            return ""
        
        result = texts[0]
        
        for text in texts[1:]:
            # If previous part ends with hyphen, join without space
            if result.endswith('-'):
                result = result[:-1] + text
            else:
                result = result + " " + text
        
        return result

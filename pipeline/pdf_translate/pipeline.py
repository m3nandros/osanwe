"""
PDF Translation Pipeline.

Main coordinator for the PDF translation process.
"""

import logging
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, Any

import fitz  # PyMuPDF

from .detection import LayoutDetector, CrossPageAnalyzer
from .extractors import (
    GeometryExtractor,
    ElementExtractor,
    ContentExtractor,
    FontAnalyzer,
)
from .generators import TemplateGenerator, LaTeXAssembler, PDFCompiler
from .translation import ContentTranslator
from .models import (
    DocumentDetectionResult,
    CrossPageAnalysisResult,
    DocumentGeometry,
    ExtractedElements,
    DocumentContent,
)

logger = logging.getLogger(__name__)


@dataclass
class PipelineConfig:
    """Configuration for the PDF translation pipeline."""
    # Translation settings
    source_language: str = "en"
    target_language: str = "ru"
    translate_labels: bool = True
    preserve_abbreviations: bool = True
    
    # Processing settings
    detection_confidence: float = 0.25
    detection_dpi: int = 150
    
    # Compilation settings
    latex_engine: str = "xelatex"
    compilation_passes: int = 2
    compilation_timeout: int = 120
    
    # Output settings
    save_debug_files: bool = False
    images_folder: str = "images"


@dataclass
class PipelineResult:
    """Result of pipeline execution."""
    success: bool
    output_path: Optional[Path] = None
    error_message: str = ""
    
    # Intermediate results (if debug mode)
    detection_result: Optional[DocumentDetectionResult] = None
    geometry: Optional[DocumentGeometry] = None
    content: Optional[DocumentContent] = None
    translated_content: Optional[DocumentContent] = None
    latex_content: Optional[str] = None
    
    # Statistics
    stats: Dict[str, Any] = field(default_factory=dict)


class PDFTranslationPipeline:
    """
    Main pipeline for translating PDF documents.
    
    Architecture: PDF → Layout Analysis → LaTeX Template + Translated Text → PDF
    """
    
    def __init__(
        self,
        config: Optional[PipelineConfig] = None,
        translator_func=None
    ):
        """
        Initialize the pipeline.
        
        Args:
            config: Pipeline configuration
            translator_func: Translation function(text, src, tgt) -> translated
        """
        self.config = config or PipelineConfig()
        self.translator_func = translator_func
        
        # Initialize components (lazy loading)
        self._layout_detector = None
        self._cross_page_analyzer = None
        self._geometry_extractor = None
        self._element_extractor = None
        self._content_extractor = None
        self._font_analyzer = None
        self._template_generator = None
        self._latex_assembler = None
        self._pdf_compiler = None
        self._content_translator = None
    
    @property
    def layout_detector(self) -> LayoutDetector:
        if self._layout_detector is None:
            self._layout_detector = LayoutDetector()
        return self._layout_detector
    
    @property
    def cross_page_analyzer(self) -> CrossPageAnalyzer:
        if self._cross_page_analyzer is None:
            self._cross_page_analyzer = CrossPageAnalyzer()
        return self._cross_page_analyzer
    
    @property
    def geometry_extractor(self) -> GeometryExtractor:
        if self._geometry_extractor is None:
            self._geometry_extractor = GeometryExtractor()
        return self._geometry_extractor
    
    @property
    def font_analyzer(self) -> FontAnalyzer:
        if self._font_analyzer is None:
            self._font_analyzer = FontAnalyzer()
        return self._font_analyzer
    
    @property
    def template_generator(self) -> TemplateGenerator:
        if self._template_generator is None:
            self._template_generator = TemplateGenerator()
        return self._template_generator
    
    @property
    def latex_assembler(self) -> LaTeXAssembler:
        if self._latex_assembler is None:
            self._latex_assembler = LaTeXAssembler()
        return self._latex_assembler
    
    @property
    def pdf_compiler(self) -> PDFCompiler:
        if self._pdf_compiler is None:
            # Use xelatex for Russian and CJK (full Unicode support)
            engine = self.config.latex_engine
            if self.config.target_language in ["ru", "zh", "ja", "ko"]:
                engine = "xelatex"
            
            self._pdf_compiler = PDFCompiler(
                engine=engine,
                timeout_seconds=self.config.compilation_timeout
            )
        return self._pdf_compiler
    
    @property
    def content_translator(self) -> ContentTranslator:
        if self._content_translator is None:
            self._content_translator = ContentTranslator(
                translator_func=self.translator_func,
                source_lang=self.config.source_language,
                target_lang=self.config.target_language,
                translate_labels=self.config.translate_labels,
                preserve_abbreviations=self.config.preserve_abbreviations
            )
        return self._content_translator
    
    def process(
        self,
        input_pdf: str,
        output_pdf: Optional[str] = None,
        work_dir: Optional[str] = None
    ) -> PipelineResult:
        """
        Process a PDF document.
        
        Args:
            input_pdf: Path to input PDF
            output_pdf: Path for output PDF (default: input_ru.pdf)
            work_dir: Working directory for intermediate files
        
        Returns:
            PipelineResult with success status and paths
        """
        input_path = Path(input_pdf)
        
        if not input_path.exists():
            return PipelineResult(
                success=False,
                error_message=f"Input file not found: {input_path}"
            )
        
        # Determine output path
        if output_pdf:
            output_path = Path(output_pdf)
        else:
            suffix = f"_{self.config.target_language}.pdf"
            output_path = input_path.with_suffix("").with_suffix(suffix)
        
        # Setup work directory
        if work_dir:
            work_path = Path(work_dir)
        else:
            work_path = output_path.parent / f".{output_path.stem}_work"
        
        work_path.mkdir(parents=True, exist_ok=True)
        images_path = work_path / self.config.images_folder
        images_path.mkdir(exist_ok=True)
        
        result = PipelineResult(success=False)
        
        try:
            # Open PDF
            logger.info(f"Processing: {input_path}")
            doc = fitz.open(str(input_path))
            
            # Step 1: Layout Detection
            logger.info("Step 1/8: Layout detection (DocLayout-YOLO)")
            detection_result = self.layout_detector.detect_document(
                doc,
                confidence_threshold=self.config.detection_confidence,
                dpi=self.config.detection_dpi
            )
            result.detection_result = detection_result
            result.stats["total_elements"] = detection_result.total_elements
            result.stats["elements_by_type"] = detection_result.elements_by_type
            
            # Step 2: Cross-Page Analysis
            logger.info("Step 2/8: Cross-page analysis")
            cross_page_result = self.cross_page_analyzer.analyze(
                detection_result, doc
            )
            result.stats["cross_page_links"] = len(cross_page_result.links)
            result.stats["semantic_units"] = len(cross_page_result.semantic_units)
            
            # Step 3: Geometry Extraction
            logger.info("Step 3/8: Geometry extraction")
            geometry = self.geometry_extractor.extract(doc, detection_result)
            result.geometry = geometry
            result.stats["paper_format"] = geometry.paper_format
            result.stats["num_columns"] = geometry.default_num_columns
            
            # Step 4: Element Extraction
            logger.info("Step 4/8: Element extraction")
            element_extractor = ElementExtractor(output_dir=images_path)
            elements = element_extractor.extract(doc, geometry, detection_result)
            result.stats["images"] = len(elements.images)
            result.stats["lines"] = len(elements.lines)
            
            # Step 5: Content Extraction
            logger.info("Step 5/8: Content extraction")
            content_extractor = ContentExtractor()
            content = content_extractor.extract(doc, geometry, detection_result)
            
            # Link extracted images to figures/tables
            self._link_images_to_content(content, elements, detection_result)
            
            result.content = content
            result.stats["sections"] = len(content.sections)
            result.stats["figures"] = len(content.figures)
            result.stats["tables"] = len(content.tables)
            result.stats["references"] = len(content.references)
            
            # Step 6: Font Analysis
            logger.info("Step 6/8: Font analysis")
            fonts = self.font_analyzer.analyze(doc)
            font_mappings = self.font_analyzer.create_font_mapping(
                fonts, self.config.target_language
            )
            result.stats["fonts_found"] = len(fonts)
            
            # Step 7: Translation
            logger.info("Step 7/8: Translation")
            if self.translator_func:
                translated_content = self.content_translator.translate(
                    content, cross_page_result
                )
            else:
                logger.warning("No translator function - using original content")
                translated_content = content
            result.translated_content = translated_content
            
            # Step 8a: Generate LaTeX Template
            logger.info("Step 8a/8: Generating LaTeX template")
            template = self.template_generator.generate(
                geometry=geometry,
                elements=elements,
                font_mappings=font_mappings,
                target_language=self.config.target_language,
                images_folder=self.config.images_folder
            )
            
            # Step 8b: Assemble LaTeX Document
            logger.info("Step 8b/8: Assembling LaTeX document")
            header_footer_setup = self.template_generator.generate_header_footer_setup(
                elements.headers_footers
            )
            
            latex_content = self.latex_assembler.assemble(
                template=template,
                content=translated_content,
                geometry=geometry,
                elements=elements,
                header_footer_setup=header_footer_setup
            )
            result.latex_content = latex_content
            
            # Save debug files if requested
            if self.config.save_debug_files:
                self._save_debug_files(work_path, result)
            
            # Step 8c: Compile PDF
            logger.info("Step 8c/8: Compiling PDF")
            compile_result = self.pdf_compiler.compile(
                latex_content=latex_content,
                output_path=output_path,
                images_folder=images_path,
                num_passes=self.config.compilation_passes
            )
            
            if compile_result.success:
                result.success = True
                result.output_path = output_path
                logger.info(f"Success! Output: {output_path}")
            else:
                result.error_message = compile_result.error_message
                logger.error(f"Compilation failed: {compile_result.error_message}")
                
                # Save LaTeX for debugging
                debug_tex = work_path / "document.tex"
                debug_tex.write_text(latex_content, encoding="utf-8")
                logger.info(f"LaTeX saved for debugging: {debug_tex}")
            
            doc.close()
            
            # Cleanup work directory if successful and not debug mode
            if result.success and not self.config.save_debug_files:
                shutil.rmtree(work_path, ignore_errors=True)
            
        except Exception as e:
            logger.exception(f"Pipeline error: {e}")
            result.error_message = str(e)
        
        return result
    
    def _save_debug_files(self, work_path: Path, result: PipelineResult):
        """Save intermediate files for debugging."""
        # Save detection result
        if result.detection_result:
            detection_file = work_path / "detection.json"
            detection_data = {
                "total_elements": result.detection_result.total_elements,
                "elements_by_type": result.detection_result.elements_by_type,
                "pages": [
                    {
                        "page_number": p.page_number,
                        "elements": [
                            {
                                "class_name": e.class_name,
                                "confidence": e.confidence,
                                "bbox": e.bbox.to_tuple()
                            }
                            for e in p.elements
                        ]
                    }
                    for p in result.detection_result.pages
                ]
            }
            detection_file.write_text(
                json.dumps(detection_data, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        
        # Save geometry
        if result.geometry:
            geometry_file = work_path / "geometry.json"
            geometry_data = {
                "paper_format": result.geometry.paper_format,
                "paper_width_pt": result.geometry.paper_width_pt,
                "paper_height_pt": result.geometry.paper_height_pt,
                "default_num_columns": result.geometry.default_num_columns,
                "default_margins": result.geometry.default_margins,
            }
            geometry_file.write_text(
                json.dumps(geometry_data, indent=2),
                encoding="utf-8"
            )
        
        # Save content structure
        if result.content:
            content_file = work_path / "content.json"
            content_data = {
                "title": result.content.title,
                "abstract": result.content.abstract[:500] if result.content.abstract else None,
                "keywords": result.content.keywords,
                "sections": [
                    {"level": s.level, "title": s.title}
                    for s in result.content.sections
                ],
                "figures": [
                    {"number": f.number, "caption": f.caption[:100]}
                    for f in result.content.figures
                ],
                "tables": [
                    {"number": t.number, "caption": t.caption[:100]}
                    for t in result.content.tables
                ],
                "references_count": len(result.content.references),
            }
            content_file.write_text(
                json.dumps(content_data, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        
        # Save LaTeX
        if result.latex_content:
            latex_file = work_path / "document.tex"
            latex_file.write_text(result.latex_content, encoding="utf-8")
        
        logger.info(f"Debug files saved to: {work_path}")
    
    def _link_images_to_content(
        self,
        content: DocumentContent,
        elements: ExtractedElements,
        detection_result: DocumentDetectionResult
    ):
        """Link extracted images to figures and tables in content, supporting multi-image figures."""
        # Deduplicate images by page, IoU, and containment
        unique_images = []
        # Sort by area descending so we keep larger containers first
        sorted_images = sorted(elements.images, key=lambda x: x.bbox.width * x.bbox.height, reverse=True)
        
        for img in sorted_images:
            if not img.extracted_path:
                continue
            
            is_duplicate = False
            for existing in unique_images:
                if img.page_number == existing.page_number:
                    # 1. Area-based Containment/Overlap check
                    # Calculate intersection
                    i_x0 = max(img.bbox.x0, existing.bbox.x0)
                    i_y0 = max(img.bbox.y0, existing.bbox.y0)
                    i_x1 = min(img.bbox.x1, existing.bbox.x1)
                    i_y1 = min(img.bbox.y1, existing.bbox.y1)
                    
                    if i_x1 > i_x0 and i_y1 > i_y0:
                        inter_area = (i_x1 - i_x0) * (i_y1 - i_y0)
                        img_area = img.bbox.width * img.bbox.height
                        existing_area = existing.bbox.width * existing.bbox.height
                        
                        # Calculate IoU
                        union_area = img_area + existing_area - inter_area
                        iou = inter_area / union_area if union_area > 0 else 0
                        
                        # Calculate coverage (how much of img is covered by existing)
                        coverage = inter_area / img_area if img_area > 0 else 0
                        # Inverse coverage (how much of existing is covered by img)
                        inv_coverage = inter_area / existing_area if existing_area > 0 else 0
                        
                        # If IoU > 0.4 or coverage > 0.6 or inv_coverage > 0.6, it's a duplicate or sub-component
                        if iou > 0.4 or coverage > 0.6 or inv_coverage > 0.6:
                            is_duplicate = True
                            break
            
            if not is_duplicate:
                unique_images.append(img)
        
        # Get figure, table, and formula images by type from unique set
        figure_images = [
            img for img in unique_images 
            if img.element_type in ["figure", "image", "isolate_formula"]
        ]
        table_images = [
            img for img in unique_images 
            if img.element_type == "table"
        ]
        formula_images = [
            img for img in unique_images 
            if img.element_type == "isolate_formula"
        ]
        
        # Sort figure images by page and Y
        figure_images.sort(key=lambda img: (img.page_number, img.bbox.y0))
        
        used_images = set()
        
        # Link figures by page number and proximity (allowing multiple images per figure)
        for i, fig in enumerate(content.figures):
            # Find all images on this page that are associated with this caption
            # Caption is usually below the figure(s)
            associated_images = []
            
            # Heuristic: Find images above this caption but below the previous caption/start of page
            prev_y = 0
            if i > 0 and content.figures[i-1].page_number == fig.page_number:
                prev_y = content.figures[i-1].bbox.y1 if content.figures[i-1].bbox else 0
            
            for img in figure_images:
                if img.extracted_path in used_images:
                    continue
                if img.page_number == fig.page_number:
                    # If image is between prev_y and this caption (with a bit of tolerance)
                    if prev_y - 10 < img.bbox.y1 < fig.bbox.y0 + 10:
                        associated_images.append(img)
            
            if associated_images:
                # Deduplicate by extracted path to prevent doubling
                seen_paths = set()
                unique_associated = []
                for img in associated_images:
                    if img.extracted_path not in seen_paths:
                        unique_associated.append(img)
                        seen_paths.add(img.extracted_path)
                
                # Add all unique associated images to the figure, sorted by X coordinate
                unique_associated.sort(key=lambda img: img.bbox.x0)
                for img in unique_associated:
                    fig.image_paths.append(Path(img.extracted_path).name)
                    fig.image_bboxes.append(img.bbox)
                    used_images.add(img.extracted_path)
            else:
                # Fallback: find the single closest image above the caption
                best_match = None
                best_distance = float('inf')
                for img in figure_images:
                    if img.extracted_path in used_images:
                        continue
                    if img.page_number == fig.page_number and img.bbox.y1 < fig.bbox.y0 + 20:
                        distance = abs(fig.bbox.y0 - img.bbox.y1)
                        if distance < best_distance:
                            best_distance = distance
                            best_match = img
                
                if best_match:
                    fig.image_paths.append(Path(best_match.extracted_path).name)
                    fig.image_bboxes.append(best_match.bbox)
                    used_images.add(best_match.extracted_path)
        
        # Link tables similarly
        used_table_images = set()
        for table in content.tables:
            best_match = None
            best_distance = float('inf')
            
            for img in table_images:
                if img.extracted_path in used_table_images:
                    continue
                if img.page_number == table.page_number:
                    # Tables - caption can be above or below
                    distance = 0
                    if hasattr(table, 'bbox') and table.bbox:
                        distance = abs(img.bbox.y0 - table.bbox.y0)
                    if distance < best_distance:
                        best_distance = distance
                        best_match = img
            
            if best_match:
                table.image_path = Path(best_match.extracted_path).name
                used_table_images.add(best_match.extracted_path)
        
        # Store formula images for later use
        content.formula_images = [
            Path(img.extracted_path).name for img in formula_images
        ]

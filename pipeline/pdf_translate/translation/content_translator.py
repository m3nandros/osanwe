"""
Content Translator.

Translates document content while preserving structure and special elements.
"""

import logging
import re
from copy import deepcopy
from typing import List, Optional, Set

from ..models import (
    DocumentContent,
    Section,
    Figure,
    Table,
    Footnote,
    BlockType,
    TRANSLATION_RULES,
    CrossPageAnalysisResult,
    SemanticUnit,
)

logger = logging.getLogger(__name__)


# Patterns to preserve during translation
PRESERVE_PATTERNS = [
    r'\[[\d,\s\-–]+\]',           # Citations [1], [1-3], [1, 2, 3]
    r'\(Eq\.?\s*\d+\)',           # Equation references (Eq. 1)
    r'\(Figure\s*\d+[a-z]?\)',    # Figure references (Figure 1a)
    r'\(Fig\.?\s*\d+[a-z]?\)',    # Fig. references (Fig. 1)
    r'\(Table\s*\d+\)',           # Table references (Table 1)
    r'\$[^$]+\$',                 # Inline math $...$
    r'https?://[^\s]+',           # URLs
    r'\b[A-Z]{2,}\b',             # Acronyms (DNA, RNA, etc.) - handled separately
]

# Scientific abbreviations that should NOT be translated
SCIENTIFIC_ABBREVIATIONS = {
    # Biology
    'DNA', 'RNA', 'mRNA', 'tRNA', 'rRNA', 'siRNA', 'miRNA',
    'ATP', 'ADP', 'GTP', 'GDP', 'NAD', 'NADH', 'FAD', 'FADH',
    'PCR', 'qPCR', 'RT-PCR', 'ELISA', 'FACS', 'CRISPR',
    # Chemistry
    'pH', 'pKa', 'NMR', 'MS', 'HPLC', 'LC-MS', 'GC-MS',
    'IR', 'UV', 'Vis', 'CD', 'ESR', 'EPR',
    # Physics
    'eV', 'keV', 'MeV', 'GeV', 'TeV',
    # Computing
    'GPU', 'CPU', 'RAM', 'ROM', 'SSD', 'HDD',
    'API', 'SDK', 'GUI', 'CLI', 'HTTP', 'HTTPS',
    # General
    'vs', 'et al', 'etc', 'i.e.', 'e.g.',
}


class ContentTranslator:
    """
    Translates document content while preserving structure.
    
    Uses the existing translator module from the arxiv pipeline.
    """
    
    def __init__(
        self,
        translator_func=None,
        source_lang: str = "en",
        target_lang: str = "ru",
        translate_labels: bool = True,
        preserve_abbreviations: bool = True
    ):
        """
        Initialize translator.
        
        Args:
            translator_func: Function(text, source_lang, target_lang) -> translated_text
            source_lang: Source language code
            target_lang: Target language code
            translate_labels: Translate labels like "Abstract" → "Аннотация"
            preserve_abbreviations: Preserve scientific abbreviations
        """
        self.translator_func = translator_func
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.translate_labels = translate_labels
        self.preserve_abbreviations = preserve_abbreviations
        
        # Build regex pattern for preservation
        self.preserve_pattern = re.compile(
            '|'.join(PRESERVE_PATTERNS),
            re.IGNORECASE
        )
    
    def translate(
        self,
        content: DocumentContent,
        cross_page_result: Optional[CrossPageAnalysisResult] = None
    ) -> DocumentContent:
        """
        Translate document content.
        
        Args:
            content: Original document content
            cross_page_result: Cross-page analysis for context
        
        Returns:
            Translated DocumentContent
        """
        translated = deepcopy(content)
        
        # Translate title
        if content.title:
            translated.title = self._translate_text(content.title)
            logger.debug(f"Translated title: {translated.title[:50]}...")
        
        # Translate abstract
        if content.abstract:
            translated.abstract = self._translate_text(content.abstract)
        
        # Translate keywords
        translated.keywords = [
            self._translate_text(kw) for kw in content.keywords
        ]
        
        # Translate sections
        translated.sections = self._translate_sections(content.sections)
        
        # Translate figure captions
        translated.figures = [
            Figure(
                number=fig.number,
                caption=self._translate_text(fig.caption),
                image_path=fig.image_path,
                page_number=fig.page_number,
                bbox=fig.bbox
            )
            for fig in content.figures
        ]
        
        # Translate table captions and content
        translated.tables = [
            self._translate_table(table) for table in content.tables
        ]
        
        # Translate footnotes
        translated.footnotes = [
            Footnote(
                marker=fn.marker,
                text=self._translate_text(fn.text),
                page_number=fn.page_number
            )
            for fn in content.footnotes
        ]
        
        # Translate acknowledgements
        if content.acknowledgements:
            translated.acknowledgements = self._translate_text(
                content.acknowledgements
            )
        
        # DO NOT translate:
        # - references (kept as-is)
        # - author names
        # - affiliations
        # - DOI, dates, copyright, license
        
        logger.info(
            f"Translation complete: {len(translated.sections)} sections, "
            f"{len(translated.figures)} figures, {len(translated.tables)} tables"
        )
        
        return translated
    
    def _translate_text(self, text: str) -> str:
        """Translate a piece of text with preservation of special elements."""
        if not text or not text.strip():
            return text
        
        if not self.translator_func:
            logger.warning("No translator function provided, returning original text")
            return text
        
        # Find and replace patterns to preserve
        placeholders = {}
        processed_text = text
        
        # Preserve matched patterns
        for i, match in enumerate(self.preserve_pattern.finditer(text)):
            placeholder = f"__PRESERVE_{i}__"
            placeholders[placeholder] = match.group()
            processed_text = processed_text.replace(match.group(), placeholder, 1)
        
        # Preserve scientific abbreviations
        if self.preserve_abbreviations:
            for abbrev in SCIENTIFIC_ABBREVIATIONS:
                if abbrev in processed_text:
                    placeholder = f"__ABBREV_{abbrev}__"
                    placeholders[placeholder] = abbrev
                    # Use word boundary matching
                    processed_text = re.sub(
                        rf'\b{re.escape(abbrev)}\b',
                        placeholder,
                        processed_text
                    )
        
        # Translate
        try:
            translated = self.translator_func(
                processed_text,
                self.source_lang,
                self.target_lang
            )
        except Exception as e:
            logger.error(f"Translation failed: {e}")
            return text
        
        # Restore preserved elements
        for placeholder, original in placeholders.items():
            translated = translated.replace(placeholder, original)
        
        return translated
    
    def _translate_sections(self, sections: List[Section]) -> List[Section]:
        """Translate sections recursively."""
        translated_sections = []
        
        for section in sections:
            # Translate title
            translated_title = self._translate_text(section.title)
            
            # Translate content
            translated_content = []
            for item in section.content:
                if isinstance(item, str):
                    translated_content.append(self._translate_text(item))
                elif isinstance(item, Section):
                    # Recursive translation
                    sub_sections = self._translate_sections([item])
                    translated_content.extend(sub_sections)
            
            translated_sections.append(Section(
                level=section.level,
                title=translated_title,
                content=translated_content
            ))
        
        return translated_sections
    
    def _translate_table(self, table: Table) -> Table:
        """Translate table caption and cell content."""
        translated_caption = self._translate_text(table.caption)
        
        # Translate headers
        translated_headers = [
            self._translate_text(h) for h in table.headers
        ]
        
        # Translate rows
        translated_rows = [
            [self._translate_text(cell) for cell in row]
            for row in table.rows
        ]
        
        return Table(
            number=table.number,
            caption=translated_caption,
            headers=translated_headers,
            rows=translated_rows,
            page_number=table.page_number
        )
    
    def translate_with_context(
        self,
        content: DocumentContent,
        cross_page_result: CrossPageAnalysisResult
    ) -> DocumentContent:
        """
        Translate with cross-page context awareness.
        
        For paragraphs that span multiple pages, translate as a unit
        then split back for positioning.
        """
        translated = deepcopy(content)
        
        # Handle semantic units that span pages
        for unit in cross_page_result.semantic_units:
            if len(unit.elements) > 1:
                # Multi-page unit - translate as whole
                full_translated = self._translate_text(unit.full_text)
                
                # Store for later use when assembling
                # (Implementation depends on how content is structured)
                logger.debug(
                    f"Translated multi-page unit: {len(unit.elements)} parts"
                )
        
        # Fall back to regular translation for other content
        return self.translate(content, cross_page_result)


def create_translator_from_pipeline(translator_module):
    """
    Create a translator function from the existing pipeline translator.
    
    Args:
        translator_module: The translator module from pipeline.translator
    
    Returns:
        Function(text, source_lang, target_lang) -> translated_text
    """
    def translate_func(text: str, source_lang: str, target_lang: str) -> str:
        # Adapt to existing translator API
        return translator_module.translate_text(
            text,
            source_language=source_lang,
            target_language=target_lang
        )
    
    return translate_func

"""
Font Analyzer.

Analyzes document fonts and creates mappings for target language.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Set

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


@dataclass
class FontInfo:
    """Information about a font used in the document."""
    name: str
    family: str = ""
    style: str = "regular"  # "regular", "bold", "italic", "bolditalic"
    is_serif: bool = True
    is_monospace: bool = False
    usage_count: int = 0
    sizes_used: Set[float] = field(default_factory=set)
    used_for: List[str] = field(default_factory=list)


@dataclass
class FontMapping:
    """Mapping from original font to target language font."""
    original_font: str
    target_font: str
    scaling_factor: float = 1.0


# Font recommendations for different languages
FONT_RECOMMENDATIONS = {
    "ru": {
        "serif": "PT Serif",
        "sans": "PT Sans",
        "mono": "PT Mono",
    },
    "zh": {
        "serif": "Noto Serif CJK SC",
        "sans": "Noto Sans CJK SC",
        "mono": "Noto Sans Mono CJK SC",
    },
    "ja": {
        "serif": "Noto Serif CJK JP",
        "sans": "Noto Sans CJK JP",
        "mono": "Noto Sans Mono CJK JP",
    },
    "ko": {
        "serif": "Noto Serif CJK KR",
        "sans": "Noto Sans CJK KR",
        "mono": "Noto Sans Mono CJK KR",
    },
    "de": {
        "serif": "Libertinus Serif",
        "sans": "Libertinus Sans",
        "mono": "Inconsolata",
    },
    "fr": {
        "serif": "Libertinus Serif",
        "sans": "Libertinus Sans",
        "mono": "Inconsolata",
    },
    "es": {
        "serif": "Libertinus Serif",
        "sans": "Libertinus Sans",
        "mono": "Inconsolata",
    },
}

# Fallback fonts
DEFAULT_FONTS = {
    "serif": "Noto Serif",
    "sans": "Noto Sans",
    "mono": "Noto Sans Mono",
}

# Known font families and their types
SERIF_PATTERNS = [
    "times", "serif", "roman", "garamond", "palatino", "georgia",
    "cambria", "book", "minion", "caslon", "baskerville", "century",
    "charter", "computer modern", "cm", "libertinus"
]

SANS_PATTERNS = [
    "arial", "helvetica", "sans", "gothic", "verdana", "tahoma",
    "calibri", "open sans", "roboto", "lato", "source sans"
]

MONO_PATTERNS = [
    "mono", "courier", "consolas", "menlo", "inconsolata",
    "source code", "fira code", "jetbrains"
]

# Math fonts (should not be replaced)
MATH_FONT_PATTERNS = [
    "cmsy", "cmmi", "cmex", "cmr", "symbol", "math", "stix"
]


class FontAnalyzer:
    """
    Analyzes fonts in a PDF document and creates mappings for translation.
    """
    
    def analyze(self, doc: fitz.Document) -> List[FontInfo]:
        """
        Analyze all fonts used in the document.
        
        Args:
            doc: PyMuPDF Document
        
        Returns:
            List of FontInfo objects sorted by usage
        """
        font_usage: Dict[str, FontInfo] = {}
        
        for page in doc:
            page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
            
            for block in page_dict.get("blocks", []):
                if block.get("type") != 0:  # Text block
                    continue
                
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        font_name = span.get("font", "")
                        font_size = span.get("size", 12.0)
                        text_len = len(span.get("text", ""))
                        
                        if not font_name:
                            continue
                        
                        if font_name not in font_usage:
                            font_usage[font_name] = FontInfo(
                                name=font_name,
                                family=self._extract_family(font_name),
                                style=self._extract_style(font_name),
                                is_serif=self._is_serif(font_name),
                                is_monospace=self._is_monospace(font_name),
                            )
                        
                        font_usage[font_name].usage_count += text_len
                        font_usage[font_name].sizes_used.add(font_size)
        
        # Sort by usage
        fonts = sorted(
            font_usage.values(),
            key=lambda f: f.usage_count,
            reverse=True
        )
        
        logger.info(
            f"Font analysis complete: found {len(fonts)} fonts, "
            f"top fonts: {[f.name for f in fonts[:3]]}"
        )
        
        return fonts
    
    def create_font_mapping(
        self,
        fonts: List[FontInfo],
        target_language: str
    ) -> List[FontMapping]:
        """
        Create font mappings for target language.
        
        Args:
            fonts: List of fonts from analyze()
            target_language: Target language code (e.g., "ru", "zh")
        
        Returns:
            List of FontMapping objects
        """
        recommendations = FONT_RECOMMENDATIONS.get(
            target_language, DEFAULT_FONTS
        )
        
        mappings: List[FontMapping] = []
        
        for font in fonts:
            # Skip math fonts
            if self._is_math_font(font.name):
                mappings.append(FontMapping(
                    original_font=font.name,
                    target_font=font.name,  # Keep original
                    scaling_factor=1.0
                ))
                continue
            
            # Select target font based on type
            if font.is_monospace:
                target = recommendations.get("mono", DEFAULT_FONTS["mono"])
            elif font.is_serif:
                target = recommendations.get("serif", DEFAULT_FONTS["serif"])
            else:
                target = recommendations.get("sans", DEFAULT_FONTS["sans"])
            
            # Determine scaling factor
            # CJK characters are typically wider than Latin
            scaling = 1.0
            if target_language in ["zh", "ja", "ko"]:
                scaling = 0.95  # Slight reduction for CJK
            elif target_language == "ru":
                scaling = 1.05  # Russian often needs slightly more space
            
            mappings.append(FontMapping(
                original_font=font.name,
                target_font=target,
                scaling_factor=scaling
            ))
        
        return mappings
    
    def get_main_font(self, fonts: List[FontInfo]) -> str:
        """Get the main body font (most used)."""
        if not fonts:
            return "Noto Serif"
        return fonts[0].name
    
    def _extract_family(self, font_name: str) -> str:
        """Extract font family from full font name."""
        # Remove subset prefix (e.g., "ABCDEF+FontName")
        if "+" in font_name:
            font_name = font_name.split("+", 1)[1]
        
        # Remove style suffixes
        for suffix in ["-Bold", "-Italic", "-BoldItalic", "-Regular",
                       "Bold", "Italic", "Regular", "Light", "Medium"]:
            font_name = font_name.replace(suffix, "")
        
        return font_name.strip()
    
    def _extract_style(self, font_name: str) -> str:
        """Extract font style from name."""
        name_lower = font_name.lower()
        
        if "bolditalic" in name_lower or ("bold" in name_lower and "italic" in name_lower):
            return "bolditalic"
        elif "bold" in name_lower:
            return "bold"
        elif "italic" in name_lower or "oblique" in name_lower:
            return "italic"
        else:
            return "regular"
    
    def _is_serif(self, font_name: str) -> bool:
        """Check if font is serif."""
        name_lower = font_name.lower()
        
        # Check sans patterns first (more specific)
        for pattern in SANS_PATTERNS:
            if pattern in name_lower:
                return False
        
        # Check serif patterns
        for pattern in SERIF_PATTERNS:
            if pattern in name_lower:
                return True
        
        # Default to serif (more common in academic papers)
        return True
    
    def _is_monospace(self, font_name: str) -> bool:
        """Check if font is monospace."""
        name_lower = font_name.lower()
        
        for pattern in MONO_PATTERNS:
            if pattern in name_lower:
                return True
        
        return False
    
    def _is_math_font(self, font_name: str) -> bool:
        """Check if font is a math font (should not be replaced)."""
        name_lower = font_name.lower()
        
        for pattern in MATH_FONT_PATTERNS:
            if pattern in name_lower:
                return True
        
        return False

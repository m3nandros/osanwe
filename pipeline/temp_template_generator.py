"""
LaTeX Template Generator.

Generates LaTeX template that reproduces the geometry and visual style of the original PDF.
"""

import logging
from typing import List, Optional

from ..models import (
    DocumentGeometry,
    ExtractedElements,
    ImageElement,
    LineElement,
    BoxElement,
    HeaderFooterElement,
)
from ..extractors.font_analyzer import FontMapping, FONT_RECOMMENDATIONS, DEFAULT_FONTS

logger = logging.getLogger(__name__)


# Language names for polyglossia
POLYGLOSSIA_LANGUAGES = {
    "ru": "russian",
    "zh": "chinese",
    "ja": "japanese",
    "ko": "korean",
    "de": "german",
    "fr": "french",
    "es": "spanish",
    "en": "english",
}


class TemplateGenerator:
    """
    Generates LaTeX template from extracted document structure.
    """
    
    def generate(
        self,
        geometry: DocumentGeometry,
        elements: ExtractedElements,
        font_mappings: List[FontMapping],
        target_language: str,
        images_folder: str = "images"
    ) -> str:
        """
        Generate complete LaTeX template.
        
        Args:
            geometry: Document geometry
            elements: Extracted visual elements
            font_mappings: Font mappings for target language
            target_language: Target language code
            images_folder: Folder name for images
        
        Returns:
            Complete LaTeX document template
        """
        parts = []
        
        # Document class
        parts.append(self._generate_document_class(geometry))
        
        # Preamble
        parts.append(self._generate_preamble(
            geometry, font_mappings, target_language, images_folder
        ))
        
        # Custom commands and styles
        parts.append(self._generate_custom_commands())
        
        # Section spacing and formatting
        parts.append(self._generate_section_styling())
        
        # Begin document
        parts.append("\\begin{document}")
        
        # Placeholders for content
        parts.append("")
        parts.append("% ===== TITLE PAGE =====")
        parts.append("\\thispagestyle{empty}")
        parts.append("{{TITLE_PAGE_CONTENT}}")
        parts.append("")
        parts.append("% ===== MAIN CONTENT =====")
        parts.append("{{MAIN_CONTENT}}")
        parts.append("")
        parts.append("\\end{document}")
        
        template = "\n".join(parts)
        
        logger.info("LaTeX template generated")
        
        return template
    
    def _generate_document_class(self, geometry: DocumentGeometry) -> str:
        """Generate document class declaration."""
        paper_size = geometry.paper_format
        if paper_size == "custom":
            paper_size = "a4paper"
        else:
            paper_size = f"{paper_size}paper"
        
        # Always use onecolumn documentclass - we manage columns manually
        # This allows title page to be single-column while body can be two-column
        return f"\\documentclass[{paper_size},10pt]{{article}}"
    
    def _generate_preamble(
        self,
        geometry: DocumentGeometry,
        font_mappings: List[FontMapping],
        target_language: str,
        images_folder: str
    ) -> str:
        """Generate LaTeX preamble with all required packages."""
        parts = []
        
        # Encoding and input
        parts.append("")
        parts.append("% ===== ENCODING =====")
        parts.append("\\usepackage[utf8]{inputenc}")
        
        # Geometry
        parts.append("")
        parts.append("% ===== PAGE GEOMETRY =====")
        parts.append(self._generate_geometry_package(geometry))
        
        # Fonts and Language support
        parts.append("")
        parts.append("% ===== FONTS AND LANGUAGE =====")
        parts.append(self._generate_font_and_language_setup(target_language))
        
        # Graphics
        parts.append("")
        parts.append("% ===== GRAPHICS =====")
        parts.append("\\usepackage{graphicx}")
        parts.append("\\usepackage{float}  % For [H] placement")
        parts.append(f"\\graphicspath{{{{{images_folder}/}}}}")
        
        # Absolute positioning
        parts.append("")
        parts.append("% ===== ABSOLUTE POSITIONING =====")
        parts.append("\\usepackage[absolute,overlay]{textpos}")
        parts.append("\\textblockorigin{0bp}{0bp}  % Use absolute coordinates from page top-left")
        parts.append("\\setlength{\\TPHorizModule}{1bp}")
        parts.append("\\setlength{\\TPVertModule}{1bp}")
        
        # TikZ for lines and boxes
        parts.append("")
        parts.append("% ===== GRAPHICS PRIMITIVES =====")
        parts.append("\\usepackage{tikz}")
        parts.append("\\usetikzlibrary{calc}")
        
        # Math
        parts.append("")
        parts.append("% ===== MATH =====")
        parts.append("\\usepackage{amsmath}")
        parts.append("\\usepackage{amssymb}")
        
        # Tables
        parts.append("")
        parts.append("% ===== TABLES =====")
        parts.append("\\usepackage{booktabs}")
        parts.append("\\usepackage{array}")
        parts.append("\\usepackage{longtable}")
        
        # Colors
        parts.append("")
        parts.append("% ===== COLORS =====")
        parts.append("\\usepackage{xcolor}")
        
        # Headers/Footers
        parts.append("")
        parts.append("% ===== HEADERS/FOOTERS =====")
        parts.append("\\usepackage{fancyhdr}")
        parts.append("\\pagestyle{fancy}")
        parts.append("\\fancyhf{}")
        parts.append("{{HEADER_FOOTER_SETUP}}")
        
        # Paragraph formatting (no indentation like original)
        parts.append("")
        parts.append("% ===== PARAGRAPH FORMATTING =====")
        parts.append("\\setlength{\\parindent}{0bp}")
        parts.append("\\topskip=0bp  % Remove default vertical skip at top of page")
        
        # Use raggedbottom to prevent vertical stretching of whitespace
        parts.append("\\raggedbottom")
        
        # Hyperlinks
        parts.append("")
        parts.append("% ===== HYPERLINKS =====")
        parts.append("\\usepackage{hyperref}")
        parts.append("\\hypersetup{")
        parts.append("    colorlinks=true,")
        parts.append("    linkcolor=blue,")
        parts.append("    citecolor=blue,")
        parts.append("    urlcolor=blue")
        parts.append("}")
        
        # Footnotes
        parts.append("")
        parts.append("% ===== FOOTNOTES =====")
        parts.append("\\usepackage[bottom]{footmisc}")
        
        # Floats
        parts.append("")
        parts.append("% ===== FLOATS =====")
        parts.append("\\usepackage{float}")
        
        # Line spacing
        parts.append("")
        parts.append("% ===== LINE SPACING =====")
        parts.append("\\usepackage{setspace}")
        
        # Margins adjustment for abstract
        parts.append("")
        parts.append("% ===== MARGIN ADJUSTMENT =====")
        parts.append("\\usepackage{changepage}")
        
        # Multi-column (if needed beyond twocolumn)
        if geometry.default_num_columns > 1:
            parts.append("")
            parts.append("% ===== MULTI-COLUMN =====")
            parts.append("\\usepackage{multicol}")
            # Use bp for column separation
            parts.append(f"\\setlength{{\\columnsep}}{{{geometry.pages[0].column_gap_pt:.4f}bp}}")
        
        return "\n".join(parts)
    
    def _generate_geometry_package(self, geometry: DocumentGeometry) -> str:
        """Generate geometry package configuration using Big Points (bp)."""
        # Use default margins (standardized to 72bp if not detected)
        margins = geometry.default_margins
        if not margins:
            margins = {"top": 72, "bottom": 72, "left": 72, "right": 72}
        
        # Apply calibration offsets observed in reconstruction
        # Original analysis showed: Body Left diff=-0.1090, Body Top diff=-1.1533
        top_margin = margins.get('top', 72) + 1.1533
        bottom_margin = margins.get('bottom', 72)
        left_margin = margins.get('left', 72) + 0.1090
        right_margin = margins.get('right', 72)
        
        paper_height = geometry.paper_height_pt
        
        # Calculate footskip dynamically based on detected page number baseline
        if geometry.page_number_baseline_bp > 0:
            footskip = geometry.page_number_baseline_bp - paper_height + bottom_margin
        else:
            footskip = 30.0
        
        lines = [
            "\\usepackage[",
            f"    paperwidth={geometry.paper_width_pt}bp,",
            f"    paperheight={paper_height}bp,",
            f"    top={top_margin:.4f}bp,",
            f"    bottom={bottom_margin:.4f}bp,",
            f"    left={left_margin:.4f}bp,",
            f"    right={right_margin:.4f}bp,",
            f"    footskip={footskip:.4f}bp,",
            "    headheight=0bp,",
            "    headsep=0bp,",
        ]
        
        lines.append("]{geometry}")
        
        return "\n".join(lines)
    
    def _generate_font_and_language_setup(self, target_language: str) -> str:
        """Generate font and language setup for pdflatex compatibility."""
        lines = []
        
        if target_language == "ru":
            # Russian: use XeLaTeX with fontspec for full Unicode support
            lines.extend([
                "\\usepackage{fontspec}",
                "\\usepackage{polyglossia}",
                "\\setmainlanguage{english}",  # Start with English, switch to Russian where needed
                "\\setotherlanguage{russian}",
                "% Use system fonts",
                "\\setmainfont{Times New Roman}[Ligatures=TeX]",
                "\\setsansfont{Arial}",
                "\\setmonofont{Menlo}",
            ])
        elif target_language in ["zh", "ja", "ko"]:
            # CJK: requires XeLaTeX
            lines.extend([
                "\\usepackage{fontspec}",
                "\\usepackage{xeCJK}",
            ])
            if target_language == "zh":
                lines.append("\\setCJKmainfont{Noto Serif CJK SC}")
            elif target_language == "ja":
                lines.append("\\setCJKmainfont{Noto Serif CJK JP}")
            elif target_language == "ko":
                lines.append("\\setCJKmainfont{Noto Serif CJK KR}")
        else:
            # Latin-based: standard setup
            lines.extend([
                "\\usepackage[T1]{fontenc}",
                f"\\usepackage[{target_language}]{{babel}}",
                "\\usepackage{lmodern}",
            ])
        
        return "\n".join(lines)
    
    def _generate_section_styling(self) -> str:
        """Generate section styling to match PDF tightness and avoid double numbering."""
        return """
% ===== SECTION STYLING =====
\\usepackage{titlesec}

% Use unnumbered sections by default to avoid doubling the numbering 
% already present in the extracted text.
\\titleformat{\\section}{\\normalfont\\Large\\bfseries}{}{0pt}{}
\\titleformat{\\subsection}{\\normalfont\\large\\bfseries}{}{0pt}{}
\\titleformat{\\subsubsection}{\\normalfont\\normalsize\\bfseries}{}{0pt}{}

% Tighten spacing around headings to match scientific paper density
\\titlespacing*{\\section}{0pt}{8bp plus 2bp minus 1bp}{4bp plus 1bp}
\\titlespacing*{\\subsection}{0pt}{6bp plus 2bp minus 1bp}{3bp plus 1bp}
\\titlespacing*{\\subsubsection}{0pt}{5bp plus 1bp minus 1bp}{2bp plus 1bp}

% Precise paragraph skip
\\setlength{\\parskip}{2bp plus 1bp minus 0.5bp}

% Ensure figures don't create massive gaps
\\setlength{\\intextsep}{12bp plus 2bp minus 2bp}
\\setlength{\\textfloatsep}{12bp plus 2bp minus 2bp}
"""

    def _generate_custom_commands(self) -> str:
        """Generate custom LaTeX commands."""
        return """
% ===== CUSTOM COMMANDS =====

% Absolute positioning command
\\newcommand{\\absposition}[4]{%
    \\begin{textblock*}{#3bp}(#1bp,#2bp)
        #4%
    \\end{textblock*}%
}

% Horizontal line at absolute position
\\newcommand{\\hlineabs}[5]{%
    % #1=x, #2=y, #3=length, #4=thickness, #5=color
    \\begin{tikzpicture}[remember picture, overlay]%
        \\draw[line width=#4, color=#5] 
            ([xshift=#1bp, yshift=-#2bp]current page.north west) -- ++(#3bp, 0);%
    \\end{tikzpicture}%
}

% Baseline text positioning using TikZ
\\newcommand{\\baselinepos}[3]{%
    % #1=x, #2=y, #3=text
    \\begin{tikzpicture}[remember picture, overlay]%
        \\node[anchor=base west, inner sep=0pt, outer sep=0pt] at ([xshift=#1bp, yshift=-#2bp]current page.north west) {#3};%
    \\end{tikzpicture}%
}

\\newcommand{\\baselineposcenter}[2]{%
    % #1=y, #2=text
    \\begin{tikzpicture}[remember picture, overlay]%
        \\node[anchor=base, inner sep=0pt, outer sep=0pt] at ([xshift=0.5\\paperwidth, yshift=-#1bp]current page.north west) {#2};%
    \\end{tikzpicture}%
}

% Box at absolute position
\\newcommand{\\boxabs}[6]{%
    % #1=x, #2=y, #3=width, #4=height, #5=stroke_color, #6=fill_color
    \\begin{tikzpicture}[remember picture, overlay]%
        \\draw[draw=#5, fill=#6] 
            ([xshift=#1bp, yshift=-#2bp]current page.north west) 
            rectangle ++(#3bp, -#4bp);%
    \\end{tikzpicture}%
}

% Figure caption formatting
\\usepackage{caption}
\\captionsetup{font=small, labelfont=bf}
"""

    def generate_header_footer_setup(
        self,
        headers_footers: List[HeaderFooterElement]
    ) -> str:
        """Generate fancyhdr configuration."""
        header_left = ""
        header_center = ""
        header_right = ""
        footer_left = ""
        footer_center = ""
        footer_right = ""
        
        # Skip page 0 headers (title page only content like copyright notice)
        # These are rendered separately on the title page
        for elem in headers_footers:
            if elem.page_number == 0:
                continue  # Title page headers are handled in title page generation
            
            content = self._escape_latex(elem.content)
            # Replace page number placeholder AFTER escaping
            content = content.replace(
                str(elem.page_number + 1), "\\thepage"
            )
            
            if elem.position == "header":
                if elem.alignment == "left":
                    header_left = content
                elif elem.alignment == "center":
                    header_center = content
                else:
                    header_right = content
            else:
                if elem.alignment == "left":
                    footer_left = content
                elif elem.alignment == "center":
                    footer_center = content
                else:
                    footer_right = content
        
        # Add page numbering in footer center if not already set
        if not footer_center:
            footer_center = "\\thepage"
        
        lines = [
            f"\\fancyhead[L]{{{header_left}}}",
            f"\\fancyhead[C]{{{header_center}}}",
            f"\\fancyhead[R]{{{header_right}}}",
            f"\\fancyfoot[L]{{{footer_left}}}",
            f"\\fancyfoot[C]{{{footer_center}}}",
            f"\\fancyfoot[R]{{{footer_right}}}",
            "\\renewcommand{\\headrulewidth}{0pt}",
            "\\renewcommand{\\footrulewidth}{0pt}",
        ]
        
        return "\n".join(lines)
    
    def generate_absolute_elements(
        self,
        elements: ExtractedElements,
        page_number: int
    ) -> str:
        """Generate LaTeX for absolutely positioned elements on a page."""
        parts = []
        
        # Images (logos, etc.)
        for img in elements.get_page_images(page_number):
            if img.positioning != "absolute":
                continue
            if not img.extracted_path:
                continue
            
            parts.append(
                f"\\absposition{{{img.bbox.x0}pt}}{{{img.bbox.y0}pt}}"
                f"{{{img.bbox.width}pt}}{{%"
            )
            parts.append(
                f"    \\includegraphics[width={img.bbox.width}pt]"
                f"{{{img.extracted_path}}}"
            )
            parts.append("}")
        
        # Lines
        for line in elements.lines:
            if line.page_number != page_number:
                continue
            
            length = abs(line.x2 - line.x1) if line.line_type == "horizontal" else abs(line.y2 - line.y1)
            parts.append(
                f"\\hlineabs{{{line.x1}pt}}{{{line.y1}pt}}"
                f"{{{length}pt}}{{{line.stroke_width}pt}}{{{line.stroke_color}}}"
            )
        
        # Boxes
        for box in elements.boxes:
            if box.page_number != page_number:
                continue
            
            fill = box.fill_color if box.fill_color else "none"
            parts.append(
                f"\\boxabs{{{box.bbox.x0}pt}}{{{box.bbox.y0}pt}}"
                f"{{{box.bbox.width}pt}}{{{box.bbox.height}pt}}"
                f"{{{box.stroke_color}}}{{{fill}}}"
            )
        
        return "\n".join(parts)
    
    def _escape_latex(self, text: str) -> str:
        """Escape special LaTeX characters."""
        replacements = [
            ('\\', '\\textbackslash{}'),
            ('&', '\\&'),
            ('%', '\\%'),
            ('$', '\\$'),
            ('#', '\\#'),
            ('_', '\\_'),
            ('{', '\\{'),
            ('}', '\\}'),
            ('~', '\\textasciitilde{}'),
            ('^', '\\textasciicircum{}'),
        ]
        
        for old, new in replacements:
            text = text.replace(old, new)
        
        return text

"""
LaTeX Assembler.

Assembles final LaTeX document from template and translated content.
"""

import logging
import re
import math
from typing import List, Optional, Dict
from collections import defaultdict

from ..models import (
    BlockType,
    DocumentContent,
    DocumentGeometry,
    ExtractedElements,
    Section,
    Figure,
    Table,
    Equation,
    Reference,
    TextBlock,
)

logger = logging.getLogger(__name__)


class LaTeXAssembler:
    """
    Assembles final LaTeX document from template and translated content.
    """
    
    def assemble(
        self,
        template: str,
        content: DocumentContent,
        geometry: DocumentGeometry,
        elements: ExtractedElements,
        header_footer_setup: str = ""
    ) -> str:
        """
        Assemble final LaTeX document.
        
        Args:
            template: LaTeX template with placeholders
            content: Translated document content
            geometry: Document geometry
            elements: Extracted visual elements
            header_footer_setup: Header/footer configuration
        
        Returns:
            Complete LaTeX document
        """
        document = template
        
        # Replace header/footer setup
        document = document.replace(
            "{{HEADER_FOOTER_SETUP}}", 
            header_footer_setup
        )
        
        # Generate title page content
        title_page = self._generate_title_page(content, geometry, elements)
        document = document.replace("{{TITLE_PAGE_CONTENT}}", title_page)
        
        # Generate main content
        main_content = self._generate_main_content(content)
        document = document.replace("{{MAIN_CONTENT}}", main_content)
        
        logger.info("LaTeX document assembled")
        
        return document
    
    def _generate_title_page(
        self,
        content: DocumentContent,
        geometry: DocumentGeometry,
        elements: ExtractedElements
    ) -> str:
        """Generate title page content using a single TikZ overlay for maximum precision and efficiency."""
        parts = []
        spacing = geometry.title_page_spacing
        
        # Start a single TikZ overlay for the entire page to avoid TeX capacity issues
        parts.append("\\begin{tikzpicture}[remember picture, overlay]%")
        
        # 1. Horizontal rules
        for rule in spacing.rules:
            # y is negative from top-left in TikZ current page.north west
            parts.append(
                f"    \\draw[line width={rule['thickness']:.6f}bp, black] "
                f"([xshift={rule['x0']:.6f}bp, yshift=-{rule['y']:.6f}bp]current page.north west) -- ++({rule['x1']-rule['x0']:.6f}bp, 0);%"
            )

        # 2. Unified Span Collection and Radical Deduplication
        all_spans = []
        
        # Add header elements as spans
        for elem in spacing.header_elements:
            all_spans.append({
                'x': elem['x'],
                'y': elem['y'],
                'text': elem['text'],
                'font_size': elem['font_size'],
                'color': elem['color'],
                'is_bold': elem.get('is_bold', False),
                'is_italic': elem.get('is_italic', False),
                'is_math': elem.get('is_math', False),
                'is_monospace': False,
                'is_title': False,
                'is_arxiv': "arxiv" in elem['text'].lower()
            })
            
        # Add spans from raw blocks on Page 0
        for block in content.raw_blocks:
            if block.page_number == 0:
                is_title = (block.block_type == BlockType.DOCUMENT_TITLE)
                is_arxiv = "arxiv" in block.text.lower()
                if hasattr(block, 'positioned_spans'):
                    for span in block.positioned_spans:
                        all_spans.append({
                            'x': span.x,
                            'y': span.y,
                            'text': span.text,
                            'font_size': span.font_size,
                            'color': getattr(span, 'color', '000000').replace('#', ''),
                            'is_bold': span.is_bold,
                            'is_italic': span.is_italic,
                            'is_math': span.is_math,
                            'is_monospace': span.is_monospace,
                            'is_title': is_title,
                            'is_arxiv': is_arxiv or "arxiv" in span.text.lower()
                        })

        # Radical Deduplication logic
        rendered_spans = []
        
        def is_duplicate(s1, s2):
            # Coordinate check with small tolerance (2pt) to preserve tiny markers
            dist = math.sqrt((s1['x'] - s2['x'])**2 + (s1['y'] - s2['y'])**2)
            if dist < 2.0: return True
            
            # Text similarity check if Y is very close
            if abs(s1['y'] - s2['y']) < 3.0:
                t1, t2 = s1['text'].strip(), s2['text'].strip()
                if t1 and t2 and (t1 in t2 or t2 in t1):
                    return True
            return False

        for s in all_spans:
            if not s['text'].strip(): continue

            # EXCLUSION: Skip marginal metadata that ruins layout (e.g. arXiv watermark)
            # Thresholds: < 20 for left margin, < 15 for top margin, > 785 for bottom margin
            # We keep elements that are explicitly titles or marked as arXiv
            if s['x'] < 20 or s['y'] < 15 or s['y'] > 785:
                if not s['is_title'] and not s['is_arxiv'] and len(s['text']) > 5:
                    lower_text = s['text'].lower()
                    if any(p in lower_text for p in ["preprint", "journal", "accepted"]):
                        continue
                    if s['x'] < 8 or s['y'] < 8:
                        continue

            duplicate = False
            for r in rendered_spans:
                if is_duplicate(s, r):
                    duplicate = True
                    break

            if not duplicate:
                rendered_spans.append(s)

                # Font size safeguard: FORCE normalsize for non-titles on Page 0
                fs_val = s['font_size']
                if not s['is_title']:
                    if s['is_arxiv']:
                        fs_val = 7.0  # arXiv code is tiny
                    elif fs_val > 10:
                        fs_val = 8.5  # Authors/affiliations usually 8.5-9pt
                    elif fs_val < 7:
                        # Keep tiny markers visible
                        fs_val = max(fs_val, 5.0)
                elif s['is_title'] and fs_val > 20:
                    fs_val = 17.0  # Cap title size

                # Render using TikZ node
                span_color = s['color']
                try:
                    r, g, b = int(span_color[:2], 16), int(span_color[2:4], 16), int(span_color[4:6], 16)
                    if r < 40 and g < 40 and b < 40:
                        span_color = "000000"
                except:
                    span_color = "000000"

                color_cmd = f"\\color[HTML]{{{span_color}}}"
                font_cmd = f"\\fontsize{{{fs_val:.4f}bp}}{{{fs_val*1.2:.4f}bp}}\\selectfont{{}}"
                styled_text = self._escape_latex(s['text'], is_math=s['is_math'])

                # Math span styling
                if s['is_math']:
                    math_inner = styled_text
                    if s['is_bold']: math_inner = f"\\mathbf{{{math_inner}}}"
                    if s['is_monospace']: math_inner = f"\\mathtt{{{math_inner}}}"
                    text_latex = f"{color_cmd}{font_cmd}{{$ {math_inner} $}}"
                else:
                    if s['is_bold']: styled_text = f"\\bfseries {styled_text}"
                    if s['is_italic']: styled_text = f"\\itshape {styled_text}"
                    if s['is_monospace']: styled_text = f"\\texttt{{{styled_text}}}"
                    text_latex = f"{color_cmd}{font_cmd}{{{styled_text}}}"

                parts.append(
                    f"    \\node[anchor=base west, inner sep=0pt, outer sep=0pt] "
                    f"at ([xshift={s['x']:.6f}bp, yshift=-{s['y']:.6f}bp]current page.north west) {{{text_latex}}};%"
                )

        # End the single TikZ overlay
        parts.append("\\end{tikzpicture}%")
        parts.append("\\null\\newpage")
        return "\n".join(parts)

    def _generate_main_content(self, content: DocumentContent) -> str:
        """Generate main content by following the unified section hierarchy."""
        parts = []
        for section in content.sections:
            section_content = self._generate_section(section)
            if section_content.strip():
                parts.append(section_content)

        if content.acknowledgements:
            parts.append("\\section*{Acknowledgements}")
            parts.append(self._escape_latex(content.acknowledgements))
            parts.append("")

        return "\n\n".join(parts)

    def _generate_table_as_image(self, table: Table) -> str:
        """Generate LaTeX for a table rendered as image."""
        if hasattr(table, 'image_path') and table.image_path:
            clean_caption = re.sub(r'(?i)^table\s+\d+[:\.]\s*', '', table.caption)
            parts = [
                "\\begin{figure}[H]",
                "    \\centering",
                f"    \\includegraphics[width=0.95\\textwidth]{{{table.image_path}}}",
                f"    \\caption{{{self._escape_latex(clean_caption)}}}",
                f"    \\label{{tab:{table.number}}}",
                "\\end{figure}"
            ]
            return "\n".join(parts)
        return self._generate_table(table)

    def _generate_section(self, section: Section, level: int = 0) -> str:
        """Generate LaTeX for a section with mixed content."""
        parts = []
        if section.title:
            actual_level = section.level if section.level > 0 else level + 1
            section_cmd = {1: "\\section*", 2: "\\subsection*", 3: "\\subsubsection*"}.get(actual_level, "\\paragraph*")
            raw_title = section.title.replace('\n', ' ').strip()
            match = re.match(r'^(\d+(?:\.\d+)*)\s*(.*)', raw_title)
            if match:
                number, title_text = match.groups()
                clean_title = f"{self._escape_latex(number)}~{self._escape_latex(title_text)}"
            else:
                clean_title = self._escape_latex(raw_title)
            parts.append(f"{section_cmd}{{{clean_title}}}")

        for item in section.content:
            if isinstance(item, TextBlock):
                if getattr(item, 'positioned_spans', []):
                    paragraph_parts = []
                    last_span = None
                    current_math_group = []

                    def flush_math_group(group, current_parts):
                        if not group: return
                        group_text = []
                        for s in group:
                            s_text = self._escape_latex(s.text, is_math=True)
                            if s.is_bold: s_text = f"\\mathbf{{{s_text}}}"
                            if s.is_monospace: s_text = f"\\mathtt{{{s_text}}}"
                            group_text.append(s_text)
                        current_parts.append(f"$ {''.join(group_text)} $")
                        group.clear()

                    for span in item.positioned_spans:
                        is_math = getattr(span, 'is_math', False)
                        if last_span:
                            if abs(span.y - last_span.y) < 2:
                                gap = span.x - (last_span.x + last_span.width)
                                if (gap > span.font_size * 0.12 or gap > 1.0) and not last_span.text.endswith(' ') and not span.text.startswith(' '):
                                    if not (is_math and current_math_group):
                                        flush_math_group(current_math_group, paragraph_parts)
                                        paragraph_parts.append(' ')
                                    elif is_math and current_math_group:
                                        if gap > span.font_size * 0.3:
                                            flush_math_group(current_math_group, paragraph_parts)
                                            paragraph_parts.append(' ')
                            elif span.y > last_span.y + 2:
                                flush_math_group(current_math_group, paragraph_parts)
                                if not last_span.text.endswith(' ') and not last_span.text.endswith('-'):
                                    paragraph_parts.append(' ')

                        if is_math:
                            current_math_group.append(span)
                        else:
                            flush_math_group(current_math_group, paragraph_parts)
                            span_text = self._escape_latex(span.text, is_math=False)
                            if span.is_bold: span_text = f"\\textbf{{{span_text}}}"
                            if span.is_italic: span_text = f"\\textit{{{span_text}}}"
                            if span.is_monospace: span_text = f"\\texttt{{{span_text}}}"
                            paragraph_parts.append(span_text)
                        last_span = span

                    flush_math_group(current_math_group, paragraph_parts)
                    parts.append("".join(paragraph_parts))
                else:
                    is_math = getattr(item, 'is_math', False)
                    text_latex = self._escape_latex(item.text, is_math=is_math)
                    if is_math:
                        if item.style.is_bold: text_latex = f"\\mathbf{{{text_latex}}}"
                        if item.style.is_monospace: text_latex = f"\\mathtt{{{text_latex}}}"
                        parts.append(f"$ {text_latex} $")
                    elif hasattr(item.style, 'is_monospace') and item.style.is_monospace:
                        parts.append(f"\\texttt{{{text_latex}}}")
                    else:
                        parts.append(text_latex)
                parts.append("")
            elif isinstance(item, str):
                parts.append(self._escape_latex(item))
                parts.append("")
            elif isinstance(item, Section):
                parts.append(self._generate_section(item, level + 1))
            elif isinstance(item, Figure):
                fig_latex = self._generate_figure(item)
                if fig_latex: parts.append(fig_latex)
            elif isinstance(item, Table):
                table_latex = self._generate_table_as_image(item)
                if table_latex: parts.append(table_latex)
            elif isinstance(item, Reference):
                escaped_text = self._escape_latex(item.text)
                parts.append(f"\\noindent {item.number} {escaped_text}")
                parts.append("")
            elif isinstance(item, Equation):
                parts.append(self._generate_equation(item))

        return "\n".join(parts)

    def _generate_figure(self, figure: Figure) -> str:
        """Generate LaTeX for a figure, supporting multiple side-by-side images."""
        parts = ["\\begin{figure}[H]", "    \\centering"]
        if figure.image_paths:
            num_images = len(figure.image_paths)
            if num_images > 1:
                total_width_bp = sum(bbox.width for bbox in figure.image_bboxes)
                scale = 1.0
                max_width_bp = 510.0
                if total_width_bp > max_width_bp: scale = max_width_bp / total_width_bp
                parts.append("    \\begin{center}")
                for i, (img_path, bbox) in enumerate(zip(figure.image_paths, figure.image_bboxes)):
                    width_bp = bbox.width * scale
                    width_linewidth = width_bp / max_width_bp
                    parts.append(f"        \\begin{{minipage}}{{{width_linewidth:.3f}\\linewidth}}")
                    parts.append(f"            \\centering")
                    parts.append(f"            \\includegraphics[width=\\linewidth]{{{img_path}}}")
                    parts.append("        \\end{minipage}%")
                    if i < num_images - 1: parts.append("        \\hfill%")
                parts.append("    \\end{center}")
            else:
                img_path = figure.image_paths[0]
                img_width = figure.image_bboxes[0].width if figure.image_bboxes else 0
                if img_width > 0:
                    parts.append(f"    \\includegraphics[width=\\minof{{{img_width:.2f}bp}}{{\\linewidth}}]{{{img_path}}}")
                else:
                    parts.append(f"    \\includegraphics[width=0.9\\linewidth]{{{img_path}}}")
        clean_caption = re.sub(r'(?i)^figure\s+\d+[:\.]\s*', '', figure.caption)
        parts.append(f"    \\caption{{{self._escape_latex(clean_caption)}}}")
        parts.append(f"    \\label{{fig:{figure.number}}}")
        parts.append("\\end{figure}")
        return "\n".join(parts)

    def _generate_table(self, table: Table) -> str:
        """Generate LaTeX for a table."""
        if not table.headers and not table.rows: return ""
        num_cols = len(table.headers) if table.headers else (len(table.rows[0]) if table.rows else 0)
        if num_cols == 0: return ""
        col_spec = "l" * num_cols
        clean_caption = re.sub(r'(?i)^table\s+\d+[:\.]\s*', '', table.caption)
        parts = ["\\begin{table}[htbp]", "    \\centering", f"    \\caption{{{self._escape_latex(clean_caption)}}}", f"    \\label{{tab:{table.number}}}", f"    \\begin{{tabular}}{{{col_spec}}}", "        \\toprule"]
        if table.headers:
            header_row = " & ".join(self._escape_latex(h) for h in table.headers)
            parts.append(f"        {header_row} \\\\")
            parts.append("        \\midrule")
        for row in table.rows:
            row_text = " & ".join(self._escape_latex(cell) for cell in row)
            parts.append(f"        {row_text} \\\\")
        parts.extend(["        \\bottomrule", "    \\end{tabular}", "\\end{table}"])
        return "\n".join(parts)

    def _generate_equation(self, equation: Equation) -> str:
        """Generate LaTeX for an equation."""
        latex_code = self._escape_latex(equation.latex, is_math=True)
        if equation.is_inline: return f"${latex_code}$"
        
        if equation.number:
            # Use \tag{} to preserve original numbering exactly as extracted
            # num = equation.number.strip("()") # Don't strip, keep original format
            num = equation.number
            return f"\\begin{{equation*}}\n    {latex_code} \\tag{{{num}}}\n    \\label{{eq:{num.strip('()')}}}\n\\end{{equation*}}"
        return f"\\begin{{equation*}}\n    {latex_code}\n\\end{{equation*}}"

    def _generate_references(self, references: List[Reference]) -> str:
        """Generate references section (NOT translated)."""
        if not references: return ""
        parts = ["\\section*{References}", "{\\small"]
        for ref in references:
            escaped_text = self._escape_latex(ref.text)
            parts.append(f"\\noindent {ref.number} {escaped_text}")
            parts.append("")
        parts.append("}")
        return "\n".join(parts)

    UNICODE_REPLACEMENTS = {
        '∗': '*', '†': '\\ensuremath{\\dagger}', '‡': '\\ensuremath{\\ddagger}', '−': '-', '\u00a0': ' ',
        '√': '\\ensuremath{\\sqrt{}}', '≤': '\\ensuremath{\\leq}', '≥': '\\ensuremath{\\geq}', '×': '\\ensuremath{\\times}',
        '→': '\\ensuremath{\\rightarrow}', '←': '\\ensuremath{\\leftarrow}', '↔': '\\ensuremath{\\leftrightarrow}',
        '⇒': '\\ensuremath{\\Rightarrow}', '⇐': '\\ensuremath{\\Leftarrow}', 'α': '\\ensuremath{\\alpha}',
        'β': '\\ensuremath{\\beta}', 'γ': '\\ensuremath{\\gamma}', 'δ': '\\ensuremath{\\delta}', 'ε': '\\ensuremath{\\epsilon}',
        'θ': '\\ensuremath{\\theta}', 'λ': '\\ensuremath{\\lambda}', 'μ': '\\ensuremath{\\mu}', 'π': '\\ensuremath{\\pi}',
        'σ': '\\ensuremath{\\sigma}', 'τ': '\\ensuremath{\\tau}', 'φ': '\\ensuremath{\\phi}', 'ω': '\\ensuremath{\\omega}',
        '≈': '\\ensuremath{\\approx}', '∞': '\\ensuremath{\\infty}', '∈': '\\ensuremath{\\in}', '∉': '\\ensuremath{\\notin}',
        '⊂': '\\ensuremath{\\subset}', '⊃': '\\ensuremath{\\supset}', '∩': '\\ensuremath{\\cap}', '∪': '\\ensuremath{\\cup}',
        '∑': '\\ensuremath{\\sum}', '∏': '\\ensuremath{\\prod}', '∫': '\\ensuremath{\\int}', '∂': '\\ensuremath{\\partial}',
        '∇': '\\ensuremath{\\nabla}', '±': '\\ensuremath{\\pm}', '÷': '\\ensuremath{\\div}', '·': '\\ensuremath{\\cdot}',
        '°': '\\ensuremath{^\\circ}', '′': "'", '″': "''", '…': '...', '–': '--', '—': '---',
        '©': '\\textcopyright{}', '®': '\\textregistered{}', '™': '\\texttrademark{}',
    }

    def _escape_latex(self, text: str, is_math: bool = False) -> str:
        """Escape special LaTeX characters, with context-aware math handling."""
        if not text: return ""
        for unicode_char, latex_equiv in self.UNICODE_REPLACEMENTS.items():
            text = text.replace(unicode_char, latex_equiv)
        if is_math:
            replacements = [('&', '\\&'), ('%', '\\%'), ('#', '\\#'), ('$', '\\$')]
        else:
            replacements = [
                ('\\', '\\textbackslash{}'), ('&', '\\&'), ('%', '\\%'), ('$', '\\$'),
                ('#', '\\#'), ('_', '\\_'), ('{', '\\{'), ('}', '\\}'), ('~', '\\textasciitilde{}'), ('^', '\\textasciicircum{}'),
            ]
        for old, new in replacements: text = text.replace(old, new)
        return text

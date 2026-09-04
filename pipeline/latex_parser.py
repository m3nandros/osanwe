"""
LaTeX parser module for Rosetta v2.

Enhanced parser that supports any LaTeX template and identifies:
- Translatable vs non-translatable content
- Mathematical formulas
- Specialized packages (tikz, xy-pic, qtree, etc.)
- Footnotes (\\footnote{}, \\marginpar{}, \\thanks{})
- Tables, images, bibliography
- Document structure (sections, paragraphs, chapters)
"""

import re
from enum import Enum
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field

from utils.logger import get_logger

logger = get_logger(__name__)


class LaTeXElementType(Enum):
    """Types of LaTeX elements in a document."""
    TEXT = "text"           # Translatable text
    COMMAND = "command"     # LaTeX command
    MATH = "math"          # Mathematical formula
    ENVIRONMENT = "env"     # LaTeX environment
    COMMENT = "comment"     # LaTeX comment
    SPECIALIZED_PACKAGE = "specialized_package"  # tikz, xy-pic, etc.


@dataclass
class LaTeXElement:
    """
    Represents a single element in a LaTeX document.
    
    Attributes:
        element_type: Type of the element
        content: Raw content of the element
        start_pos: Starting position in original document (line number)
        end_pos: Ending position in original document (line number)
        metadata: Additional metadata (e.g., command name, environment name)
    """
    element_type: LaTeXElementType
    content: str
    start_pos: int = 0
    end_pos: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LaTeXDocument:
    """
    Represents a parsed LaTeX document with its structure.
    
    Attributes:
        preamble: Document content before \begin{document}
        content: List of elements in the main document body
        bibliography: Bibliography section (if present)
        packages: List of packages used (extracted from preamble)
        specialized_packages: Set of specialized packages (tikz, xy-pic, etc.)
    """
    preamble: str
    content: List[LaTeXElement] = field(default_factory=list)
    bibliography: Optional[str] = None
    packages: List[str] = field(default_factory=list)
    specialized_packages: set = field(default_factory=set)


class LaTeXParser:
    """
    Enhanced LaTeX parser that supports any LaTeX template.
    
    Handles:
    - Document structure parsing (preamble, content, bibliography)
    - Identification of LaTeX commands and environments
    - Detection of specialized packages
    - Structure tracking for validation
    """
    
    # Specialized packages that should not be translated
    SPECIALIZED_PACKAGES = {
        'tikz', 'pgf', 'pgfplots', 'xy', 'xypic', 'qtree', 'pstricks',
        'pst-node', 'pst-tree', 'circuitikz', 'tikz-cd'
    }
    
    # Environments from specialized packages (NOT TO TRANSLATE)
    SPECIALIZED_ENVIRONMENTS = {
        'tikzpicture', 'pgfpicture', 'xymatrix', 'xy', 'pspicture',
        'circuitikz', 'tikzcd'
    }
    
    # Commands from specialized packages (NOT TO TRANSLATE)
    SPECIALIZED_COMMANDS = {
        'tikz', 'pgf', 'draw', 'node', 'coordinate', 'path', 'fill', 'shade',
        'xymatrix', 'xy', 'Tree', 'qtree', 'pspicture', 'circuitikz'
    }
    
    # Mathematical environments
    MATH_ENVIRONMENTS = {
        'equation', 'equation*', 'align', 'align*', 'alignat', 'alignat*',
        'multline', 'multline*', 'gather', 'gather*', 'flalign', 'flalign*',
        'eqnarray', 'eqnarray*', 'split', 'cases', 'matrix', 'pmatrix',
        'bmatrix', 'vmatrix', 'Vmatrix', 'smallmatrix'
    }
    
    # Table environments (translate text, not structure)
    TABLE_ENVIRONMENTS = {
        'table', 'tabular', 'tabularx', 'longtable', 'array'
    }
    
    # Image/figure environments (NOT TO TRANSLATE, except captions)
    IMAGE_ENVIRONMENTS = {
        'figure', 'figure*', 'subfigure', 'subfigure*'
    }
    
    # Algorithm environments (translate text, not code)
    ALGORITHM_ENVIRONMENTS = {
        'algorithm', 'algorithmic', 'algorithmicx', 'algorithm2e'
    }
    
    # Structural commands (sections, chapters, etc.)
    STRUCTURAL_COMMANDS = {
        'part', 'chapter', 'section', 'subsection', 'subsubsection',
        'paragraph', 'subparagraph'
    }
    
    # Citation and reference commands
    CITATION_COMMANDS = {
        'cite', 'citep', 'citet', 'citeauthor', 'citeyear', 'footcite'
    }
    
    # Reference commands
    REFERENCE_COMMANDS = {
        'ref', 'label', 'pageref', 'eqref', 'autoref', 'nameref'
    }
    
    # Footnote commands (TRANSLATE content)
    FOOTNOTE_COMMANDS = {
        'footnote', 'marginpar', 'thanks', 'footnotemark', 'footnotetext'
    }
    
    # Image commands (NOT TO TRANSLATE)
    IMAGE_COMMANDS = {
        'includegraphics', 'includepdf', 'graphicspath'
    }
    
    # URL/DOI commands (NOT TO TRANSLATE)
    URL_COMMANDS = {
        'url', 'href', 'doi', 'path', 'texttt'
    }
    
    def __init__(self):
        """Initialize the parser."""
        self.logger = get_logger(__name__)
    
    def parse(self, tex_content: str) -> LaTeXDocument:
        """
        Parse a LaTeX document and identify its structure.
        
        Args:
            tex_content: Raw LaTeX content as string
            
        Returns:
            LaTeXDocument dataclass with parsed structure
        """
        self.logger.info("Starting LaTeX document parsing...")
        
        # Split document into preamble, content, and bibliography
        preamble, content, bibliography = self._split_document(tex_content)
        
        # Extract packages from preamble
        packages = self._extract_packages(preamble)
        specialized_packages = self._identify_specialized_packages(packages)
        
        # Parse content into elements
        content_elements = self._parse_content(content, specialized_packages)
        
        document = LaTeXDocument(
            preamble=preamble,
            content=content_elements,
            bibliography=bibliography,
            packages=packages,
            specialized_packages=specialized_packages
        )
        
        self.logger.info(
            f"Parsing complete: {len(content_elements)} elements, "
            f"{len(packages)} packages, {len(specialized_packages)} specialized"
        )
        
        return document
    
    def _split_document(self, tex_content: str) -> tuple[str, str, Optional[str]]:
        """
        Split document into preamble, content, and bibliography.
        
        Args:
            tex_content: Full LaTeX document content
            
        Returns:
            Tuple of (preamble, content, bibliography)
        """
        # Find \begin{document}
        doc_start_match = re.search(r'\\begin\{document\}', tex_content)
        if not doc_start_match:
            self.logger.warning("No \\begin{document} found, treating entire document as preamble")
            return tex_content, "", None
        
        doc_start = doc_start_match.end()
        preamble = tex_content[:doc_start_match.start()].rstrip()
        
        # Find \end{document}
        doc_end_match = re.search(r'\\end\{document\}', tex_content[doc_start:])
        if not doc_end_match:
            self.logger.warning("No \\end{document} found, treating rest as content")
            return preamble, tex_content[doc_start:].strip(), None
        
        doc_end = doc_start + doc_end_match.start()
        body_content = tex_content[doc_start:doc_end].strip()
        
        # Find bibliography section
        bib_start_match = re.search(r'\\begin\{thebibliography\}', body_content)
        if bib_start_match:
            bib_start = bib_start_match.start()
            bib_end_match = re.search(r'\\end\{thebibliography\}', body_content[bib_start:])
            if bib_end_match:
                bib_end = bib_start + bib_end_match.end()
                bibliography = body_content[bib_start:bib_end]
                content = body_content[:bib_start] + body_content[bib_end:]
                return preamble, content.strip(), bibliography
        
        return preamble, body_content, None
    
    def _extract_packages(self, preamble: str) -> List[str]:
        """
        Extract list of packages from preamble.
        
        Args:
            preamble: Document preamble
            
        Returns:
            List of package names
        """
        packages = []
        # Match \usepackage[...]{package} or \usepackage{package}
        pattern = r'\\usepackage(?:\[[^\]]*\])?\{([^}]+)\}'
        matches = re.finditer(pattern, preamble)
        
        for match in matches:
            package_list = match.group(1).split(',')
            for pkg in package_list:
                pkg = pkg.strip()
                if pkg:
                    packages.append(pkg)
        
        return packages
    
    def _identify_specialized_packages(self, packages: List[str]) -> set:
        """
        Identify specialized packages that should not be translated.
        
        Args:
            packages: List of package names
            
        Returns:
            Set of specialized package names found
        """
        specialized = set()
        for pkg in packages:
            if pkg in self.SPECIALIZED_PACKAGES:
                specialized.add(pkg)
        return specialized
    
    def _parse_content(self, content: str, specialized_packages: set = None) -> List[LaTeXElement]:
        """
        Parse document content into structured elements.
        
        Part 2: Enhanced parsing with command and formula detection.
        
        Args:
            content: Document body content
            
        Returns:
            List of LaTeXElement objects
        """
        # First, parse at line level for environments and comments
        elements = []
        lines = content.split('\n')
        i = 0
        
        while i < len(lines):
            line = lines[i]
            line_stripped = line.strip()
            
            # Handle empty lines (preserve paragraph structure)
            if not line_stripped:
                i += 1
                continue
            
            # Check for comments
            if line_stripped.startswith('%'):
                elements.append(LaTeXElement(
                    element_type=LaTeXElementType.COMMENT,
                    content=line,
                    start_pos=i + 1,
                    end_pos=i + 1
                ))
                i += 1
                continue
            
            # Check for environments
            env_match = re.search(r'\\begin\{([^}]+)\}', line)
            if env_match:
                env_name = env_match.group(1)
                env_content = [line]
                start_line = i + 1
                i += 1
                
                # Find matching \end with proper nesting
                depth = 1
                while i < len(lines) and depth > 0:
                    current_line = lines[i]
                    env_end_match = re.search(r'\\end\{([^}]+)\}', current_line)
                    env_begin_match = re.search(r'\\begin\{([^}]+)\}', current_line)
                    
                    if env_end_match and env_end_match.group(1) == env_name:
                        depth -= 1
                        if depth == 0:
                            env_content.append(current_line)
                            # Determine element type based on environment
                            if env_name in self.SPECIALIZED_ENVIRONMENTS:
                                element_type = LaTeXElementType.SPECIALIZED_PACKAGE
                            elif env_name in self.MATH_ENVIRONMENTS:
                                element_type = LaTeXElementType.MATH
                            elif env_name in self.TABLE_ENVIRONMENTS:
                                element_type = LaTeXElementType.ENVIRONMENT
                                metadata = {'environment': env_name, 'is_table': True}
                            elif env_name in self.IMAGE_ENVIRONMENTS:
                                element_type = LaTeXElementType.ENVIRONMENT
                                metadata = {'environment': env_name, 'is_image': True}
                            elif env_name in self.ALGORITHM_ENVIRONMENTS:
                                element_type = LaTeXElementType.ENVIRONMENT
                                metadata = {'environment': env_name, 'is_algorithm': True}
                            else:
                                element_type = LaTeXElementType.ENVIRONMENT
                                metadata = {'environment': env_name}
                            
                            elements.append(LaTeXElement(
                                element_type=element_type,
                                content='\n'.join(env_content),
                                start_pos=start_line,
                                end_pos=i + 1,
                                metadata=metadata
                            ))
                            i += 1
                            break
                    elif env_begin_match and env_begin_match.group(1) == env_name:
                        depth += 1
                    
                    env_content.append(current_line)
                    i += 1
                continue
            
            # For non-environment lines, parse in detail for commands and formulas
            parsed_line_elements = self._parse_line_detailed(
                line, i + 1, specialized_packages or set()
            )
            elements.extend(parsed_line_elements)
            i += 1
        
        return elements
    
    def _parse_line_detailed(
        self, line: str, line_num: int, specialized_packages: set = None
    ) -> List[LaTeXElement]:
        """
        Parse a single line in detail, extracting commands, formulas, and text.
        
        Part 2: Detailed parsing of commands and formulas.
        
        Args:
            line: Line content to parse
            line_num: Line number in document
            
        Returns:
            List of LaTeXElement objects found in the line
        """
        elements = []
        pos = 0
        text_buffer = []
        text_start = None
        
        while pos < len(line):
            # Check for inline math $...$
            dollar_match = re.match(r'\$\$?', line[pos:])
            if dollar_match:
                # Save any accumulated text
                if text_buffer:
                    elements.append(LaTeXElement(
                        element_type=LaTeXElementType.TEXT,
                        content=''.join(text_buffer),
                        start_pos=line_num,
                        end_pos=line_num
                    ))
                    text_buffer = []
                
                # Extract math formula
                math_start = pos
                is_display = dollar_match.group() == '$$'
                pos += len(dollar_match.group())
                
                # Find closing dollar(s)
                if is_display:
                    closing = '$$'
                else:
                    closing = '$'
                
                closing_pos = line.find(closing, pos)
                if closing_pos == -1:
                    # Unclosed formula - treat rest as math
                    math_content = line[math_start:]
                    elements.append(LaTeXElement(
                        element_type=LaTeXElementType.MATH,
                        content=math_content,
                        start_pos=line_num,
                        end_pos=line_num,
                        metadata={'math_type': 'inline' if not is_display else 'display'}
                    ))
                    break
                
                math_content = line[math_start:closing_pos + len(closing)]
                elements.append(LaTeXElement(
                    element_type=LaTeXElementType.MATH,
                    content=math_content,
                    start_pos=line_num,
                    end_pos=line_num,
                    metadata={'math_type': 'inline' if not is_display else 'display'}
                ))
                pos = closing_pos + len(closing)
                continue
            
            # Check for display math \[...\] or \(...\)
            display_math_match = re.match(r'\\(?:\[|\(|begin\{equation)', line[pos:])
            if display_math_match:
                # Save any accumulated text
                if text_buffer:
                    elements.append(LaTeXElement(
                        element_type=LaTeXElementType.TEXT,
                        content=''.join(text_buffer),
                        start_pos=line_num,
                        end_pos=line_num
                    ))
                    text_buffer = []
                
                # Handle \[...\] or \(...\)
                if line[pos:pos+2] == '\\[':
                    closing = '\\]'
                    closing_pos = line.find(closing, pos + 2)
                    if closing_pos != -1:
                        math_content = line[pos:closing_pos + 2]
                        elements.append(LaTeXElement(
                            element_type=LaTeXElementType.MATH,
                            content=math_content,
                            start_pos=line_num,
                            end_pos=line_num,
                            metadata={'math_type': 'display'}
                        ))
                        pos = closing_pos + 2
                        continue
                elif line[pos:pos+2] == '\\(':
                    closing = '\\)'
                    closing_pos = line.find(closing, pos + 2)
                    if closing_pos != -1:
                        math_content = line[pos:closing_pos + 2]
                        elements.append(LaTeXElement(
                            element_type=LaTeXElementType.MATH,
                            content=math_content,
                            start_pos=line_num,
                            end_pos=line_num,
                            metadata={'math_type': 'inline'}
                        ))
                        pos = closing_pos + 2
                        continue
                # If we get here, it might be \begin{equation} which is handled at environment level
                # Fall through to command detection
            
            # Check for LaTeX commands \command[...]{...} or \command{...}
            command_match = re.match(r'\\([a-zA-Z@]+)(?:\[([^\]]*)\])?(?:\{([^}]*)\})?', line[pos:])
            if command_match:
                # Save any accumulated text
                if text_buffer:
                    elements.append(LaTeXElement(
                        element_type=LaTeXElementType.TEXT,
                        content=''.join(text_buffer),
                        start_pos=line_num,
                        end_pos=line_num
                    ))
                    text_buffer = []
                
                cmd_name = command_match.group(1)
                opt_arg = command_match.group(2) if command_match.group(2) else None
                req_arg = command_match.group(3) if command_match.group(3) else None
                
                # Extract full command with arguments
                full_cmd = self._extract_full_command(line, pos)
                
                # Determine command type and metadata
                metadata = {'command': cmd_name}
                if opt_arg:
                    metadata['optional_arg'] = opt_arg
                if req_arg:
                    metadata['required_arg'] = req_arg
                
                # Check if it's a structural command
                if cmd_name in self.STRUCTURAL_COMMANDS:
                    metadata['is_structural'] = True
                
                # Check command categories
                if cmd_name in self.CITATION_COMMANDS:
                    metadata['is_citation'] = True
                elif cmd_name in self.REFERENCE_COMMANDS:
                    metadata['is_reference'] = True
                elif cmd_name in self.FOOTNOTE_COMMANDS:
                    metadata['is_footnote'] = True
                    # Footnotes should be translated, mark for translation
                    metadata['should_translate'] = True
                elif cmd_name in self.IMAGE_COMMANDS:
                    metadata['is_image'] = True
                    # Images should NOT be translated
                    metadata['should_translate'] = False
                elif cmd_name in self.URL_COMMANDS:
                    metadata['is_url'] = True
                    # URLs should NOT be translated
                    metadata['should_translate'] = False
                elif self._is_specialized_package_command(
                    cmd_name, specialized_packages or set()
                ):
                    metadata['is_specialized'] = True
                    # Specialized commands should NOT be translated
                    metadata['should_translate'] = False
                
                # Extract footnote content for translation
                if cmd_name in self.FOOTNOTE_COMMANDS:
                    footnote_content = self._extract_footnote_content(full_cmd)
                    if footnote_content:
                        metadata['footnote_content'] = footnote_content
                
                elements.append(LaTeXElement(
                    element_type=LaTeXElementType.COMMAND,
                    content=full_cmd,
                    start_pos=line_num,
                    end_pos=line_num,
                    metadata=metadata
                ))
                
                pos += len(full_cmd)
                continue
            
            # Regular character - add to text buffer
            text_buffer.append(line[pos])
            if text_start is None:
                text_start = pos
            pos += 1
        
        # Add any remaining text
        if text_buffer:
            elements.append(LaTeXElement(
                element_type=LaTeXElementType.TEXT,
                content=''.join(text_buffer),
                start_pos=line_num,
                end_pos=line_num
            ))
        
        return elements
    
    def _extract_full_command(self, line: str, start_pos: int) -> str:
        """
        Extract full LaTeX command including all arguments.
        
        Handles nested braces and optional arguments.
        
        Args:
            line: Line content
            start_pos: Starting position of command
            
        Returns:
            Full command string including all arguments
        """
        if start_pos >= len(line) or line[start_pos] != '\\':
            return ''
        
        pos = start_pos
        result = ['\\']
        pos += 1
        
        # Extract command name
        while pos < len(line) and (line[pos].isalnum() or line[pos] == '@'):
            result.append(line[pos])
            pos += 1
        
        # Extract optional argument [ ... ]
        if pos < len(line) and line[pos] == '[':
            result.append('[')
            pos += 1
            brace_depth = 1
            while pos < len(line) and brace_depth > 0:
                if line[pos] == '[':
                    brace_depth += 1
                elif line[pos] == ']':
                    brace_depth -= 1
                result.append(line[pos])
                pos += 1
        
        # Extract required arguments { ... }
        while pos < len(line) and line[pos] == '{':
            result.append('{')
            pos += 1
            brace_depth = 1
            while pos < len(line) and brace_depth > 0:
                if line[pos] == '{':
                    brace_depth += 1
                elif line[pos] == '}':
                    brace_depth -= 1
                result.append(line[pos])
                pos += 1
        
        return ''.join(result)
    
    def _extract_footnote_content(self, command_content: str) -> Optional[str]:
        """
        Extract translatable content from footnote commands.
        
        For \\footnote{text}, \\marginpar{text}, \\thanks{text}, extracts the text
        inside the braces for translation.
        
        Args:
            command_content: Full command string (e.g., "\\footnote{Some text}")
            
        Returns:
            Content inside braces if found, None otherwise
        """
        # Match \command{content} pattern
        match = re.search(r'\\[a-zA-Z@]+\{([^}]*)\}', command_content)
        if match:
            return match.group(1)
        return None
    
    def _is_specialized_package_command(self, cmd_name: str, specialized_packages: set) -> bool:
        """
        Check if a command belongs to a specialized package.
        
        Args:
            cmd_name: Command name
            specialized_packages: Set of specialized packages found in document
            
        Returns:
            True if command is from specialized package
        """
        # Direct match
        if cmd_name in self.SPECIALIZED_COMMANDS:
            return True
        
        # Check if command starts with package prefix
        for pkg in specialized_packages:
            if cmd_name.startswith(pkg) or pkg in cmd_name.lower():
                return True
        
        return False


def parse_latex_document(tex_content: str) -> LaTeXDocument:
    """
    Convenience function to parse a LaTeX document.
    
    Args:
        tex_content: Raw LaTeX content as string
        
    Returns:
        LaTeXDocument dataclass with parsed structure
    """
    parser = LaTeXParser()
    return parser.parse(tex_content)


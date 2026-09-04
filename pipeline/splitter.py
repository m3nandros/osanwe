"""
Content Splitter module for Rosetta v3.

Responsible for splitting masked LaTeX content into manageable chunks for translation,
preserving context and structural integrity.
"""

import re
from typing import List, Optional, Dict, Any
from dataclasses import dataclass

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Chunk:
    """
    Represents a chunk of text to be translated.
    """
    id: str
    text: str
    type: str  # 'preamble', 'section', 'subsection', 'paragraph', 'bib'
    order: int
    token_count: int
    context_summary: Optional[str] = None
    original_section_title: Optional[str] = None


class ContentSplitter:
    """
    Splits masked LaTeX content into chunks.
    
    Strategies:
    1. Split by major sections (\section, \chapter)
    2. Split by subsections if sections are too large
    3. Split by paragraphs if subsections are too large
    4. Isolate Preamble and Bibliography
    """
    
    def __init__(self, max_chunk_tokens: int = 1500):
        """
        Initialize the splitter.
        
        Args:
            max_chunk_tokens: Approximate maximum tokens per chunk
        """
        self.max_chunk_tokens = max_chunk_tokens
        self.logger = get_logger(__name__)
        
    def split_content(self, masked_content: str) -> List[Chunk]:
        """
        Split masked content into chunks.
        
        Args:
            masked_content: The masked LaTeX content
            
        Returns:
            List of Chunk objects
        """
        self.logger.info("Starting content splitting...")
        chunks = []
        
        # 1. Extract Preamble
        preamble_match = re.search(r'\\begin\{document\}', masked_content)
        body_start_idx = 0
        
        if preamble_match:
            preamble_end = preamble_match.start()
            preamble_text = masked_content[:preamble_end]
            body_start_idx = preamble_end
            
            chunks.append(Chunk(
                id="chunk_0_preamble",
                text=preamble_text,
                type="preamble",
                order=0,
                token_count=self._estimate_tokens(preamble_text),
                context_summary="Document Preamble"
            ))
        else:
            self.logger.warning("No \\begin{document} found, treating all as body")
            
        full_body_text = masked_content[body_start_idx:]
            
        # 2. Locate Bibliography
        bib_match = re.search(r'\\begin\{thebibliography\}', full_body_text)
        
        pre_bib_text = full_body_text
        bib_text = ""
        post_bib_text = ""
        
        if bib_match:
            bib_start = bib_match.start()
            # Find the end of bibliography
            bib_end_match = re.search(r'\\end\s*\{\s*thebibliography\s*\}', full_body_text[bib_start:])
            
            if bib_end_match:
                bib_end = bib_start + bib_end_match.end()
                pre_bib_text = full_body_text[:bib_start]
                bib_text = full_body_text[bib_start:bib_end]
                post_bib_text = full_body_text[bib_end:]
            else:
                # Fallback: take everything from start of bib
                pre_bib_text = full_body_text[:bib_start]
                bib_text = full_body_text[bib_start:]
                post_bib_text = ""
        
        # 3. Process Pre-Bib Content
        current_order = len(chunks)
        if pre_bib_text.strip():
            pre_bib_chunks = self._process_body_text(pre_bib_text, current_order)
            chunks.extend(pre_bib_chunks)
            current_order += len(pre_bib_chunks)
            
        # 4. Process Bibliography
        if bib_text:
            bib_tokens = self._estimate_tokens(bib_text)
            if bib_tokens > self.max_chunk_tokens:
                self.logger.info(f"Bibliography is large ({bib_tokens} tokens), splitting...")
                bib_chunks = self._split_bibliography(bib_text, current_order)
                chunks.extend(bib_chunks)
                current_order += len(bib_chunks)
            else:
                chunks.append(Chunk(
                    id=f"chunk_{current_order}_bib",
                    text=bib_text,
                    type="bib",
                    order=current_order,
                    token_count=bib_tokens,
                    context_summary="Bibliography"
                ))
                current_order += 1
                
        # 5. Process Post-Bib Content
        if post_bib_text.strip():
            post_bib_chunks = self._process_body_text(post_bib_text, current_order)
            chunks.extend(post_bib_chunks)
            
        self.logger.info(f"Split content into {len(chunks)} chunks")
        return chunks

    def _process_body_text(self, body_text: str, start_order: int) -> List[Chunk]:
        """Process a section of body text (split by sections/paragraphs)."""
        chunks = []
        current_order = start_order
        
        # Find all section indices
        section_pattern = re.compile(r'\\(?:section|chapter|part)\*?\{')
        matches = list(section_pattern.finditer(body_text))
        
        if not matches:
            # No sections, split by paragraphs
            return self._split_by_paragraphs(body_text, start_order)
            
        # Content before first section
        if matches[0].start() > 0:
            intro_text = body_text[:matches[0].start()]
            if intro_text.strip():
                intro_chunks = self._split_large_text(intro_text, "intro", current_order)
                chunks.extend(intro_chunks)
                current_order += len(intro_chunks)
        
        for i, match in enumerate(matches):
            start_pos = match.start()
            
            # Determine end of this section
            if i < len(matches) - 1:
                end_pos = matches[i+1].start()
            else:
                end_pos = len(body_text)
            
            section_text = body_text[start_pos:end_pos]
            
            # Extract title
            title_match = re.search(r'\{([^}]+)\}', section_text)
            title = title_match.group(1) if title_match else "Section"
            
            if self._estimate_tokens(section_text) > self.max_chunk_tokens:
                section_chunks = self._split_large_text(
                    section_text, "section", current_order, context=f"Section: {title}"
                )
                chunks.extend(section_chunks)
                current_order += len(section_chunks)
            else:
                chunks.append(Chunk(
                    id=f"chunk_{current_order}_section",
                    text=section_text,
                    type="section",
                    order=current_order,
                    token_count=self._estimate_tokens(section_text),
                    context_summary=f"Section: {title}",
                    original_section_title=title
                ))
                current_order += 1
                
        return chunks

    def _split_large_text(self, text: str, type_prefix: str, start_order: int, 
                         context: str = "") -> List[Chunk]:
        """Split text that is too large for a single chunk."""
        chunks = []
        current_text = ""
        current_tokens = 0
        
        # Split by paragraphs (double newline)
        paragraphs = re.split(r'\n\s*\n', text)
        
        for i, para in enumerate(paragraphs):
            para_tokens = self._estimate_tokens(para)
            
            if current_tokens + para_tokens > self.max_chunk_tokens and current_text:
                # Flush current chunk
                chunks.append(Chunk(
                    id=f"chunk_{start_order + len(chunks)}_{type_prefix}",
                    text=current_text,
                    type=type_prefix,
                    order=start_order + len(chunks),
                    token_count=current_tokens,
                    context_summary=f"{context} (Part {len(chunks) + 1})"
                ))
                current_text = para + "\n\n"
                current_tokens = para_tokens
            else:
                current_text += para + "\n\n"
                current_tokens += para_tokens
                
        # Add remaining text
        if current_text:
            chunks.append(Chunk(
                id=f"chunk_{start_order + len(chunks)}_{type_prefix}",
                text=current_text,
                type=type_prefix,
                order=start_order + len(chunks),
                token_count=current_tokens,
                context_summary=f"{context} (Part {len(chunks) + 1})"
            ))
            
        return chunks

    def _split_by_paragraphs(self, text: str, start_order: int) -> List[Chunk]:
        """Split text purely by paragraphs."""
        return self._split_large_text(text, "paragraph", start_order)

    def _estimate_tokens(self, text: str) -> int:
        """Rough estimation of tokens (4 chars per token)."""
        return len(text) // 4

    def _split_bibliography(self, bib_text: str, start_order: int) -> List[Chunk]:
        """Split large bibliography by bibitem entries."""
        chunks = []
        
        # Find all \bibitem entries
        bibitem_pattern = re.compile(r'\\bibitem\{[^}]+\}')
        matches = list(bibitem_pattern.finditer(bib_text))
        
        if not matches:
            # No bibitems found, treat as single chunk
            return [Chunk(
                id=f"chunk_{start_order}_bib",
                text=bib_text,
                type="bib",
                order=start_order,
                token_count=self._estimate_tokens(bib_text),
                context_summary="Bibliography"
            )]
        
        # Extract header (before first bibitem)
        header = bib_text[:matches[0].start()]
        
        # Extract footer (after last bibitem, usually \end{thebibliography})
        footer_match = re.search(r'\\end\s*\{\s*thebibliography\s*\}', bib_text)
        if footer_match:
            footer = bib_text[footer_match.start():]
            # Remove footer from bib_text for processing
            bib_text_no_footer = bib_text[:footer_match.start()]
        else:
            footer = ""
            bib_text_no_footer = bib_text
        
        # Group bibitems into chunks
        current_text = header  # First chunk gets header
        current_tokens = self._estimate_tokens(header)
        first_chunk = True
        
        for i, match in enumerate(matches):
            start_pos = match.start()
            # Find end of this bibitem (start of next or end of text)
            if i < len(matches) - 1:
                end_pos = matches[i+1].start()
            else:
                end_pos = len(bib_text_no_footer)
            
            bibitem_text = bib_text_no_footer[start_pos:end_pos]
            bibitem_tokens = self._estimate_tokens(bibitem_text)
            
            if current_tokens + bibitem_tokens > self.max_chunk_tokens and not first_chunk:
                # Flush current chunk (not the first one, so no header)
                chunks.append(Chunk(
                    id=f"chunk_{start_order + len(chunks)}_bib",
                    text=current_text,
                    type="bib",
                    order=start_order + len(chunks),
                    token_count=current_tokens,
                    context_summary=f"Bibliography (Part {len(chunks) + 1})"
                ))
                current_text = bibitem_text  # No header for subsequent chunks
                current_tokens = bibitem_tokens
            else:
                current_text += bibitem_text
                current_tokens += bibitem_tokens
                first_chunk = False
        
        # Add remaining text with footer
        if current_text:
            current_text += footer  # Add footer to last chunk
            chunks.append(Chunk(
                id=f"chunk_{start_order + len(chunks)}_bib",
                text=current_text,
                type="bib",
                order=start_order + len(chunks),
                token_count=self._estimate_tokens(current_text),
                context_summary=f"Bibliography (Part {len(chunks) + 1})"
            ))
        
        return chunks

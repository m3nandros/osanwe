import pytest
from pipeline.splitter import ContentSplitter

class TestContentSplitter:
    
    def test_split_preamble(self):
        splitter = ContentSplitter()
        text = r"""
        \documentclass{article}
        \usepackage{tikz}
        \begin{document}
        Hello world
        \end{document}
        """
        chunks = splitter.split_content(text)
        
        assert len(chunks) >= 1
        assert chunks[0].type == "preamble"
        assert "\\documentclass{article}" in chunks[0].text
        
    def test_split_sections(self):
        splitter = ContentSplitter()
        text = r"""
        \begin{document}
        Intro text
        \section{First Section}
        Content 1
        \section{Second Section}
        Content 2
        \end{document}
        """
        chunks = splitter.split_content(text)
        
        # Expect: Preamble (empty/implicit), Intro chunk, Section 1, Section 2
        # Preamble chunk is created if \begin{document} is found
        
        # Filter for sections
        section_chunks = [c for c in chunks if c.type == "section"]
        assert len(section_chunks) == 2
        assert "First Section" in section_chunks[0].text
        assert "Second Section" in section_chunks[1].text
        
    def test_split_large_text(self):
        splitter = ContentSplitter(max_chunk_tokens=10) # Very small limit
        text = r"""
        \begin{document}
        Para 1 is short.
        
        Para 2 is longer and should probably be split if the limit is very small.
        
        Para 3 is here.
        \end{document}
        """
        chunks = splitter.split_content(text)
        
        # Should have multiple paragraph chunks
        para_chunks = [c for c in chunks if c.type == "paragraph" or c.type == "intro"]
        # Note: Intro text is split by paragraphs if no sections
        assert len(para_chunks) > 1

import pytest
from pipeline.masker import ContentMasker

class TestContentMasker:
    
    def test_mask_comments(self):
        masker = ContentMasker()
        text = "Hello world % This is a comment\nNext line"
        masked = masker.mask_content(text)
        
        assert "Hello world" in masked.text
        assert "% This is a comment" not in masked.text
        assert "Next line" in masked.text
        assert masked.stats["comments"] == 1
        assert len(masked.token_map) == 1
        
    def test_mask_inline_math(self):
        masker = ContentMasker()
        text = "Let $x^2 + y^2 = z^2$ be the equation."
        masked = masker.mask_content(text)
        
        assert "Let " in masked.text
        assert "$x^2 + y^2 = z^2$" not in masked.text
        assert "<<MATH_INLINE_" in masked.text
        assert masked.stats["math_inline"] == 1
        
    def test_mask_display_math(self):
        masker = ContentMasker()
        text = r"""
        The equation is:
        $$
        E = mc^2
        $$
        End of equation.
        """
        masked = masker.mask_content(text)
        
        assert "The equation is:" in masked.text
        assert "E = mc^2" not in masked.text
        assert "<<MATH_DISP_" in masked.text
        assert masked.stats["math_display"] == 1
        
    def test_mask_tikz(self):
        masker = ContentMasker()
        text = r"""
        \begin{figure}
        \begin{tikzpicture}
        \draw (0,0) -- (1,1);
        \end{tikzpicture}
        \caption{A tikz figure}
        \end{figure}
        """
        masked = masker.mask_content(text)
        
        assert "\\begin{figure}" in masked.text
        assert "\\begin{tikzpicture}" not in masked.text
        assert "\\draw (0,0) -- (1,1);" not in masked.text
        assert "<<TIKZ_" in masked.text
        assert "\\caption{A tikz figure}" in masked.text
        assert masked.stats["specialized"] == 1
        
    def test_nested_tikz(self):
        # This tests robust nesting handling
        masker = ContentMasker()
        text = r"""
        \begin{tikzpicture}
          \node {Outer};
          \begin{scope}
             \node {Inner};
          \end{scope}
        \end{tikzpicture}
        """
        masked = masker.mask_content(text)
        
        assert "\\begin{tikzpicture}" not in masked.text
        assert "Outer" not in masked.text
        assert "Inner" not in masked.text
        assert "<<TIKZ_" in masked.text
        # Should be one single token for the whole outer tikzpicture
        assert masked.stats["specialized"] == 1
        
    def test_unmask(self):
        masker = ContentMasker()
        text = "Hello $x$ world"
        masked = masker.mask_content(text)
        
        restored = masker.unmask_content(masked.text, masked.token_map)
        assert restored == text

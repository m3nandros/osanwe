import pytest
pytest.importorskip("pipeline.pdf_reconstruct")
"""Tests for Typst renderer."""
from pathlib import Path

from pipeline.pdf_reconstruct.typst_renderer import (
    MarkdownToTypstConverter,
    create_typst_document,
    render_markdown_to_pdf_typst,
)


class TestMarkdownToTypstConverter:
    """Tests for Markdown to Typst conversion."""

    def test_convert_heading_level_1(self):
        converter = MarkdownToTypstConverter()
        result = converter.convert("# Title")
        assert "= Title" in result

    def test_convert_heading_level_2(self):
        converter = MarkdownToTypstConverter()
        result = converter.convert("## Section")
        assert "== Section" in result

    def test_convert_heading_level_3(self):
        converter = MarkdownToTypstConverter()
        result = converter.convert("### Subsection")
        assert "=== Subsection" in result

    def test_convert_unnumbered_heading(self):
        converter = MarkdownToTypstConverter()
        result = converter.convert("## References {-}")
        assert "#heading(numbering: none)[References]" in result

    def test_convert_bold_text(self):
        converter = MarkdownToTypstConverter()
        result = converter.convert("This is **bold** text")
        assert "*bold*" in result

    def test_convert_italic_text(self):
        converter = MarkdownToTypstConverter()
        # Note: Markdown *italic* becomes Typst _italic_
        result = converter._convert_inline("This is *italic* text")
        assert "_italic_" in result

    def test_convert_link(self):
        converter = MarkdownToTypstConverter()
        result = converter._convert_inline("[Link text](https://example.com)")
        assert '#link("https://example.com")[Link text]' in result

    def test_convert_image(self):
        converter = MarkdownToTypstConverter()
        result = converter.convert("![Alt text](image.png)")
        assert '#image("image.png")' in result or '#figure' in result

    def test_convert_bullet_list(self):
        converter = MarkdownToTypstConverter()
        result = converter.convert("- Item 1\n- Item 2")
        assert "- Item 1" in result
        assert "- Item 2" in result

    def test_convert_numbered_list(self):
        converter = MarkdownToTypstConverter()
        result = converter.convert("1. First\n2. Second")
        assert "1. First" in result
        assert "2. Second" in result

    def test_convert_code_block(self):
        converter = MarkdownToTypstConverter()
        result = converter.convert("```python\nprint('hello')\n```")
        assert "```python" in result
        assert "print('hello')" in result

    def test_convert_math_block(self):
        converter = MarkdownToTypstConverter()
        result = converter.convert("$$x^2 + y^2 = z^2$$")
        assert "$ x^2 + y^2 = z^2 $" in result

    def test_convert_simple_table(self):
        converter = MarkdownToTypstConverter()
        md_table = """| Col1 | Col2 |
|------|------|
| A    | B    |
| C    | D    |"""
        result = converter.convert(md_table)
        assert "#figure(" in result
        assert "table(" in result
        assert "columns: 2" in result

    def test_convert_table_with_russian(self):
        converter = MarkdownToTypstConverter()
        md_table = """| Заголовок | Значение |
|-----------|----------|
| Тест      | Данные   |"""
        result = converter.convert(md_table)
        assert "Заголовок" in result
        assert "Значение" in result
        assert "Тест" in result

    def test_preserve_inline_code(self):
        converter = MarkdownToTypstConverter()
        result = converter._convert_inline("Use `code` here")
        assert "`code`" in result

    def test_preserve_math_inline(self):
        converter = MarkdownToTypstConverter()
        result = converter._convert_inline("The formula $E=mc^2$ is famous")
        assert "$E=mc^2$" in result


class TestCreateTypstDocument:
    """Tests for full document creation."""

    def test_creates_document_with_title(self):
        md = "# My Title\n\nSome content."
        result = create_typst_document(md, title="My Title")
        assert "My Title" in result
        assert "#set document(" in result

    def test_creates_document_with_author(self):
        md = "# Title\n\nContent."
        result = create_typst_document(md, title="Title", author="John Doe")
        assert "John Doe" in result

    def test_creates_document_with_abstract(self):
        md = "# Title\n\nContent."
        result = create_typst_document(md, title="Title", abstract="This is abstract.")
        assert "This is abstract." in result
        assert "Аннотация" in result

    def test_sets_russian_language(self):
        md = "# Заголовок\n\nТекст на русском."
        result = create_typst_document(md)
        assert 'lang: "ru"' in result


class TestTypstRendering:
    """Integration tests for Typst rendering."""

    def test_render_simple_document(self, tmp_path: Path):
        md_content = """# Test Document

## Introduction

This is a test document with **bold** and *italic* text.

## Methods

We used the following approach:
- Step 1
- Step 2
- Step 3

## Results

| Metric | Value |
|--------|-------|
| A      | 10    |
| B      | 20    |

## Conclusion

The end.
"""
        md_path = tmp_path / "test.md"
        md_path.write_text(md_content, encoding="utf-8")
        
        output_pdf = tmp_path / "test.pdf"
        
        result = render_markdown_to_pdf_typst(
            md_path=md_path,
            output_pdf_path=output_pdf,
            logs_dir=tmp_path / "logs",
        )
        
        # Check that Typst file was created
        assert result.typst_path.exists()
        typst_content = result.typst_path.read_text()
        assert "= Test Document" in typst_content
        assert "== Introduction" in typst_content
        
        # If Typst is installed, PDF should be created
        if result.success:
            assert output_pdf.exists()
            assert output_pdf.stat().st_size > 0

    def test_render_russian_document(self, tmp_path: Path):
        md_content = """# Тестовый документ

## Введение

Это тестовый документ на русском языке.

## Методы

| Параметр | Значение |
|----------|----------|
| Тест     | 100      |

## Заключение

Конец документа.
"""
        md_path = tmp_path / "test_ru.md"
        md_path.write_text(md_content, encoding="utf-8")
        
        output_pdf = tmp_path / "test_ru.pdf"
        
        result = render_markdown_to_pdf_typst(
            md_path=md_path,
            output_pdf_path=output_pdf,
            logs_dir=tmp_path / "logs",
        )
        
        assert result.typst_path.exists()
        typst_content = result.typst_path.read_text()
        assert "Тестовый документ" in typst_content
        assert "Введение" in typst_content
        
        if result.success:
            assert output_pdf.exists()


class TestLatexBlockConversion:
    """Tests for LaTeX block conversion to Typst."""

    def test_convert_latex_table_basic(self):
        converter = MarkdownToTypstConverter()
        latex = r"""
\begin{table}[htbp]
\centering
\begin{tabularx}{\linewidth}{|l|c|}
\hline
Header1 & Header2 \\
\hline
Cell1 & Cell2 \\
\hline
\end{tabularx}
\caption{Test table}
\end{table}
"""
        result = converter._convert_latex_table(latex)
        assert "#figure(" in result
        assert "table(" in result
        assert "Header1" in result or "Cell1" in result

    def test_convert_rosetta_fit_token(self):
        converter = MarkdownToTypstConverter()
        latex = r"\rosettaFitToken{SomeText}"
        result = converter._convert_latex_table(f"\\begin{{tabular}}{{l}}{latex}\\end{{tabular}}")
        # rosettaFitToken should be stripped, leaving just the text
        assert "rosettaFitToken" not in result or "SomeText" in result

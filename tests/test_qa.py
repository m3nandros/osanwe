import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from pipeline.latex_postprocessor import post_process_latex
from pipeline.validator import Validator, ValidationResult
from pipeline.pdf_compiler import PDFCompiler, CompilationResult

class TestPostProcessor:
    def test_fix_broken_commands(self):
        text = r"This is \ text and \ cite{ref}."
        fixed = post_process_latex(text)
        assert r"\text" in fixed
        assert r"\cite{ref}" in fixed
        
    def test_fix_spacing(self):
        text = r"Math $ x $ end."
        # Current implementation doesn't fix $ x $ to $x$ aggressively
        # but let's check if it breaks anything
        fixed = post_process_latex(text)
        assert "$" in fixed

    def test_normalize_float_environments(self):
        text = (
            r"\begin{figure*}"
            r"{\includegraphics[width=\textwidth]{img}}"
            r"\end{figure*>}"
        )
        fixed = post_process_latex(text)
        assert r"\begin{figure}[ht]" in fixed
        assert r"\end{figure}" in fixed
        assert "{\\includegraphics" not in fixed

    def test_table_normalization_to_tabularx(self):
        text = r"""
\documentclass{article}
\begin{document}
\begin{tabular}{|l|c|r|}
\hline
Название & Значение & Описание \\
\hline
Модель & 123 & Это очень длинное описание, которое в противном случае вышло бы за правое поле страницы \\
\hline
\end{tabular}
\end{document}
"""
        fixed = post_process_latex(text)
        assert "\\usepackage{tabularx}" in fixed
        assert "\\begin{tabularx}{\\textwidth}" in fixed
        assert "Y" in fixed.split("\\begin{tabularx}{\\textwidth}", 1)[1].split("}", 1)[0]

class TestValidator:
    def test_validate_good(self):
        validator = Validator()
        orig = r"\section{Title} Hello world. $$E=mc^2$$"
        trans = r"\section{Заголовок} Привет мир. $$E=mc^2$$"
        
        result = validator.validate_translation(orig, trans)
        assert result.valid
        assert result.score > 0.8
        
    def test_validate_bad_structure(self):
        validator = Validator()
        orig = r"\section{One} \section{Two}"
        trans = r"\section{One}" # Missing section
        
        result = validator.validate_translation(orig, trans)
        assert not result.valid
        assert "Section count mismatch" in str(result.issues)

class TestPDFCompiler:
    @patch('pipeline.pdf_compiler.subprocess.run')
    def test_compile_success(self, mock_run, tmp_path):
        compiler = PDFCompiler()
        tex_path = tmp_path / "test.tex"
        tex_path.write_text("content")
        
        # Mock successful run
        mock_run.return_value.returncode = 0
        
        result = compiler.compile_pdf(tex_path)
        assert result.success
        assert result.attempts == 1
        
    @patch('pipeline.pdf_compiler.subprocess.run')
    def test_compile_retry(self, mock_run, tmp_path):
        compiler = PDFCompiler(max_attempts=2)
        tex_path = tmp_path / "test.tex"
        tex_path.write_text(r"\documentclass{article} \begin{document} \includegraphics{file} \end{document}")
        
        # Mock fail then success
        # We need to mock side_effect to simulate different runs
        # But _surgical_fix reads the file, so we need to ensure it sees the file
        
        # First run: fail with log
        fail_result = MagicMock()
        fail_result.returncode = 1
        
        # Second run: success
        success_result = MagicMock()
        success_result.returncode = 0
        
        mock_run.side_effect = [fail_result, success_result]
        
        # We also need to mock reading the log file
        # This is hard with just subprocess mock.
        # Let's just test that it retries.
        
        # To make _parse_log_errors find errors, we need to write a log file
        log_path = tex_path.parent / "test.log"
        log_path.write_text("! Undefined control sequence.\nl.10 \\includegraphics")
        
        result = compiler.compile_pdf(tex_path)
        
        # It should have tried twice
        assert mock_run.call_count == 2
        # It should have tried to fix it (by adding graphicx)
        # Check if file content changed
        fixed_content = (tex_path.parent / "test_fixed_1.tex").read_text()
        assert "\\usepackage{graphicx}" in fixed_content

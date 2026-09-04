"""
Tests for LaTeX post-processor module.
Тесты для модуля пост-обработки LaTeX.
"""

import pytest
from pipeline.latex_postprocessor import (
    post_process_latex,
    _fix_nobibliography_command,
    _fix_bibliography_spacing,
    _fix_combining_diacritics,
    _fix_babel_conflicts,
    _fix_duplicate_end_document,
)


class TestFixNobibliographyCommand:
    """Тесты для функции _fix_nobibliography_command."""

    def test_removes_nobibliography_star(self):
        """Проверяет удаление \\nobibliography* команды."""
        input_text = r"""
\bibliographystyle{abbrvnat}
\nobibliography*
\bibliography{custom}
"""
        expected = r"""
\bibliographystyle{abbrvnat}
\bibliography{custom}
"""
        result = _fix_nobibliography_command(input_text)
        assert result == expected

    def test_removes_nobibliography_star_with_newline(self):
        """Проверяет удаление \\nobibliography* с переводом строки."""
        input_text = r"\nobibliography*" + "\n" + r"\bibliography{refs}"
        expected = r"\bibliography{refs}"
        result = _fix_nobibliography_command(input_text)
        assert result == expected

    def test_converts_nobibliography_with_braces(self):
        """Проверяет преобразование \\nobibliography{...} в \\bibliography{...}."""
        input_text = r"\nobibliography{myfile}"
        expected = r"\bibliography{myfile}"
        result = _fix_nobibliography_command(input_text)
        assert result == expected

    def test_no_change_when_no_nobibliography(self):
        """Проверяет, что текст не изменяется без \\nobibliography."""
        input_text = r"""
\bibliographystyle{plain}
\bibliography{references}
"""
        result = _fix_nobibliography_command(input_text)
        assert result == input_text

    def test_preserves_bibliography_command(self):
        """Проверяет, что \\bibliography{...} сохраняется после удаления \\nobibliography*."""
        input_text = r"""
\nobibliography*
\bibliography{custom}
\end{document}
"""
        result = _fix_nobibliography_command(input_text)
        assert r"\bibliography{custom}" in result
        assert r"\nobibliography*" not in result


class TestFixBibliographySpacing:
    """Тесты для функции _fix_bibliography_spacing."""

    def test_adds_raggedbottom_before_thebibliography(self):
        """Проверяет добавление \\raggedbottom перед thebibliography."""
        input_text = r"\begin{thebibliography}{99}"
        result = _fix_bibliography_spacing(input_text)
        assert r"\raggedbottom" in result
        assert result.index(r"\raggedbottom") < result.index(r"\begin{thebibliography}")

    def test_no_duplicate_raggedbottom(self):
        """Проверяет, что \\raggedbottom не дублируется."""
        input_text = r"\raggedbottom" + "\n" + r"\begin{thebibliography}{99}"
        result = _fix_bibliography_spacing(input_text)
        assert result.count(r"\raggedbottom") == 1


class TestFixCombiningDiacritics:
    """Тесты для функции _fix_combining_diacritics."""

    def test_removes_combining_acute_accent(self):
        """Проверяет удаление комбинирующего акута (U+0301)."""
        # "прого́нов" с комбинирующим акутом
        input_text = "прого\u0301нов"
        expected = "прогонов"
        result = _fix_combining_diacritics(input_text)
        assert result == expected

    def test_preserves_normal_text(self):
        """Проверяет, что обычный текст не изменяется."""
        input_text = "Обычный русский текст без диакритиков"
        result = _fix_combining_diacritics(input_text)
        assert result == input_text

    def test_removes_multiple_diacritics(self):
        """Проверяет удаление нескольких комбинирующих символов."""
        # Текст с несколькими комбинирующими символами
        input_text = "a\u0301b\u0300c\u0302"  # á̀̂ (combining acute, grave, circumflex)
        expected = "abc"
        result = _fix_combining_diacritics(input_text)
        assert result == expected


class TestFixBabelConflicts:
    """Тесты для функции _fix_babel_conflicts."""

    def test_adds_pass_options_for_googlecloud_class(self):
        """Проверяет добавление PassOptionsToPackage для класса googlecloud."""
        input_text = r"""
\documentclass{googlecloud}
\usepackage[russian]{babel}
Привет мир
\begin{document}
\end{document}
"""
        result = _fix_babel_conflicts(input_text)
        assert r"\PassOptionsToPackage{russian}{babel}" in result
        assert r"\usepackage[russian]{babel}" not in result

    def test_no_change_for_standard_class(self):
        """Проверяет, что для обычного класса babel остаётся."""
        input_text = r"""
\documentclass{article}
\usepackage[russian]{babel}
Привет мир
\begin{document}
\end{document}
"""
        result = _fix_babel_conflicts(input_text)
        assert r"\usepackage[russian]{babel}" in result


class TestFixDuplicateEndDocument:
    """Тесты для функции _fix_duplicate_end_document."""

    def test_removes_duplicate_end_document(self):
        """Проверяет удаление дублирующегося \\end{document}."""
        input_text = r"""\begin{document}
\maketitle
\end{document}

\section{Введение}
Текст введения.

\end{document}"""
        result = _fix_duplicate_end_document(input_text)
        # Должен остаться только один \end{document} в конце
        assert result.count(r"\end{document}") == 1
        assert r"\section{Введение}" in result
        assert result.strip().endswith(r"\end{document}")

    def test_removes_multiple_duplicates(self):
        """Проверяет удаление нескольких дубликатов \\end{document}."""
        input_text = r"""\begin{document}
\end{document}
\section{A}
\end{document}
\section{B}
\end{document}"""
        result = _fix_duplicate_end_document(input_text)
        assert result.count(r"\end{document}") == 1
        assert r"\section{A}" in result
        assert r"\section{B}" in result

    def test_no_change_with_single_end_document(self):
        """Проверяет, что один \\end{document} не удаляется."""
        input_text = r"""\begin{document}
\section{Введение}
Текст.
\end{document}"""
        result = _fix_duplicate_end_document(input_text)
        assert result == input_text
        assert result.count(r"\end{document}") == 1

    def test_no_change_without_end_document(self):
        """Проверяет, что документ без \\end{document} не изменяется."""
        input_text = r"""\begin{document}
\section{Введение}
Текст."""
        result = _fix_duplicate_end_document(input_text)
        assert result == input_text


class TestPostProcessLatex:
    """Интеграционные тесты для post_process_latex."""

    def test_fixes_nobibliography_in_full_document(self):
        """Проверяет исправление \\nobibliography* в полном документе."""
        input_text = r"""
\documentclass{article}
\begin{document}
Some text~\citep{ref1}.
\bibliographystyle{abbrvnat}
\nobibliography*
\bibliography{refs}
\end{document}
"""
        result = post_process_latex(input_text)
        assert r"\nobibliography*" not in result
        assert r"\bibliography{refs}" in result

    def test_fixes_duplicate_end_document_in_full_document(self):
        """Проверяет удаление дубликата \\end{document} в полном документе."""
        input_text = r"""
\documentclass{article}
\begin{document}
\maketitle
\end{document}

\section{Введение}
Текст введения.

\end{document}
"""
        result = post_process_latex(input_text)
        # Должен остаться только один \end{document}
        assert result.count(r"\end{document}") == 1
        assert r"\section{Введение}" in result

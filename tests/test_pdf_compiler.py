"""
Tests for PDF compiler module.
"""


from pathlib import Path

from pipeline.pdf_compiler import PDFCompiler


def test_patch_unicode_preamble_injects_polyglossia_for_arabic():
    compiler = PDFCompiler()
    raw = """\\documentclass{article}
\\usepackage[utf8]{inputenc}
\\usepackage[english]{babel}
\\begin{document}
مرحبا بالعالم
\\end{document}
"""
    patched = compiler._patch_unicode_preamble(raw)
    assert "\\usepackage{fontspec}" in patched
    assert "\\usepackage{polyglossia}" in patched
    assert "\\setdefaultlanguage{arabic}" in patched
    assert "% \\usepackage[english]{babel}" in patched
    assert "\\def\\enlargethispage{\\futurelet\\rosetta@tok" in patched


def test_patch_unicode_preamble_injects_polyglossia_for_hebrew():
    compiler = PDFCompiler()
    raw = """\\documentclass{article}
\\usepackage[utf8]{inputenc}
\\usepackage{babel}
\\begin{document}
שלום עולם
\\end{document}
"""
    patched = compiler._patch_unicode_preamble(raw)
    assert "\\usepackage{fontspec}" in patched
    assert "\\usepackage{polyglossia}" in patched
    assert "\\setdefaultlanguage{hebrew}" in patched
    assert "% \\usepackage{babel}" in patched
    assert "\\def\\enlargethispage{\\futurelet\\rosetta@tok" in patched


def test_bibliography_fallback_bbl_when_bib_missing(tmp_path):
    compiler = PDFCompiler()

    aux_path = tmp_path / "translated.aux"
    aux_path.write_text(
        "\\relax\n\\bibstyle{unsrt}\n\\bibdata{main}\n",
        encoding="utf-8",
    )

    assert compiler._referenced_bib_files_exist(aux_path, tmp_path) is False

    fallback_bbl = tmp_path / "main.bbl"
    fallback_bbl.write_text(
        "\\begin{thebibliography}{1}\n\\bibitem{a} X.\n\\end{thebibliography}\n",
        encoding="utf-8",
    )

    found = compiler._find_fallback_bbl(tmp_path, target_stem="translated")
    assert found == fallback_bbl

    ok = compiler._install_fallback_bbl(tmp_path, target_stem="translated", fallback_bbl=fallback_bbl)
    assert ok
    installed = tmp_path / "translated.bbl"
    assert installed.exists()
    assert "\\bibitem" in installed.read_text(encoding="utf-8", errors="replace")


def test_find_fallback_bbl_prefers_largest_nonempty(tmp_path):
    compiler = PDFCompiler()

    (tmp_path / "empty.bbl").write_text("\\begin{thebibliography}{}\\n\\end{thebibliography}\\n", encoding="utf-8")
    big = tmp_path / "big.bbl"
    small = tmp_path / "small.bbl"

    small.write_text(
        "\\begin{thebibliography}{1}\n\\bibitem{x} A.\n\\end{thebibliography}\n",
        encoding="utf-8",
    )
    big.write_text(
        "\\begin{thebibliography}{2}\n\\bibitem{x} A.\n\\bibitem{y} B.\n\\end{thebibliography}\n",
        encoding="utf-8",
    )

    found = compiler._find_fallback_bbl(tmp_path, target_stem="translated")
    assert found == big


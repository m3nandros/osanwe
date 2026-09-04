import pytest
pytest.importorskip("pipeline.pdf_reconstruct")
import shutil
from pathlib import Path


from pipeline.pdf_reconstruct.bundle import PdfBundlePaths
from pipeline.pdf_reconstruct.renderer import render_markdown_to_pdf


def test_pdf_render_optional(tmp_path: Path):
    if shutil.which("pandoc") is None or shutil.which("xelatex") is None:
        pytest.skip("pandoc/xelatex not available")

    md_path = tmp_path / "paper.ru.md"
    md_path.write_text("# Заголовок\n\nТекст.\n", encoding="utf-8")

    bundle = PdfBundlePaths(tmp_path)
    template = Path("templates") / "rosetta.latex"
    out_pdf = tmp_path / "out.pdf"

    render_markdown_to_pdf(md_path=md_path, output_pdf_path=out_pdf, template_path=template, logs_dir=bundle.logs_dir, target_lang="ru")
    assert out_pdf.exists()
    assert out_pdf.stat().st_size > 0

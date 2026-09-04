import pytest
pytest.importorskip("pipeline.pdf_reconstruct")
from pathlib import Path

from pipeline.pdf_reconstruct.bundle import PdfBundlePaths
from pipeline.pdf_reconstruct.pipeline import extract_pdf, normalize_bundle
from pipeline.pdf_reconstruct.cli import pdf_main


def test_pdf_pipeline_extract_mock_and_normalize(tmp_path: Path):
    pdf_path = tmp_path / "input.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%mock\n")

    out_dir = tmp_path / "out"
    bundle = extract_pdf(pdf_path=pdf_path, out_dir=out_dir, extractor="mock")

    assert bundle.raw_md_path.exists()
    assert bundle.manifest_path.exists()
    assert bundle.assets_dir.exists()

    norm_path = normalize_bundle(bundle)
    assert norm_path.exists()

    text = norm_path.read_text(encoding="utf-8", errors="replace")
    assert "Test Paper" in text
    assert len(text.strip()) > 0

    # assets link should be normalized
    assert "assets/fig1.png" in text


def test_pdf_cli_run_mock_stop_after_normalize(tmp_path: Path):
    pdf_path = tmp_path / "input.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%mock\n")

    out_dir = tmp_path / "run_out"
    rc = pdf_main(["run", str(pdf_path), "-o", str(out_dir), "--lang", "ru", "--extractor", "mock", "--stop-after", "normalize"])
    assert rc == 0

    paper_dirs = [p for p in out_dir.iterdir() if p.is_dir()]
    assert paper_dirs
    paper_dir = sorted(paper_dirs)[0]
    bundle = PdfBundlePaths(paper_dir / "bundle")
    assert bundle.normalized_md_path.exists()

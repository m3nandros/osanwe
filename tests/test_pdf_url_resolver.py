import pytest
pytest.importorskip("pipeline.pdf_reconstruct")
from pipeline.pdf_reconstruct.url_resolver import arxiv_abs_to_pdf_url, arxiv_id_to_pdf_url, resolve_pdf_url


def test_arxiv_abs_to_pdf_url():
    assert arxiv_abs_to_pdf_url("https://arxiv.org/abs/1706.03762") == "https://arxiv.org/pdf/1706.03762.pdf"


def test_arxiv_id_to_pdf_url():
    assert arxiv_id_to_pdf_url("1706.03762") == "https://arxiv.org/pdf/1706.03762.pdf"
    assert arxiv_id_to_pdf_url("1706.03762v2") == "https://arxiv.org/pdf/1706.03762v2.pdf"


def test_resolve_pdf_url_prefers_arxiv_transform_for_abs():
    assert resolve_pdf_url("https://arxiv.org/abs/1706.03762") == "https://arxiv.org/pdf/1706.03762.pdf"

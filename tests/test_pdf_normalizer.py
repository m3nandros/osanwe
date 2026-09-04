import pytest
pytest.importorskip("pipeline.pdf_reconstruct")
from pipeline.pdf_reconstruct.normalizer import MarkdownNormalizer


def test_markdown_normalizer_removes_page_artifacts_and_normalizes_images():
    raw = """
# Title

Page 1 of 2

Some text.

2

![](figure.png)

![](assets/already.png)

""".lstrip("\n")

    norm = MarkdownNormalizer().normalize_text(raw).text

    assert "Page 1 of 2" not in norm
    assert "\n2\n" not in norm
    assert "![](assets/figure.png)" in norm
    assert "![](assets/already.png)" in norm

#!/usr/bin/env python3
"""
A/B Test: LaTeX vs Typst renderers for PDF generation.

This script compares the two rendering approaches on test documents
and generates a comparison report.
"""
import time
import tempfile
import shutil
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

# Test documents
TEST_DOCUMENTS = {
    "simple_russian": """# Тестовый документ

## Введение

Это простой тестовый документ на русском языке для проверки рендеринга.

## Методы

Мы использовали следующие подходы:
- Первый метод
- Второй метод
- Третий метод

### Подраздел методов

Дополнительные детали о методах.

## Результаты

| Параметр | Значение | Единицы |
|----------|----------|---------|
| Тест 1   | 100      | мс      |
| Тест 2   | 200      | мс      |
| Тест 3   | 150      | мс      |

## Обсуждение

Результаты показывают, что **первый метод** работает лучше всего.

## Заключение {-}

Исследование завершено успешно.
""",

    "with_math": """# Научная статья с формулами

## Аннотация

Данная статья рассматривает математические модели.

## Введение

Рассмотрим уравнение:

$$E = mc^2$$

Это знаменитая формула Эйнштейна.

## Методы

Используем формулу для расчёта:

$$\\sum_{i=1}^{n} x_i = \\frac{n(n+1)}{2}$$

## Результаты

Результаты представлены в таблице:

| n | Сумма |
|---|-------|
| 1 | 1     |
| 2 | 3     |
| 3 | 6     |

## Заключение {-}

Формулы работают корректно.
""",

    "complex_tables": """# Документ со сложными таблицами

## Введение

Тестируем рендеринг таблиц.

## Данные

| Автор | Страна | Год | Метод | Результат |
|-------|--------|-----|-------|-----------|
| Иванов | Россия | 2020 | Метод А | Положительный |
| Петров | Россия | 2021 | Метод Б | Отрицательный |
| Сидоров | Россия | 2022 | Метод В | Нейтральный |

## Анализ

Анализ показал следующее:

1. Первый пункт анализа
2. Второй пункт анализа
3. Третий пункт анализа

## Заключение {-}

Таблицы отображаются корректно.
""",
}


@dataclass
class RenderResult:
    success: bool
    time_seconds: float
    pdf_size_bytes: int
    errors: list[str]
    warnings: list[str]


def test_latex_render(md_content: str, tmp_dir: Path) -> RenderResult:
    """Test LaTeX rendering."""
    from pipeline.pdf_reconstruct.renderer import render_markdown_to_pdf
    from pipeline.pdf_reconstruct.bundle import PdfBundlePaths
    
    md_path = tmp_dir / "test.md"
    md_path.write_text(md_content, encoding="utf-8")
    
    bundle_dir = tmp_dir / "bundle"
    bundle_dir.mkdir(exist_ok=True)
    
    output_pdf = tmp_dir / "output_latex.pdf"
    template_path = Path("templates/rosetta.latex")
    
    errors = []
    start_time = time.time()
    
    try:
        render_markdown_to_pdf(
            md_path=md_path,
            output_pdf_path=output_pdf,
            template_path=template_path,
            logs_dir=tmp_dir / "logs_latex",
            target_lang="ru",
        )
        elapsed = time.time() - start_time
        
        if output_pdf.exists():
            return RenderResult(
                success=True,
                time_seconds=elapsed,
                pdf_size_bytes=output_pdf.stat().st_size,
                errors=[],
                warnings=[],
            )
        else:
            return RenderResult(
                success=False,
                time_seconds=elapsed,
                pdf_size_bytes=0,
                errors=["PDF not created"],
                warnings=[],
            )
    except Exception as e:
        elapsed = time.time() - start_time
        return RenderResult(
            success=False,
            time_seconds=elapsed,
            pdf_size_bytes=0,
            errors=[str(e)],
            warnings=[],
        )


def test_typst_render(md_content: str, tmp_dir: Path) -> RenderResult:
    """Test Typst rendering."""
    from pipeline.pdf_reconstruct.typst_renderer import render_markdown_to_pdf_typst
    
    md_path = tmp_dir / "test.md"
    md_path.write_text(md_content, encoding="utf-8")
    
    output_pdf = tmp_dir / "output_typst.pdf"
    
    start_time = time.time()
    
    result = render_markdown_to_pdf_typst(
        md_path=md_path,
        output_pdf_path=output_pdf,
        logs_dir=tmp_dir / "logs_typst",
    )
    
    elapsed = time.time() - start_time
    
    return RenderResult(
        success=result.success,
        time_seconds=elapsed,
        pdf_size_bytes=output_pdf.stat().st_size if output_pdf.exists() else 0,
        errors=result.errors,
        warnings=result.warnings,
    )


def run_ab_test():
    """Run A/B test comparing LaTeX and Typst renderers."""
    print("=" * 60)
    print("A/B TEST: LaTeX vs Typst Renderers")
    print("=" * 60)
    print()
    
    results = {}
    
    for doc_name, md_content in TEST_DOCUMENTS.items():
        print(f"\n### Testing: {doc_name}")
        print("-" * 40)
        
        results[doc_name] = {"latex": None, "typst": None}
        
        # Test LaTeX
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            print("  LaTeX: ", end="", flush=True)
            latex_result = test_latex_render(md_content, tmp_path)
            results[doc_name]["latex"] = latex_result
            if latex_result.success:
                print(f"✓ ({latex_result.time_seconds:.2f}s, {latex_result.pdf_size_bytes/1024:.1f}KB)")
            else:
                print(f"✗ ({latex_result.errors[0][:50] if latex_result.errors else 'Unknown error'})")
        
        # Test Typst
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            print("  Typst: ", end="", flush=True)
            typst_result = test_typst_render(md_content, tmp_path)
            results[doc_name]["typst"] = typst_result
            if typst_result.success:
                print(f"✓ ({typst_result.time_seconds:.2f}s, {typst_result.pdf_size_bytes/1024:.1f}KB)")
            else:
                print(f"✗ ({typst_result.errors[0][:50] if typst_result.errors else 'Unknown error'})")
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    latex_success = sum(1 for r in results.values() if r["latex"] and r["latex"].success)
    typst_success = sum(1 for r in results.values() if r["typst"] and r["typst"].success)
    total = len(results)
    
    latex_time = sum(r["latex"].time_seconds for r in results.values() if r["latex"] and r["latex"].success)
    typst_time = sum(r["typst"].time_seconds for r in results.values() if r["typst"] and r["typst"].success)
    
    print(f"\nSuccess Rate:")
    print(f"  LaTeX: {latex_success}/{total} ({latex_success/total*100:.0f}%)")
    print(f"  Typst: {typst_success}/{total} ({typst_success/total*100:.0f}%)")
    
    print(f"\nTotal Render Time (successful only):")
    print(f"  LaTeX: {latex_time:.2f}s")
    print(f"  Typst: {typst_time:.2f}s")
    
    if latex_time > 0 and typst_time > 0:
        speedup = latex_time / typst_time
        print(f"  Typst is {speedup:.1f}x {'faster' if speedup > 1 else 'slower'}")
    
    # Recommendation
    print("\n" + "=" * 60)
    print("RECOMMENDATION")
    print("=" * 60)
    
    if typst_success >= latex_success and typst_time < latex_time:
        print("\n✓ TYPST is recommended:")
        print("  - Equal or better success rate")
        print("  - Faster compilation")
        print("  - Simpler syntax (closer to Markdown)")
        print("  - Native Unicode/Cyrillic support")
        print("  - Single binary, no TeX Live required")
    elif latex_success > typst_success:
        print("\n✓ LATEX is recommended:")
        print("  - Higher success rate")
        print("  - More mature ecosystem")
        print("  - Better handling of complex documents")
    else:
        print("\n⚠ Both have trade-offs:")
        print(f"  - LaTeX: {latex_success}/{total} success, {latex_time:.2f}s")
        print(f"  - Typst: {typst_success}/{total} success, {typst_time:.2f}s")
    
    return results


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    run_ab_test()

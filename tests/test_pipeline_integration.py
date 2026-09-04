import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from datetime import datetime
from pipeline.translator import TranslationOrchestrator
from integrations.openai_client import TranslationResult
from pipeline.arxiv_fetcher import ArxivArticle

def test_full_pipeline_flow(tmp_path):
    # Create a fake article structure
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    main_tex = source_dir / "main.tex"
    main_tex.write_text(r"""
    \documentclass{article}
    \begin{document}
    Hello World.
    $E=mc^2$
    \end{document}
    """, encoding='utf-8')
    
    # Mock dependencies
    with patch('pipeline.translator.ArxivFetcher') as MockFetcher, \
         patch('pipeline.translator.OpenAIClient') as MockClient:
         
        # Setup Fetcher
        mock_fetcher = MockFetcher.return_value
        stamp_date = datetime(2023, 8, 2)
        mock_fetcher.fetch_article.return_value = ArxivArticle(
            arxiv_id="1234.5678",
            title="Test",
            authors=["Me"],
            abstract="Abstract",
            published_date=stamp_date,
            categories=["cs.CL"],
            main_tex_path=main_tex,
            source_directory=source_dir,
            updated_date=stamp_date,
            version="v2",
            primary_category="cs.CL"
        )
        
        # Setup Client
        mock_client = MockClient.return_value
        
        # Mock translation to echo input but change text
        def echo_translate(text, **kwargs):
            # Simple mock translation: replace "Hello" with "Privet"
            # But keep tokens intact
            translated = text.replace("Hello", "Privet")
            return TranslationResult(
                translated_content=translated,
                input_tokens=10,
                output_tokens=10,
                cost_usd=0.001,
                model="gpt-4o-mini",
                metadata={}
            )
            
        mock_client.translate_latex.side_effect = echo_translate
        
        # Run Orchestrator
        orchestrator = TranslationOrchestrator()
        success = orchestrator.translate_article("1234.5678")
        
        assert success
        
        # Check output
        output_file = source_dir / "translated.tex"
        assert output_file.exists()
        content = output_file.read_text(encoding='utf-8')
        
        # Verify translation happened
        assert "Privet World" in content
        # Verify formula restored
        assert "$E=mc^2$" in content
        # Verify arXiv stamp injected
        assert "arXiv:1234.5678v2 [cs.CL] 2 Aug 2023" in content
        assert r"\usepackage{eso-pic}" in content

#!/usr/bin/env python3
"""
Rosetta v2 - Main entry point for translation pipeline.

Usage:
    python3 rosetta.py <arxiv_id_or_url>
    
Examples:
    python3 rosetta.py 1706.03762
    python3 rosetta.py https://arxiv.org/abs/1706.03762
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from utils.logger import get_logger
from config import Config

logger = get_logger(__name__)


def main():
    """Main entry point for Rosetta v2."""
    if len(sys.argv) < 2:
        print(__doc__)
        print("\nError: Please provide an arXiv ID or URL")
        print("Example: python3 rosetta.py 1706.03762")
        sys.exit(1)
    
    arxiv_input = sys.argv[1]
    
    # Validate configuration (warn but don't fail for testing)
    # For ArxivFetcher test, we don't need OpenAI API key
    try:
        Config.validate()
    except Exception as e:
        logger.warning(f"Configuration warning: {e}")
        logger.warning("Continuing with ArxivFetcher test (OpenAI API key not required)")
    
    logger.info("=" * 70)
    logger.info("Rosetta v2 - Translation Pipeline")
    logger.info("=" * 70)
    logger.info(f"Input: {arxiv_input}")
    
    try:
        # For now, just test ArxivFetcher
        # TODO: Implement full pipeline when other modules are ready
        from pipeline.arxiv_fetcher import ArxivFetcher
        
        logger.info("\n[1/1] Fetching article from arXiv...")
        fetcher = ArxivFetcher()
        article = fetcher.fetch_article(arxiv_input)
        
        logger.info("\n" + "=" * 70)
        logger.info("✅ Article fetched successfully!")
        logger.info("=" * 70)
        logger.info(f"Title: {article.title}")
        logger.info(f"Authors: {', '.join(article.authors[:3])}{'...' if len(article.authors) > 3 else ''}")
        logger.info(f"Published: {article.published_date.strftime('%Y-%m-%d')}")
        logger.info(f"Categories: {', '.join(article.categories)}")
        logger.info(f"Main .tex file: {article.main_tex_path}")
        logger.info(f"Source directory: {article.source_directory}")
        if article.original_pdf_path:
            logger.info(f"Original PDF: {article.original_pdf_path}")
        
        logger.info("\n" + "=" * 70)
        logger.info("Note: Full translation pipeline not yet implemented.")
        logger.info("This was a test of ArxivFetcher module only.")
        logger.info("=" * 70)
        
    except Exception as e:
        logger.error(f"\n❌ Error: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()


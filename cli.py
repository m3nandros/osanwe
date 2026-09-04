"""
Command Line Interface for Rosetta v3.
"""

import argparse
import sys
from pathlib import Path

from pipeline.translator import TranslationOrchestrator
from utils.logger import get_logger, configure_logging

def main():
    # Backwards-compatible routing:
    # - `cli.py <arxiv_id_or_url> [--output ...] [--verbose]` keeps working
    # - `cli.py pdf ...` uses the optional PDF reconstruction pipeline
    if len(sys.argv) >= 2 and sys.argv[1] == "pdf":
        from pipeline.pdf_reconstruct.cli import pdf_main

        return_code = pdf_main(sys.argv[2:])
        sys.exit(return_code)

    parser = argparse.ArgumentParser(
        description="Rosetta v3: Automated Scientific Article Translator"
    )

    parser.add_argument(
        "arxiv_id",
        help="arXiv ID or URL of the article to translate (e.g., 1706.03762)"
    )

    parser.add_argument(
        "--output", "-o",
        help="Output directory (optional)",
        type=Path
    )

    parser.add_argument(
        "--verbose", "-v",
        help="Enable verbose logging",
        action="store_true"
    )

    parser.add_argument(
        "--lang",
        help="Target language (default: ru)",
        default="ru",
    )

    args = parser.parse_args()

    # Setup logging
    configure_logging(verbose=args.verbose)
    logger = get_logger(__name__)

    logger.info(f"Starting translation for arXiv ID: {args.arxiv_id}")

    # Initialize Orchestrator
    orchestrator = TranslationOrchestrator()

    # Run translation
    success = orchestrator.translate_article(args.arxiv_id, args.output, target_lang=str(args.lang))

    if success:
        logger.info("Translation completed successfully!")
        sys.exit(0)
    else:
        logger.error("Translation failed.")
        sys.exit(1)

if __name__ == "__main__":
    main()

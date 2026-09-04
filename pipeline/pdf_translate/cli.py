"""
CLI interface for PDF Translation Pipeline.
"""

import argparse
import logging
import sys
from pathlib import Path

from .pipeline import PDFTranslationPipeline, PipelineConfig


def setup_logging(verbose: bool = False):
    """Setup logging configuration."""
    level = logging.DEBUG if verbose else logging.INFO
    
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S"
    )
    
    # Reduce noise from external libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)


def create_translator_func():
    """
    Create translator function using the existing pipeline translator.
    
    Returns None if translator is not available (for testing without translation).
    """
    try:
        # Try to import the existing translator
        from pipeline.translator import translate_text
        
        def translator_func(text: str, source_lang: str, target_lang: str) -> str:
            return translate_text(
                text,
                source_language=source_lang,
                target_language=target_lang
            )
        
        return translator_func
    except ImportError:
        return None


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Translate PDF scientific papers while preserving layout",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s paper.pdf
  %(prog)s paper.pdf -o paper_translated.pdf -l ru
  %(prog)s paper.pdf --debug --verbose
        """
    )
    
    parser.add_argument(
        "input_pdf",
        help="Path to input PDF file"
    )
    
    parser.add_argument(
        "-o", "--output",
        help="Path to output PDF file (default: input_<lang>.pdf)"
    )
    
    parser.add_argument(
        "-l", "--language",
        default="ru",
        choices=["ru", "zh", "ja", "ko", "de", "fr", "es"],
        help="Target language code (default: ru)"
    )
    
    parser.add_argument(
        "--source-lang",
        default="en",
        help="Source language code (default: en)"
    )
    
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Save intermediate files for debugging"
    )
    
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )
    
    parser.add_argument(
        "--no-translate",
        action="store_true",
        help="Skip translation (for testing layout extraction)"
    )
    
    parser.add_argument(
        "--engine",
        default="xelatex",
        choices=["xelatex", "lualatex"],
        help="LaTeX engine to use (default: xelatex)"
    )
    
    parser.add_argument(
        "--dpi",
        type=int,
        default=150,
        help="DPI for layout detection (default: 150)"
    )
    
    parser.add_argument(
        "--confidence",
        type=float,
        default=0.25,
        help="Confidence threshold for detection (default: 0.25)"
    )
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)
    
    # Check input file
    input_path = Path(args.input_pdf)
    if not input_path.exists():
        logger.error(f"Input file not found: {input_path}")
        sys.exit(1)
    
    # Create configuration
    config = PipelineConfig(
        source_language=args.source_lang,
        target_language=args.language,
        save_debug_files=args.debug,
        latex_engine=args.engine,
        detection_dpi=args.dpi,
        detection_confidence=args.confidence,
    )
    
    # Create translator function
    translator_func = None
    if not args.no_translate:
        translator_func = create_translator_func()
        if translator_func is None:
            logger.warning(
                "Translator not available. Running without translation. "
                "Use --no-translate to suppress this warning."
            )
    
    # Create and run pipeline
    pipeline = PDFTranslationPipeline(
        config=config,
        translator_func=translator_func
    )
    
    result = pipeline.process(
        input_pdf=str(input_path),
        output_pdf=args.output
    )
    
    # Report results
    if result.success:
        print(f"\n✓ Success! Output: {result.output_path}")
        print(f"\nStatistics:")
        for key, value in result.stats.items():
            print(f"  {key}: {value}")
    else:
        print(f"\n✗ Error: {result.error_message}")
        sys.exit(1)


if __name__ == "__main__":
    main()

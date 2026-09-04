#!/usr/bin/env python3
"""
Test script for PDF Translation Pipeline.

Usage:
    python scripts/test_pdf_translate.py out_dir/1706.03762/original.pdf --no-translate --debug
"""

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.pdf_translate.pipeline import PDFTranslationPipeline, PipelineConfig


def main():
    parser = argparse.ArgumentParser(description="Test PDF Translation Pipeline")
    parser.add_argument("pdf_path", help="Path to PDF file")
    parser.add_argument("-o", "--output", help="Output path")
    parser.add_argument("-l", "--lang", default="ru", help="Target language")
    parser.add_argument("--no-translate", action="store_true", help="Skip translation")
    parser.add_argument("--debug", action="store_true", help="Save debug files")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()
    
    # Setup logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S"
    )
    
    # Create config
    config = PipelineConfig(
        target_language=args.lang,
        save_debug_files=args.debug,
    )
    
    # Create translator function (mock if --no-translate)
    translator_func = None
    if not args.no_translate:
        try:
            from pipeline.translator import translate_text
            def translator_func(text, src, tgt):
                return translate_text(text, source_language=src, target_language=tgt)
        except ImportError:
            print("Warning: translator not available, using no translation")
    
    # Run pipeline
    pipeline = PDFTranslationPipeline(config=config, translator_func=translator_func)
    
    result = pipeline.process(
        input_pdf=args.pdf_path,
        output_pdf=args.output
    )
    
    # Print results
    print("\n" + "="*60)
    if result.success:
        print(f"✓ SUCCESS: {result.output_path}")
    else:
        print(f"✗ FAILED: {result.error_message}")
    
    print("\nStatistics:")
    for key, value in result.stats.items():
        print(f"  {key}: {value}")
    
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())

import shutil
import os
from pathlib import Path
from pipeline.arxiv_fetcher import ArxivFetcher
from pipeline.pdf_compiler import PDFCompiler
from utils.logger import get_logger

logger = get_logger("debug_restore")

def restore_and_compile(article_id: str):
    work_dir = Path(f"temp/{article_id}")
    source_dir = work_dir / "source"
    tex_file = source_dir / "translated.tex"
    backup_file = work_dir / "translated.tex.bak"
    
    # Backup translated.tex if it exists
    if tex_file.exists():
        print(f"Backing up {tex_file}...")
        shutil.copy(tex_file, backup_file)
    else:
        print("No translated.tex found to backup!")
        return

    print("Fetching original source files...")
    fetcher = ArxivFetcher()
    # This might overwrite source dir, which is what we want (except translated.tex)
    fetcher.fetch_article(article_id)
    
    # Restore translated.tex
    if backup_file.exists():
        print(f"Restoring {tex_file}...")
        shutil.copy(backup_file, tex_file)
        
    # Compile
    print("Compiling PDF...")
    compiler = PDFCompiler()
    result = compiler.compile_pdf(tex_file, output_dir=source_dir)
    
    if result.success:
        print(f"SUCCESS: PDF compiled at {result.pdf_path}")
    else:
        print("FAILURE: PDF compilation failed.")
        log_file = source_dir / "translated.log"
        if log_file.exists():
             print("Log tail:")
             print(log_file.read_text(encoding='latin-1', errors='replace')[-500:])

if __name__ == "__main__":
    restore_and_compile("1706.03762")



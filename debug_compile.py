import sys
from pathlib import Path
from pipeline.pdf_compiler import PDFCompiler
from utils.logger import get_logger

logger = get_logger("debug_compile")

def compile_only(article_id: str):
    work_dir = Path(f"temp/{article_id}")
    source_dir = work_dir / "source"
    tex_file = source_dir / "translated.tex"
    
    if not tex_file.exists():
        print(f"File not found: {tex_file}")
        return

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
    if len(sys.argv) > 1:
        compile_only(sys.argv[1])
    else:
        compile_only("1706.03762")



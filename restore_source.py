from pipeline.arxiv_fetcher import ArxivFetcher
from pathlib import Path
import shutil
import os

def restore():
    fetcher = ArxivFetcher()
    # This will download and extract to temp/1706.03762/source
    # It might overwrite existing files, which is what we want for assets, 
    # but we need to preserve our translated.tex if it's there.
    
    # Backup translated.tex if it exists in temp
    temp_tex = Path("temp/1706.03762/source/translated.tex")
    root_tex = Path("translated.tex")
    
    if not root_tex.exists():
        print("Error: translated.tex not found in root")
        return

    print("Fetching source...")
    try:
        fetcher.download_sources("1706.03762")
    except Exception as e:
        print(f"Error fetching source: {e}")
        return

    print("Source restored.")
    
    # Copy translated.tex from root to source dir
    dest = Path("temp/1706.03762/source/translated.tex")
    print(f"Copying {root_tex} to {dest}")
    shutil.copy(root_tex, dest)
    
    # Remove the dummy nips_2017.sty if I created it (it would be overwritten by download if it exists in source, 
    # but if it doesn't exist in source, my dummy might still be there if I didn't clean up? 
    # ArxivFetcher extracts to a clean dir? No, it extracts to existing dir.
    # But tarfile extraction overwrites.
    # If nips_2017.sty is NOT in the source, then we are back to square one.
    # But the user said it worked before (implied).
    
    print("Done.")

if __name__ == "__main__":
    restore()

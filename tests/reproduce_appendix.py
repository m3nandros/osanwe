import sys
from pathlib import Path
import re

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from pipeline.splitter import ContentSplitter

def test_appendix_splitting():
    splitter = ContentSplitter()
    
    # Case 1: Standard formatting
    latex_content = r"""
\documentclass{article}
\begin{document}
\section{Introduction}
Some text.

\begin{thebibliography}{9}
\bibitem{lamport94}
Leslie Lamport, \emph{\LaTeX: A Document Preparation System}.
\end{thebibliography}

\appendix
\section{My Appendix}
This should be translated.
\end{document}
"""
    
    print("--- Test Case 1: Standard Formatting ---")
    chunks = splitter.split_content(latex_content)
    
    bib_chunk = next((c for c in chunks if c.type == 'bib'), None)
    appendix_chunk = next((c for c in chunks if 'My Appendix' in c.text), None)
    
    if bib_chunk:
        print(f"Bibliography Chunk Text:\n---\n{bib_chunk.text}\n---")
        if "My Appendix" in bib_chunk.text:
            print("FAIL: Appendix found inside Bibliography chunk!")
        else:
            print("PASS: Appendix NOT in Bibliography chunk.")
    else:
        print("FAIL: No Bibliography chunk found.")
        
    if appendix_chunk:
        print(f"Appendix Chunk Type: {appendix_chunk.type}")
        if appendix_chunk.type == 'bib':
             print("FAIL: Appendix chunk marked as 'bib'!")
        else:
             print("PASS: Appendix chunk found and not 'bib'.")
    else:
        print("FAIL: Appendix chunk NOT found (might be merged into bib).")

    if bib_chunk and appendix_chunk:
        if bib_chunk.order < appendix_chunk.order:
            print("PASS: Bibliography appears BEFORE Appendix.")
        else:
            print(f"FAIL: Bibliography order ({bib_chunk.order}) is >= Appendix order ({appendix_chunk.order})!")

    # Case 2: Whitespace in \end command
    latex_content_loose = r"""
\documentclass{article}
\begin{document}
\section{Introduction}
Some text.

\begin{thebibliography}{9}
\bibitem{lamport94}
Leslie Lamport.
\end {thebibliography}

\appendix
\section{Loose Appendix}
This should be translated.
\end{document}
"""
    print("\n--- Test Case 2: Loose Formatting (\end {thebibliography}) ---")
    chunks = splitter.split_content(latex_content_loose)
    
    bib_chunk = next((c for c in chunks if c.type == 'bib'), None)
    
    if bib_chunk and "Loose Appendix" in bib_chunk.text:
        print("FAIL: Appendix found inside Bibliography chunk with loose formatting!")
    else:
        print("PASS: Appendix correctly separated with loose formatting.")

if __name__ == "__main__":
    test_appendix_splitting()

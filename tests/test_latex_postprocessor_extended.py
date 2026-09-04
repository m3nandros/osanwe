import pytest
from pipeline.latex_postprocessor import post_process_latex, _fix_malformed_commands

def test_fix_malformed_usepackage():
    text = r"""
\documentclass{article}
\usepackage{subfiles>
\usepackage{graphicx}
\begin{document}
Hello
\end{document}
"""
    fixed = post_process_latex(text)
    assert r"\usepackage{subfiles}" in fixed
    assert r"\usepackage{subfiles>" not in fixed

def test_deduplicate_usepackage():
    text = r"""
\documentclass{article}
\usepackage{subfiles}
\usepackage{ subfiles }
\usepackage{graphicx}
\usepackage{graphicx} % duplicate
\begin{document}
\usepackage{subfiles} % Should stay, inside document
Hello
\end{document}
"""
    # Note: post_process_latex includes all fixers.
    fixed = post_process_latex(text)
    
    # We expect only one occurrence of each in preamble
    preamble = fixed.split(r"\begin{document}")[0]
    
    # Count occurrences in preamble
    assert preamble.count(r"\usepackage{subfiles}") == 1
    assert preamble.count(r"\usepackage{graphicx}") == 1
    
    # The one in document should remain (if it was there)
    # Wait, does my deduplication logic preserve body content correctly?
    # Yes, it iterates lines and only deduplicates if `in_preamble` is True.
    body = fixed.split(r"\begin{document}")[1]
    assert r"\usepackage{subfiles}" in body

def test_fix_malformed_environments():
    text = r"""
\begin{figure}>
Content
\end{figure}>
"""
    fixed = post_process_latex(text)
    # post_process_latex also adds [ht] to figures if missing, so we expect \begin{figure}[ht]
    assert r"\begin{figure}[ht]" in fixed or r"\begin{figure}" in fixed
    assert r"\end{figure}" in fixed
    # Ensure > is gone from commands
    assert r"\begin{figure}>" not in fixed
    assert r"\end{figure}>" not in fixed


"""
LaTeX Post-processor module for Rosetta v3.

Responsible for deterministic, regex-based cleaning and fixing of translated LaTeX content.
Fixes common GPT errors such as:
- Remaining tokens (<<...>>) that weren't restored and cause LaTeX errors
- Broken commands (e.g. \\ text instead of \\text)
- Spacing issues
- Hallucinated Russian commands
- Encoding artifacts
"""

import os
import re
import unicodedata
from typing import List, Optional, Tuple

from utils.logger import get_logger

logger = get_logger(__name__)

def post_process_latex(content: str, target_lang: str = "ru") -> str:
    """
    Apply a series of regex-based fixes to the translated LaTeX content.
    
    Args:
        content: The raw translated LaTeX string
        
    Returns:
        Cleaned and fixed LaTeX string
    """
    logger.info("Starting LaTeX post-processing...")
    original_len = len(content)
    
    fixed_content = content

    normalized_target_lang = str(target_lang or "ru").strip().lower() or "ru"
    is_rtl = normalized_target_lang in ("ar", "he")
    is_ja = normalized_target_lang in ("ja", "jp", "jpn")
    
    # List of fixers to apply in order
    fixers = [
        _fix_malformed_commands, # Fix structural issues first
        _fix_spurious_documentclass_lines,  # Remove/neutralize broken template lines like "\\documentclass command."
        _fix_command_conflicts,  # Fix command redefinition conflicts (e.g., \\C)
        _fix_fontenc_conflicts,  # Fix T1/T2A fontenc conflicts
        _fix_times_font_conflicts,
        _drop_bilingual_duplicate_paragraphs_for_cjk,
        _drop_bilingual_english_duplicates_for_cjk,
        _tune_maketitle_thanks_layout,
        _fix_acmart_title_bold,
        _fix_acmart_affiliation_countries,
        _fix_acmart_linebreaking,
        _fix_package_order,      # Fix package order (fontenc before babel)
        _fix_babel_conflicts,    # Fix babel option clashes
        _fix_sectsty_conflicts,  # Remove sectsty for incompatible document classes
        _fix_cjk_conflicts,      # Fix CJK package issues with pdfLaTeX
        _remove_remaining_tokens,  # Remove any leftover tokens that weren't restored
        _fix_minted_environments,  # Fix minted environments
        _normalize_float_environments,  # Fix figure/table environments and graphics usage
        _fix_multicol_includegraphics,
        _fix_broken_includegraphics_placeholders,
        _fix_llama_macros,
        _normalize_table_widths,  # Convert long-text tables to tabularx with wrapping
        _fix_broken_commands,
        _fix_spacing_around_math,
        _fix_russian_babel_artifacts,
        _fix_common_typos,
        # _add_section_bold_formatting,  # Disabled: using sectsty package instead
        (lambda x, _tl=normalized_target_lang: _translate_abstract_heading(x, target_lang=_tl)),
        _fix_abstract_page_break,      # Prevent abstract from moving to next page
        _remove_orphaned_bibliography_blocks,
        _fix_bibliography_spacing,     # Fix bibliography spacing issues
        _fix_nobibliography_command,   # Remove \\nobibliography* that suppresses bibliography output
        _fix_combining_diacritics,     # Remove combining diacritics that cause pdfLaTeX errors
        _disable_arxiv_stamp_for_rtl if is_rtl else (lambda x: x),
        _wrap_latin_tokens_in_title_author_for_rtl if is_rtl else (lambda x: x),
        _wrap_latin_tokens_for_rtl if is_rtl else (lambda x: x),
        _tune_japanese_line_spacing if is_ja else (lambda x: x),
        _fix_duplicate_end_document,   # Remove duplicate \\end{document} (keep only last one)
    ]

    if normalized_target_lang != "ru":
        fixers = [
            f
            for f in fixers
            if f not in (
                _fix_russian_babel_artifacts,
            )
        ]
    
    for fixer in fixers:
        fixed_content = fixer(fixed_content)
        
    logger.info(f"Post-processing complete. Length change: {len(fixed_content) - original_len}")
    return fixed_content


def _disable_arxiv_stamp_for_rtl(text: str) -> str:
    # For RTL languages, keep the arXiv stamp but move it to the physical LEFT side
    # and render it in LTR so it doesn't get mirrored.
    lines = text.splitlines(keepends=False)
    out: list[str] = []
    changed = False
    in_stamp_macro = False

    for line in lines:
        stripped = line.lstrip()

        if stripped.startswith(r"\newcommand{\ArxivStamp}"):
            in_stamp_macro = True

        if in_stamp_macro:
            # Cleanup: previous runs might have inserted \begin{LTR}/\end{LTR} lines here.
            # They break macro grouping and are not needed if we wrap only the payload with \LR{...}.
            if stripped in (r"\begin{LTR}", r"\end{LTR}"):
                changed = True
                continue

            # In RTL documents, shipout coordinates often end up mirrored.
            # To keep the stamp on the physical LEFT (as in the original/LTR PDFs),
            # anchor to the logical RIGHT edge.
            if r"\AtPageLowerLeft" in line:
                out.append(line.replace(r"\AtPageLowerLeft", r"\AtPageLowerRight"))
                changed = True
                continue
            if r"\AtPageLowerRight" in line:
                out.append(line)
                changed = True
                continue

            # If a previous run inserted a broken double-backslash wrapper, fix it.
            if r"\\LR{" in line:
                out.append(line.replace(r"\\LR{", r"\LR{"))
                changed = True
                continue
            # Force LTR rendering for the stamp payload.
            if " #1" in line and "rotatebox" in line and r"\LR{" not in line:
                out.append(line.replace(" #1", r" \LR{#1}"))
                changed = True
                continue

            out.append(line)

            if stripped.startswith("}"):
                # Macro definition ends with a standalone '}' line in our generated template.
                # This is safe enough for this specific artifact.
                in_stamp_macro = False
            continue

        # Ensure the invocation is not commented out.
        if stripped.startswith("%") and stripped.lstrip("% ").startswith(r"\ArxivStamp{"):
            out.append(stripped.lstrip("% "))
            changed = True
            continue

        out.append(line)

    return "\n".join(out) if changed else text


def _tune_japanese_line_spacing(text: str) -> str:
    try:
        head_m = re.search(r"\\begin\{document\}", text)
        if not head_m:
            return text
        head = text[: head_m.start()]
        tail = text[head_m.start() :]
        if re.search(r"\\documentclass\s*\{beamer\}", head):
            return text
        if re.search(r"\\(?:linespread|setstretch)\b|baselinestretch|\\usepackage\{setspace\}", head):
            return text
        factor_env = (os.environ.get("ROSETTA_JA_LINE_SPREAD", "") or "").strip()
        try:
            factor = float(factor_env) if factor_env else 1.08
        except Exception:
            factor = 1.08
        if factor <= 1.0:
            return text
        ins = f"\\linespread{{{factor:.2f}}}\n"
        return head + ins + tail
    except Exception:
        return text


def _wrap_latin_tokens_for_rtl(text: str) -> str:
    # Prevent Latin/ASCII tokens from being mirrored/reordered in RTL.
    # We do NOT change bibliography content.
    if not re.search(r"[\u0590-\u05FF\u0600-\u06FF]", text):
        return text

    # Only process the document body. Touching preamble is risky.
    m = re.search(r"\\begin\{document\}", text)
    if not m:
        return text
    head = text[: m.end()]
    body = text[m.end() :]

    # 1) Global sanitation: remove simple non-nested \LR{...}/\RL{...} wrappers
    # that might have been inserted by previous runs and could break LaTeX code lines.
    # We only unwrap the simple form (no nested braces) to stay safe.
    body = re.sub(r"\\LR\{([^{}]*)\}", r"\1", body)
    body = re.sub(r"\\RL\{([^{}]*)\}", r"\1", body)
    # Also unwrap the broken double-backslash variant (\\LR{...}) that renders as a linebreak
    # followed by literal letters and often appears mirrored in RTL PDFs.
    body = re.sub(r"\\\\LR\{([^{}]*)\}", r"\1", body)
    body = re.sub(r"\\\\RL\{([^{}]*)\}", r"\1", body)

    # 2) Protect environments we must never touch.
    protected_env_rx = re.compile(
        r"(\\begin\{thebibliography\}.*?\\end\{thebibliography\}"
        r"|\\begin\{tabular\*?\}.*?\\end\{tabular\*?\}"
        r"|\\begin\{tabularx\}.*?\\end\{tabularx\}"
        r"|\\begin\{array\}.*?\\end\{array\}"
        r"|\\begin\{longtable\}.*?\\end\{longtable\}"
        r"|\\begin\{align\*?\}.*?\\end\{align\*?\}"
        r"|\\begin\{flalign\*?\}.*?\\end\{flalign\*?\}"
        r"|\\begin\{alignat\*?\}.*?\\end\{alignat\*?\}"
        r"|\\begin\{equation\*?\}.*?\\end\{equation\*?\}"
        r"|\\begin\{gather\*?\}.*?\\end\{gather\*?\}"
        r"|\\begin\{multline\*?\}.*?\\end\{multline\*?\})",
        flags=re.DOTALL,
    )
    parts = protected_env_rx.split(body)

    protected: list[str] = []
    replacements: list[str] = []

    def _protect(pattern: str, s: str, flags: int = 0) -> str:
        nonlocal protected, replacements
        rx = re.compile(pattern, flags)

        def _sub(m: re.Match) -> str:
            idx = len(replacements)
            # IMPORTANT: avoid <<...>> because _remove_remaining_tokens will delete them on re-run.
            token = f"ROSETTA_PROTECT_TOKEN_{idx}__"
            replacements.append(m.group(0))
            protected.append(token)
            return token

        return rx.sub(_sub, s)

    def _restore(s: str) -> str:
        for token, original in zip(protected, replacements):
            s = s.replace(token, original)
        return s

    def _process_chunk(chunk: str) -> str:
        # Only wrap in prose-like lines (not starting with backslash).
        lines = chunk.splitlines(keepends=True)
        out_lines: list[str] = []
        changed_local = False

        dangerous_markers = (
            r"\\setlength",
            r"\\addtolength",
            r"\\newcommand",
            r"\\renewcommand",
            r"\\providecommand",
            r"\\def",
            r"\\let",
            r"\\Declare",
            r"\\usepackage",
            r"\\begin{",
            r"\\end{",
            r"\\includegraphics",
            r"\\bibitem",
            r"\\bibliography",
            r"\\AtBeginDocument",
        )

        for line in lines:
            if line.lstrip().startswith("%"):
                out_lines.append(line)
                continue
            # Only if RTL script is present.
            if not re.search(r"[\u0590-\u05FF\u0600-\u06FF]", line):
                out_lines.append(line)
                continue
            # Do not touch structural/definition/layout lines.
            if any(m in line for m in dangerous_markers):
                out_lines.append(line)
                continue

            tmp = line
            tmp = _protect(r"\\\[.*?\\\]", tmp, flags=re.DOTALL)
            tmp = _protect(r"\$[^$]*\$", tmp, flags=0)
            tmp = _protect(r"\\(?:cite|citet|citep|citealp|citeauthor|Cite|Citet|Citep)\s*\{[^}]*\}", tmp)
            tmp = _protect(r"\\(?:ref|eqref|autoref|pageref|label)\s*\{[^}]*\}", tmp)
            tmp = _protect(r"\\url\s*\{[^}]*\}", tmp)
            tmp = _protect(r"\\href\s*\{[^}]*\}\s*\{[^}]*\}", tmp)
            tmp = _protect(r"\\[A-Za-z@]+\*?", tmp)

            latin_rx = re.compile(r"\b[A-Za-z][A-Za-z0-9@._:+\-/]*\b")

            def _wrap(m: re.Match) -> str:
                s = m.group(0)
                if s.startswith("ROSETTA_PROTECT_TOKEN_"):
                    return s
                return r"\LR{" + s + r"}"

            wrapped = latin_rx.sub(_wrap, tmp)
            wrapped = _restore(wrapped)
            if wrapped != line:
                changed_local = True
            out_lines.append(wrapped)

        return "".join(out_lines) if changed_local else chunk

    out_parts: list[str] = []
    changed = False
    for p in parts:
        if not p:
            continue
        if protected_env_rx.fullmatch(p):
            out_parts.append(p)
            continue
        new_p = _process_chunk(p)
        changed = changed or (new_p != p)
        out_parts.append(new_p)

    new_body = "".join(out_parts)
    if not changed and new_body == body:
        return text
    return head + new_body


def _wrap_latin_tokens_in_title_author_for_rtl(text: str) -> str:
    # Fix mirrored Latin in RTL within preamble title/author blocks (including \thanks{} text).
    # We keep the scope narrow to avoid touching arbitrary TeX.
    if not re.search(r"[\u0590-\u05FF\u0600-\u06FF]", text):
        return text

    m = re.search(r"\\begin\{document\}", text)
    if not m:
        return text
    preamble = text[: m.start()]
    rest = text[m.start() :]

    def _find_brace_block(s: str, open_pos: int) -> Optional[Tuple[int, int]]:
        # open_pos points to the '{' that starts the block.
        depth = 0
        i = open_pos
        while i < len(s):
            ch = s[i]
            prev = s[i - 1] if i > 0 else ""
            if ch == "{" and prev != "\\":
                depth += 1
            elif ch == "}" and prev != "\\":
                depth -= 1
                if depth == 0:
                    return (open_pos, i)
            i += 1
        return None

    def _wrap_latin_in_fragment(fragment: str) -> str:
        # Protect fragile constructs we don't want to alter.
        protected: list[str] = []
        replacements: list[str] = []

        # Avoid nested wrappers if the file was processed before.
        fragment = re.sub(r"\\LR\{([^{}]*)\}", r"\1", fragment)
        fragment = re.sub(r"\\RL\{([^{}]*)\}", r"\1", fragment)

        def _protect(pattern: str, s: str, flags: int = 0) -> str:
            rx = re.compile(pattern, flags)

            def _sub(mm: re.Match) -> str:
                idx = len(replacements)
                # IMPORTANT: keep a leading backslash so this placeholder cannot be
                # concatenated into a Latin word token (e.g. 'Vaswani\thanks').
                token = f"\\ROSETTA_TITLEAUTH_PROTECT_{idx}__"
                replacements.append(mm.group(0))
                protected.append(token)
                return token

            return rx.sub(_sub, s)

        def _restore(s: str) -> str:
            for token, original in zip(protected, replacements):
                s = s.replace(token, original)
            return s

        tmp = fragment
        # Do not touch monospace emails/urls; they already render LTR and are fragile.
        tmp = _protect(r"\\texttt\s*\{[^}]*\}", tmp)
        tmp = _protect(r"\\url\s*\{[^}]*\}", tmp)
        tmp = _protect(r"\\href\s*\{[^}]*\}\s*\{[^}]*\}", tmp)
        # Protect inline math.
        tmp = _protect(r"\$[^$]*\$", tmp)
        tmp = _protect(r"\\\[.*?\\\]", tmp, flags=re.DOTALL)
        # Protect command names (args remain). Do NOT treat our internal placeholders
        # as LaTeX commands, otherwise they won't restore.
        tmp = _protect(r"\\(?!ROSETTA_TITLEAUTH_PROTECT_)[A-Za-z@]+\*?", tmp)

        latin_rx = re.compile(r"\b[A-Za-z][A-Za-z0-9@._:+\-/]*\b")

        def _wrap(mm: re.Match) -> str:
            s = mm.group(0)
            if s.startswith("ROSETTA_TITLEAUTH_PROTECT_"):
                return s
            return r"\LR{" + s + r"}"

        wrapped = latin_rx.sub(_wrap, tmp)
        wrapped = _restore(wrapped)
        # Safety: restore should remove all placeholders.
        wrapped = re.sub(r"\\ROSETTA_TITLEAUTH_PROTECT_\d+__", "", wrapped)
        return wrapped

    changed = False
    for cmd in (r"\title", r"\author"):
        search_from = 0
        while True:
            idx = preamble.find(cmd, search_from)
            if idx == -1:
                break
            # Find first '{' after cmd (skip spaces/newlines).
            j = idx + len(cmd)
            while j < len(preamble) and preamble[j].isspace():
                j += 1
            if j >= len(preamble) or preamble[j] != "{":
                search_from = idx + len(cmd)
                continue
            block = _find_brace_block(preamble, j)
            if not block:
                break
            a, b = block
            inner = preamble[a + 1 : b]
            new_inner = _wrap_latin_in_fragment(inner)
            if new_inner != inner:
                preamble = preamble[: a + 1] + new_inner + preamble[b:]
                changed = True
                search_from = a + 1 + len(new_inner)
            else:
                search_from = b + 1

    return (preamble + rest) if changed else text


def _drop_bilingual_english_duplicates_for_cjk(text: str) -> str:
    # Only apply for target languages where we expect non-ASCII scripts and bilingual duplication is common.
    # This is content-preserving: we drop an English line only when the next line appears to be its translation.
    if not re.search(r"[ぁ-ゟ゠-ヿ一-龯]", text):
        return text

    lines = text.splitlines(keepends=False)
    if len(lines) < 2:
        return text

    def english_word_count(s: str) -> int:
        return len(re.findall(r"\b[A-Za-z]{3,}\b", s))

    def has_cjk(s: str) -> bool:
        return bool(re.search(r"[ぁ-ゟ゠-ヿ一-龯]", s))

    cmd_rx = re.compile(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?(?:\{[^{}]*\})?")
    math_rx = re.compile(r"\$[^$]*\$")
    num_rx = re.compile(r"\b\d+(?:\.\d+)?\b")

    def sig_tokens(s: str) -> set[str]:
        toks: set[str] = set()
        for m in cmd_rx.finditer(s):
            toks.add(m.group(0))
        for m in math_rx.finditer(s):
            toks.add(m.group(0))
        for m in num_rx.finditer(s):
            toks.add(m.group(0))
        # Also keep common reference patterns even if not caught above.
        for m in re.finditer(r"Figure~\\ref\{[^}]+\}|Table~\\ref\{[^}]+\}|Section~\\ref\{[^}]+\}", s):
            toks.add(m.group(0))
        return toks

    def jaccard(a: set[str], b: set[str]) -> float:
        if not a and not b:
            return 0.0
        inter = len(a & b)
        union = len(a | b)
        return inter / union if union else 0.0

    out: list[str] = []
    i = 0
    changed = False
    while i < len(lines):
        cur = lines[i]
        if i + 1 < len(lines):
            nxt = lines[i + 1]
            # Skip comments-only lines.
            if cur.lstrip().startswith('%'):
                out.append(cur)
                i += 1
                continue
            if english_word_count(cur) >= 6 and has_cjk(nxt):
                a = sig_tokens(cur)
                b = sig_tokens(nxt)
                # Require some shared structure (math/refs/commands) to avoid accidental deletions.
                if (len(a) >= 2 or len(b) >= 2) and jaccard(a, b) >= 0.55:
                    # Drop the English duplicate line, keep the translated next line.
                    changed = True
                    i += 1
                    continue
        out.append(cur)
        i += 1

    return "\n".join(out) if changed else text


def _drop_bilingual_duplicate_paragraphs_for_cjk(text: str) -> str:
    # Paragraph-level version of bilingual dedup.
    # Handles the common case where an English paragraph is wrapped across multiple lines,
    # then followed by its translated CJK paragraph.
    if not re.search(r"[ぁ-ゟ゠-ヿ一-龯]", text):
        return text

    def english_word_count(s: str) -> int:
        return len(re.findall(r"\b[A-Za-z]{3,}\b", s))

    def has_cjk(s: str) -> bool:
        return bool(re.search(r"[ぁ-ゟ゠-ヿ一-龯]", s))

    def looks_like_environment(s: str) -> bool:
        return "\\begin{" in s or "\\end{" in s

    cmd_rx = re.compile(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?(?:\{[^{}]*\})?")
    math_rx = re.compile(r"\$[^$]*\$", re.DOTALL)
    num_rx = re.compile(r"\b\d+(?:\.\d+)?\b")

    def sig_tokens(s: str) -> set[str]:
        toks: set[str] = set()
        for m in cmd_rx.finditer(s):
            toks.add(m.group(0))
        for m in math_rx.finditer(s):
            toks.add(m.group(0))
        for m in num_rx.finditer(s):
            toks.add(m.group(0))
        return toks

    def jaccard(a: set[str], b: set[str]) -> float:
        if not a and not b:
            return 0.0
        inter = len(a & b)
        union = len(a | b)
        return inter / union if union else 0.0

    # Split while preserving paragraph separators.
    parts = re.split(r"(\n\s*\n+)", text)
    if len(parts) < 3:
        return text

    changed = False
    i = 0
    out_parts: list[str] = []
    while i < len(parts):
        cur = parts[i]
        sep = parts[i + 1] if i + 1 < len(parts) else ""
        nxt = parts[i + 2] if i + 2 < len(parts) else None

        if nxt is None:
            out_parts.append(cur)
            if sep:
                out_parts.append(sep)
            break

        # Only consider normal text paragraphs (avoid environments/bibliography).
        if (
            english_word_count(cur) >= 18
            and not has_cjk(cur)
            and has_cjk(nxt)
            and not looks_like_environment(cur)
            and not looks_like_environment(nxt)
            and "\\begin{thebibliography}" not in cur
            and "\\bibitem" not in cur
            and "\\begin{thebibliography}" not in nxt
            and "\\bibitem" not in nxt
        ):
            a = sig_tokens(cur)
            b = sig_tokens(nxt)
            # Require shared anchors (math/commands/numbers) to ensure it's the same content.
            if (len(a) >= 2 or len(b) >= 2) and jaccard(a, b) >= 0.45:
                # Drop the English paragraph, keep separator + translated paragraph.
                changed = True
                out_parts.append(nxt)
                if i + 3 < len(parts):
                    out_parts.append(parts[i + 3])
                i += 4
                continue

        out_parts.append(cur)
        out_parts.append(sep)
        i += 2

    return "".join(out_parts) if changed else text


def _tune_maketitle_thanks_layout(text: str) -> str:
    if "\\maketitle" not in text:
        return text

    changed = False
    looks_japanese = bool(re.search(r"[\u3040-\u30FF]", text))

    # nips_2017 sometimes carries a very long equal-contribution \thanks block.
    # In Japanese translation this can expand further and break the first page layout.
    if (
        ("nips_2017" in text)
        and looks_japanese
        and ("\\thanks{" in text)
        and str(os.environ.get("ROSETTA_SHORTEN_AUTHOR_THANKS", "0") or "0").strip() in ("1", "true", "yes")
    ):
        def _shorten_thanks_blocks(src: str) -> str:
            i = 0
            n = len(src)
            out_parts: list[str] = []
            last = 0

            while i < n:
                j = src.find("\\thanks{", i)
                if j < 0:
                    break
                k = j + len("\\thanks")
                if k >= n or src[k] != "{":
                    i = j + 1
                    continue

                # Parse balanced braces for the required argument
                brace_depth = 0
                p = k
                while p < n:
                    ch = src[p]
                    if ch == "{":
                        brace_depth += 1
                    elif ch == "}":
                        brace_depth -= 1
                        if brace_depth == 0:
                            break
                    p += 1
                if brace_depth != 0 or p >= n:
                    # Unbalanced; give up
                    break

                content = src[k + 1 : p]

                replacement_content = content
                # Only touch very long ones.
                if len(content) >= 260 or content.count("\n") >= 6:
                    # Prefer a deterministic short note for the common equal-contribution paragraph.
                    if "貢献度" in content or "同等" in content:
                        replacement_content = "貢献度は同等である。表記順はランダムである。"
                    else:
                        # Fallback: keep the first 2 sentences.
                        parts = [s.strip() for s in re.split(r"[。．\.]", content) if s.strip()]
                        head = "。".join(parts[:2]).strip()
                        if head and not head.endswith("。"):
                            head += "。"
                        replacement_content = head or content[:160]

                out_parts.append(src[last:j])
                out_parts.append("\\thanks{" + replacement_content + "}")
                last = p + 1
                i = last

            if last == 0:
                return src
            out_parts.append(src[last:])
            return "".join(out_parts)

        text2 = _shorten_thanks_blocks(text)
        if text2 != text:
            text = text2
            changed = True

    # Cleanup artifacts from previous runs where we accidentally injected literal "\n" and "\\baselineskip".
    text2 = re.sub(
        r"\\maketitle\\n\\enlargethispage\*\{(\d+)\\\\baselineskip\}",
        r"\\maketitle\n\\enlargethispage*{\1\\baselineskip}",
        text,
    )
    text2 = re.sub(
        r"\\enlargethispage\*\{(\d+)\\\\baselineskip\}",
        r"\\enlargethispage*{\1\\baselineskip}",
        text2,
    )
    if text2 != text:
        text = text2
        changed = True

    # Patch \@thanks rendering (used by many classes after \maketitle) without changing content.
    if "\\rosetta@old@thanks" not in text and "\\begin{document}" in text:
        is_nips_2017 = "nips_2017" in text
        if is_nips_2017:
            thanks_total_chars = 0
            if looks_japanese and ("\\thanks{" in text):
                try:
                    i = 0
                    n = len(text)
                    while i < n:
                        j = text.find("\\thanks{", i)
                        if j < 0:
                            break
                        k = j + len("\\thanks")
                        if k >= n or text[k] != "{":
                            i = j + 1
                            continue
                        depth = 0
                        p = k
                        while p < n:
                            ch = text[p]
                            if ch == "{":
                                depth += 1
                            elif ch == "}":
                                depth -= 1
                                if depth == 0:
                                    break
                            p += 1
                        if depth != 0 or p >= n:
                            break
                        thanks_total_chars += (p - (k + 1))
                        i = p + 1
                except Exception:
                    thanks_total_chars = 0

            thanks_font_cmd = "\\footnotesize"
            if looks_japanese:
                if thanks_total_chars >= 1200:
                    thanks_font_cmd = "\\tiny"
                elif thanks_total_chars >= 650:
                    thanks_font_cmd = "\\scriptsize"

            thanks_prefix = ""
            if looks_japanese:
                thanks_prefix = (
                    "\\insert\\footins{"
                    "\\footnotesize"
                    "\\setlength{\\parindent}{0pt}"
                    "\\setlength{\\parskip}{0pt}"
                    "\\hbox to \\textwidth{\\hfil\\@noticestring\\hfil}"
                    "\\par"
                    "}"
                )
            thanks_def = (
                "\\def\\@thanks{"
                "\\par\\begingroup"
                + thanks_font_cmd
                + "\\linespread{0.98}\\selectfont"
                "\\setlength{\\parindent}{0pt}"
                "\\setlength{\\parskip}{0pt}"
                "\\raggedright"
                + "\\rosetta@old@thanks"
                + thanks_prefix
                + "\\par\\endgroup}"
            )
        else:
            thanks_def = "\\def\\@thanks{\\par\\begingroup\\scriptsize\\rosetta@old@thanks\\par\\endgroup}"

        notice_def = ""
        if is_nips_2017 and looks_japanese:
            # Disable the original float-based notice to avoid it showing up above the abstract
            # or floating away; we inject the notice as part of \@thanks instead.
            notice_def = "\\let\\rosetta@old@notice\\@notice\n\\let\\@notice\\relax\n"

        extra = ""
        if is_nips_2017 and looks_japanese:
            extra = "\\raggedbottom\n\\widowpenalty=1500\\clubpenalty=1500\\displaywidowpenalty=1500\n"

        patch = (
            "\\makeatletter\n"
            "\\let\\rosetta@old@thanks\\@thanks\n"
            + thanks_def
            + "\n"
            + notice_def
            + "\\makeatother\n"
            + extra
        )
        text = text.replace("\\begin{document}", "\\begin{document}\n" + patch, 1)
        changed = True

    # Give the first page a bit more vertical slack.
    baselines_env = str(os.environ.get("ROSETTA_MAKETITLE_ENLARGE_BASELINES", "10") or "10").strip()
    try:
        baselines = int(float(baselines_env))
    except Exception:
        baselines = 10

    # Heuristic: nips_2017 + Japanese often produces a huge \thanks block.
    # If we move the notice into the footnote stream, TeX may push the entire
    # footnote area to the next page unless we give the first page more slack.
    if ("nips_2017" in text) and looks_japanese:
        try:
            total = 0
            i = 0
            n = len(text)
            while i < n:
                j = text.find("\\thanks{", i)
                if j < 0:
                    break
                k = j + len("\\thanks")
                if k >= n or text[k] != "{":
                    i = j + 1
                    continue
                depth = 0
                p = k
                while p < n:
                    ch = text[p]
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            break
                    p += 1
                if depth != 0 or p >= n:
                    break
                total += (p - (k + 1))
                i = p + 1
            # Tune thresholds conservatively, but avoid excessive enlargement.
            # Too-large values can push the footnote area beyond the physical page box
            # (it then gets clipped in the PDF output).
            if total >= 1200:
                baselines = max(baselines, 16)
            elif total >= 700:
                baselines = max(baselines, 14)
            elif total >= 350:
                baselines = max(baselines, 12)

            # Safety cap (unless the user explicitly set a larger env baseline).
            if baselines_env == "10":
                baselines = min(baselines, 18)
        except Exception:
            pass
    if baselines > 0:
        # Detect nips_2017 package/class presence. Use a simple substring check to
        # avoid regex pitfalls with TeX backslashes.
        is_nips_2017 = "nips_2017" in text

        cap_default = 18 if (is_nips_2017 and looks_japanese and baselines_env == "10") else None

        def _clamp_enlarge(n: int) -> int:
            if cap_default is None:
                return n
            return min(n, cap_default)

        # If we already have an \enlargethispage* near \maketitle, make sure it's large
        # enough for huge JA \thanks (and our injected notice) to stay on page 1.
        if is_nips_2017:
            # Handle older behavior where it was placed after \maketitle.
            text2 = re.sub(
                r"(\\maketitle)\s*\n\\enlargethispage\*\{(\d+)\\baselineskip\}",
                lambda m: (
                    f"\\enlargethispage*{{{_clamp_enlarge(max(int(m.group(2)), baselines))}\\baselineskip}}\n" + m.group(1)
                ),
                text,
                count=1,
            )
            if text2 != text:
                text = text2
                changed = True

            # If it's already before \maketitle, just bump the number if needed.
            text2 = re.sub(
                r"\\enlargethispage\*\{(\d+)\\baselineskip\}\s*\n(\s*\\maketitle)",
                lambda m: f"\\enlargethispage*{{{_clamp_enlarge(max(int(m.group(1)), baselines))}\\baselineskip}}\n" + m.group(2),
                text,
                count=1,
            )
            if text2 != text:
                text = text2
                changed = True

        # If we already injected it after \maketitle (older behavior), move it before for nips_2017
        # so the bottom noticebox/thanks logic inside \maketitle gets the extra space.
        if is_nips_2017:
            text2 = re.sub(
                r"(\\maketitle)\n\\enlargethispage\*\{(\d+)\\baselineskip\}",
                lambda m: f"\\enlargethispage*{{{_clamp_enlarge(max(int(m.group(2)), baselines))}\\baselineskip}}\n" + m.group(1),
                text,
                count=1,
            )
            if text2 != text:
                text = text2
                changed = True

        already_near_maketitle = bool(
            re.search(r"\\maketitle\s*\n\s*\\enlargethispage\*?\{", text)
            or re.search(r"\\enlargethispage\*?\{[^}]+\}\s*\n\s*\\maketitle", text)
        )
        if not already_near_maketitle:
            if is_nips_2017:
                text = text.replace(
                    "\\maketitle",
                    f"\\enlargethispage*{{{_clamp_enlarge(baselines)}\\baselineskip}}\n\\maketitle",
                    1,
                )
            else:
                text = text.replace(
                    "\\maketitle",
                    f"\\maketitle\n\\enlargethispage*{{{baselines}\\baselineskip}}",
                    1,
                )
            changed = True

        tight_title_abstract = str(os.environ.get("ROSETTA_NIPS_TIGHT_TITLE_ABSTRACT", "0") or "0").strip() in (
            "1",
            "true",
            "yes",
        )

        # If we previously injected title/abstract tightening wrappers, remove them unless explicitly enabled.
        if is_nips_2017 and not tight_title_abstract:
            text2 = text
            text2 = text2.replace("{\\linespread{1.00}\\selectfont\\maketitle}", "\\maketitle")
            text2 = text2.replace("\\begingroup\\linespread{1.00}\\selectfont\n\\enlargethispage", "\\enlargethispage")
            text2 = text2.replace("\\end{abstract}\n\\endgroup", "\\end{abstract}")
            if text2 != text:
                text = text2
                changed = True

        if is_nips_2017 and tight_title_abstract and "\\linespread{1.00}\\selectfont\\maketitle" not in text:
            text2 = text.replace(
                "\\maketitle",
                "{\\linespread{1.00}\\selectfont\\maketitle}",
                1,
            )
            if text2 != text:
                text = text2
                changed = True

        # For Japanese nips_2017, keep the title+abstract block a bit tighter,
        # while preserving the global Japanese linespread for the rest of the paper.
        # IMPORTANT: don't use re.sub replacement strings here (they treat \n/\r as escapes).
        if is_nips_2017 and looks_japanese and tight_title_abstract:
            tight_open = "\\begingroup\\linespread{1.00}\\selectfont"
            if tight_open not in text and "\\enlargethispage" in text and "\\end{abstract}" in text:
                text2 = text.replace(
                    "\n\\enlargethispage",
                    "\n" + tight_open + "\n\\enlargethispage",
                    1,
                )
                if text2 != text:
                    text2 = text2.replace(
                        "\\end{abstract}",
                        "\\end{abstract}\n\\endgroup",
                        1,
                    )
                if text2 != text:
                    text = text2
                    changed = True

    return text if changed else text


def _remove_orphaned_bibliography_blocks(text: str) -> str:
    pattern = re.compile(
        r"\n[ \t]*\\begin\{thebibliography\}\{[^}]+\}\s*(?:[ \t]*%[^\n]*\n|[ \t]*\n)*?(?=\\end\{document\})",
        re.MULTILINE,
    )
    return pattern.sub("\n", text)


def _fix_malformed_commands(text: str) -> str:
    """
    Fix common malformed commands and duplicate packages.
    """
    # Fix > instead of } in \usepackage
    # Example: \usepackage{subfiles> -> \usepackage{subfiles}
    text = re.sub(r'\\usepackage\{([^}]+)>', r'\\usepackage{\1}', text)

    # Fix ) instead of } in \usepackage
    # Example: \usepackage{subfiles) -> \usepackage{subfiles}
    text = re.sub(r'\\usepackage\{([^}]+)\)', r'\\usepackage{\1}', text)
    
    # Fix > instead of } in begin/end environment
    # Example: \end{figure> -> \end{figure}
    text = re.sub(r'\\(begin|end)\{([^}]+)>', r'\\\1{\2}', text)

    # Fix > at end of begin/end environment (if brace exists)
    # Example: \begin{figure}> -> \begin{figure}
    text = re.sub(r'\\(begin|end)\{([^}]+)\}>', r'\\\1{\2}', text)
    
    # Deduplicate \usepackage lines in the preamble
    lines = text.split('\n')
    seen_usepackages = set()
    new_lines = []
    in_preamble = True
    
    for line in lines:
        if r'\begin{document}' in line:
            in_preamble = False
            
        if in_preamble and line.strip().startswith(r'\usepackage'):
            # Normalize spaces to catch duplicates like \usepackage{x} vs \usepackage{ x }
            # Also strip comments for comparison purposes (simplistic)
            content = line.strip().split('%')[0].strip()
            clean_line = re.sub(r'\s+', '', content)
            
            if clean_line in seen_usepackages:
                continue
                
            seen_usepackages.add(clean_line)
            new_lines.append(line)
        else:
            new_lines.append(line)
            
    text = '\n'.join(new_lines)

    # Дополнительный страховочный фикс: самая частая сломанная строка из ACM шаблона
    # "The first command in your LaTeX source must be the \\documentclass command."
    # иногда превращается в реальную строку "\\documentclass command.", что
    # заставляет LaTeX искать класс c.cls. Комментируем её в любом случае.
    text = text.replace('\n\\documentclass command.\n', '\n% \\documentclass command.\n')

    return text


def _fix_spurious_documentclass_lines(text: str) -> str:
    """Comment out malformed template lines with ``\documentclass`` before the real class.

    Некоторые шаблоны (например, ACM acmart) содержат строку в комментариях
    вида "The first command in your LaTeX source must be the \\documentclass command.".
    Перевод/модель иногда снимает символ "%" и оставляет строку
    ``\documentclass command.``, из-за чего LaTeX пытается загрузить класс ``c.cls``.

    Стратегия:
    - до первой корректной команды ``\documentclass[...]{...}`` ищем любые строки,
      содержащие ``\documentclass``, но *без* ``{...}``;
    - такие строки комментируем, добавляя "%" в начало (если ещё не закомментированы).
    """
    lines = text.split('\n')
    new_lines: List[str] = []
    seen_real_docclass = False

    docclass_pattern = re.compile(r"^\s*\\documentclass(\[[^\]]*\])?\{[^}]+\}")

    for line in lines:
        if not seen_real_docclass and docclass_pattern.search(line):
            seen_real_docclass = True
            new_lines.append(line)
            continue

        if not seen_real_docclass and "\\documentclass" in line and "{" not in line:
            # Лишняя строка вроде "\\documentclass command." — закомментируем её
            stripped = line.lstrip()
            if not stripped.startswith('%'):
                leading_spaces = line[: len(line) - len(stripped)]
                line = f"{leading_spaces}% {stripped}"

        new_lines.append(line)

    return "\n".join(new_lines)


def _fix_command_conflicts(text: str) -> str:
    r"""
    Исправляет конфликты переопределения команд, которые уже определены в пакетах.
    
    Некоторые команды (например, \C) уже определены в пакетах babel или fontenc
    для поддержки кириллицы. Если документ пытается определить их через \newcommand,
    это вызывает ошибку "Command already defined". Решение - использовать \renewcommand
    вместо \newcommand.
    
    Args:
        text: LaTeX содержимое
        
    Returns:
        Исправленное содержимое
    """
    # Список команд, которые могут конфликтовать с пакетами кириллицы
    # \C определена в babel/fontenc как команда акцента
    conflicting_commands = ['C']
    
    for cmd in conflicting_commands:
        # Универсальный паттерн для всех вариантов \newcommand{\C}:
        # - \newcommand{\C}{...}
        # - \newcommand*{\C}{...}
        # - \newcommand{\C}[1]{...}
        # - \newcommand*{\C}[1]{...}
        pattern = re.compile(
            r'\\newcommand(\*?)\{\\' + re.escape(cmd) + r'\}(\[[^\]]*\])?',
            re.IGNORECASE
        )
        
        def replace_cmd(match):
            star = match.group(1)  # * если есть
            opt_args = match.group(2) or ''  # [1] если есть
            return r'\renewcommand' + star + r'{\\' + cmd + r'}' + opt_args
        
        new_text = pattern.sub(replace_cmd, text)
        if new_text != text:
            logger.info(f"Исправлено: заменено \\newcommand{{\\{cmd}}} на \\renewcommand{{\\{cmd}}}")
            text = new_text
    
    return text


def _fix_fontenc_conflicts(text: str) -> str:
    """
    Универсальное исправление кодировок fontenc для поддержки кириллицы.
    
    Стратегия:
    1. Если есть T2A — удаляем все T1
    2. Если нет T2A, но есть T1 — заменяем T1 на T2A
    3. Если есть fontenc без опций — добавляем T2A
    
    Args:
        text: LaTeX содержимое
        
    Returns:
        Исправленное содержимое
    """
    # Проверяем, есть ли кириллица (если нет — не трогаем fontenc)
    has_cyrillic = bool(re.search(r'[а-яА-ЯёЁ]', text))
    if not has_cyrillic:
        return text
    
    has_t2a = r'\usepackage[T2A]{fontenc}' in text or r'\usepackage[T2A,T1]{fontenc}' in text
    
    if has_t2a:
        # Случай 1: T2A уже есть — удаляем все отдельные T1
        pattern = re.compile(r'\\usepackage\[T1\]\{fontenc\}\s*(%[^\n]*)?\n?')
        new_text = pattern.sub('', text)
        if new_text != text:
            logger.info("Удалён дублирующий \\usepackage[T1]{fontenc}")
            text = new_text
    else:
        # Случай 2: T2A нет — ищем любой fontenc и модифицируем
        # Паттерн для \usepackage[...]{fontenc}
        fontenc_pattern = re.compile(
            r'\\usepackage(\[([^\]]*)\])?\{fontenc\}',
            re.IGNORECASE
        )
        
        match = fontenc_pattern.search(text)
        if match:
            existing_options = match.group(2) if match.group(2) else ""
            
            if existing_options:
                # Заменяем T1 на T2A в опциях
                if 'T1' in existing_options:
                    new_options = existing_options.replace('T1', 'T2A')
                else:
                    # Добавляем T2A к существующим опциям
                    new_options = f"T2A,{existing_options}"
            else:
                new_options = "T2A"
            
            replacement = f'\\usepackage[{new_options}]{{fontenc}}'
            new_text = text[:match.start()] + replacement + text[match.end():]
            
            if new_text != text:
                logger.info(f"Изменены опции fontenc: [{existing_options or 'none'}] -> [{new_options}]")
                text = new_text
    
    # Удаляем лишние пустые строки
    text = re.sub(r'\n\n\n+', '\n\n', text)
    
    return text


def _fix_times_font_conflicts(text: str) -> str:
    """Удаляет пакет times для документов с кириллицей и T2A.

    Для кириллических документов с fontenc[T2A] шрифт Times
    часто даёт некорректные жирные начертания.
    """
    # Только если есть кириллица
    if not re.search(r'[а-яА-ЯёЁ]', text):
        return text

    # И только если используется T2A
    if r'\usepackage[T2A]{fontenc}' not in text:
        return text

    original = text

    # Частый случай в шаблонах RevTeX
    text = text.replace(r'\usepackage{graphicx,times}', r'\usepackage{graphicx}')

    # Чистый times
    text = text.replace(r'\usepackage{times}', '')

    # Дополнительный случай: IEEEtran по умолчанию использует шрифт Times (ptm)
    # без T2A-глифов, из-за чего жирные/малые прописные варианты исчезают.
    doc_class_match = re.search(r'\\documentclass(?:\[[^\]]*\])?\{([^}]+)\}', text)
    if doc_class_match:
        doc_class = doc_class_match.group(1).lower()
        if 'ieeetran' in doc_class and r'\\renewcommand{\\rmdefault}{cmr}' not in text:
            insert_pos = doc_class_match.end()
            text = text[:insert_pos] + '\n\\renewcommand{\\rmdefault}{cmr}\n' + text[insert_pos:]

    # Нормализуем пустые строки
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)

    return text


def _fix_acmart_title_bold(text: str) -> str:
    """Ensure bold title font for acmart documents with Cyrillic.

    Для класса acmart заголовок формируется через \@titlefont внутри maketitle,
    а вложенный \textbf{} внутри \title{...} не всегда даёт ожидаемый результат.

    Стратегия:
    - если документ использует класс acmart и содержит кириллицу;
    - перед \begin{document} вставляем переопределение \@titlefont, которое
      включает \bfseries и переключает на serif-шрифт (rmfamily), чтобы
      заголовок визуально совпадал с жирными русскими секциями.
    """

    # Только если есть кириллица
    if not re.search(r"[а-яА-ЯёЁ]", text):
        return text

    # И только для класса acmart
    doc_class_match = re.search(r"\\documentclass(?:\[[^\]]*\])?\{([^}]+)\}", text)
    if not doc_class_match:
        return text
    if "acmart" not in doc_class_match.group(1).lower():
        return text

    # Если мы уже патчили @titlefont, выходим
    if "\def\\@titlefont" in text or "\renewcommand{\\@titlefont}" in text:
        return text

    doc_pos = text.find("\\begin{document}")
    if doc_pos == -1:
        return text

    patch = (
        "\n% Rosetta: make acmart title bold in Cyrillic"\
        "\n\\makeatletter"\
        "\n\\def\\@titlefont{\\Huge\\rmfamily\\bfseries}"\
        "\n\\makeatother"\
        "\n"
    )

    return text[:doc_pos] + patch + text[doc_pos:]


def _fix_acmart_affiliation_countries(text: str) -> str:
    """Раскомментирует \city и \country в аффилиациях для acmart.

    acmart требует, чтобы у каждой аффилиации была страна, иначе
    выдаёт `Class acmart Error: No country present for an affiliation.`
    В исходной статье 2401.09883 строки с \city и \country были
    закомментированы. Мы просто раскомментируем их обратно.
    """

    doc_class_match = re.search(r"\\documentclass(?:\[[^\]]*\])?\{([^}]+)\}", text)
    if not doc_class_match or "acmart" not in doc_class_match.group(1).lower():
        return text

    # Обрабатываем только содержимое: внутри каждого \affiliation{...}
    def _fix_affil_block(match: re.Match) -> str:
        block = match.group(0)

        # Раскомментируем строки вида `% \city{...}` и `% \country{...}`
        block_fixed = re.sub(
            r"(^\s*)%\s*(\\city\{[^}]*\})",
            r"\1\2",
            block,
            flags=re.MULTILINE,
        )
        block_fixed = re.sub(
            r"(^\s*)%\s*(\\country\{[^}]*\})",
            r"\1\2",
            block_fixed,
            flags=re.MULTILINE,
        )
        return block_fixed

    pattern = re.compile(r"\\affiliation\s*\{[^}]*\}", re.DOTALL)
    new_text = pattern.sub(_fix_affil_block, text)
    return new_text


def _fix_acmart_linebreaking(text: str) -> str:
    """Relax line breaking for acmart documents with Cyrillic to reduce overfull boxes.

    Стратегия (минимально инвазивная):
    - только для классов acmart с кириллицей;
    - один раз после первого \maketitle вставляем \sloppy и небольшое \emergencystretch,
      чтобы LaTeX охотнее переносил длинные русские слова и не вылезал за края колонок.
    """

    # Только если есть кириллица
    if not re.search(r"[а-яА-ЯёЁ]", text):
        return text

    # И только для класса acmart
    doc_class_match = re.search(r"\\documentclass(?:\[[^\]]*\])?\{([^}]+)\}", text)
    if not doc_class_match or "acmart" not in doc_class_match.group(1).lower():
        return text

    # Если уже применяли этот фикс — выходим
    if "Rosetta: relax line breaking for acmart" in text:
        return text

    # Ищем первое вхождение \maketitle
    mk_idx = text.find("\\maketitle")
    if mk_idx == -1:
        return text

    insert_pos = mk_idx + len("\\maketitle")
    patch = (
        "\n% Rosetta: relax line breaking for acmart Russian text"\
        "\n\\sloppy"\
        "\n\\emergencystretch=3em"\
        "\n"
    )

    return text[:insert_pos] + patch + text[insert_pos:]


def _fix_broken_includegraphics_placeholders(text: str) -> str:
    """Fix placeholder-like broken includegraphics lines such as ``\\1\\columnwidth]{...}``.

    В некоторых документах (например, 2401.09883) после комбинации переводчика и
    регекс-фиксов строки с картинками повреждаются до вида::

        \\1\\columnwidth]{images/foo.pdf}

    Такая строка даёт cascade ошибок (Undefined control sequence, Missing number и т.п.)
    и одновременно ломает верстку (ширина не контролируется).

    Здесь мы консервативно распознаём такие строки и восстанавливаем их до
    корректного ``\\includegraphics[width=\\columnwidth]{...}``.
    """

    pattern = re.compile(
        r"(?m)^(?P<indent>\s*)\\1\\columnwidth]\{(?P<path>[^}]+)\}",
    )

    def _repl(m: re.Match) -> str:
        indent = m.group("indent")
        path = m.group("path")
        return f"{indent}\\includegraphics[width=\\columnwidth]{{{path}}}"

    new_text = pattern.sub(_repl, text)
    return new_text


def _fix_llama_macros(text: str) -> str:
    """Fix broken LLaMA-related macros produced by translation.

    В некоторых русских переводах статьи о Code Llama макрос для Llama 2
    повреждается до вида ``\llamавtwo``, что даёт Undefined control sequence
    (\llam) при компиляции. Здесь мы возвращаем его к корректному
    ``\llamavtwo``.
    """

    # Быстрый выход, если характерного артефакта нет
    if "llamавtwo" not in text:
        return text

    return text.replace("\\llamавtwo", "\\llamavtwo")


def _fix_babel_conflicts(text: str) -> str:
    r"""
    Универсальное исправление пакета babel для поддержки кириллицы.
    
    Стратегия:
    1. Определяем, может ли класс документа загружать babel (googlecloud, acl и др.)
    2. Если да — удаляем \usepackage{babel} и добавляем \PassOptionsToPackage{russian}{babel}
    3. Иначе — объединяем опции всех \usepackage{babel}
    
    Args:
        text: LaTeX содержимое
        
    Returns:
        Исправленное содержимое
    """
    # Проверяем, есть ли кириллица (если нет — не трогаем babel)
    has_cyrillic = bool(re.search(r'[а-яА-ЯёЁ]', text))
    if not has_cyrillic:
        return text
    
    # Классы документов, которые часто загружают babel самостоятельно
    babel_loading_classes = [
        'googlecloud', 'acl', 'aclpub', 'nips', 'neurips', 'icml',
        'aaai', 'ijcai', 'emnlp', 'naacl', 'coling', 'acl_natbib',
        'acmart', 'revtex'
    ]
    
    # Проверяем, использует ли документ такой класс
    doc_class_match = re.search(r'\\documentclass(?:\[[^\]]*\])?\{(\w+)\}', text)
    class_may_load_babel = False
    if doc_class_match:
        doc_class = doc_class_match.group(1).lower()
        class_may_load_babel = any(cls in doc_class for cls in babel_loading_classes)
    
    # Ищем все \usepackage[...]{babel}
    babel_pattern = re.compile(
        r'\\usepackage(\[([^\]]*)\])?\{babel\}',
        re.IGNORECASE
    )
    
    matches = list(babel_pattern.finditer(text))
    
    # Проверяем, есть ли уже PassOptionsToPackage для babel с russian
    has_pass_options = r'\PassOptionsToPackage' in text and 'russian' in text.split(r'\documentclass')[0] if r'\documentclass' in text else False
    
    # Если класс может загружать babel — используем PassOptionsToPackage подход
    if class_may_load_babel:
        # Удаляем все \usepackage{babel} из текста
        new_text = babel_pattern.sub('', text)
        
        # Добавляем \PassOptionsToPackage перед \documentclass если его ещё нет
        if not has_pass_options:
            doc_match = re.search(r'\\documentclass', new_text)
            if doc_match:
                pass_cmd = r'\PassOptionsToPackage{russian}{babel}' + '\n'
                new_text = new_text[:doc_match.start()] + pass_cmd + new_text[doc_match.start():]
                logger.info("Добавлен \\PassOptionsToPackage{russian}{babel} (класс может загружать babel)")
        
        # Удаляем лишние пустые строки
        new_text = re.sub(r'\n\s*\n\s*\n', '\n\n', new_text)
        return new_text
    
    # Стандартная логика для обычных классов
    if not matches:
        # Babel не найден — возможно, загружается из класса
        doc_match = re.search(r'\\documentclass', text)
        if doc_match and not has_pass_options:
            pass_cmd = r'\PassOptionsToPackage{russian}{babel}' + '\n'
            text = pass_cmd + text
            logger.info("Добавлен \\PassOptionsToPackage{russian}{babel} (babel может загружаться из класса)")
        return text
    
    # Собираем все опции из всех \usepackage{babel}
    all_options = set()
    for match in matches:
        if match.group(2):
            # Разделяем опции по запятой
            options = [opt.strip() for opt in match.group(2).split(',') if opt.strip()]
            all_options.update(options)
    
    # Проверяем, есть ли уже russian (регистронезависимо)
    has_russian = any(opt.lower() == 'russian' for opt in all_options)
    
    if not has_russian:
        all_options.add('russian')
    
    # Сортируем опции: основной язык (russian) должен быть последним в babel
    # Это определяет основной язык документа
    sorted_options = sorted([opt for opt in all_options if opt.lower() != 'russian'])
    sorted_options.append('russian')
    
    new_options_str = ','.join(sorted_options)
    
    # Если есть только один babel и опции не изменились — не трогаем
    if len(matches) == 1:
        existing_opts = matches[0].group(2) or ""
        existing_opts_set = set(opt.strip() for opt in existing_opts.split(',') if opt.strip())
        if existing_opts_set == all_options:
            return text
    
    # Заменяем первый \usepackage[...]{babel} на объединённый
    replacement = f'\\usepackage[{new_options_str}]{{babel}}'
    
    # Строим новый текст
    result_parts = []
    last_end = 0
    
    for i, match in enumerate(matches):
        result_parts.append(text[last_end:match.start()])
        if i == 0:
            # Первый — заменяем на объединённый
            result_parts.append(replacement)
        else:
            # Остальные — пропускаем (удаляем)
            logger.info("Удалён дублирующий \\usepackage{babel}")
        last_end = match.end()
    
    result_parts.append(text[last_end:])
    new_text = ''.join(result_parts)
    
    if new_text != text:
        logger.info(f"Объединены опции babel: [{new_options_str}]")
        # Удаляем пустые строки
        new_text = re.sub(r'\n\s*\n\s*\n', '\n\n', new_text)
        text = new_text
    
    return text


def _fix_sectsty_conflicts(text: str) -> str:
    """
    Удаляет sectsty для нестандартных классов документов.
    
    Многие кастомные шаблоны (googlecloud, leaplab, acmart, ieeeconf и др.)
    несовместимы с sectsty. Вместо поддержания списка несовместимых классов,
    используем эвристику: удаляем sectsty для всех нестандартных классов.
    
    Стандартные классы: article, report, book, letter, slides
    
    Args:
        text: LaTeX содержимое
        
    Returns:
        Исправленное содержимое
    """
    # Если sectsty нет — ничего не делаем
    if r'\usepackage{sectsty}' not in text:
        return text
    
    # Определяем класс документа
    doc_class_match = re.search(r'\\documentclass.*?\{([^}]+)\}', text)
    if not doc_class_match:
        return text
    
    doc_class = doc_class_match.group(1).lower().strip()
    
    # Стандартные классы LaTeX, которые поддерживают sectsty
    standard_classes = {
        'article', 'report', 'book', 'letter', 'slides', 'memoir',
        'scrartcl', 'scrreprt', 'scrbook',  # KOMA-Script
        'extarticle', 'extreport', 'extbook',  # extsizes
    }
    
    # Если класс стандартный — оставляем sectsty
    if doc_class in standard_classes:
        return text
    
    # Для всех остальных классов — удаляем sectsty (слишком много несовместимостей)
    logger.info(f"Удаляем sectsty (нестандартный класс: {doc_class})")
    
    # Удаляем \usepackage{sectsty}
    text = re.sub(r'\\usepackage\{sectsty\}\s*(%[^\n]*)?\n?', '', text, flags=re.IGNORECASE)
    
    # Удаляем связанные команды sectsty
    sectsty_commands = [
        r'\\allsectionsfont\{[^}]*\}',
        r'\\sectionfont\{[^}]*\}',
        r'\\subsectionfont\{[^}]*\}',
        r'\\subsubsectionfont\{[^}]*\}',
        r'\\paragraphfont\{[^}]*\}',
        r'\\chapterfont\{[^}]*\}',
        r'\\partfont\{[^}]*\}',
    ]
    
    for cmd_pattern in sectsty_commands:
        text = re.sub(cmd_pattern + r'\s*\n?', '', text, flags=re.IGNORECASE)
    
    # Удаляем пустые строки
    text = re.sub(r'\n\s*\n\s*\n', '\n\n', text)
    
    return text


def _fix_cjk_conflicts(text: str) -> str:
    r"""
    Исправляет проблемы с CJK пакетом при компиляции pdfLaTeX.
    
    CJK пакет для китайских/японских/корейских символов часто конфликтует
    с babel и fontenc при использовании pdfLaTeX. 
    
    Стратегия:
    1. Удаляем \usepackage{CJK} если есть русский babel
    2. Удаляем или комментируем \begin{CJK}...\end{CJK} блоки
    3. Оставляем содержимое без CJK-окружения (символы станут нечитаемыми, но не сломают компиляцию)
    
    Args:
        text: LaTeX содержимое
        
    Returns:
        Исправленное содержимое
    """
    # Проверяем, есть ли CJK
    if r'\usepackage{CJK}' not in text and r'\begin{CJK}' not in text:
        return text
    
    # Проверяем, есть ли русский babel (конфликт)
    has_russian = 'russian' in text and 'babel' in text
    
    if not has_russian:
        return text
    
    logger.info("Обнаружен конфликт CJK с русским babel, исправляем...")
    
    # Удаляем \usepackage{CJK}
    text = re.sub(r'\\usepackage\{CJK\}\s*\n?', '', text)
    
    # Удаляем \begin{CJK}...\end{CJK} блоки, оставляя только содержимое
    # Паттерн: \begin{CJK}{encoding}{font}content\end{CJK}
    # Заменяем на пустоту, так как китайские символы всё равно не отобразятся
    cjk_pattern = re.compile(
        r'\\begin\{CJK\}\{[^}]*\}\{[^}]*\}(.*?)\\end\{CJK\}',
        re.DOTALL
    )
    
    # Заменяем CJK блоки на пустоту (символы в них всё равно будут битыми)
    new_text = cjk_pattern.sub('', text)
    
    if new_text != text:
        logger.info("Удалены CJK-блоки (несовместимы с русским babel)")
        text = new_text
    
    return text


def _fix_package_order(text: str) -> str:
    r"""
    Исправляет порядок загрузки пакетов: fontenc должен быть ДО babel.
    
    Babel при загрузке проверяет наличие fontenc и использует его кодировку.
    Если fontenc загружается после babel, возникает предупреждение и проблемы.
    
    Args:
        text: LaTeX содержимое
        
    Returns:
        Исправленное содержимое с правильным порядком пакетов
    """
    # Ищем позиции fontenc и babel
    fontenc_match = re.search(r'\\usepackage(\[[^\]]*\])?\{fontenc\}', text)
    babel_match = re.search(r'\\usepackage(\[[^\]]*\])?\{babel\}', text)
    
    if not fontenc_match or not babel_match:
        return text
    
    # Если fontenc уже до babel — всё хорошо
    if fontenc_match.start() < babel_match.start():
        return text
    
    logger.info("Исправляем порядок пакетов: fontenc должен быть до babel")
    
    # Извлекаем строку fontenc
    fontenc_line = fontenc_match.group(0)
    
    # Удаляем fontenc с текущей позиции
    text = text[:fontenc_match.start()] + text[fontenc_match.end():]
    
    # Находим новую позицию babel (она изменилась после удаления fontenc)
    babel_match_new = re.search(r'\\usepackage(\[[^\]]*\])?\{babel\}', text)
    if babel_match_new:
        # Вставляем fontenc перед babel
        text = text[:babel_match_new.start()] + fontenc_line + '\n' + text[babel_match_new.start():]
    
    # Удаляем лишние пустые строки
    text = re.sub(r'\n\s*\n\s*\n', '\n\n', text)
    
    return text


def _fix_broken_commands(text: str) -> str:
    r"""
    Fix common command breakages.
    
    1. Fix space after backslash: \ text -> \text
    2. Fix missing braces in fancyhdr commands: \lhead\includegraphics -> \lhead{\includegraphics}
    """
    # Fix space after backslash: \ text -> \text, \ cite -> \cite
    # Be careful not to fix escaped spaces "\ "
    # Pattern: backslash, space, letters. 
    # We want to target specific common commands that get broken.
    
    common_cmds = [
        "text", "cite", "ref", "label", "section", "chapter", "item", 
        "begin", "end", "caption", "include", "input"
    ]
    
    for cmd in common_cmds:
        # \ cmd -> \cmd
        pattern = r'\\ ' + cmd
        text = re.sub(pattern, r'\\' + cmd, text)
    
    # Fix missing braces in fancyhdr commands
    # GPT often loses {} around arguments: \lhead\includegraphics -> \lhead{\includegraphics}
    fancyhdr_cmds = ['lhead', 'chead', 'rhead', 'lfoot', 'cfoot', 'rfoot']
    for cmd in fancyhdr_cmds:
        # Pattern: \lhead followed by \ but no {
        # \lhead\something -> \lhead{\something}
        # Find \lhead followed by \command (without brace)
        pattern = re.compile(
            r'\\' + cmd + r'(\\[a-zA-Z]+)',  # \lhead followed by \command
            re.IGNORECASE
        )
        
        def add_braces(match):
            content = match.group(1)
            # Find the full extent of the argument (until end of line or next command)
            return '\\' + cmd + '{' + content
        
        # More precise fix: wrap until end of obvious argument
        # \lhead\includegraphics[...]{...} -> \lhead{\includegraphics[...]{...}}
        pattern2 = re.compile(
            r'\\' + cmd + r'(\\includegraphics(?:\[[^\]]*\])?\{[^}]+\})',
            re.IGNORECASE
        )
        text = pattern2.sub(r'\\' + cmd + r'{\1}', text)
        
    return text


def _fix_spacing_around_math(text: str) -> str:
    """Ensure proper spacing around math delimiters."""
    # Ensure space before inline math if not preceded by punctuation or open brace
    # This is a bit risky as it might change intended formatting.
    # Let's stick to safer fixes.
    
    # Fix double dollars that might have become $ $
    text = text.replace("$ $", "$$")
    
    return text


def _fix_russian_babel_artifacts(text: str) -> str:
    """Fix specific Russian language artifacts."""
    # Sometimes "Рис." (Fig.) gets messed up
    return text


def _fix_common_typos(text: str) -> str:
    """Fix common typos introduced by translation."""
    # Replace "..." with "\dots" or "…" depending on context?
    # LaTeX usually handles "..." fine.
    
    # Fix "e.g." and "i.e." spacing
    text = text.replace(r"e.g. ", r"e.g.\ ")
    text = text.replace(r"i.e. ", r"i.e.\ ")
    
    # Исправление опечаток в названиях пакетов, которые GPT иногда делает
    package_typos = {
        'pifong': 'pifont',      # Символы (ding, checkmark)
        'graphix': 'graphicx',    # Графика
        'hypperef': 'hyperref',   # Гиперссылки
        'amssymb': 'amssymb',     # AMS символы (проверка правильности)
        'amsfonts': 'amsfonts',   # AMS шрифты
    }
    
    for typo, correct in package_typos.items():
        if typo != correct:
            # Исправляем в \usepackage{...}
            text = re.sub(
                r'\\usepackage(\[[^\]]*\])?\{' + re.escape(typo) + r'\}',
                r'\\usepackage\1{' + correct + '}',
                text
            )
            # Исправляем в \RequirePackage{...}
            text = re.sub(
                r'\\RequirePackage(\[[^\]]*\])?\{' + re.escape(typo) + r'\}',
                r'\\RequirePackage\1{' + correct + '}',
                text
            )
    
    return text


def _add_section_bold_formatting(text: str) -> str:
    """Add bold formatting to section titles and document title (simplified version)."""
    
    # 1. Make section titles bold: \section{Title} -> \section{\textbf{Title}}
    def make_section_bold(match):
        cmd = match.group(1)  # section, subsection, etc.
        star = match.group(2) if match.group(2) else ''
        content = match.group(3)
        
        # Check if already bold
        if r'\textbf{' in content:
            return match.group(0)
        
        return f'\\{cmd}{star}{{\\textbf{{{content}}}}}'
    
    # Apply to section, subsection, subsubsection, chapter, part, paragraph, subparagraph
    section_pattern = re.compile(r'\\((?:sub)*(?:section|paragraph)|chapter|part)(\*?)\{([^}]+)\}')
    text = section_pattern.sub(make_section_bold, text)
    
    # 2. Make title bold: \title{...} -> \title{\textbf{...}}
    def make_title_bold(match):
        content = match.group(1)
        if r'\textbf{' in content:
            return match.group(0)
        return f'\\title{{\\textbf{{{content}}}}}'
    
    title_pattern = re.compile(r'\\title\{([^}]+)\}')
    text = title_pattern.sub(make_title_bold, text)
    
    return text


def _remove_remaining_tokens(text: str) -> str:
    """
    Remove any remaining tokens of the form <<...>> that weren't restored.
    
    These tokens can cause LaTeX compilation errors because << and >> are interpreted
    as mathematical operators. This function removes all such tokens that remain after
    the restoration process.
    """
    # Pattern to match tokens like <<TYPE_ID>> or <<КОММЕНТАРИЙ_42>>
    # Matches: << followed by any characters (non-greedy) followed by >>
    token_pattern = re.compile(r'<<[^>]+>>')
    
    matches = token_pattern.findall(text)
    if matches:
        logger.warning(f"Found {len(matches)} remaining tokens to remove: {matches[:5]}...")
        # Remove all tokens (replace with empty string)
        text = token_pattern.sub('', text)
        # Clean up any double newlines that might result from token removal
        text = re.sub(r'\n\n\n+', '\n\n', text)
    
    return text


def _fix_minted_environments(text: str) -> str:
    """Преобразует minted в безопасные конструкции, не требующие shell-escape."""
    # Удаляем \usepackage{minted} или \usepackage[...]{minted}
    text = re.sub(r'\\usepackage(\[[^\]]*\])?\{minted\}\s*(%[^\n]*)?\n?', '', text)

    # Заменяем окружения minted на verbatim
    pattern_env = re.compile(
        r'\\begin\{minted\}(?:\[[^\]]*\])?\{[^}]*\}(?P<body>.*?)\\end\{minted\}',
        re.DOTALL,
    )

    def _replace_env(match: re.Match) -> str:
        body = match.group('body')
        return '\\begin{verbatim}\n' + body + '\n\\end{verbatim}'

    text = pattern_env.sub(_replace_env, text)

    # Заменяем команды \inputminted на текстовый маркер, чтобы избежать обращений к внешним файлам
    pattern_input = re.compile(
        r'\\inputminted(?:\[[^\]]*\])?\{[^}]*\}\{(?P<file>[^}]*)\}',
    )

    def _replace_input(match: re.Match) -> str:
        filename = match.group('file') or ''
        return '\\texttt{[code from ' + filename + ' omitted]}'

    text = pattern_input.sub(_replace_input, text)

    # Удаляем одиночные конфигурационные строки вида \setminted{...},
    # которые ссылаются на пакет minted и вызывают Undefined control sequence,
    # если сам пакет уже убран.
    text = re.sub(
        r'^\s*\\setminted(\[[^\]]*\])?\{[^}]*\}\s*(%[^\n]*)?\n?',
        '',
        text,
        flags=re.MULTILINE,
    )
    return text


def _translate_abstract_heading(text: str, target_lang: str = "ru") -> str:
    """Translate 'Abstract' heading to Russian."""
    
    # Cleanup artifacts from previous runs where we accidentally injected double-backslash commands.
    # "\\renewcommand{\\abstractname}{...}" renders as a line break and prints words in the PDF.
    text = re.sub(
        r"\\\\renewcommand\{\\\\abstractname\}",
        r"\\renewcommand{\\abstractname}",
        text,
    )

    normalized_target_lang = str(target_lang or "ru").strip().lower() or "ru"
    abstract_name_by_lang = {
        "ru": "Аннотация",
        "uk": "Анотація",
        "be": "Анатацыя",
        "bg": "Резюме",
        "pl": "Streszczenie",
        "cs": "Abstrakt",
        "sk": "Abstrakt",
        "de": "Zusammenfassung",
        "fr": "Résumé",
        "es": "Resumen",
        "pt": "Resumo",
        "it": "Sommario",
        "nl": "Samenvatting",
        "sv": "Sammanfattning",
        "no": "Sammendrag",
        "da": "Resumé",
        "fi": "Tiivistelmä",
        "tr": "Özet",
        "ar": "الملخص",
        "he": "תקציר",
        "fa": "چکیده",
        "hi": "सारांश",
        "ja": "概要",
        "jp": "概要",
        "jpn": "概要",
        "zh": "摘要",
        "zhs": "摘要",
        "zht": "摘要",
        "ko": "초록",
    }
    abstract_name = abstract_name_by_lang.get(normalized_target_lang)
    if not abstract_name:
        return text

    # Only proceed if abstract environment is used
    if r'\begin{abstract}' in text or r'\begin{leapabstract}' in text:
        # 1. Ensure etoolbox is loaded for patching capabilities
        if r'{etoolbox}' not in text:
            # Try to insert after \documentclass to be safe
            match = re.search(r'\\documentclass(\[.*?\])?\{.*?\}', text)
            if match:
                text = text[:match.end()] + '\n\\usepackage{etoolbox}' + text[match.end():]
            else:
                # Fallback: find \usepackage or insert before \begin{document}
                pkg_match = re.search(r'\\usepackage', text)
                if pkg_match:
                    text = text[:pkg_match.start()] + '\\usepackage{etoolbox}\n' + text[pkg_match.start():]
        
        # 2. Add the patch commands and rename abstractname
        # We add this before \begin{document}
        doc_match = re.search(r'\\begin\{document\}', text)
        if doc_match:
            insert_pos = doc_match.start()
            
            # Define the fix block
            fixes = []
            
            # Define/override abstractname
            if re.search(r"\\renewcommand\{\\abstractname\}\{[^}]*\}", text):
                text = re.sub(
                    r"\\renewcommand\{\\abstractname\}\{[^}]*\}",
                    r"\\renewcommand{\\abstractname}{" + abstract_name + r"}",
                    text,
                    count=1,
                )
            else:
                fixes.append(r'\renewcommand{\abstractname}{' + abstract_name + r'}')
            
            # Patch \abstract command to replace "Abstract" with \abstractname
            # This handles styles (like nips_2017) that hardcode "Abstract" in the environment definition
            if r'\patchcmd{\abstract}{Abstract}' not in text:
                fixes.append(r'\makeatletter')
                fixes.append(r'\patchcmd{\abstract}{Abstract}{\abstractname}{}{}')
                fixes.append(r'\patchcmd{\abstract}{ABSTRACT}{\abstractname}{}{}')
                fixes.append(r'\makeatother')
            
            if fixes:
                text = text[:insert_pos] + '\n' + '\n'.join(fixes) + '\n' + text[insert_pos:]
    
    return text


def _fix_abstract_page_break(text: str) -> str:
    r"""
    Предотвращает перенос abstract на следующую страницу.
    
    Русский текст длиннее английского, поэтому abstract может не поместиться
    на первую страницу. Эта функция добавляет команды для управления разрывами
    страниц и сжатия пространства перед abstract.
    
    Args:
        text: LaTeX содержимое
        
    Returns:
        Исправленное содержимое
    """
    # Ищем начало abstract (leapabstract или обычный abstract)
    abstract_patterns = [
        (r'\\begin\{leapabstract\}', 'leapabstract'),
        (r'\\begin\{abstract\}', 'abstract'),
    ]
    
    for pattern, env_name in abstract_patterns:
        match = re.search(pattern, text)
        if match:
            # Находим позицию перед \begin{abstract}
            insert_pos = match.start()
            
            # Проверяем, нет ли уже команд управления разрывами
            # Смотрим на 200 символов перед abstract
            context_before = text[max(0, insert_pos - 200):insert_pos]
            
            if 'enlargethispage' not in context_before and 'samepage' not in context_before:
                # Оцениваем длину abstract (приблизительно)
                # Находим конец abstract
                end_match = re.search(r'\\end\{' + env_name + r'\}', text[insert_pos:])
                if end_match:
                    abstract_length = end_match.end()
                    # Если abstract длинный (> 1000 символов), используем более агрессивное сжатие
                    if abstract_length > 1000:
                        fixes = [
                            r'\enlargethispage{5\baselineskip}',  # Увеличиваем страницу на 5 строк
                            r'\vspace{-0.3in}',  # Сжимаем вертикальное пространство
                        ]
                    else:
                        fixes = [
                            r'\enlargethispage{3\baselineskip}',  # Увеличиваем страницу на 3 строки
                            r'\vspace{-0.2in}',  # Немного сжимаем вертикальное пространство
                        ]
                else:
                    # Если не нашли конец, используем стандартные значения
                    fixes = [
                        r'\enlargethispage{3\baselineskip}',
                        r'\vspace{-0.2in}',
                    ]
                
                fix_block = '\n'.join(fixes) + '\n'
                text = text[:insert_pos] + fix_block + text[insert_pos:]
                logger.info(f"Добавлены команды для предотвращения разрыва страницы перед {env_name}")
                break
    
    return text


def _process_tabular(spec: str, body: str) -> Optional[Tuple[str, str]]:
    """
    Analyze a tabular environment and determine if it should be converted to tabularx.
    """
    if not spec:
        return None

    column_tokens = [ch for ch in spec if ch in "lcr"]
    if not column_tokens:
        return None

    # Reject specs with unsupported tokens (anything besides l/c/r, pipes, spaces, @{...})
    temp = re.sub(r"@{[^}]*}", "", spec)
    if re.search(r"[^\s|lcr]", temp):
        return None

    rows = _extract_table_rows(body)
    if not rows:
        return None

    col_count = len(column_tokens)
    stats = [
        {"max_len": 0, "text_cells": 0, "numeric_cells": 0, "total_cells": 0}
        for _ in range(col_count)
    ]

    for row in rows:
        for idx in range(min(len(row), col_count)):
            stripped = _strip_tex_commands(row[idx])
            if not stripped:
                continue
            stats[idx]["total_cells"] += 1
            stats[idx]["max_len"] = max(stats[idx]["max_len"], len(stripped))
            if _looks_numeric(stripped):
                stats[idx]["numeric_cells"] += 1
            if _has_letters(stripped):
                stats[idx]["text_cells"] += 1

    wrap_columns: List[bool] = []
    for column in stats:
        should_wrap = (
            column["max_len"] >= 20  # Lowered threshold to catch more potential overflows
            and column["total_cells"] > 0
            and column["text_cells"] >= column["numeric_cells"]
        )
        wrap_columns.append(should_wrap)

    if not any(wrap_columns):
        return None

    new_spec = _build_wrapped_spec(spec, wrap_columns)
    if not new_spec:
        return None

    return new_spec, body


def _extract_table_rows(body: str) -> List[List[str]]:
    """Split table body into rows/cells for heuristic analysis."""
    cleaned = re.sub(r"(?<!\\)%.*", "", body)
    raw_rows = re.split(r"(?<!\\)\\\\", cleaned)

    rows: List[List[str]] = []
    for raw in raw_rows:
        row = raw.strip()
        if not row:
            continue
        row = row.replace("\\hline", "").strip()
        if not row:
            continue
        cells = [cell.strip() for cell in row.split("&")]
        if cells:
            rows.append(cells)

    return rows


def _strip_tex_commands(cell: str) -> str:
    """Remove common LaTeX commands and math to estimate text length."""
    if not cell:
        return ""
    cell = re.sub(r"\$[^$]*\$", "", cell)  # remove inline math
    cell = re.sub(r"\\[a-zA-Z*]+(?:\[[^\]]*\])?(?:\{[^{}]*\})?", "", cell)
    cell = cell.replace("{", " ").replace("}", " ")
    cell = re.sub(r"\s+", " ", cell)
    return cell.strip()


def _looks_numeric(text: str) -> bool:
    if not text:
        return False
    return bool(re.fullmatch(r"[\d\s\.\,\+\-\(\)%/]*", text))


def _has_letters(text: str) -> bool:
    return any(ch.isalpha() for ch in text)


def _build_wrapped_spec(spec: str, wrap_flags: List[bool]) -> Optional[str]:
    """Create a new column spec string with wrapped columns replaced by Y."""
    result: List[str] = []
    col_idx = 0
    i = 0

    while i < len(spec):
        ch = spec[i]
        if ch in "lcr":
            if col_idx >= len(wrap_flags):
                return None
            result.append("Y" if wrap_flags[col_idx] else ch)
            col_idx += 1
            i += 1
        elif ch in "| ":
            result.append(ch)
            i += 1
        elif ch == "@":
            closing = spec.find("}", i)
            if closing == -1:
                return None
            result.append(spec[i : closing + 1])
            i = closing + 1
        else:
            return None

    if col_idx != len(wrap_flags):
        return None

    return "".join(result)


def _ensure_tabularx_support(text: str) -> str:
    """Insert tabularx package and Y column definition in the preamble if needed."""
    additions = []
    if "\\usepackage{tabularx}" not in text:
        additions.append("\\usepackage{tabularx}")
    if "\\newcolumntype{Y}" not in text:
        additions.append("\\newcolumntype{Y}{>{\\raggedright\\arraybackslash}X}")

    if not additions:
        return text

    insert_block = "\n".join(additions) + "\n"
    doc_pos = text.find("\\begin{document}")
    if doc_pos == -1:
        return insert_block + text
    return text[:doc_pos] + insert_block + text[doc_pos:]


def _normalize_float_environments(text: str) -> str:
    r"""
    Normalize figure/table environments to avoid LaTeX float errors.

    - Converts figure*/table* to regular single-column floats (article class is single-column here)
    - Ensures default [ht] placement when missing
    - Removes stray '>' characters after \begin/\end
    - Unwraps lone { \includegraphics ... } blocks
    """
    # Некоторые классы (например, IEEEtran) действительно используют
    # двухколоночные float-окружения figure*/table*. Для таких классов
    # нельзя безнаказанно превращать figure* в figure, иначе широкие
    # формулы (как уравнение (7) в Lemma 1) начнут вылезать за границы
    # колонки и наезжать на текст соседней колонки.

    doc_class_match = re.search(r"\\documentclass(?:\[[^\]]*\])?\{([^}]+)\}", text)
    doc_class = doc_class_match.group(1).lower() if doc_class_match else ""

    # Классы с реальной двухколоночной вёрсткой (IEEEtran, acmart и др.)
    # не трогаем: для них figure* и table* нужны как есть.
    is_ieee = "ieeetran" in doc_class
    is_acmart = "acmart" in doc_class

    if not (is_ieee or is_acmart):
        # Convert \begin{figure*} -> \begin{figure}[ht] (preserve explicit options)
        def _replace_begin(match):
            env = match.group(1)
            options = match.group(2)
            if not options:
                options = "[ht]"
            return f"\\begin{{{env}}}{options}"

        text = re.sub(
            r"\\begin\{(figure|table)\*\}(\[[^\]]*\])?",
            _replace_begin,
            text,
            flags=re.IGNORECASE,
        )

        # Convert \end{figure*} -> \end{figure}
        text = re.sub(
            r"\\end\{(figure|table)\*\}",
            lambda m: f"\\end{{{m.group(1)}}}",
            text,
            flags=re.IGNORECASE,
        )

    # Fix malformed braces like \end{figure*>} — это безопасно и для figure*
    text = text.replace("{figure*>", "{figure*").replace("{table*>", "{table*")

    # Remove stray '>' after begin/end (e.g., \end{figure>})
    text = re.sub(
        r"(\\(?:begin|end)\{(?:figure|table)\*?\})>",
        r"\1",
        text,
        flags=re.IGNORECASE,
    )

    # Ensure figure/table have default [ht] if missing (after removing star)
    def _ensure_options(match):
        env = match.group(1)
        return f"\\begin{{{env}}}[ht]"

    text = re.sub(
        r"\\begin\{(figure|table)\}(?!\[[^\]]*\])",
        _ensure_options,
        text,
        flags=re.IGNORECASE,
    )

    # Unwrap `{\includegraphics...}` that appears immediately after `\begin{figure...}`.
    text = re.sub(
        r"(\\begin\{(?:figure|table)\}(?:\[[^\]]*\])?)\{\s*(\\includegraphics[^{}]*\{[^{}]*\})\s*\}",
        r"\1\n\2",
        text,
        flags=re.IGNORECASE,
    )

    # Unwrap standalone { \includegraphics ... } только если группа стоит отдельно,
    # а не является аргументом \subfigure и т.п. (проверяем, что слева пробел/начало строки)
    text = re.sub(
        r"(?<!\S)\{\s*(\\includegraphics[^{}]*\{[^{}]*\})\s*\}",
        r"\1",
        text,
    )

    return text


def _fix_multicol_includegraphics(text: str) -> str:
    """Сужает слишком широкие includegraphics в двухколоночных acmart-документах.

    Для acmart в формате sigconf ширина колонки ~\columnwidth. Если
    внутри обычных figure/table использовать width=\textwidth, картинка
    вылезает за пределы колонки и может залезать на текст.

    Осторожный фикс:
    - только если класс документа — acmart;
    - глобально заменяем width=\textwidth и k*\textwidth на \columnwidth.
    Для одноколоночных классов \textwidth == \columnwidth, так что
    поведение не меняется.
    """

    doc_class_match = re.search(r"\\documentclass(?:\[[^\]]*\])?\{([^}]+)\}", text)
    if not doc_class_match or "acmart" not in doc_class_match.group(1).lower():
        return text

    def _to_columnwidth(m: re.Match) -> str:
        # m.group(1) — это префикс '\includegraphics[...width='
        return m.group(1) + r"\columnwidth"

    # width=\textwidth -> width=\columnwidth
    text = re.sub(
        r"(\\includegraphics\[[^]]*width=)\\textwidth",
        _to_columnwidth,
        text,
    )

    # width=1.0\textwidth, 0.99\textwidth etc. -> \columnwidth
    text = re.sub(
        r"(\\includegraphics\[[^]]*width=)[0-9.]+\\textwidth",
        _to_columnwidth,
        text,
    )

    return text


def _normalize_table_widths(text: str) -> str:
    """
    Convert overly wide tables into tabularx environments with wrapping columns.
    """
    pattern = re.compile(
        r"\\begin\{tabular\}(\[[^\]]*\])?\{(?P<spec>[^}]*)\}(?P<body>.*?)\\end\{tabular\}",
        re.DOTALL,
    )

    tabularx_needed = False
    adjustbox_needed = False

    def _replace_table(match: re.Match) -> str:
        nonlocal tabularx_needed, adjustbox_needed
        opt = match.group(1) or ""
        spec = match.group("spec")
        body = match.group("body")

        processed = _process_tabular(spec, body)
        if processed:
            new_spec, new_body = processed
            tabularx_needed = True
            begin = f"\\begin{{tabularx}}{{\\textwidth}}{opt}{{{new_spec}}}"
            end = "\\end{tabularx}"
            return begin + new_body + end
            
        # If not converted to tabularx, check if it's wide and needs resizing
        if _is_wide_table(spec):
            adjustbox_needed = True
            # Use adjustbox to constrain width while preserving aspect ratio
            begin = f"\\begin{{adjustbox}}{{max width=\\textwidth}}\n\\begin{{tabular}}{opt}{{{spec}}}"
            end = f"\\end{{tabular}}\n\\end{{adjustbox}}"
            return begin + body + end

        return match.group(0)

    new_text = pattern.sub(_replace_table, text)

    if tabularx_needed:
        new_text = _ensure_tabularx_support(new_text)
        
    if adjustbox_needed:
        new_text = _ensure_adjustbox_support(new_text)

    return new_text


def _is_wide_table(spec: str) -> bool:
    """
    Determine if a table is likely to be wide based on column count.
    """
    # Count base columns (l, c, r)
    # Ignore @{} expressions which affect spacing but not column count directly
    cleaned_spec = re.sub(r"@{[^}]*}", "", spec)
    column_tokens = [ch for ch in cleaned_spec if ch in "lcr"]
    
    # Heuristic: Tables with 6 or more columns often overflow portrait pages
    # especially with numeric data or headers.
    return len(column_tokens) >= 6


def _ensure_adjustbox_support(text: str) -> str:
    """Ensure adjustbox package is loaded."""
    if "\\usepackage{adjustbox}" in text:
        return text
        
    addition = "\\usepackage{adjustbox}\n"
    
    # Try to insert with other packages
    doc_pos = text.find("\\begin{document}")
    if doc_pos == -1:
        return addition + text
        
    # Insert before \begin{document}
    return text[:doc_pos] + addition + text[doc_pos:]


def _fix_bibliography_spacing(text: str) -> str:
    """
    Prevent vertical stretching of bibliography items by adding \\raggedbottom
    before the bibliography environment.
    """
    if r'\begin{thebibliography}' in text:
        # Check if \raggedbottom is already there to avoid duplication
        if r'\raggedbottom' not in text:
             text = text.replace(r'\begin{thebibliography}', r'\raggedbottom' + '\n' + r'\begin{thebibliography}')
    return text


def _fix_nobibliography_command(text: str) -> str:
    """
    Удаляет команду \\nobibliography* которая подавляет вывод библиографии.
    
    Команда \\nobibliography* из пакета bibentry предназначена для inline-ссылок
    с помощью \\bibentry{}, но она подавляет вывод раздела библиографии.
    Если документ использует обычные команды цитирования (\\cite, \\citep),
    то \\nobibliography* вызывает проблему: ссылки показываются как "?".
    
    Решение: удалить \\nobibliography* и оставить \\bibliography{...} для
    корректного вывода списка литературы.
    """
    # Удаляем \nobibliography* (с возможными пробелами/переводами строк до \bibliography)
    # Паттерн: \nobibliography* с возможным переводом строки
    text = re.sub(r'\\nobibliography\*\s*\n?', '', text)
    
    # Также обрабатываем случай без звёздочки (менее распространённый)
    # \nobibliography{...} также подавляет вывод, но загружает указанный .bib файл
    # В этом случае заменяем на \bibliography{...}
    text = re.sub(r'\\nobibliography(\{[^}]+\})', r'\\bibliography\1', text)
    
    return text


def _fix_combining_diacritics(text: str) -> str:
    """
    Удаляет комбинирующие диакритические знаки, которые вызывают ошибки pdfLaTeX.
    
    Проблема: GPT иногда генерирует текст с комбинирующими символами Unicode
    (например, U+0301 COMBINING ACUTE ACCENT), которые pdfLaTeX не может обработать
    и выдаёт ошибку "Unicode character Ì (U+0301)".
    
    Решение: удалить все комбинирующие диакритики (Mark, Nonspacing категория).
    Для русского текста ударения обычно не нужны.
    """
    # Находим и удаляем все комбинирующие символы (категория 'Mn')
    result = []
    removed_count = 0
    
    for char in text:
        if unicodedata.category(char) == 'Mn':
            removed_count += 1
            continue  # Пропускаем комбинирующий символ
        result.append(char)
    
    if removed_count > 0:
        logger.info(f"Удалено {removed_count} комбинирующих диакритических знаков")
    
    return ''.join(result)


def _fix_duplicate_end_document(text: str) -> str:
    r"""
    Удаляет дублирующиеся команды \end{document}, оставляя только последнюю.
    
    Проблема: GPT иногда добавляет лишние \end{document} в середину документа
    при переводе чанков, особенно для чанков типа "intro" которые содержат
    \begin{document} и \maketitle. Это приводит к тому, что LaTeX игнорирует
    всё содержимое после первого \end{document}.
    
    Решение: найти все \end{document} и оставить только последний.
    """
    # Ищем все \end{document}
    pattern = r'\\end\{document\}'
    matches = list(re.finditer(pattern, text))
    
    if len(matches) <= 1:
        # Нет дубликатов или только один \end{document}
        return text
    
    # Есть дубликаты - удаляем все кроме последнего
    logger.warning(f"Найдено {len(matches)} команд \\end{{document}}, удаляем лишние (оставляем последний)")
    
    # Удаляем все кроме последнего, идя с конца чтобы не сбить индексы
    for match in reversed(matches[:-1]):
        start = match.start()
        end = match.end()
        # Также удаляем пустые строки после \end{document}
        while end < len(text) and text[end] in '\n\r':
            end += 1
        text = text[:start] + text[end:]
    
    return text

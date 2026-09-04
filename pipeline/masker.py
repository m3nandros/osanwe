"""
LaTeX Content Masker module for Rosetta v3.

Responsible for "Aggressive Masking" - replacing non-translatable elements
(formulas, specialized graphics, code, etc.) with safe tokens before translation.
"""

import re
import os
import time
from typing import Tuple, Dict, List, Optional, Any
from dataclasses import dataclass

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class MaskedContent:
    """Container for masked content and the mapping to restore it."""
    text: str
    token_map: Dict[str, str]
    stats: Dict[str, int]


class ContentMasker:
    r"""
    Handles masking of LaTeX content to protect sensitive elements from translation.
    
    Masks:
    1. Comments (% ...)
    2. Verbatim/Listings environments
    3. Specialized packages (TikZ, xy-pic, etc.)
    4. Math formulas (inline and display)
    5. Images (\includegraphics)
    6. Complex commands (\cite, \ref, \url, etc.)
    """
    
    # Token templates
    TOKEN_TEMPLATE = "<<{type}_{id}>>"
    
    # Regex patterns
    # Note: These are simplified patterns. For full robustness with nested structures,
    # we use a recursive approach or a proper parser where possible.
    
    def __init__(self):
        """Initialize the masker."""
        self.logger = get_logger(__name__)
        self._max_total_sec = int(os.environ.get("ROSETTA_MASKER_MAX_TOTAL_SEC", "300") or "300")
        
    def mask_content(self, latex_content: str) -> MaskedContent:
        """
        Main entry point to mask LaTeX content.
        
        Args:
            latex_content: Raw LaTeX string
            
        Returns:
            MaskedContent object with masked text and token map
        """
        self.logger.info("Starting aggressive content masking...")

        start_total = time.time()
        deadline = start_total + max(1, int(self._max_total_sec))
        
        text = latex_content
        token_map = {}
        stats = {
            "comments": 0,
            "verbatim": 0,
            "specialized": 0,
            "math_display": 0,
            "math_inline": 0,
            "images": 0,
            "commands": 0
        }
        
        # 1. Mask Comments (First to avoid masking inside comments)
        # We need to be careful not to mask \% (escaped percent)
        # Pattern: (?<!\\)% .+$
        text, count = self._mask_regex(
            text, 
            r'(?<!\\)%.*$', 
            "COMMENT", 
            token_map,
            flags=re.MULTILINE
        )
        stats["comments"] = count
        self.logger.info(f"Masking step comments done in {round(time.time() - start_total, 2)}s")
        if time.time() > deadline:
            self.logger.warning("Aggressive masking reached time budget after comments; returning partial masking result")
            return MaskedContent(text, token_map, stats)
        
        # 2. Mask Verbatim/Listings (Code blocks)
        # \begin{verbatim}...\end{verbatim}
        # \begin{lstlisting}...\end{lstlisting}
        # \verb|...| or \verb+...+
        text, count = self._mask_environments(
            text, 
            ["verbatim", "lstlisting", "minted"], 
            "CODE", 
            token_map
        )
        stats["verbatim"] = count
        self.logger.info(f"Masking step verbatim done in {round(time.time() - start_total, 2)}s")
        if time.time() > deadline:
            self.logger.warning("Aggressive masking reached time budget after verbatim; returning partial masking result")
            return MaskedContent(text, token_map, stats)

        # 2.5 Mask inline verbatim commands (\verb, \lstinline)
        # These are delimiter-based and often contain ASCII/code where translation can break LaTeX.
        text, count_inline_verb = self._mask_delimited_command(text, "verb", "CODE", token_map)
        text, count_inline_lst = self._mask_delimited_command(text, "lstinline", "CODE", token_map)
        stats["verbatim"] = stats["verbatim"] + count_inline_verb + count_inline_lst
        self.logger.info(f"Masking step inline verbatim done in {round(time.time() - start_total, 2)}s")
        if time.time() > deadline:
            self.logger.warning("Aggressive masking reached time budget after inline verbatim; returning partial masking result")
            return MaskedContent(text, token_map, stats)
        
        # 3. Mask Specialized Packages (TikZ, etc.)
        # These must be masked BEFORE math to avoid partial matching
        specialized_envs = [
            'tikzpicture', 'pgfpicture', 'xymatrix', 'xy', 'pspicture',
            'circuitikz', 'tikzcd', 'forest'
        ]
        text, count = self._mask_environments(
            text, 
            specialized_envs, 
            "TIKZ", 
            token_map
        )
        stats["specialized"] = count
        self.logger.info(f"Masking step specialized envs done in {round(time.time() - start_total, 2)}s")
        if time.time() > deadline:
            self.logger.warning("Aggressive masking reached time budget after specialized envs; returning partial masking result")
            return MaskedContent(text, token_map, stats)
        
        # 4. Mask Images
        # \includegraphics[...]{...}
        text, count = self._mask_command(
            text,
            "includegraphics",
            "IMG",
            token_map
        )
        stats["images"] = count
        self.logger.info(f"Masking step images done in {round(time.time() - start_total, 2)}s")
        if time.time() > deadline:
            self.logger.warning("Aggressive masking reached time budget after images; returning partial masking result")
            return MaskedContent(text, token_map, stats)
        
        # 5. Mask Display Math
        # $$...$$, \[...\], \begin{equation}...\end{equation}
        
        # 5.1 \begin{equation*}...
        math_envs = [
            'equation', 'equation*', 'align', 'align*', 'alignat', 'alignat*',
            'multline', 'multline*', 'gather', 'gather*', 'flalign', 'flalign*',
            'eqnarray', 'eqnarray*', 'split', 'cases',
            'IEEEeqnarray', 'IEEEeqnarray*'
        ]
        text, count_env = self._mask_environments(
            text,
            math_envs,
            "MATH_DISP",
            token_map
        )
        if time.time() > deadline:
            self.logger.warning("Aggressive masking reached time budget during display math envs; returning partial masking result")
            stats["math_display"] = count_env
            return MaskedContent(text, token_map, stats)
        
        # 5.2 \[ ... \]
        text, count_bracket = self._mask_balanced_pair(
            text,
            r'\\\[',
            r'\\\]',
            "MATH_DISP",
            token_map
        )
        if time.time() > deadline:
            self.logger.warning("Aggressive masking reached time budget during \\[..\\]; returning partial masking result")
            stats["math_display"] = count_env + count_bracket
            return MaskedContent(text, token_map, stats)
        
        # 5.3 $$ ... $$
        text, count_dollars = self._mask_regex(
            text,
            r'\$\$(?s:.*?)\$\$',
            "MATH_DISP",
            token_map
        )
        
        stats["math_display"] = count_env + count_bracket + count_dollars
        self.logger.info(f"Masking step display math done in {round(time.time() - start_total, 2)}s")
        if time.time() > deadline:
            self.logger.warning("Aggressive masking reached time budget after display math; returning partial masking result")
            return MaskedContent(text, token_map, stats)
        
        # 6. Mask Inline Math
        # $ ... $, \( ... \)
        
        # 6.1 \( ... \)
        text, count_paren = self._mask_balanced_pair(
            text,
            r'\\\(',
            r'\\\)',
            "MATH_INLINE",
            token_map
        )
        if time.time() > deadline:
            self.logger.warning("Aggressive masking reached time budget during \\(..\\); returning partial masking result")
            stats["math_inline"] = count_paren
            return MaskedContent(text, token_map, stats)
        
        # 6.2 $ ... $ (Non-greedy, single line or small multiline)
        # We use a negative lookbehind/lookahead to ensure we don't match $$
        # Pattern is tricky because of escaped dollars inside.
        # Simplified: \$[^$]+\$
        text, count_dollar = self._mask_inline_math_dollars(
            text,
            "MATH_INLINE",
            token_map,
        )
        
        stats["math_inline"] = count_paren + count_dollar
        self.logger.info(f"Masking step inline math done in {round(time.time() - start_total, 2)}s")
        if time.time() > deadline:
            self.logger.warning("Aggressive masking reached time budget after inline math; returning partial masking result")
            return MaskedContent(text, token_map, stats)
        
        # 7. Mask Complex Commands (Citations, Refs, URLs)
        # \cite{...}, \ref{...}, \url{...}, \href{...}{...}
        
        # 7.1 Simple commands: \cite{...}, \ref{...}, \label{...}, \url{...}, \doi{...}
        simple_cmds = ["cite", "citep", "citet", "ref", "label", "url", "doi", "eqref", "autoref"]
        count_cmds = 0
        for cmd in simple_cmds:
            text, c = self._mask_command(text, cmd, "CMD", token_map)
            count_cmds += c
            if time.time() > deadline:
                self.logger.warning("Aggressive masking reached time budget during command masking; returning partial masking result")
                stats["commands"] = count_cmds
                return MaskedContent(text, token_map, stats)
            
        # 7.2 \href{url}{text} - we only mask the URL part if possible, or the whole thing?
        # Strategy: Mask the whole \href{...}{...} as it's safer, or mask just the first arg?
        # For now, let's mask the whole thing to be safe, or maybe just \href{...} and leave the second brace?
        # Actually, \href{url}{text} -> text should be translated.
        # So we should mask \href{url} and leave {text}.
        # But parsing that with regex is hard.
        # Let's try to mask \href{...} but leave the content block exposed?
        # No, standard approach: mask \href{url} as a token, then the text follows.
        # But \href takes two arguments.
        # Let's skip complex \href masking for now and treat it as a command to be careful with.
        # Or mask the whole thing if it's just a link.
        
        stats["commands"] = count_cmds
        self.logger.info(f"Masking step commands done in {round(time.time() - start_total, 2)}s")
        
        self.logger.info(f"Masking complete. Stats: {stats}")
        return MaskedContent(text, token_map, stats)

    def _mask_regex(self, text: str, pattern: str, type_prefix: str, 
                   token_map: Dict[str, str], flags=0) -> Tuple[str, int]:
        """Helper to mask content based on regex."""
        count = 0
        
        def replace_func(match):
            nonlocal count
            content = match.group(0)
            # Check if already masked (optimization)
            if content.startswith("<<") and content.endswith(">>"):
                return content
                
            token = self.TOKEN_TEMPLATE.format(type=type_prefix, id=len(token_map))
            token_map[token] = content
            count += 1
            return token
            
        new_text = re.sub(pattern, replace_func, text, flags=flags)
        return new_text, count

    def _mask_environments(self, text: str, env_names: List[str], type_prefix: str, 
                          token_map: Dict[str, str]) -> Tuple[str, int]:
        """
        Mask LaTeX environments: \begin{name}...\end{name}.
        Handles nested environments of the same type using a recursive approach or balanced matching.
        """
        count = 0

        # We need to handle nesting. Regex is bad at this.
        # Use a stack-based matcher for each environment name.
        for env in env_names:
            begin_re = r'\\begin\{' + re.escape(env) + r'\}'
            end_re = r'\\end\{' + re.escape(env) + r'\}'
            combined = re.compile(f"(?:{begin_re})|(?:{end_re})")

            stack: List[int] = []
            spans: List[Tuple[int, int]] = []

            for m in combined.finditer(text):
                token_text = m.group(0)
                if token_text.startswith("\\begin"):
                    stack.append(m.start())
                else:
                    if stack:
                        start_pos = stack.pop()
                        spans.append((start_pos, m.end()))

            if not spans:
                continue

            spans.sort(key=lambda x: x[0])
            for start_pos, end_pos in reversed(spans):
                full_match = text[start_pos:end_pos]
                token = self.TOKEN_TEMPLATE.format(type=type_prefix, id=len(token_map))
                token_map[token] = full_match
                text = text[:start_pos] + token + text[end_pos:]
                count += 1

        return text, count

    def _mask_delimited_command(
        self,
        text: str,
        cmd_name: str,
        type_prefix: str,
        token_map: Dict[str, str],
    ) -> Tuple[str, int]:
        """Mask delimiter-based commands like \verb|...| or \lstinline! ... !"""
        n = len(text)
        i = 0
        out_parts: List[str] = []
        last = 0
        count = 0
        needle = "\\" + cmd_name

        while i < n:
            j = text.find(needle, i)
            if j < 0:
                break

            k = j + len(needle)
            if k >= n:
                i = k
                continue

            # Skip if it's something like \verb* (star variant)
            if text[k] == '*':
                k += 1
                if k >= n:
                    i = k
                    continue

            delim = text[k]
            # Delimiter must be a non-whitespace single char
            if delim.isspace():
                i = k + 1
                continue

            # Find closing delimiter (unescaped)
            m = k + 1
            while m < n:
                if text[m] != delim:
                    m += 1
                    continue
                # Treat escaped delimiter as literal
                if m > 0 and text[m - 1] == '\\':
                    m += 1
                    continue
                break
            if m >= n:
                i = k + 1
                continue

            full_cmd = text[j : m + 1]
            out_parts.append(text[last:j])
            token = self.TOKEN_TEMPLATE.format(type=type_prefix, id=len(token_map))
            token_map[token] = full_cmd
            out_parts.append(token)
            count += 1
            last = m + 1
            i = last

        if last == 0:
            return text, 0

        out_parts.append(text[last:])
        return "".join(out_parts), count

    def _mask_balanced_pair(self, text: str, open_pat: str, close_pat: str, 
                           type_prefix: str, token_map: Dict[str, str]) -> Tuple[str, int]:
        """Mask content between balanced delimiters (e.g. \\[ ... \\])."""
        # Regex approach for non-nested:
        pattern = f"{open_pat}(?s:.*?){close_pat}"
        return self._mask_regex(text, pattern, type_prefix, token_map)

    def _mask_inline_math_dollars(
        self,
        text: str,
        type_prefix: str,
        token_map: Dict[str, str],
        max_span_chars: int = 5000,
    ) -> Tuple[str, int]:
        """Mask inline math delimited by single $...$ in linear time."""
        n = len(text)
        i = 0
        out_parts: List[str] = []
        last = 0
        count = 0

        def _is_escaped(pos: int) -> bool:
            # Count consecutive backslashes immediately preceding pos
            bs = 0
            k = pos - 1
            while k >= 0 and text[k] == '\\':
                bs += 1
                k -= 1
            return (bs % 2) == 1

        while i < n:
            if text[i] != '$':
                i += 1
                continue

            # Skip escaped dollars (\$)
            if _is_escaped(i):
                i += 1
                continue

            # Skip display math $$
            if i + 1 < n and text[i + 1] == '$':
                i += 2
                continue

            # Find closing single $
            j = i + 1
            span_limit = min(n, i + 1 + max_span_chars)
            found = False
            while j < span_limit:
                if text[j] != '$':
                    j += 1
                    continue
                if _is_escaped(j):
                    j += 1
                    continue
                if j + 1 < n and text[j + 1] == '$':
                    # Don't treat start of $$ as end of inline math
                    j += 2
                    continue
                found = True
                break

            if not found:
                i += 1
                continue

            # Emit prefix + token for $...$
            out_parts.append(text[last:i])
            full_match = text[i : j + 1]
            token = self.TOKEN_TEMPLATE.format(type=type_prefix, id=len(token_map))
            token_map[token] = full_match
            out_parts.append(token)
            count += 1
            last = j + 1
            i = last

        if last == 0:
            return text, 0

        out_parts.append(text[last:])
        return "".join(out_parts), count

    def _mask_command(self, text: str, cmd_name: str, type_prefix: str, 
                     token_map: Dict[str, str]) -> Tuple[str, int]:
        """
        Mask a command like \cmd{arg} or \cmd[opt]{arg}.
        Handles nested braces in arguments.
        """
        count = 0
        pattern = re.compile(r'\\' + re.escape(cmd_name) + r'(?:\[|\{)')

        while True:
            match = pattern.search(text)
            if not match:
                break

            start_pos = match.start()

            # Now we need to parse arguments to find the end of the command
            # We'll use a simple brace counter
            pos = start_pos + len(cmd_name) + 1  # Skip \cmdname

            # Check for optional arguments [ ... ]
            if pos < len(text) and text[pos] == '[':
                # Scan until matching ]
                pos += 1
                bracket_depth = 1
                while pos < len(text) and bracket_depth > 0:
                    if text[pos] == '[':
                        bracket_depth += 1
                    elif text[pos] == ']':
                        bracket_depth -= 1
                    pos += 1

            # Check for required arguments { ... }
            if pos < len(text) and text[pos] == '{':
                pos += 1
                brace_depth = 1
                while pos < len(text) and brace_depth > 0:
                    if text[pos] == '{':
                        brace_depth += 1
                    elif text[pos] == '}':
                        brace_depth -= 1
                    pos += 1

            # Extract the full command
            full_cmd = text[start_pos:pos]

            token = self.TOKEN_TEMPLATE.format(type=type_prefix, id=len(token_map))
            token_map[token] = full_cmd

            text = text[:start_pos] + token + text[pos:]
            count += 1

        return text, count

    def unmask_content(self, masked_text: str, token_map: Dict[str, str]) -> str:
        """
        Restore masked content.

        Args:
            masked_text: Text with tokens
            token_map: Dictionary mapping tokens to original content

        Returns:
            Restored text
        """
        text = masked_text

        for token, original in token_map.items():
            text = text.replace(token, original)

        return text

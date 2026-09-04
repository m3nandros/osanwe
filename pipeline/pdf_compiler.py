"""
PDF Compiler module for Rosetta v3.

Responsible for compiling LaTeX to PDF with iterative error fixing (Surgical Fixer).
Uses pdflatex and analyzes logs to detect and fix errors.
"""

import os
import subprocess
import re
import shutil
import unicodedata
from pathlib import Path
from typing import Optional, Tuple, List
from dataclasses import dataclass

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class CompilationResult:
    """Result of the compilation process."""
    success: bool
    pdf_path: Optional[Path]
    log_path: Optional[Path]
    errors: List[str]
    attempts: int


class PDFCompiler:
    """
    Handles LaTeX compilation and error fixing.
    """
    
    def __init__(self, max_attempts: int = 3):
        """
        Initialize the compiler.
        
        Args:
            max_attempts: Maximum number of compilation attempts with fixing
        """
        self.max_attempts = max_attempts
        self.logger = get_logger(__name__)
        
    def compile_pdf(self, tex_path: Path, output_dir: Optional[Path] = None) -> CompilationResult:
        """
        Compile LaTeX file to PDF.
        
        Args:
            tex_path: Path to the .tex file
            output_dir: Optional output directory (defaults to tex file's dir)
            
        Returns:
            CompilationResult object
        """
        self.logger.info(f"Compiling {tex_path}...")
        
        if not output_dir:
            output_dir = tex_path.parent
        output_dir = output_dir.resolve()
            
        # Ensure output dir exists
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Очищаем старые временные файлы от предыдущих попыток
        self._cleanup_temp_files(output_dir, tex_path.stem)
        
        current_tex_path = tex_path
        errors = []
        # Если после surgical fix мы компилируем *_fixed_N.tex, то .aux/.bcf/.bbl имеют другой stem.
        # Поэтому отслеживаем, для какого stem уже запускали BibTeX/Biber.
        bibtex_ran_for_stem: Optional[str] = None
        biber_ran_for_stem: Optional[str] = None
        temp_files_to_cleanup = []  # Список временных файлов для удаления
        
        max_attempts = self.max_attempts
        extra_retry_used = False

        engine = self._select_latex_engine()
        # If the source already requires XeLaTeX/LuaLaTeX (e.g., contains fontspec),
        # starting with pdfLaTeX will fail immediately. Prefer XeLaTeX in that case.
        if engine == "pdflatex" and self._tex_requires_xetex(tex_path):
            if shutil.which("xelatex"):
                engine = "xelatex"
            elif shutil.which("lualatex"):
                engine = "lualatex"
            self.logger.info(f"Selected Unicode-capable engine: {engine}")

        current_tex_path = self._maybe_patch_unicode_preamble(engine, current_tex_path, output_dir)

        attempt = 1
        while attempt <= max_attempts:
            self.logger.info(f"Compilation attempt {attempt}/{max_attempts}")
            
            # Run LaTeX engine
            success, log_content = self._run_latex(engine, current_tex_path, output_dir)

            if (not success) and self._is_unicode_character_error(log_content) and engine == "pdflatex":
                # pdfLaTeX can't handle arbitrary Unicode (e.g., CJK). Retry with XeLaTeX.
                xelatex = shutil.which("xelatex")
                if xelatex:
                    self.logger.warning("Detected Unicode character errors with pdflatex; retrying with xelatex")
                    engine = "xelatex"
                    current_tex_path = self._maybe_patch_unicode_preamble(engine, current_tex_path, output_dir)
                    success, log_content = self._run_latex(engine, current_tex_path, output_dir)

            if (not success) and ("TeX capacity exceeded" in (log_content or "")) and engine == "xelatex":
                # Some RTL combinations (polyglossia/bidi + certain templates) can overflow XeTeX input stack.
                # LuaLaTeX often avoids this class of failure.
                lualatex = shutil.which("lualatex")
                if lualatex:
                    self.logger.warning("Detected TeX capacity exceeded with xelatex; retrying with lualatex")
                    engine = "lualatex"
                    current_tex_path = self._maybe_patch_unicode_preamble(engine, current_tex_path, output_dir)
                    success, log_content = self._run_latex(engine, current_tex_path, output_dir)
            
            # Запускаем BibTeX/Biber после первой компиляции (независимо от успеха)
            # Для BibTeX/Biber нужен .aux/.bcf, который создаётся даже при ошибках.
            aux_path = output_dir / current_tex_path.with_suffix('.aux').name
            bcf_path = output_dir / f"{aux_path.stem}.bcf"

            if biber_ran_for_stem != aux_path.stem:
                if self._needs_biber(bcf_path, log_content):
                    self.logger.info("Detected biblatex/biber, running biber...")
                    biber_success = self._run_biber(aux_path.stem, output_dir)
                    biber_ran_for_stem = aux_path.stem
                    bibtex_ran_for_stem = aux_path.stem
                    if biber_success:
                        self.logger.info("Biber successful, running LaTeX pass 1/2 for bibliography...")
                        success, log_content = self._run_latex(engine, current_tex_path, output_dir)
                        self.logger.info("Running LaTeX pass 2/2 for citation resolution...")
                        success, log_content = self._run_latex(engine, current_tex_path, output_dir)
                    else:
                        self.logger.warning("Biber failed, citations may be missing.")

            if bibtex_ran_for_stem != aux_path.stem:
                if self._needs_bibtex(aux_path):
                    bib_success = False
                    if self._referenced_bib_files_exist(aux_path, output_dir):
                        self.logger.info("Detected bibliography, running bibtex...")
                        bib_success = self._run_bibtex(aux_path.stem, output_dir)
                        if not bib_success:
                            fallback_bbl = self._find_fallback_bbl(output_dir, aux_path.stem)
                            if fallback_bbl and self._install_fallback_bbl(output_dir, aux_path.stem, fallback_bbl):
                                self.logger.info(
                                    f"BibTeX failed but found prebuilt .bbl fallback ({fallback_bbl.name}); using it."
                                )
                                bib_success = True
                    else:
                        fallback_bbl = self._find_fallback_bbl(output_dir, aux_path.stem)
                        if fallback_bbl and self._install_fallback_bbl(output_dir, aux_path.stem, fallback_bbl):
                            self.logger.info(
                                f"No .bib files found for BibTeX, but found prebuilt .bbl ({fallback_bbl.name}); using it."
                            )
                            bib_success = True
                        else:
                            self.logger.warning(
                                "Detected bibliography but no .bib files were found and no prebuilt .bbl fallback is available; citations may be missing."
                            )

                    bibtex_ran_for_stem = aux_path.stem
                    if bib_success:
                        # После bibtex/подстановки .bbl нужно 2 прохода LaTeX для разрешения всех ссылок
                        self.logger.info("Bibliography ready, running LaTeX pass 1/2 for bibliography...")
                        success, log_content = self._run_latex(engine, current_tex_path, output_dir)
                        timed_out_pdf = (
                            "Compilation timed out" in (log_content or "") and "(PDF produced)" in (log_content or "")
                        )
                        if success and not timed_out_pdf:
                            self.logger.info("Running LaTeX pass 2/2 for citation resolution...")
                            success, log_content = self._run_latex(engine, current_tex_path, output_dir)

            # Check for undefined references or citations (need more passes)
            needs_rerun = self._needs_rerun(log_content)
            timed_out_pdf = "Compilation timed out" in (log_content or "") and "(PDF produced)" in (log_content or "")
            
            if success:
                # If successful but has undefined references, run extra passes to resolve them.
                rerun_passes = 0
                while needs_rerun and rerun_passes < 2 and not timed_out_pdf:
                    rerun_passes += 1
                    self.logger.info(
                        f"Compilation successful but found unresolved references/citations. Running extra LaTeX pass {rerun_passes}/2..."
                    )
                    prev_log_content = log_content
                    pass_success, pass_log_content = self._run_latex(engine, current_tex_path, output_dir)
                    pass_timed_out_pdf = (
                        "Compilation timed out" in (pass_log_content or "")
                        and "(PDF produced)" in (pass_log_content or "")
                    )

                    if not pass_success:
                        if pass_timed_out_pdf:
                            # Extra pass timed out, but we already have a usable PDF from the previous pass.
                            # Keep the previous successful state.
                            self.logger.warning(
                                "Extra pdflatex pass timed out; keeping previously generated PDF/log."
                            )
                            log_content = prev_log_content
                            break
                        success = False
                        log_content = pass_log_content
                        break

                    success = True
                    log_content = pass_log_content
                    needs_rerun = self._needs_rerun(log_content)
                    timed_out_pdf = "Compilation timed out" in (log_content or "") and "(PDF produced)" in (log_content or "")

                self.logger.info("Compilation successful!")

                # Определяем реальный PDF, созданный последней успешной попыткой
                final_pdf_path = output_dir / current_tex_path.with_suffix('.pdf').name
                canonical_pdf_path = output_dir / tex_path.with_suffix('.pdf').name

                final_log_path = output_dir / current_tex_path.with_suffix('.log').name
                canonical_log_path = output_dir / tex_path.with_suffix('.log').name

                # Если компиляция шла по *_fixed_N.tex, копируем результат в canonical имя (translated.pdf и т.п.)
                if final_pdf_path.exists() and final_pdf_path != canonical_pdf_path:
                    try:
                        shutil.copy2(final_pdf_path, canonical_pdf_path)
                        self.logger.info(
                            f"Copied final PDF {final_pdf_path.name} -> {canonical_pdf_path.name}"
                        )
                    except Exception as e:
                        self.logger.warning(
                            f"Failed to copy final PDF from {final_pdf_path} to {canonical_pdf_path}: {e}"
                        )

                if final_log_path.exists() and final_log_path != canonical_log_path:
                    try:
                        shutil.copy2(final_log_path, canonical_log_path)
                    except Exception as e:
                        self.logger.warning(
                            f"Failed to copy final log from {final_log_path} to {canonical_log_path}: {e}"
                        )

                # Если canonical существует, возвращаем его, иначе — фактический путь
                pdf_path = canonical_pdf_path if canonical_pdf_path.exists() else final_pdf_path

                log_path = canonical_log_path if canonical_log_path.exists() else (final_log_path if final_log_path.exists() else None)

                # Очищаем временные файлы при успешной компиляции (canonical_pdf_path не трогаем)
                return CompilationResult(True, pdf_path, log_path, [], attempt)
            
            # Analyze errors
            current_errors = self._parse_log_errors(log_content)
            errors.extend(current_errors)
            self.logger.warning(f"Compilation failed with {len(current_errors)} errors.")
            
            can_attempt_fix = (attempt < max_attempts) or ((attempt == max_attempts) and (not extra_retry_used))
            if can_attempt_fix:
                # Try to fix errors
                self.logger.info("Attempting surgical fix...")
                fixed_content = self._surgical_fix(current_tex_path, current_errors)

                if fixed_content:
                    # Save fixed content to new file
                    fixed_tex_path = output_dir / f"{tex_path.stem}_fixed_{attempt}.tex"
                    with open(fixed_tex_path, 'w', encoding='utf-8') as f:
                        f.write(fixed_content)
                    current_tex_path = fixed_tex_path
                    temp_files_to_cleanup.append(fixed_tex_path)  # Добавляем в список для очистки

                    # If we applied a fix on the last configured attempt, allow one extra retry.
                    if (attempt == max_attempts) and (not extra_retry_used):
                        extra_retry_used = True
                        max_attempts += 1

                    attempt += 1
                    continue

                self.logger.warning("Could not apply any fixes. Stopping.")
                break

            self.logger.error("Max attempts reached. Compilation failed.")
            break

            attempt += 1
        
        if temp_files_to_cleanup:
            self.logger.info("Keeping temporary *_fixed_* files for debugging (compilation failed).")
                
        return CompilationResult(False, None, None, errors, self.max_attempts)

    def _run_pdflatex(self, tex_path: Path, output_dir: Path) -> Tuple[bool, str]:
        """Run pdflatex command."""
        return self._run_latex("pdflatex", tex_path, output_dir)

    def _run_latex(self, engine: str, tex_path: Path, output_dir: Path) -> Tuple[bool, str]:
        tex_arg = str(tex_path)
        try:
            tex_resolved = tex_path.resolve()
            out_resolved = output_dir.resolve()
            if tex_resolved.parent == out_resolved:
                tex_arg = tex_resolved.name
            else:
                tex_arg = str(tex_resolved)
        except Exception:
            tex_arg = str(tex_path)

        cmd = [
            engine,
            "-interaction=nonstopmode",
            f"-output-directory={output_dir}",
            tex_arg,
        ]

        timeout_sec = int(os.environ.get("ROSETTA_PDFLATEX_TIMEOUT_SEC", "60") or "60")
        if timeout_sec < 10:
            timeout_sec = 60

        try:
            result = subprocess.run(
                cmd,
                cwd=output_dir,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=timeout_sec,
            )

            log_path = output_dir / tex_path.with_suffix('.log').name
            log_content = ""
            if log_path.exists():
                try:
                    with open(log_path, 'r', encoding='latin-1', errors='replace') as f:
                        log_content = f.read()
                except Exception as e:
                    self.logger.warning(f"Could not read log file: {e}")
                    log_content = ""

            pdf_path = output_dir / tex_path.with_suffix('.pdf').name
            success = result.returncode == 0
            if (not success) and pdf_path.exists() and log_content:
                if (
                    ("Output written on" in log_content)
                    and ("Fatal error occurred" not in log_content)
                    and ("Emergency stop" not in log_content)
                    and ("no output PDF file produced" not in log_content)
                ):
                    success = True

            return success, log_content

        except subprocess.TimeoutExpired:
            log_path = output_dir / tex_path.with_suffix('.log').name
            log_content = ""
            if log_path.exists():
                try:
                    with open(log_path, 'r', encoding='latin-1', errors='replace') as f:
                        log_content = f.read()
                except Exception as e:
                    self.logger.warning(f"Could not read log file after timeout: {e}")
                    log_content = ""

            self.logger.error(f"Compilation timed out after {timeout_sec}s")
            marker = f"\n! Compilation timed out after {timeout_sec}s\n"
            try:
                if log_path.exists():
                    with open(log_path, 'a', encoding='latin-1', errors='replace') as f:
                        f.write(marker)
            except Exception:
                pass

            pdf_path = output_dir / tex_path.with_suffix('.pdf').name
            if pdf_path.exists():
                return True, (log_content + marker + "(PDF produced)")

            return False, (log_content + marker)

        except Exception as e:
            self.logger.error(f"Compilation error: {e}")
            return False, ""

    def _select_latex_engine(self) -> str:
        forced = (os.environ.get("ROSETTA_LATEX_ENGINE") or "").strip().lower()
        if forced:
            return forced
        return "pdflatex"

    def _tex_requires_xetex(self, tex_path: Path) -> bool:
        try:
            name = tex_path.name.lower()
            if name.endswith("_unicode.tex") or "unicode" in name:
                return True
            # Read a limited prefix; preamble always appears early.
            s = tex_path.read_text(encoding="utf-8", errors="replace")
            prefix = s[:120000]
            needles = (
                "\\usepackage{fontspec}",
                "\\setmainfont",
                "\\usepackage{xeCJK}",
                "\\XeTeXlinebreaklocale",
            )
            return any(n in prefix for n in needles)
        except Exception:
            return False

    def _is_unicode_character_error(self, log_content: str) -> bool:
        if not log_content:
            return False
        return ("LaTeX Error" in log_content) and ("Unicode character" in log_content)

    def _maybe_patch_unicode_preamble(self, engine: str, tex_path: Path, output_dir: Path) -> Path:
        e = (engine or "").strip().lower()
        if e not in ("xelatex", "lualatex"):
            return tex_path

        try:
            raw = Path(tex_path).read_text(encoding="utf-8", errors="replace")
        except Exception:
            return tex_path

        if "\\usepackage{fontspec}" in raw or "\\usepackage[no-math]{fontspec}" in raw:
            return tex_path

        if not self._has_non_ascii_letters(raw):
            return tex_path

        patched = self._patch_unicode_preamble(raw)
        if patched == raw:
            return tex_path

        try:
            base = Path(tex_path).stem
            suffix = Path(tex_path).suffix or ".tex"
            out_path = (output_dir / f"{base}_unicode{suffix}").resolve()
            out_path.write_text(patched, encoding="utf-8")
            return out_path
        except Exception:
            return tex_path

    def _has_non_ascii_letters(self, s: str) -> bool:
        for ch in s:
            if ord(ch) < 128:
                continue
            if ch.isalpha():
                return True
        return False

    def _patch_unicode_preamble(self, content: str) -> str:
        t = content

        def _has_range(ranges: list[tuple[int, int]]) -> bool:
            for ch in t:
                cp = ord(ch)
                for a, b in ranges:
                    if a <= cp <= b:
                        return True
            return False

        has_hiragana_katakana = _has_range([(0x3040, 0x30FF), (0x31F0, 0x31FF)])
        has_hangul = _has_range([(0x1100, 0x11FF), (0x3130, 0x318F), (0xAC00, 0xD7AF)])
        has_cjk = _has_range([(0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF)])
        has_arabic = _has_range([(0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)])
        has_hebrew = _has_range([(0x0590, 0x05FF), (0xFB1D, 0xFB4F)])

        def _comment_pkg(pattern: str, text: str) -> str:
            lines = text.split("\n")
            out: list[str] = []
            rx = re.compile(pattern, re.IGNORECASE)
            for line in lines:
                if rx.search(line) and not line.lstrip().startswith('%'):
                    stripped = line.lstrip()
                    prefix = line[: len(line) - len(stripped)]
                    out.append(f"{prefix}% {stripped}")
                else:
                    out.append(line)
            return "\n".join(out)

        t = _comment_pkg(r"\\usepackage\[[^\]]*\]\{inputenc\}", t)
        t = _comment_pkg(r"\\usepackage\{inputenc\}", t)
        t = _comment_pkg(r"\\usepackage\[[^\]]*\]\{fontenc\}", t)
        t = _comment_pkg(r"\\usepackage\{fontenc\}", t)

        if has_arabic or has_hebrew:
            t = _comment_pkg(r"\\usepackage\[[^\]]*\]\{babel\}", t)
            t = _comment_pkg(r"\\usepackage\{babel\}", t)
            t = _comment_pkg(r"\\usepackage\[[^\]]*\]\{microtype\}", t)
            t = _comment_pkg(r"\\usepackage\{microtype\}", t)

        def _nested_if_font_exists(fonts: list[str], on_found: str, on_none: str) -> str:
            cur = on_none
            for name in reversed([x for x in fonts if x]):
                cur = f"\\IfFontExistsTF{{{name}}}{{{on_found.format(name=name)}}}{{{cur}}}"
            return cur

        preamble_lines: list[str] = []
        preamble_lines.append(r"\usepackage{fontspec}")
        preamble_lines.append(r"\defaultfontfeatures{Ligatures=TeX}")

        if has_arabic or has_hebrew:
            preamble_lines.append(r"\usepackage{polyglossia}")
            preamble_lines.append(r"\makeatletter")
            preamble_lines.append(
                r"\AtBeginDocument{"
                r"\def\enlargethispage{\futurelet\rosetta@tok\rosetta@enlargethispage@i}"
                r"\def\rosetta@enlargethispage@i{\ifx\rosetta@tok*\expandafter\rosetta@enlargethispage@star\else\expandafter\rosetta@enlargethispage@nostar\fi}"
                r"\def\rosetta@enlargethispage@star*##1{}"
                r"\def\rosetta@enlargethispage@nostar##1{}"
                r"}"
            )
            preamble_lines.append(r"\AtBeginDocument{\def\@noticestring{}\def\@notice{}}")
            preamble_lines.append(r"\makeatother")

        if has_arabic or has_hebrew:
            abstract_title = "الملخص" if has_arabic else "תקציר"
            preamble_lines.append(r"\AtBeginDocument{")
            preamble_lines.append(r"\renewenvironment{abstract}{")
            preamble_lines.append(r"\vskip 0.075in")
            preamble_lines.append(r"\centerline{{\large\bf " + abstract_title + r"}}")
            preamble_lines.append(r"\vspace{0.5ex}")
            preamble_lines.append(r"\begin{quote}")
            preamble_lines.append(r"}{")
            preamble_lines.append(r"\par")
            preamble_lines.append(r"\end{quote}")
            preamble_lines.append(r"\vskip 1ex")
            preamble_lines.append(r"}")
            preamble_lines.append(r"}")

            # Bibliography should remain LTR (it is typically Latin script even in RTL documents).
            # This does not change bibliography content, only the typesetting direction.
            preamble_lines.append(r"\makeatletter")
            preamble_lines.append(r"\AtBeginDocument{")
            preamble_lines.append(r"\let\rosetta@orig@thebibliography\thebibliography")
            preamble_lines.append(r"\let\rosetta@orig@endthebibliography\endthebibliography")
            preamble_lines.append(r"\renewenvironment{thebibliography}[1]{")
            preamble_lines.append(r"\begin{LTR}\rosetta@orig@thebibliography{##1}")
            preamble_lines.append(r"}{")
            preamble_lines.append(r"\rosetta@orig@endthebibliography\end{LTR}")
            preamble_lines.append(r"}")
            preamble_lines.append(r"}")
            preamble_lines.append(r"\makeatother")

        if has_arabic:
            preamble_lines.append(
                _nested_if_font_exists(
                    [
                        "Times New Roman",
                        "TeX Gyre Termes",
                        "STIX Two Text",
                        "Libertinus Serif",
                        "DejaVu Serif",
                        "FreeSerif",
                        "Arial Unicode MS",
                    ],
                    on_found="\\setmainfont{{{name}}}",
                    on_none="",
                )
            )
            preamble_lines.append(r"\setdefaultlanguage{arabic}")
            preamble_lines.append(
                _nested_if_font_exists(
                    [
                        "Amiri",
                        "Scheherazade New",
                        "Noto Naskh Arabic",
                        "Geeza Pro",
                        "Arial Unicode MS",
                        "Times New Roman",
                        "DejaVu Serif",
                        "FreeSerif",
                    ],
                    on_found="\\newfontfamily\\arabicfont[Script=Arabic]{{{name}}}",
                    on_none="",
                )
            )
        elif has_hebrew:
            preamble_lines.append(
                _nested_if_font_exists(
                    [
                        "Times New Roman",
                        "TeX Gyre Termes",
                        "STIX Two Text",
                        "Libertinus Serif",
                        "DejaVu Serif",
                        "FreeSerif",
                        "Arial Unicode MS",
                    ],
                    on_found="\\setmainfont{{{name}}}",
                    on_none="",
                )
            )
            preamble_lines.append(r"\setdefaultlanguage{hebrew}")
            preamble_lines.append(
                _nested_if_font_exists(
                    [
                        "Arial Hebrew",
                        "Ezra SIL",
                        "SBL Hebrew",
                        "Arial Unicode MS",
                        "Times New Roman",
                        "DejaVu Serif",
                        "FreeSerif",
                    ],
                    on_found="\\newfontfamily\\hebrewfont[Script=Hebrew]{{{name}}}",
                    on_none="",
                )
            )
        else:
            preamble_lines.append(
                _nested_if_font_exists(
                    [
                        "Times New Roman",
                        "TeX Gyre Termes",
                        "STIX Two Text",
                        "Libertinus Serif",
                        "DejaVu Serif",
                        "FreeSerif",
                    ],
                    on_found="\\setmainfont{{{name}}}",
                    on_none="",
                )
            )

        if has_cjk or has_hiragana_katakana or has_hangul:
            preamble_lines.append(r"\usepackage{xeCJK}")
            if has_hiragana_katakana:
                locale = "ja"
            elif has_hangul:
                locale = "ko"
            else:
                locale = "zh"
            # Important: in TeX, \" is an accent command, not a quote. Use plain quotes: "zh".
            preamble_lines.append(
                r'\AtBeginDocument{\XeTeXlinebreaklocale "' + locale + r'"\XeTeXlinebreakskip=0pt plus 1pt}'
            )
            if has_hiragana_katakana:
                preamble_lines.append(
                    _nested_if_font_exists(
                        [
                            "Hiragino Mincho ProN",
                            "Noto Serif CJK JP",
                            "IPAexMincho",
                            "FandolSong-Regular",
                        ],
                        on_found="\\setCJKmainfont{{{name}}}",
                        on_none="",
                    )
                )
            elif has_hangul:
                preamble_lines.append(
                    _nested_if_font_exists(
                        [
                            "Apple SD Gothic Neo",
                            "Noto Sans CJK KR",
                            "UnBatang",
                            "FandolSong-Regular",
                        ],
                        on_found="\\setCJKmainfont{{{name}}}",
                        on_none="",
                    )
                )
            else:
                preamble_lines.append(
                    _nested_if_font_exists(
                        [
                            "PingFang SC",
                            "Noto Serif CJK SC",
                            "FandolSong-Regular",
                        ],
                        on_found="\\setCJKmainfont{{{name}}}",
                        on_none="",
                    )
                )

        preamble_lines.append(r"\emergencystretch=2em")
        preamble_lines.append(r"\sloppy")

        preamble = "\n".join(preamble_lines) + "\n"

        m = re.search(r"^\\documentclass[^\n]*\n", t, flags=re.MULTILINE)
        if not m:
            return content
        insert_pos = m.end()
        return t[:insert_pos] + preamble + t[insert_pos:]

    def _needs_bibtex(self, aux_path: Path) -> bool:
        if not aux_path.exists():
            return False
        try:
            with open(aux_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            return (r'\bibdata' in content) or (r'\bibstyle' in content)
        except Exception:
            return False

    def _referenced_bib_files_exist(self, aux_path: Path, output_dir: Path) -> bool:
        if not aux_path.exists():
            return False
        try:
            content = aux_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return False

        m = re.search(r"\\bibdata\{([^}]*)\}", content)
        if not m:
            return False
        names = [p.strip() for p in m.group(1).split(",") if p.strip()]
        for name in names:
            bib_path = output_dir / f"{name}.bib"
            if bib_path.exists():
                return True
        return False

    def _find_fallback_bbl(self, output_dir: Path, target_stem: str) -> Optional[Path]:
        try:
            candidates: List[Path] = []
            for p in output_dir.glob("*.bbl"):
                if p.name == f"{target_stem}.bbl":
                    continue
                try:
                    txt = p.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                if "\\bibitem" not in txt:
                    continue
                candidates.append(p)

            if not candidates:
                return None
            candidates.sort(key=lambda x: x.stat().st_size, reverse=True)
            return candidates[0]
        except Exception:
            return None

    def _install_fallback_bbl(self, output_dir: Path, target_stem: str, fallback_bbl: Path) -> bool:
        try:
            target = output_dir / f"{target_stem}.bbl"
            shutil.copy2(fallback_bbl, target)
            return True
        except Exception as e:
            self.logger.warning(f"Failed to install fallback .bbl {fallback_bbl} -> {target_stem}.bbl: {e}")
            return False

    def _run_bibtex(self, stem: str, output_dir: Path) -> bool:
        """Run bibtex command."""
        cmd = ["bibtex", stem]
        
        try:
            result = subprocess.run(
                cmd,
                cwd=output_dir,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=30
            )
            if result.returncode != 0:
                self.logger.warning(f"BibTeX failed: {result.stderr}")
                return False
            return True
        except Exception as e:
            self.logger.error(f"BibTeX error: {e}")
            return False

    def _needs_biber(self, bcf_path: Path, log_content: str) -> bool:
        if bcf_path.exists():
            return True
        if not log_content:
            return False
        markers = [
            "Please (re)run Biber",
            "Run Biber",
            "biber",
        ]
        if "biblatex" in log_content.lower() and any(m.lower() in log_content.lower() for m in markers):
            return True
        return False

    def _run_biber(self, stem: str, output_dir: Path) -> bool:
        cmd = ["biber", stem]
        try:
            result = subprocess.run(
                cmd,
                cwd=output_dir,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=60,
            )
            if result.returncode != 0:
                self.logger.warning(f"Biber failed: {result.stderr}")
                return False
            return True
        except FileNotFoundError:
            self.logger.warning("Biber is not available on PATH")
            return False
        except Exception as e:
            self.logger.error(f"Biber error: {e}")
            return False

    def _parse_log_errors(self, log_content: str) -> List[str]:
        """Parse LaTeX log for errors."""
        errors: List[str] = []

        # Collect a few high-signal errors even if we later trim the list.
        # Reason: some cases (e.g. Unicode character cascade) can produce hundreds
        # of errors and the relevant trigger line might be in the middle.
        keyword_hits: List[str] = []
        keyword_markers = [
            "Unicode character",
            "\\usepackage before \\documentclass",
            "c.cls",
        ]

        # Look for lines starting with !
        for line in log_content.splitlines():
            if not line.startswith("!"):
                continue
            errors.append(line)
            if any(m in line for m in keyword_markers):
                keyword_hits.append(line)

        # Dedupe while preserving order.
        def _dedupe(seq: List[str]) -> List[str]:
            seen = set()
            out: List[str] = []
            for s in seq:
                if s in seen:
                    continue
                seen.add(s)
                out.append(s)
            return out

        if len(errors) <= 50:
            return _dedupe(errors)

        trimmed = errors[:25] + errors[-25:]
        # Put keyword hits first so surgical fixes can trigger deterministically.
        return _dedupe(keyword_hits + trimmed)

    def _needs_rerun(self, log_content: str) -> bool:
        """Detect whether LaTeX indicates that another pdflatex pass is needed."""
        if not log_content:
            return False

        markers = [
            "There were undefined references",
            "Rerun to get cross-references right",
            "Label(s) may have changed",
            "Rerun LaTeX",
        ]
        if any(m in log_content for m in markers):
            return True

        # Many classes/packages emit the citation warning without the generic rerun markers.
        # A rerun may resolve them after BibTeX/Biber and helps avoid leaving '?' in the PDF.
        if re.search(r"Citation [`'][^`']+[`'] on page .* undefined", log_content):
            return True

        # More granular warnings
        if "LaTeX Warning:" in log_content and "undefined" in log_content:
            if "Citation" in log_content or "Reference" in log_content:
                return True

        return False

    def _surgical_fix(self, tex_path: Path, errors: List[str]) -> Optional[str]:
        """
        Применяет исправления на основе ошибок компиляции.
        В настоящее время реализует базовые исправления, но предназначен для использования LLM в будущем.
        """
        try:
            with open(tex_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
        except Exception:
            try:
                with open(tex_path, 'r', encoding='latin-1', errors='replace') as f:
                    content = f.read()
            except Exception:
                return None

        fixed = False

        def _comment_tex_line(line: str) -> Tuple[str, bool]:
            stripped = line.lstrip()
            if stripped.startswith('%'):
                return line, False
            prefix = line[: len(line) - len(stripped)]
            return f"{prefix}% {stripped}", True

        def _patch_cmd_in_aux_files(cmd_name: str) -> bool:
            """Patch command definitions in sibling *.sty/*.tex files within the temp source dir.

            Motivation: some templates define macros like \Prob in notation.sty; the compilation
            error is raised while loading that file, so changing translated.tex alone is insufficient.
            """

            base_dir = tex_path.parent
            changed_any = False

            def _patch_file(p: Path) -> bool:
                txt = _read_text_any(p)
                if txt is None:
                    return False

                # First, try the safe transformation: \newcommand -> \renewcommand
                pattern = re.compile(
                    r"\\newcommand(\*?)\{\\" + re.escape(cmd_name) + r"\}(\[[^\]]*\])?",
                    re.IGNORECASE,
                )

                def replace_cmd(m):
                    star = m.group(1)
                    opt_args = m.group(2) or ""
                    # NOTE: this is a function replacement, so backslashes are not "unescaped".
                    # We must emit single backslashes into the LaTeX file.
                    return f"\\renewcommand{star}{{\\{cmd_name}}}{opt_args}"

                new_txt = pattern.sub(replace_cmd, txt)
                if new_txt != txt:
                    try:
                        p.write_text(new_txt, encoding="utf-8")
                        return True
                    except Exception:
                        return False

                # Otherwise, comment out other conflicting definition forms.
                lines = txt.split("\n")
                new_lines: List[str] = []
                changed = False
                pats = [
                    re.compile(r"^\s*\\DeclareMathOperator\\*?\s*\{\\" + re.escape(cmd_name) + r"\}\b"),
                    re.compile(r"^\s*\\providecommand\\*?\s*\{\\" + re.escape(cmd_name) + r"\}\b"),
                    re.compile(r"^\s*\\DeclareRobustCommand\\*?\s*\{\\" + re.escape(cmd_name) + r"\}\b"),
                    re.compile(r"^\s*\\def\s*\\" + re.escape(cmd_name) + r"\b"),
                ]
                for line in lines:
                    if any(pat.search(line) for pat in pats):
                        commented, did = _comment_tex_line(line)
                        new_lines.append(commented)
                        changed = changed or did
                    else:
                        new_lines.append(line)
                if changed:
                    try:
                        p.write_text("\n".join(new_lines), encoding="utf-8")
                        return True
                    except Exception:
                        return False
                return False

            try:
                for p in sorted(base_dir.glob("*.sty")):
                    if _patch_file(p):
                        changed_any = True
                for p in sorted(base_dir.glob("*.tex")):
                    if p.name.startswith("translated"):
                        continue
                    if _patch_file(p):
                        changed_any = True
            except Exception:
                return changed_any

            return changed_any

        def _brace_delta_tex(line: str) -> int:
            head = re.split(r"(?<!\\)%", line, maxsplit=1)[0]
            return head.count("{") - head.count("}")
        
        # Исправление 1: Команда уже определена
        # ! LaTeX Error: Command \C already defined.
        if any("Command" in e and "already defined" in e for e in errors):
            # Извлекаем имя команды из ошибки
            for error in errors:
                match = re.search(r'Command \\(\w+) already defined', error)
                if match:
                    cmd_name = match.group(1)

                    if cmd_name in ("inserted", "moved", "modified", "insertedd"):
                        # If the document defines the same command multiple times in the preamble,
                        # keep the first definition and comment out the rest.
                        lines = content.split("\n")
                        new_lines: List[str] = []
                        changed = False
                        begin_doc_re = re.compile(r"^\s*\\begin\{document\}")
                        def_re = re.compile(
                            r"^\s*\\(re)?newcommand\*?\s*(\{\\" + re.escape(cmd_name) + r"\}|\\" + re.escape(cmd_name) + r")\b",
                            re.IGNORECASE,
                        )
                        seen = False
                        for line in lines:
                            if begin_doc_re.search(line):
                                new_lines.append(line)
                                new_lines.extend(lines[len(new_lines):])
                                break
                            if line.lstrip().startswith('%'):
                                new_lines.append(line)
                                continue
                            if def_re.search(line):
                                if seen:
                                    commented, did = _comment_tex_line(line)
                                    new_lines.append(commented)
                                    changed = changed or did
                                else:
                                    seen = True
                                    new_lines.append(line)
                                continue
                            new_lines.append(line)
                        if changed:
                            content = "\n".join(new_lines)
                            fixed = True
                            self.logger.info(f"Исправлено: закомментированы дубликаты определения команды \\{cmd_name} в преамбуле")

                    # Заменяем \newcommand{\C} на \renewcommand{\C}
                    # Универсальный паттерн для всех вариантов
                    pattern = re.compile(
                        r'\\newcommand(\*?)\{\\' + re.escape(cmd_name) + r'\}(\[[^\]]*\])?',
                        re.IGNORECASE
                    )
                    def replace_cmd(m):
                        star = m.group(1)  # * если есть
                        opt_args = m.group(2) or ''  # [1] если есть
                        # Emit single backslashes into LaTeX (avoid producing leading "\\" which is a linebreak).
                        return f"\\renewcommand{star}{{\\{cmd_name}}}{opt_args}"
                    
                    new_content = pattern.sub(replace_cmd, content)
                    if new_content != content:
                        content = new_content
                        fixed = True
                        self.logger.info(f"Исправлено: заменено \\newcommand{{\\{cmd_name}}} на \\renewcommand{{\\{cmd_name}}}")
                    else:
                        lines = content.split("\n")
                        new_lines: List[str] = []
                        changed = False
                        pats = [
                            re.compile(r"^\\s*\\\\DeclareMathOperator\\*?\\s*\\{\\\\" + re.escape(cmd_name) + r"\\}\b"),
                            re.compile(r"^\\s*\\\\providecommand\\*?\\s*\\{\\\\" + re.escape(cmd_name) + r"\\}\b"),
                            re.compile(r"^\\s*\\\\DeclareRobustCommand\\*?\\s*\\{\\\\" + re.escape(cmd_name) + r"\\}\b"),
                            re.compile(r"^\\s*\\\\def\\s*\\\\" + re.escape(cmd_name) + r"\\b"),
                        ]
                        for line in lines:
                            if line.lstrip().startswith('%'):
                                new_lines.append(line)
                                continue
                            if any(p.search(line) for p in pats):
                                prefix = line[: len(line) - len(line.lstrip())]
                                new_lines.append(f"{prefix}% {line.lstrip()}")
                                changed = True
                            else:
                                new_lines.append(line)
                        if changed:
                            content = "\n".join(new_lines)
                            fixed = True
                            self.logger.info(f"Исправлено: закомментировано конфликтующее определение команды \\{cmd_name}")

                    if _patch_cmd_in_aux_files(cmd_name):
                        fixed = True
                        self.logger.info(f"Исправлено: патч определения команды \\{cmd_name} в подключаемых файлах (sty/tex)")
        
        # Исправление 2: Конфликт опций babel
        # ! LaTeX Error: Option clash for package babel.
        if any("Option clash for package babel" in e for e in errors):
            from pipeline.latex_postprocessor import _fix_babel_conflicts
            new_content = _fix_babel_conflicts(content)
            if new_content != content:
                content = new_content
                fixed = True
                self.logger.info("Исправлено: конфликт опций babel")

        # Исправление 2b: inputenc latin1 -> utf8
        # ! Package inputenc Error: Keyboard character used is undefined
        if any("Package inputenc Error: Keyboard character used is undefined" in e for e in errors):
            def _fix_inputenc_encoding(txt: str) -> str:
                # Typical culprit: \usepackage[latin1]{inputenc}
                def repl(m: re.Match) -> str:
                    opts = (m.group(1) or "").strip()
                    if not opts:
                        return m.group(0)
                    low = opts.lower()
                    if "utf8" in low:
                        return m.group(0)
                    return "\\usepackage[utf8]{inputenc}"

                return re.sub(
                    r"\\usepackage\[([^\]]*)\]\{inputenc\}",
                    repl,
                    txt,
                    flags=re.IGNORECASE,
                )

            new_content = _fix_inputenc_encoding(content)
            if new_content != content:
                content = new_content
                fixed = True
                self.logger.info("Исправлено: заменён inputenc на utf8 (устранение Keyboard character undefined)")
            else:
                # Если функция не помогла, добавляем PassOptionsToPackage
                doc_match = re.search(r'\\documentclass', content)
                if doc_match and r'\PassOptionsToPackage{russian}{babel}' not in content:
                    content = r'\PassOptionsToPackage{russian}{babel}' + '\n' + content
                    fixed = True
                    self.logger.info("Добавлен \\PassOptionsToPackage{russian}{babel}")
        
        # Исправление 3: Конфликт кодировок fontenc
        # ! LaTeX Error: Command \cyra unavailable in encoding T1.
        # Кириллица недоступна в T1 — нужен T2A
        if any("unavailable in encoding T1" in e for e in errors):
            from pipeline.latex_postprocessor import _fix_fontenc_conflicts
            new_content = _fix_fontenc_conflicts(content)
            if new_content != content:
                content = new_content
                fixed = True
                self.logger.info("Исправлено: конфликт кодировок fontenc")

        if any("Command \\DH unavailable in encoding" in e for e in errors):
            if "\\begin{document}" in content:
                override_block = "\\providecommand{\\DH}{}\n" \
                                 "\\renewcommand{\\DH}{DH}\n" \
                                 "\\providecommand{\\dh}{}\n" \
                                 "\\renewcommand{\\dh}{dh}\n"
                if override_block not in content:
                    content = content.replace("\\begin{document}", override_block + "\\begin{document}")
                    fixed = True
                    self.logger.info("Исправлено: нейтрализованы команды \\DH/\\dh, отсутствующие в текущей кодировке")
        
        # Исправление 4: Несовместимость sectsty
        # ! Package sectsty Error: The sectsty package doesn't work with this document class.
        if any("sectsty" in e.lower() and ("doesn't work" in e.lower() or "error" in e.lower()) for e in errors):
            from pipeline.latex_postprocessor import _fix_sectsty_conflicts
            new_content = _fix_sectsty_conflicts(content)
            if new_content != content:
                content = new_content
                fixed = True
                self.logger.info("Исправлено: удалён несовместимый sectsty")
        
        # Исправление 5: Неопределённая управляющая последовательность
        # ! Undefined control sequence.
        # l.100 \somecommand

        if any(("\\inserted" in e or "\\moved" in e or "\\modified" in e or "\\insertedd" in e) and "undefined" in e for e in errors):
            # These macros can be used in the preamble (e.g. inside \title/\author),
            # so stubs must be inserted in the preamble, not right before \begin{document}.
            stub_block = (
                "\\providecommand{\\inserted}[1]{#1}\n"
                "\\providecommand{\\moved}[1]{#1}\n"
                "\\providecommand{\\modified}[1]{#1}\n"
                "\\providecommand{\\insertedd}[1]{#1}\n"
            )

            has_stubs = any(
                ("\\providecommand{\\inserted}" in l)
                and (not l.lstrip().startswith('%'))
                for l in content.split("\n")
            )
            if not has_stubs:
                lines = content.split("\n")
                docclass_re = re.compile(r"^\s*\\documentclass(\[[^\]]*\])?\{[^}]+\}")
                doc_idx = None
                for i, line in enumerate(lines):
                    if docclass_re.search(line):
                        doc_idx = i
                        break
                if doc_idx is not None:
                    insert_pos = doc_idx + 1
                    lines[insert_pos:insert_pos] = stub_block.rstrip("\n").split("\n")
                    content = "\n".join(lines)
                    fixed = True
                    self.logger.info("Исправлено: добавлены заглушки для команд inserted/moved/modified")

        if any("Package tikz Error: Sorry, the system call" in e for e in errors):
            lines = content.split("\n")
            new_lines: List[str] = []
            changed = False
            ext_pats = [
                re.compile(r"\\usetikzlibrary\{[^}]*external[^}]*\}", re.IGNORECASE),
                re.compile(r"\\usepgfplotslibrary\{[^}]*external[^}]*\}", re.IGNORECASE),
                re.compile(r"\\tikzexternalize\b", re.IGNORECASE),
                re.compile(r"\\tikzsetnextfilename\b", re.IGNORECASE),
                re.compile(r"external/system call", re.IGNORECASE),
            ]
            for line in lines:
                if line.lstrip().startswith('%'):
                    new_lines.append(line)
                    continue
                if any(p.search(line) for p in ext_pats):
                    commented, did = _comment_tex_line(line)
                    new_lines.append(commented)
                    changed = changed or did
                else:
                    new_lines.append(line)
            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: отключена tikz/pgfplots externalization (system call)")

        if any("A node must have a" in e for e in errors):
            lines = content.split("\n")
            new_lines = []
            changed = False
            for line in lines:
                if line.lstrip().startswith('%'):
                    new_lines.append(line)
                    continue
                if re.search(r"\\1\s*;", line):
                    new_lines.append(re.sub(r"\\1\s*;", "{};", line))
                    changed = True
                else:
                    new_lines.append(line)
            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: заменены битые тикз-плейсхолдеры \\1; на пустые метки {};")

        if any("Undefined control sequence" in e for e in errors) and re.search(r"^\s*\\1\b", content, flags=re.MULTILINE):
            # Some sources contain a stray "\1" line (often after \subfloat{}), which is invalid TeX.
            lines = content.split("\n")
            new_lines: List[str] = []
            changed = False
            for line in lines:
                if line.lstrip().startswith('%'):
                    new_lines.append(line)
                    continue
                if re.match(r"^\s*\\1\s*$", line):
                    new_lines.append("\\relax")
                    changed = True
                else:
                    new_lines.append(line)
            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: удалён битый плейсхолдер \\1 (заменён на \\relax)")

        if any("Extra }, or forgotten $" in e for e in errors) or any("Missing } inserted" in e for e in errors):
            # Fix common typo: "$_{train}}$" -> "$_{train}$"
            new_content = re.sub(r"\$_\{([^}]+)\}\}\$", r"$_{\1}$", content)
            if new_content != content:
                content = new_content
                fixed = True
                self.logger.info("Исправлено: исправлен дисбаланс скобок в инлайн-математике вида $_{..}}$")

        if any("Runaway argument" in e for e in errors) or any("\\@xdblarg" in e for e in errors):
            lines = content.split("\n")
            new_lines: List[str] = []
            changed = False
            head_re = re.compile(r"^\s*\\(section|subsection|subsubsection|caption)\b")
            for line in lines:
                if line.lstrip().startswith('%'):
                    new_lines.append(line)
                    continue
                if head_re.search(line):
                    delta = _brace_delta_tex(line)
                    if delta > 0:
                        new_lines.append(line + ("}" * delta))
                        changed = True
                        continue
                new_lines.append(line)
            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: добавлены недостающие '}' в заголовках (Runaway argument/\\@xdblarg)")

        if any("File `fig1' not found" in e for e in errors) or any("fig1.png" in e and "not found" in e for e in errors):
            lines = content.split("\n")
            new_lines: List[str] = []
            changed = False
            for line in lines:
                if line.lstrip().startswith('%'):
                    new_lines.append(line)
                    continue
                if re.search(r"\\includegraphics(\[[^\]]*\])?\{fig1(\.png)?\}", line, flags=re.IGNORECASE):
                    new_lines.append(re.sub(
                        r"\\includegraphics(\[[^\]]*\])?\{fig1(\.png)?\}",
                        r"\\fbox{\\rule{0pt}{1in}\\rule{2.5in}{0pt}}",
                        line,
                        flags=re.IGNORECASE,
                    ))
                    changed = True
                else:
                    new_lines.append(line)
            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: заменены отсутствующие fig1/fig1.png на placeholder")

        if any("ended by \\end{IEEEbiography}" in e for e in errors) or any("\\end{IEEEbiography}" in e for e in errors):
            lines = content.split("\n")
            new_lines: List[str] = []
            changed = False
            open_count = 0
            begin_re = re.compile(r"\\begin\{IEEEbiography\}")
            end_re = re.compile(r"\\end\{IEEEbiography\}")
            for line in lines:
                if line.lstrip().startswith('%'):
                    new_lines.append(line)
                    continue

                # If the optional photo argument references fig1.png and the file is missing, strip it.
                if re.search(r"\\begin\{IEEEbiography\}\[\{[^\}]*fig1\.png[^\}]*\}\]", line, flags=re.IGNORECASE):
                    new_lines.append(re.sub(
                        r"(\\begin\{IEEEbiography\})\[\{[^\}]*fig1\.png[^\}]*\}\]",
                        r"\\begin{IEEEbiography}[{}]",
                        line,
                        flags=re.IGNORECASE,
                    ))
                    changed = True
                    continue

                if begin_re.search(line):
                    open_count += len(begin_re.findall(line))
                    new_lines.append(line)
                    continue

                if end_re.search(line):
                    ends = len(end_re.findall(line))
                    if open_count <= 0:
                        # Orphan end: remove token(s) without killing the whole line.
                        new_lines.append(end_re.sub("", line))
                        changed = True
                    else:
                        open_count = max(0, open_count - ends)
                        new_lines.append(line)
                    continue

                new_lines.append(line)

            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: нейтрализованы ошибки окружения IEEEbiography (сиротские end/фото fig1)")

        if any("ended by \\end{tikzpicture}" in e for e in errors):
            lines = content.split("\n")
            new_lines = []
            changed = False
            open_count = 0
            begin_re = re.compile(r"\\begin\{tikzpicture\}")
            end_re = re.compile(r"\\end\{tikzpicture\}")
            for line in lines:
                if line.lstrip().startswith('%'):
                    new_lines.append(line)
                    continue
                if begin_re.search(line):
                    open_count += 1
                    new_lines.append(line)
                    continue
                if end_re.search(line):
                    if open_count <= 0:
                        commented, did = _comment_tex_line(line)
                        new_lines.append(commented)
                        changed = changed or did
                    else:
                        open_count -= 1
                        new_lines.append(line)
                    continue
                new_lines.append(line)
            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: закомментированы сиротские \\end{tikzpicture}")

        if any("Package pgfplots Error: Could not read table file" in e for e in errors) or any("TeX capacity exceeded" in e for e in errors):
            lines = content.split("\n")
            new_lines: List[str] = []
            i = 0
            changed = False
            begin_re = re.compile(r"^\s*\\begin\{tikzpicture\}")
            end_re = re.compile(r"^\s*\\end\{tikzpicture\}")
            while i < len(lines):
                line = lines[i]
                if line.lstrip().startswith('%'):
                    new_lines.append(line)
                    i += 1
                    continue

                if "\\enlargethispage" in line:
                    commented, did = _comment_tex_line(line)
                    new_lines.append(commented)
                    changed = changed or did
                    i += 1
                    continue

                if begin_re.search(line):
                    block_lines = [line]
                    j = i + 1
                    while j < len(lines):
                        block_lines.append(lines[j])
                        if end_re.search(lines[j]) and not lines[j].lstrip().startswith('%'):
                            break
                        j += 1

                    block_text = "\n".join(block_lines)
                    if re.search(r"\\begin\{(axis|groupplot)\}", block_text) or "\\addplot" in block_text or "\\pgfplots" in block_text:
                        for bl in block_lines:
                            commented, _ = _comment_tex_line(bl)
                            new_lines.append(commented)
                        changed = True
                    else:
                        new_lines.extend(block_lines)

                    i = j + 1
                    continue

                new_lines.append(line)
                i += 1

            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: закомментированы тяжёлые pgfplots/tikzpicture блоки из-за ошибок таблиц/памяти")

        if any("end{tikzpicture}" in e for e in errors):
            lines = content.split("\n")
            new_lines: List[str] = []
            in_commented_tikz = False
            changed = False
            commented_begin_re = re.compile(r"^\s*%.*\\begin\{tikzpicture\}")
            end_re = re.compile(r"\\end\{tikzpicture\}")

            for line in lines:
                if not in_commented_tikz and commented_begin_re.search(line):
                    in_commented_tikz = True
                    new_lines.append(line)
                    continue

                if in_commented_tikz:
                    commented, did = _comment_tex_line(line)
                    new_lines.append(commented)
                    changed = changed or did
                    if end_re.search(line):
                        in_commented_tikz = False
                    continue

                new_lines.append(line)

            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: закомментирован битый блок tikzpicture (\\begin{tikzpicture} был закомментирован)")
        
        # Пример: \\begin{equation*} без amsmath
        if any("Environment equation* undefined" in e for e in errors):
            if "\\usepackage{amsmath}" not in content:
                content = content.replace("\\begin{document}", "\\usepackage{amsmath}\\n\\begin{document}")
                fixed = True
                
        # Пример: \includegraphics без graphicx
        if any("Undefined control sequence" in e for e in errors) and "includegraphics" in content:
             if "\\usepackage{graphicx}" not in content:
                content = content.replace("\\begin{document}", "\\usepackage{graphicx}\n\\begin{document}")
                fixed = True
        
        # Исправление 6: CJK конфликты
        # Undefined control sequence: \CJK@XX или подобные
        if any("Undefined control sequence" in e for e in errors) and "CJK" in content:
            from pipeline.latex_postprocessor import _fix_cjk_conflicts
            new_content = _fix_cjk_conflicts(content)
            if new_content != content:
                content = new_content
                fixed = True
                self.logger.info("Исправлено: удалены CJK-блоки")

        # Исправление 6b: Unicode character ... not set up (часто кириллица без babel)
        # ! LaTeX Error: Unicode character Д (U+0414) not set up for use with LaTeX.
        if any("Unicode character" in e for e in errors):
            lines = content.split("\n")
            docclass_re = re.compile(r"^\s*\\documentclass(\[[^\]]*\])?\{[^}]+\}")
            doc_idx = None
            for i, line in enumerate(lines):
                if docclass_re.search(line):
                    doc_idx = i
                    break

            if doc_idx is not None:
                changed = False

                passopt = "\\PassOptionsToPackage{russian}{babel}"
                has_passopt = any(
                    (passopt in l) and (not l.lstrip().startswith('%'))
                    for l in lines[: doc_idx + 1]
                )
                if not has_passopt:
                    lines.insert(doc_idx, passopt)
                    doc_idx += 1
                    changed = True

                has_babel = any(
                    ("\\usepackage" in l or "\\RequirePackage" in l)
                    and ("{babel}" in l)
                    and (not l.lstrip().startswith('%'))
                    for l in lines
                )
                has_inputenc = any(
                    ("\\usepackage" in l or "\\RequirePackage" in l)
                    and ("{inputenc}" in l)
                    and (not l.lstrip().startswith('%'))
                    for l in lines
                )

                insert_after_docclass: List[str] = []
                if not has_inputenc:
                    insert_after_docclass.append("\\usepackage[utf8]{inputenc}")
                if not has_babel:
                    insert_after_docclass.append("\\usepackage[russian]{babel}")

                if insert_after_docclass:
                    insert_pos = doc_idx + 1
                    lines[insert_pos:insert_pos] = insert_after_docclass
                    changed = True

                if changed:
                    content = "\n".join(lines)
                    fixed = True
                    self.logger.info("Исправлено: добавлены настройки кириллицы (babel/inputenc) для Unicode character")

        # Исправление 7: Ложная строка "\\documentclass command." и попытка загрузить c.cls
        # ! LaTeX Error: File `c.cls' not found.
        # Такая ошибка часто возникает из-за того, что текстовая фраза в шаблоне ACM
        # вроде "the \\documentclass command" превращается в отдельную строку
        # "\\documentclass command.", и LaTeX читает её как запрос класса c.cls.
        if any("c.cls" in e for e in errors):
            lines = content.split("\n")
            new_lines: List[str] = []
            changed = False

            for line in lines:
                if "\\documentclass" in line and "{" not in line:
                    stripped = line.lstrip()
                    if not stripped.startswith('%'):
                        prefix = line[: len(line) - len(stripped)]
                        line = f"{prefix}% {stripped}"
                        changed = True
                new_lines.append(line)

            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: закомментированы некорректные строки с \\documentclass без аргументов (c.cls)")

        if any("\\usepackage before \\documentclass" in e for e in errors):
            lines = content.split("\n")
            docclass_re = re.compile(r"^\s*\\documentclass(\[[^\]]*\])?\{[^}]+\}")
            doc_idx = None
            for i, line in enumerate(lines):
                if docclass_re.search(line):
                    doc_idx = i
                    break

            if doc_idx is not None:
                moved: List[str] = []
                before: List[str] = []
                after: List[str] = []
                pkg_re = re.compile(r"^\s*\\(usepackage|RequirePackage)(\[[^\]]*\])?\{[^}]+\}")

                for i, line in enumerate(lines):
                    if i < doc_idx:
                        stripped = line.lstrip()
                        if stripped.startswith('%'):
                            before.append(line)
                            continue
                        if pkg_re.search(line):
                            moved.append(line)
                            continue
                        before.append(line)
                        continue

                    if i == doc_idx:
                        continue

                    after.append(line)

                if moved:
                    content = "\n".join(before + [lines[doc_idx]] + moved + after)
                    fixed = True
                    self.logger.info("Исправлено: перемещены строки \\usepackage/\\RequirePackage после \\documentclass")

        # Исправление 8: Ошибка порядка загрузки hyperxmp/hyperref
        # ! Package hyperxmp Error: hyperref must be loaded before hyperxmp.
        if any("hyperxmp Error" in e and "hyperref must be loaded before hyperxmp" in e for e in errors):
            changed = False

            # 1) Патчим основной .tex: комментируем прямые подключения hyperxmp
            pattern = re.compile(
                r"\\(usepackage|RequirePackage)(\\[[^\]]*\\])?\{hyperxmp\}",
                re.IGNORECASE,
            )
            new_content = pattern.sub(r"% \\1\\2{hyperxmp}", content)
            if new_content != content:
                content = new_content
                changed = True
                self.logger.info("Исправлено: отключён hyperxmp в основном .tex (ошибка порядка загрузки с hyperref)")

            # 2) Дополнительно пытаемся пропатчить acmart.cls в той же директории
            cls_path = tex_path.parent / "acmart.cls"
            if cls_path.exists():
                try:
                    with open(cls_path, "r", encoding="utf-8", errors="replace") as f:
                        cls_content = f.read()
                    new_cls_content = re.sub(
                        r"\\RequirePackage\{hyperxmp\}",
                        r"%\\RequirePackage{hyperxmp}",
                        cls_content,
                    )
                    if new_cls_content != cls_content:
                        with open(cls_path, "w", encoding="utf-8") as f:
                            f.write(new_cls_content)
                        changed = True
                        self.logger.info("Исправлено: hyperxmp отключён в acmart.cls (ошибка порядка загрузки с hyperref)")
                except Exception as e:
                    self.logger.warning(f"Не удалось пропатчить acmart.cls для hyperxmp: {e}")

            if changed:
                fixed = True

        if any("Command \\DH unavailable in encoding OT1" in e for e in errors) or any(
            "Command \\dh unavailable in encoding OT1" in e for e in errors
        ):
            lines = content.split("\n")
            docclass_re = re.compile(r"^\s*\\documentclass(\[[^\]]*\])?\{[^}]+\}")
            doc_idx = None
            for i, line in enumerate(lines):
                if docclass_re.search(line):
                    doc_idx = i
                    break
            override_block = "\\providecommand{\\DH}{DH}\n" \
                             "\\providecommand{\\dh}{dh}\n" \
                             "\\renewcommand{\\DH}{DH}\n" \
                             "\\renewcommand{\\dh}{dh}\n"
            if override_block not in content and doc_idx is not None:
                insert_pos = doc_idx + 1
                lines[insert_pos:insert_pos] = override_block.rstrip("\n").split("\n")
                content = "\n".join(lines)
                fixed = True
                self.logger.info("Исправлено: нейтрализованы команды \\DH/\\dh (OT1)")

        if any("@iiiparbox" in e for e in errors) or any("caption@xdblarg" in e for e in errors):
            lines = content.split("\n")
            new_lines: List[str] = []
            changed = False
            pkg_re = re.compile(r"^\s*\\(usepackage|RequirePackage)(\[[^\]]*\])?\{(caption|subcaption)\}")
            cmd_re = re.compile(r"^\s*\\(caption|captionof|captionsetup)\b")
            for line in lines:
                stripped = line.lstrip()
                if stripped.startswith('%'):
                    new_lines.append(line)
                    continue
                if pkg_re.search(line) or cmd_re.search(line):
                    commented, did = _comment_tex_line(line)
                    new_lines.append(commented)
                    changed = changed or did
                    continue
                new_lines.append(line)
            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: закомментированы caption/subcaption/\\caption* из-за ошибок \\@iiiparbox")

        if any("begin{tikzpicture}" in e and "ended by \\end{figure}" in e for e in errors):
            line_no = None
            for e in errors:
                m = re.search(r"on input line\s+(\d+)", e)
                if m:
                    try:
                        line_no = int(m.group(1))
                        break
                    except Exception:
                        line_no = None
            lines = content.split("\n")
            if line_no is not None and 1 <= line_no <= len(lines):
                idx = line_no - 1
                start = None
                for i in range(idx, -1, -1):
                    if "\\begin{figure" in lines[i] and not lines[i].lstrip().startswith('%'):
                        start = i
                        break
                end = None
                for i in range(idx, len(lines)):
                    if "\\end{figure" in lines[i] and not lines[i].lstrip().startswith('%'):
                        end = i
                        break
                if start is not None and end is not None and start <= end:
                    new_lines = []
                    changed = False
                    for i, line in enumerate(lines):
                        if start <= i <= end:
                            commented, did = _comment_tex_line(line)
                            new_lines.append(commented)
                            changed = changed or did
                        else:
                            new_lines.append(line)
                    if changed:
                        content = "\n".join(new_lines)
                        fixed = True
                        self.logger.info("Исправлено: закомментирован figure-блок с повреждённым tikzpicture")

        # Исправление 9: Осиротевший шаблонный блок thebibliography (часто пример из класса)
        # ! LaTeX Error: \begin{thebibliography} ... ended by \end{document}.
        if any("begin{thebibliography}" in e and "ended by \\end{document" in e for e in errors):
            from pipeline.latex_postprocessor import _remove_orphaned_bibliography_blocks
            new_content = _remove_orphaned_bibliography_blocks(content)
            if new_content != content:
                content = new_content
                fixed = True
                self.logger.info("Исправлено: удалён осиротевший шаблонный блок thebibliography перед \\end{document}")

        # Исправление 10: TikZ externalization требует shell-escape (недоступно в нашем режиме компиляции)
        # ! Package tikz Error: Sorry, the system call 'pdflatex ...' did not result in a usable output file.
        if any("Package tikz Error" in e and "system call" in e for e in errors):
            lines = content.split("\n")
            new_lines: List[str] = []
            changed = False

            external_markers = [
                "\\tikzexternalize",
                "\\usetikzlibrary{external}",
                "\\tikzset{external",
                "\\tikzsetnextfilename",
            ]

            for line in lines:
                if any(m in line for m in external_markers):
                    stripped = line.lstrip()
                    if not stripped.startswith('%'):
                        prefix = line[: len(line) - len(stripped)]
                        line = f"{prefix}% {stripped}"
                        changed = True
                new_lines.append(line)

            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: отключена TikZ externalization (требует shell-escape)")

        # Исправление 11: Отсутствующие картинки (\includegraphics{...})
        # ! LaTeX Error: File `fig1' not found.
        missing_files: List[str] = []
        for e in errors:
            m = re.search(r"File `([^']+?)' not found", e)
            if m:
                missing_files.append(m.group(1))

        bad_graphics: List[str] = []
        for e in errors:
            m = re.search(r"Unable to load picture or PDF file '([^']+?)'", e)
            if m:
                bad_graphics.append(m.group(1))

        missing_files_all = missing_files + bad_graphics
        if missing_files_all:
            missing_bases = set()
            for name in missing_files_all:
                base = name
                for ext in (".png", ".jpg", ".jpeg", ".pdf", ".eps", ".ps"):
                    if base.lower().endswith(ext):
                        base = base[: -len(ext)]
                        break
                missing_bases.add(base)

            lines = content.split("\n")
            new_lines: List[str] = []
            changed = False

            inc_re = re.compile(r"(\\includegraphics(?:\[[^\]]*\])?\{)([^\}]+)(\})")
            for line in lines:
                m = inc_re.search(line)
                if not m:
                    new_lines.append(line)
                    continue

                path = m.group(2).strip()
                path_base = path
                for ext in (".png", ".jpg", ".jpeg", ".pdf", ".eps", ".ps"):
                    if path_base.lower().endswith(ext):
                        path_base = path_base[: -len(ext)]
                        break
                if path_base not in missing_bases:
                    new_lines.append(line)
                    continue

                stripped = line.lstrip()
                if not stripped.startswith('%'):
                    prefix = line[: len(line) - len(stripped)]
                    line = f"{prefix}% {stripped}"
                    changed = True
                new_lines.append(line)

            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: закомментированы строки \\includegraphics для проблемных/отсутствующих файлов")

        # Исправление 12: listings не может обработать UTF-8 (часто в *.listing файлах tcolorbox)
        # ! LaTeX Error: Invalid UTF-8 byte sequence (..\lst@EC..)
        if any("Invalid UTF-8 byte sequence" in e and "lst@EC" in e for e in errors):
            lines = content.split("\n")
            new_lines: List[str] = []
            changed = False

            def _comment_line(line: str) -> Tuple[str, bool]:
                stripped = line.lstrip()
                if stripped.startswith('%'):
                    return line, False
                prefix = line[: len(line) - len(stripped)]
                return f"{prefix}% {stripped}", True

            in_lst = False
            disabled_envs = {"Code", "FullCode", "tcblisting"}
            in_disabled_env: Optional[str] = None
            in_lstset_block = False
            lstset_brace_depth = 0
            in_newtcblisting_def = False
            newtcblisting_brace_depth = 0
            for line in lines:
                if "\\tcbset{listing options=" in line:
                    commented, did = _comment_line(line)
                    changed = changed or did
                    new_lines.append(commented)
                    continue

                # Disable tcolorbox/listings machinery which can read/write \jobname.listing.
                if "\\tcbuselibrary{listings}" in line:
                    commented, did = _comment_line(line)
                    changed = changed or did
                    new_lines.append(commented)
                    continue

                if not in_lstset_block and "\\lstset" in line and "{" in line:
                    in_lstset_block = True
                    lstset_brace_depth = line.count("{") - line.count("}")
                    commented, did = _comment_line(line)
                    changed = changed or did
                    new_lines.append(commented)
                    if lstset_brace_depth <= 0:
                        in_lstset_block = False
                    continue
                if in_lstset_block:
                    lstset_brace_depth += line.count("{") - line.count("}")
                    commented, did = _comment_line(line)
                    changed = changed or did
                    new_lines.append(commented)
                    if lstset_brace_depth <= 0:
                        in_lstset_block = False
                    continue

                if not in_newtcblisting_def and "\\newtcblisting{Code}" in line:
                    in_newtcblisting_def = True
                    newtcblisting_brace_depth = line.count("{") - line.count("}")
                    commented, did = _comment_line(line)
                    changed = changed or did
                    new_lines.append(commented)
                    if newtcblisting_brace_depth <= 0:
                        in_newtcblisting_def = False
                    continue
                if in_newtcblisting_def:
                    newtcblisting_brace_depth += line.count("{") - line.count("}")
                    commented, did = _comment_line(line)
                    changed = changed or did
                    new_lines.append(commented)
                    if newtcblisting_brace_depth <= 0:
                        in_newtcblisting_def = False
                    continue

                if in_disabled_env is None:
                    for env in disabled_envs:
                        if re.search(r"\\begin\{" + re.escape(env) + r"\}", line):
                            in_disabled_env = env
                            commented, did = _comment_line(line)
                            changed = changed or did
                            new_lines.append(commented)
                            break
                    if in_disabled_env is not None:
                        continue

                if in_disabled_env is not None:
                    commented, did = _comment_line(line)
                    changed = changed or did
                    new_lines.append(commented)
                    if re.search(r"\\end\{" + re.escape(in_disabled_env) + r"\}", line):
                        in_disabled_env = None
                    continue

                if not in_lst and re.search(r"\\begin\{lstlisting\}", line):
                    in_lst = True
                    commented, did = _comment_line(line)
                    changed = changed or did
                    new_lines.append(commented)
                    continue

                if in_lst:
                    commented, did = _comment_line(line)
                    changed = changed or did
                    new_lines.append(commented)
                    if re.search(r"\\end\{lstlisting\}", line):
                        in_lst = False
                    continue

                if "translated.listing" in line or "\\lstinputlisting" in line or "\\tcbinputlisting" in line:
                    commented, did = _comment_line(line)
                    changed = changed or did
                    new_lines.append(commented)
                    continue

                new_lines.append(line)

            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: отключены listings/внешние *.listing блоки из-за ошибки UTF-8")

        # Исправление 13: Extra \or в tabular (обычно из-за повреждённого содержимого)
        if any("Extra \\or." in e for e in errors):
            lines = content.split("\n")
            new_lines: List[str] = []
            changed = False

            def _comment_line(line: str) -> Tuple[str, bool]:
                stripped = line.lstrip()
                if stripped.startswith('%'):
                    return line, False
                prefix = line[: len(line) - len(stripped)]
                return f"{prefix}% {stripped}", True

            tabular_envs = ("tabular", "tabularx", "tabular*")
            in_tabular: Optional[str] = None
            for line in lines:
                if in_tabular is None:
                    for env in tabular_envs:
                        if f"\\begin{{{env}}}" in line:
                            in_tabular = env
                            break

                if in_tabular is not None:
                    commented, did = _comment_line(line)
                    changed = changed or did
                    new_lines.append(commented)
                    if f"\\end{{{in_tabular}}}" in line:
                        in_tabular = None
                    continue
                new_lines.append(line)

            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: закомментированы блоки tabular из-за ошибки Extra \\or")

        if any("\\begin{tabular}" in e and "ended by \\end{tabularx}" in e for e in errors) or any(
            "\\begin{tabular}" in e and "ended by \\end{adjustbox}" in e for e in errors
        ):
            lines = content.split("\n")
            new_lines: List[str] = []
            changed = False

            def _comment_line(line: str) -> Tuple[str, bool]:
                stripped = line.lstrip()
                if stripped.startswith('%'):
                    return line, False
                prefix = line[: len(line) - len(stripped)]
                return f"{prefix}% {stripped}", True

            target_envs = {"tabular", "tabularx", "adjustbox", "tabular*", "table", "table*"}
            env_stack: List[str] = []

            begin_re = re.compile(r"\\begin\{([^}]+)\}")
            end_re = re.compile(r"\\end\{([^}]+)\}")

            for line in lines:
                lstripped = line.lstrip()
                is_commented = lstripped.startswith('%')

                if not is_commented:
                    m_begin = begin_re.search(line)
                    if m_begin:
                        env = m_begin.group(1)
                        if env in target_envs:
                            env_stack.append(env)

                if env_stack:
                    commented, did = _comment_line(line)
                    changed = changed or did
                    new_lines.append(commented)
                else:
                    new_lines.append(line)

                if not is_commented:
                    m_end = end_re.search(line)
                    if m_end and env_stack:
                        env = m_end.group(1)
                        if env in env_stack:
                            while env_stack and env_stack[-1] != env:
                                env_stack.pop()
                            if env_stack and env_stack[-1] == env:
                                env_stack.pop()

            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: закомментированы блоки tabular/tabularx/adjustbox из-за несовпадения окружений")

            # Fallback: disable all table/table* blocks entirely (resilient mode).
            # These mismatch errors often cascade; commenting the whole float is the safest.
            lines = content.split("\n")
            new_lines = []
            changed2 = False
            in_table: Optional[str] = None
            for line in lines:
                lstripped = line.lstrip()
                is_commented = lstripped.startswith('%')

                if in_table is None and not is_commented:
                    if "\\begin{table*}" in line:
                        in_table = "table*"
                    elif "\\begin{table}" in line:
                        in_table = "table"

                if in_table is not None:
                    commented, did = _comment_line(line)
                    changed2 = changed2 or did
                    new_lines.append(commented)
                    if not is_commented and f"\\end{{{in_table}}}" in line:
                        in_table = None
                    continue

                new_lines.append(line)

            if changed2:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: отключены все table/table* блоки (fallback) из-за несовпадения окружений")

        if any("Paragraph ended before \\title was complete" in e for e in errors):
            lines = content.split("\n")
            new_lines: List[str] = []
            changed = False

            def _comment_line(line: str) -> Tuple[str, bool]:
                stripped = line.lstrip()
                if stripped.startswith('%'):
                    return line, False
                prefix = line[: len(line) - len(stripped)]
                return f"{prefix}% {stripped}", True

            in_title = False
            brace_depth = 0
            inserted_replacement = False
            title_lines = 0
            boundary_re = re.compile(
                r"^\s*\\(author|address|begin\{abstract\}|begin\{keyword\}|end\{frontmatter\}|tnotetext|cortext|ead|journal)\b"
            )
            for line in lines:
                if not in_title and "\\title" in line and "{" in line:
                    in_title = True
                    brace_depth = _brace_delta_tex(line)
                    title_lines = 1
                    commented, did = _comment_line(line)
                    changed = changed or did
                    new_lines.append(commented)
                    if brace_depth <= 0:
                        in_title = False
                        if not inserted_replacement:
                            new_lines.append("\\title{}")
                            inserted_replacement = True
                    continue

                if in_title:
                    if (not line.lstrip().startswith('%') and boundary_re.search(line)) or title_lines >= 60:
                        in_title = False
                        if not inserted_replacement:
                            new_lines.append("\\title{}")
                            inserted_replacement = True
                        new_lines.append(line)
                        continue

                    title_lines += 1
                    brace_depth += _brace_delta_tex(line)
                    commented, did = _comment_line(line)
                    changed = changed or did
                    new_lines.append(commented)
                    if brace_depth <= 0:
                        in_title = False
                        if not inserted_replacement:
                            new_lines.append("\\title{}")
                            inserted_replacement = True
                    continue

                new_lines.append(line)

            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: заменён повреждённый блок \\title на безопасный")

        if any("Paragraph ended before \\@textcolor was complete" in e for e in errors) or any(
            "Too many }" in e for e in errors
        ):
            if "\\begin{document}" in content:
                override_block = "\\providecommand{\\inserted}[1]{#1}\n" \
                                 "\\providecommand{\\moved}[1]{#1}\n" \
                                 "\\providecommand{\\modified}[1]{#1}\n" \
                                 "\\providecommand{\\insertedd}[1]{#1}\n" \
                                 "\\renewcommand{\\inserted}[1]{#1}\n" \
                                 "\\renewcommand{\\moved}[1]{#1}\n" \
                                 "\\renewcommand{\\modified}[1]{#1}\n" \
                                 "\\renewcommand{\\insertedd}[1]{#1}\n"
                if override_block not in content:
                    lines = content.split("\n")
                    docclass_re = re.compile(r"^\s*\\documentclass(\[[^\]]*\])?\{[^}]+\}")
                    doc_idx = None
                    for i, line in enumerate(lines):
                        if docclass_re.search(line):
                            doc_idx = i
                            break
                    if doc_idx is not None:
                        insert_pos = doc_idx + 1
                        lines[insert_pos:insert_pos] = override_block.rstrip("\n").split("\n")
                        content = "\n".join(lines)
                    else:
                        content = content.replace("\\begin{document}", override_block + "\\begin{document}")
                    fixed = True
                    self.logger.info("Исправлено: отключены макросы inserted/moved/modified для предотвращения ошибок \\textcolor")
            lines = content.split("\n")
            new_lines = []
            changed = False
            sidenote_re = re.compile(r"\\sidenote[A-Za-z]*\s*\{")
            for line in lines:
                if sidenote_re.search(line) and not line.lstrip().startswith('%'):
                    prefix = line[: len(line) - len(line.lstrip())]
                    new_lines.append(f"{prefix}% {line.lstrip()}")
                    changed = True
                else:
                    new_lines.append(line)
            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: закомментированы вызовы \\sidenote* из-за ошибок баланса скобок")

        if any("There's no line here to end" in e for e in errors) or any("Missing \\begin{document}" in e for e in errors) or any(
            "macro parameter character #" in e for e in errors
        ):
            # Common corruption mode in translated sources: lines like
            #   \\renewcommand{\\inserted}[1]{...}
            # LaTeX treats leading \\ as a linebreak command, breaking the preamble.
            # Normalize these into valid LaTeX before any further fixes.
            lines = content.split("\n")
            new_lines: List[str] = []
            changed = False
            for line in lines:
                if line.lstrip().startswith('%'):
                    new_lines.append(line)
                    continue

                new_line = line
                new_line = re.sub(
                    r"^(\s*)\\\\renewcommand\b",
                    lambda m: m.group(1) + r"\renewcommand",
                    new_line,
                )
                new_line = re.sub(
                    r"^(\s*)\\\\newcommand\b",
                    lambda m: m.group(1) + r"\newcommand",
                    new_line,
                )
                new_line = re.sub(
                    r"\{\\\\\\\\(inserted|moved|modified|insertedd)\}",
                    lambda m: "{\\" + m.group(1) + "}",
                    new_line,
                )

                new_line = re.sub(
                    r"\{\\\\(inserted|moved|modified|insertedd)\}",
                    lambda m: "{\\" + m.group(1) + "}",
                    new_line,
                )

                if new_line != line:
                    changed = True
                new_lines.append(new_line)

            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: нормализованы строки вида \\\\renewcommand{\\\\inserted} (двойные слэши)")

            lines = content.split("\n")
            new_lines: List[str] = []
            changed = False
            bad_cmd_re = re.compile(r"^\s*\\\\(re)?newcommand\{\\\\(inserted|moved|modified|insertedd)\}", re.IGNORECASE)
            for line in lines:
                if line.lstrip().startswith('%'):
                    new_lines.append(line)
                    continue
                if bad_cmd_re.search(line):
                    prefix = line[: len(line) - len(line.lstrip())]
                    new_lines.append(f"{prefix}% {line.lstrip()}")
                    changed = True
                else:
                    new_lines.append(line)
            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: закомментированы битые строки \\\\renewcommand/\\\\newcommand для inserted/moved/modified")

            # Also neutralize cases like: "\\renewcommand{\\inserted}..." (double backslash at line start).
            # In LaTeX, leading "\\" is a linebreak command and breaks the preamble.
            lines = content.split("\n")
            new_lines = []
            changed = False
            bad_double_slash_re = re.compile(
                r"^\s*\\\\(re)?newcommand\*?\s*\{\\\\(inserted|moved|modified|insertedd)\}\b",
                re.IGNORECASE,
            )
            for line in lines:
                if line.lstrip().startswith('%'):
                    new_lines.append(line)
                    continue
                if bad_double_slash_re.search(line):
                    commented, did = _comment_tex_line(line)
                    new_lines.append(commented)
                    changed = changed or did
                else:
                    new_lines.append(line)
            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: закомментированы строки \\\\renewcommand{\\\\inserted}* (двойной слэш в начале строки)")

            # Some elsarticle sources define algorithmic comment helpers using nested macros.
            # If braces are corrupted (often by translation), this triggers Runaway argument / \@argdef
            # and cascades into Missing \begin{document}.
            lines = content.split("\n")
            new_lines = []
            changed = False
            begin_doc_re = re.compile(r"^\s*\\begin\{document\}")
            start_re = re.compile(
                r"^\s*\\(re)?newcommand\*?\s*\\algorithmiccomment\b|^\s*\\newcommand\*?\s*\\LONGCOMMENT\b",
                re.IGNORECASE,
            )
            in_bad_def = False
            brace_depth = 0
            for line in lines:
                if begin_doc_re.search(line):
                    in_bad_def = False
                    brace_depth = 0
                    new_lines.append(line)
                    continue

                if line.lstrip().startswith('%'):
                    new_lines.append(line)
                    continue

                if not in_bad_def and start_re.search(line):
                    commented, did = _comment_tex_line(line)
                    new_lines.append(commented)
                    changed = changed or did
                    in_bad_def = True
                    brace_depth = _brace_delta_tex(line)
                    if brace_depth <= 0:
                        in_bad_def = False
                        brace_depth = 0
                    continue

                if in_bad_def:
                    commented, did = _comment_tex_line(line)
                    new_lines.append(commented)
                    changed = changed or did
                    brace_depth += _brace_delta_tex(line)
                    if brace_depth <= 0:
                        in_bad_def = False
                        brace_depth = 0
                    continue

                new_lines.append(line)

            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: закомментированы определения \\algorithmiccomment/\\LONGCOMMENT (ломали \@argdef)")

        if any("Runaway argument" in e for e in errors) or any("\\@argdef" in e for e in errors):
            lines = content.split("\n")
            new_lines: List[str] = []
            changed = False

            # Special-case: some sources define a helper like
            #   \newcommand{\sqimg}[2]{% ...
            # but the closing brace is missing. This creates a runaway \@argdef in the body.
            # We comment the broken definition block and insert a safe stub.
            sqimg_start_re = re.compile(r"^\s*\\newcommand\s*\{\\sqimg\}\s*\[2\]\s*\{", re.IGNORECASE)
            if any("\\@argdef" in e for e in errors) and any(sqimg_start_re.search(ln) for ln in lines):
                tmp_lines: List[str] = []
                in_sqimg = False
                inserted_stub = False
                local_changed = False
                for line in lines:
                    stripped = line.lstrip()
                    is_comment = stripped.startswith('%')

                    if (not in_sqimg) and (not is_comment) and sqimg_start_re.search(line):
                        commented, did = _comment_tex_line(line)
                        tmp_lines.append(commented)
                        local_changed = local_changed or did
                        in_sqimg = True
                        continue

                    if in_sqimg:
                        # End the commented-out region once we reach the first figure (or a clear boundary).
                        if re.search(r"^\s*\\begin\{figure\}", line):
                            if not inserted_stub:
                                tmp_lines.append(r"\newcommand{\sqimg}[2]{}");
                                inserted_stub = True
                                local_changed = True
                            in_sqimg = False
                            tmp_lines.append(line)
                            continue

                        if is_comment:
                            tmp_lines.append(line)
                        else:
                            commented, did = _comment_tex_line(line)
                            tmp_lines.append(commented)
                            local_changed = local_changed or did
                        continue

                    tmp_lines.append(line)

                if local_changed:
                    content = "\n".join(tmp_lines)
                    lines = content.split("\n")
                    fixed = True
                    self.logger.info("Исправлено: закомментирован сломанный блок \\newcommand{\\sqimg} и добавлен безопасный stub")

            appendix_setup_re = re.compile(r"^\s*\\newcommand\s*\{\\AppendixSetup\}\s*\{", re.IGNORECASE)
            appendix_title_re = re.compile(r"^\s*\\newcommand\s*\{\\AppendixTitlePage\}\s*\{", re.IGNORECASE)
            if any("\\@argdef" in e for e in errors) and (
                any(appendix_setup_re.search(ln) for ln in lines) or any(appendix_title_re.search(ln) for ln in lines)
            ):
                tmp_lines: List[str] = []
                in_setup = False
                in_title = False
                inserted_setup_stub = False
                inserted_title_stub = False
                local_changed = False

                for line in lines:
                    stripped = line.lstrip()
                    is_comment = stripped.startswith('%')

                    if (not in_setup) and (not is_comment) and appendix_setup_re.search(line):
                        commented, did = _comment_tex_line(line)
                        tmp_lines.append(commented)
                        local_changed = local_changed or did
                        in_setup = True
                        continue

                    if (not in_title) and (not is_comment) and appendix_title_re.search(line):
                        commented, did = _comment_tex_line(line)
                        tmp_lines.append(commented)
                        local_changed = local_changed or did
                        in_title = True
                        continue

                    if (not inserted_setup_stub) and re.search(r"^\s*\\AppendixSetup\b", line):
                        tmp_lines.append(r"\newcommand{\AppendixSetup}{\appendix}")
                        inserted_setup_stub = True
                        local_changed = True

                    if (not inserted_title_stub) and re.search(r"^\s*\\AppendixTitlePage\b", line):
                        tmp_lines.append(r"\newcommand{\AppendixTitlePage}{}");
                        inserted_title_stub = True
                        local_changed = True

                    if in_setup or in_title:
                        if is_comment:
                            tmp_lines.append(line)
                        else:
                            commented, did = _comment_tex_line(line)
                            tmp_lines.append(commented)
                            local_changed = local_changed or did

                        if re.search(r"^\s*\\AppendixSetup\b", line):
                            in_setup = False
                        if re.search(r"^\s*\\AppendixTitlePage\b", line):
                            in_title = False
                        continue

                    tmp_lines.append(line)

                if local_changed:
                    content = "\n".join(tmp_lines)
                    lines = content.split("\n")
                    fixed = True
                    self.logger.info("Исправлено: нейтрализованы битые определения AppendixSetup/AppendixTitlePage (\\@argdef)")

            def_re = re.compile(
                r"^\s*\\(re)?newcommand\*?\s*(\{\\[A-Za-z@]+\}|\\[A-Za-z@]+)\b",
                re.IGNORECASE,
            )

            in_def = False
            brace_depth = 0

            # Only repair preamble (to avoid touching body content).
            for line in lines:
                if (not in_def) and re.search(r"^\s*\\begin\{document\}", line):
                    if brace_depth > 0:
                        new_lines.append("}" * brace_depth)
                        changed = True
                    in_def = False
                    brace_depth = 0
                    new_lines.append(line)
                    # After \begin{document} we stop trying to repair definitions.
                    new_lines.extend(lines[len(new_lines):])
                    break

                if line.lstrip().startswith('%'):
                    new_lines.append(line)
                    continue

                if in_def and def_re.search(line):
                    if brace_depth > 0:
                        new_lines.append("}" * brace_depth)
                        changed = True
                    in_def = False
                    brace_depth = 0

                if def_re.search(line) and "{" in line:
                    in_def = True
                    brace_depth += _brace_delta_tex(line)
                    new_lines.append(line)
                    if brace_depth <= 0:
                        in_def = False
                        brace_depth = 0
                    continue

                if in_def:
                    brace_depth += _brace_delta_tex(line)
                    new_lines.append(line)
                    if brace_depth <= 0:
                        in_def = False
                        brace_depth = 0
                    continue

                new_lines.append(line)

            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: закрыты незакрытые определения команд (Runaway argument/\\@argdef)")

        if any("pgfkeys@@qset" in e for e in errors):
            # Typical failure mode: an unclosed \tikzset{...} block (often node style maps)
            # makes pgfkeys scan to EOF. We comment the offending tikzset blocks up to
            # the next \begin{tikzpicture}, which is sufficient to restore compilation.
            lines = content.split("\n")
            new_lines: List[str] = []
            changed = False
            in_tikzset = False
            tikzset_start_re = re.compile(r"^\s*\\tikzset\s*\{")
            node_style_re = re.compile(r"node\s+[123]\/\.style\s*=", re.IGNORECASE)
            for line in lines:
                stripped = line.lstrip()
                is_comment = stripped.startswith('%')

                if (not in_tikzset) and (not is_comment) and tikzset_start_re.search(line):
                    in_tikzset = True
                    commented, did = _comment_tex_line(line)
                    new_lines.append(commented)
                    changed = changed or did
                    continue

                if in_tikzset:
                    # Stop commenting when the actual picture begins.
                    if re.search(r"^\s*\\begin\{tikzpicture\}", line):
                        in_tikzset = False
                        new_lines.append(line)
                        continue

                    # Comment everything inside; this neutralizes the unbalanced pgfkeys input.
                    if is_comment:
                        new_lines.append(line)
                    else:
                        commented, did = _comment_tex_line(line)
                        new_lines.append(commented)
                        changed = changed or did
                    continue

                # If we see node style lines outside tikzset (rare), comment them as well.
                if (not is_comment) and node_style_re.search(line):
                    commented, did = _comment_tex_line(line)
                    new_lines.append(commented)
                    changed = changed or did
                    continue

                new_lines.append(line)

            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: закомментированы битые tikzset-блоки (pgfkeys@@qset runaway)")

        if any("Missing } inserted" in e for e in errors) and ("\\begin{wraptable" in content):
            lines = content.split("\n")
            new_lines: List[str] = []
            changed = False
            in_wrap = False
            begin_re = re.compile(r"^\s*\\begin\{wraptable\}")
            end_re = re.compile(r"^\s*\\end\{wraptable\}")
            for line in lines:
                stripped = line.lstrip()
                is_comment = stripped.startswith('%')

                if (not in_wrap) and (not is_comment) and begin_re.search(line):
                    in_wrap = True
                    commented, did = _comment_tex_line(line)
                    new_lines.append(commented)
                    changed = changed or did
                    continue

                if in_wrap:
                    if is_comment:
                        new_lines.append(line)
                    else:
                        commented, did = _comment_tex_line(line)
                        new_lines.append(commented)
                        changed = changed or did
                    if end_re.search(line):
                        in_wrap = False
                    continue

                if (not is_comment) and end_re.search(line):
                    commented, did = _comment_tex_line(line)
                    new_lines.append(commented)
                    changed = changed or did
                    continue

                new_lines.append(line)

            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: закомментированы wraptable-блоки из-за Missing } inserted")

        if any("Package titletoc Error" in e and "No partial toc named" in e for e in errors):
            lines = content.split("\n")
            new_lines: List[str] = []
            changed = False
            for line in lines:
                stripped = line.lstrip()
                if stripped.startswith('%'):
                    new_lines.append(line)
                    continue
                if re.search(r"\\printcontents\s*\[\s*app\s*\]", line) or re.search(
                    r"\\startcontents\s*\[\s*app\s*\]", line
                ):
                    commented, did = _comment_tex_line(line)
                    new_lines.append(commented)
                    changed = changed or did
                    continue
                # titletoc itself is optional; disabling it often avoids fragile partial toc state.
                if re.search(r"\\usepackage\s*(\[[^\]]*\])?\s*\{\s*titletoc\s*\}", line):
                    commented, did = _comment_tex_line(line)
                    new_lines.append(commented)
                    changed = changed or did
                    continue
                new_lines.append(line)
            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: отключены titletoc partial toc (printcontents/startcontents[app])")

        if any("__tcobox_new_tcolorbox:w" in e for e in errors):
            lines = content.split("\n")
            new_lines: List[str] = []
            changed = False
            in_def = False
            brace_depth = 0
            begin_def_re = re.compile(r"^\s*\\newtcolorbox\s*\{\s*promptbox\s*\}")
            for line in lines:
                stripped = line.lstrip()
                is_comment = stripped.startswith('%')

                if (not in_def) and (not is_comment) and begin_def_re.search(line):
                    in_def = True
                    brace_depth = _brace_delta_tex(line)
                    commented, did = _comment_tex_line(line)
                    new_lines.append(commented)
                    changed = changed or did
                    # Provide a safe fallback environment so later \begin{promptbox} doesn't error.
                    prefix = line[: len(line) - len(stripped)]
                    new_lines.append(f"{prefix}\\newenvironment{{promptbox}}[1]{{}}{{--}}")
                    changed = True
                    if brace_depth <= 0:
                        in_def = False
                        brace_depth = 0
                    continue

                if in_def:
                    brace_depth += _brace_delta_tex(line)
                    if is_comment:
                        new_lines.append(line)
                    else:
                        commented, did = _comment_tex_line(line)
                        new_lines.append(commented)
                        changed = changed or did
                    if brace_depth <= 0:
                        in_def = False
                        brace_depth = 0
                    continue

                new_lines.append(line)

            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: закомментирован битый \newtcolorbox{promptbox} (\__tcobox_new_tcolorbox:w runaway)")

        if any("Too many }" in e for e in errors):
            lines = content.split("\n")
            new_lines: List[str] = []
            changed = False

            def _strip_orphan_closing_braces(line: str) -> Tuple[str, bool]:
                bal = 0
                out: List[str] = []
                i = 0
                local_changed = False
                while i < len(line):
                    ch = line[i]
                    if ch == "\\" and (i + 1) < len(line):
                        out.append(ch)
                        i += 1
                        out.append(line[i])
                        i += 1
                        continue
                    if ch == "{":
                        bal += 1
                        out.append(ch)
                        i += 1
                        continue
                    if ch == "}":
                        if bal <= 0:
                            local_changed = True
                            i += 1
                            continue
                        bal -= 1
                        out.append(ch)
                        i += 1
                        continue
                    out.append(ch)
                    i += 1
                return "".join(out), local_changed

            in_body = False
            for line in lines:
                if (not in_body) and re.search(r"^\s*\\begin\{document\}", line):
                    in_body = True
                    new_lines.append(line)
                    continue

                if not in_body:
                    new_lines.append(line)
                    continue

                if line.lstrip().startswith('%'):
                    new_lines.append(line)
                    continue

                new_line, did = _strip_orphan_closing_braces(line)
                new_lines.append(new_line)
                changed = changed or did

            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: удалены лишние '}' (Too many }'s)")

        if any("\\Gscale@box@dd" in e for e in errors) or any("Division by 0" in e for e in errors):
            lines = content.split("\n")
            new_lines: List[str] = []
            changed = False
            in_block = False
            brace_depth = 0
            resize_re = re.compile(r"\\resizebox\b")

            for line in lines:
                if line.lstrip().startswith('%'):
                    new_lines.append(line)
                    continue

                # Never comment-out the document terminator.
                if in_block and re.search(r"^\s*\\end\{document\}\s*$", line):
                    in_block = False
                    brace_depth = 0
                    new_lines.append(line)
                    continue

                if (not in_block) and resize_re.search(line):
                    in_block = True
                    brace_depth = _brace_delta_tex(line)
                    commented, did = _comment_tex_line(line)
                    new_lines.append(commented)
                    changed = changed or did
                    if brace_depth <= 0:
                        in_block = False
                        brace_depth = 0
                    continue

                if in_block:
                    brace_depth += _brace_delta_tex(line)
                    commented, did = _comment_tex_line(line)
                    new_lines.append(commented)
                    changed = changed or did
                    if brace_depth <= 0:
                        in_block = False
                        brace_depth = 0
                    continue

                new_lines.append(line)

            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: закомментированы битые \\resizebox блоки (\\Gscale@box@dd/Division by 0)")

        if any("no legal \\end found" in e for e in errors) or any("Emergency stop" in e for e in errors):
            if not re.search(r"^\s*\\end\{document\}\s*$", content, flags=re.MULTILINE):
                # If \end{document} is present but commented, uncomment it.
                if re.search(r"^\s*%\s*\\end\{document\}\s*$", content, flags=re.MULTILINE):
                    content = re.sub(
                        r"^(\s*)%\s*(\\end\{document\})\s*$",
                        r"\1\2",
                        content,
                        flags=re.MULTILINE,
                    )
                    fixed = True
                    self.logger.info("Исправлено: раскомментирован \\end{document} (иначе no legal \\end found)")
                else:
                    content = content.rstrip() + "\n\\end{document}\n"
                    fixed = True
                    self.logger.info("Исправлено: добавлен отсутствующий \\end{document} (иначе no legal \\end found)")

        if any("\\begin{table}" in e and "ended by \\end{document}" in e for e in errors) or any(
            "internal vertical mode" in e for e in errors
        ) or any("\\Gscale@box@dd" in e for e in errors) or any("Division by 0" in e for e in errors):
            lines = content.split("\n")
            new_lines: List[str] = []
            changed = False
            in_table = False
            begin_re = re.compile(r"^\s*\\begin\{table\*?\}")
            end_re = re.compile(r"^\s*\\end\{table\*?\}")
            end_doc_any_re = re.compile(r"^\s*%?\s*\\end\{document\}\s*$")
            for line in lines:
                stripped = line.lstrip()
                is_comment = stripped.startswith('%')

                if (not in_table) and (not is_comment) and begin_re.search(line):
                    commented, did = _comment_tex_line(line)
                    new_lines.append(commented)
                    changed = changed or did
                    in_table = True
                    continue

                if in_table:
                    # Never allow the table commenter to swallow the document terminator.
                    if end_doc_any_re.search(line):
                        in_table = False
                        uncommented = re.sub(r"^(\s*)%\s*", r"\1", line)
                        new_lines.append(uncommented)
                        changed = True
                        continue

                    if (not is_comment) and end_re.search(line):
                        commented, did = _comment_tex_line(line)
                        new_lines.append(commented)
                        changed = changed or did
                        in_table = False
                        continue

                    if is_comment:
                        new_lines.append(line)
                    else:
                        commented, did = _comment_tex_line(line)
                        new_lines.append(commented)
                        changed = changed or did
                    continue

                new_lines.append(line)

            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: закомментированы битые окружения table/table*")

        if any("\\unskip" in e for e in errors) or any("\\@iiiparbox" in e for e in errors):
            # Some templates embed large instruction blocks using tcolorbox (promptbox).
            # These boxes can break in pdfLaTeX with tagging/box internals, leading to
            # \unskip in vertical mode / runaway \@iiiparbox cascades at \end{promptbox}.
            # The content is non-essential for the compiled paper, so we comment it out.
            lines = content.split("\n")
            new_lines: List[str] = []
            changed = False
            in_promptbox = False
            # Allow optional args: \begin{promptbox}[...]
            begin_re = re.compile(r"^\s*\\begin\{promptbox\}(?:\[[^\]]*\])?\s*$")
            end_re = re.compile(r"^\s*\\end\{promptbox\}")
            for line in lines:
                stripped = line.lstrip()
                is_comment = stripped.startswith('%')

                # Always neutralize any stray \begin{promptbox} / \end{promptbox} lines.
                # This also handles cases where \begin{promptbox} was already commented
                # by another fix, but \end{promptbox} remained active.
                if (not is_comment) and (begin_re.search(line) or end_re.search(line)):
                    prefix = line[: len(line) - len(stripped)]
                    new_lines.append(f"{prefix}% {stripped}")
                    changed = True
                    if begin_re.search(line):
                        in_promptbox = True
                    if end_re.search(line):
                        in_promptbox = False
                    continue

                if in_promptbox:
                    if is_comment:
                        new_lines.append(line)
                    else:
                        prefix = line[: len(line) - len(stripped)]
                        new_lines.append(f"{prefix}% {stripped}")
                        changed = True
                    continue

                new_lines.append(line)

            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: закомментированы блоки promptbox из-за ошибок tcolorbox/\\@iiiparbox")

        if any("Extra }, or forgotten \\endgroup" in e for e in errors) or re.search(
            r"\\(inserted|moved|modified|insertedd)\{[^\n%]*?(?<!\\)%", content
        ) or re.search(r"\\cite[a-zA-Z]*\{[^}]+\}\}", content):
            lines = content.split("\n")
            new_lines: List[str] = []
            changed = False
            macros = ("inserted", "moved", "modified", "insertedd")
            macro_pats = {
                m: re.compile(r"\\" + re.escape(m) + r"\{([^%]*?)(?<!\\)%") for m in macros
            }
            def _unwrap_before_comment(line: str) -> Tuple[str, bool]:
                if line.lstrip().startswith('%'):
                    return line, False
                new_line = line
                for m, pat in macro_pats.items():
                    new_line = pat.sub(r"\1%", new_line)
                return new_line, (new_line != line)

            for line in lines:
                new_line, did = _unwrap_before_comment(line)
                changed = changed or did
                new_lines.append(new_line)

            content_candidate = "\n".join(new_lines)
            lines = content_candidate.split("\n")
            new_lines = []
            changed2 = False
            cite_re = re.compile(r"(\\cite[a-zA-Z]*\{[^}]+\})\}(?=\S)")
            for line in lines:
                if line.lstrip().startswith('%'):
                    new_lines.append(line)
                    continue
                new_line = cite_re.sub(r"\1", line)
                if new_line != line:
                    changed2 = True
                new_lines.append(new_line)
            if changed or changed2:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: удалены лишние '}' после \\cite* команд")

        if any("\\begin{document} ended by \\end{definition}" in e for e in errors) or "\\end{definition}" in content:
            lines = content.split("\n")
            new_lines: List[str] = []
            changed = False

            def _comment_line(line: str) -> Tuple[str, bool]:
                stripped = line.lstrip()
                if stripped.startswith('%'):
                    return line, False
                prefix = line[: len(line) - len(stripped)]
                return f"{prefix}% {stripped}", True

            begin_re = re.compile(r"\\begin\{definition\}")
            end_re = re.compile(r"\\end\{definition\}")
            depth = 0
            for line in lines:
                if line.lstrip().startswith('%'):
                    new_lines.append(line)
                    continue

                line_out = line
                if begin_re.search(line_out):
                    depth += len(begin_re.findall(line_out))

                if end_re.search(line_out):
                    end_count = len(end_re.findall(line_out))
                    if depth <= 0:
                        line_out2 = end_re.sub("", line_out)
                        if line_out2 != line_out:
                            changed = True
                            line_out = line_out2
                    else:
                        depth = max(0, depth - end_count)

                if line_out != line:
                    new_lines.append(line_out)
                    continue

                new_lines.append(line)

            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: закомментированы осиротевшие \\end{definition}")

        if any(
            ("Too many }" in e)
            or ("Extra }, or forgotten \\endgroup" in e)
            or ("Paragraph ended before \\@textcolor was complete" in e)
            or ("endabstract" in e)
            for e in errors
        ) and "\\begin{abstract}" in content and "\\end{abstract}" in content:
            lines = content.split("\n")
            new_lines: List[str] = []
            changed = False
            in_abstract = False
            for line in lines:
                if not in_abstract and "\\begin{abstract}" in line and not line.lstrip().startswith('%'):
                    in_abstract = True
                    new_lines.append(line)
                    changed = True
                    continue

                if in_abstract:
                    if "\\end{abstract}" in line and not line.lstrip().startswith('%'):
                        new_lines.append(line)
                        in_abstract = False
                        continue
                    changed = True
                    continue

                new_lines.append(line)

            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: очищено содержимое окружения abstract из-за каскада ошибок баланса скобок")

        if any("Too many }" in e for e in errors) or any("Extra }, or forgotten \\endgroup" in e for e in errors):
            def _remove_unmatched_closing_braces(src: str) -> Tuple[str, bool]:
                out_chars: List[str] = []
                depth = 0
                i = 0
                changed_local = False
                while i < len(src):
                    ch = src[i]
                    if ch == "\\":
                        if i + 1 < len(src):
                            out_chars.append(ch)
                            out_chars.append(src[i + 1])
                            i += 2
                            continue
                        out_chars.append(ch)
                        i += 1
                        continue

                    if ch == "%":
                        out_chars.append(ch)
                        i += 1
                        while i < len(src) and src[i] != "\n":
                            out_chars.append(src[i])
                            i += 1
                        continue

                    if ch == "{":
                        depth += 1
                        out_chars.append(ch)
                        i += 1
                        continue

                    if ch == "}":
                        if depth <= 0:
                            changed_local = True
                            i += 1
                            continue
                        depth -= 1
                        out_chars.append(ch)
                        i += 1
                        continue

                    out_chars.append(ch)
                    i += 1

                return "".join(out_chars), changed_local

            new_content, did = _remove_unmatched_closing_braces(content)
            if did:
                content = new_content
                fixed = True
                self.logger.info("Исправлено: удалены лишние закрывающие '}' для восстановления баланса групп")

        if any("Package graphics Error: Division by 0" in e for e in errors):
            lines = content.split("\n")
            new_lines: List[str] = []
            changed = False

            def _comment_line(line: str) -> Tuple[str, bool]:
                stripped = line.lstrip()
                if stripped.startswith('%'):
                    return line, False
                prefix = line[: len(line) - len(stripped)]
                return f"{prefix}% {stripped}", True

            recent: List[str] = []
            for line in lines:
                stripped = line.strip()
                if stripped == "}":
                    if any(
                        ("\\resizebox" in l or "\\scalebox" in l or "\\begin{adjustbox}" in l)
                        and l.lstrip().startswith('%')
                        for l in recent[-8:]
                    ):
                        commented, did = _comment_line(line)
                        changed = changed or did
                        new_lines.append(commented)
                        recent.append(line)
                        continue

                new_lines.append(line)
                recent.append(line)

            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: закомментированы лишние '}' после закомментированных resizebox/scalebox")

        if any("Lonely \\item" in e for e in errors) or any(
            "\\begin{document} ended by \\end{enumerate}" in e for e in errors
        ) or any("\\begin{document} ended by \\end{itemize}" in e for e in errors) or any(
            "\\begin{document} ended by \\end{description}" in e for e in errors
        ):
            lines = content.split("\n")
            new_lines: List[str] = []
            changed = False

            def _comment_line(line: str) -> Tuple[str, bool]:
                stripped = line.lstrip()
                if stripped.startswith('%'):
                    return line, False
                prefix = line[: len(line) - len(stripped)]
                return f"{prefix}% {stripped}", True

            list_envs = {"itemize", "enumerate", "description", "itemize*", "enumerate*", "description*"}
            list_stack: List[str] = []

            begin_re = re.compile(r"^\s*\\begin\{([^}]+)\}")
            end_re = re.compile(r"^\s*\\end\{([^}]+)\}")
            item_re = re.compile(r"^\s*\\item\b")

            def _find_matching_end(start_idx: int) -> Optional[str]:
                window_end = min(len(lines), start_idx + 120)
                for j in range(start_idx + 1, window_end):
                    l = lines[j]
                    if l.lstrip().startswith('%'):
                        continue
                    m_begin = begin_re.search(l)
                    if m_begin and m_begin.group(1) in list_envs:
                        return None
                    m_end = end_re.search(l)
                    if m_end and m_end.group(1) in list_envs:
                        return m_end.group(1)
                return None

            for i, line in enumerate(lines):
                m_begin = begin_re.search(line)
                if m_begin:
                    env = m_begin.group(1)
                    if env in list_envs:
                        list_stack.append(env)

                m_end = end_re.search(line)
                if m_end:
                    env = m_end.group(1)
                    if env in list_envs:
                        if list_stack and list_stack[-1] == env:
                            list_stack.pop()
                            new_lines.append(line)
                            continue
                        commented, did = _comment_line(line)
                        changed = changed or did
                        new_lines.append(commented)
                        continue

                if item_re.search(line) and not list_stack:
                    inferred = _find_matching_end(i)
                    if inferred is not None:
                        new_lines.append(f"\\begin{{{inferred}}}")
                        list_stack.append(inferred)
                        changed = True
                        new_lines.append(line)
                        continue

                    commented, did = _comment_line(line)
                    changed = changed or did
                    new_lines.append(commented)
                    continue

                new_lines.append(line)

            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: закомментированы stray item/ends вне list-окружений")

        if any("Package enumitem Error: Misplaced \\item." in e for e in errors) or any(
            "Something's wrong--perhaps a missing \\item" in e for e in errors
        ):
            lines = content.split("\n")
            new_lines: List[str] = []
            changed = False

            def _comment_line(line: str) -> Tuple[str, bool]:
                stripped = line.lstrip()
                if stripped.startswith('%'):
                    return line, False
                prefix = line[: len(line) - len(stripped)]
                return f"{prefix}% {stripped}", True

            starred_envs = {"enumerate*", "itemize*", "description*"}
            in_starred: Optional[str] = None
            for line in lines:
                if in_starred is None:
                    for env in starred_envs:
                        if f"\\begin{{{env}}}" in line:
                            in_starred = env
                            break

                if in_starred is not None:
                    commented, did = _comment_line(line)
                    changed = changed or did
                    new_lines.append(commented)
                    if f"\\end{{{in_starred}}}" in line:
                        in_starred = None
                    continue

                new_lines.append(line)

            if changed:
                content = "\n".join(new_lines)
                fixed = True
                self.logger.info("Исправлено: отключены starred list-окружения (enumitem) из-за ошибки missing/Misplaced \\item")

        if any("begin{tabular}" in e and "end{adjustbox}" in e for e in errors):
            lines = content.split("\n")
            new_lines: List[str] = []
            changed = False

            def _comment_line(line: str) -> Tuple[str, bool]:
                stripped = line.lstrip()
                if stripped.startswith('%'):
                    return line, False
                prefix = line[: len(line) - len(stripped)]
                return f"{prefix}% {stripped}", True

            # First, fix the common pattern: a fully commented wraptable block where
            # a few lines (like \begin{tabular} / \end{adjustbox}) remain uncommented.
            wraptable_ranges: List[Tuple[int, int]] = []
            i = 0
            while i < len(lines):
                if re.search(r"^\s*%.*\\begin\{wraptable\}", lines[i]):
                    j = i + 1
                    while j < len(lines) and not re.search(r"^\s*%.*\\end\{wraptable\}", lines[j]):
                        j += 1
                    if j < len(lines):
                        wraptable_ranges.append((i, j))
                        i = j + 1
                        continue
                i += 1

            if wraptable_ranges:
                changed_block = False
                for idx, line in enumerate(lines):
                    in_range = any(start <= idx <= end for start, end in wraptable_ranges)
                    if not in_range:
                        continue
                    commented_line, did = _comment_line(line)
                    if did:
                        lines[idx] = commented_line
                        changed_block = True
                if changed_block:
                    changed = True

            for i, line in enumerate(lines):
                if "\\begin{tabular}" not in line:
                    new_lines.append(line)
                    continue

                stripped = line.lstrip()
                if stripped.startswith('%'):
                    new_lines.append(line)
                    continue

                prev_commented = i > 0 and lines[i - 1].lstrip().startswith('%')
                next_commented = i + 1 < len(lines) and lines[i + 1].lstrip().startswith('%')
                if prev_commented or next_commented:
                    commented_line, did = _comment_line(line)
                    if did:
                        changed = True
                        line = commented_line
                    new_lines.append(line)
                    continue

                window_start = max(0, i - 15)
                prev_window = "\n".join(lines[window_start:i])
                if not re.search(r"^\s*%.*\\begin\{wraptable\}", prev_window, re.MULTILINE) and not re.search(
                    r"^\s*%.*(\\begin\{adjustbox\}|\\resizebox)", prev_window, re.MULTILINE
                ):
                    new_lines.append(line)
                    continue

                commented_line, did = _comment_line(line)
                if did:
                    changed = True
                    line = commented_line
                new_lines.append(line)

            content_candidate = "\n".join(new_lines)
            lines2 = content_candidate.split("\n")
            new_lines2: List[str] = []
            changed2 = False
            for i, line in enumerate(lines2):
                if "\\end{adjustbox}" in line and not line.lstrip().startswith('%'):
                    prev_commented = i > 0 and lines2[i - 1].lstrip().startswith('%')
                    next_commented = i + 1 < len(lines2) and lines2[i + 1].lstrip().startswith('%')
                    in_wraptable_range = any(start <= i <= end for start, end in wraptable_ranges)
                    if prev_commented or next_commented or in_wraptable_range:
                        commented_line, did = _comment_line(line)
                        if did:
                            changed2 = True
                            line = commented_line
                new_lines2.append(line)

            content_after = "\n".join(new_lines2)
            if changed or changed2:
                content = content_after
                fixed = True
                self.logger.info("Исправлено: закомментированы осиротевшие строки tabular/adjustbox в закомментированном блоке")

        # TODO: Интегрировать LLM для сложных исправлений

        return content if fixed else None

    def _cleanup_temp_files(self, output_dir: Path, base_stem: str):
        """
        Очищает старые временные файлы от предыдущих попыток компиляции.
        
        Args:
            output_dir: Директория с файлами
            base_stem: Базовое имя файла (например, "translated")
        """
        # Паттерн для временных файлов: translated_fixed_1.tex, translated_fixed_2.tex и т.д.
        pattern = f"{base_stem}_fixed_*"
        
        import glob
        temp_files = list(output_dir.glob(f"{base_stem}_fixed_*.*"))
        
        if temp_files:
            self.logger.info(f"Очистка {len(temp_files)} старых временных файлов...")
            for temp_file in temp_files:
                try:
                    temp_file.unlink()
                except Exception as e:
                    self.logger.warning(f"Не удалось удалить {temp_file}: {e}")

    def _cleanup_temp_files_from_list(self, temp_tex_files: List[Path], output_dir: Path):
        """
        Удаляет временные файлы и все связанные с ними файлы компиляции.
        
        Args:
            temp_tex_files: Список путей к временным .tex файлам
            output_dir: Директория с файлами
        """
        extensions = ['.tex', '.aux', '.log', '.out', '.pdf', '.bbl', '.blg', '.bcf', '.run.xml', '.synctex.gz']
        
        for tex_file in temp_tex_files:
            if not tex_file.exists():
                continue
                
            stem = tex_file.stem
            removed_count = 0
            
            for ext in extensions:
                temp_file = output_dir / f"{stem}{ext}"
                if temp_file.exists():
                    try:
                        temp_file.unlink()
                        removed_count += 1
                    except Exception as e:
                        self.logger.warning(f"Не удалось удалить {temp_file}: {e}")
            
            if removed_count > 0:
                self.logger.info(f"Удалено {removed_count} временных файлов для {stem}")

# Convenience function
def compile_pdf(tex_path: Path) -> CompilationResult:
    compiler = PDFCompiler()
    return compiler.compile_pdf(tex_path)

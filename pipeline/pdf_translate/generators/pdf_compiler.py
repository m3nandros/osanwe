"""
PDF Compiler.

Compiles LaTeX document to PDF using XeLaTeX or LuaLaTeX.
"""

import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class CompilationResult:
    """Result of LaTeX compilation."""
    success: bool
    pdf_path: Optional[Path] = None
    error_message: str = ""
    log_content: str = ""


class PDFCompiler:
    """
    Compiles LaTeX documents to PDF.
    
    Uses XeLaTeX or LuaLaTeX for Unicode and OpenType font support.
    """
    
    def __init__(
        self,
        engine: str = "xelatex",
        timeout_seconds: int = 120
    ):
        """
        Initialize compiler.
        
        Args:
            engine: LaTeX engine ("xelatex" or "lualatex")
            timeout_seconds: Timeout for compilation
        """
        self.engine = engine
        self.timeout = timeout_seconds
        
        # Verify engine is available
        if not self._check_engine_available():
            logger.warning(
                f"{engine} not found in PATH. "
                "Please install TeX Live or MacTeX."
            )
    
    def _check_engine_available(self) -> bool:
        """Check if LaTeX engine is available."""
        try:
            result = subprocess.run(
                [self.engine, "--version"],
                capture_output=True,
                timeout=10
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, FileNotFoundError):
            return False
    
    def compile(
        self,
        latex_content: str,
        output_path: Path,
        images_folder: Optional[Path] = None,
        num_passes: int = 2
    ) -> CompilationResult:
        """
        Compile LaTeX to PDF.
        
        Args:
            latex_content: LaTeX document content
            output_path: Path for output PDF
            images_folder: Folder containing images (will be copied)
            num_passes: Number of compilation passes (for cross-references)
        
        Returns:
            CompilationResult with success status and paths
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            
            # Write LaTeX file
            tex_file = temp_path / "document.tex"
            tex_file.write_text(latex_content, encoding="utf-8")
            
            # Copy images if provided
            if images_folder and images_folder.exists():
                images_dest = temp_path / "images"
                shutil.copytree(images_folder, images_dest)
            
            # Run compilation
            log_content = ""
            
            for pass_num in range(num_passes):
                logger.debug(f"Compilation pass {pass_num + 1}/{num_passes}")
                
                try:
                    result = subprocess.run(
                        [
                            self.engine,
                            "-interaction=nonstopmode",
                            "-halt-on-error",
                            "-output-directory", str(temp_path),
                            str(tex_file)
                        ],
                        capture_output=True,
                        timeout=self.timeout,
                        cwd=temp_path,
                        env={**os.environ, "TEXINPUTS": f".:{temp_path}:"}
                    )
                    
                    # Decode with error handling for non-UTF8 characters
                    stdout = result.stdout.decode("utf-8", errors="replace")
                    stderr = result.stderr.decode("utf-8", errors="replace")
                    log_content = stdout + stderr
                    
                    if result.returncode != 0:
                        # Try to extract meaningful error
                        log_file = temp_path / "document.log"
                        error = self._extract_error(log_file)
                        
                        return CompilationResult(
                            success=False,
                            error_message=error or "Compilation failed",
                            log_content=log_content
                        )
                        
                except subprocess.TimeoutExpired:
                    return CompilationResult(
                        success=False,
                        error_message=f"Compilation timed out after {self.timeout}s",
                        log_content=log_content
                    )
                except Exception as e:
                    return CompilationResult(
                        success=False,
                        error_message=f"Compilation error: {str(e)}",
                        log_content=log_content
                    )
            
            # Check for output PDF
            pdf_file = temp_path / "document.pdf"
            
            if not pdf_file.exists():
                return CompilationResult(
                    success=False,
                    error_message="PDF file was not generated",
                    log_content=log_content
                )
            
            # Copy PDF to output path
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(pdf_file, output_path)
            
            logger.info(f"PDF compiled successfully: {output_path}")
            
            return CompilationResult(
                success=True,
                pdf_path=output_path,
                log_content=log_content
            )
    
    def compile_file(
        self,
        tex_path: Path,
        output_path: Optional[Path] = None,
        num_passes: int = 2
    ) -> CompilationResult:
        """
        Compile a .tex file to PDF.
        
        Args:
            tex_path: Path to .tex file
            output_path: Path for output PDF (default: same name as input)
            num_passes: Number of compilation passes
        
        Returns:
            CompilationResult
        """
        if not tex_path.exists():
            return CompilationResult(
                success=False,
                error_message=f"TeX file not found: {tex_path}"
            )
        
        latex_content = tex_path.read_text(encoding="utf-8")
        
        if output_path is None:
            output_path = tex_path.with_suffix(".pdf")
        
        # Use parent directory for images
        images_folder = tex_path.parent / "images"
        if not images_folder.exists():
            images_folder = None
        
        return self.compile(
            latex_content=latex_content,
            output_path=output_path,
            images_folder=images_folder,
            num_passes=num_passes
        )
    
    def _extract_error(self, log_file: Path) -> str:
        """Extract meaningful error message from LaTeX log."""
        if not log_file.exists():
            return ""
        
        try:
            log_content = log_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""
        
        error_lines = []
        capture = False
        
        for line in log_content.split("\n"):
            # Start capturing on error
            if line.startswith("!"):
                capture = True
            
            if capture:
                error_lines.append(line)
                
                # Stop after getting enough context
                if len(error_lines) >= 10:
                    break
                
                # Stop on certain patterns
                if "l." in line and line[0] != "!":
                    break
        
        if error_lines:
            return "\n".join(error_lines)
        
        # Look for other error patterns
        for line in log_content.split("\n"):
            if "Error:" in line or "error:" in line:
                return line
        
        return ""
    
    def verify_installation(self) -> Tuple[bool, str]:
        """
        Verify LaTeX installation.
        
        Returns:
            (is_valid, message)
        """
        if not self._check_engine_available():
            return False, f"{self.engine} not found. Install TeX Live or MacTeX."
        
        # Check for required packages
        required_packages = [
            "fontspec", "polyglossia", "geometry", "graphicx",
            "amsmath", "booktabs", "hyperref"
        ]
        
        # Try a minimal compilation
        test_doc = r"""
\documentclass{article}
\usepackage{fontspec}
\begin{document}
Test
\end{document}
"""
        
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            tex_file = temp_path / "test.tex"
            tex_file.write_text(test_doc, encoding="utf-8")
            
            try:
                result = subprocess.run(
                    [
                        self.engine,
                        "-interaction=nonstopmode",
                        str(tex_file)
                    ],
                    capture_output=True,
                    timeout=30,
                    cwd=temp_path
                )
                
                if result.returncode == 0:
                    return True, f"{self.engine} installation verified"
                else:
                    return False, "LaTeX compilation failed. Check package installation."
                    
            except subprocess.TimeoutExpired:
                return False, "LaTeX compilation timed out"
            except Exception as e:
                return False, f"Verification failed: {str(e)}"

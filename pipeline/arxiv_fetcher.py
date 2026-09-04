"""
ArXiv article fetcher module.

Handles downloading and extracting LaTeX sources from arXiv articles.
"""

import re
import tarfile
import shutil
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

import arxiv
import requests

from utils.helpers import extract_arxiv_id
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ArxivArticle:
    """
    Container for arXiv article data.
    """
    arxiv_id: str
    title: str
    authors: List[str]
    abstract: str
    published_date: datetime
    categories: List[str]
    main_tex_path: Path
    source_directory: Path
    original_pdf_path: Optional[Path] = None
    updated_date: Optional[datetime] = None
    version: str = ""
    primary_category: str = ""


class ArxivFetcher:
    """
    Fetches and extracts LaTeX sources from arXiv.
    
    Handles:
    - Metadata fetching via arXiv API
    - Source archive download and extraction
    - Main .tex file identification
    - Original PDF download (optional)
    """
    
    def __init__(self, temp_dir: Optional[Path] = None):
        """
        Initialize ArxivFetcher.
        
        Args:
            temp_dir: Directory for temporary files. If None, uses default from config.
        """
        if temp_dir is None:
            try:
                from config import Config
                temp_dir = Config.TEMP_DIR
            except ImportError:
                logger.warning("Config not available, using default temp directory")
                temp_dir = Path("temp")

        self.temp_dir = Path(temp_dir)
        if not self.temp_dir.is_absolute():
            repo_root = Path(__file__).resolve().parents[1]
            self.temp_dir = repo_root / self.temp_dir
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"ArxivFetcher initialized with temp_dir: {self.temp_dir}")
    
    def extract_metadata(self, arxiv_id: str) -> Dict[str, Any]:
        """
        Extract metadata from arXiv API.
        
        Args:
            arxiv_id: arXiv paper ID
            
        Returns:
            Dictionary containing paper metadata:
            - arxiv_id: Paper ID
            - title: Paper title
            - authors: List of author names
            - abstract: Paper abstract
            - published_date: Publication date (datetime)
            - categories: List of arXiv categories
            - pdf_url: URL to PDF
            
        Raises:
            ValueError: If paper not found or API error
        """
        try:
            logger.info(f"Fetching metadata for arXiv:{arxiv_id}")
            
            # Search for the paper using arxiv library
            search = arxiv.Search(id_list=[arxiv_id])
            results = list(search.results())
            
            if not results:
                raise ValueError(f"Paper {arxiv_id} not found on arXiv")
            
            paper = results[0]
            
            entry_id = getattr(paper, 'entry_id', '') or ''
            version = ''
            if entry_id:
                match = re.search(r'v\d+$', entry_id)
                if match:
                    version = match.group(0)
            primary_category = getattr(paper, 'primary_category', '') or ''
            
            metadata = {
                'arxiv_id': arxiv_id,
                'title': paper.title,
                'authors': [author.name for author in paper.authors],
                'abstract': paper.summary,
                'published_date': paper.published,
                'categories': paper.categories,
                'pdf_url': paper.pdf_url,
                'updated_date': getattr(paper, 'updated', None),
                'version': version,
                'primary_category': primary_category,
            }
            
            logger.info(f"Metadata fetched: {metadata['title']}")
            logger.debug(f"Authors: {', '.join(metadata['authors'][:3])}...")
            logger.debug(f"Published: {metadata['published_date']}")
            logger.debug(f"Categories: {', '.join(metadata['categories'])}")
            
            return metadata
            
        except arxiv.UnexpectedEmptyPageError:
            logger.error(f"Unexpected empty page for {arxiv_id}")
            raise ValueError(f"Paper {arxiv_id} not found on arXiv")
        except Exception as e:
            logger.error(f"Failed to fetch metadata: {e}")
            raise ValueError(f"Error fetching metadata for {arxiv_id}: {e}")
    
    def download_sources(self, arxiv_id: str) -> Path:
        """
        Download and extract LaTeX source files.
        
        Args:
            arxiv_id: arXiv paper ID
            
        Returns:
            Path to extracted source directory
            
        Raises:
            ValueError: If download or extraction fails or sources not available
        """
        try:
            logger.info(f"Downloading source for arXiv:{arxiv_id}")
            
            # Create directory for this paper
            paper_dir = self.temp_dir / arxiv_id
            paper_dir.mkdir(parents=True, exist_ok=True)

            extract_dir = paper_dir / "source"
            checkpoint_path = extract_dir / "translation_checkpoint.json"
            assemble_only = str(os.environ.get("ROSETTA_ASSEMBLE_FROM_CHECKPOINT", "0") or "0").strip().lower() in (
                "1",
                "true",
                "yes",
            )
            if assemble_only and checkpoint_path.exists() and extract_dir.exists():
                logger.info(
                    f"ROSETTA_ASSEMBLE_FROM_CHECKPOINT is enabled; reusing existing sources at {extract_dir}"
                )
                return extract_dir

            checkpoint_backup = paper_dir / "translation_checkpoint.json.bak"
            if checkpoint_path.exists():
                try:
                    shutil.copy2(checkpoint_path, checkpoint_backup)
                except Exception:
                    pass
            
            source_urls = [
                f"https://arxiv.org/e-print/{arxiv_id}",
                f"https://arxiv.org/src/{arxiv_id}",
            ]

            source_file = paper_dir / f"{arxiv_id}.tar.gz"
            pdf_payload_path = paper_dir / f"{arxiv_id}.pdf"
            last_status: Optional[int] = None
            last_ct = ""
            last_head = b""

            for source_url in source_urls:
                logger.info(f"Downloading from {source_url}")

                # Download with timeout
                response = requests.get(source_url, timeout=120, stream=True)
                last_status = response.status_code
                last_ct = (response.headers.get("Content-Type") or "").strip()

                if response.status_code == 404:
                    continue

                if response.status_code != 200:
                    continue

                # Save the file
                source_file.parent.mkdir(parents=True, exist_ok=True)
                last_head = b""
                head_remaining = 256
                with open(source_file, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if not chunk:
                            continue
                        if head_remaining > 0:
                            take = chunk[:head_remaining]
                            last_head += take
                            head_remaining -= len(take)
                        f.write(chunk)

                file_size = source_file.stat().st_size
                logger.info(f"Source downloaded to {source_file} ({file_size:,} bytes)")

                if tarfile.is_tarfile(source_file):
                    break

                is_pdf = ("application/pdf" in last_ct.lower()) or last_head.startswith(b"%PDF")
                if is_pdf:
                    logger.warning(
                        f"Downloaded payload is a PDF, not a tar archive (Content-Type={last_ct!r}). "
                        f"Will try another source URL."
                    )
                    try:
                        if pdf_payload_path.exists():
                            pdf_payload_path.unlink()
                    except Exception:
                        pass
                    try:
                        source_file.replace(pdf_payload_path)
                    except Exception:
                        try:
                            shutil.copy2(source_file, pdf_payload_path)
                        except Exception:
                            pass
                        try:
                            source_file.unlink()
                        except Exception:
                            pass
                else:
                    logger.warning(
                        f"Downloaded payload is not a tar archive (Content-Type={last_ct!r}, head={last_head[:16]!r}). "
                        f"Will try another source URL."
                    )

                    try:
                        source_file.unlink()
                    except Exception:
                        pass

            is_pdf = ("application/pdf" in (last_ct or "").lower()) or (last_head or b"").startswith(b"%PDF")
            tar_ok = False
            if source_file.exists():
                try:
                    tar_ok = tarfile.is_tarfile(source_file)
                except Exception:
                    tar_ok = False

            if (not tar_ok) and is_pdf and pdf_payload_path.exists():
                if extract_dir.exists():
                    try:
                        shutil.rmtree(extract_dir)
                    except Exception as e:
                        logger.warning(f"Failed to remove existing source dir {extract_dir}: {e}")
                extract_dir.mkdir(parents=True, exist_ok=True)

                pdf_name = "paper.pdf"
                pdf_path = extract_dir / pdf_name
                try:
                    pdf_payload_path.replace(pdf_path)
                except Exception:
                    shutil.copy2(pdf_payload_path, pdf_path)
                    try:
                        pdf_payload_path.unlink()
                    except Exception:
                        pass

                tex_path = extract_dir / "main.tex"
                tex_path.write_text(
                    "\\documentclass{article}\n"
                    "\\usepackage{pdfpages}\n"
                    "\\begin{document}\n"
                    f"\\includepdf[pages=-]{{{pdf_name}}}\n"
                    "\\end{document}\n",
                    encoding="utf-8",
                )

                logger.warning(
                    f"No tar sources available for {arxiv_id}; using PDF wrapper sources instead (Content-Type={last_ct!r})."
                )
                if checkpoint_backup.exists() and not checkpoint_path.exists():
                    try:
                        shutil.copy2(checkpoint_backup, checkpoint_path)
                    except Exception:
                        pass
                return extract_dir

            if not source_file.exists():
                if last_status == 404:
                    raise ValueError(
                        f"LaTeX sources not available for paper {arxiv_id}. "
                        f"Some papers may not have source files available."
                    )
                raise ValueError(
                    f"Failed to download source for {arxiv_id} from arXiv (last_status={last_status})."
                )
            
            # Extract archive
            if source_file.exists():
                if extract_dir.exists():
                    try:
                        shutil.rmtree(extract_dir)
                    except Exception as e:
                        logger.warning(f"Failed to remove existing source dir {extract_dir}: {e}")
                extract_dir.mkdir(parents=True, exist_ok=True)
                
                try:
                    logger.info(f"Extracting archive to {extract_dir}")

                    if not tarfile.is_tarfile(source_file):
                        head = b""
                        try:
                            with open(source_file, "rb") as f:
                                head = f.read(256)
                        except Exception:
                            head = b""
                        raise ValueError(
                            f"Invalid or corrupted source archive: not a tar file (Content-Type={last_ct!r}, head={head!r})"
                        )

                    with tarfile.open(source_file, 'r:*') as tar:
                        tar.extractall(path=extract_dir)
                    
                    # Count extracted files
                    extracted_files = list(extract_dir.rglob('*'))
                    file_count = len([f for f in extracted_files if f.is_file()])
                    logger.info(f"Source extracted: {file_count} files in {extract_dir}")
                    
                    # Clean up tar file
                    source_file.unlink()
                    logger.debug(f"Cleaned up archive file: {source_file}")
                    if checkpoint_backup.exists() and not checkpoint_path.exists():
                        try:
                            shutil.copy2(checkpoint_backup, checkpoint_path)
                        except Exception:
                            pass
                    return extract_dir
                    
                except tarfile.TarError as e:
                    logger.error(f"Failed to extract tar archive: {e}")
                    raise ValueError(f"Invalid or corrupted source archive: {e}")
                except Exception as e:
                    logger.error(f"Error during extraction: {e}")
                    raise ValueError(f"Error extracting source archive: {e}")
            else:
                raise ValueError("Source file not found after download")
                
        except requests.exceptions.Timeout:
            logger.error(f"Timeout while downloading source for {arxiv_id}")
            raise ValueError(f"Timeout while downloading source for {arxiv_id}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Network error while downloading source: {e}")
            raise ValueError(f"Network error while downloading source for {arxiv_id}: {e}")
        except Exception as e:
            logger.error(f"Failed to download source: {e}")
            raise ValueError(f"Error downloading source for {arxiv_id}: {e}")
    
    def find_main_tex(self, directory: Path) -> Path:
        """
        Find the main .tex file in source directory.
        
        Strategy:
        1. Check common names (main.tex, paper.tex, etc.)
        2. Look for files with \\documentclass
        3. If only one .tex file, use it
        4. Otherwise, raise error
        
        Args:
            directory: Directory containing LaTeX sources
            
        Returns:
            Path to main .tex file
            
        Raises:
            ValueError: If main .tex file cannot be identified
        """
        logger.info(f"Searching for main .tex file in {directory}")
        
        # Common names for main file
        common_names = ['main.tex', 'paper.tex', 'manuscript.tex', 'article.tex', 'document.tex']
        
        # Check common names first
        for name in common_names:
            candidate = directory / name
            if candidate.exists():
                logger.info(f"Found main file (common name): {candidate}")
                return candidate
        
        # Look for any .tex file with \documentclass
        # Ignore generated translation artifacts (they may include \documentclass and confuse selection).
        tex_files = [p for p in directory.rglob('*.tex')]
        filtered_tex_files = []
        for p in tex_files:
            name = p.name.lower()
            if name.startswith('translated'):
                continue
            if '_fixed_' in name:
                continue
            filtered_tex_files.append(p)
        tex_files = filtered_tex_files
        
        if not tex_files:
            raise ValueError(f"No .tex files found in {directory}")
        
        logger.debug(f"Found {len(tex_files)} .tex files, checking for \\documentclass")
        
        candidates_with_documentclass = []
        for tex_file in tex_files:
            try:
                content = tex_file.read_text(encoding='utf-8', errors='ignore')
                if r'\documentclass' in content:
                    candidates_with_documentclass.append(tex_file)
                    logger.debug(f"Found file with \\documentclass: {tex_file}")
            except Exception as e:
                logger.warning(f"Error reading {tex_file}: {e}")
                continue
        
        if len(candidates_with_documentclass) == 1:
            logger.info(f"Found main file (with \\documentclass): {candidates_with_documentclass[0]}")
            return candidates_with_documentclass[0]
        elif len(candidates_with_documentclass) > 1:
            # Prefer files in root directory
            root_candidates = [f for f in candidates_with_documentclass if f.parent == directory]
            if root_candidates:
                logger.info(f"Found main file (root with \\documentclass): {root_candidates[0]}")
                return root_candidates[0]
            else:
                # Use the first one found
                logger.warning(f"Multiple files with \\documentclass found, using: {candidates_with_documentclass[0]}")
                return candidates_with_documentclass[0]
        
        # If only one .tex file, assume it's the main one
        if len(tex_files) == 1:
            logger.info(f"Single .tex file found: {tex_files[0]}")
            return tex_files[0]
        
        # Multiple .tex files but none with \documentclass - prefer root level
        root_tex_files = [f for f in tex_files if f.parent == directory]
        if root_tex_files:
            logger.warning(f"Multiple .tex files found, using root file: {root_tex_files[0]}")
            return root_tex_files[0]
        
        # Last resort: use first file
        logger.warning(f"Could not definitively identify main .tex file, using: {tex_files[0]}")
        return tex_files[0]
    
    def download_pdf(self, arxiv_id: str) -> Optional[Path]:
        """
        Download original PDF of the paper (optional).
        
        Args:
            arxiv_id: arXiv paper ID
            
        Returns:
            Path to downloaded PDF file, or None if download fails
            
        Note:
            This method does not raise exceptions - PDF download is optional.
            Failures are logged but don't stop the process.
        """
        try:
            logger.info(f"Downloading PDF for arXiv:{arxiv_id}")
            
            # Create directory for this paper
            paper_dir = self.temp_dir / arxiv_id
            paper_dir.mkdir(parents=True, exist_ok=True)
            
            # PDF URL
            pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
            pdf_file = paper_dir / f"{arxiv_id}_original.pdf"
            
            # Skip if already exists
            if pdf_file.exists():
                logger.info(f"PDF already exists: {pdf_file}")
                return pdf_file
            
            logger.info(f"Downloading from {pdf_url}")
            
            response = requests.get(pdf_url, timeout=120, stream=True)
            
            if response.status_code == 200:
                # Save the PDF
                with open(pdf_file, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                
                file_size = pdf_file.stat().st_size
                logger.info(f"PDF downloaded to {pdf_file} ({file_size:,} bytes)")
                return pdf_file
            else:
                logger.warning(
                    f"Failed to download PDF (HTTP {response.status_code}). "
                    f"PDF may not be available for paper {arxiv_id}."
                )
                return None
                
        except Exception as e:
            logger.warning(f"Failed to download PDF (non-critical): {e}")
            return None
    
    def fetch_article(self, url_or_id: str) -> ArxivArticle:
        """
        Fetch article from arXiv (metadata + sources).
        
        Main entry point for fetching articles. Handles:
        1. Extracting arXiv ID from URL or string
        2. Fetching metadata
        3. Downloading and extracting sources
        4. Finding main .tex file
        5. Downloading original PDF (optional)
        
        Args:
            url_or_id: arXiv URL or ID
            
        Returns:
            ArxivArticle dataclass with all article data
            
        Raises:
            ValueError: If fetch fails at any stage
        """
        # Extract ID using helper function
        arxiv_id = extract_arxiv_id(url_or_id)
        if not arxiv_id:
            raise ValueError(f"Invalid arXiv URL or ID: {url_or_id}")
        
        logger.info(f"Fetching paper {arxiv_id}")
        
        # Fetch metadata
        metadata = self.extract_metadata(arxiv_id)
        
        # Download and extract source
        source_dir = self.download_sources(arxiv_id)
        
        # Find main .tex file
        try:
            main_tex = self.find_main_tex(source_dir)
        except ValueError as e:
            logger.error(f"Could not find main .tex file: {e}")
            raise
        
        # Download original PDF (optional, don't fail if it doesn't work)
        pdf_path = self.download_pdf(arxiv_id)
        
        # Create article object
        article = ArxivArticle(
            arxiv_id=metadata['arxiv_id'],
            title=metadata['title'],
            authors=metadata['authors'],
            abstract=metadata['abstract'],
            published_date=metadata['published_date'],
            categories=metadata['categories'],
            main_tex_path=main_tex,
            source_directory=source_dir,
            original_pdf_path=pdf_path,
            updated_date=metadata.get('updated_date'),
            version=metadata.get('version', ''),
            primary_category=metadata.get('primary_category', '')
        )
        
        logger.info(f"Successfully fetched paper: {article.title}")
        return article
    
    def cleanup(self, arxiv_id: str) -> None:
        """
        Clean up temporary files for a paper.
        
        Args:
            arxiv_id: arXiv paper ID
        """
        paper_dir = self.temp_dir / arxiv_id
        if paper_dir.exists():
            try:
                shutil.rmtree(paper_dir)
                logger.info(f"Cleaned up temporary files for {arxiv_id}")
            except Exception as e:
                logger.warning(f"Error cleaning up {paper_dir}: {e}")


# Convenience function
def fetch_article(url_or_id: str) -> ArxivArticle:
    """
    Convenience function to fetch an article from arXiv.
    
    Args:
        url_or_id: arXiv URL or ID
        
    Returns:
        ArxivArticle dataclass with metadata and source paths
    """
    fetcher = ArxivFetcher()
    return fetcher.fetch_article(url_or_id)


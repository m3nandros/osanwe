"""
Helper utility functions for Rosetta v2.

Provides common utility functions:
- arXiv ID extraction and validation
- Path manipulation
- Text formatting
- Temporary file handling
"""

import re
from pathlib import Path
from typing import Optional


def extract_arxiv_id(url_or_id: str) -> Optional[str]:
    """
    Extract arXiv ID from URL or validate ID format.
    
    Supports various formats:
    - https://arxiv.org/abs/1706.03762
    - https://arxiv.org/pdf/1706.03762.pdf
    - 1706.03762
    - 1706.03762v1
    - math.CO/1234567 (new format)
    
    Args:
        url_or_id: arXiv URL or ID string
        
    Returns:
        Extracted arXiv ID or None if invalid
        
    Examples:
        >>> extract_arxiv_id("https://arxiv.org/abs/1706.03762")
        '1706.03762'
        >>> extract_arxiv_id("1706.03762")
        '1706.03762'
        >>> extract_arxiv_id("https://arxiv.org/pdf/1706.03762.pdf")
        '1706.03762'
    """
    if not url_or_id:
        return None
    
    # Pattern for arXiv ID (supports both old and new formats)
    # Old format: YYMM.NNNNN or YYMM.NNNNNvN
    # New format: category/YYMMNNN or category/YYMMNNNvN
    arxiv_id_pattern = r'(\d{4}\.\d{4,5}(v\d+)?|\w+-\w+/\d{7}(v\d+)?)'
    
    # Try to extract from URL
    url_match = re.search(r'arxiv\.org/(?:abs|pdf)/(' + arxiv_id_pattern + ')', url_or_id)
    if url_match:
        return url_match.group(1)
    
    # Check if it's already an ID
    id_match = re.fullmatch(arxiv_id_pattern, url_or_id.strip())
    if id_match:
        return url_or_id.strip()
    
    return None


def validate_arxiv_id(arxiv_id: str) -> bool:
    """
    Validate arXiv ID format.
    
    Args:
        arxiv_id: arXiv ID to validate
        
    Returns:
        True if valid, False otherwise
    """
    return extract_arxiv_id(arxiv_id) is not None


def ensure_directory(path: Path) -> Path:
    """
    Ensure directory exists, create if it doesn't.
    
    Args:
        path: Path to directory
        
    Returns:
        Path object (same as input)
    """
    path.mkdir(parents=True, exist_ok=True)
    return path


def clean_filename(filename: str) -> str:
    """
    Clean filename by removing invalid characters.
    
    Args:
        filename: Original filename
        
    Returns:
        Cleaned filename safe for filesystem
    """
    # Remove invalid characters for filenames
    invalid_chars = '<>:"/\\|?*'
    cleaned = filename
    for char in invalid_chars:
        cleaned = cleaned.replace(char, '_')
    
    # Remove leading/trailing spaces and dots
    cleaned = cleaned.strip(' .')
    
    return cleaned


def format_file_size(size_bytes: int) -> str:
    """
    Format file size in human-readable format.
    
    Args:
        size_bytes: Size in bytes
        
    Returns:
        Formatted string (e.g., "1.5 MB")
    """
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"


def get_temp_file_path(base_dir: Path, prefix: str, suffix: str = "") -> Path:
    """
    Get a temporary file path in the specified directory.
    
    Args:
        base_dir: Base directory for temp files
        prefix: Filename prefix
        suffix: Filename suffix (e.g., extension)
        
    Returns:
        Path to temporary file
    """
    ensure_directory(base_dir)
    return base_dir / f"{prefix}{suffix}"


def remove_empty_lines(text: str) -> str:
    """
    Remove empty lines from text while preserving structure.
    
    Args:
        text: Input text
        
    Returns:
        Text with empty lines removed
    """
    lines = text.split('\n')
    # Remove completely empty lines, but preserve lines with only whitespace
    # (as they might be intentional in LaTeX)
    return '\n'.join(line for line in lines if line.strip() or line == '')


def truncate_text(text: str, max_length: int = 100, suffix: str = "...") -> str:
    """
    Truncate text to maximum length.
    
    Args:
        text: Text to truncate
        max_length: Maximum length
        suffix: Suffix to add if truncated
        
    Returns:
        Truncated text
    """
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix


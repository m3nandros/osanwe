"""
Glossary management for Rosetta v2.

Handles loading, filtering, and formatting of terminology dictionary:
- Load glossary from JSON
- Find relevant terms for a document
- Format glossary compactly for prompts
- Cache formatted glossaries
- Estimate token usage
"""

import os
import json
import re
from pathlib import Path
from typing import Dict, Optional, Tuple, Any
from functools import lru_cache

from utils.logger import get_logger

logger = get_logger(__name__)


def _normalize_lang_code(lang: str) -> str:
    t = (lang or "").strip().lower()
    mapping = {
        "ch": "zh",
        "cn": "zh",
        "zh-cn": "zh",
        "zh-hans": "zh",
        "zh-hant": "zh",
        "jp": "ja",
        "jpn": "ja",
        "ja-jp": "ja",
    }
    return mapping.get(t, t)


def select_glossary_for_language(glossary: Dict[str, Any], target_lang: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    tl = _normalize_lang_code(target_lang)
    allow_string_all = (str(os.getenv("ROSETTA_GLOSSARY_STRING_ALL_LANGS", "0") or "0").strip().lower() in ("1", "true", "yes"))
    for term, value in (glossary or {}).items():
        if not isinstance(term, str):
            continue
        if isinstance(value, str):
            if tl == "ru" or allow_string_all:
                out[term] = value
            continue
        if isinstance(value, dict):
            vmap = {str(k).strip().lower(): v for k, v in value.items() if isinstance(k, str) and isinstance(v, str)}
            chosen = vmap.get(tl)
            if chosen is None and "-" in tl:
                chosen = vmap.get(tl.split("-", 1)[0])
            if chosen is None and "_" in tl:
                chosen = vmap.get(tl.split("_", 1)[0])
            if chosen is not None:
                out[term] = chosen
    return out


def load_glossary(path: str) -> Dict[str, Any]:
    """
    Load glossary from JSON file.
    
    Args:
        path: Path to glossary.json file
        
    Returns:
        Dictionary of terms and translations
        
    Raises:
        FileNotFoundError: If glossary file doesn't exist
        json.JSONDecodeError: If JSON is invalid
    """
    glossary_path = Path(path)
    
    if not glossary_path.exists():
        logger.warning(f"Glossary file not found at {glossary_path}. Using empty glossary.")
        return {}
    
    try:
        with open(glossary_path, 'r', encoding='utf-8') as f:
            glossary = json.load(f)
        
        # Validate structure
        if not isinstance(glossary, dict):
            logger.error(f"Glossary must be a dictionary, got {type(glossary)}")
            return {}
        
        # Validate entries
        valid_glossary: Dict[str, Any] = {}
        for key, value in glossary.items():
            if not isinstance(key, str):
                logger.warning(f"Skipping invalid entry: {key} -> {value}")
                continue
            k = key.lower()
            if isinstance(value, str):
                valid_glossary[k] = value
                continue
            if isinstance(value, dict):
                vv: Dict[str, str] = {}
                for lang, tr in value.items():
                    if isinstance(lang, str) and isinstance(tr, str):
                        vv[lang.strip().lower()] = tr
                if vv:
                    valid_glossary[k] = vv
                else:
                    logger.warning(f"Skipping invalid entry: {key} -> {value}")
                continue
            logger.warning(f"Skipping invalid entry: {key} -> {value}")
        
        logger.info(f"Loaded {len(valid_glossary)} terms from glossary")
        return valid_glossary
        
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse glossary JSON: {e}")
        return {}
    except Exception as e:
        logger.error(f"Error loading glossary: {e}")
        return {}


def find_relevant_terms(glossary: Dict[str, Any], document: str) -> Dict[str, Any]:
    """
    Find relevant terms from glossary that appear in the document.
    
    Analyzes the document for presence of glossary terms and returns only
    those that are actually used, saving tokens in the prompt.
    
    Args:
        glossary: Full glossary dictionary
        document: Document content to analyze
        
    Returns:
        Dictionary of relevant terms (subset of glossary)
        
    Examples:
        >>> glossary = {"machine learning": "машинное обучение", "deep learning": "глубокое обучение"}
        >>> doc = "This paper uses machine learning techniques."
        >>> find_relevant_terms(glossary, doc)
        {'machine learning': 'машинное обучение'}
    """
    if not glossary or not document:
        return {}
    
    document_lower = document.lower()
    relevant_terms: Dict[str, Any] = {}
    
    # Check each term in glossary
    for term, translation in glossary.items():
        # Use word boundaries to avoid partial matches
        # But also allow matches in LaTeX commands and formulas
        pattern = r'\b' + re.escape(term) + r'\b'
        
        if re.search(pattern, document_lower, re.IGNORECASE):
            relevant_terms[term] = translation
    
    logger.info(
        f"Found {len(relevant_terms)} relevant terms out of {len(glossary)} total "
        f"({len(relevant_terms)/len(glossary)*100:.1f}% usage)"
    )
    
    return relevant_terms


def format_glossary_compact(glossary: Dict[str, str]) -> str:
    """
    Format glossary in compact JSON format for GPT prompt.
    
    Uses compact JSON format instead of verbose text to minimize tokens.
    
    Args:
        glossary: Glossary dictionary to format
        
    Returns:
        Compact JSON string representation
        
    Examples:
        >>> format_glossary_compact({"ml": "машинное обучение"})
        '{"ml": "машинное обучение"}'
    """
    if not glossary:
        return "{}"
    
    # Use compact JSON (no spaces, single line)
    return json.dumps(glossary, ensure_ascii=False, separators=(',', ':'))


def format_glossary_verbose(glossary: Dict[str, str]) -> str:
    """
    Format glossary in verbose format (for debugging or non-optimized prompts).
    
    Args:
        glossary: Glossary dictionary to format
        
    Returns:
        Verbose formatted string
    """
    if not glossary:
        return "Глоссарий пуст."
    
    lines = []
    for eng, rus in sorted(glossary.items()):
        lines.append(f"  • {eng} → {rus}")
    
    return "\n".join(lines)


@lru_cache(maxsize=128)
def _cached_format_glossary(glossary_json: str, compact: bool = True) -> str:
    """
    Cached version of glossary formatting.
    
    Internal function for caching formatted glossaries.
    Uses JSON string as cache key.
    """
    glossary = json.loads(glossary_json)
    if compact:
        return format_glossary_compact(glossary)
    else:
        return format_glossary_verbose(glossary)


def format_glossary_cached(glossary: Dict[str, str], compact: bool = True) -> str:
    """
    Format glossary with caching for performance.
    
    Args:
        glossary: Glossary dictionary to format
        compact: Whether to use compact format (default: True)
        
    Returns:
        Formatted glossary string
    """
    # Create cache key from sorted glossary
    glossary_json = json.dumps(dict(sorted(glossary.items())), ensure_ascii=False)
    return _cached_format_glossary(glossary_json, compact)


def estimate_glossary_tokens(glossary: Dict[str, str], format_type: str = "compact") -> int:
    """
    Estimate token count for formatted glossary.
    
    Rough estimation: ~4 characters per token for English/Russian text.
    This is a heuristic and may not be exact.
    
    Args:
        glossary: Glossary dictionary
        format_type: Format type ("compact" or "verbose")
        
    Returns:
        Estimated token count
    """
    if not glossary:
        return 0
    
    if format_type == "compact":
        formatted = format_glossary_compact(glossary)
    else:
        formatted = format_glossary_verbose(glossary)
    
    # Rough estimation: ~4 characters per token
    # This is a heuristic, actual tokenization may vary
    estimated_tokens = len(formatted) // 4
    
    return max(estimated_tokens, 1)  # At least 1 token


def get_glossary_metrics(
    full_glossary: Dict[str, Any],
    relevant_glossary: Dict[str, Any],
    target_lang: str = "ru",
) -> Dict[str, any]:
    """
    Get metrics about glossary usage and optimization.
    
    Args:
        full_glossary: Full glossary dictionary
        relevant_glossary: Filtered relevant glossary dictionary
        
    Returns:
        Dictionary with metrics:
        - total_terms: Total number of terms in glossary
        - relevant_terms: Number of relevant terms found
        - usage_percentage: Percentage of glossary used
        - tokens_saved: Estimated tokens saved through filtering
        - full_tokens: Estimated tokens for full glossary
        - relevant_tokens: Estimated tokens for relevant glossary
    """
    total_terms = len(full_glossary)
    relevant_terms = len(relevant_glossary)
    
    full_tokens = estimate_glossary_tokens(select_glossary_for_language(full_glossary, target_lang), "compact")
    relevant_tokens = estimate_glossary_tokens(select_glossary_for_language(relevant_glossary, target_lang), "compact")
    tokens_saved = full_tokens - relevant_tokens
    
    usage_percentage = (relevant_terms / total_terms * 100) if total_terms > 0 else 0
    
    return {
        "total_terms": total_terms,
        "relevant_terms": relevant_terms,
        "usage_percentage": round(usage_percentage, 1),
        "tokens_saved": tokens_saved,
        "full_tokens": full_tokens,
        "relevant_tokens": relevant_tokens,
    }


class GlossaryManager:
    """
    Manager class for glossary operations.
    
    Provides convenient interface for loading, filtering, and formatting glossaries.
    """
    
    def __init__(self, glossary_path: Optional[Path] = None):
        """
        Initialize glossary manager.
        
        Args:
            glossary_path: Path to glossary JSON file. If None, uses default from config.
        """
        if glossary_path is None:
            try:
                from config import Config
                glossary_path = Config.GLOSSARY_PATH
            except ImportError:
                logger.warning("Config not available, using default glossary path")
                glossary_path = Path("glossary.json")
        
        self.glossary_path = Path(glossary_path)
        self.full_glossary: Dict[str, Any] = {}
        self._load_glossary()
    
    def _load_glossary(self) -> None:
        """Load glossary from file."""
        self.full_glossary = load_glossary(str(self.glossary_path))
    
    def get_relevant_terms(self, document: str) -> Dict[str, Any]:
        """
        Get relevant terms for a document.
        
        Args:
            document: Document content to analyze
            
        Returns:
            Dictionary of relevant terms
        """
        return find_relevant_terms(self.full_glossary, document)
    
    def format_for_prompt(
        self,
        document: Optional[str] = None,
        use_filtering: bool = True,
        compact: bool = True,
        target_lang: str = "ru",
    ) -> Tuple[str, Dict[str, any]]:
        """
        Format glossary for GPT prompt with optional filtering.
        
        Args:
            document: Document content for filtering (if use_filtering=True)
            use_filtering: Whether to filter relevant terms (default: True)
            compact: Whether to use compact format (default: True)
            
        Returns:
            Tuple of (formatted_glossary_string, metrics_dict)
        """
        if use_filtering and document:
            relevant_glossary = self.get_relevant_terms(document)
            metrics = get_glossary_metrics(self.full_glossary, relevant_glossary, target_lang=target_lang)
            formatted = format_glossary_cached(select_glossary_for_language(relevant_glossary, target_lang), compact=compact)
        else:
            metrics = get_glossary_metrics(self.full_glossary, self.full_glossary, target_lang=target_lang)
            formatted = format_glossary_cached(select_glossary_for_language(self.full_glossary, target_lang), compact=compact)
        
        return formatted, metrics
    
    def reload(self) -> None:
        """Reload glossary from file."""
        logger.info("Reloading glossary...")
        self._load_glossary()
        # Clear cache
        _cached_format_glossary.cache_clear()

"""
LaTeX Restorer & Assembler module for Rosetta v3.

Responsible for:
1. Assembling translated chunks in the correct order.
2. Unmasking the content (replacing tokens with original code).
3. Validating that all tokens are correctly restored.
"""

from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass

from pipeline.splitter import Chunk
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class RestorationResult:
    """Result of the restoration process."""
    full_text: str
    success: bool
    missing_tokens: List[str]
    hallucinated_tokens: List[str]


class LaTeXRestorer:
    """
    Restores original LaTeX content from translated chunks and token map.
    """
    
    def __init__(self):
        """Initialize the restorer."""
        self.logger = get_logger(__name__)
        
    def assemble_and_restore(self, chunks: List[Chunk], token_map: Dict[str, str]) -> RestorationResult:
        """
        Assemble chunks and restore tokens.
        
        Args:
            chunks: List of translated chunks
            token_map: Dictionary mapping tokens to original content
            
        Returns:
            RestorationResult object
        """
        self.logger.info("Starting document assembly and restoration...")
        
        # 1. Sort chunks by order (handle both Chunk objects and dicts)
        sorted_chunks = sorted(chunks, key=lambda c: c.order if hasattr(c, 'order') else c['order'])
        
        # 2. Concatenate text (handle both Chunk objects and dicts)
        full_text = "\n\n".join([
            chunk.text if hasattr(chunk, 'text') else chunk['text'] 
            for chunk in sorted_chunks
        ])
        
        # 3. Restore tokens
        restored_text, missing, hallucinated = self._restore_tokens(full_text, token_map)
        
        success = len(missing) == 0
        
        if not success:
            self.logger.error(f"Restoration failed. Missing tokens: {len(missing)}")
            # TODO: Implement fallback strategy (e.g., try to find context match)
        else:
            self.logger.info("Restoration successful: All tokens restored.")
            
        if hallucinated:
            self.logger.warning(f"Found {len(hallucinated)} hallucinated tokens (ignored).")
            
        return RestorationResult(
            full_text=restored_text,
            success=success,
            missing_tokens=missing,
            hallucinated_tokens=hallucinated
        )
        
    def _restore_tokens(self, text: str, token_map: Dict[str, str]) -> Tuple[str, List[str], List[str]]:
        """
        Replace tokens with original content and track issues.
        Now with recursive restoration for nested tokens.
        """
        import re
        
        restored_text = text
        missing_tokens = []
        hallucinated_tokens = []
        
        # Recursive restoration: keep replacing until no more tokens found
        max_iterations = 10  # Prevent infinite loops
        iteration = 0
        
        while iteration < max_iterations:
            iteration += 1
            tokens_replaced = False
            
            # Replace all tokens in current text
            for token, original in token_map.items():
                if token in restored_text:
                    restored_text = restored_text.replace(token, original)
                    tokens_replaced = True
            
            # If no tokens were replaced, we're done
            if not tokens_replaced:
                break
        
        # Tokens that still remain after restoration
        token_pattern = re.compile(r'<<[A-Z]+_\d+>>')
        remaining_tokens = token_pattern.findall(restored_text)

        # Missing tokens: placeholders that remain but SHOULD have been restored (exist in token_map)
        for token in remaining_tokens:
            if token in token_map and token not in missing_tokens:
                missing_tokens.append(token)

        # Hallucinated tokens: placeholders that remain but are NOT in token_map
        for token in remaining_tokens:
            if token not in token_map and token not in hallucinated_tokens:
                hallucinated_tokens.append(token)
            
        return restored_text, missing_tokens, hallucinated_tokens

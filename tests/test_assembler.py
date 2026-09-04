import pytest
from pipeline.assembler import LaTeXRestorer
from pipeline.splitter import Chunk

class TestLaTeXRestorer:
    
    def test_assemble_and_restore(self):
        restorer = LaTeXRestorer()
        
        chunks = [
            Chunk(id="1", text="Hello <<MATH_0>>", type="text", order=1, token_count=10),
            Chunk(id="0", text="Start", type="text", order=0, token_count=5)
        ]
        
        token_map = {
            "<<MATH_0>>": "$x$"
        }
        
        result = restorer.assemble_and_restore(chunks, token_map)
        
        assert result.success
        assert result.full_text == "Start\n\nHello $x$"
        assert len(result.missing_tokens) == 0
        
    def test_missing_token(self):
        restorer = LaTeXRestorer()
        
        chunks = [
            Chunk(id="1", text="Hello <<MATH_0>>", type="text", order=0, token_count=10)
        ]
        
        token_map = {} # Empty map
        
        result = restorer.assemble_and_restore(chunks, token_map)
        
        assert result.success # It counts as success if no tokens from map are missing?
        # Wait, logic was: iterate map, check if in text.
        # If map is empty, missing_tokens is empty.
        # But we check for hallucinated tokens (tokens in text but not in map)
        
        assert len(result.hallucinated_tokens) == 1
        assert "<<MATH_0>>" in result.hallucinated_tokens

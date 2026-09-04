#!/usr/bin/env python3
"""
Debug script to inspect chunks before translation.
"""

from pathlib import Path
from pipeline.arxiv_fetcher import ArxivFetcher
from pipeline.masker import ContentMasker
from pipeline.splitter import ContentSplitter

# Fetch article
fetcher = ArxivFetcher()
result = fetcher.fetch_article("1706.03762")

work_dir = result.source_directory
main_tex = result.main_tex_path

print(f"Working directory: {work_dir}")
print(f"Main TeX file: {main_tex}")

# Read and flatten
with open(main_tex, 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

# Flatten (simplified version without recursion for now)
import re
def flatten_latex(base_dir, content, depth=0):
    if depth > 10:
        return content
    pattern = re.compile(r'\\(?:input|include)\{([^}]+)\}')
    def replace_input(match):
        filename = match.group(1)
        if not filename.endswith('.tex'):
            filename += '.tex'
        file_path = base_dir / filename
        if not file_path.exists():
            return match.group(0)
        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                sub_content = f.read()
            return flatten_latex(base_dir, sub_content, depth + 1)
        except:
            return match.group(0)
    return pattern.sub(replace_input, content)

flattened = flatten_latex(work_dir, content)
print(f"\nFlattened content length: {len(flattened)} chars")

# Mask
masker = ContentMasker()
masked = masker.mask_content(flattened)
print(f"Masked content length: {len(masked.text)} chars")
print(f"Masking stats: {masked.stats}")

# Split
splitter = ContentSplitter(max_chunk_tokens=1500)
chunks = splitter.split_content(masked.text)

print(f"\nTotal chunks: {len(chunks)}")
print("\nChunk summary:")
for i, chunk in enumerate(chunks):
    print(f"  Chunk {i}: {chunk.type}, {chunk.token_count} tokens, order={chunk.order}")
    if chunk.context_summary:
        print(f"           Context: {chunk.context_summary}")

# Show chunk 11 details
if len(chunks) > 11:
    chunk11 = chunks[11]
    print(f"\n{'='*80}")
    print(f"CHUNK 11 DETAILS:")
    print(f"{'='*80}")
    print(f"Type: {chunk11.type}")
    print(f"Order: {chunk11.order}")
    print(f"Token count: {chunk11.token_count}")
    print(f"Context: {chunk11.context_summary}")
    print(f"Text length: {len(chunk11.text)} chars")
    print(f"\nFirst 500 chars:")
    print(chunk11.text[:500])
    print(f"\nLast 500 chars:")
    print(chunk11.text[-500:])
    
    # Save to file for inspection
    debug_file = Path("temp/chunk_11_debug.txt")
    debug_file.parent.mkdir(exist_ok=True)
    with open(debug_file, 'w', encoding='utf-8') as f:
        f.write(chunk11.text)
    print(f"\nFull chunk 11 saved to: {debug_file}")
else:
    print(f"\nERROR: Only {len(chunks)} chunks found, chunk 11 doesn't exist!")

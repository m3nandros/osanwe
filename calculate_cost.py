import re
from pathlib import Path

def calculate_cost():
    log_path = Path("logs/rosetta.log")
    if not log_path.exists():
        print("Log file not found.")
        return

    with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
        lines = f.readlines()

    # Filter for the last run (based on the last "Starting translation pipeline" or just the recent timestamps)
    # Since we know the last run was recent, let's just look at the last 1000 lines and find the "Translation complete" entries
    # associated with the last session.
    
    # Or simpler: just sum up all "Translation complete" lines that appear after the last "Rosetta v2 - Translation Pipeline" header
    
    last_run_start_idx = 0
    for i, line in enumerate(lines):
        if "Rosetta v2 - Translation Pipeline" in line:
            last_run_start_idx = i
            
    relevant_lines = lines[last_run_start_idx:]
    
    total_input = 0
    total_output = 0
    
    # Pattern: Translation complete: 2361 input tokens, 2363 output tokens
    pattern = re.compile(r"Translation complete: (\d+) input tokens, (\d+) output tokens")
    
    for line in relevant_lines:
        match = pattern.search(line)
        if match:
            total_input += int(match.group(1))
            total_output += int(match.group(2))
            
    print(f"Total Input Tokens: {total_input}")
    print(f"Total Output Tokens: {total_output}")
    
    # GPT-5.1 Pricing (from search)
    # Input: $1.25 / 1M
    # Output: $10.00 / 1M
    
    cost_input = (total_input / 1_000_000) * 1.25
    cost_output = (total_output / 1_000_000) * 10.00
    total_cost = cost_input + cost_output
    
    print(f"Estimated Cost (GPT-5.1): ${total_cost:.4f}")

if __name__ == "__main__":
    calculate_cost()

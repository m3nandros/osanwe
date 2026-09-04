import re
from pathlib import Path

def calculate_cost_for_model():
    log_path = Path("logs/rosetta.log")
    
    with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
        lines = f.readlines()
    
    # Find the last run
    last_run_start = 0
    for i, line in enumerate(lines):
        if "Rosetta v2 - Translation Pipeline" in line:
            last_run_start = i
    
    relevant_lines = lines[last_run_start:]
    
    total_input = 0
    total_output = 0
    
    pattern = re.compile(r"Translation complete: (\d+) input tokens, (\d+) output tokens")
    
    for line in relevant_lines:
        match = pattern.search(line)
        if match:
            total_input += int(match.group(1))
            total_output += int(match.group(2))
    
    print(f"Total Input Tokens: {total_input:,}")
    print(f"Total Output Tokens: {total_output:,}")
    print()
    
    # GPT-5-mini (actual cost from logs)
    gpt5_mini_input_price = 0.25 / 1_000_000
    gpt5_mini_output_price = 2.00 / 1_000_000
    mini_cost = (total_input * gpt5_mini_input_price) + (total_output * gpt5_mini_output_price)
    
    # GPT-5.1
    gpt51_input_price = 1.25 / 1_000_000
    gpt51_output_price = 10.00 / 1_000_000
    gpt51_cost = (total_input * gpt51_input_price) + (total_output * gpt51_output_price)
    
    print(f"GPT-5-mini cost: ${mini_cost:.4f}")
    print(f"GPT-5.1 cost:    ${gpt51_cost:.4f}")
    print(f"Difference:      ${gpt51_cost - mini_cost:.4f} ({gpt51_cost/mini_cost:.1f}x more expensive)")

if __name__ == "__main__":
    calculate_cost_for_model()

import json
import sys
from pathlib import Path

def sort_json(data):
    if isinstance(data, dict):
        # Sort keys by: 1) type of value (strings first), 2) case-insensitive key
        sorted_items = sorted(
            data.items(),
            key=lambda kv: (0 if isinstance(kv[1], str) else 1, kv[0].lower())
        )
        return {k: sort_json(v) for k, v in sorted_items}
    elif isinstance(data, list):
        return [sort_json(item) for item in data]
    else:
        return data

def main(input_path, output_path=None):
    input_path = Path(input_path)
    output_path = Path(output_path) if output_path else input_path.with_name(f"sorted_{input_path.name}")

    with input_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    sorted_data = sort_json(data)

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(sorted_data, f, indent=2, ensure_ascii=False)

    print(f"Sorted JSON saved to: {output_path}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python sort_json_custom.py input.json [output.json]")
    else:
        main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)

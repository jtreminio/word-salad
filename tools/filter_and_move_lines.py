import argparse

def filter_lines(input_file, output_file, match_string):
    with open(input_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    keep = []
    move = []

    for line in lines:
        if match_string in line:
            move.append(line)
        else:
            keep.append(line)

    with open(input_file, 'w', encoding='utf-8') as f:
        f.writelines(keep)

    with open(output_file, 'a', encoding='utf-8') as f:
        f.writelines(move)

    print(f"Moved {len(move)} lines containing '{match_string}' to {output_file}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Move matching lines from one file to another.")
    parser.add_argument("--input", required=True, help="Input file to scan and modify")
    parser.add_argument("--output", required=True, help="File to append matched lines to")
    parser.add_argument("--match", required=True, help="String to search for in each line")

    args = parser.parse_args()
    filter_lines(args.input, args.output, args.match)

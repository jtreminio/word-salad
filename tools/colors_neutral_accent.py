import csv
import random

# Load color names
color_file = "rgb.csv"
name_to_hex = {}

with open(color_file, "r") as f:
    reader = csv.reader(f)
    for row in reader:
        if not row or row[0].startswith("#"):
            continue
        name, hexcode = row
        name = name.strip().lower()
        name_to_hex[name] = hexcode.strip().lower()

# Define neutral keywords
neutral_keywords = ["grey", "gray", "beige", "taupe", "cream", "cement", "white", "black", "stone", "sand", "warm brown", "muted grey", "soft taupe"]

# Split neutrals and accents
neutrals = [name for name in name_to_hex if any(k in name for k in neutral_keywords)]
accents = [name for name in name_to_hex if name not in neutrals]

# Generate 5 combos per neutral
output_lines = []

for neutral in sorted(neutrals):
    sampled_accents = random.sample(accents, 5)
    for accent in sampled_accents:
        output_lines.append(f"{neutral} <var:color_var_1> and {accent} <var:color_var_2>")

# Write to file
with open("_data/colors/neutral_plus_accent.txt", "w") as f_out:
    instructions = """
# <setvar[color_var_1,false]:theme><setvar[color_var_2,false]:highlights>
# <setvar[color_var_1,false]:background><setvar[color_var_2,false]:highlights>
# <setvar[color_var_1,false]:subject><setvar[color_var_2,false]:highlights>
"""
    f_out.write(instructions.lstrip())

    for line in output_lines:
        f_out.write(line + "\n")

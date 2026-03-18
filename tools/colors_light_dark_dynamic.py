import csv
import random

# Load color names and RGB
color_file = "rgb.csv"
name_to_rgb = {}

with open(color_file, "r") as f:
    reader = csv.reader(f)
    for row in reader:
        if not row or row[0].startswith("#"):
            continue
        name, hexcode = row
        name = name.strip().lower()
        hexcode = hexcode.strip().lower()
        rgb = tuple(int(hexcode[i:i+2], 16) for i in (1, 3, 5))
        name_to_rgb[name] = rgb

# Calculate perceived luminance (0–1 range)
def luminance(rgb):
    r, g, b = [x / 255.0 for x in rgb]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b

# Classify by luminance
light_colors = []
dark_colors = []

for name, rgb in name_to_rgb.items():
    l = luminance(rgb)
    if l >= 0.7:
        light_colors.append(name)
    elif l <= 0.3:
        dark_colors.append(name)

# Generate pairs: light with 3 darks
output_lines = []
for light in light_colors:
    dark_sample = random.sample(dark_colors, 10)
    for dark in dark_sample:
        output_lines.append(f"{light} <var:color_var_1> and {dark} <var:color_var_2>")

# Save output
with open("_data/colors/light_dark_dynamic.txt", "w") as f_out:
    instructions = """
# <setvar[color_var_1,false]:theme><setvar[color_var_2,false]:highlights>
# <setvar[color_var_1,false]:background><setvar[color_var_2,false]:highlights>
# <setvar[color_var_1,false]:subject><setvar[color_var_2,false]:highlights>
"""
    f_out.write(instructions.lstrip())

    for line in output_lines:
        f_out.write(line + "\n")

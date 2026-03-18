import csv
import colorsys
import random

# Load color names and hex codes
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

# Convert RGB to Hue (in degrees)
def get_hue(rgb):
    r, g, b = [x / 255.0 for x in rgb]
    h, _, s = colorsys.rgb_to_hls(r, g, b)
    return h * 360 if s > 0.1 else None  # skip desaturated tones

# Split into warm (hue 0–90 or 330–360) and cool (hue 90–270)
warm_colors = []
cool_colors = []

for name, rgb in name_to_rgb.items():
    hue = get_hue(rgb)
    if hue is None:
        continue
    if hue <= 90 or hue >= 330:
        warm_colors.append(name)
    elif 90 < hue < 270:
        cool_colors.append(name)

# Generate contrast pairs
output_lines = []
for warm in warm_colors:
    cool_sample = random.sample(cool_colors, 3)
    for cool in cool_sample:
        output_lines.append(f"{warm} <var:color_var_1> and {cool} <var:color_var_2>")

# Save result
with open("_data/colors/warm_vs_cool_contrast.txt", "w") as f_out:
    instructions = """
# <setvar[color_var_1,false]:theme><setvar[color_var_2,false]:highlights>
# <setvar[color_var_1,false]:background><setvar[color_var_2,false]:highlights>
# <setvar[color_var_1,false]:subject><setvar[color_var_2,false]:highlights>
"""
    f_out.write(instructions.lstrip())

    for line in output_lines:
        f_out.write(line + "\n")

import colorsys
import csv
from collections import defaultdict

# Load named colors from CSV
color_file = "rgb.csv"
name_to_rgb = {}

with open(color_file, "r") as f:
    reader = csv.reader(f)
    for row in reader:
        if not row or row[0].startswith("#"):
            continue
        name, hexcode = row
        hexcode = hexcode.strip()
        rgb = tuple(int(hexcode[i:i+2], 16) for i in (1, 3, 5))
        key = f"{name.strip().lower()}|{hexcode.lower()}"
        name_to_rgb[key] = rgb

# Assign hue to 12 bins (0–11)
def hue_bin(rgb):
    r, g, b = [x / 255.0 for x in rgb]
    h, _, _ = colorsys.rgb_to_hls(r, g, b)
    return int(h * 12) % 12  # 12 hue slices

# Group color names by hue bin
hue_groups = defaultdict(list)

for key, rgb in name_to_rgb.items():
    name, _ = key.split("|")
    bin_id = hue_bin(rgb)
    hue_groups[bin_id].append(name)

# Sort and write output
with open("_data/colors/monochromatic_groups.txt", "w") as f_out:
    for bin_id in sorted(hue_groups.keys()):
        names = sorted(set(hue_groups[bin_id]))
        f_out.write(", ".join(names) + "\n")

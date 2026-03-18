import colorsys
import csv
import numpy as np
from sklearn.metrics.pairwise import euclidean_distances

# Load CSV color name and hex values
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

# Rotate hue by degrees (as fraction)
def rotate_hue(rgb, degree_offset):
    r, g, b = [x / 255.0 for x in rgb]
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    h = (h + degree_offset / 360.0) % 1.0
    r_new, g_new, b_new = colorsys.hls_to_rgb(h, l, s)
    return tuple(int(x * 255) for x in (r_new, g_new, b_new))

# Find closest matching color
def closest_color(rgb_target, name_rgb_dict):
    all_rgbs = np.array(list(name_rgb_dict.values()))
    distances = euclidean_distances([rgb_target], all_rgbs)
    idx = np.argmin(distances)
    closest_name = list(name_rgb_dict.keys())[idx]
    return closest_name, tuple(all_rgbs[idx])

# Output split complements
with open("_data/colors/split_complementary.txt", "w", newline="") as f_out:
    instructions = """
# <setvar[color_var_1,false]:base><setvar[color_var_2,false]:blend><setvar[color_var_3,false]:highlight>
# <setvar[color_var_1,false]:base><setvar[color_var_2,false]:gradient><setvar[color_var_3,false]:highlight>
"""
    f_out.write(instructions.lstrip())

    for full_key, rgb in name_to_rgb.items():
        base_name, _ = full_key.split("|")

        split1_rgb = rotate_hue(rgb, 150)
        split2_rgb = rotate_hue(rgb, 210)

        split1_name, _ = closest_color(split1_rgb, name_to_rgb)
        split2_name, _ = closest_color(split2_rgb, name_to_rgb)

        split1_name = split1_name.split("|")[0]
        split2_name = split2_name.split("|")[0]

        line = f"{base_name} <var:color_var_1>, {split1_name} <var:color_var_2> and {split2_name} <var:color_var_3>\n"
        f_out.write(line)

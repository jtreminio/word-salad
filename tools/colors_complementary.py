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
        # Use unique name:hex combination to avoid silent overwrite
        key = f"{name.strip().lower()}|{hexcode.lower()}"
        name_to_rgb[key] = rgb

# Convert RGB to complementary RGB via HLS rotation
def get_complement_rgb(rgb):
    r, g, b = [x / 255.0 for x in rgb]
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    h = (h + 0.5) % 1.0
    r_c, g_c, b_c = colorsys.hls_to_rgb(h, l, s)
    return tuple(int(x * 255) for x in (r_c, g_c, b_c))

# Find closest color in name_to_rgb by Euclidean RGB distance
def closest_color(rgb_target, name_rgb_dict):
    all_rgbs = np.array(list(name_rgb_dict.values()))
    distances = euclidean_distances([rgb_target], all_rgbs)
    idx = np.argmin(distances)
    closest_name = list(name_rgb_dict.keys())[idx]
    return closest_name, tuple(all_rgbs[idx])

# Output results
with open("_data/colors/complementary.txt", "w", newline="") as f_out:
    instructions = """
# <setvar[color_var_1,false]:theme><setvar[color_var_2,false]:highlights>
# <setvar[color_var_1,false]:background><setvar[color_var_2,false]:highlights>
# <setvar[color_var_1,false]:subject><setvar[color_var_2,false]:highlights>
"""
    f_out.write(instructions.lstrip())

    for full_key, rgb in name_to_rgb.items():
        comp_rgb = get_complement_rgb(rgb)
        comp_name, comp_rgb_actual = closest_color(comp_rgb, name_to_rgb)

        name, _ = full_key.split("|")
        comp_name, _ = comp_name.split("|")

        line = f"{name} <var:color_var_1> and {comp_name} <var:color_var_2>\n"
        f_out.write(line)

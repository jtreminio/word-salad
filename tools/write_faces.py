from pathlib import Path

faces = [
    "angry",
    "annoyed",
    "arguing",
    "aroused",
    "being watched",
    "bored",
    "confused",
    "crazy",
    "depressed",
    "despair",
    "determined",
    "disappointed",
    "disdain",
    "disgust",
    "drunk",
    "embarrassed",
    "envy",
    "evil",
    "exhausted",
    "flustered",
    "frustrated",
    "happy",
    "horrified",
    "lonely",
    "nervous",
    "panicking",
    "pensive",
    "sad",
    "scared",
    "screaming",
    "serious",
    "sleepy",
    "sexy",
    "smug",
    "sobbing",
    "sulking",
    "surprised",
    "thinking",
    "upset",
    "worried",
]

for face in faces:
    ref_file = Path("_data/body/face/") / f"{face}.txt"
    contents = f"{face}, <random:||<wc:body/face/{face}/data>>,<random:||<wc:body/face/{face}/eyes>>,<random:||<wc:body/face/{face}/mouth>>,<random:||<wc:body/face/{face}/symbols>>"

    with ref_file.open("w", encoding="utf-8") as f:
        f.write(contents)

ref_file = Path("_data/body/face.txt")
with ref_file.open("w", encoding="utf-8") as f:
    for face in faces:
        contents = f"<wc:_bundles/face_{face}>\n"
        f.write(contents)

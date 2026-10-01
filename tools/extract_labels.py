import argparse
import json
import re
import sys

# GFF40 label extraction
#
# Example run:
#   uv run .\tools\extract_labels.py --dll "F:\SteamLibrary\steamapps\common\Dragon Age Ultimate Edition\tools\plugins\EditorGff40.dll" --out .\src\thedas_io\gff40\labels.py

HEADER = (
    "# LABEL ID -> name table (generated from BinaryGFFIDList.h in EditorGFF40.dll)\n"
    "# Generated automatically, do not edit!\n"
    "\n\nDATA = {\n"
)
FOOTER = "}\n"


def get_from_dll(path):
    with open(path, "rb") as f:
        data = f.read()
    text = data.decode("ascii", errors="ignore")
    pairs = re.findall(r"(GFF_[A-Z0-9_]+)\s*=\s*(\d+)", text)
    hash = {}
    for name, value in pairs:
        hash[int(value)] = name
    return hash


def write_to_file(hash, path):
    with open(path, "w") as f:
        f.write(HEADER)
        f.writelines(f'    {key}: "{hash[key]}",\n' for key in sorted(hash))
        f.write(FOOTER)
    print(f"Wrote {len(hash)} labels to {path}")


if __name__ == "__main__":
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--dll", default="")
    argument_parser.add_argument("--json", default="")
    argument_parser.add_argument("--out", default="src/thedas_io/gff40/labels.py")
    arg = argument_parser.parse_args()

    if arg.json:
        with open(arg.json) as f:
            hash = {int(k): v for k, v in json.load(f).items()}
    elif arg.dll:
        hash = get_from_dll(arg.dll)
    else:
        sys.exit("Need --dll or --json!")

    if hash:
        write_to_file(hash, arg.out)

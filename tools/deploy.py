from __future__ import annotations

import json
import os
import shutil
import sys
import urllib.request
import zipfile

EXTENSION_ID = "thedas_io"
TARGET = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "Blender Foundation",
    "Blender",
    "5.2",
    "extensions",
    "user_default",
    EXTENSION_ID,
)
WHEELS = (("pillow", "12.3.0", "cp313-cp313-win_amd64"), ("pynvtt", "0.0.2", "py3-none-any"))


def fetch_wheels(dest):
    os.makedirs(dest, exist_ok=True)
    for pkg, ver, tag in WHEELS:
        meta = json.load(urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/{ver}/json"))
        urls = [u for u in meta["urls"] if tag in u["filename"] and u["filename"].endswith(".whl")]
        for u in urls:
            path = os.path.join(dest, u["filename"])
            if not os.path.exists(path):
                urllib.request.urlretrieve(u["url"], path)


def install_wheels(target):
    # Blender only installs manifest wheels on "Install from Disk".
    # A plain copy into user_default/ skips that step, so unpack them into
    # libs/; thedas_io/__init__.py puts libs/ on sys.path (see _libs).
    libs = os.path.join(target, "libs")
    os.makedirs(libs, exist_ok=True)
    for name in sorted(os.listdir(os.path.join(target, "wheels"))):
        if not name.endswith(".whl"):
            continue
        with zipfile.ZipFile(os.path.join(target, "wheels", name)) as z:
            z.extractall(libs)
        print(f"installed {name}")


def package(out):
    # Drag-and-drop Blender package: one top-level thedas_io/ folder with
    # the manifest and wheels. Blender installs the wheels itself on
    # "Install from Disk", so libs/ (the local-deploy unpack) stays out.
    src = os.path.join(
        os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)), "src", "thedas_io"
    )
    fetch_wheels(os.path.join(src, "wheels"))
    out = os.path.abspath(out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(src):
            dirs[:] = sorted(d for d in dirs if d not in ("__pycache__", "libs"))
            for name in sorted(files):
                full = os.path.join(root, name)
                z.write(full, os.path.join("thedas_io", os.path.relpath(full, src)))
    print(f"packaged {out}")
    return out


def main(root=None) -> str:
    src = os.path.join(
        os.path.abspath(root or os.path.join(os.path.dirname(__file__), os.pardir)),
        "src",
        "thedas_io",
    )
    fetch_wheels(os.path.join(src, "wheels"))
    shutil.rmtree(TARGET, ignore_errors=True)
    shutil.copytree(src, TARGET, ignore=shutil.ignore_patterns("__pycache__"))
    install_wheels(TARGET)
    count = sum(len(files) for _, _, files in os.walk(TARGET))
    print(f"deployed {count} files to {TARGET}")
    return TARGET


if __name__ == "__main__":
    if "--package" in sys.argv:
        i = sys.argv.index("--package")
        sys.exit(package(sys.argv[i + 1]) is None)
    sys.exit(main() is None)

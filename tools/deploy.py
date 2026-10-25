from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import urllib.request
import zipfile

EXTENSION_ID = "thedas_io"
EXTENSIONS_DIR = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "Blender Foundation",
    "Blender",
    "5.2",
    "extensions",
)
TARGET = os.path.join(EXTENSIONS_DIR, "user_default", EXTENSION_ID)
# Blender's own wheel location, outside the extension directory. See
# addon_utils._extension_sync_wheels. Anything unpacked *inside* the extension
# trips its policy check ("Policy violation with top level module"), which is
# why libs/ used to live there and no longer does.
SITE_PACKAGES = os.path.join(EXTENSIONS_DIR, ".local", "lib", "python3.13", "site-packages")
# abi3 wheel, so one file serves every CPython 3.7+ Blender. the cp313 build is
# the free-threaded one and must not be picked up here.
# (name, version, platform tag, sha256): the hash is checked at fetch and again
# before unpacking, so a renamed or tampered PyPI artifact cannot ship.
WHEELS = (
    (
        "pillow",
        "12.3.0",
        "cp313-cp313-win_amd64",
        "1cca606cd25738df4ed873d5ad46bbdb3d83b5cbca291f6b4ff13a4df6b0bbe8",
    ),
    (
        "pynvtt",
        "0.0.2",
        "py3-none-any",
        "0572fe513bffeca73ce4d3aa5330362e4e9bde1699da6ca14270a7d0624894ca",
    ),
    (
        "pycryptodome",
        "3.24.1",
        "cp37-abi3-win_amd64",
        "c00aa444033bac0379413728e92223c7e2f2b5b85fb3e9284fee19239b6ad8a4",
    ),
)


def _wheel_name(pkg: str, version: str, tag: str) -> str:
    return f"{pkg}-{version}-{tag}.whl"


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify(path: str, expected: str) -> None:
    got = _sha256(path)
    if got.lower() != expected.lower():
        raise SystemExit(f"{os.path.basename(path)}: sha256 {got} != expected {expected}")


def fetch_wheels(dest):
    os.makedirs(dest, exist_ok=True)
    for pkg, ver, tag, expect in WHEELS:
        meta = json.load(urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/{ver}/json"))
        urls = [u for u in meta["urls"] if tag in u["filename"] and u["filename"].endswith(".whl")]
        for u in urls:
            path = os.path.join(dest, u["filename"])
            if os.path.exists(path) and _sha256(path).lower() != expect.lower():
                os.remove(path)  # stale or tampered copy: fetch it again
            if not os.path.exists(path):
                urllib.request.urlretrieve(u["url"], path)
            _verify(path, expect)


def install_wheels(target):
    # Blender only installs manifest wheels on "Install from Disk", so a plain
    # copy into user_default/ skips that step. Unpack them into the same
    # site-packages Blender would have used instead; that directory is outside
    # the extension, so it raises no policy warning.
    libs = os.path.join(target, "libs")
    shutil.rmtree(libs, ignore_errors=True)  # left by an older deploy

    os.makedirs(SITE_PACKAGES, exist_ok=True)
    expected = {_wheel_name(pkg, ver, tag): sha for pkg, ver, tag, sha in WHEELS}
    for name in sorted(os.listdir(os.path.join(target, "wheels"))):
        if not name.endswith(".whl"):
            continue
        path = os.path.join(target, "wheels", name)
        digest = expected.get(name)
        if digest is None:
            raise SystemExit(f"unexpected wheel in {target}/wheels: {name}")
        _verify(path, digest)
        with zipfile.ZipFile(path) as z:
            z.extractall(SITE_PACKAGES)
        print(f"installed {name} -> {SITE_PACKAGES}")


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

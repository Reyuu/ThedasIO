"""Texture cache: unpack imported DDS/TGA copies beside the .blend and read them back."""

from __future__ import annotations

import io
import shutil
import sqlite3
from pathlib import Path
from typing import Any

import bpy
import numpy as np
from PIL import Image as PilImage

from .assets import resolve
from .datablocks import tag
from .export import _LOADED_ITEMS, remember
from .fsio import write_bytes_atomic
from .materials import pil_to_blender, role_of, thedas_io_normals_to_rgba
from .tnt import parse

_CACHE_DIR_NAME = "thedas_io_textures"
_CACHE_NOTED = False


def _existing_tint(
    stem: str, row: dict | None, db_path=None, local_only: bool = False
) -> dict | None:
    # tint values from stem.tnt next to mao or in db

    if not stem or row is None:
        return None

    try:
        path = resolve(f"{stem}.tnt", row, db_path, local_only)
        return parse(path.read_bytes()) if path is not None and path.is_file() else None
    except (OSError, ValueError):
        return None


def _resolve_texture(res, mao_path, row=None, db_path=None, local_only: bool = False):
    # one resname to blender image via sibling, db or loaded bytes

    source = mao_path.parent / res
    if source.is_file():
        return load_image(source)
    if row is not None and not row.get("package"):
        sibling = Path(row["path"]).parent / res
        if sibling.is_file():
            return load_image(sibling)

    anchor = (
        dict(row)
        if row
        else {
            "package": None,
            "filename": mao_path.name,
            "ext": mao_path.suffix,
            "path": str(mao_path),
        }
    )
    try:
        hit = resolve(res, anchor, db_path, local_only)
    except (OSError, ValueError, KeyError, sqlite3.Error):
        hit = None
    if hit is not None and Path(hit).is_file():
        return load_image(hit)

    # texture in archive, pull from loaded db items

    tex_bytes = None
    want = Path(res).name.lower()
    for item in _LOADED_ITEMS:
        if item["file"].lower() == want:
            tex_bytes = item["raw"]
            break
    if tex_bytes is None:
        return None
    pil = PilImage.open(io.BytesIO(tex_bytes))
    pil.load()
    return _new_image(Path(res).name, pil, tex_bytes)


def _cache_dir() -> Path:
    # texture copies beside blend, else blender temp dir
    global _CACHE_NOTED

    base = Path(bpy.data.filepath).parent if bpy.data.filepath else Path(bpy.app.tempdir)
    out = base / _CACHE_DIR_NAME

    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"DAO: no texture cache at {out}: {e}")

    if not _CACHE_NOTED and out.is_dir():
        _CACHE_NOTED = True
        print(f"DAO: texture cache at {out} (edit these to change a texture)")
    return out


def _strip_suffix(name: str) -> str:
    stem, dot, digits = name.rpartition(".")
    if dot and len(digits) == 3 and digits.isdigit():
        return stem
    return name


def _cache_file(image_name: str, folder: Path) -> str | None:
    # the cache keeps the source name, so a re-imported texture ("name.001")
    # still has to resolve to "name" or reload/repack silently skip it
    if image_name and (folder / image_name).is_file():
        return image_name
    stem = _strip_suffix(image_name)
    if stem != image_name and (folder / stem).is_file():
        return stem
    return None


def _texture_images() -> list:
    # images with editable copy in cache, found via bpy data

    folder = _cache_dir()
    out = []

    for image in bpy.data.images:
        try:
            name = image.name
        except ReferenceError:
            continue
        cache = _cache_file(name, folder) if name else None
        if cache is not None:
            out.append((image, cache))
    return out


def reload_textures() -> tuple[list, list, list]:
    # re-read cached textures from disk and re-pack

    folder = _cache_dir()
    done, missing, failed = [], [], []

    for image, name in _texture_images():
        path = folder / name
        if not path.is_file():
            missing.append(name)
            continue

        try:
            with PilImage.open(path) as im:
                im.load()
                pil = im.copy()
        except OSError as e:
            failed.append(f"{name}: {e}")
            continue

        if tuple(pil.size) != tuple(image.size):
            try:
                image.scale(*pil.size)
            except (AttributeError, ReferenceError, RuntimeError) as e:
                failed.append(f"{name}: cannot rescale live image: {e}")
                continue

        try:
            _blit(image, pil, role_of(name))
            image.pack()
            done.append(name)
        except (AttributeError, ReferenceError, RuntimeError) as e:
            failed.append(f"{name}: {e}")
    return done, missing, failed


def _move_file(src: Path, dest: Path) -> None:
    # move src onto dest, copy fallback across drives
    try:
        src.replace(dest)
    except OSError:
        shutil.copy2(src, dest)
        src.unlink()


def _relocate_cache(scene=None, depsgraph=None) -> None:
    # move temp cache beside blend once saved

    if not bpy.data.filepath:
        return
    src = Path(bpy.app.tempdir) / _CACHE_DIR_NAME
    if not src.is_dir():
        return
    dest = Path(bpy.data.filepath).parent / _CACHE_DIR_NAME
    if dest == src:
        return

    try:
        dest.mkdir(parents=True, exist_ok=True)
        for f in list(src.iterdir()):
            target = dest / f.name
            if not target.exists():
                _move_file(f, target)  # never clobber edit already there
    except OSError as e:
        print(f"DAO: could not move the texture cache: {e}")
        return
    try:
        src.rmdir()
    except OSError:
        pass
    print(f"DAO: texture cache moved to {dest}")


def _scale_live_images(model: dict, repacked: dict, failed: list) -> set:
    # resize live images to repacked sizes, keep-resolution only

    try:
        images = list(bpy.data.images)
    except (AttributeError, ReferenceError):
        return set()

    by_name: dict = {}
    for image in images:
        try:
            name = image.name
        except ReferenceError:
            continue
        by_name[name] = image
        stripped = _strip_suffix(name)
        if stripped != name:
            # the newer duplicate ("name.001") is the one the item exports
            by_name[stripped] = image

    textures = model.get("textures") or {}
    skipped: set = set()

    for slot, pil in repacked.items():
        res = textures.get(slot)
        if not res:
            continue
        live: Any = by_name.get(Path(res).name)
        if live is None:
            continue
        try:
            size = tuple(live.size)
        except (AttributeError, ReferenceError):
            continue
        if size != tuple(pil.size):
            try:
                live.scale(*pil.size)
            except (AttributeError, ReferenceError, RuntimeError) as e:
                failed.append(f"{res}: cannot rescale live image: {e}")
                skipped.add(slot)
    return skipped


def _blit(image, pil, role: str | None) -> None:
    # push decoded dao pixels into image, normals unswizzled

    shown = thedas_io_normals_to_rgba(pil) if role == "normal" else pil
    image.pixels.foreach_set(pil_to_blender(shown))
    image.update()


def _new_image(name, pil, raw):
    # blender image from pillow pixels, stashes source bytes

    cached = _cache_dir() / Path(name).name
    if cached.is_file():
        try:  # edited copy wins
            with PilImage.open(cached) as im:
                im.load()
                pil = im
        except OSError:
            pass
    else:
        try:
            write_bytes_atomic(cached, raw)
        except OSError:
            pass

    role = role_of(name)
    image: Any = bpy.data.images.new(name, pil.width, pil.height, alpha=True)
    tag(image, "image")
    image.alpha_mode = "CHANNEL_PACKED"
    colorspace: Any = image.colorspace_settings
    colorspace.name = "sRGB" if role == "diffuse" else "Non-Color"

    _blit(image, pil, role)
    try:
        image.pack()  # pixels ride along in blend
    except RuntimeError:
        pass
    remember(name, "texture", None, raw)["objects"].append(image)
    return image


def load_image(path):
    # dds/tga file to blender image, stashes source bytes

    raw = Path(path).read_bytes()
    pil = PilImage.open(io.BytesIO(raw))
    pil.load()
    return _new_image(Path(path).name, pil, raw)


def _missing_image(res, images):
    # magenta stand-in for unresolvable resname, tagged to skip export

    sizes = []
    for im in images.values():
        try:
            sizes.append(tuple(im.size))
        except (AttributeError, ReferenceError, TypeError):
            continue

    w, h = max(sizes, key=lambda s: s[0] * s[1]) if sizes else (64, 64)
    name = Path(res).name
    image: Any = bpy.data.images.new(name, w, h, alpha=True)
    tag(image, "image")
    image.alpha_mode = "CHANNEL_PACKED"
    colorspace: Any = image.colorspace_settings
    colorspace.name = "sRGB" if role_of(name) == "diffuse" else "Non-Color"

    flat = np.zeros(w * h * 4, dtype=np.float32)
    flat[0::4] = 1.0
    flat[2::4] = 1.0
    flat[3::4] = 1.0
    image.pixels.foreach_set(flat)
    image.update()

    try:
        image.pack()
    except RuntimeError:
        pass

    try:
        image["thedas_io_missing"] = res
    except (AttributeError, TypeError):
        pass
    return image

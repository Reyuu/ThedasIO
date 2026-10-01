# standalone export of loaded resources, untouched ones go out byte-identical

import io
import math
import struct
import tempfile
from pathlib import Path

import bpy
import numpy as np
from nvtt.compression import CompressionOptions
from nvtt.context import Context
from nvtt.enums import Format, Quality
from nvtt.output import OutputOptions
from nvtt.surface import Surface
from PIL import Image

from . import tnt
from .geometry import (
    FORMATS,
    IDENTITY_QUAT,
    _decode_row,
    compose,
    decode_chunk,
    flatten,
    node_local,
    node_paths,
    quat_mul,
    quat_rotate,
    triangulate_loops,
)
from .geometry import (
    USAGE_BINORMAL as _USAGE_BINORMAL,
)
from .geometry import (
    USAGE_BLENDINDICES as _USAGE_INDICES,
)
from .geometry import (
    USAGE_BLENDWEIGHT as _USAGE_WEIGHT,
)
from .geometry import (
    USAGE_NORMAL as _USAGE_NORMAL,
)
from .geometry import (
    USAGE_POSITION as _USAGE_POSITION,
)
from .geometry import (
    USAGE_TANGENT as _USAGE_TANGENT,
)
from .geometry import (
    USAGE_TEXCOORD as _USAGE_TEXCOORD,
)
from .gff40.description import GFFReader, GFFWriter
from .materials import (
    resize_rgba,
    role_of,
    slot_roles,
    swizzle_for_encode,
    thedas_io_normals_to_rgba,
    write_mao,
)
from .msh.description import MSHReader, MSHWriter
from .tnt import NAMES, SIDES, alpha_input, group_name

ITEMS: dict[str, dict] = {}
_LOADED_ITEMS: list[dict] = []


def remember(filename, kind, model, raw):
    # fresh import replaces the old one so edits export, not first-read bytes
    item = ITEMS.get(filename)
    if item is None:
        item = ITEMS[filename] = {
            "file": filename,
            "kind": kind,
            "model": model,
            "raw": raw,
            "objects": [],
        }
        _LOADED_ITEMS.append(item)
    elif model is not None:
        if item.get("model") is not model:
            item.update(kind=kind, model=model, raw=raw, objects=[])
    else:
        # textures carry no model so just clear, newest image wins
        item.update(kind=kind, raw=raw, objects=[])
    return item


def reset():
    ITEMS.clear()
    _LOADED_ITEMS.clear()


def export_bytes(item, tint=None, renames=None):
    # source bytes unless edited, then kind-specific re-encode
    check = _DIRTY.get(item["kind"])
    edited = None
    if check is not None:
        edited = check(item, tint, renames)
    if edited is None:
        return item["raw"]
    return edited


def export_all(dest, tint=False, tint_name="", only=None):
    # tint goes two places, a .tnt plus baked into the mao, and 6340 gets cleared
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    written, errors = [], []
    keys = [k for k in ITEMS if only is None or k in only]

    renames = {}
    for k in keys:
        name = (ITEMS[k].get("as") or "").strip()
        if name and name.lower() != k.lower():
            renames[k.lower()] = name

    taken: dict = {}
    for k in keys:
        taken.setdefault((renames.get(k.lower()) or k).lower(), []).append(k)
    for out, ks in taken.items():
        if len(ks) > 1:
            errors.append(f"{out}: renamed twice ({', '.join(ks)}); skipping extras")
    skip = {k for ks in taken.values() if len(ks) > 1 for k in ks[1:]}

    model_tints: dict = {}  # id(mmh item) -> its merged values
    mao_tints: dict = {}  # MAO MaterialObject name -> that material's values
    if tint:
        for k in keys:
            item = ITEMS[k]
            if item["kind"] != "mmh":
                continue
            found = _tint_by_material(item)
            if not found:
                errors.append(
                    f"{item['file']}: no DAO Tint group on the imported "
                    "meshes (no tint mask texture?)"
                )
                continue
            merged: dict = {}
            for values in found.values():
                merged.update(values)
            model_tints[id(item)] = merged
            mao_tints.update(found)
            try:
                name = item.get("as_tnt") or _tnt_name(item, tint_name)
                (dest / name).write_bytes(_tnt_bytes(merged))
                written.append(name)
            except (OSError, ValueError) as e:
                errors.append(f"{_tnt_name(item, tint_name)}: {e}")

    for k in keys:
        if k in skip:
            continue
        item = ITEMS[k]
        out_name = renames.get(k.lower()) or item["file"]
        if not tint:
            values = None
        elif item["kind"] == "mmh":
            values = model_tints.get(id(item))
        else:
            model = item.get("model")
            values = mao_tints.get(model.get("name")) if isinstance(model, dict) else None
        try:
            (dest / out_name).write_bytes(export_bytes(item, values, renames))
            written.append(out_name)
        except (OSError, ValueError) as e:
            errors.append(f"{out_name}: {e}")

    return written, errors


def _tnt_name(item: dict, tint_name: str = "") -> str:
    name = (tint_name or "").strip().lower()
    if name:
        return name if name.endswith(".tnt") else f"{name}.tnt"
    return f"{Path(item['file']).stem}.tnt"


def _tnt_bytes(values: dict) -> bytes:
    # the ten tint colours as a .tnt file
    return tnt.build(values)


def roots() -> list:
    # hierarchy roots, in import order: every mmh, plus every standalone msh
    referenced = set()
    for item in ITEMS.values():
        if item.get("kind") != "mmh":
            continue
        model = item.get("model")
        msh = getattr(model, "values", {}).get(6005) if model is not None else None
        if msh:
            referenced.add(str(msh).lower())
    return [
        k
        for k, item in ITEMS.items()
        if item.get("kind") == "mmh" or (item.get("kind") == "msh" and k.lower() not in referenced)
    ]


def _short_list(values, limit: int = 5) -> str:
    values = list(values)
    shown = ", ".join(str(v) for v in values[:limit])
    return shown if len(values) <= limit else f"{shown} (+{len(values) - limit} more)"


def check_model(only: set | None = None) -> list:
    # pre-export sanity notes for the checked hierarchies; [] means clean

    notes: list = []
    keys = [k for k in ITEMS if only is None or k in only]
    mao_by_material = {}
    for k in keys:
        item = ITEMS[k]
        if item.get("kind") != "mao":
            continue
        model = item.get("model")
        if isinstance(model, dict) and model.get("name"):
            mao_by_material[model["name"].lower()] = k

    for k in keys:
        item = ITEMS[k]
        kind = item.get("kind")
        if kind == "msh":
            notes.extend(_check_msh_item(item, mao_by_material))
        elif kind == "mao":
            notes.extend(_check_mao_item(item))
        elif kind == "texture":
            notes.extend(_check_texture_item(item))
    return notes


def _live_polys(obj) -> list | None:
    # blender polygons, or None when the object cannot be read headless
    try:
        data = obj.data
    except (AttributeError, ReferenceError):
        return None
    try:
        return list(data.polygons)
    except (AttributeError, ReferenceError):
        return None


def _check_msh_item(item: dict, mao_by_material: dict) -> list:
    notes: list = []
    name = item.get("file") or "?"
    mesh = item.get("model")
    if mesh is None:
        return notes
    entries = item.get("objects") or []
    for ci, chunk in enumerate(mesh.chunks):
        try:
            decoded = decode_chunk(mesh, chunk)
        except ValueError as e:
            notes.append(f"{name}: chunk {chunk.name or ci} unreadable ({e})")
            continue

        label = f"{name}/{chunk.name or ci}"
        skinned = any(not d.is_terminator and d.usage == _USAGE_WEIGHT for d in chunk.declarators)
        if skinned:
            bare = [
                str(v)
                for v in range(decoded["vert_count"])
                if not any(v in [m[0] for m in ms] for ms in decoded["groups"].values())
            ]
            if bare:
                notes.append(
                    f"{label}: {len(bare)} verts have no weights "
                    f"({_short_list(bare)}); they stay rigid"
                )

        if decoded["triangles"]:
            bad = [
                str(t)
                for t, tri in enumerate(decoded["triangles"])
                if _tri_area([decoded["positions"][v] for v in tri]) <= 1e-12
            ]
            if bad:
                notes.append(f"{label}: {len(bad)} degenerate tris ({_short_list(bad)})")

        for layer, (_, coords) in enumerate(sorted(decoded["uvs"].items())):
            lo = min((c for p in coords for c in p), default=0.0)
            hi = max((c for p in coords for c in p), default=1.0)
            if lo < 0.0 or hi > 1.0:
                notes.append(
                    f"{label}: UV layer {layer} spans "
                    f"{lo:.3g}..{hi:.3g} (outside [0, 1]; atlases wrap)"
                )

        obj = entries[ci][0] if ci < len(entries) else None
        if isinstance(obj, (tuple, list)):
            obj = obj[0] if obj else None
        if obj is not None:
            notes.extend(_check_live_mesh(obj, label, chunk))
        for mat in _obj_materials(obj) if obj is not None else ():
            if mat.lower() not in mao_by_material:
                notes.append(f"{label}: material {mat!r} has no MAO (renders untextured in game)")
    return notes


def _tri_area(points) -> float:
    (ax, ay, az), (bx, by, bz), (cx, cy, cz) = points
    ux, uy, uz = bx - ax, by - ay, bz - az
    vx, vy, vz = cx - ax, cy - ay, cz - az
    return (
        0.5
        * ((uy * vz - uz * vy) ** 2 + (uz * vx - ux * vz) ** 2 + (ux * vy - uy * vx) ** 2) ** 0.5
    )


def _check_live_mesh(obj, label: str, chunk) -> list:
    # viewport-side checks: topology Blender edits may have introduced
    notes: list = []
    polys = _live_polys(obj)
    if polys is not None:
        quads = sum(1 for p in polys if len(p.vertices) != 3)
        if quads:
            notes.append(
                f"{label}: {quads} non-tri faces; export retriangulates them automatically"
            )

    try:
        mods = list(obj.modifiers)
    except (AttributeError, ReferenceError):
        mods = []
    for m in mods:
        if getattr(m, "type", "") != "ARMATURE" and getattr(m, "name", None):
            notes.append(f"{label}: modifier {m.name!r} is not applied on export")
            break

    try:
        has_shapes = obj.data.shape_keys is not None
    except (AttributeError, ReferenceError):
        has_shapes = False
    if has_shapes:
        notes.append(f"{label}: shape keys never export; apply them first")

    try:
        n_layers = len(obj.data.uv_layers)
    except (AttributeError, ReferenceError):
        n_layers = None
    if n_layers is not None:
        want = sum(
            1 for d in chunk.declarators if not d.is_terminator and d.usage == _USAGE_TEXCOORD
        )
        if n_layers < want:
            notes.append(
                f"{label}: {want - n_layers} UV layer(s) removed; "
                f"export keeps source bytes for those channels"
            )
        elif n_layers > want:
            notes.append(
                f"{label}: {n_layers - want} extra UV layer(s); only the declared {want} export"
            )
    return notes


def _check_mao_item(item: dict) -> list:
    notes: list = []
    name = item.get("file") or "?"
    model = item.get("model")
    if not isinstance(model, dict):
        return notes
    have = {k.lower() for k in ITEMS if ITEMS[k].get("kind") == "texture"}
    for res in (model.get("textures") or {}).values():
        if res and str(res).lower() not in have:
            notes.append(f"{name}: texture {res} not imported")
    return notes


def _check_texture_item(item: dict) -> list:
    notes: list = []
    name = item.get("file") or "?"
    try:
        with Image.open(io.BytesIO(item.get("raw") or b"")) as im:
            w, h = im.size
    except (OSError, ValueError):
        return [f"{name}: unreadable image bytes"]
    if w % 4 or h % 4:
        notes.append(f"{name}: {w}x{h} is not a multiple of 4; DXT compression requires it")
    if w & (w - 1) or h & (h - 1):
        notes.append(f"note: {name}: {w}x{h} is not power-of-two")
    return notes


def connected(seeds) -> set:
    # ITEMS keys in the same hierarchy as any seed
    by_key = {k.lower(): k for k in ITEMS}
    mao_by_material = {}
    mmh_by_msh = {}

    for k, item in ITEMS.items():
        if item.get("kind") == "mao":
            model = item.get("model")
            if isinstance(model, dict) and model.get("name"):
                mao_by_material[model["name"].lower()] = k
        elif item.get("kind") == "mmh":
            model = item.get("model")
            msh = getattr(model, "values", {}).get(6005) if model is not None else None
            if msh:
                mmh_by_msh[str(msh).lower()] = k

    out: set = set()
    stack = [s for s in seeds if s in ITEMS]

    while stack:
        key = stack.pop()
        if key in out:
            continue
        out.add(key)
        item = ITEMS[key]
        kind = item.get("kind")
        if kind == "mmh":
            model = item.get("model")
            msh = getattr(model, "values", {}).get(6005) if model is not None else None
            if msh:
                hit = by_key.get(str(msh).lower())
                if hit is not None and hit not in out:
                    stack.append(hit)
            for obj in item.get("meshes") or []:
                for mat in _obj_materials(obj):
                    hit = mao_by_material.get(mat.lower())
                    if hit is not None and hit not in out:
                        stack.append(hit)
        elif kind == "msh":
            hit = mmh_by_msh.get(key.lower())
            if hit is not None and hit not in out:
                stack.append(hit)
            for entry in item.get("objects") or []:
                obj = entry[0] if isinstance(entry, (tuple, list)) else entry
                for mat in _obj_materials(obj):
                    hit = mao_by_material.get(mat.lower())
                    if hit is not None and hit not in out:
                        stack.append(hit)
        elif kind == "mao":
            model = item.get("model")
            if isinstance(model, dict):
                for res in (model.get("textures") or {}).values():
                    if res:
                        hit = by_key.get(str(res).lower())
                        if hit is not None and hit not in out:
                            stack.append(hit)
    return out


def _obj_materials(obj) -> list:
    # blender material names on one object; [] when deleted or material-less
    try:
        mats = obj.data.materials
    except (AttributeError, ReferenceError):
        return []
    names = []
    for mat in mats or []:
        try:
            if mat is not None and getattr(mat, "name", None):
                names.append(mat.name)
        except ReferenceError:
            continue
    return names


def _tint_by_material(item: dict) -> dict:
    # {MAO MaterialObject name: that material's ten tint colours}
    wanted = {group_name(side) for side in SIDES}
    by_material = {}
    for obj in item.get("meshes") or []:
        for mat in obj.data.materials if obj.type == "MESH" else ():
            tree = getattr(mat, "node_tree", None)
            if tree is None:
                continue
            values = {}
            for node in tree.nodes:
                if node.type == "GROUP" and node.node_tree.name in wanted:
                    values.update(_group_tint(node))
            if values:
                by_material[mat.name] = values
    return by_material


def _group_tint(node) -> dict:
    # the tnt values one DAO Tint group carries, splitting no socket in half
    values = {}
    for name in NAMES:
        socket = node.inputs.get(name)
        if socket is None:
            continue
        colour = tuple(float(v) for v in socket.default_value)
        alpha = node.inputs.get(alpha_input(name) or "")
        if alpha is not None:
            colour = colour[:3] + (float(alpha.default_value),)
        values[name] = colour
    return values


TINT_ARRAY = "mml_vTintMaskColours"


def _tint_extra(parsed: dict, tint: dict | None) -> list | None:
    # parsed MAO 'extra' children with the tint array set, or None to leave it
    if not tint:
        return None

    flat = " ".join(f"{v:.6g}" for name in NAMES for v in tint[name])
    extra = [
        (tag, attrs)
        for tag, attrs in (parsed.get("extra") or ())
        if attrs.get("Name") != TINT_ARRAY
    ]
    extra.append(("Vector4fArray", {"Name": TINT_ARRAY, "value": flat}))
    return extra


# kind -> re-encode hook: return None when untouched, new bytes when edited
# mao: material edits (texture swaps, vector tweaks) re-encode via write_mao
_DIRTY: dict = {}


def _mao_bytes(item: dict, tint: dict | None = None, renames: dict | None = None) -> bytes | None:
    # re-encode MAO when vectors, texture links or the tint were edited
    parsed = item.get("model")
    if not isinstance(parsed, dict):
        return None
    vectors = parsed.get("vectors") or {}
    textures = parsed.get("textures") or {}
    extra = _tint_extra(parsed, tint)
    roles = slot_roles(textures)

    for mat in item.get("objects") or []:
        try:
            state = _mao_mat_state(mat, vectors)
            live = _live_textures(mat)
        except ReferenceError:
            continue  # material deleted; leave raw bytes
        if state is None:
            continue
        swaps = {}
        for semantic, res in textures.items():
            role = roles.get(semantic)
            if role is None or role not in live or live[role] == res:
                continue
            swaps[semantic] = live[role]
        if not state["drift"] and not swaps and not extra:
            continue
        live_parsed = dict(parsed)
        live_parsed["vectors"] = state["vectors"]
        live_parsed["textures"] = {**textures, **swaps}
        if extra is not None:
            live_parsed["extra"] = extra
        return write_mao(live_parsed).encode("utf-8")

    if extra is not None:
        return write_mao({**parsed, "extra": extra}).encode("utf-8")
    return None


def _mao_mat_state(mat, vectors: dict) -> dict | None:
    # custom-prop vectors vs parsed; None when a prop is missing/not a list
    current = {}
    for key in vectors:
        cur = mat.get(key)
        if cur is None or isinstance(cur, (str, bytes)):
            return None
        try:
            cur = list(cur)
        except TypeError:
            return None
        current[key] = cur

    drift = any(current[k] != list(v) for k, v in vectors.items())
    return {"vectors": current, "drift": drift}


def _live_textures(mat) -> dict:
    # role -> image name for every TexImage node carrying an image
    found: dict = {}
    try:
        nodes = mat.node_tree.nodes
    except (AttributeError, ReferenceError):
        return found

    for node in nodes:
        name = _tex_image_name(node)
        if name is None:
            continue

        role = role_of(name)
        if role is not None:
            found.setdefault(role, name)
    return found


def _tex_image_name(node) -> str | None:
    # image name of a TexImage node; None when missing or dead
    try:
        if node.type != "TEX_IMAGE" or node.image is None:
            return None
        return node.image.name or None
    except (AttributeError, ReferenceError):
        return None


def _texture_bytes(
    item: dict, tint: dict | None = None, renames: dict | None = None
) -> bytes | None:
    # re-encode only when Blender pixels differ from the decoded source
    if not item.get("objects"):
        return None  # no live image to compare; the source bytes stand
    img = item["objects"][-1]  # latest import wins when a name repeats
    pil = Image.open(io.BytesIO(item["raw"]))
    pil.load()
    role = role_of(item["file"])
    width, height = (int(img.size[0]), int(img.size[1]))
    got = np.empty(width * height * 4, dtype=np.float32)
    img.pixels.foreach_get(got)

    if tuple(pil.size) == (width, height):
        source = thedas_io_normals_to_rgba(pil) if role == "normal" else pil.convert("RGBA")
        want = np.asarray(source).astype(np.float32).reshape(-1) / 255.0
        if want.shape == got.shape and np.allclose(want, got, atol=0.5 / 255.0):
            return None

    _warn_size(item["file"], width, height)
    edited = Image.fromarray((got.reshape(height, width, 4) * 255.0 + 0.5).astype(np.uint8))
    return _encode_texture(item["raw"], edited, (width, height), role)


def _warn_size(name, width: int, height: int) -> None:
    # warn-only oversize notice: export proceeds regardless
    biggest = max(width, height)
    if width & (width - 1) or height & (height - 1):
        print(f"DAO: {name}: {width}x{height} is not power-of-two")
    if biggest > 4096:
        print(f"DAO: {name}: {width}x{height} exceeds the 4096 build cap")


def _encode_texture(raw: bytes, pil, size, role: str | None) -> bytes:
    # source-compressed -> nvtt re-compress with matching format; else Pillow
    fmt, _ = _dds_format(raw)
    image = _swizzle_for_encode(_blender_image(pil, size), role)
    if getattr(image, "format", None) is None:
        image.format = "PNG"

    if fmt is None:
        buf = io.BytesIO()
        image.save(buf, "TGA")  # not DDS at all (a .tga source)
        return buf.getvalue()
    if fmt == "UNCOMPRESSED":
        buf = io.BytesIO()
        image.save(buf, "DDS")
        return buf.getvalue()
    if not hasattr(Format, fmt):
        raise ValueError(f"no encoder for DDS format {fmt}")

    surf = Surface(image)
    surf.normal_map = role == "normal"
    co = CompressionOptions()
    co.format(getattr(Format, fmt))
    co.quality(Quality.Normal)
    with tempfile.NamedTemporaryFile(suffix=".dds", delete=False) as f:
        out_path = f.name
    try:
        oo = OutputOptions()
        oo.filename(out_path)
        Context().compress_all(surf, co, oo)
        del oo  # releases the nvtt file handle before read (Windows sharing)
        return Path(out_path).read_bytes()
    finally:
        Path(out_path).unlink(missing_ok=True)


def _blender_image(pil, size):
    # pillow image resampled to the Blender image size (crop/extend match)
    if pil.size == tuple(size):
        return pil.convert("RGBA")
    return resize_rgba(pil.convert("RGBA"), tuple(size))


def _swizzle_for_encode(image, role: str | None):
    # kept for compatibility; the implementation lives in materials
    return swizzle_for_encode(image, role)


def _dds_format(raw: bytes) -> tuple[str | None, bool]:
    # (fourcc family, has alpha) from a DDS header; None when not DDS
    if len(raw) < 128 or raw[:4] != b"DDS ":
        return None, False
    pf_flags = struct.unpack_from("<I", raw, 80)[0]
    fourcc = raw[84:88]
    if fourcc in (b"DXT1", b"DXT3", b"DXT5"):
        return fourcc.decode(), fourcc != b"DXT1" or bool(pf_flags & 0x1)
    if pf_flags & 0x40:  # DDPF_RGB: uncompressed
        return "UNCOMPRESSED", bool(pf_flags & 0x1)
    return fourcc.decode("ascii", "replace").strip("\x00") or "UNKNOWN", False


_DIRTY["texture"] = _texture_bytes

_DIRTY["mao"] = _mao_bytes


class _TopologyChanged(ValueError):
    # blender topology diverged past surgical patching; rebuild the chunk
    pass


def _msh_bytes(item: dict, tint: dict | None = None, renames: dict | None = None) -> bytes | None:
    # re-encode when Blender geometry diverges from the decoded source
    mesh = item["model"]
    states = [
        (obj, chunk, decode_chunk(mesh, chunk), _blender_state(obj))
        for obj, chunk in item["objects"]
    ]
    planned = []
    for obj, chunk, decoded, bstate in states:
        try:
            state = _expand_rows(decoded, obj, bstate)
            planned.append(
                (
                    obj,
                    chunk,
                    decoded,
                    "patch" if _chunk_edited(decoded, state, obj) else "raw",
                    state,
                )
            )
        except _TopologyChanged:
            planned.append((obj, chunk, decoded, "rebuild", bstate))

    if not any(mode != "raw" for _, _, _, mode, _ in planned):
        return None

    base = MSHReader(item["raw"]).mesh  # pristine copy: untouched bytes stay verbatim
    vblob, iblob = bytearray(base.vertex_data), bytearray(base.index_data)  # type: ignore
    base_blob = bytes(vblob)

    for i, (obj, _, decoded, mode, state) in enumerate(planned):
        if mode == "patch":
            _patch_chunk(base.chunks[i], obj, state, decoded, base_blob, vblob, iblob)  # type: ignore

    for i, (obj, _, decoded, mode, state) in enumerate(planned):
        if mode != "rebuild":
            continue
        vreg, ireg, nrows, ncounts, bounds, rmap = _rebuild_chunk(base.chunks[i], obj, state)  # type: ignore
        while len(vblob) % 4:  # appended regions start %4: element offsets stay integral
            vblob.append(0)
        while len(iblob) % 4:
            iblob.append(0)
        bc = base.chunks[i]  # type: ignore
        bc.cells[8006] = len(vblob)
        vblob += vreg + b"\x00" * 16  # 16B tail: stored list counts cover it, like source
        width = 4 if ncounts > 0xFFFF else 2
        bc.cells[8009] = len(iblob) // width
        iblob += ireg + b"\x00" * 16
        while len(iblob) % 4:
            iblob.append(0)
        bc.cells.update({8001: nrows, 8002: ncounts, 8007: 0, 8008: nrows})
        bc.bounds = bounds
        try:
            obj["thedas_io_row_to_vert"] = rmap
        except (AttributeError, ReferenceError, TypeError):
            pass  # mapless blends still export; they just rebuild again next time

    base.vertex_data, base.index_data = list(vblob), list(iblob)  # type: ignore
    return MSHWriter(base).write()


def _evaluated_mesh(obj):
    # mesh as the depsgraph sees it (armature pose, modifiers, sculpt)
    try:
        ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    except (AttributeError, ReferenceError, RuntimeError):
        return obj.data
    if len(ev.data.vertices) or not len(obj.data.vertices):
        return ev.data
    return None


def _blender_state(obj) -> dict:
    # current positions/triangles/uvs/normals/weights read back from Blender
    data = _evaluated_mesh(obj)
    if data is None:
        raise ValueError(f"{obj.name}: in Edit Mode — leave Edit Mode, then export")
    n = len(data.vertices)
    loops = [loop.vertex_index for loop in data.loops]

    def per_vertex(read) -> list:
        first: dict = {}
        for i, lv in enumerate(loops):
            first.setdefault(lv, read(i))
        return [first.get(v) for v in range(n)]

    corners = getattr(data, "corner_normals", None)
    normals = None
    if corners is not None and len(corners) == len(loops):
        normals = per_vertex(lambda i: tuple(corners[i].vector)[:3])
    bone_map = dict(obj.get("thedas_io_bone_index") or {})
    rename_map = _bone_rename_map(obj, bone_map)
    weights: list = [{} for _ in range(n)]
    for v in data.vertices:
        for g in v.groups:
            name = obj.vertex_groups[g.group].name
            bone = _bone_index(name, bone_map, rename_map)
            if bone is None:
                raise ValueError(f"{obj.name}: vertex group {name!r} has no bone index")
            weights[v.index][bone] = g.weight

    return {
        "positions": [tuple(v.co)[:3] for v in data.vertices],
        "triangles": [tuple(p.vertices) for p in data.polygons],
        "uvs": [
            per_vertex(lambda i, layer=layer: tuple(layer.data[i].uv)) for layer in data.uv_layers
        ],
        "normals": normals,
        "weights": weights,
    }


def _row_map(obj, n_rows: int, n_verts: int):
    # MSH-row -> Blender-vert indices, or None for the legacy layout
    try:
        raw = obj.get("thedas_io_row_to_vert")
    except (AttributeError, ReferenceError):
        raw = None
    if raw is None:
        if n_verts != n_rows:
            raise _TopologyChanged(f"{obj.name}: vertex count changed")
        return None

    m = list(raw)
    if len(m) != n_rows or any(v < 0 or v >= n_verts for v in m):
        raise _TopologyChanged(f"{obj.name}: weld map stale")
    return m


def _expand_rows(decoded: dict, obj, bstate: dict) -> dict:
    # expand Blender-indexed state back to MSH-row indexing
    n = decoded["vert_count"]
    m = _row_map(obj, n, len(bstate["positions"]))
    if m is None:
        return bstate

    dtris = decoded["triangles"]
    btris = bstate["triangles"]
    if len(btris) != len(dtris):
        raise _TopologyChanged(f"{obj.name}: face count changed")
    for t, bt in zip(dtris, btris):
        if tuple(m[r] for r in t) != tuple(bt):
            raise _TopologyChanged(f"{obj.name}: face topology changed")

    data = _evaluated_mesh(obj)
    if data is None:
        raise ValueError(f"{obj.name}: in Edit Mode — leave Edit Mode, then export")

    try:
        have_layers = len(data.uv_layers)
    except (AttributeError, ReferenceError):
        have_layers = 0
    if have_layers < len(decoded["uvs"]):
        # a removed UV channel has no source bytes to keep; the rebuild
        # below re-raises this as the "UV layer N removed" error
        raise _TopologyChanged(f"{obj.name}: UV layer count changed")

    loops = data.loops
    polys = data.polygons
    row_loop: dict = {}
    for i, poly in enumerate(polys):
        base = poly.loop_start
        for j, r in enumerate(dtris[i]):
            row_loop.setdefault(r, base + j)

    expanded_uvs = []
    for layer_i, (_, dcoords) in enumerate(sorted(decoded["uvs"].items())):
        layer = data.uv_layers[layer_i]
        coords = []
        for r in range(n):
            li = row_loop.get(r)
            coords.append(tuple(layer.data[li].uv) if li is not None else dcoords[r])
        expanded_uvs.append(coords)

    corners = getattr(data, "corner_normals", None)
    normals = None
    if corners is not None and len(corners) == len(loops) and decoded["normals"]:
        dnorms = decoded["normals"]
        out = []
        for r in range(n):
            li = row_loop.get(r)
            if li is not None:
                out.append(tuple(corners[li].vector)[:3])
            else:
                out.append(dnorms[r] if r < len(dnorms) else (0.0, 0.0, 0.0))
        normals = out

    return {
        "positions": [bstate["positions"][m[r]] for r in range(n)],
        "triangles": [tuple(t) for t in dtris],
        "uvs": expanded_uvs,
        "normals": normals,
        "weights": [bstate["weights"][m[r]] for r in range(n)],
        "tangent_loops": _loop_tangents(data, row_loop),
    }


def _loop_tangents(data, row_loop: dict) -> dict | None:
    # per-row (tangent xyz, binormal xyz) via Blender's MikkTSpace
    try:
        layers = data.uv_layers
    except (AttributeError, ReferenceError):
        return None
    if not len(layers):
        return None
    try:
        work = data.copy()
    except (AttributeError, ImportError, RuntimeError):
        return None
    try:
        for layer in work.uv_layers:
            for loop in work.loops:
                u, v = layer.data[loop.index].uv
                layer.data[loop.index].uv = (u, 1.0 - v)
        work.calc_tangents(uvmap=work.uv_layers[0].name)
        return {
            r: (tuple(work.loops[li].tangent)[:3], tuple(work.loops[li].bitangent)[:3])
            for r, li in row_loop.items()
        }
    except (AttributeError, ReferenceError, IndexError, TypeError, ValueError, RuntimeError):
        return None
    finally:
        try:
            bpy.data.meshes.remove(work)
        except (AttributeError, ReferenceError, RuntimeError, ValueError, NameError):
            pass


def _bone_rename_map(obj, bone_map: dict) -> dict:
    # current bone name -> import bone index, via the pose-bone thedas_io_node tags
    try:
        mods = list(obj.modifiers)
    except (AttributeError, ReferenceError):
        return {}
    arm = next(
        (m.object for m in mods if getattr(m, "type", "") == "ARMATURE" and m.object is not None),
        None,
    )
    if arm is None:
        return {}

    try:
        pose_bones = list(arm.pose.bones)
    except (AttributeError, ReferenceError):
        return {}

    out = {}
    for pb in pose_bones:
        try:
            tag = pb.get("thedas_io_node")
        except ReferenceError:
            continue
        if tag in bone_map:
            out[pb.name] = bone_map[tag]
    return out


def _bone_index(name: str, bone_map: dict, rename_map: dict):
    if name.startswith("bone_"):
        try:
            return int(name[5:])
        except ValueError:
            return None
    if name in bone_map:
        return bone_map[name]
    return rename_map.get(name)


def _chunk_edited(decoded: dict, state: dict, obj) -> bool:
    n = decoded["vert_count"]
    if len(state["positions"]) != n:
        raise _TopologyChanged(f"{obj.name}: vertex count changed")
    if len(state["triangles"]) != len(decoded["triangles"]):
        raise _TopologyChanged(f"{obj.name}: face count changed")
    if len(state["uvs"]) != len(decoded["uvs"]):
        return True
    if any(_drift(a, b) for a, b in zip(state["positions"], decoded["positions"])):
        return True
    if any(len(t) != 3 for t in state["triangles"]):
        raise _TopologyChanged(f"{obj.name}: non-tri faces")
    if state["triangles"] != decoded["triangles"]:
        return True
    for layer, (_, coords) in enumerate(sorted(decoded["uvs"].items())):
        if any(_drift(a, b) for a, b in zip(state["uvs"][layer], coords)):
            return True
    if (
        state["normals"] is not None
        and decoded["normals"]
        and any(_normal_drift(a, b) for a, b in zip(state["normals"], decoded["normals"]))
    ):
        return True

    return _weights_edited(decoded, state)


def _normal_drift(cur, want, tol: float = 0.02) -> bool:
    # blender can't hold a zero normal (it substitutes its own) and stores
    # near-unit normals a hair off
    return bool(any(want)) and _drift(cur, want, tol)


def _weights_edited(decoded: dict, state: dict) -> bool:
    orig = _group_weights(decoded["groups"], decoded["vert_count"])
    for cur, want in zip(state["weights"], orig):
        if cur.keys() != want.keys() or any(abs(cur[b] - want[b]) > 1e-3 for b in cur):
            return True
    return False


def _group_weights(groups: dict, n: int) -> list:
    out: list = [{} for _ in range(n)]
    for bone, members in groups.items():
        for v, w in members:
            out[v][bone] = w
    return out


def _drift(a, b, tol: float = 1e-6) -> bool:
    return any(abs(x - y) > tol for x, y in zip(a, b))


def _patch_chunk(chunk, obj, state, decoded, base_blob, vblob, iblob):
    # overwrite editable fields, indices and bounds in a copy of the source blobs
    verts = chunk.cells.get(8001, 0)
    count = chunk.cells.get(8002, 0)
    width = 4 if count > 0xFFFF else 2
    vbase = chunk.cells.get(8006, 0)
    ibase = chunk.cells.get(8009, 0) * width
    weights = state["weights"]  # live mesh weights: the decoded ones would undo a repaint
    skin_untouched = not _weights_edited(decoded, state)
    reframed = set()

    for d in chunk.declarators:
        if d.is_terminator or d.usage in (_USAGE_TANGENT, _USAGE_BINORMAL):
            continue
        if d.usage in (_USAGE_WEIGHT, _USAGE_INDICES) and skin_untouched:
            continue
        fmt = FORMATS.get(d.dtype)
        if fmt is None:
            raise ValueError(f"{obj.name}: no encoder for declarator type {d.dtype}")
        for v in range(verts):
            start = vbase + v * chunk.stride + d.offset
            vals, _ = _decode_row(d.dtype, base_blob, start)
            new = _encode_row(d, state, weights, vals, v, obj)
            if new is None:
                continue
            blob = _pack(fmt, (new + list(vals))[: len(vals)], d.usage)
            vblob[start : start + len(blob)] = blob
            if d.usage == _USAGE_NORMAL or (
                d.usage == _USAGE_POSITION and _drift(state["positions"][v], vals[:3])
            ):
                reframed.add(v)
    _patch_tangents(chunk, obj, state, base_blob, vblob, reframed)
    ifmt = "I" if width == 4 else "H"
    for t, tri in enumerate(state["triangles"]):
        off = ibase + t * 3 * width
        iblob[off : off + 3 * width] = struct.pack(f"<{ifmt * 3}", *tri)

    chunk.bounds = _rebounds(state["positions"], chunk.bounds)


def _patch_tangents(chunk, obj, state, base_blob, vblob, reframed: set) -> None:
    # refresh tangent/binormal rows whose frame moved, keep the rest
    if not reframed:
        return
    tloops = state.get("tangent_loops") or {}
    decls = [
        d
        for d in chunk.declarators
        if not d.is_terminator and d.usage in (_USAGE_TANGENT, _USAGE_BINORMAL)
    ]
    if not decls:
        return

    vbase = chunk.cells.get(8006, 0)
    for d in decls:
        fmt = FORMATS.get(d.dtype)
        if fmt is None:
            raise ValueError(f"{obj.name}: no encoder for declarator type {d.dtype}")

        for v in sorted(reframed):
            entry = tloops.get(v)
            if entry is None:
                continue
            t, b = entry
            new = [t[0], t[1], t[2], 1.0] if d.usage == _USAGE_TANGENT else [b[0], b[1], b[2], 1.0]
            start = vbase + v * chunk.stride + d.offset
            vals, _ = _decode_row(d.dtype, base_blob, start)
            blob = _pack(fmt, (new + list(vals))[: len(vals)], d.usage)
            vblob[start : start + len(blob)] = blob


def _rebuild_chunk(chunk, obj, bstate):
    # fresh vertex/index regions after a topology change (subdivide, quads)
    data = _evaluated_mesh(obj)
    if data is None:
        raise ValueError(f"{obj.name}: in Edit Mode — leave Edit Mode, then export")
    ndecl_uv = sum(
        1 for d in chunk.declarators if not d.is_terminator and d.usage == _USAGE_TEXCOORD
    )
    try:
        have_layers = len(data.uv_layers)
    except (AttributeError, ReferenceError):
        have_layers = 0
    if have_layers < ndecl_uv:
        raise ValueError(f"{obj.name}: UV layer {have_layers} removed")
    corners = getattr(data, "corner_normals", None)
    if corners is not None and len(corners) != len(data.loops):
        corners = None
    faces = [
        [(v, poly.loop_start + k) for k, v in enumerate(poly.vertices)] for poly in data.polygons
    ]
    positions, weights = bstate["positions"], bstate["weights"]
    seen: dict = {}
    rows: list = []
    new_tris: list = []
    new_row_loop: dict = {}
    for a, b, c in triangulate_loops(faces):
        tri = []
        for v, li in (a, b, c):
            uvkey = tuple(tuple(data.uv_layers[i].data[li].uv) for i in range(ndecl_uv))
            nkey = tuple(corners[li].vector)[:3] if corners is not None else None
            r = seen.get((v, uvkey, nkey))
            if r is None:
                r = len(rows)
                seen[(v, uvkey, nkey)] = r
                rows.append((v, li))
                new_row_loop[r] = li
            tri.append(r)
        new_tris.append(tuple(tri))

    tloops = _loop_tangents(data, new_row_loop)
    enc = []
    for d in chunk.declarators:
        if d.is_terminator:
            continue
        fmt = FORMATS.get(d.dtype)
        if fmt is None:
            raise ValueError(f"{obj.name}: no encoder for declarator type {d.dtype}")
        enc.append((d, fmt, len(struct.unpack(fmt, bytes(struct.calcsize(fmt))))))
    vreg = bytearray()
    for ri, (v, li) in enumerate(rows):
        rec = {
            "pos": positions[v],
            "weights": weights[v],
            "uvs": [tuple(data.uv_layers[i].data[li].uv) for i in range(ndecl_uv)],
            "normal": (tuple(corners[li].vector)[:3] if corners is not None else None),
        }
        row = bytearray(chunk.stride)
        for d, fmt, ncomp in enc:
            vals = _encode_fresh(d, rec, tloops, ri, obj)
            if vals is None:
                continue
            blob = _pack(fmt, (vals + [0.0] * ncomp)[:ncomp], d.usage)
            row[d.offset : d.offset + len(blob)] = blob
        vreg += row

    ncounts = 3 * len(new_tris)
    width = "I" if ncounts > 0xFFFF else "H"
    ireg = struct.pack(f"<{width * ncounts}", *[i for t in new_tris for i in t])
    return (
        bytes(vreg),
        ireg,
        len(rows),
        ncounts,
        _rebounds([positions[v] for v, _ in rows], chunk.bounds),
        [v for v, _ in rows],
    )


def _encode_fresh(d, rec, tloops, ri, obj):
    # live values for one fresh row; None keeps the zeroed bytes
    if d.usage == _USAGE_POSITION:
        return list(rec["pos"]) + [1.0]
    if d.usage == _USAGE_NORMAL:
        base = list(rec["normal"]) if rec["normal"] is not None else [0.0, 0.0, 0.0]
        return base + [1.0]
    if d.usage == _USAGE_TEXCOORD:
        if d.usage_index >= len(rec["uvs"]):
            raise ValueError(f"{obj.name}: UV layer {d.usage_index} removed")
        u, w = rec["uvs"][d.usage_index]
        return [u, 1.0 - w]  # import flips V
    if d.usage == _USAGE_WEIGHT:
        return [w for _, w in _skin(rec["weights"])]
    if d.usage == _USAGE_INDICES:
        return [b for b, _ in _skin(rec["weights"])]
    if d.usage in (_USAGE_TANGENT, _USAGE_BINORMAL):
        entry = (tloops or {}).get(ri)
        if entry is None:
            return None
        v3 = entry[0] if d.usage == _USAGE_TANGENT else entry[1]
        return [v3[0], v3[1], v3[2], 1.0]
    return None


def _encode_row(d, state, weights, vals, v, obj):
    if d.usage == _USAGE_POSITION:
        return list(state["positions"][v]) + list(vals[3:])
    if d.usage == _USAGE_NORMAL:
        # rewrite only real normal edits: skip Blender's storage noise and
        # source-zero normals (Blender substituted its own there)
        if state["normals"] is None or not any(vals[:3]):
            return None
        if not _drift(state["normals"][v], vals[:3], 2e-3):
            return None
        return list(state["normals"][v]) + list(vals[3:])
    if d.usage == _USAGE_TEXCOORD:
        if d.usage_index >= len(state["uvs"]):
            raise ValueError(f"{obj.name}: UV layer {d.usage_index} removed")
        u, w = state["uvs"][d.usage_index][v]
        return [u, 1.0 - w]  # import flips V
    if d.usage == _USAGE_WEIGHT:
        return [w for _, w in _skin(weights[v])] + [0.0, 0.0, 0.0, 0.0]
    if d.usage == _USAGE_INDICES:
        return [b for b, _ in _skin(weights[v])] + [0, 0, 0, 0]
    return None


def _skin(vertex_weights: dict) -> list:
    # top-4 (bone, weight) desc, normalized: decode_chunk divides by the stored
    # sum, so writing an unnormalized repaint (0.9 on top of 0.5 + 0.1) would inflate
    # the pose
    pairs = sorted(vertex_weights.items(), key=lambda kv: -kv[1])[:4]
    total = sum(w for _, w in pairs) or 1.0
    return [(b, round(float(w / total), 3)) for b, w in pairs]


def _pack(fmt: str, values, usage: int) -> bytes:
    if "f" in fmt or "e" in fmt:
        return struct.pack(fmt, *(float(v) for v in values))
    scale = (255 if "B" in fmt else 32767) if usage == _USAGE_WEIGHT else 1
    return struct.pack(fmt, *(round(float(v) * scale) for v in values))


def _rebounds(positions, bounds):
    # AABB min/max (w kept) + bounding sphere: center of box, half diagonal
    if bounds is None or not positions:
        return bounds
    mn = [min(p[i] for p in positions) for i in range(3)]
    mx = [max(p[i] for p in positions) for i in range(3)]
    half = [(mx[i] - mn[i]) / 2.0 for i in range(3)]
    return {
        8017: (*mn, bounds[8017][3]),
        8018: (*mx, bounds[8018][3]),
        8019: (
            mn[0] + half[0],
            mn[1] + half[1],
            mn[2] + half[2],
            math.sqrt(sum(h * h for h in half)),
        ),
    }


_DIRTY["msh"] = _msh_bytes


def _mmh_bytes(item: dict, tint: dict | None = None, renames: dict | None = None) -> bytes | None:
    # patch node names/transforms, plus the tint flag, when Blender diverges
    tree, hosts = item["model"], item.get("hosts") or {}
    rows = list(node_paths(tree))
    node_at = {path: node for node, path in rows}
    base_world = {id(n): (loc, q) for n, _, loc, q in flatten(tree)}
    tag_maps: dict = {}
    edits: dict = {}

    for path, (kind, resolver, snap) in hosts.items():
        node = node_at.get(path)
        if node is None:
            continue
        try:
            if kind == "bone":
                live_name, live = _live_bone(tag_maps, resolver)
            else:
                live = resolver.matrix_world
                live_name = resolver.name
        except ReferenceError as exc:
            raise ValueError(f"node {node.name or path}: deleted in Blender") from exc
        world = _live_world(live, snap, base_world[id(node)])
        name = live_name if node.name is not None and live_name != node.name else None
        if world is not None or name is not None:
            edits[path] = (world, name)

    gff = GFFReader(item["raw"])
    # from_reader, not from_bytes: that builds a second GFFReader over the
    # same bytes, and an mmh's data-block copy is not small
    writer = GFFWriter.from_reader(gff)
    patched = _use_variation_tint(gff, writer) if tint else False
    renamed = _patch_msh_ref(gff, writer, item, renames or {}) if renames else False
    if not edits and not patched and not renamed:
        return None

    bases = {path: (si, base) for path, si, base in _gff_paths(gff, 0, 0)}
    # worlds by path: rows is flat pre-order, so a running parent world
    # would wrongly absorb sibling trsl/rota leaves; look up the parent
    out_world: dict = {}
    for node, path in rows:
        pw = out_world[path[:-1]] if path else ((0.0, 0.0, 0.0), IDENTITY_QUAT)
        src = node_local(node.values)
        edit = edits.get(path)
        if edit is None:
            out_world[path] = compose(pw, src)
            continue

        world, new_name = edit
        local = src
        if world is not None:
            candidate = _inverse_compose(pw, world)
            if _drift(candidate[0], src[0], 1e-5) or _drift(candidate[1], src[1], 1e-5):
                base = bases.get(path)
                if base is None:
                    raise ValueError(f"node {node.name or path}: lost in source walk")
                _patch_node_xf(gff, writer, base, node, candidate)
                patched = True
                local = candidate
        if new_name is not None:
            base = bases.get(path)
            if base is None:
                raise ValueError(f"node {node.name or path}: lost in source walk")
            _patch_node_name(gff, writer, base, new_name, node.name)
            patched = True
        out_world[path] = compose(pw, local)
    return writer.write() if (patched or renamed) else None


def _live_bone(tag_maps: dict, resolver):
    # current (name, rest matrix) of an import-tagged bone; rename-proof
    arm_obj, tag = resolver
    key = id(arm_obj)
    names = tag_maps.get(key)
    if names is None:
        names = {
            pb.get("thedas_io_node"): pb.name
            for pb in arm_obj.pose.bones
            if pb.get("thedas_io_node") is not None
        }
        tag_maps[key] = names

    current = names.get(tag)
    bone = None if current is None else arm_obj.data.bones.get(current)
    if bone is None:
        raise ValueError(f"bone {tag}: deleted in Blender")
    return bone.name, bone.matrix_local


def _gff_paths(gff, struct_index: int, base: int, path=()):
    # parallel walk of the raw GFF: (path, struct index, instance base)
    yield path, struct_index, base
    i = 0

    for entry in _children(gff, struct_index, base):
        if entry is None or entry.get("value") is None:
            continue  # NULL_REF: MMHReader._node skips these too
        yield from _gff_paths(gff, entry["struct_index"], entry["offset"], path + (i,))
        i += 1


def _children(gff, struct_index: int, base: int) -> list:
    # generic CHILDREN list of one instance; leaf structs have none
    if _field(gff, struct_index, 6999) is None:
        return []
    return gff.get_from_data_block(struct_index, base, 6999) or []


def _field(gff, struct_index: int, label: int):
    for field in gff.struct_defs[struct_index].field_instances:
        if field.label_id == label:
            return field
    return None


def _patch_msh_ref(gff, writer, item: dict, renames: dict) -> bool:
    # repoint the hierarchy at its renamed .msh; False when nothing to do
    field = _field(gff, 0, 6005)
    if field is None or field.flags != 0 or field.type_id != 14:
        return False
    try:
        current = gff.get_from_data_block(0, 0, 6005)
    except (ValueError, struct.error):
        return False
    if not current:
        return False
    target = renames.get(str(current).lower())
    if not target or target == current:
        return False

    offset = writer.alloc_ecstring(target)
    struct.pack_into("<I", writer.data_block, 0 + field.data_index, offset)
    return True


def _use_variation_tint(gff, writer) -> bool:
    # clear 6340 USE_VARIATION_TINT on every mesh node
    fourcc = int.from_bytes(b"mshh", "little")
    changed = False
    for _, struct_index, base in _gff_paths(gff, 0, 0):
        if gff.struct_defs[struct_index].type_fourcc != fourcc:
            continue
        field = _field(gff, struct_index, 6340)
        if field is None:
            continue  # already 0 by default
        if gff.decode_scalar(field, base):
            struct.pack_into("<B", writer.data_block, base + field.data_index, 0)
            changed = True
    return changed


def _patch_node_name(gff, writer, base, new_name: str, label: str) -> None:
    struct_index, instance = base
    field = _field(gff, struct_index, 6000)
    if field is None or field.flags & 0xE000:
        raise ValueError(f"node {label}: no inline name field")
    offset = writer.alloc_ecstring(new_name)
    struct.pack_into("<I", writer.data_block, instance + field.data_index, offset)


def _patch_node_xf(gff, writer, base, node, local) -> None:
    struct_index, instance = base
    entries = _children(gff, struct_index, instance)
    label = node.name or "?"
    for kind, field_label, values, keep_w in (
        ("trsl", 6047, local[0], True),
        ("rota", 6048, local[1], False),
    ):
        fourcc = int.from_bytes(kind.encode("ascii"), "little")
        entry = next(
            (
                e
                for e in entries
                if e.get("value") is not None
                and gff.struct_defs[e["struct_index"]].type_fourcc == fourcc
            ),
            None,
        )
        if entry is None:
            raise ValueError(f"node {label}: no {kind} struct to patch")
        field = _field(gff, entry["struct_index"], field_label)
        if field is None or field.flags & 0xE000 or field.type_id not in (12, 13):
            raise ValueError(f"node {label}: no inline {field_label} field")
        pack = [float(v) for v in values]
        if keep_w:
            source = node.values.get(field_label) or (0.0, 0.0, 0.0, 1.0)
            pack.append(float(source[3]))
        struct.pack_into("<4f", writer.data_block, entry["offset"] + field.data_index, *pack)


def _inverse_compose(parent, world):
    # local such that compose(parent, local) == world (legacy MSH_Tool rule)
    (ploc, prot), (wloc, wrot) = parent, world
    conj = (-prot[0], -prot[1], -prot[2], prot[3])
    delta = (wloc[0] - ploc[0], wloc[1] - ploc[1], wloc[2] - ploc[2])
    return quat_rotate(conj, delta), quat_mul(wrot, conj)


def _mat_state(matrix) -> tuple:
    # blender matrix -> (xyz, xyzw quat) in geometry.py convention
    t, q = matrix.translation, matrix.to_quaternion()
    return (float(t[0]), float(t[1]), float(t[2])), (q.x, q.y, q.z, q.w)


def _live_world(live, snap, base) -> tuple | None:
    # edited world (loc, xyzw quat), or None when the rest matrix is untouched
    live_loc, live_q = _mat_state(live)
    snap_loc, snap_q = _mat_state(snap)
    if not (_drift(live_loc, snap_loc, 1e-4) or _drift(live_q, snap_q, 1e-4)):
        return None
    conj = (-snap_q[0], -snap_q[1], -snap_q[2], snap_q[3])
    return live_loc, quat_mul(quat_mul(live_q, conj), base[1])


_DIRTY["mmh"] = _mmh_bytes

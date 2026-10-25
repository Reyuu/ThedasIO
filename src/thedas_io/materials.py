# mao parse/write, texture roles, dao normal stuff

import re
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from PIL import Image as PilImage

ROLE_BY_SUFFIX = {
    "_d": "diffuse",
    "_n": "normal",
    "_s": "specular",
    "_t": "tint",
    "_h": "height",
    "_e": "emission",
    "_i": "emission",  # mml_tEmissiveMask ships _0i (pn_arm_dwpd_0i.dds)
    "_f": "fresnel",
}

# semantic wins over filename when both claim a slot
ROLE_BY_SEMANTIC = {
    "mml_tDiffuse": "diffuse",
    "mml_tNormalMap": "normal",
    "mml_tSpecularMask": "specular",
    "mml_tEmissiveMask": "emission",
    "mml_tTintMask": "tint",
    "mml_tLightmap": "lightmap",
    "mml_tReliefMapPalette": "height",
    "mml_tHeightMap": "height",
}


def semantic_role(semantic):
    if not semantic:
        return None
    return ROLE_BY_SEMANTIC.get(semantic.strip())


def slot_roles(textures):
    # filename first, semantic as fallback so mml_tTintMask
    # doesn't steal tint from mml_tTintNoise
    if not textures:
        textures = {}

    out = {}
    claimed = set()
    for semantic, res in textures.items():
        role = None
        if res:
            role = role_of(res)
        out[semantic] = role
        if role is not None:
            claimed.add(role)

    for semantic, res in textures.items():
        if out[semantic] is None and res:
            fallback = semantic_role(semantic)
            if fallback is None or fallback in claimed:
                out[semantic] = semantic
            else:
                out[semantic] = fallback
                claimed.add(fallback)
    return out


def role_of(filename):
    stem = Path(filename).stem.lower()
    stem = re.sub(r"l[23]$", "", stem)
    stem = re.sub(r"_(\d+)([a-z])$", r"_\2", stem)
    for suffix, role in ROLE_BY_SUFFIX.items():
        if stem.endswith(suffix):
            return role
    return None


_MAO_KNOWN = {"Material", "DefaultSemantic", "Texture", "Vector4f", "Float"}
_MAO_ROOT = "<MaterialObject"


def parse_mao(text):
    # extra/header kept verbatim so re-encode drops nothing
    root = ET.fromstring(text)
    material = root.find("Material")
    semantic = root.find("DefaultSemantic")
    at = text.find(_MAO_ROOT)
    textures = {}
    for t in root.findall("Texture"):
        textures[t.get("Name")] = t.get("ResName")

    vectors = {}
    for v in root.findall("Vector4f"):
        vals = []
        for x in (v.get("value") or "").split():
            vals.append(float(x))
        vectors[v.get("Name")] = vals

    floats = {}
    for f in root.findall("Float"):
        floats[f.get("Name")] = float(f.get("value") or 0.0)

    extra = []
    for c in root:
        if c.tag not in _MAO_KNOWN:
            extra.append((c.tag, dict(c.attrib)))

    header = ""
    if at > 0:
        header = text[:at]

    if material is not None:
        mat_name = material.get("Name")
    else:
        mat_name = None
    if semantic is not None:
        sem_name = semantic.get("Name")
    else:
        sem_name = None

    return {
        "name": root.get("Name"),
        "material": mat_name,
        "semantic": sem_name,
        "textures": textures,
        "vectors": vectors,
        "floats": floats,
        "extra": extra,
        "header": header,
    }


# da2 ships maos as gff v4 rather than the dao xml dialect; same fields, so
# both parse into the dict build_material and write_mao already understand.
# root "mao " is 12 bytes: 33012 material, 33013 semantic, then one struct
# array per optional list, in label order. element rows are "int ", "flot",
# "flt4" (32 bytes; the value sits at 0 and the name at 16) and "tex ".
_MAO_GFF_LABELS = {
    "name": 33012,
    "semantic": 33013,
    "ints": 33015,
    "floats": 33016,
    "vectors": 33017,
    "textures": 33020,
}
_MAO_ITEM_LABELS = {
    "int": (33000, 33001),
    "float": (33002, 33003),
    "vector": (33004, 33005),
    "texture": (33010, 33011),
}
# label -> (element fourcc, name label, value label, scalar fmt)
_MAO_BINARY_ARRAYS = {
    33015: (b"int ", 33000, 33001, "<i"),
    33016: (b"flot", 33002, 33003, "<f"),
    33017: (b"flt4", 33004, 33005, "<4f"),
    33020: (b"tex ", 33010, 33011, None),
}


def parse_mao_binary(raw):
    from .gff40.description import GFFReader

    root = GFFReader(raw).get_from_data_block(0)
    int_name, int_value = _MAO_ITEM_LABELS["int"]
    flt_name, flt_value = _MAO_ITEM_LABELS["float"]
    vec_name, vec_value = _MAO_ITEM_LABELS["vector"]
    tex_name, tex_res = _MAO_ITEM_LABELS["texture"]

    ints = {}
    for item in root.get(_MAO_GFF_LABELS["ints"]) or []:
        if item.get(int_name):
            ints[item[int_name]] = int(item.get(int_value) or 0)
    floats = {}
    for item in root.get(_MAO_GFF_LABELS["floats"]) or []:
        if item.get(flt_name):
            floats[item[flt_name]] = float(item.get(flt_value) or 0.0)
    vectors = {}
    for item in root.get(_MAO_GFF_LABELS["vectors"]) or []:
        if item.get(vec_name):
            vectors[item[vec_name]] = list(item.get(vec_value) or ())
    textures = {}
    for item in root.get(_MAO_GFF_LABELS["textures"]) or []:
        if item.get(tex_name):
            textures[item[tex_name]] = item.get(tex_res)

    name = root.get(_MAO_GFF_LABELS["name"])
    return {
        "name": name,
        "material": name,
        "semantic": root.get(_MAO_GFF_LABELS["semantic"]),
        "textures": textures,
        "vectors": vectors,
        "floats": floats,
        "ints": ints,
        "extra": [],
        "header": "",
        "binary": True,
    }


def parse_mao_bytes(raw: bytes):
    if raw[:8] == b"GFF V4.0":
        return parse_mao_binary(raw)
    try:
        return parse_mao(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise ValueError("MAO is not UTF-8 text") from None
    except ET.ParseError as exc:
        raise ValueError(f"MAO is not valid XML: {exc}") from None


def _mao_field(struct_def, label):
    for field in struct_def.field_instances:
        if field.label_id == label:
            return field
    return None


def _patch_root_string(writer, reader, field, value) -> bool:
    # 33012/33013 are ecstring refs in the root instance
    current = reader.get_from_data_block(0, label_id=field.label_id)
    if value is None or value == current:
        return False
    struct.pack_into("<I", writer.data_block, field.data_index, writer.alloc_ecstring(value))
    return True


def _patch_array(writer, reader, root, field, label, desired) -> bool:
    fourcc, name_label, value_label, scalar = _MAO_BINARY_ARRAYS[label]
    if field.type_id < 0 or field.type_id >= len(reader.struct_defs):
        raise ValueError(f"binary MAO: label {label} points at no struct")
    element = reader.struct_defs[field.type_id]
    if element.type_fourcc.to_bytes(4, "little") != fourcc:
        raise ValueError(f"binary MAO: label {label} is not a {fourcc!r} array")
    name_field = _mao_field(element, name_label)
    value_field = _mao_field(element, value_label)
    if name_field is None or value_field is None:
        raise ValueError(f"binary MAO: {fourcc!r} rows are missing their name/value cells")

    rows = root.get(label) or []
    ref = struct.unpack_from("<I", writer.data_block, field.data_index)[0]
    if ref == 0xFFFFFFFF:
        raise ValueError(f"binary MAO: label {label} names no rows")
    base = ref + 4
    # the shipped files pad some rows ("flt4" is 32 bytes for a 20-byte payload),
    # so row size always comes from the file, never from the label
    size = element.struct_size
    if base + len(rows) * size > len(writer.data_block):
        raise ValueError(f"binary MAO: label {label} rows run past the data block")
    # copy each row whole, so cells this writer does not model survive
    raw_rows = [
        bytearray(writer.data_block[base + i * size : base + (i + 1) * size])
        for i in range(len(rows))
    ]
    names = [row.get(name_label) for row in rows]
    missing = next((name for name in desired if name not in names), None)
    if missing is not None:
        # a new row would need values for the cells we do not model; refuse
        # rather than write zeros over them
        raise ValueError(f"binary MAO: no row named {missing!r} in label {label}")

    changed = False
    for i, row in enumerate(rows):
        value = desired.get(names[i])
        if value is None:
            continue
        if scalar is None:
            if str(value) == str(row.get(value_label)):
                continue
            packed: bytes = struct.pack("<I", writer.alloc_ecstring(str(value)))
        elif label == 33017:
            if len(value) != 4:
                raise ValueError(f"binary MAO: {names[i]!r} is not a Vector4f")
            current = row.get(value_label) or ()
            if len(current) == 4 and all(float(a) == float(b) for a, b in zip(value, current)):
                continue
            packed = struct.pack("<4f", *(float(v) for v in value))
        else:
            if float(value) == float(row.get(value_label) or 0):
                continue
            packed = struct.pack(scalar, value)
        raw_rows[i][value_field.data_index : value_field.data_index + len(packed)] = packed
        changed = True

    if not changed:
        return False
    block = struct.pack("<I", len(raw_rows)) + b"".join(bytes(row) for row in raw_rows)
    struct.pack_into("<I", writer.data_block, field.data_index, writer.alloc(block))
    return True


def write_mao_binary(parsed: dict, raw: bytes) -> bytes:
    # patch a da2 GFF MAO in place; an untouched file comes back byte-identical
    from .gff40.description import FLAGS, GFFReader, GFFWriter

    reader = GFFReader(raw)
    if reader.file_type != b"MAO " or not reader.struct_defs:
        raise ValueError("not a DA2 MAO file")
    root_def = reader.struct_defs[0]
    writer = GFFWriter.from_reader(reader)
    root = reader.get_from_data_block(0)
    changed = False

    for key in ("material", "semantic"):
        label = _MAO_GFF_LABELS["name" if key == "material" else "semantic"]
        field = _mao_field(root_def, label)
        if field is not None:
            changed |= _patch_root_string(writer, reader, field, parsed.get(key))

    for label, key in (
        (33015, "ints"),
        (33016, "floats"),
        (33017, "vectors"),
        (33020, "textures"),
    ):
        field = _mao_field(root_def, label)
        desired = parsed.get(key) or {}
        if field is None:
            if desired:
                raise ValueError(f"binary MAO: this file has no {key} list")
            continue
        if field.flags != FLAGS["STRUCT_ARRAY"]:
            raise ValueError(f"binary MAO: label {label} is not a struct array")
        if desired:
            changed |= _patch_array(writer, reader, root, field, label, desired)

    return writer.write() if changed else bytes(raw)


def write_mao(parsed):
    root = ET.Element("MaterialObject", {"Name": parsed.get("name") or ""})
    ET.SubElement(root, "Material", {"Name": parsed.get("material") or ""})
    ET.SubElement(root, "DefaultSemantic", {"Name": parsed.get("semantic") or ""})
    textures = parsed.get("textures") or {}
    for name in textures:
        res = textures[name] or ""
        ET.SubElement(root, "Texture", {"Name": name, "ResName": res})

    vectors = parsed.get("vectors") or {}
    for name in vectors:
        vals = []
        for v in vectors[name]:
            vals.append(str(v))
        ET.SubElement(root, "Vector4f", {"Name": name, "value": " ".join(vals)})

    floats = parsed.get("floats") or {}
    for name in floats:
        ET.SubElement(root, "Float", {"Name": name, "value": str(floats[name])})

    for tag, attrs in parsed.get("extra") or ():
        ET.SubElement(root, tag, attrs)

    ET.indent(root, space="\t")
    return f"{parsed.get('header') or ''}{ET.tostring(root, encoding='unicode')}"


def swizzle_for_encode(image, role):
    # standard RGBA -> dao layout, non-normals pass through
    if role != "normal":
        return image
    a = np.asarray(image.convert("RGBA")).astype(np.float32) / 255.0
    raw_y = 1.0 - a[:, :, 1]
    raw_x = a[:, :, 0]
    dao = np.stack([raw_y, raw_y, raw_y, raw_x], axis=-1)
    return PilImage.fromarray((dao * 255.0 + 0.5).astype(np.uint8))


def thedas_io_normals_to_rgba(image):
    # dao swizzle -> standard tangent space, green is source of truth
    a = np.asarray(image.convert("RGBA")).astype(np.float32)
    x = a[..., 3] / 255.0 * 2.0 - 1.0
    y = -(a[..., 1] / 255.0 * 2.0 - 1.0)
    z = np.sqrt(np.maximum(0.0, 1.0 - x * x - y * y))
    rgb = np.stack([x, y, z], axis=-1) * 0.5 + 0.5
    rgba = np.concatenate([rgb, np.ones_like(rgb[..., :1])], axis=-1)

    return PilImage.fromarray((rgba * 255.0 + 0.5).astype(np.uint8))


def pil_to_blender(pil) -> np.ndarray:
    """PIL image as the flat float32 array `image.pixels` expects.

    `image.pixels` is row-major from the *bottom*; PIL is row-major from the
    top. Both directions live here so they cannot drift apart -- the two must
    agree exactly or export reads every texture back as edited.
    """
    array = np.asarray(pil.convert("RGBA"))
    return np.ascontiguousarray(array[::-1]).astype(np.float32).reshape(-1) / 255.0


def blender_to_pil(flat, size) -> PilImage.Image:
    """Inverse of `pil_to_blender`: a Blender pixel buffer back to a PIL image."""
    width, height = size
    array = np.asarray(flat, dtype=np.float32).reshape(height, width, 4)[::-1]
    return PilImage.fromarray((array * 255.0 + 0.5).astype(np.uint8))


def resize_rgba(image, size):
    # resize rgb and alpha separately, pillow kills rgb where alpha is zero
    size = tuple(size)
    if tuple(image.size) == size:
        return image
    a = np.asarray(image.convert("RGBA"))
    rgb = np.asarray(PilImage.fromarray(a[..., :3]).resize(size, PilImage.BICUBIC))  # type: ignore
    alpha = np.asarray(PilImage.fromarray(a[..., 3]).resize(size, PilImage.BICUBIC))  # type: ignore
    return PilImage.fromarray(np.concatenate([rgb, alpha[..., None]], -1))

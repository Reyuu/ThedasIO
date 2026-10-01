# mao parse/write, texture roles, dao normal stuff

import re
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


def resize_rgba(image, size):
    # resize rgb and alpha separately, pillow kills rgb where alpha is zero
    size = tuple(size)
    if tuple(image.size) == size:
        return image
    a = np.asarray(image.convert("RGBA"))
    rgb = np.asarray(PilImage.fromarray(a[..., :3]).resize(size, PilImage.BICUBIC))  # type: ignore
    alpha = np.asarray(PilImage.fromarray(a[..., 3]).resize(size, PilImage.BICUBIC))  # type: ignore
    return PilImage.fromarray(np.concatenate([rgb, alpha[..., None]], -1))

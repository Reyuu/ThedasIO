# dao <-> substance painter bits

from pathlib import Path

import numpy as np
from PIL import Image as PilImage

from .materials import resize_rgba, slot_roles, swizzle_for_encode, thedas_io_normals_to_rgba

MAP_BASECOLOR = "basecolor"
MAP_OPACITY = "opacity"
MAP_NORMAL = "normal"
MAP_SPECULAR = "specular"
MAP_GLOSSINESS = "glossiness"
MAP_EMISSIVE = "emissive"
MAP_HEIGHT = "height"

SP_MAPS = {
    MAP_BASECOLOR,
    MAP_OPACITY,
    MAP_NORMAL,
    MAP_SPECULAR,
    MAP_GLOSSINESS,
    MAP_EMISSIVE,
    MAP_HEIGHT,
}


SP_ALIASES = {"diffuse": MAP_BASECOLOR}


def flat_map_target(filename):
    stem = Path(filename).stem
    mat, sep, tail = stem.rpartition("_")
    if sep and mat:
        tail = SP_ALIASES.get(tail.lower(), tail.lower())
        if tail in SP_MAPS:
            return mat, tail
        return None

    bare = SP_ALIASES.get(stem.lower(), stem.lower())
    if bare in SP_MAPS:
        return "", bare
    return None


def attribute_bare(have, mao_names):
    bare = have.pop("", None)
    if not bare:
        return have, []

    names = []
    for n in mao_names:
        if n and n not in names:
            names.append(n)

    if len(names) != 1:
        dropped = []
        for p in bare.values():
            dropped.append(p.name)
        dropped = ", ".join(sorted(dropped))
        return have, [
            f"ambiguous bare maps ({dropped}): several materials imported, use $textureSet_$map names"
        ]

    merged = dict(have)
    maps = dict(bare)
    old = merged.get(names[0], {})
    for k in old:
        maps[k] = old[k]
    merged[names[0]] = maps
    return merged, []


def collect_sp_maps(root):
    root = Path(root)
    out = {}

    if root.is_dir():
        for png in sorted(root.glob("*.png")):
            hit = flat_map_target(png.name)
            if hit is not None:
                if hit[0] not in out:
                    out[hit[0]] = {}
                out[hit[0]][hit[1]] = png

    if root.is_dir():
        dirs = []
        for p in root.iterdir():
            if p.is_dir():
                dirs.append(p)
        for mat_dir in sorted(dirs):
            if mat_dir.name not in out:
                out[mat_dir.name] = {}
            maps = out[mat_dir.name]
            for png in sorted(mat_dir.glob("*.png")):
                if png.stem not in maps:
                    maps[png.stem] = png
    return out


def obj_text(chunks, mtl_name="model.mtl"):
    lines = ["# exported from dao for substance", f"mtllib {mtl_name}"]
    voff = 1
    voff_uv = 1

    for name, mat, decoded in chunks:
        positions = decoded["positions"]
        uvs = (decoded["uvs"] or {}).get(0) or []
        normals = decoded["normals"]
        if not name:
            name = "chunk"
        lines.append(f"o {name}")

        for p in positions:
            lines.append(f"v {p[0]:.6g} {p[1]:.6g} {p[2]:.6g}")
        for u, v in uvs:
            lines.append(f"vt {u:.6g} {v:.6g}")
        for n in normals:
            lines.append(f"vn {n[0]:.6g} {n[1]:.6g} {n[2]:.6g}")
        has_uv = bool(uvs) and len(uvs) == len(positions)
        has_n = bool(normals) and len(normals) == len(positions)
        if not mat:
            mat = name
        lines.append(f"usemtl {mat}")

        for a, b, c in decoded["triangles"]:
            face = []
            for v in (a, b, c):
                f = str(voff + v)
                if has_uv:
                    f += f"/{voff_uv + v}"
                else:
                    f += "/"
                if has_n:
                    f += f"/{voff + v}"
                face.append(f)
            lines.append(f"f {' '.join(face)}")
        voff += len(positions)
        if has_uv:
            voff_uv += len(uvs)
        else:
            voff_uv += len(positions)

    lines.append("")
    return "\n".join(lines)


MTL_MAPS = {
    MAP_BASECOLOR: "map_Kd",
    MAP_OPACITY: "map_d",
    MAP_NORMAL: "map_Bump",
    MAP_SPECULAR: "map_Ks",
    MAP_EMISSIVE: "map_Ke",
}


def mtl_text(materials):
    lines = ["# exported from dao for substance"]

    for mat, stems in materials:
        lines.append(f"newmtl {mat}")
        lines.append("Ka 0.000 0.000 0.000")
        lines.append("Kd 1.000 1.000 1.000")
        lines.append("Ks 0.000 0.000 0.000")
        lines.append("d 1.000")
        lines.append("illum 2")
        for stem in (MAP_BASECOLOR, MAP_OPACITY, MAP_NORMAL, MAP_SPECULAR, MAP_EMISSIVE):
            if stem in stems and stem in MTL_MAPS:
                lines.append(f"{MTL_MAPS[stem]} textures/{mat}/{stem}.png")

    lines.append("")
    return "\n".join(lines)


_TRANSPARENT = ("alpha", "punchthrough", "addvpunchthrough")
_CUTOUT = ("punchthrough", "addvpunchthrough")


def unpack_plan(mao, split_packed=True):
    # slot to sp map, mirrors importer channel rules
    textures = mao.get("textures") or {}
    roles = slot_roles(textures)
    semantic = (mao.get("semantic") or "").lower()
    transparent = semantic.startswith(_TRANSPARENT)
    plan = {}

    for slot, res in textures.items():
        if not res:
            plan[slot] = (None, "empty ResName")
            continue
        role = roles.get(slot)
        if role == "diffuse":
            if transparent:
                plan[slot] = (MAP_BASECOLOR, None)
            elif "pack" in slot.lower():
                if split_packed:
                    plan[slot] = (MAP_BASECOLOR, "packed map split on import")
                else:
                    plan[slot] = (MAP_BASECOLOR, "packed map kept whole")
            else:
                plan[slot] = (MAP_BASECOLOR, None)
            # alpha rides with basecolor, meaning saved for repack
            plan[f"{slot}#alpha"] = ("opacity" if transparent else "specular", None)
        elif role == "normal":
            plan[slot] = (MAP_NORMAL, None)
        elif role == "specular":
            plan[slot] = (MAP_SPECULAR, None)
            plan[f"{slot}#gloss"] = (MAP_GLOSSINESS, None)
        elif role == "tint":
            plan[slot] = (None, "no SP equivalent; passes through untouched")
        elif role == "emission":
            plan[slot] = (MAP_EMISSIVE, None)
        elif role == "height":
            plan[slot] = (MAP_HEIGHT, None)
        elif role == "lightmap":
            plan[slot] = (None, "no SP equivalent; passes through untouched")
        else:
            plan[slot] = (None, f"unmodelled slot {slot}")
    return plan


def unpack_images(mao, images, split_packed=True):
    # dao textures to sp maps at source size, alpha goes to sidecar
    plan = unpack_plan(mao, split_packed)
    out = {}
    # face-style falls back to diffuse alpha when no spec slot exists
    has_spec = False
    for s in images:
        if plan.get(s, (None, None))[0] == MAP_SPECULAR:
            has_spec = True
            break

    def rgba(img):
        return np.asarray(img.convert("RGBA")).astype(np.uint8)

    def opaque(rgb):
        return np.concatenate([rgb, np.full_like(rgb[..., :1], 255)], -1)

    for slot, pil in images.items():
        stem, _ = plan.get(slot, (None, None))
        if stem is None:
            continue
        a = rgba(pil)

        if stem == MAP_BASECOLOR and slot.lower().find("pack") >= 0 and split_packed:
            # hair packing: A diffuse gray, G spec, B mask
            out[MAP_BASECOLOR] = PilImage.fromarray(
                np.stack([a[..., 3]] * 3 + [np.full_like(a[..., 3], 255)], -1)
            )
            out[MAP_SPECULAR] = PilImage.fromarray(
                np.stack([a[..., 1]] * 3 + [np.full_like(a[..., 1], 255)], -1)
            )
            out[MAP_OPACITY] = PilImage.fromarray(
                np.stack([a[..., 2]] * 3 + [np.full_like(a[..., 2], 255)], -1)
            )
        elif stem == MAP_BASECOLOR:
            out[MAP_BASECOLOR] = PilImage.fromarray(opaque(a[..., :3]))
            sidecar = plan.get(f"{slot}#alpha", (None, None))[0]
            if sidecar == "opacity":
                out[MAP_OPACITY] = PilImage.fromarray(
                    np.stack([a[..., 3]] * 3 + [np.full_like(a[..., 3], 255)], -1)
                )
            elif sidecar == "specular" and not has_spec:
                out[MAP_SPECULAR] = PilImage.fromarray(
                    np.stack([a[..., 3]] * 3 + [np.full_like(a[..., 3], 255)], -1)
                )
        elif stem == MAP_NORMAL:
            out[MAP_NORMAL] = thedas_io_normals_to_rgba(pil)
        elif stem == MAP_SPECULAR:
            out[MAP_SPECULAR] = PilImage.fromarray(opaque(a[..., :3]))
            out[MAP_GLOSSINESS] = PilImage.fromarray(
                np.stack([a[..., 3]] * 3 + [np.full_like(a[..., 3], 255)], -1)
            )
        else:
            out[stem] = PilImage.fromarray(a)
    return out


def _has_spec_slot(plan, mao):
    for s in mao.get("textures") or {}:
        if plan.get(s, (None, None))[0] == MAP_SPECULAR:
            return True
    return False


def _source_alpha(sources, slot, size):
    if not sources or slot not in sources:
        return None

    src = sources[slot].convert("RGBA")
    if size is not None:
        src = resize_rgba(src, tuple(size))
    return np.asarray(src).astype(np.uint8)[..., 3]


def repack_images(mao, sp, split_packed=True, sizes=None, sources=None):
    # sp maps back to dao layout, sidecars resampled to slot size
    plan = unpack_plan(mao, split_packed)
    out = {}

    def fit(img, size):
        if img is None or size is None:
            return img
        return resize_rgba(img, tuple(size))

    def gray(img, channel=0):
        a = np.asarray(img.convert("RGBA")).astype(np.uint8)
        return a[..., channel]

    for slot in mao.get("textures") or {}:
        stem, _ = plan.get(slot, (None, None))
        if stem is None:
            continue
        size = (sizes or {}).get(slot)

        if stem == MAP_BASECOLOR:
            base = fit(sp.get(MAP_BASECOLOR), size)
            if base is None:
                continue
            a = np.asarray(base.convert("RGBA")).astype(np.uint8)
            sidecar = plan.get(f"{slot}#alpha", (None, None))[0]
            if sidecar == "opacity":
                alpha = gray(fit(sp.get(MAP_OPACITY, base), size), 0)
            elif sidecar == "specular" and MAP_SPECULAR in sp:
                if _has_spec_slot(plan, mao):
                    # diffuse alpha unused here, keep source bytes
                    src_alpha = _source_alpha(sources, slot, tuple(base.size))
                    alpha = (
                        src_alpha if src_alpha is not None else gray(fit(sp[MAP_SPECULAR], size), 0)
                    )
                else:
                    alpha = gray(fit(sp[MAP_SPECULAR], size), 0)
            else:
                alpha = a[..., 3]

            if slot.lower().find("pack") >= 0 and split_packed:
                # fold split: A diffuse, G spec, B opacity, R normal red
                spec = gray(fit(sp.get(MAP_SPECULAR, base), size), 0)
                opac = gray(fit(sp.get(MAP_OPACITY, base), size), 0)
                nred = gray(fit(sp.get(MAP_NORMAL, base), size), 0)
                lum = (0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]).astype(np.uint8)
                out[slot] = PilImage.fromarray(np.stack([nred, spec, opac, lum], -1))
            else:
                out[slot] = PilImage.fromarray(np.concatenate([a[..., :3], alpha[..., None]], -1))
        elif stem == MAP_NORMAL:
            normal = fit(sp.get(MAP_NORMAL), size)
            if normal is None:
                continue
            repacked = swizzle_for_encode(normal.convert("RGBA"), "normal")

            out[slot] = (
                resize_rgba(repacked, tuple(size))
                if size is not None and tuple(repacked.size) != tuple(size)
                else repacked
            )
        elif stem == MAP_SPECULAR:
            spec = fit(sp.get(MAP_SPECULAR), size)
            if spec is None:
                continue
            a = np.asarray(spec.convert("RGBA")).astype(np.uint8)
            gloss = gray(fit(sp.get(MAP_GLOSSINESS, spec), size), 0)
            out[slot] = PilImage.fromarray(np.concatenate([a[..., :3], gloss[..., None]], -1))
        else:
            img = fit(sp.get(stem), size)
            if img is not None:
                out[slot] = img.convert("RGBA")
    return out

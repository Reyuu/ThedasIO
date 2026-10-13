from __future__ import annotations

import struct

from ..gff40.description import GFFWriter
from .description import (
    L_BOX_DIM,
    L_CHILDREN,
    L_COBJ_DENSITY,
    L_COBJ_GROUP,
    L_COBJ_TYPE,
    L_HEIGHT,
    L_NAME,
    L_RADIUS,
    L_SHAPE_NAME,
    L_SHAPE_POS,
    L_SHAPE_ROT,
    L_SHAPE_TYPE,
    L_SHAPE_TYPESTRUCT,
    MASKS,
    PhyReader,
)

# type_id -> struct format, straight out of gff40.description. the collision
# masks are bytes, not ints, packed a few to a field around data_index 64.
_FORMATS = {0: "<B", 1: "<b", 4: "<I", 5: "<i", 8: "<f", 10: "<3f", 12: "<4f", 13: "<4f"}

# the editable names the Blender panel hands us, mapped to their label
LABELS = {
    "position": L_SHAPE_POS,
    "rotation": L_SHAPE_ROT,
    "name": L_SHAPE_NAME,
    **{f"mask_{short}": label for label, short in MASKS},
}
OBJ_LABELS = {"density": L_COBJ_DENSITY, "obj_type": L_COBJ_TYPE, "group": L_COBJ_GROUP}
DIM_LABELS = {"radius": L_RADIUS, "height": L_HEIGHT, "box_dim": L_BOX_DIM}


def _field(gff, struct_index, label):
    for f in gff.struct_defs[struct_index].field_instances:
        if f.label_id == label:
            return f
    return None


def _walk(gff, struct_index, base, path=()):
    yield path, struct_index, base
    i = 0
    entries = gff.get_from_data_block(struct_index, base, L_CHILDREN) or []
    for entry in entries:
        if entry is None or entry.get("value") is None:
            continue
        yield from _walk(gff, entry["struct_index"], entry["offset"], path + (i,))
        i += 1


def _pack_inline(writer, gff, struct_index, base, label, value) -> bool:
    # write a scalar or short vector straight into the instance's data block
    field = _field(gff, struct_index, label)
    if field is None or field.flags != 0:
        return False
    fmt = _FORMATS.get(field.type_id)
    if fmt is None:
        return False
    if isinstance(value, (list, tuple)):
        numbers = [float(v) for v in value]
        if len(numbers) != struct.calcsize(fmt) // struct.calcsize("<f" if "f" in fmt else "<i"):
            return False
    else:
        numbers = [float(value) if "f" in fmt else int(value)]
    at = base + field.data_index
    if at + struct.calcsize(fmt) > len(writer.data_block):
        return False
    struct.pack_into(fmt, writer.data_block, at, *numbers)
    return True


def _current(gff, struct_index, base, label):
    try:
        return gff.get_from_data_block(struct_index, base, label)
    except (ValueError, IndexError):
        return None


def _type_struct(gff, struct_index, base) -> tuple[int, int] | None:
    # resolve the TYPE_STRUCT generic on this shap to (struct index, instance base)
    ref = gff.get_from_data_block(struct_index, base, L_SHAPE_TYPESTRUCT)
    if isinstance(ref, dict) and ref.get("offset") != 0xFFFFFFFF:
        return ref["struct_index"], ref["offset"]
    return None


def phy_bytes(raw: bytes, edits: dict | None = None) -> bytes | None:
    # Patched bytes, or None when nothing changed.
    # ``edits`` maps a shape's ``path`` (recorded by :class:`PhyReader`) to the
    # field values to write. Returning None lets the caller pass the original
    # bytes through, which is how untouched files stay byte-identical.
    if not edits:
        return None
    reader = PhyReader(raw)
    by_path = {p: (si, base) for p, si, base in _walk(reader.gff, 0, 0)}
    shapes = {s.path: s for s in reader.shapes}

    writer = GFFWriter.from_reader(reader.gff)
    gff = reader.gff
    changed = False

    for path, values in edits.items():
        if path not in by_path or path not in shapes or not isinstance(values, dict):
            continue
        struct_index, base = by_path[path]
        shape = shapes[path]
        # the shap's parent instance carries the cobj-level fields, and the
        # shape's own path minus its last step is exactly that parent
        parent = by_path.get(path[:-1])

        for key, value in values.items():
            if key in OBJ_LABELS:
                if parent is None:
                    continue
                p_si, p_base = parent
                label = OBJ_LABELS[key]
                if _current(gff, p_si, p_base, label) == value:
                    continue
                changed |= _pack_inline(writer, gff, p_si, p_base, label, value)
            elif key in DIM_LABELS:
                if key == "box_dim":
                    current = shape.box_dim
                    if current is None or tuple(float(v) for v in current) == \
                            tuple(float(v) for v in value):
                        continue
                else:
                    current = shape.radius if key == "radius" else shape.height
                    if current is None or float(current) == float(value):
                        continue
                inner = _type_struct(gff, struct_index, base)
                if inner is None:
                    continue
                changed |= _pack_inline(writer, gff, inner[0], inner[1],
                                        DIM_LABELS[key], value)
            elif key == "name":
                if shape.name == value:
                    continue
                changed |= _pack_string(writer, gff, struct_index, base, L_SHAPE_NAME, value)
            else:
                shape_label = LABELS.get(key)
                if shape_label is None:
                    continue
                if _current(gff, struct_index, base, shape_label) == value:
                    continue
                changed |= _pack_inline(writer, gff, struct_index, base, shape_label, value)

    return writer.write() if changed else None


def _pack_string(writer, gff, struct_index, base, label, text) -> bool:
    # ecstring is an offset to <I count><utf16>; allocate a new one and repoint
    field = _field(gff, struct_index, label)
    if field is None or field.flags != 0 or field.type_id != 14:
        return False
    if not isinstance(text, str):
        return False
    at = base + field.data_index
    if at + 4 > len(writer.data_block):
        return False
    struct.pack_into("<I", writer.data_block, at, writer.alloc_ecstring(text))
    return True


# the struct each primitive lives in, and the SHAPE_TYPE that selects it
PRIMITIVES = {
    "capsule": (b"caps", 3),
    "sphere": (b"sphs", 0),
    "box": (b"boxs", 2),
}


def _add_label(kind):
    return {"capsule": "Capsule", "sphere": "Sphere", "box": "Box"}.get(kind, "Shape")
TEMPLATE_FOURCC = b"shap"
COBJECT_FOURCC = b"cobj"
NULL_REF = 0xFFFFFFFF
GENERIC_STRUCT = 0x4000


def _fourcc_of(gff, struct_index) -> bytes:
    return gff.struct_defs[struct_index].type_fourcc.to_bytes(4, "little")


def _struct_of(gff, fourcc):
    for i, sd in enumerate(gff.struct_defs):
        if _fourcc_of(gff, i) == fourcc:
            return i
    return None


def _instances(gff):
    return list(_walk(gff, 0, 0))


def _clone(writer, gff, struct_index, base):
    size = gff.struct_defs[struct_index].struct_size
    at = len(writer.data_block)
    writer.data_block += gff.data[gff.data_offset + base : gff.data_offset + base + size]
    return at


def _set_inline(writer, gff, struct_index, base, label, fmt, values) -> bool:
    return _pack_inline(writer, gff, struct_index, base, label, values)


def _child_entries(gff, struct_index, base):
    field = _field(gff, struct_index, L_CHILDREN)
    if field is None:
        return None
    value = gff.get_from_data_block(struct_index, base, L_CHILDREN) or []
    generic = field.type_id == 0xFFFF
    out = []
    for e in value:
        if not isinstance(e, dict) or e.get("value") is None:
            continue
        if generic:
            out.append((e["struct_index"], e.get("flags") or 0, e["offset"]))
        else:
            out.append(e["offset"])
    return out


def _replace_children(writer, gff, struct_index, base, entries) -> bool:
    field = _field(gff, struct_index, L_CHILDREN)
    if field is None or field.flags == 0:
        return False
    region = len(writer.data_block)
    writer.data_block += struct.pack("<I", len(entries))
    if entries and not isinstance(entries[0], tuple):
        for off in entries:
            writer.data_block += struct.pack("<I", off)
    else:
        for si, fl, off in entries:
            writer.data_block += struct.pack("<HHI", si, fl, off)
    at = base + field.data_index
    if at + 4 > len(writer.data_block):
        return False
    struct.pack_into("<I", writer.data_block, at, region)
    return True


def _cobj_under(gff, bone, instances):
    for path, struct_index, base in instances:
        if _fourcc_of(gff, struct_index) != b"node":
            continue
        try:
            if gff.get_from_data_block(struct_index, base, L_NAME) != bone:
                continue
        except ValueError:
            continue
        entries = gff.get_from_data_block(struct_index, base, L_CHILDREN) or []
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("value") is None:
                continue
            si = entry["struct_index"]
            if _fourcc_of(gff, si) == COBJECT_FOURCC:
                return si, entry["offset"]
    return None, None


def _existing_names(gff) -> set:
    shap_si = _struct_of(gff, TEMPLATE_FOURCC)
    if shap_si is None:
        return set()
    out = set()
    for _path, si, base in _instances(gff):
        if si != shap_si:
            continue
        try:
            value = gff.get_from_data_block(si, base, L_SHAPE_NAME)
        except ValueError:
            continue
        if value:
            out.add(str(value))
    return out


def add_shapes(raw: bytes, additions) -> bytes | None:
    #Append new collision primitives. Returns None when nothing was added.
    #
    #``additions`` is a list of dicts::
    #
    #    {"bone": "LeftElbow", "kind": "capsule", "radius": 0.08,
    #     "height": 0.2, "position": (0, 0, 0), "rotation": (0, 0, 0, 1),
    #     "name": "Added", "masks": {...}}
    additions = [a for a in (additions or []) if isinstance(a, dict)]
    if not additions:
        return None

    reader = PhyReader(raw)
    gff = reader.gff
    writer = GFFWriter.from_reader(gff)

    shap_si = _struct_of(gff, TEMPLATE_FOURCC)
    if shap_si is None:
        raise ValueError("this file has no shap struct; cannot add collision")
    cobj_si = _struct_of(gff, COBJECT_FOURCC)
    if cobj_si is None:
        raise ValueError("this file has no cobj struct; cannot add collision")

    instances = _instances(gff)
    # a shap to copy: every field is already laid out the way the file expects
    template = next((b for _p, si, b in instances if si == shap_si), None)
    if template is None:
        raise ValueError("this file has no shap instance to copy")

    added = 0
    # once a parent's list has been rewritten it lives in the writer's block, not
    # in the source file, so the running list has to be carried here
    parent_children: dict = {}
    for spec in additions:
        kind = str(spec.get("kind") or "capsule").lower()
        if kind not in PRIMITIVES:
            raise ValueError(f"unknown collision kind {kind!r}")
        want_fourcc, type_id = PRIMITIVES[kind]
        type_si = _struct_of(gff, want_fourcc)
        if type_si is None:
            raise ValueError(
                f"this file has no {want_fourcc.decode()} struct, so a "
                f"{kind} cannot be added here"
            )

        bone = str(spec.get("bone") or "")
        parent_si, parent_base = _cobj_under(gff, bone, instances)
        if parent_si is None:
            raise ValueError(f"no collision object under bone {bone!r}")
        key = (parent_si, parent_base)
        if key not in parent_children:
            parent_children[key] = _child_entries(gff, parent_si, parent_base) or []
        siblings = parent_children[key]

        # the primitive's own parameters
        type_base = _clone(writer, gff, type_si,
                           next((b for _p, si, b in instances if si == type_si), 0))
        if kind == "capsule":
            _set_inline(writer, gff, type_si, type_base, L_RADIUS, "<f",
                        float(spec.get("radius") or 0.05))
            _set_inline(writer, gff, type_si, type_base, L_HEIGHT, "<f",
                        float(spec.get("height") or 0.0))
        elif kind == "sphere":
            _set_inline(writer, gff, type_si, type_base, L_RADIUS, "<f",
                        float(spec.get("radius") or 0.05))
        else:
            dims = spec.get("box_dim") or spec.get("dims") or (0.2, 0.2, 0.2)
            _set_inline(writer, gff, type_si, type_base, L_BOX_DIM, "<3f",
                        [float(v) for v in dims][:3])

        # the shape itself
        shape_base = _clone(writer, gff, shap_si, template)
        _set_inline(writer, gff, shap_si, shape_base, L_SHAPE_TYPE, "<I", type_id)
        pos = spec.get("position") or (0.0, 0.0, 0.0)
        rot = spec.get("rotation") or (0.0, 0.0, 0.0, 1.0)
        _set_inline(writer, gff, shap_si, shape_base, L_SHAPE_POS, "<4f",
                    [float(v) for v in tuple(pos)[:3]] + [1.0])
        _set_inline(writer, gff, shap_si, shape_base, L_SHAPE_ROT, "<4f",
                    [float(v) for v in tuple(rot)[:4]])
        taken = _existing_names(gff)
        base_name = str(spec["name"]) if spec.get("name") else \
            f"{_add_label(kind)}_{bone}" if bone else _add_label(kind)
        name = base_name
        n = 1
        while name in taken:
            n += 1
            name = f"{base_name}{n}"
        _pack_string(writer, gff, shap_si, shape_base, L_SHAPE_NAME, name)
        masks = spec.get("masks") or {}
        for short, value in masks.items():
            for label, candidate in MASKS:
                if candidate == short:
                    _set_inline(writer, gff, shap_si, shape_base, label, "<B",
                                int(value))

        # point the shape at its primitive
        field = _field(gff, shap_si, L_SHAPE_TYPESTRUCT)
        if field is None:
            raise ValueError("this file's shap has no TYPE_STRUCT field")
        struct.pack_into(
            "<II", writer.data_block, shape_base + field.data_index,
            (GENERIC_STRUCT << 16) | type_si, type_base,
        )

        # append the new shape to the parent's list, matching its entry width
        if siblings and isinstance(siblings[0], tuple):
            entries = siblings + [(shap_si, 0, shape_base)]
        else:
            entries = siblings + [shape_base]
        if _replace_children(writer, gff, parent_si, parent_base, entries):
            parent_children[key] = entries
            added += 1

    return writer.write() if added else None
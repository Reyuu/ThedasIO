# dao .tnt tints, ten TINT_MASK colours in one gff struct

import struct

from .gff40.description import (
    FIELD_SIZE,
    FIELD_TYPES,
    FLAGS,
    HEADER_SIZE,
    STRUCT_DEF_SIZE,
    GFFReader,
    GFFStructDef,
    GFFWriter,
)

FIELDS = (
    ("Diffuse opacity", 14008),
    ("Specular opacity", 14009),
    ("Diffuse R", 14000),
    ("Specular R", 14003),
    ("Diffuse G", 14001),
    ("Specular G", 14004),
    ("Diffuse B", 14002),
    ("Specular B", 14005),
    ("Diffuse A", 14006),
    ("Specular A", 14007),
)
NAMES: tuple[str, ...] = tuple(name for name, _ in FIELDS)
SIDES = ("Diffuse", "Specular")

# all-zero opacity = no tint, same as old imports without tnt
DEFAULTS = {}
for name in NAMES:
    if name.endswith("opacity"):
        DEFAULTS[name] = (0.0, 0.0, 0.0, 0.0)
    else:
        DEFAULTS[name] = (1.0, 1.0, 1.0, 1.0)


def alpha_input(name):
    # no alpha out on SeparateColor so opacities get their own float input
    if name in _OPAQUE:
        return f"{name} A"
    return None


def group_name(side):
    return f"DAO Tint {side}"


def for_side(side):
    out = []
    for name in NAMES:
        if name.startswith(f"{side} "):
            out.append(name)
    return tuple(out)


_OPAQUE = set()
for n in NAMES:
    if n.endswith("opacity"):
        _OPAQUE.add(n)

# the tnt struct carries no mmh/msh kind, so it uses "GFF " itself
_STRUCT = int.from_bytes(b"GFF ", "little")
# fixed layout, ten Vector4f cells: 28 header + 16 defs + 120 fields = 164,
# padded with 0xFF to the next 16-byte boundary like stock msh/mmh files
_FIELDS_END = HEADER_SIZE + STRUCT_DEF_SIZE + len(FIELDS) * FIELD_SIZE
_GAP = b"\xff" * (-_FIELDS_END % 16)


def build(values):
    # missing names fall back to DEFAULTS
    writer = GFFWriter(b"PC  ", b"GFF ", b"V4.0", gap=_GAP)
    writer.struct_defs.append(GFFStructDef(_STRUCT, 0, 0, FIELD_TYPES[12] * len(FIELDS)))

    for name, label in FIELDS:
        colour = values.get(name)
        if not colour:
            colour = DEFAULTS[name]
        index = writer.alloc(struct.pack("<4f", *colour))
        writer.add_field(0, label, 12, FLAGS["NONE"], index)
    return writer.write()


def parse(raw):
    if raw[:8] != b"GFF V4.0":
        raise ValueError("not a GFF V4.0 file")
    reader = GFFReader(raw)
    fields = {}
    if reader.struct_defs:
        for f in reader.struct_defs[0].field_instances:
            fields[f.label_id] = f
    missing = [name for name, label in FIELDS if label not in fields]
    if missing:
        raise ValueError(f"tint file is missing {', '.join(missing)}")
    for name, label in FIELDS:
        field = fields[label]
        if field.type_id != 12 or field.flags & 0xE000:
            raise ValueError(f"{name}: not a plain Vector4f field")
    values = reader.get_from_data_block(0, 0)
    return {name: values[label] for name, label in FIELDS}  # type: ignore

# DAO `.ani`: a GFF V4.0 whose root names the animation and hangs one node
# list off it, one node per animated channel. Node = NodeName, Target (u8),
# NodeData (generic list) -> key struct array. Key = KeyTime (u16), KeyDataN.
# Target 2/4 = 32/64-bit packed quaternion rotation, 6 = 3x f32 translation,
# else one f32 per KeyData slot (vfx). KeyTime is a u16 fraction of the clip:
# seconds = time / 65535 * length.

from __future__ import annotations

import math
import struct
from collections.abc import Sequence
from pathlib import Path

from ..gff40.description import FIELD_TYPES, FLAGS, GFFReader
from ..gff40.labels import DATA as LABELS

_ID = {name: value for value, name in LABELS.items()}

L_NAME = _ID["GFF_ANIMATION_NAME"]
L_LENGTH = _ID["GFF_ANIMATION_ANIMLENGTH"]
L_ADDITIVE = _ID["GFF_ANIMATION_ISADDITIVE"]
L_OVERRIDE = _ID["GFF_ANIMATION_ISOVERRIDE"]
L_IGNORE_SCALE = _ID["GFF_ANIMATION_IGNORESCALE"]
L_NODES = _ID["GFF_ANIMATION_NODELIST"]
L_NODE_NAME = _ID["GFF_ANIMATION_NODENAME"]
L_NODE_TARGET = _ID["GFF_ANIMATION_TARGET"]
L_NODE_DATA = _ID["GFF_ANIMATION_NODEDATA"]
L_KEY_TIME = _ID["GFF_ANIMATION_KEY_TIME"]
L_KEY_DATA0 = _ID["GFF_ANIMATION_KEY_DATA0"]
L_KEY_DATA1 = _ID["GFF_ANIMATION_KEY_DATA1"]
L_KEY_DATA2 = _ID["GFF_ANIMATION_KEY_DATA2"]

TIME_MAX = 65535

ROTATION32 = 2
ROTATION64 = 4
TRANSLATION = 6

# GFF field type ids, needed wherever a value is written back by hand
TYPE_U16 = 2
TYPE_U32 = 4

# A packed rotation holds three offset-binary fields (the kept components
# scaled by sqrt(2)) and a two-bit index naming the dropped one, laid high to
# low as field0 | field1 | field2 | index. A field of n bits is centred on
# 2^(n - 1), so the code 0 is the most negative value the field can hold.
SQRT2 = math.sqrt(2.0)

Q32_BITS = (10, 10, 10)
Q64_BITS = (21, 21, 20)

INDEX_BITS = 2
INDEX_MASK = (1 << INDEX_BITS) - 1


def _rebuild(index: int, ratios: Sequence) -> tuple:
    # the dropped component follows from the other three and is always positive
    dropped = math.sqrt(max(0.0, 1.0 - sum(r * r for r in ratios)))
    parts = list(ratios)
    parts.insert(index, dropped)
    return tuple(parts)


def _field_code(ratio: float, bits: int) -> int:
    centre = 1 << (bits - 1)
    code = round(ratio * SQRT2 * (centre - 1)) + centre
    return max(0, min((1 << bits) - 1, code))


def _field_ratio(code: int, bits: int) -> float:
    centre = 1 << (bits - 1)
    return (code - centre) / (SQRT2 * (centre - 1))


def _read_fields(word: int, bits) -> list:
    # mirror of _write_fields: walk up from the index bits, filling in reverse
    fields = [0] * len(bits)
    shift = INDEX_BITS
    for i in range(len(bits) - 1, -1, -1):
        fields[i] = (word >> shift) & ((1 << bits[i]) - 1)
        shift += bits[i]
    return fields


def _write_fields(fields, bits, index: int) -> int:
    word = 0
    for value, width in zip(fields, bits):
        word = (word << width) | value
    return (word << INDEX_BITS) | index


def decode_quat32(word: int) -> tuple:
    fields = _read_fields(word, Q32_BITS)
    ratios = [_field_ratio(c, b) for c, b in zip(fields, Q32_BITS)]
    return _rebuild(word & INDEX_MASK, ratios)


def decode_quat64(word: int) -> tuple:
    fields = _read_fields(word, Q64_BITS)
    ratios = [_field_ratio(c, b) for c, b in zip(fields, Q64_BITS)]
    return _rebuild(word & INDEX_MASK, ratios)


def decode_key(target: int, raw: dict):
    d = raw.get(L_KEY_DATA0)
    if target == ROTATION32 and isinstance(d, int):
        return decode_quat32(d)
    if target == ROTATION64 and isinstance(d, int):
        return decode_quat64(d)

    d1 = raw.get(L_KEY_DATA1) or 0.0
    d2 = raw.get(L_KEY_DATA2) or 0.0
    if target == TRANSLATION:
        return (float(d or 0.0), float(d1), float(d2))

    if isinstance(d, (int, float)):
        return float(d)
    return (float(d or 0.0), float(d1), float(d2))


class AnimNode:
    def __init__(self, name, target, keys):
        self.name = name
        self.target = target
        self.keys = keys

    @property
    def kind(self) -> str:
        if self.target in (ROTATION32, ROTATION64):
            return "rotation"
        if self.target == TRANSLATION:
            return "position"
        return "scalar"

    def __repr__(self):
        return f"<AnimNode {self.name!r} {self.kind} keys={len(self.keys)}>"


class Animation:
    def __init__(self, name, length, additive, nodes, skipped=()):
        self.name = name
        self.length = length
        self.additive = additive
        self.nodes = nodes
        self.skipped = list(skipped)

    def by_name(self, export_name: str) -> AnimNode | None:
        for node in self.nodes:
            if node.name == export_name:
                return node
        return None

    def summary(self) -> dict:
        kinds: dict = {}
        for n in self.nodes:
            kinds[n.kind] = kinds.get(n.kind, 0) + 1
        return {"name": self.name, "length": self.length,
                "nodes": len(self.nodes), "kinds": kinds}


def _decode_node(node: dict, length: float) -> AnimNode | None:
    if not isinstance(node, dict):
        return None

    target = int(node.get(L_NODE_TARGET) or 0)
    node_data = node.get(L_NODE_DATA)
    if not isinstance(node_data, dict):
        return None

    rows = (node_data.get("value") or {}).get(L_NODE_DATA)
    if not isinstance(rows, list):
        return None

    keys = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        time = float(row.get(L_KEY_TIME) or 0) / TIME_MAX * length
        keys.append((time, decode_key(target, row)))

    if not keys:
        return None
    return AnimNode(node.get(L_NODE_NAME), target, keys)


class ANIReader:
    def __init__(self, raw: bytes):
        self.raw = raw
        self.gff = GFFReader(raw)
        self.root = self.gff.get_from_data_block(0, 0)
        self.name = self.root.get(L_NAME)
        self.length = float(self.root.get(L_LENGTH) or 0.0)
        self.additive = bool(self.root.get(L_ADDITIVE))
        self.animation = Animation(
            self.name,
            self.length,
            self.additive,
            [n for n in (_decode_node(node, self.length)
                         for node in (self.root.get(L_NODES) or []))
             if n is not None],
        )

    @property
    def nodes(self) -> list:
        return self.animation.nodes

    def summary(self) -> dict:
        return self.animation.summary()


def load(path) -> ANIReader:
    return ANIReader(Path(path).read_bytes())

# Keys are written in place: the key count, the key times and every struct stay
# where they are, so an edited file is the same size and the untouched channels
# keep their original bytes.

# -q is the same rotation, and _rebuild always puts the dropped component
# back positive, so flip the sign here before it is dropped.
def _largest_first(q) -> tuple:
    comps = [float(c) for c in q]
    idx = max(range(4), key=lambda i: abs(comps[i]))
    if comps[idx] < 0:
        comps = [-c for c in comps]
    return idx, [comps[i] for i in range(4) if i != idx]


def encode_quat32(q) -> int:
    idx, rest = _largest_first(q)
    fields = [_field_code(r, b) for r, b in zip(rest, Q32_BITS)]
    return _write_fields(fields, Q32_BITS, idx)


def encode_quat64(q) -> int:
    idx, rest = _largest_first(q)
    fields = [_field_code(r, b) for r, b in zip(rest, Q64_BITS)]
    return _write_fields(fields, Q64_BITS, idx)


# ref_cell is the u32 pointing at the row list, first the first key struct,
# rows are ksize bytes apart, time_rel/data_rel are field offsets inside a
# key struct (the data ones paired with their type ids).
class _KeyLayout:
    def __init__(self, ref_cell, first, ksize, time_rel, data_rel):
        self.ref_cell = ref_cell
        self.first = first
        self.ksize = ksize
        self.time_rel = time_rel
        self.data_rel = data_rel


def _locate_keys(reader: GFFReader) -> list:
    # where each root node's key list lives, in file order, None where it has none
    def field(struct_def, label):
        return next((f for f in struct_def.field_instances if f.label_id == label), None)

    def u32(block, offset):
        at = reader.data_offset + block + offset
        if at + 4 > len(reader.data):
            raise ValueError(f"key list reads past end of file at {at}")
        return struct.unpack_from("<I", reader.data, at)[0]

    root = reader.struct_defs[0]
    nodes_field = field(root, L_NODES)
    if nodes_field is None:
        return []
    node_def = reader.struct_defs[nodes_field.type_id]
    node_data_field = field(node_def, L_NODE_DATA)
    nodes_block = u32(0, nodes_field.data_index)
    out: list = []

    for i in range(u32(nodes_block, 0)):
        node_block = nodes_block + 4 + i * node_def.struct_size
        if node_data_field is None or node_data_field.flags != FLAGS["REFERENCE"]:
            out.append(None)
            continue

        # a generic reference is written inline as (flags << 16 | type), address
        at = reader.data_offset + node_block + node_data_field.data_index
        if at + 8 > len(reader.data):
            raise ValueError(f"key list reads past end of file at {at}")
        packed, address = struct.unpack_from("<II", reader.data, at)

        rows_def = reader.struct_defs[packed & 0xFFFF]
        rows_field = field(rows_def, L_NODE_DATA)
        if rows_field is None or rows_field.flags != FLAGS["STRUCT_ARRAY"]:
            out.append(None)
            continue

        key_def = reader.struct_defs[rows_field.type_id]
        time_field = field(key_def, L_KEY_TIME)
        data_fields = [field(key_def, label)
                       for label in (L_KEY_DATA0, L_KEY_DATA1, L_KEY_DATA2)]
        data_fields = [f for f in data_fields if f is not None]

        rows_block = u32(address, rows_field.data_index)
        if time_field is None or not data_fields or u32(rows_block, 0) == 0:
            out.append(None)
            continue

        out.append(_KeyLayout(
            reader.data_offset + address + rows_field.data_index,
            reader.data_offset + rows_block + 4,
            key_def.struct_size,
            time_field.data_index,
            [(f.data_index, f.type_id) for f in data_fields],
        ))
    return out


def _put(buf: bytearray, at: int, type_id: int, value) -> None:
    size = FIELD_TYPES[type_id]
    if isinstance(value, float):
        struct.pack_into("<f" if size == 4 else "<d", buf, at, value)
    else:
        buf[at : at + size] = int(value).to_bytes(size, "little")


def _write_value(buf: bytearray, base: int, layout: _KeyLayout, target: int, value) -> None:
    if target in (ROTATION32, ROTATION64):
        word = encode_quat32(value) if target == ROTATION32 else encode_quat64(value)
        rel, type_id = layout.data_rel[0]
        _put(buf, base + rel, type_id, word)
    elif target == TRANSLATION:
        for (rel, type_id), component in zip(layout.data_rel, value):
            _put(buf, base + rel, type_id, float(component))


def _quant_time(seconds: float, length: float) -> int:
    if length <= 0:
        return 0
    return max(0, min(TIME_MAX, round(seconds / length * TIME_MAX)))


# the value the reader will see back, the clip length is f32 not f64
def _stored_as(fmt: str, value: float) -> float:
    return struct.unpack(fmt, struct.pack(fmt, value))[0]


def _float_fmt(type_id: int) -> str:
    return "<f" if FIELD_TYPES[type_id] == 4 else "<d"


# Write new key values and key times into the file. values(node) gets each
# decoded AnimNode and returns None to leave the node as it is; (times,
# values) with the node's own key times to rewrite values in place, where a
# None value keeps that key's bytes; or (times, values) with any other times
# for a brand-new key list (no Nones allowed), appended to the data block
# with the node repointed at it.
def patch_ani(raw: bytes, values, length: float | None = None) -> bytes:
    reader = GFFReader(raw)
    root = reader.get_from_data_block(0, 0)
    old_len = float(root.get(L_LENGTH) or 0.0)
    new_len = old_len if length is None else float(length)
    nodes = root.get(L_NODES) or []
    layouts = _locate_keys(reader)
    buf = bytearray(raw)

    if new_len != old_len:
        # every key's time is a fraction of the length, so all of them are rebuilt
        length_field = next(f for f in reader.struct_defs[0].field_instances
                            if f.label_id == L_LENGTH)
        fmt = _float_fmt(length_field.type_id)
        at = reader.data_offset + length_field.data_index
        _put(buf, at, length_field.type_id, new_len)
        new_len = _stored_as(fmt, new_len)

    for node, layout in zip(nodes, layouts):
        if layout is None:
            continue
        anim_node = _decode_node(node, old_len)
        if anim_node is None:
            continue

        plan = values(anim_node)
        if plan is None:
            if new_len != old_len:
                raise ValueError(f"{anim_node.name}: the clip is longer, so its "
                                 "keys must be rewritten")
            continue

        times, new_values = plan
        if len(new_values) != len(times):
            raise ValueError(f"{anim_node.name}: {len(times)} times but "
                             f"{len(new_values)} values")

        # the key times are unchanged, so only the values that moved are written
        if new_len == old_len and list(times) == [t for t, _ in anim_node.keys]:
            for index, value in enumerate(new_values):
                if value is not None:
                    base = layout.first + index * layout.ksize
                    _write_value(buf, base, layout, anim_node.target, value)
            continue

        if any(v is None for v in new_values):
            raise ValueError(f"{anim_node.name}: a rebuilt key list needs every value")
        if anim_node.target not in (ROTATION32, ROTATION64, TRANSLATION):
            raise ValueError(f"{anim_node.name}: cannot rewrite target "
                             f"{anim_node.target} keys")

        payload = bytearray()
        template = bytes(buf[layout.first : layout.first + layout.ksize])
        for time, value in zip(times, new_values):
            key = bytearray(template)
            _put(key, layout.time_rel, TYPE_U16, _quant_time(time, new_len))
            _write_value(key, 0, layout, anim_node.target, value)
            payload += key

        # align the new list; fill is 0xFF like the header gap
        while (len(buf) - reader.data_offset) % 4:
            buf.append(0xFF)

        block = len(buf) - reader.data_offset
        buf += struct.pack("<I", len(times)) + payload
        _put(buf, layout.ref_cell, TYPE_U32, block)
    return bytes(buf)
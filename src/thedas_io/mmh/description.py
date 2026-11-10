import struct
from pathlib import Path
from typing import Any

from ..gff40.description import FLAGS, GFFReader, GFFWriter, label_name

# mmh layout: same gff header as msh, root is def 0 at data offset 0
# kids walk 6999 lists, unknown defs/labels stay in the tree untouched

FILE_TYPE = b"MMH "
L_NAME = 6000
L_CHILDREN = 6999


def _kind_name(fourcc: Any) -> str:
    # "replace" so a fourcc with a high byte decodes to a name instead of
    # killing the read; this runs for every node and every orphan def. The msh
    # module's copy of this helper already does this.
    return fourcc.to_bytes(4, "little").decode("ascii", "replace")


class Node:
    def __init__(self, kind: Any, name: str, values: dict, children: list) -> None:
        self.kind = kind
        self.name = name
        self.values = values
        self.children = children

    def __repr__(self) -> str:
        return f"Node({self.kind!r}, {self.name!r}, {len(self.children)} children)"

    def walk(self) -> Any:
        yield self
        for child in self.children:
            yield from child.walk()

    def find(self, name: str) -> Any:
        for node in self.walk():
            if node.name == name:
                return node
        raise KeyError(f"Node {name!r} not found")


def load(path: Any) -> "MMHReader":
    return MMHReader(Path(path).read_bytes()).tree


class MMHReader:
    def __init__(self, data: Any) -> None:
        self.data = data
        self.gff: Any = None
        self.root: dict = {}
        self.tree: Any = None
        self.orphan_kinds: list = []
        self._parse()

    def _parse(self) -> None:
        gff = GFFReader(self.data)
        if gff.file_type != FILE_TYPE:
            raise ValueError(f"Invalid MMH file_type {gff.file_type!r}, expected {FILE_TYPE!r}")

        self.gff = gff
        self.root = gff.get_from_data_block(0, 0)
        visited: set = set()
        self.tree = self._node(0, self.root, visited)

        orphans: list = []
        for i in range(gff.nstruct):
            if i not in visited:
                orphans.append(_kind_name(gff.struct_defs[i].type_fourcc))
        self.orphan_kinds = orphans

    def _struct_def(self, struct_index: int) -> Any:
        # struct_index comes out of a generic list entry, so it is whatever the
        # file says. Index the table directly and it is a bare IndexError;
        # GFFReader already names this one.
        defs = self.gff.struct_defs
        if struct_index < 0 or struct_index >= len(defs):
            raise ValueError(f"struct index {struct_index} out of range")
        return defs[struct_index]

    def _node(self, struct_index: Any, values: dict, visited: Any) -> Any:
        kind = _kind_name(self._struct_def(struct_index).type_fourcc)
        values = dict(values)
        name = values.pop(L_NAME, None)
        entries = values.pop(L_CHILDREN, None)
        if not entries:
            entries = []
        if not isinstance(entries, list):
            raise TypeError(f"expected list for {label_name(L_CHILDREN)} in {kind}")

        children = []
        for e in entries:
            if e is None or e.get("value") is None:
                continue
            children.append(self._node(e["struct_index"], e["value"], visited))

        # trsl/rota/scal live in child structs, fold them up
        for child in children:
            if child.kind == "trsl" and 6047 in child.values and 6047 not in values:
                values[6047] = child.values[6047]
            elif child.kind == "rota" and 6048 in child.values and 6048 not in values:
                values[6048] = child.values[6048]
            elif child.kind == "scal" and 6278 in child.values and 6278 not in values:
                values[6278] = child.values[6278]

        visited.add(struct_index)
        return Node(kind, name, values, children)


class MMHWriter:
    def __init__(self, reader: Any) -> None:
        if not isinstance(reader, MMHReader):
            raise TypeError(f"need a MMHReader, got {type(reader)}")
        self._gff = GFFWriter.from_reader(reader.gff)

    def write(self) -> bytes:
        return self._gff.write()

    # mshh node fields the chunk-split path patches in place. The real MMH chunk
    # node is wider than these, but the split callback only touches the fields it
    # needs and leaves the rest alone, so a file keeps its unknown cells.
    _NODE_NAME = 6000
    _NODE_MATERIAL = 6001
    _NODE_MESH = 6005
    _NODE_CHUNK = 6006
    _NODE_COLOR = 6335
    _NODE_BBOX_MIN = 6054
    _NODE_BBOX_MAX = 6055

    def node_offsets(self) -> list:
        # (name, instance offset) for every mshh node in the file, in file order
        return node_offsets(self._gff)

    def split_multi_mesh(self, node_offset: int, material_names: list[str]) -> bool:
        # one mshh node per material slot: the first slot keeps the original
        # struct, every later slot gets a copy inserted right after it under the
        # same parent, pointing at the same mesh and chunk refs but with the
        # slot's own material and a disambiguated name and chunk name. Returns
        # True when the file changed
        if len(material_names) < 2:
            return False

        gff = self._gff
        node_si = _gff_struct_index(gff, b"mshh")
        found = _parent_list(gff, node_si, node_offset)
        if found is None:
            return False
        parent_inst, parent_field, rows = found
        index = next((i for i, row in enumerate(rows) if row[2] == node_offset), None)
        if index is None:
            return False

        base_name = _read_ecstring_field(gff, node_si, node_offset, self._NODE_NAME)
        base_chunk = _read_ecstring_field(gff, node_si, node_offset, self._NODE_CHUNK)
        if base_name is None or base_chunk is None:
            return False

        _write_ecstring_field(gff, node_si, node_offset, self._NODE_NAME, f"{base_name}_0")
        _write_ecstring_field(gff, node_si, node_offset, self._NODE_CHUNK, f"{base_chunk}_0")
        _write_ecstring_field(gff, node_si, node_offset, self._NODE_MATERIAL, material_names[0])

        flags = rows[index][1]
        for i, material in enumerate(material_names[1:], start=1):
            copy = _copy_instance(gff, node_si, node_offset)
            _write_ecstring_field(gff, node_si, copy, self._NODE_NAME, f"{base_name}_{i}")
            _write_ecstring_field(gff, node_si, copy, self._NODE_CHUNK, f"{base_chunk}_{i}")
            _write_ecstring_field(gff, node_si, copy, self._NODE_MATERIAL, material)
            rows.insert(index + i, (node_si, flags, copy))

        _write_child_rows(gff, parent_inst, parent_field, rows)
        return True


def _field_def(gff: Any, struct_index: int, label: int) -> Any:
    for field in gff.struct_defs[struct_index].field_instances:
        if field.label_id == label:
            return field
    return None


def _read_ecstring(block: Any, offset: int) -> str | None:
    if offset == 0xFFFFFFFF or offset < 0 or offset + 4 > len(block):
        return None
    (count,) = struct.unpack_from("<I", block, offset)
    raw = block[offset + 4 : offset + 4 + count * 2]
    if len(raw) < count * 2:
        return None
    return raw.decode("utf-16-le").split("\x00", 1)[0]


def _read_ecstring_field(gff: Any, struct_index: int, inst: int, label: int) -> str | None:
    field = _field_def(gff, struct_index, label)
    if field is None:
        return None
    cell = inst + field.data_index
    if cell + 4 > len(gff.data_block):
        return None
    (offset,) = struct.unpack_from("<I", gff.data_block, cell)
    return _read_ecstring(gff.data_block, offset)


def _write_ecstring_field(gff: Any, struct_index: int, inst: int, label: int, value: str) -> None:
    field = _field_def(gff, struct_index, label)
    if field is None:
        return
    cell = inst + field.data_index
    if cell + 4 > len(gff.data_block):
        return
    struct.pack_into("<I", gff.data_block, cell, gff.alloc_ecstring(value))


def _copy_instance(gff: Any, struct_index: int, inst: int) -> int:
    # a byte copy of the struct instance, so every field the split does not
    # rewrite (bbox, tint flag, unknown cells) survives on the new node
    size = gff.struct_defs[struct_index].struct_size
    return gff.alloc(bytes(gff.data_block[inst : inst + size]))


def _child_rows(gff: Any, list_offset: int) -> list:
    # the 6999 child list is <I n> then n * (struct index, flags, data offset)
    if list_offset == 0xFFFFFFFF or list_offset + 4 > len(gff.data_block):
        return []
    (count,) = struct.unpack_from("<I", gff.data_block, list_offset)
    rows = []
    for i in range(count):
        at = list_offset + 4 + i * 8
        if at + 8 > len(gff.data_block):
            break
        rows.append(struct.unpack_from("<HHI", gff.data_block, at))
    return rows


def _parent_list(
    gff: Any, node_si: int, target: int, struct_index: int = 0, inst: int = 0, seen: Any = None
) -> Any:
    # (parent instance, its 6999 field, that parent's rows) for the node at
    # `target`, found by walking the child lists from the root down
    if seen is None:
        seen = set()
    if (struct_index, inst) in seen:
        return None
    seen.add((struct_index, inst))

    field = _field_def(gff, struct_index, L_CHILDREN)
    if field is None or field.flags != FLAGS["GENERIC_LIST"]:
        return None
    cell = inst + field.data_index
    if cell + 4 > len(gff.data_block):
        return None
    (list_offset,) = struct.unpack_from("<I", gff.data_block, cell)
    rows = _child_rows(gff, list_offset)
    for row_si, _, row_off in rows:
        if row_si == node_si and row_off == target:
            return inst, field, rows
    for row_si, _, row_off in rows:
        found = _parent_list(gff, node_si, target, row_si, row_off, seen)
        if found is not None:
            return found
    return None


def _write_child_rows(gff: Any, parent_inst: int, field: Any, rows: list) -> None:
    # the list has to grow, so it moves to the end of the data block and the
    # parent's cell is repointed at it
    blob = bytearray(struct.pack("<I", len(rows)))
    for row in rows:
        blob += struct.pack("<HHI", *row)
    list_offset = gff.alloc(bytes(blob))
    struct.pack_into("<I", gff.data_block, parent_inst + field.data_index, list_offset)


def node_offsets(gff: Any, struct_index: int = 0, inst: int = 0) -> list:
    # (name, instance offset) for every mshh node reachable from the root, in
    # file order -- what the export needs to address a node it only knows by name
    out: list = []
    field = _field_def(gff, struct_index, L_CHILDREN)
    if field is None or field.flags != FLAGS["GENERIC_LIST"]:
        return out
    cell = inst + field.data_index
    if cell + 4 > len(gff.data_block):
        return out
    (list_offset,) = struct.unpack_from("<I", gff.data_block, cell)
    mshh_si = None
    for row_si, _, row_off in _child_rows(gff, list_offset):
        if mshh_si is None:
            try:
                mshh_si = _gff_struct_index(gff, b"mshh")
            except ValueError:
                return out
        if row_si == mshh_si:
            name = _read_ecstring_field(gff, row_si, row_off, L_NAME)
            out.append((name, row_off))
        out.extend(node_offsets(gff, row_si, row_off))
    return out


def _gff_struct_index(gff: Any, fourcc: bytes) -> int:
    target = int.from_bytes(fourcc, "little")
    for i, sd in enumerate(gff.struct_defs):
        if sd.type_fourcc == target:
            return i
    raise ValueError(f"struct {fourcc!r} not found in GFF")

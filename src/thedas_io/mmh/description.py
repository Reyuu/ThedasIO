from pathlib import Path

from ..gff40.description import GFFReader, GFFWriter, label_name

# mmh layout: same gff header as msh, root is def 0 at data offset 0
# kids walk 6999 lists, unknown defs/labels stay in the tree untouched

FILE_TYPE = b"MMH "
L_NAME = 6000
L_CHILDREN = 6999


def _kind_name(fourcc):
    return fourcc.to_bytes(4, "little").decode("ascii")


class Node:
    def __init__(self, kind, name, values, children):
        self.kind = kind
        self.name = name
        self.values = values
        self.children = children

    def __repr__(self):
        return f"Node({self.kind!r}, {self.name!r}, {len(self.children)} children)"

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def find(self, name):
        for node in self.walk():
            if node.name == name:
                return node
        raise KeyError(f"Node {name!r} not found")


def load(path):
    return MMHReader(Path(path).read_bytes()).tree


class MMHReader:
    def __init__(self, data):
        self.data = data
        self.gff = None
        self.root = {}
        self.tree = None
        self.orphan_kinds = []
        self._parse()

    def _parse(self):
        gff = GFFReader(self.data)
        if gff.file_type != FILE_TYPE:
            raise ValueError(f"Invalid MMH file_type {gff.file_type!r}, expected {FILE_TYPE!r}")

        self.gff = gff
        self.root = gff.get_from_data_block(0, 0)
        visited = set()
        self.tree = self._node(0, self.root, visited)

        orphans = []
        for i in range(gff.nstruct):
            if i not in visited:
                orphans.append(_kind_name(gff.struct_defs[i].type_fourcc))
        self.orphan_kinds = orphans

    def _node(self, struct_index, values, visited):
        kind = _kind_name(self.gff.struct_defs[struct_index].type_fourcc)  # type: ignore
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

        # trsl/rota live in child structs, fold them up
        for child in children:
            if child.kind == "trsl" and 6047 in child.values and 6047 not in values:
                values[6047] = child.values[6047]
            elif child.kind == "rota" and 6048 in child.values and 6048 not in values:
                values[6048] = child.values[6048]

        visited.add(struct_index)
        return Node(kind, name, values, children)


class MMHWriter:
    def __init__(self, reader):
        if not isinstance(reader, MMHReader):
            raise TypeError(f"need a MMHReader, got {type(reader)}")
        self._gff = GFFWriter.from_reader(reader.gff)

    def write(self):
        return self._gff.write()

import struct
from pathlib import Path

from ..gff40.description import (
    FIELD_SIZE,
    FLAGS,
    HEADER_SIZE,
    STRUCT_DEF_SIZE,
    GFFField,
    GFFReader,
    GFFStructDef,
    GFFWriter,
    label_name,
)

# msh layout: [header 28B][defs n*16B][fields m*12B][gap FF*][data]
# seen on c_corspidr_3.msh: 5 defs, data at 544, 4B gap
# mesh: 2/name, 8021 chunks, 8022/8023 blobs, 8032/8033 u32s
# chnk: 2/name, 8000-8009/8034 cells, 8011 strms, 8020 bnds, 8025 decls
# decl: 8026 stream, 8027 offset, 8028 type, 8029 usage, 8030 idx, 8031 method

FILE_TYPE = b"MESH"

FOURCC_MESH = int.from_bytes(b"mesh", "little")
FOURCC_DECL = int.from_bytes(b"decl", "little")
FOURCC_BNDS = int.from_bytes(b"bnds", "little")
FOURCC_STRM = int.from_bytes(b"strm", "little")
FOURCC_CHNK = int.from_bytes(b"chnk", "little")
KNOWN_FOURCCS = frozenset({FOURCC_MESH, FOURCC_DECL, FOURCC_BNDS, FOURCC_STRM, FOURCC_CHNK})

# mesh root labels
L_NAME = 2
L_CHUNKS = 8021
L_VERTEX_DATA = 8022
L_INDEX_DATA = 8023
L_INDEX_FORMAT = 8032
L_INSTANCED_STREAM = 8033
# chnk labels
L_CHUNK_BOUNDS = 8020
L_CHUNK_STREAMS = 8011
L_CHUNK_DECLARATORS = 8025
L_CHUNK_CELLS = tuple(range(8000, 8010)) + (8034,)
# decl row labels
L_DECL_STREAM = 8026
L_DECL_OFFSET = 8027
L_DECL_TYPE = 8028
L_DECL_USAGE = 8029
L_DECL_USAGE_INDEX = 8030
L_DECL_METHOD = 8031
_DECL_LABELS = (
    L_DECL_STREAM,
    L_DECL_OFFSET,
    L_DECL_TYPE,
    L_DECL_USAGE,
    L_DECL_USAGE_INDEX,
    L_DECL_METHOD,
)

# bnds labels
L_BOUNDS_MIN = 8017
L_BOUNDS_MAX = 8018
L_BOUNDS_SPHERE = 8019


# https://datoolset.net/wiki/MSH Constants
DECLTYPE = {
    0: ("FLOAT1", 4),
    1: ("FLOAT2", 8),
    2: ("FLOAT3", 12),
    3: ("FLOAT4", 16),
    4: ("D3DCOLOR", 4),
    5: ("UBYTE4", 4),
    6: ("SHORT2", 4),
    7: ("SHORT4", 8),
    8: ("UBYTE4N", 4),
    9: ("SHORT2N", 4),
    10: ("SHORT4N", 8),
    11: ("USHORT2N", 4),
    12: ("USHORT4N", 8),
    13: ("UDEC3", 4),
    14: ("DEC3N", 4),
    15: ("FLOAT16_2", 4),
    16: ("FLOAT16_4", 8),
    17: ("UNUSED", 0),
}
DECLUSAGE = {
    0: "POSITION",
    1: "BLENDWEIGHT",
    2: "BLENDINDICES",
    3: "NORMAL",
    4: "PSIZE",
    5: "TEXCOORD",
    6: "TANGENT",
    7: "BINORMAL",
    8: "TESSFACTOR",
    9: "POSITIONT",
    10: "COLOR",
    11: "FOG",
    12: "DEPTH",
    13: "SAMPLE",
}
DECLMETHOD = {
    0: "DEFAULT",
    1: "PARTIALU",
    2: "PARTIALV",
    3: "CROSSUV",
    4: "UV",
    5: "LOOKUP",
    6: "LOOKUPPRESAMPLED",
}


class Declarator:
    def __init__(self, row):
        missing = []
        for l in _DECL_LABELS:
            if l == L_DECL_METHOD:
                continue  # DA2 omits the method cell
            if l not in row:
                missing.append(label_name(l))
        if missing:
            raise ValueError(f"declarator row missing labels {missing}")

        self.stream = row[L_DECL_STREAM]
        self.offset = row[L_DECL_OFFSET]
        self.dtype = row[L_DECL_TYPE]
        self.usage = row[L_DECL_USAGE]
        self.usage_index = row[L_DECL_USAGE_INDEX]
        # DA2 declarator rows omit the method cell (8031); default to DEFAULT
        self.method = row.get(L_DECL_METHOD, 0)

    @property
    def is_terminator(self):
        # D3DDECL_END row. 8026 is typed INT in our schema and DWORD in others,
        # so the terminator arrives as either -1 or 0xFFFFFFFF; it is all ones
        # either way. Missing one folds a dead row into Chunk.stride.
        return self.stream == -1 or self.stream == 0xFFFFFFFF

    @property
    def type_name(self):
        entry = DECLTYPE.get(self.dtype)
        if entry:
            return entry[0]
        return f"DECLTYPE_{self.dtype}"

    @property
    def type_size(self):
        entry = DECLTYPE.get(self.dtype)
        if entry:
            return entry[1]
        return None

    @property
    def usage_name(self):
        return DECLUSAGE.get(self.usage, f"DECLUSAGE_{self.usage}")

    @property
    def method_name(self):
        return DECLMETHOD.get(self.method, f"DECLMETHOD_{self.method}")


class Chunk:
    def __init__(self, data):
        self.name = data.get(L_NAME)
        self.cells = {}
        for label in L_CHUNK_CELLS:
            if label in data:
                self.cells[label] = data[label]

        self.declarators = []
        for r in data.get(L_CHUNK_DECLARATORS) or []:
            self.declarators.append(Declarator(r))

        self.bounds = data.get(L_CHUNK_BOUNDS)
        self.additional_streams = data.get(L_CHUNK_STREAMS)

    @property
    def stride(self):
        ends = []
        for d in self.declarators:
            if d.is_terminator:
                continue
            s = d.type_size
            if s is not None:
                ends.append(d.offset + s)

        if not ends:
            return 0
        return max(ends)


class Mesh:
    def __init__(self, root):
        self.name = root.get(L_NAME)
        self.chunks = []
        for c in root.get(L_CHUNKS) or []:
            self.chunks.append(Chunk(c))

        self.vertex_data = root.get(L_VERTEX_DATA) or []
        self.index_data = root.get(L_INDEX_DATA) or []
        self.index_format = root.get(L_INDEX_FORMAT)
        self.instanced_stream = root.get(L_INSTANCED_STREAM)

    def get_chunk(self, index):
        if index < 0 or index >= len(self.chunks):
            raise ValueError(f"chunk index {index} out of range")
        return self.chunks[index]


def load(path):
    return MSHReader(Path(path).read_bytes()).mesh


NULL_REF = 0xFFFFFFFF

# fixed schema, checked against c_corspidr_3.msh
# def order matters, struct ids index into it
MESH_STRUCT_SIZE = 24
DECL_STRUCT_SIZE = 24
BNDS_STRUCT_SIZE = 48
STRM_STRUCT_SIZE = 20
CHNK_STRUCT_SIZE = 112
_SCHEMA = (
    (
        FOURCC_MESH,
        MESH_STRUCT_SIZE,
        (
            (L_NAME, 14, FLAGS["NONE"], 0),
            (L_CHUNKS, 4, FLAGS["LIST_OF_REFS"], 8),
            (L_VERTEX_DATA, 0, FLAGS["SCALAR_LIST"], 12),
            (L_INDEX_DATA, 0, FLAGS["SCALAR_LIST"], 16),
            (L_INDEX_FORMAT, 4, FLAGS["NONE"], 4),
            (L_INSTANCED_STREAM, 0, FLAGS["NONE"], 20),
        ),
    ),
    (
        FOURCC_DECL,
        DECL_STRUCT_SIZE,
        (
            (L_DECL_STREAM, 5, FLAGS["NONE"], 0),
            (L_DECL_OFFSET, 5, FLAGS["NONE"], 4),
            (L_DECL_TYPE, 4, FLAGS["NONE"], 8),
            (L_DECL_USAGE, 4, FLAGS["NONE"], 12),
            (L_DECL_USAGE_INDEX, 4, FLAGS["NONE"], 16),
            (L_DECL_METHOD, 4, FLAGS["NONE"], 20),
        ),
    ),
    (
        FOURCC_BNDS,
        BNDS_STRUCT_SIZE,
        (
            (L_BOUNDS_MIN, 12, FLAGS["NONE"], 0),
            (L_BOUNDS_MAX, 12, FLAGS["NONE"], 16),
            (L_BOUNDS_SPHERE, 12, FLAGS["NONE"], 32),
        ),
    ),
    (
        FOURCC_STRM,
        STRM_STRUCT_SIZE,
        (
            (8012, 4, FLAGS["NONE"], 4),
            (8013, 4, FLAGS["NONE"], 8),
            (8014, 4, FLAGS["NONE"], 12),
            (8015, 0, FLAGS["NONE"], 16),
            (8016, 0, FLAGS["NONE"], 17),
            (8024, 0, FLAGS["SCALAR_LIST"], 0),
        ),
    ),
    (
        FOURCC_CHNK,
        CHNK_STRUCT_SIZE,
        (
            (L_NAME, 14, FLAGS["NONE"], 48),
            *((label, 4, FLAGS["NONE"], 52 + 4 * i) for i, label in enumerate(range(8000, 8010))),
            (L_CHUNK_STREAMS, 3, FLAGS["STRUCT_ARRAY"], 92),
            (L_CHUNK_BOUNDS, 2, FLAGS["STRUCT"], 0),
            (L_CHUNK_DECLARATORS, 1, FLAGS["STRUCT_ARRAY"], 96),
            (8034, 4, FLAGS["NONE"], 100),
        ),
    ),
)


class MSHReader:
    def __init__(self, data):
        self.data = data
        self.gff = None
        self.root = {}
        self.mesh = None
        self.unknown_fourccs = []
        self._parse()

    def _parse(self):
        gff = GFFReader(self.data)
        if gff.file_type != FILE_TYPE:
            raise ValueError(f"Invalid MSH file_type {gff.file_type!r}, expected {FILE_TYPE!r}")

        # Struct kinds outside the schema are carried, not refused: the def
        # table is walked generically and the writer clones it, so a new kind in
        # a patch must load and survive export. Refusing it here made one
        # unknown fourcc -- even an unused one -- unreadable.
        self.unknown_fourccs = sorted(
            f.to_bytes(4, "little").decode("ascii", "replace")
            for f in {sd.type_fourcc for sd in gff.struct_defs}
            if f not in KNOWN_FOURCCS
        )

        gff.get_struct(FOURCC_MESH)
        self.gff = gff
        # root lives at data offset 0
        self.root = gff.get_from_data_block(0, 0)
        self.mesh = Mesh(self.root)

    @property
    def name(self):
        return self.root.get(L_NAME)  # type: ignore

    @property
    def chunks(self):
        value = self.root.get(L_CHUNKS)  # type: ignore
        if value is None:
            return []
        if not isinstance(value, list):
            raise TypeError(f"expected list for {label_name(L_CHUNKS)}")
        return value

    def get_chunk(self, index):
        chunks = self.chunks
        if index < 0 or index >= len(chunks):
            raise ValueError(f"chunk index {index} out of range")
        return chunks[index]

    def get_declarators(self, index=0):
        chunk = self.get_chunk(index)
        value = chunk.get(L_CHUNK_DECLARATORS)
        if value is None:
            return []
        if not isinstance(value, list):
            raise TypeError(f"expected list for {label_name(L_CHUNK_DECLARATORS)}")
        return value

    @property
    def decls(self):
        return self.get_declarators(0)

    @property
    def vertex_data(self):
        return self.root.get(L_VERTEX_DATA) or []  # type: ignore

    @property
    def index_data(self):
        return self.root.get(L_INDEX_DATA) or []  # type: ignore

    @property
    def bounds(self):
        return self.root.get(L_INDEX_FORMAT)  # type: ignore

    @property
    def streams(self):
        return self.root.get(L_INSTANCED_STREAM)  # type: ignore


def _field_offsets(struct_def, labels):
    # data_index of each label inside a struct instance, missing ones skipped
    wanted = set(labels)
    out = {}
    for f in struct_def.field_instances:
        if f.label_id in wanted:
            out[f.label_id] = f.data_index
    return out


def _chunk_unchanged(a, orig) -> bool:
    if a.additional_streams is not None:
        return False
    decls = [(d.stream, d.offset, d.dtype, d.usage, d.usage_index, d.method) for d in a.declarators]
    name, bounds, cells, decls0 = orig
    if a.name != name or a.bounds != bounds or decls != decls0:
        return False
    return all(a.cells.get(label) == cells.get(label) for label in L_CHUNK_CELLS)


def _chunk_list(gff: GFFReader):
    # (flag field, block offset, element struct size, row count) for 8021.
    # 8021 is a LIST_OF_REFS in the shipped files and a STRUCT_ARRAY in
    # hand-built ones; the two have different layouts and reading one as the
    # other puts the chunk bases hundreds of kilobytes off.
    root = gff.struct_defs[0]
    field = next((f for f in root.field_instances if f.label_id == L_CHUNKS), None)
    if field is None:
        return None, 0, 0, 0
    ref = struct.unpack_from("<I", gff.data, gff.data_offset + field.data_index)[0]
    (count,) = struct.unpack_from("<I", gff.data, gff.data_offset + ref)
    return field, ref, gff.struct_defs[field.type_id].struct_size, count


def _chunk_bases(gff: GFFReader) -> list:
    # data-block offsets of the root's chunk instances, in file order
    field, ref, size, count = _chunk_list(gff)
    if field is None:
        return []
    if field.flags == FLAGS["LIST_OF_REFS"]:
        out = []
        for i in range(count):
            (r,) = struct.unpack_from("<I", gff.data, gff.data_offset + ref + 4 + 4 * i)
            if r != 0xFFFFFFFF:
                out.append(r)
        return out
    return [ref + 4 + i * size for i in range(count)]


class MSHWriter:
    PLATFORM = b"PC  "
    VERSION = b"V0.1"

    def __init__(self, mesh, reader=None):
        if not isinstance(mesh, Mesh):
            raise TypeError(f"need a Mesh, got {type(mesh)}")
        self.mesh = mesh
        self._reader = reader
        self._orig = None
        if reader is not None:
            src = reader.mesh
            self._orig = {
                "name": src.name,
                "index_format": src.index_format,
                "vertex": list(src.vertex_data),
                "index": list(src.index_data),
                "chunks": [
                    (
                        c.name,
                        c.bounds,
                        dict(c.cells),
                        [
                            (d.stream, d.offset, d.dtype, d.usage, d.usage_index, d.method)
                            for d in c.declarators
                        ],
                    )
                    for c in src.chunks
                ],
            }

    @classmethod
    def from_reader(cls, reader):
        return cls(reader.mesh, reader)

    @classmethod
    def from_bytes(cls, data):
        reader = MSHReader(data)
        return cls(reader.mesh, reader)

    def write(self):
        if self._reader is not None:
            return self._patched()
        return self._rebuilt()

    def _patched(self) -> bytes:
        # clone-then-patch: only what actually changed is written, so an
        # untouched mesh comes back the same size it went in
        gff = self._reader.gff
        w = GFFWriter.from_reader(gff)
        mesh = self.mesh
        orig = self._orig
        at = _field_offsets(
            gff.struct_defs[0],
            (L_NAME, L_CHUNKS, L_VERTEX_DATA, L_INDEX_DATA, L_INDEX_FORMAT, L_INSTANCED_STREAM),
        )

        if L_NAME in at and mesh.name != orig["name"]:
            value = NULL_REF
            if mesh.name is not None:
                value = w.alloc_ecstring(mesh.name)
            struct.pack_into("<I", w.data_block, at[L_NAME], value)
        if L_INDEX_FORMAT in at and (mesh.index_format or 0) != (orig["index_format"] or 0):
            struct.pack_into("<I", w.data_block, at[L_INDEX_FORMAT], mesh.index_format or 0)
        if L_INSTANCED_STREAM in at:
            w.data_block[at[L_INSTANCED_STREAM]] = mesh.instanced_stream or 0
        if L_VERTEX_DATA in at and list(mesh.vertex_data) != orig["vertex"]:
            ref = w.alloc_scalar_list(0, list(mesh.vertex_data))
            struct.pack_into("<I", w.data_block, at[L_VERTEX_DATA], ref)
        if L_INDEX_DATA in at and list(mesh.index_data) != orig["index"]:
            ref = w.alloc_scalar_list(0, list(mesh.index_data))
            struct.pack_into("<I", w.data_block, at[L_INDEX_DATA], ref)

        chnk_def = None
        if L_CHUNKS in at:
            type_id = next(
                f.type_id for f in gff.struct_defs[0].field_instances if f.label_id == L_CHUNKS
            )
            chnk_def = gff.struct_defs[type_id]
        offsets = {}
        size = 0
        if chnk_def is not None:
            offsets = _field_offsets(
                chnk_def, (L_NAME, L_CHUNK_DECLARATORS, L_CHUNK_STREAMS, 8034, *L_CHUNK_CELLS)
            )
            size = chnk_def.struct_size

        bases = _chunk_bases(gff)
        refs = []
        for i, chunk in enumerate(mesh.chunks):
            if (
                i < len(bases)
                and i < len(orig["chunks"])
                and _chunk_unchanged(chunk, orig["chunks"][i])
            ):
                refs.append(bases[i])
                continue
            if i < len(bases):
                # rewrite the file's own instance, keeping any bytes the schema
                # does not model
                self._check_chunk(chunk)
                inst = bytearray(w.data_block[bases[i] : bases[i] + size])
                self._fill_chunk(w, chunk, inst, offsets)
                w.data_block[bases[i] : bases[i] + size] = inst
                refs.append(bases[i])
            else:
                refs.append(w.alloc(self._chunk_bytes(w, chunk)))
        if L_CHUNKS in at and (len(refs) != len(bases) or refs != bases):
            field, ref, _size, _count = _chunk_list(gff)
            if field is not None and field.flags != FLAGS["LIST_OF_REFS"]:
                # the instances are inline and contiguous, so a different count
                # cannot be laid out by patching; rebuild instead
                return self._rebuilt()
            blob = struct.pack("<I", len(refs)) + b"".join(struct.pack("<I", r) for r in refs)
            struct.pack_into("<I", w.data_block, at[L_CHUNKS], w.alloc(blob))
        return w.write()

    def _rebuilt(self) -> bytes:
        w = GFFWriter(self.PLATFORM, FILE_TYPE, self.VERSION)
        for fourcc, size, fields in _SCHEMA:
            sd = GFFStructDef(fourcc, len(fields), 0, size)
            for label, type_id, flags, index in fields:
                sd.field_instances.append(GFFField(label, type_id, flags, index))
            w.struct_defs.append(sd)
        total_fields = sum(len(fields) for _, _, fields in _SCHEMA)
        fields_end = HEADER_SIZE + len(_SCHEMA) * STRUCT_DEF_SIZE + total_fields * FIELD_SIZE
        w.gap = b"\xff" * (-fields_end % 16)

        mesh = self.mesh
        w.alloc(b"\x00" * MESH_STRUCT_SIZE)
        name_ref = NULL_REF if mesh.name is None else w.alloc_ecstring(mesh.name)
        chunk_refs = [self._write_chunk(w, c) for c in mesh.chunks]
        chunks_ref = w.alloc(
            struct.pack("<I", len(chunk_refs)) + b"".join(struct.pack("<I", r) for r in chunk_refs)
        )
        vertex_ref = w.alloc_scalar_list(0, list(mesh.vertex_data))
        index_ref = w.alloc_scalar_list(0, list(mesh.index_data))

        inst = bytearray(b"\xff" * MESH_STRUCT_SIZE)
        struct.pack_into("<I", inst, 0, name_ref)
        struct.pack_into("<I", inst, 4, mesh.index_format or 0)
        struct.pack_into("<I", inst, 8, chunks_ref)
        struct.pack_into("<I", inst, 12, vertex_ref)
        struct.pack_into("<I", inst, 16, index_ref)
        inst[20] = mesh.instanced_stream or 0
        w.data_block[0:MESH_STRUCT_SIZE] = inst
        return w.write()

    @staticmethod
    def _check_chunk(chunk):
        if chunk.bounds is None:
            raise ValueError(
                f"Chunk {chunk.name!r} has no bounds (chnk 8020 is inline, never NULL)"
            )
        if chunk.additional_streams is not None:
            raise NotImplementedError(
                "strm instances (chnk 8011) have no observed samples to encode against"
            )
        try:
            for label in (L_BOUNDS_MIN, L_BOUNDS_MAX, L_BOUNDS_SPHERE):
                struct.pack("<4f", *chunk.bounds[label])
        except (KeyError, struct.error) as e:
            raise ValueError(f"chunk {chunk.name!r} has bad bounds: {e}")

    @staticmethod
    def _declarator_bytes(chunk) -> bytes:
        try:
            rows = []
            for d in chunk.declarators:
                # the terminator is all ones; write it as -1 so it fits the
                # signed int the schema declares, whatever the source spelled it
                stream = -1 if d.is_terminator else d.stream
                rows.append(
                    struct.pack(
                        "<iiIIII", stream, d.offset, d.dtype, d.usage, d.usage_index, d.method
                    )
                )
            return struct.pack("<I", len(rows)) + b"".join(rows)
        except struct.error as e:
            raise ValueError(f"Chunk {chunk.name!r} has an unencodable declarator: {e}")

    def _chunk_bytes(self, w, chunk) -> bytes:
        # a whole chunk instance, laid out from the schema offsets
        self._check_chunk(chunk)
        inst = bytearray(b"\xff" * CHNK_STRUCT_SIZE)

        bounds = b""
        for label in (L_BOUNDS_MIN, L_BOUNDS_MAX, L_BOUNDS_SPHERE):
            bounds += struct.pack("<4f", *chunk.bounds[label])
        if len(inst) >= BNDS_STRUCT_SIZE:
            inst[0:BNDS_STRUCT_SIZE] = bounds

        decl_ref = w.alloc(self._declarator_bytes(chunk))
        name_ref = NULL_REF
        if chunk.name is not None:
            name_ref = w.alloc_ecstring(chunk.name)
        struct.pack_into("<I", inst, 48, name_ref)
        for i, label in enumerate(range(8000, 8010)):
            struct.pack_into("<I", inst, 52 + 4 * i, chunk.cells.get(label, 0))
        struct.pack_into("<I", inst, 92, NULL_REF)
        struct.pack_into("<I", inst, 96, decl_ref)
        struct.pack_into("<I", inst, 100, chunk.cells.get(8034, 0))
        return bytes(inst)

    def _fill_chunk(self, w, chunk, inst: bytearray, offsets: dict) -> None:
        # patch only the fields the schema models, by label, so a file that
        # lays its chnk out differently still writes correctly
        self._check_chunk(chunk)
        for label in (L_BOUNDS_MIN, L_BOUNDS_MAX, L_BOUNDS_SPHERE):
            if label in offsets:
                struct.pack_into("<4f", inst, offsets[label], *chunk.bounds[label])
        if L_NAME in offsets:
            value = NULL_REF
            if chunk.name is not None:
                value = w.alloc_ecstring(chunk.name)
            struct.pack_into("<I", inst, offsets[L_NAME], value)
        for label in L_CHUNK_CELLS:
            if label in offsets:
                struct.pack_into("<I", inst, offsets[label], chunk.cells.get(label, 0))
        if 8034 in offsets:
            struct.pack_into("<I", inst, offsets[8034], chunk.cells.get(8034, 0))
        if L_CHUNK_STREAMS in offsets:
            # strms is never carried (see _check_chunk); leaving the source
            # bytes here would keep a garbage count the reader then chokes on
            struct.pack_into("<I", inst, offsets[L_CHUNK_STREAMS], NULL_REF)
        if L_CHUNK_DECLARATORS in offsets:
            ref = w.alloc(self._declarator_bytes(chunk))
            struct.pack_into("<I", inst, offsets[L_CHUNK_DECLARATORS], ref)

    def _write_chunk(self, w, chunk):
        return w.alloc(self._chunk_bytes(w, chunk))

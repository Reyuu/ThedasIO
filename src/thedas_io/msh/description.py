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
            if l not in row:
                missing.append(label_name(l))
        if missing:
            raise ValueError(f"declarator row missing labels {missing}")

        self.stream = row[L_DECL_STREAM]
        self.offset = row[L_DECL_OFFSET]
        self.dtype = row[L_DECL_TYPE]
        self.usage = row[L_DECL_USAGE]
        self.usage_index = row[L_DECL_USAGE_INDEX]
        self.method = row[L_DECL_METHOD]

    @property
    def is_terminator(self):
        # D3DDECL_END row
        return self.stream == -1

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
        self._parse()

    def _parse(self):
        gff = GFFReader(self.data)
        if gff.file_type != FILE_TYPE:
            raise ValueError(f"Invalid MSH file_type {gff.file_type!r}, expected {FILE_TYPE!r}")

        seen = set()
        for sd in gff.struct_defs:
            seen.add(sd.type_fourcc)
        unknown = []
        for f in seen:
            if f not in KNOWN_FOURCCS:
                unknown.append(f.to_bytes(4, "little").decode("ascii", "replace"))
        if unknown:
            raise ValueError(f"unknown struct kinds {sorted(unknown)}")

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


class MSHWriter:
    PLATFORM = b"PC  "
    VERSION = b"V0.1"

    def __init__(self, mesh):
        if not isinstance(mesh, Mesh):
            raise TypeError(f"need a Mesh, got {type(mesh)}")
        self.mesh = mesh

    @classmethod
    def from_reader(cls, reader):
        return cls(reader.mesh)

    @classmethod
    def from_bytes(cls, data):
        return cls(MSHReader(data).mesh)

    def write(self):
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
    def _write_chunk(w, chunk):
        if chunk.bounds is None:
            raise ValueError(
                f"Chunk {chunk.name!r} has no bounds (chnk 8020 is inline, never NULL)"
            )
        if chunk.additional_streams is not None:
            raise NotImplementedError(
                "strm instances (chnk 8011) have no observed samples to encode against"
            )

        try:
            bounds = b""
            for l in (L_BOUNDS_MIN, L_BOUNDS_MAX, L_BOUNDS_SPHERE):
                bounds += struct.pack("<4f", *chunk.bounds[l])
        except (KeyError, struct.error) as e:
            raise ValueError(f"chunk {chunk.name!r} has bad bounds: {e}")

        if chunk.name is None:
            name_ref = NULL_REF
        else:
            name_ref = w.alloc_ecstring(chunk.name)

        try:
            decls = struct.pack("<I", len(chunk.declarators)) + b"".join(
                struct.pack(
                    "<iiIIII", d.stream, d.offset, d.dtype, d.usage, d.usage_index, d.method
                )
                for d in chunk.declarators
            )
        except struct.error as e:
            raise ValueError(f"Chunk {chunk.name!r} has an unencodable declarator: {e}")
        decl_ref = w.alloc(decls)

        inst = bytearray(b"\xff" * CHNK_STRUCT_SIZE)
        inst[0:BNDS_STRUCT_SIZE] = bounds
        struct.pack_into("<I", inst, 48, name_ref)
        for i, label in enumerate(range(8000, 8010)):
            struct.pack_into("<I", inst, 52 + 4 * i, chunk.cells.get(label, 0))
        struct.pack_into("<I", inst, 92, NULL_REF)
        struct.pack_into("<I", inst, 96, decl_ref)
        struct.pack_into("<I", inst, 100, chunk.cells.get(8034, 0))
        return w.alloc(bytes(inst))

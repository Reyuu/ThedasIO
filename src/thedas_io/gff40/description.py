import bisect
import struct

from .labels import DATA as LABELS

# gff v4.0: [header 28B][defs n*16B][fields m*12B][gap FF*][data]
# header: magic, platform, file_type, version, nstruct, data_offset
# gap len isn't stored, derive it from data_offset
# struct-def: <IIII kind, field_count, field_offset, struct_size
# kind is a fourcc, LE bytes = ascii (mdlh node mshh trsl rota mesh chnk etc)
# field: <IHHI label, type, flags, index
# plain scalar -> cell offset inside the instance
# list/struct/ref -> offset of a u32 ref cell holding a data-block offset or NULL_REF
# flags: 0x8000 list, 0x4000 struct, 0x2000 ref,
# 0xFFFF generic list, 0xFFFFFFFF NULL_REF
# type ids (no #11): 0:1 1:1 2:2 3:2 4:4 5:4 6:8 7:8 8:4 9:8
# 10:12 12:16 13:16 14:4 15:16 16:64 17:8

MAGIC = b"GFF V4.0"
HEADER_SIZE = 28
STRUCT_DEF_SIZE = 16
FIELD_SIZE = 12
FIELD_TYPES = {
    0: 1,
    1: 1,
    2: 2,
    3: 2,
    4: 4,
    5: 4,
    6: 8,
    7: 8,
    8: 4,
    9: 8,
    10: 12,
    12: 16,
    13: 16,
    14: 4,
    15: 16,
    16: 64,
    17: 8,
}
_SCALAR_FORMATS = {
    0: "B",
    1: "b",
    2: "H",
    3: "h",
    4: "I",
    5: "i",
    6: "Q",
    7: "q",
    8: "f",
    9: "d",
    10: "3f",
    12: "4f",
    13: "4f",
    15: "4f",
    16: "16f",
    17: "2I",
}
FLAGS = {
    "NONE": 0x0000,
    "REFERENCE": 0x2000,
    "STRUCT": 0x4000,
    "SCALAR_LIST": 0x8000,
    "STRUCT_ARRAY": 0xC000,
    "LIST_OF_REFS": 0xE000,
    "GENERIC_LIST": 0xA000,
}


def label_name(label_id):
    if label_id in LABELS:
        return LABELS[label_id]
    return f"label_{label_id}"


def _unpack_items(fmt, buf):
    out = []
    for v in struct.iter_unpack(fmt, buf):
        if len(v) == 1:
            out.append(v[0])
        else:
            out.append(v)
    return out


class GFFStructDef:
    def __init__(self, type_id, field_count, field_offset, struct_size):
        self.type_fourcc = type_id
        self.field_count = field_count
        self.field_offset = field_offset
        self.struct_size = struct_size
        self.field_instances = []


class GFFField:
    def __init__(self, label_id, type_id, flags, data_index):
        self.label_id = label_id
        self.label_name = label_name(label_id)
        self.type_id = type_id
        try:
            self.type_size = FIELD_TYPES[self.type_id]
        except KeyError:
            if self.type_id == 0xFFFF:
                self.type_size = -1
            else:
                raise ValueError(f"unknown type {self.type_id} in GFFField")

        self.flags = flags
        self.data_index = data_index

    def pack(self):
        return struct.pack("<IHHI", self.label_id, self.type_id, self.flags, self.data_index)


class GFFReader:
    def __init__(self, data):
        self.platform = b""
        self.file_type = b""
        self.version = b""
        self.data_offset = 0
        self.nstruct = 0
        self.data = data
        self.struct_defs = []
        self.field_instances_offset = 0
        self.field_instances_offset_end = 0
        self.gap = 0
        self._parse()

    def _parse(self):
        magic = self.data[0:8]
        if magic != MAGIC:
            raise ValueError("Invalid GFF file magic")

        self.platform, self.file_type, self.version, self.nstruct, self.data_offset = struct.unpack(
            "<4s4s4sII", self.data[len(MAGIC) : HEADER_SIZE]
        )

        global_field_count = 0
        for i in range(self.nstruct):
            entry_offset = HEADER_SIZE + i * STRUCT_DEF_SIZE
            entry_data = self.data[entry_offset : entry_offset + STRUCT_DEF_SIZE]
            type_id, field_count, field_offset, struct_size = struct.unpack("<IIII", entry_data)
            struct_def = GFFStructDef(type_id, field_count, field_offset, struct_size)
            global_field_count += field_count
            for j in range(field_count):
                label, type_, flags, index = struct.unpack_from(
                    "<IHHI", self.data, field_offset + j * FIELD_SIZE
                )
                field = GFFField(label, type_, flags, index)
                struct_def.field_instances.append(field)

            self.struct_defs.append(struct_def)

        self.field_instances_offset = HEADER_SIZE + self.nstruct * STRUCT_DEF_SIZE
        self.field_instances_offset_end = (
            self.field_instances_offset + global_field_count * FIELD_SIZE
        )
        self.gap = self.data_offset - self.field_instances_offset_end

    def get_struct(self, type_fourcc):
        for entry in self.struct_defs:
            if entry.type_fourcc == type_fourcc:
                return entry
        raise ValueError(f"struct {type_fourcc} not found")

    def get_from_data_block(self, struct_ref, instance_base=0, label_id=None, occurrence=0):
        def struct_def(ref):
            if isinstance(ref, GFFStructDef):
                return ref
            if ref < 0 or ref >= self.nstruct:
                raise ValueError(f"struct index {ref} out of range")
            return self.struct_defs[ref]

        def raw(block_offset, length):
            if block_offset < 0 or block_offset + length > len(self.data) - self.data_offset:
                raise ValueError("data block read out of range")
            a = self.data_offset + block_offset
            return self.data[a : a + length]

        def ecstring(block_offset):
            if block_offset == 0xFFFFFFFF:
                return None
            count = struct.unpack_from("<I", self.data, self.data_offset + block_offset)[0]
            text = raw(block_offset + 4, count * 2).decode("utf-16-le")
            if text.endswith("\x00"):
                return text[:-1]
            return text

        def instance(sd, base):
            if base < 0 or base + sd.struct_size > len(self.data) - self.data_offset:
                raise ValueError("instance out of range")
            output = {}
            for f in sd.field_instances:
                value = field(sd, base, f)
                if f.label_id in output:
                    current = output[f.label_id]
                    if isinstance(current, list):
                        output[f.label_id] = current + [value]
                    else:
                        output[f.label_id] = [current, value]
                else:
                    output[f.label_id] = value
            return output

        def field(sd, instance_base, f):
            if f.flags == FLAGS["NONE"]:
                if f.type_id == 14:
                    (ref,) = struct.unpack_from(
                        "<I", self.data, self.data_offset + instance_base + f.data_index
                    )
                    return ecstring(ref)
                return self.decode_scalar(f, instance_base)

            if f.flags == FLAGS["STRUCT"]:
                return instance(struct_def(f.type_id), instance_base + f.data_index)

            (ref,) = struct.unpack_from(
                "<I", self.data, self.data_offset + instance_base + f.data_index
            )
            if ref == 0xFFFFFFFF:
                return None

            if f.flags == FLAGS["SCALAR_LIST"]:
                (n,) = struct.unpack_from("<I", self.data, self.data_offset + ref)
                if f.type_id == 14:
                    return [ecstring(r) for (r,) in struct.iter_unpack("<I", raw(ref + 4, n * 4))]
                suffix = _SCALAR_FORMATS.get(f.type_id)
                if suffix is None:
                    raise ValueError(
                        f"Unknown list type {f.type_id} (label {f.label_id} -> {f.label_name})"
                    )
                fmt = f"<{suffix}"
                return _unpack_items(fmt, raw(ref + 4, n * struct.calcsize(fmt)))

            if f.flags == FLAGS["STRUCT_ARRAY"]:
                sub_struct = struct_def(f.type_id)
                (n,) = struct.unpack_from("<I", self.data, self.data_offset + ref)
                return [
                    instance(sub_struct, ref + 4 + i * sub_struct.struct_size) for i in range(n)
                ]

            if f.flags == FLAGS["LIST_OF_REFS"]:
                sub_struct = struct_def(f.type_id)
                (n,) = struct.unpack_from("<I", self.data, self.data_offset + ref)
                out = []
                for (r,) in struct.iter_unpack("<I", raw(ref + 4, n * 4)):
                    if r == 0xFFFFFFFF:
                        out.append(None)
                    else:
                        out.append(instance(sub_struct, r))
                return out

            if f.type_id == 0xFFFF:
                if f.flags == FLAGS["GENERIC_LIST"]:
                    (n,) = struct.unpack_from("<I", self.data, self.data_offset + ref)
                    out = []
                    for s, fl, off in struct.iter_unpack("<HHI", raw(ref + 4, n * 8)):
                        out.append(
                            {
                                "struct_index": s,
                                "flags": fl,
                                "offset": off,
                                "value": None
                                if off == 0xFFFFFFFF
                                else instance(struct_def(s), off),
                            }
                        )
                    return out
                if f.flags == FLAGS["REFERENCE"]:
                    s, fl, off = struct.unpack_from("<HHI", self.data, self.data_offset + ref)
                    return {
                        "struct_index": s,
                        "flags": fl,
                        "offset": off,
                        "value": None if off == 0xFFFFFFFF else instance(struct_def(s), off),
                    }
            elif f.flags == FLAGS["REFERENCE"]:
                return instance(struct_def(f.type_id), ref)
            raise ValueError(f"unsupported flags {f.flags:#x} for label {f.label_id}")

        sd = struct_def(struct_ref)
        if label_id is None:
            return instance(sd, instance_base)
        if isinstance(label_id, GFFField):
            return field(sd, instance_base, label_id)

        cands = []
        for f in sd.field_instances:
            if f.label_id == label_id:
                cands.append(f)
        if not cands or occurrence >= len(cands):
            raise ValueError(f"field {label_id} not in struct")
        return field(sd, instance_base, cands[occurrence])

    def decode_scalar(self, field, instance_base=0):
        if field.flags != FLAGS["NONE"]:
            raise ValueError(f"decode_scalar needs a plain scalar, got flags {field.flags:#x}")

        suffix = _SCALAR_FORMATS.get(field.type_id)
        if suffix is None:
            raise ValueError(f"not an inline scalar type {field.type_id}")

        fmt = f"<{suffix}"
        size = struct.calcsize(fmt)
        block_len = len(self.data) - self.data_offset
        cell = instance_base + field.data_index
        if instance_base < 0 or field.data_index < 0 or cell + size > block_len:
            raise ValueError("scalar cell out of range")

        start = self.data_offset + cell
        return _unpack_items(fmt, self.data[start : start + size])[0]


class GFFWriter:
    def __init__(self, platform, file_type, version, gap=b""):
        for name, value in (("platform", platform), ("file_type", file_type), ("version", version)):
            if not isinstance(value, (bytes, bytearray)) or len(value) != 4:
                raise ValueError(f"{name} must be 4 bytes, got {value!r}")
        gap = bytes(gap)
        for b in gap:
            if b != 0xFF:
                raise ValueError("gap bytes must all be 0xFF")

        self.platform = bytes(platform)
        self.file_type = bytes(file_type)
        self.version = bytes(version)
        self.gap = gap
        self.struct_defs = []
        self.data_block = bytearray()

    @classmethod
    def from_reader(cls, reader):
        w = cls(
            reader.platform,
            reader.file_type,
            reader.version,
            reader.data[reader.field_instances_offset_end : reader.data_offset],
        )

        for sd in reader.struct_defs:
            clone = GFFStructDef(sd.type_fourcc, sd.field_count, sd.field_offset, sd.struct_size)
            for f in sd.field_instances:
                clone.field_instances.append(GFFField(f.label_id, f.type_id, f.flags, f.data_index))
            w.struct_defs.append(clone)

        w.data_block = bytearray(reader.data[reader.data_offset :])
        return w

    @classmethod
    def from_bytes(cls, data):
        return cls.from_reader(GFFReader(data))

    # append raw bytes, return block offset
    def alloc(self, payload):
        if not isinstance(payload, (bytes, bytearray)):
            raise TypeError("payload must be bytes")
        offset = len(self.data_block)
        self.data_block += payload
        return offset

    # ecstring is <I count incl NUL> + utf-16le
    def alloc_ecstring(self, text):
        if not isinstance(text, str):
            raise TypeError("ecstring must be str")
        chars = f"{text}\x00".encode("utf-16-le")
        return self.alloc(struct.pack("<I", len(chars) // 2) + chars)

    # scalar list is <I n> + packed elements
    def alloc_scalar_list(self, type_id, values):
        if type_id == 14:
            refs = []
            for v in values:
                if v is None:
                    refs.append(0xFFFFFFFF)
                else:
                    refs.append(self.alloc_ecstring(v))
            return self.alloc(
                struct.pack("<I", len(refs)) + struct.pack("<" + "I" * len(refs), *refs)
            )

        suffix = _SCALAR_FORMATS.get(type_id)
        if suffix is None:
            raise ValueError(f"Not an encodable scalar-list type {type_id:#x}")

        fmt = f"<{suffix}"
        # join, blob += in a loop is quadratic on big vertex blobs
        parts = []
        try:
            for v in values:
                if isinstance(v, tuple):
                    parts.append(struct.pack(fmt, *v))
                else:
                    parts.append(struct.pack(fmt, v))
        except struct.error as e:
            raise ValueError(f"cannot encode list type {type_id}: {e}")

        body = b"".join(parts)
        return self.alloc(struct.pack("<I", len(values)) + body)

    # keep label-ascending order
    def add_field(self, struct_index, label_id, type_id, flags, data_index):
        if struct_index < 0 or struct_index >= len(self.struct_defs):
            raise ValueError(f"struct index {struct_index} out of range")

        field = GFFField(label_id, type_id, flags, data_index)
        fields = self.struct_defs[struct_index].field_instances
        ids = []
        for f in fields:
            ids.append(f.label_id)
        fields.insert(bisect.bisect_right(ids, label_id), field)

        return field

    def write(self):
        nstruct = len(self.struct_defs)
        defs_blob = bytearray()
        fields_blob = bytearray()

        # field offsets get recomputed here
        field_offset = HEADER_SIZE + nstruct * STRUCT_DEF_SIZE
        for sd in self.struct_defs:
            if sd.struct_size <= 0:
                raise ValueError(f"bad struct_size {sd.struct_size}")

            field_count = len(sd.field_instances)
            defs_blob += struct.pack(
                "<IIII", sd.type_fourcc, field_count, field_offset, sd.struct_size
            )

            for f in sd.field_instances:
                fields_blob += f.pack()
            field_offset += field_count * FIELD_SIZE

        data_offset = HEADER_SIZE + nstruct * STRUCT_DEF_SIZE + len(fields_blob) + len(self.gap)
        header = MAGIC + struct.pack(
            "<4s4s4sII", self.platform, self.file_type, self.version, nstruct, data_offset
        )
        return bytes(header + defs_blob + fields_blob + self.gap + bytes(self.data_block))

# GFF V4.0 Guide (Dragon Age: Origins, PC)

Binary, little-endian. No text variant. Walk the tables, do not index them. Never drop an unknown label.

Implementation: [src/thedas_io/gff40/description.py](src/thedas_io/gff40/description.py) (`GFFReader`, `GFFStructDef`, `GFFField`, `FLAGS`, `MAGIC`).

Plain scalars and scalar-list elements come back decoded to int/float
(single values) or tuples (vectors/matrix/TlkString).

## Layout

```
[header 28B][struct-defs n*16B][fields m*12B][gap 0xFF*][data-block]
```

Header (`description.py:81-87`): ASCII magic, then `<4s4s4sII`:
```
0-8:   b'GFF V4.0' (MAGIC, description.py:60)
8-12:  platform, e.g. b'PC  '
12-16: file_type, e.g. b'MMH ' / b'MESH' / b'PHY '
16-20: version, e.g. b'V0.1'
20-24: <I nstruct (struct-def count)
24-28: <I data_offset (absolute file offset of data-block)
data_offset = 28 + nstruct*16 + total_fields*12 + len(gap)
```

The gap length is stored nowhere — it is derived (see Gap below).
Read direction: header `data_offset` minus walked `fields_end`.
Write direction: chosen gap bytes, then `data_offset` recomputed.

Observed (`tests/test_gff40.py`):

| file | file_type | nstruct | fields_end | data_offset | gap |
| ---- | --------- | ------- | ---------- | ----------- | --- |
| `c_corspidr_3.msh` | `MESH` | 5 | 540 | 544 | 4 x `0xFF` |
| `c_corspidra_0.mmh` | `MMH ` | 35 | 3828 | 3840 | 12 x `0xFF` |
| `cai_lrgroofa_0.phy` | `PHY ` | 20 | 1944 | 1944 | empty |

Struct-def `<IIII` (`description.py:89-94`):
```
type:         u32 fourcc — 4 ASCII chars, LE bytes read straight
              (mdlh node mshh bbox trsl rota
              xprt mesh chnk strm decl bnds ...). Decode with
              type_id.to_bytes(4, "little").decode("ascii").
field_count:  u32
field_offset: u32 absolute file offset of first field. Defs start at
              28 + i*16 and their field blocks are sequential with no
              holes; a writer must recompute them, the reader trusts them.
struct_size:  u32 sizeof one instance in the data-block (never 0;
              zero-field structs use size 1, e.g. phy prsj/pipj/fxdj/cylj).
```

## Fields

Field `<IHHI`, at `field_offset + j*12`:
```
label: u32 label id (see Labels below)
type:  u16 data type id (see Type ids)
flags: u16 flag bits (see Flags)
index: u32 byte index into the instance. Plain scalar -> the cell
       itself is at instance_base + index. List/Struct/Reference ->
       a u32 ref cell is at instance_base + index; the cell's value
       is a data-block offset to the payload (or NULL_REF).
```

Verified invariants (`tests/test_gff40.py::test_struct_def_table`):
field blocks sequential, `fields_end <= data_offset`, gap all-`0xFF`.

Do not confuse the two offset kinds: `field_offset` points at
12-byte field *definitions* in the header area (e.g. msh `mesh`
`field_offset` 108 = right after the def table), while
`instance_base` points at instance *data* inside the data-block
(measured from `data_offset`). Definitions describe; instances contain.

## Gap

Bytes `raw[fields_end:data_offset]`, where `fields_end` is the end of
the last struct's field block:

```python
import struct
last = g.struct_defs[-1]
fields_end = last.field_offset + last.field_count * 12
gap = raw[fields_end:g.data_offset]
assert fields_end <= g.data_offset and set(gap) <= {0xFF}
```

* The length lives nowhere in the file — `data_offset` minus the
  walked `fields_end` is the only way to know it. A parser must walk
  the whole struct-def table first; the count alone is not enough.
* Content is `0xFF` fill when present. msh/mmh pad to the next
  16-byte boundary (540→544 = 16×34, 3828→3840 = 16×240 —
  plausibly for aligned vector loads, both files are full of 16/64B
  inline values). phy's writer skipped padding (gap empty, start at
  1944 = 16×121+8). So: never require non-empty, never assume the
  data-block starts 16-aligned.
* Validation: `fields_end <= data_offset`, every gap byte `0xFF`.
* Writer: choose the gap (pad with `0xFF` to 16B to match stock
  msh/mmh exporters, or emit empty like phy), then recompute
  `data_offset = fields_end + len(gap)`. Preserve the original gap
  byte-exact on clone-then-patch writes.

Caveats seen in the samples:
* Zero-field structs share their `field_offset` with the next def
  (phy `prsj/pipj/fxdj` all point at 720, `cylj` at 1296). Read zero
  fields; never index into the neighbor's block.
* Labels are NOT unique within a struct: phy `d6j` contains label
  6136 twice (cell offsets 160 and 176). Key field lookups by
  `(label, occurrence)` or keep the field list, never a plain
  `{label: field}` dict.
* Fields are stored sorted by label ascending in every struct def
  observed. A writer must preserve that order.

## Labels (how to parse them)

A label is a bare `u32` id. The file contains NO label strings, so a
parser needs an external id-to-name table (`LABELS`):

```python
name = LABELS.get(label_id, f"label_{label_id}")  # never drop unknowns
```

Building the table:
1. Dump the distinct ids per file (msh: 35, mmh: 220, phy: 123).
   The probe script used for this guide lives outside the repo
   (`<Temp>\opencode\probe_labels.py` pattern: `unpack_from("<IHHI")`
   over each struct's field block).
2. Ids cluster by domain, which bootstraps naming:
```
2, 8, 16              tiny ids (shared scalars / counts)
3104                  phy/mmh leaf string (nrfl)
6000-6345            MMH hierarchy (names, transforms, bones, …)
  6000              ECString name (every named node/struct)
  6999              generic CHILDREN list (type 0xFFFF, flags 0xA000)
6058-6166            PHY joints/shapes
8000-8034            MESH geometry (chunks, streams, declarators)
  8017-8019          bnds (shared by msh AND mmh — ids are cross-file)
  8026-8031          vertex declarator (msh `decl` == mmh `vtdl`)
6998                  second generic-list marker (phy `shap` only)
```
3. Cross-file overlaps pin down shared semantics (`bnds`, `decl`/`vtdl`
   above). Per-fourcc meaning still needs DAO toolset knowledge
   (community tables, `EditorGff40.dll` strings in fuller projects);
   until then keep the numeric id as the key.
4. Roundtrip rule: every id must survive a read→write cycle even if
   unnamed. A "strict" mode may additionally require all ids to be
   known, but the default parser must fall back to `label_<id>`.

## Flags (observed combinations)

```
0x0000 plain scalar / tightly-packed bytes (value inline)
0x2000 Reference (cell -> ref to a single struct instance; phy `jnt`
       label 6080, phy `shap` labels 6998/…)
0x4000 Struct (Type id = struct-def index of array elements;
       msh `chnk` label 8020)
0x8000 List (cell -> ref to a list; msh `mesh` 8022/8023, mmh `mshh` 6255)
0xA000 List+Reference (0x8000|0x2000): generic CHILDREN lists —
       label 6999 with type 0xFFFF in nearly every mmh struct
0xC000 List+Struct (0x8000|0x4000): inline struct arrays —
       msh `chnk` labels 8011/8025, mmh `nclt` 6233, `msgr` 8025
0xE000 List+Struct+Ref (msh `mesh` label 8021)
0xFFFFFFFF NULL_REF: empty ECString / empty list (no payload)
```

Note: `0xFFFF` is a *type* value (generic list), not a flag, though the
older comment block placed it under flags. Generic-list payload:
`<I n + n*<HHI` (structdef-index u16, flags u16, data-offset u32).
Entry flags are per-element, not inherited from the field: the mmh
sample carries `0x4000` (inline struct) on every `6999` entry.

## Type ids (observed sizes)

Scalar sizes from the format comment (`description.py:41-58`):
```
0:1 1:1 2:2 3:2 4:4 5:4 6:8 7:8 8:4 9:8 10:12
12:16 13:16 14:4 15:16 16:64 17:8
(11 is absent; unlisted ids only appear as List/Struct/Ref holders
whose payload is a u32 ref, never inline scalars.)
```

Roles confirmed by usage in the samples:
* `14` (size 4, plain): ECString reference — cell holds a data-block
  offset to `<I count incl. trailing NUL> + count WCHARs utf-16-le`.
  Every `6000`-style name field. `NULL_REF` means None.
* `0xFFFF` (65535): generic list (see above), always with label 6999
  (mmh) / 6998+6999 (phy `shap`).
* `12/13/15/16` (16/16/16/64): wide inline values — transforms and
  bounding volumes (`trsl` label 6047 type 12, `rota` 6048 type 13,
  `nirr` 6336-6338 type 16). Float-vs-int decoding per consumer.
* `0/1` (size 1): packed byte fields; note `strm` labels 8015/8016
  are single bytes at adjacent cell offsets 16/17.

## Data-block

* Scalar cell: the data-block holds *instances* of each struct def
  (`struct_size` bytes each). `instance_base` is where one instance
  starts, measured from the data-block start (root instance is at 0;
  the rest are found via ref cells / lists, see API below). The cell's
  absolute file position is `data_offset + instance_base +
  field.data_index`, size from the table above. The cell decodes per
  the Scalar convention (type 14 stays an ECString `str`).
* Ref cell: `u32` at the cell holding a data-block offset
  (i.e. relative to `data_offset`, `0xFFFFFFFF` = NULL_REF).
* `u32`/`u8` lists: `<I n` + decoded elements (type 14 → list of
  `str`/`None`), with range checks.
* Inline struct array (`List+Struct` without Ref): `<I n` + `n*size`
  bytes inline (msh declarators: 24B each).
* Gap: see Gap above — validate, preserve byte-exact, never assume
  length or alignment.

### Data-block API (`GFFReader.get_from_data_block`)

```python
get_from_data_block(struct_ref, instance_base=0, label_id=None, occurrence=0)
# struct_ref: GFFStructDef or struct-def table index
# instance_base: block-relative offset of the instance (root is 0)
# label_id: None -> whole instance; int label id or GFFField -> one field
# occurrence: selects among duplicate labels (e.g. phy d6j label 6136 x2)
```

Whole instance returns `{label_id: value}`; duplicate labels aggregate
to a list in definition order. Return shapes per flag combo:

| flags | shape |
| ----- | ----- |
| `0x0000` scalar | decoded (`_SCALAR_FORMATS`); type 14 → ECString `str` |
| `0x0000` type 14 | `str`, `None` on NULL_REF |
| `0x4000` Struct | inline single struct → nested dict (type = struct-def index) |
| `0x8000` List | `<I n` + decoded elements (type 14 → list of `str`/`None`) |
| `0xC000` List+Struct | `<I n` + `n` inline instances → list of dicts |
| `0xE000` List+Struct+Ref | `<I n` + `n` u32 refs → list of dicts (`None` per NULL element) |
| `0x2000` Reference | single instance → dict |
| `0xA000` + type `0xFFFF` | generic list → list of `{struct_index, flags, offset, value}` |
| `0x2000` + type `0xFFFF` | single generic ref (one `<HHI>`, no count) → same dict shape |

`NULL_REF` resolves to `None` in every ref position. Out-of-range
reads (bad instance base, ref, count, struct index, label) raise
`ValueError`. ECString layout: `<I count incl. trailing NUL>` +
`count` UTF-16LE chars.

### Scalar convention

`decode_scalar` maps each inline scalar type to a little-endian struct
format (`_SCALAR_FORMATS` in `description.py`): single values come back
as int/float, vectors/matrix/TlkString as tuples. ECString (14) and
generic (0xFFFF) are references and raise there. `get_from_data_block`
uses it for plain cells and scalar-list elements.

Confirmed against the samples: decl `8028` reads 16 as u32, `bnds`
boxmin decodes as 4 floats.

## Worked example (`data/c_corspidr_3.msh`)

```
  0  +-------------------------------+
     | "GFF V4.0"   magic (8B)       |
  8  +-------------------------------+
     | "PC  "       platform (4B)    |
 12  +-------------------------------+
     | "MESH"       file_type (4B)   |
 16  +-------------------------------+
     | "V0.1"       version (4B)     |
 20  +-------------------------------+
     | nstruct = 5          (u32 LE) |
 24  +-------------------------------+
     | data_offset = 544    (u32 LE) |
 28  +===============================+
     | struct def 0 'mesh' (16B):    |
     |  type fc=6 fo=108 size=24     |
 44  +-------------------------------+
     | struct defs 1..4 (4 x 16B)    |
108  +===============================+
     | field 0, label 2 (12B):       |
     |  type=14 ECString, plain,     |
     |  cell at instance byte 0      |
120  +-------------------------------+
     | remaining 35 fields (35x12B)  |
540  +-------------------------------+
     | gap: 4 x 0xFF                 |
544  +===============================+
     | DATA BLOCK: root 'mesh'       |
     | instance, base 0 (24B):       |
     |  +0  u32 61556 -> name       |
     |      ECString @ data+61556    |
     |  +4  u32 0        (l. 8032)   |
     |  +8  u32 61596    (l. 8021)   |
     | +12  u32 380      (l. 8022)   |
     | +16  u32 57600    (l. 8023)   |
     | +20  u32 0xFFFF0000 (l. 8033) |
568  +-------------------------------+
     | ... rest of data ...          |
```

Chain: header locates the tables, the def describes `mesh`,
the field says "cell 0 is a string ref", the data holds
`61556` → the mesh-name ECString (a `u32` count + UTF-16LE chars).

## Format specification (datoolset.net/wiki/GFF)

Canonical source: https://datoolset.net/wiki/GFF (last edited 2011-08-08).
This section records the format-level spec independently of our parser
implementation above, so the doc stays accurate if the reader code drifts.

### General overview

Data is grouped into **structs**; the file level is the top-level struct.
Each struct can hold any number of fields of any type, including nested
structs and variable-size lists of any type.

### GFF V4.0 vs V3.2

- Labels: 4-byte numeric IDs instead of 16-byte strings.
- Lists: any type (V3.2 only allowed struct lists).
- Struct data lives in the data block, not in fields; fields provide the
  mapping of where the data is stored.
- References: added to mimic pointers for complex data structures.
- Header additions: `FileVersion` (GFF format version) and `TargetPlatform`
  (intended platform for the resource).
- Removed sections: label array, field-indices array, list-indices array.
- Overall goal: smaller files, faster access.

### Field data types

The format supports up to 65,535 basic types. The starting set:

| Type       | ID   | Notes |
| ---        | ---  | --- |
| UINT8      | 0    | |
| INT8       | 1    | |
| UINT16     | 2    | |
| INT16      | 3    | |
| UINT32     | 4    | |
| INT32      | 5    | |
| UINT64     | 6    | |
| INT64      | 7    | |
| FLOAT32    | 8    | |
| FLOAT64    | 9    | |
| Vector3f   | 10   | |
| Vector4f   | 12   | |
| Quaternionf| 13   | |
| ECString   | 14   | Always a reference to the raw data block (regardless of flags); a list of WCHARs (UTF-16LE). |
| Color4f    | 15   | |
| Matrix4x4f | 16   | |
| TlkString  | 17   | Not a string per se — a pair of UInt32 values; one is an index into the TLK string table. |

There is also a **"Generic"** type, ID `0xFFFF`, only usable in lists
(and references?). Strings are stored as a list of WCHARs.

### Field labels

Binary GFF uses 4-byte IDs to label each field. Within a struct, each
field must have a unique ID. IDs may be string hashes or numerical IDs;
the only requirement is that reader and writer agree on the IDs.

A large common-ID list can be recovered by opening
`Dragon Age\tools\plugins\EditorGff40.dll` in a text editor and searching
for the string `BinaryGFFIDList.h`.

### File format physical layout

Order (top to bottom):

```
[Header][Struct Array][Field Array][Raw Data Block]
```

#### Platform dependence

Endianness follows the target platform: Intel → little-endian, PowerPC →
big-endian. Other platform differences (alignment, in-memory layout) may
exist.

#### Header

Located at file start. First five fields are **big-endian** and never
byteswapped (so they stay human-readable on any machine).

| Value            | Description |
| ---              | --- |
| GFFMagicNumber   | `0x47464620` = ASCII `"GFF "`. |
| GFFVersion       | 4 bytes = GFF format version. V4.0 → `"V4.0"` / `0x56342E30`. |
| TargetPlatform   | 4-byte platform code. `"PS3 "` = `0x50533320`, `"X360"` = `0x58333630`, `"PC  "` = `0x50432020`. |
| FileType         | 4-byte file-type ID; by convention three-letter extension + space (e.g. `"MMH "`). |
| FileVersion      | 4-byte version of the FileType; by convention `"Vx.x"` / `"xx.x"`. |
| StructCount      | 4-byte unsigned count of elements in the Struct Array. |
| DataOffset       | 4-byte unsigned offset from file start to the Raw Data Block. |

#### Struct Array

Starts immediately after the header. Element 0 is always the **Top-Level
Struct**; every GFF file has at least one struct. `N = Header.StructCount`.

| Value        | Description |
| ---          | --- |
| StructType   | 4-byte programmer-defined ID. |
| FieldCount   | 4-byte number of fields in the struct. |
| FieldOffset  | 4-byte unsigned offset from file start to the first field of this struct. |
| StructSize   | 4-byte unsigned size of the data-block chunk representing one instance of this struct. |

All fields for a struct are contiguous, so first-field address + count
is enough to reach any field.

Struct array order:

```
Struct 0 (Top-Level Struct) — always present
Struct 1
Struct 2
...
Struct N-1     (N = Header.StructCount)
```

#### Field Array

Starts immediately after the Struct Array. Each struct's fields are
contiguous in the array and appear in **increasing label order**. Top-level
struct fields come first.

Field array order:

```
Struct 0 field 0
Struct 0 field 1
...
Struct 0 field (FieldCount-1)
Struct 1 field 0
...
```

Each field:

| Value    | Description |
| ---      | --- |
| Label    | 4-byte label used to look up the field. |
| FieldType| 4 bytes, split into two 2-byte halves: **TypeID** (upper) + **Flags** (lower). |
| Index    | 4-byte unsigned offset to the data, measured from the start of the struct **in the data block**. |

The Index can produce padding inside structs (garbage data by convention
filled with `0xFF`); this happens when aligning e.g. 16-byte vectors.

FieldType breakdown:

| Half  | Description |
| ---   | --- |
| TypeID (2B) | Unsigned type ID (see Field data types). |
| Flags (2B)  | Bit flags (see below). |

Flags (from MSB):

| Bit  | Name       | Description |
| ---  | ---        | --- |
| 1 (MSB) | List       | Field is a list of the described type. |
| 2       | Struct     | Field is a struct. If clear, TypeID is the base type; if set, TypeID is the index into the Struct Array. |
| 3       | Reference  | Data block cell holds an offset (from data-block start) to the real data — mimics pointers. |

#### Raw Data Block

Where the actual data lives. The top-level struct's data is at the start
of the block; everything else is reached via struct fields or references.

#### Lists

A list reference points to another location in the file holding the list.
The list layout:

```
[Length (u32)][Element 0][Element 1]...[Element Length-1]
```

Empty lists can store a null reference instead of allocating another block.

**Generic lists** (FieldType = `0xFFFF`) store **pairs**: `(type, reference)`
where `type` defines the FieldType (with flags) of the individual element
and `reference` is a standard reference to the element's data. Each entry
is 8 bytes.

#### References

A reference is a 4-byte unsigned offset from the beginning of the data
block to the data's location. Null references are `0xFFFFFFFF`. Null refs
can appear both in lists and as standalone reference items.
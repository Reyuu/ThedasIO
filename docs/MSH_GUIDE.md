# MSH Guide (.msh, `GFF V4.0` / `MESH`)

Mesh file. 5 struct-defs hold the vertex blob, the index blob and the declarators. The container format is documented in [docs/GFF40_GUIDE.md](docs/GFF40_GUIDE.md).

## Observed structure

```
[0] mesh fc=6 fo=108 sz=24
    label=2    type=14 flags=0x0    index=0    # ECString mesh name
    label=8021 type=4  flags=0xE000 index=8    # List+Struct+Ref
    label=8022 type=0  flags=0x8000 index=12   # List -> vertex blob
    label=8023 type=0  flags=0x8000 index=16   # List -> index blob
    label=8032 type=4  flags=0x0    index=4
    label=8033 type=0  flags=0x0    index=20
[1] decl fc=6 fo=180 sz=24                    # vertex declarator, 24B rows
    8026/i32@0, 8027/i32@4, 8028/u32@8, 8029/u32@12, 8030/u32@16, 8031/u32@20
    (identical labels in mmh `vtdl` — shared semantics)
[2] bnds fc=3 fo=252 sz=48                    # bounds, 3 x 16B inline
    8017@0, 8018@16, 8019@32  (same ids as mmh `bnds`)
[3] strm fc=6 fo=288 sz=20
    8012/u32@4, 8013/u32@8, 8014/u32@12, 8015/u8@16, 8016/u8@17,
    8024 type=0 flags=0x8000 index=0          # List
[4] chnk fc=15 fo=360 sz=112                  # chunk, u32 cells + decl array
    2/ECString@48, 8000-8009/u32@52-88, 8034/u32@100,
    8011 type=3 flags=0xC000 index=92,        # List+Struct inline array
    8020 type=2 flags=0x4000 index=0,         # Struct ref
    8025 type=1 flags=0xC000 index=96         # List+Struct inline array
```

Observed chunk values (`c_corspidr_3.msh`, chunk `C_CORSPIDR_Mesh1`):
`8000=52, 8001=1100, 8002=1968, 8003=0, 8004=0, 8005=0, 8006=0,`
`8007=0, 8008=1100, 8009=0, 8011=None, 8034=1`.
Roles still open, but the arithmetic constrains them: vertex blob is
57216B = 52×1100+16 with stride 52 (see Declarators) and 1100
recurring in 8001/8008, so one of those is plausibly the vertex
count. Index blob is 3952B (988 u32s per the wiki's `Index32` note).

## Declarators

`decl` rows are `D3DVERTEXELEMENT9`-shaped (confirmed against
https://datoolset.net/wiki/MSH Constants):

```
8026 stream, 8027 offset, 8028 type, 8029 usage, 8030 usage-index, 8031 method
```

Sample rows (chunk `C_CORSPIDR_Mesh1`, 8 rows):

```
stream off  type usage uidx method
0      0    16   0     0    0       # FLOAT16_4 POSITION
0      8    15   5     0    0       # FLOAT16_2 TEXCOORD
0      12   16   6     0    0       # FLOAT16_4 TANGENT
0      20   16   7     0    0       # FLOAT16_4 BINORMAL
0      28   16   3     0    0       # FLOAT16_4 NORMAL
0      36   16   1     0    0       # FLOAT16_4 BLENDWEIGHT
0      44   7    2     0    0       # SHORT4 BLENDINDICES
-1     0    MAX  MAX   0    0       # terminator (DECLTYPE_UNUSED)
```

Type ids used: `7` SHORT4 (8B), `15` FLOAT16_2 (4B), `16`
FLOAT16_4 (8B). Element sizes sum to the 52B stride
(8+4+8+8+8+8+8), matching `chnk:8000`. Usage ids used:
`0` POSITION, `1` BLENDWEIGHT, `2` BLENDINDICES, `3` NORMAL,
`5` TEXCOORD, `6` TANGENT, `7` BINORMAL. The terminator row
(`stream=-1`, `type=usage=0xFFFFFFFF`) is `D3DDECL_END`.

Reading: `mesh` 8022/8023 cells hold refs to int lists (vertex /
index data, decoded `u8` scalars); `chnk` 8011/8025 are inline
struct arrays (`List+Struct` without Ref: `<I n` + `n` inline
rows); `decl` rows are 24B (`struct_size` 24, 6 plain-scalar
fields). To split the vertex blob into attributes, walk the
declarators: each row's `offset`/`type` gives one attribute's
position and width inside the 52B stride.
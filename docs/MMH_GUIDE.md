# MMH Guide (.mmh, `GFF V4.0` / `MMH `)

Hierarchy file. Alone only defines the mesh hierarchy along with its bones and other hooks; needs MSH to actually display a mesh. The container format is documented in [docs/GFF40_GUIDE.md](docs/GFF40_GUIDE.md).

Known issues (roles open): `xprt` controller-type ids, `chnk`-style arithmetic roles in `nclt`/`nemt`, and references of binary-only defs (`data`, `vtdl`, `msgr`, `nclt`, `nemt`), all unresolved in this sample.

## Observed structure

Struct defs (`fourcc fc fo sz` — type, field count, field offset,
instance size):

```
[0]  mdlh fc=7        [12] vtdl fc=6        [24] nemg fc=3
[1]  data fc=10       [13] msgr fc=10       [25] nlpr fc=2
[2]  wtrl fc=6        [14] nclt fc=50       [26] nrfl fc=1
[3]  bbox fc=2        [15] crst fc=3        [27] nirr fc=4
[4]  bnds fc=3        [16] ntrn fc=5        [28] nalt fc=4
[5]  xprt fc=4        [17] emat fc=4        [29] nplt fc=13
[6]  attr fc=2        [18] emas fc=1        [30] usrp fc=2
[7]  rota fc=1        [19] spnv fc=10       [31] snap fc=2
[8]  trsl fc=1        [20] amel fc=4        [32] emtg fc=2
[9]  scal fc=1        [21] amap fc=2        [33] node fc=4
[10] att  fc=6        [22] nemt fc=62       [34] mshh fc=17
[11] catl fc=1        [23] spla fc=15
```

Key field patterns (label ids, see `docs/GFF40_GUIDE.md` Labels).
Fourcc ↔ wiki element mapping (confirmed against
https://datoolset.net/wiki/MMH schema):
* `mdlh` = ModelHierarchy (root). Fields: `6000` Name
  (`c_corspidra_0.mmh`), `6005` ModelDataName (`c_corspidr_0.msh`
  style mesh-file ref), `6248` ECString (None here), `6256`/`6275`
  u32 (66/136 here), `6306` ECString list (NULL_REF here),
  `6999` CHILDREN.
* `node` = Node. Fields: `6000` Name, `6254` BoneIndex (-1 =
  unattached here), `6330` SoundMaterialType (0 here), `6999`
  CHILDREN.
* `mshh` = NodeMesh. Fields: `6000` Name (`Mesh1`), `6001`
  MeshName (mesh file: `c_corspidr`), `6006` MeshGroupName (mesh
  chunk: `C_CORSPIDR_Mesh1` — matches the msh `chnk` name),
  `6255` u32 list (BonesUsed bone indices), `6335` Color4f
  (MaterialColor; `0xFF`×16 unset-fill here, reads as NaN×4 —
  see `docs/GFF40_GUIDE.md` Roundtrip hazard), `6999` CHILDREN.
* `xprt` = Export. Fields: `6052` ECString ExportName/TagName
  (e.g. `gob_gobtranslation`), `6053`/`6238`/`6274` u32
  (ControllerType-style ids: 6/5/17 here — roles open).
* `crst` = NodeCrustHierarchy. Fields: `6000` Name
  (`CrustHook1..14`), `6235` u32 (HookID), `6999` CHILDREN
  (`trsl`+`rota` only here).
* `trsl` = Translation: single field 6047 type 12 (16B inline
  value, e.g. `(0.0, 0.0, 0.0, 1.0)`).
* `rota` = Rotation: single field 6048 type 13 (16B inline
  value, quaternion per wiki).
* `scal` = Scale: single field 6278 type 8 (4B inline value).
* `bbox` = BoundingBox: 6054/6055 type 12 (min/max corners);
  `bnds`: 8017/8018/8019 type 12 (same ids as the msh `bnds` —
  shared semantics).
* `attr` = Attribute: 6049/6050 type 14 (`BaseLight`/`BaseLight`
  here, matching the wiki's near-universal values).
* `6999` type `0xFFFF` flags `0xA000` = generic CHILDREN list —
  present in most defs (`mdlh`, `wtrl`, `nclt`, `node`, `mshh`, …).
  Tree walk: `n = u32(r); per k: unpack("<HHI") =
  (structdef-index, flags, data-off)`; recurse. Entry flags are
  per-element: `0x4000` (inline struct) seen in this sample.
* `vtdl` (fc=6): labels 8026–8031, identical to msh `decl` —
  shared vertex-declarator semantics (see `docs/MSH_GUIDE.md`
  Declarators).
* `data` (fc=10): mixes plain scalars with `0x8000` lists
  (labels 8 type 2, 16 type 10).
* `nclt` (fc=50, sz=224) and `nemt` (fc=62, sz=240) are the two
  heavyweight defs; mostly type 0/4/8/10/14 plain scalars plus one
  `0x8000` list (6232) / CHILDREN (6999) each.
* `nirr` (fc=4, sz=208): three type-16 (64B) inline values
  6336/6337/6338 + one type-14 string.

Observed tree shape (`c_corspidra_0.mmh`): `mdlh` → single
`node` (`GOB`) → `GOD` → `Root` → limb chains (`Spine02…`,
`ArmL01…`, `RFLeg01…`, …) plus `CrustHook` leaves; every `node`
carries 2 `xprt` + `trsl` + `rota` children; `mshh` (`Mesh1`)
hangs off `GOB`. Binary-only defs (`data`, `vtdl`, `msgr`,
`nclt`, `nemt`, …) appear in no `6999` list in this sample —
their references (if any) are unresolved.

Root (verified): struct-def 0 (`mdlh`) instance at data-block
offset 0, children via its 6999 list. Do not hardcode the def
count — walk, don't index.
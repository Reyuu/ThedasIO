# Exporting

1. Open the ThedasIO tab, Export panel.
2. Tick the rows to export.
3. Set Export to: the folder standalone files land in. Untouched resources come
   out byte-identical to the source.
4. Press **Check model** first. It reports what export would fix up silently or
   fail on: unweighted verts, missing materials and textures, degenerate faces,
   non-tri polygons, unapplied modifiers, shape keys and texture sizes.
   Read-only. Each note and what to do about it is listed in
   [Reference: Check model messages](reference.md#check-model-messages).
5. Press **Export**.

Export writes a `.phy` beside a model when you have changed collision and PHY
support is on. See [Collision](collision.md).

Animations are exported separately with **Export Animation** in the same panel.
See [Animation](animation.md).

## Vertex groups and bones

Every weighted vertex group must resolve to a bone index before export. Imported
meshes already carry that mapping. If you add or rename a group, use the
[Bone map](bone-map.md) panel to pick its bone.

Export resolves a missing mapping automatically when the group name exactly
matches a bone on the mesh's armature. Otherwise it reports:

`<mesh>: vertex group 'X' has no bone; pick one in Object Properties > ThedasIO Bone Map`

Open Bone Map, map the group, and export again. Bones that are not in the mesh's
bone order cannot be mapped without an explicit raw index; see
[Bone order and bone indices](bone-order.md).

## Tint

Export tint writes each model's DAO Tint values as a `.tnt` for the chargen
variation path. Tint resource names the `.tnt` file (e.g. `t3_arm_rlr.tnt`);
leave blank for `<model>.tnt`.

DAO tint values are also baked into the XML MAO as a fallback. On Dragon Age II,
tint colours live in `tints.rim`, not in the binary MAO. Export tint is refused
for a DA2 binary MAO, with:

`binary MAO tint export is not supported; DA2 tint colours live in tints.rim, not the MAO`

Disable Export tint when exporting DA2 MAOs.

## Rename and archive behaviour

Rename on export shows per-file rename fields; set names apply on export.

Source `.erf`/`.rim` archives are read-only. Export never writes back into an
archive.

A mesh keeps its shipped index width, 16-bit unless the file says otherwise. If
an edit grows one chunk past 65535 vertices, export widens the **whole mesh** to
32-bit indices: every chunk's index region is repacked and re-offset, because the
per-chunk start is an offset shared by the whole file and one chunk widening
alone would shift the rest. That doubles the index region size, which for a mesh
just over the limit is a small cost.

Only two shipped meshes are 32-bit, so this path is rare. Check model reports
the vertex count before you commit to a heavy subdivision.

One failing resource does not stop the batch: the rest still lands and the
failure is listed with the file name.

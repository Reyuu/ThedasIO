# Exporting

1. Open the ThedasIO tab, Export panel.
2. Tick the rows to export.
3. Set Export to: the folder standalone files land in. Untouched resources come out byte-identical to the source.
4. Press Check model first. It reports what export would fix up silently or fail on: unweighted verts, missing materials and textures, degenerate faces, non-tri polygons, unapplied modifiers, shape keys and texture sizes. Read-only. Each note and what to do about it is listed in [Reference: Check model messages](reference.md#check-model-messages).
5. Press Export.

Export writes a `.phy` beside a model when you have changed collision and PHY support is on. See [Collision](collision.md).

Animations are exported separately with **Export Animation** in the same panel. See [Animation](animation.md).

Export tint writes each model's DAO Tint values as a `.tnt` for the chargen variation path. Tint resource names the `.tnt` file (e.g. `t3_arm_rlr.tnt`); leave blank for `<model>.tnt`.

Rename on export shows per-file rename fields; set names apply on export.

Source `.erf`/`.rim` archives are read-only. Export never writes back into an archive.

A mesh keeps the index width it was shipped with — 16-bit unless the file says
otherwise. If an edit grows one chunk past 65535 vertices, export widens the
**whole mesh** to 32-bit indices: every chunk's index region is re-packed and
re-offset, because the per-chunk start is an offset shared by the whole file and
one chunk widening alone would shift the rest. That doubles the index region
size, which for a mesh just over the limit is a small cost.

Only two shipped meshes are 32-bit, so this path is rare. Check model reports
the vertex count before you commit to a heavy subdivision.

One failing resource does not stop the batch — the rest still lands and the
failure is listed with the file name.

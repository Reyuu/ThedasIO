# Exporting

1. Open the ThedasIO tab, Export panel.
2. Tick the rows to export.
3. Set Export to: the folder standalone files land in. Untouched resources come out byte-identical to the source.
4. Press Check model first. It reports what export would fix up silently or fail on: unweighted verts, missing materials and textures, degenerate faces, non-tri polygons, unapplied modifiers, shape keys and texture sizes. Read-only.
5. Press Export.

Export tint writes each model's DAO Tint values as a `.tnt` for the chargen variation path. Tint resource names the `.tnt` file (e.g. `t3_arm_rlr.tnt`); leave blank for `<model>.tnt`.

Rename on export shows per-file rename fields; set names apply on export.

Source `.erf`/`.rim` archives are read-only. Export never writes back into an archive.

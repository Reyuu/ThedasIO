# Modder documentation

ThedasIO imports Dragon Age: Origins and Dragon Age II models into Blender and
exports them back as standalone files.

Set each game's folder once in the add-on preferences: **Game folder** for
Origins and **Dragon Age II folder** for II. Choose the game in the Import panel.
The panels live in the ThedasIO tab.

## How-to guides

- [Importing](importing.md): pull a model from either game's database or a file
  from disk, and scan the archives.
- [Exporting](exporting.md): write loaded resources back to a folder, and check a
  model before export.
- [Bone map](bone-map.md): map vertex groups to bones when export reports a
  group has no bone.
- [Animation](animation.md): play a `.ani` on a model, edit its keys, and export
  it.
- [Collision](collision.md): turn on PHY support, edit collision shapes, and add
  a capsule to a bone.
- [Substance Painter round trip](substance.md): paint textures in Substance and
  bring them back.

## Explanation

- [Bone order and bone indices](bone-order.md): why an armature bone may not be
  available for a particular mesh, and what `#index` means.

## Reference

- [Reference](reference.md): every panel control, check-model warning, report
  message, and supported format.

# Bone order and bone indices

An armature can show a bone that has no index for a particular mesh. The mesh's
bone order decides which slots it can write; the armature only supplies the
names. The `#index` in the panel is the mesh's slot for that bone.

## Two bone lists

A model has two related lists of bones:

| List | What it is | Where it lives |
|---|---|---|
| Armature bones | The bones you see in Blender, with their parents, names, and rest transforms. | The armature object |
| Mesh bone order | The bone slots this particular mesh can write vertex weights into. | The mesh's source data |

The armature is the skeleton. The mesh bone order is the subset of that skeleton
the mesh was built for. A model can have several meshes, and each mesh can use a
different subset.

## What export writes

A `.msh` vertex weight is an index, not a bone name. The index points into the
mesh's bone order. The game resolves that slot through the model hierarchy to
find the actual skeleton bone.

ThedasIO stores the name-to-index map when it imports a mesh with its `.mmh`.
An imported mesh usually exports without Bone Map work because the import already
has that map. The names in the panel are for editing; the file always gets the
index.

The `#index` shown next to a bone is its position in the mesh's bone order. It
is not the Blender armature's bone order, and it is not a global skeleton index.
Two meshes on the same armature can give the same bone different indices.

## Why a bone can have no index

A bone has no index for a mesh when it is absent from that mesh's bone order.
Common reasons are:

- The bone is a helper, control, or attachment bone that is not skinned by this
  mesh.
- The mesh came from a different model or a different `.mmh`.
- The mesh was imported without its skeleton, so its bone order is incomplete or
  unknown.
- The bone was added to the armature after the mesh was imported.

The bone is still valid for the armature. It is just not a valid skin target for
that mesh. Writing a different bone's index would make the weights follow the
wrong bone in the game.

## What to do

If an imported mesh has the bone on the rig, re-import it with the `.mmh` that
owns the correct skeleton.

If you added or renamed a group, use Bone Map and pick the matching bone when it
has an index. Auto-map handles exact name matches.

If the bone has no index, enable **Allow bone index assignment for unindexed
bones** only when you know the correct raw index. Otherwise the mesh and
skeleton do not match.

If several meshes share one rig, select the mesh whose bone order you mean. The
panel uses that mesh's order, not a merged list from the whole rig.

## Dragon Age II

The same rule applies in Dragon Age II. DA2 archives store hashed paths, but a
mesh's bone order still comes from the mesh and its model hierarchy. Archive
names and Resource paths do not change which bone indices a mesh can use.

## Related

- [Bone map](bone-map.md)
- [Importing](importing.md)
- [Reference: Bone Map panel](reference.md#bone-map-panel)

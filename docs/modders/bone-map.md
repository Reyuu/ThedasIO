# Bone map

The Bone Map panel tells ThedasIO which bone a vertex group belongs to when the
mesh is exported. Imported meshes usually need no changes. Use it when you add a
vertex group to a custom or joined mesh, or when export reports that a group has
no bone.

A vertex group stores a name. The exported `.msh` stores a bone index. The panel
keeps the name-to-index mapping for each mesh. For the background, see
[Bone order and bone indices](bone-order.md).

## Open the panel

1. Select the mesh object in the viewport or the outliner.
2. Open **Object Properties** (the orange object tab).
3. Expand **ThedasIO Bone Map**.

The panel is available for mesh objects. It finds the armature through the
mesh's Armature modifier.

## Map groups

### Auto-map matching names

Press **Auto-map same-named groups**. Every vertex group whose name exactly
matches an indexed bone on the mesh's armature is mapped to that bone. Bones
outside the mesh's bone order are left alone.

Use it after importing a mesh, or after naming groups after bones.

### Pick a bone for one group

1. Find the group in the panel. Its button shows the current bone and index, for
   example `Spine02 (#8)`, or **Pick bone...** when it is unmapped.
2. Press the button. The **Map Vertex Group to Bone** dialog opens.
3. Type part of a bone name in **Search** to filter the list.
4. Choose the bone and confirm.

The group is now mapped. The row shows the bone name and its `#index`. That index
is the bone's position in this mesh's bone order, not its position in Blender's
armature.

### Export

Mapped groups export normally. Unmapped groups are reported during export:

`<mesh>: vertex group 'X' has no bone; pick one in Object Properties > ThedasIO Bone Map`

Map the group and export again.

## Unindexed bones

Some bones on the armature are not in this mesh's bone order. They may be helper
or control bones, or the mesh may have been imported with a different `.mmh`.
Those bones have no index to write, so the panel hides them by default.

The panel reports how many are hidden, for example:

`3 bone(s) are hidden; enable the checkbox above`

If you need to see them, tick **Allow bone index assignment for unindexed
bones**. The bone list now includes entries marked `(no index)`, and the
**Bone index** field appears in the picker.

`Bone index` is an advanced raw index. The panel cannot derive it for an
unindexed bone. Enter it only when you know the target mesh has a valid row for
that bone. A wrong number weights the mesh to a different bone.

If a bone is on the rig but missing from the mesh's bone order, the usual fix is
to re-import the mesh with the `.mmh` that owns the correct skeleton. Turning on
the checkbox is not a substitute for the correct mesh bone order.

## Troubleshooting

| Message | Meaning |
|---|---|
| `No armature modifier. Add the model's armature to map groups.` | The mesh has no Armature modifier. Add the modifier and point it at the armature before mapping. |
| `Bone order unknown. Re-import this mesh with its .mmh, then map groups.` | The mesh has no stored bone order. Re-import it with its `.mmh`. |
| `This rig mixes bone orders. Map groups on the imported mesh.` | Different meshes on the armature use different bone orders. Select the mesh that owns the target bone order. |
| `N group(s) still need a bone` | One or more groups have no mapping. Use auto-map or pick bones for them. |
| `'X' is not in this mesh's bone order` | The selected bone has no valid index for this mesh. Choose another bone or re-import with the correct skeleton. |
| `'X' has no MSH index; set Bone index manually` | The bone is visibly unindexed. Enter its raw index only if you know it. |

## Related

- [Bone order and bone indices](bone-order.md)
- [Importing](importing.md)
- [Exporting](exporting.md)
- [Reference: Bone Map panel](reference.md#bone-map-panel)

# Collision

Edit a model's PHY collision shapes in Blender, add shapes to a bone, and write
the changes into a `.phy` on export.

Collision is off by default. Turn it on in the add-on preferences before any
collision is imported, edited, or written.

## Turn PHY support on

1. Open Edit > Preferences > Add-ons and find ThedasIO.
2. Tick **Enable PHY collision**.
3. Import the model again. If the model was already imported with the switch
   off, its collision shapes do not exist yet.

## Edit an existing shape

Shapes appear as proxy objects in the **ThedasIO Collision** collection, each
placed on the bone it belongs to.

1. Select a proxy in the viewport or the outliner.
2. Move it with **G**, rotate it with **R**, or resize it with **S**.
3. Export the model (see [Exporting](exporting.md)).

Only a proxy you changed is written to the `.phy`. Unchanged shapes keep their
original data.

Scale controls the size. The axes depend on the shape:

| Shape   | Radius             | Length / height | Box size              |
|---------|--------------------|-----------------|-----------------------|
| Capsule | Y and Z scale (averaged) | X scale   | -                     |
| Sphere  | X scale            | -               | -                     |
| Box     | -                  | -               | X, Y, Z scale each    |

Do not apply the scale (Ctrl+A). Export reads the scale on each proxy to work
out the size, so an applied scale would be lost.

## Add a shape to a bone

Adding needs a model that has a `.phy`. A model without one reports "This model
has no .phy to add collision to".

1. Import the model from the Game source, with PHY support on.
2. Choose the bone:
   - In the Collision panel, set **New shape bone** to the bone name.
   - Or select the bone in Pose Mode. With no bone chosen in the panel, the
     selected bone is used. Without a selection, the first bone is used.
3. Press **Add Capsule**, **Add Sphere**, **Add Box**, or **Add Mesh**.
4. A shape appears on that bone. Move and resize it as described above.
5. Export. The new shape is written into the `.phy` beside the model.

**Add Mesh** builds collision from the selected model mesh. If you have faces
selected it uses those; if you have vertices selected it uses their convex
hull; otherwise it uses the whole mesh. The mesh is cooked with the game's
cooking library (cook.exe plus nxcooking.dll or physxcooking.dll), so the
add-on must find the game folder and its cooking DLL. If cooking fails, the
shape is not added.

## Remove a shape

1. Select a collision proxy in the viewport or the outliner.
2. Press **Remove** next to it in the Collision panel.

A shape you added in Blender is dropped. An imported shape is cut out of the
model's `.phy` on export; nothing else in the file changes.

## Move a shape to another bone

1. Select a collision proxy.
2. Press **Move Collision to Bone** next to it in the Collision panel.
3. Pick the target bone. The shape is re-hung under that bone's collision
   object on export.

## Limits

- **Mesh collision** can be edited and created, but only through cooking. An
  existing mesh shape in the file can be edited in place without re-cooking.
- **Proxies are created on import only** when the model's `.phy` exists and the
  switch is on. Turning the switch on afterwards requires a re-import.
- **Disk imports** do not offer Add Collision. Collision is loaded for disk
  models too, but the Add button is in the Game source panel only.

## Related

- [Reference: panel controls and messages](reference.md)
- [Importing](importing.md)
- [Exporting](exporting.md)

# Collision

Edit a model's PHY collision shapes in Blender, add a capsule to a bone, and
write the changes into a `.phy` on export.

Collision is experimental. It is off by default, and nothing collision-related
is imported, edited, or written until you turn it on.

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

## Add a capsule to a bone

Adding needs a model that has a `.phy`. A model without one reports "This model
has no .phy to add collision to".

1. Import the model from the Game source, with PHY support on.
2. Choose the bone in one of two ways:
   - In the Import panel, set **Bone** to the bone name.
   - Or enter Pose Mode and select the bone. With no bone chosen in the panel,
     the selected bone is used. Without a selection, the first bone is used.
3. In the Import panel, press **Add Collision**.
4. A capsule appears on that bone, with radius 0.1 and height 0.2. Move and
   resize it as described above.
5. Export. The new shape is written into the `.phy` beside the model.

The panel always adds a capsule. This version does not offer sphere or box
shapes for creation, so a new shape is always a capsule.

## Limits

- **Mesh collision** can be edited but not created. Its acceleration data is
  not fully decoded, so ThedasIO will not write a new one.
- **Proxies are created on import only** when the model's `.phy` exists and the
  switch is on. Turning the switch on afterwards requires a re-import.
- **Disk imports** do not offer Add Collision. Collision is loaded for disk
  models too, but the Add button is in the Game source panel only.

## Related

- [Reference: panel controls and messages](reference.md)
- [Importing](importing.md)
- [Exporting](exporting.md)

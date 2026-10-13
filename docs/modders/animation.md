# Animation

Play a DAO `.ani` on an imported model, edit its keys, and write it back as a
`.ani`. Animation works on `.mmh` hierarchies only, because the clip is matched
to the armature built from the hierarchy.

## Play an animation on a model

1. Import the model first, from the Import panel. The armature it creates must
   be in the scene.
2. Make the armature the active object by clicking it in the viewport or the
   outliner.
3. Choose the `.ani` in one of two ways:
   - **From the game database:** Import panel, Source set to Game. Search for
     the animation, select its row, and press **Import Selected**.
   - **From disk:** Import panel, Source set to Disk. Pick the file and press
     **Import from file**.

ThedasIO creates one Action on the armature and sets the scene to 30 frames per
second. The frame range is set to the clip length.

The message bar reports `<armature>: <n> curves from <clip>`. If it reports that
no bone matched, the animation does not belong to this model. Import the model
the animation was made for.

## Edit an animation

Bones are keyed as they were in the file. Blender's keyframes are the editable
part:

1. Enter Pose Mode on the armature and select the bones you want to change.
2. Move the frame to the time you want to change, then pose the bones.
3. Press **I** to insert keyframes on the posed bones. A keyframe placed past the
   end of the clip makes the clip longer.
4. Check the result by scrubbing the timeline.

Export writes only what changed:

- A key you moved or added is re-encoded.
- A bone you did not key keeps its original bytes.
- A change smaller than about 0.1 degree is dropped, because Blender stores
  poses in single precision. Keys that do not change are not touched.

## Export the animation

1. Make the armature the active object. Its action must have come from a `.ani`
   import. An action made in Blender from scratch has no source file, so the
   export is refused with "Its action was not imported from a .ani".
2. Open the Export panel and press **Export Animation**.
3. Choose where to save. The default name is the action's name with `.ani`.

The file is written atomically, so a failed export leaves any existing file in
place.

## What is not preserved

- **Vfx and scalar channels** are kept with their original key data. They are
  not bone-driven, so editing them in Blender has no effect. When a clip grows,
  their key times are rescaled to the new length.
- **Bones that do not exist in the model** are kept as they were in the file.
- **Other animation formats:** `.anb` and `.fxe` are not imported. `.cub`
  files are listed in the asset filter, but importing one reports "No importer".

## Related

- [Reference: Export Animation and messages](reference.md)
- [Exporting](exporting.md)

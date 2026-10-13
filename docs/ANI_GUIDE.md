# ANI Guide (.ani, `GFF V4.0PC` / `ANI `)

Animation sequences. Each `.ani` is one clip of pose-bone channels, bound at
runtime by the bone's `xprt` export name (e.g. `chest_rotation`). Reader:
`src/thedas_io/ani/description.py`; Blender binding: `ani/scene.py`.

## Observed structure

```
anim  GFF_ANIMATION_NAME      (4006)
      GFF_ANIMATION_ANIMLENGTH (4009, f64, seconds)
      GFF_ANIMATION_ISADDITIVE (4011)  GFF_ANIMATION_ISOVERRIDE (4012)
      GFF_ANIMATION_NODELIST   (4005) -> node[]
node  GFF_ANIMATION_NODENAME   (4000)   "export name", e.g. leftclav_rotation
      GFF_ANIMATION_TARGET     (4001, u8)
      GFF_ANIMATION_NODEDATA   (4004, generic list) -> key struct array
key   GFF_ANIMATION_KEY_TIME   (4035, u16)   GFF_ANIMATION_KEY_DATA0.. (4036..)
```

`GFF_ANIMATION_TARGET` picks both the channel type and the compression:

| target | channel | encoding |
|--------|---------|----------|
| 2      | rotation | 32-bit packed quaternion |
| 4      | rotation | 64-bit packed quaternion |
| 6      | translation | `KEY_DATA0..2` three f32 |
| 8      | scalar vfx | `KEY_DATA0` f32 (e.g. birthrate) |
| other  | vfx vector | `KEY_DATA0..N` f32 (e.g. colour multipliers) |

## Key time

`KEY_TIME` is a u16 fraction of the clip: `seconds = time / 65535 * length`.
All clips in the install end their last key at 65535. The length field is a
4-byte float. A keyframe past the end of the clip extends it, which rewrites
every key list, since all times are fractions of the length.

## Packed quaternions

Both compressions encode the same scheme with an index in the low two bits
naming the component that is rebuilt, and the remaining offset-binary fields
storing the other three components scaled by `sqrt(2)` (see `decode_quat32` /
`decode_quat64`). The rebuilt component is always positive.

```
32-bit   fields of 10 bits at 22/12/2, (value - 512) / (sqrt2 * 511)
64-bit   21/21/20-bit fields across the two longs, same offset-binary scaling
```

## Component order

Quaternion components decode in `(x, y, z, w)` order; Blender wants
`(w, x, y, z)`.

## Applying an ANI to a Blender armature

Channels are named by export names (the `6052` values on a bone's `xprt`
children, e.g. `chest_rotation`, `god_translation`). `build_hierarchy` stamps
them on each bone as `thedas_io_xprt`.

Packed rotations use the 3ds Max importer layout (`max_plugins/.../DAOGFFMath.ms`):
64-bit reads the high long as the 32-bit half and the low long as the 64-bit
half, index in the low two bits. A unit norm does not validate a layout; the
first keys of `c_dog.sit_p.ani` match the MMH rest rotations within 1 degree on
42 of 77 channels, which the old layout did not. Translation channels are
offsets: local position = rest position + rest rotation applied to the key.

The ANI local node transforms are composed down the MMH hierarchy to get
`N_anim`. To preserve Blender's imported rest pose while matching the game's
skinning delta, each bone's target matrix is:

```text
target = N_anim @ N_rest.inverted() @ B_rest
```

Thus `target @ B_rest.inverted() == N_anim @ N_rest.inverted()`: the per-bone
skin transform matches the game without guessing Blender's pose-bone frame.
The importer sets this target parent-first; Blender solves and keys its pose
basis. A real-rig regression test compares the evaluated bone matrices against
this equation at several clip times.

The **Import Selected** button plays the selected `.ani` asset row on the
active armature (a disk `.ani` works with **Import from file** the same way),
so archived
clips are extracted from their `.erf`/`.rim` package through the normal asset
pipeline. It assigns a Blender Action and sets the scene frame rate/range for
playback. The Toolset uses `.cub` blend trees to combine streams; one `.ani` is
one clip. GAD/root-motion metadata is not yet applied to the armature object.

## Related

- `.cub` cutscene blend trees are GFF `ANBT` (structs `tree`/`node`, labels
  4021-4030); `4025 TREE_NODE_FILE` names the clips, already readable.
- `.anb` is XML, not binary.
- `.fxe` / `.fxm` facial animation is `BIOE` magic, outside the Binary GFF
  family.

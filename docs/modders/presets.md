# Character presets

Import a `.mor` character preset. It brings in every part the preset lists, each as its own `.mmh` on the first part's rig, with tint slots and material values. Rigs after the first are driven by the first, so the parts move as one character. You can also apply a `.mop` override on top.

## Import a character preset

1. Open the ThedasIO tab, Import panel, and set **Game** to Dragon Age: Origins or Dragon Age II.
2. Pick the `.mor` from the database or from disk:
   - From the game database: search for the `.mor`, select its row, and press **Import Selected**.
   - From disk: set Source to Disk, pick the `.mor` file, and press **Import from file**.
3. Every part the preset lists imports as its own `.mmh`, all on the first part's rig. The message bar reports the imported parts. Each part's meshes land in their own collection, named after the model stem.

## The Character preset panel

After importing, the Character preset panel shows in the ThedasIO tab:

- Preset: the `.mor` name the character was built from.
- Parts: one entry per part, with its file. Press a part to select its meshes in the viewport.
- Tint slots: one entry per tint file (`.tnt`). Slots whose `.tnt` was found show in color; missing ones show with an error icon.
- Preset override: shown when a `.mop` has been applied, naming the override file.

Tints land on the DAO Tint node groups of the character's materials, the same ten colours a `.tnt` holds. The tint slot name's prefix says which node group it paints:

| Prefix | Nodes painted |
|--------|---------------|
| `skn` | FaceM1 |
| `eye` | EyesM1 |
| `har` | HairM1, HairM2, HairM3, BeardM1, BeardM2, BeardM3 |
| `brd` | BeardM1, BeardM2, BeardM3 |
| `ulm` | LashesM1 |
| `stb` | FaceM1, HairM1, HairM2, HairM3 |

## Apply a `.mop` override

A `.mop` carries only the rows it sets. It patches what a `.mor` brought in instead of wiping it.

1. Import a character preset first. The preset armature must be in the scene.
2. Pick the `.mop` from the asset list or from disk, then press **Import Selected** or **Import from file**.
3. Its colour rows go on the matching material inputs. Anything the `.mop` doesn't mention stays as the `.mor` left it.

## Related

- [Importing](importing.md): searching the asset database and importing from disk.
- [Reference: panel controls](reference.md)
- [Reference: supported formats](reference.md#supported-formats)

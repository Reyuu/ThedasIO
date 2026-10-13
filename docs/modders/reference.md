# Reference

Every control, message, and supported format in ThedasIO. For the steps, see the
how-to pages linked from [index](index.md).

## Add-on preferences

Edit > Preferences > Add-ons > ThedasIO.

| Control | Effect |
|---|---|
| **Game folder** | The Dragon Age: Origins install folder. Scan uses it when no scan folder is given. |
| **Enable PHY collision** | Turns on collision import, the Add Collision button, and `.phy` export. Off by default. |

## Assets panel

### Import (sub-panel)

| Control | Effect |
|---|---|
| **Source** (Game / Disk) | Game searches the asset database. Disk imports one file from your computer. |
| **Weld seams** | Merges vertices that sit at the same position into one. On by default. Export accepts either layout. |
| **Extension** | Filters the list to MSH, MMH, ANI, or CUB. (None) shows every extension. |
| **File name** | Filters by name, matching any part of it. |
| **Package** | Filters by the archive the file came from. |
| **Search** | Applies the filters to the database. Does not rescan. |
| **Include AddIns** | Scans the AddIns folder along with the game folder. |
| **AddIns folder** | Shown while Include AddIns is ticked. Defaults to `Documents\BioWare\Dragon Age\AddIns`. |
| **Scan** | Indexes the archives and loose files under the scan folders into the asset database. |
| **Bone** | Shown with PHY support on. The bone that Add Collision attaches to. |
| **Add Collision** | Adds a capsule to the chosen bone. Needs PHY support and a model with a `.phy`. |
| **Import Selected** | Imports the row chosen in the list. A `.ani` row plays on the active armature. |
| **Resolve imports from local files** | Disk only. Looks for sibling `.mao` and textures next to the file before the database. |
| **Import from file** | Disk only. Imports the file picked above. |

### Export (sub-panel)

| Control | Effect |
|---|---|
| **Export to** | Folder that standalone files are written to. |
| **Export tint** | Writes each model's DAO Tint values as a `.tnt`. |
| **Tint resource** | Name of the `.tnt`, for example `t3_arm_rlr.tnt`. Blank uses `<model>.tnt`. |
| **Rename on export** | Shows a per-file name field. Names apply on export. |
| **Reload textures from files** | Reads texture files from disk back into Blender after an external edit. |
| **Check model** | Reports problems that export would fix or fail on, without writing anything. |
| **Export Animation** | Writes the active armature's `.ani` action. See [Animation](animation.md). |
| **Export** | Writes every checked hierarchy. |

### Substance Painter (sub-panel)

| Control | Effect |
|---|---|
| **Folder** | Where the Substance export and import read and write. |
| **Split packed maps** | On splits hair-style maps into basecolor, specular, and opacity. |
| **Keep Substance resolution** | Keeps the painted texture size on import instead of the source size. |
| **Export for Substance** | Writes `model.obj`, `model.mtl`, and `textures/<material>/<map>.png`. |
| **Import from Substance** | Reads the painted maps back and repacks them to DAO layout. |

## Check model messages

Check model prints one warning per note. `Model clean` means there are none.

| Message | Meaning and fix |
|---|---|
| `<label>: N verts have no weights (...); they stay rigid` | Vertices are not bound to any bone. Weight them, or accept rigid behaviour. |
| `<label>: N degenerate tris (...)` | Triangles with zero area. Delete or fix them. |
| `<label>: UV layer n spans a..b (outside [0, 1]; atlases wrap)` | UVs leave the 0-1 range. Intentional for atlases. |
| `<label>: N non-tri faces; export retriangulates them automatically` | Quads and n-gons are split on export. No action needed. |
| `<label>: modifier 'X' is not applied on export` | Apply the modifier first. Armature modifiers are exempt. |
| `<label>: shape keys never export; apply them first` | Shape keys are dropped. Apply them. |
| `<label>: N UV layer(s) removed; export keeps source bytes for those channels` | A UV map was deleted. The original data is kept for it. |
| `<label>: N extra UV layer(s); only the declared N export` | Extra UV maps are ignored on export. |
| `<label>: material 'X' has no MAO (renders untextured in game)` | The material has no MAO. Import its package or add one. |
| `<label>: chunk X unreadable (...)` | The chunk cannot be decoded. The rest of the model still checks. |
| `<file>: N x M is not a multiple of 4; DXT compression requires it` | Texture size needs changing before export. |
| `note: <file>: N x M is not power-of-two` | Informational. Game engines prefer power-of-two sizes. |
| `<file>: unreadable image bytes` | The texture cannot be decoded. |
| `<file>: texture X not imported` | A MAO names a texture that is not in the scene. |

## Export and import messages

| Message | Meaning |
|---|---|
| `Nothing imported yet` | Import a model first. |
| `Nothing checked to export` | Tick at least one row in the export list. |
| `Exported N files to <folder>` | Export finished. |
| `SKIPPED N: ...` | These files failed and were not written. Each entry names the file. The others were written. |
| `Left Edit Mode on X so its edits export` | Edit cages were committed so the edits are included. |
| `No importer for <file>` | The file type cannot be imported (for example `.cub`, `.anb`). |
| `No MAO found for <stem>.mao` | The material file was not found in the database. Scan its package. |
| `<file>: could not read collision (...)` | The `.phy` is damaged. The model still imports without collision. |
| `Select the armature that should play the animation` | Make an armature active first. |
| `No bone in <armature> matched <clip>'s channels` | The animation does not belong to this model. |
| `Could not read <file>: ...` | The file is damaged or not an animation. |

## Scan report

After a scan, the message bar reads:

`Scanned N archive files, N entries, N assets.` followed by any of:

- `skipped N missing folder(s)`: a folder in the scan list does not exist.
- `N unreadable archive(s)`: these archives could not be parsed. Their entries
  are not listed.

## Supported formats

| Format | Import | Export | Notes |
|---|---|---|---|
| `.msh` mesh | Yes | Yes | Index width kept; widens to 32-bit when a mesh outgrows 16-bit. |
| `.mmh` hierarchy | Yes | Yes | Imports the sibling `.msh`, materials, and textures. |
| `.mao` material | With its model | Edited materials | Imported with the model that uses it. Rebuilt as a Specular BSDF with a DAO Tint group. |
| `.dds` / `.tga` textures | With its model | Yes | Imported with the model. Re-encoded to DDS with the source compression. |
| `.tnt` tint | With its model | Yes | Read with the model. Written by Export tint. |
| `.phy` collision | Yes, with PHY on | Yes, with PHY on | Capsule, sphere, and box edit and add. Mesh edit only. |
| `.ani` animation | Yes | Yes | Edited keys re-encoded; untouched channels keep their bytes. |
| `.cub` cutscene blend tree | Read only | No | Listed in the filter, not imported. |
| `.anb`, `.fxe` | No | No | Not supported. |
| `.erf` / `.rim` archives | Read only | Never written | ERF V2.0, V2.1 (zlib), and V2.2 (encrypted DLC) are read. |

### Archives

| Version | Used by | Payload |
|---|---|---|
| ERF V2.0 | Stock game and older repacks | Stored |
| ERF V2.1 | `dao_prc_gib` environment areas | zlib |
| ERF V2.2 | DLC and promotional modules in AddIns | Blowfish encrypted |

V2.2 archives decrypt with the 24 stock DAO module passwords. A repack that uses
another password is still indexed, but importing from it reports `no known
password for ERF module N`.

# Reference

Every control, message, and supported format in ThedasIO. For the steps, see the
how-to pages linked from [index](index.md).

## Add-on preferences

Edit > Preferences > Add-ons > ThedasIO.

| Control | Effect |
|---|---|
| **Game folder** | The Dragon Age: Origins install folder. Scan uses it when no scan folder is given. |
| **Dragon Age II folder** | The Dragon Age II install folder. Scan uses it when no scan folder is given. |
| **Enable PHY collision** | Turns on collision import, the Add Collision button, and `.phy` export. Off by default. |
| **Show all assets (no limit)** | Loads every database row into the asset list. Off by default; use search and paging for a full game install. |

## Assets panel

### Import (sub-panel)

| Control | Effect |
|---|---|
| **Game** | Dragon Age: Origins or Dragon Age II. Each game has its own asset database and archive reader. |
| **Source** (Game / Disk) | Game searches the asset database. Disk imports one file from your computer. |
| **Weld seams** | Merges vertices that sit at the same position into one. On by default. Export accepts either layout. |
| **Extension** | Filters the list to MSH, MMH, ANI, or CUB. (None) shows every extension. |
| **File name** | Filters by name, matching any part of it. |
| **Package** | Filters by archive file name. The list's Archive column shows the same package. |
| **Search** | Applies the filters to the database. Does not rescan. |
| **Include AddIns** | Scans the AddIns/addins folder along with the game folder. |
| **AddIns folder** | Shown while Include AddIns is ticked. Origins defaults to `Documents\BioWare\Dragon Age\AddIns`; DA2 uses the game's `addins` folder. |
| **Scan** | Indexes the archives and loose files under the scan folders into the current game's database. |
| **Bone** | Shown with PHY support on. The bone that Add Collision attaches to. |
| **Add Collision** | Adds a capsule to the chosen bone. Needs PHY support and a model with a `.phy`. |
| **Import Selected** | Imports the row chosen in the list. A `.ani` row plays on the active armature. |
| **Resolve imports from local files** | Disk only. Looks for sibling `.mao` and textures next to the file before the database. |
| **Import from file** | Disk only. Imports the file picked above. |

### Asset list columns

| Column | Effect |
|---|---|
| **File** | Resource name without its extension. |
| **Type** | Extension without the dot. |
| **Resource** | Path inside the archive, when known. In DA2 this distinguishes variants such as `humanfemale` and `humanmale`. Blank if the path could not be recovered. |
| **Archive** | Archive file name. |

The selected-item box below the list shows shortened **Archive** and **Resource**
paths. A leading `...\` means the left side was shortened.

### Export (sub-panel)

| Control | Effect |
|---|---|
| **Export to** | Folder that standalone files are written to. |
| **Export tint** | Writes each model's DAO Tint values as a `.tnt`. Not supported for DA2 binary MAOs. |
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

## Bone map panel

Object Properties > ThedasIO Bone Map. Shown for mesh objects. See
[Bone map](bone-map.md) for the steps and [Bone order](bone-order.md) for why a
bone may be unavailable.

| Control | Effect |
|---|---|
| **Auto-map same-named groups** | Maps every vertex group whose name exactly matches an indexed bone on the armature. |
| Group row | Shows the group and its current mapping, for example `Spine02 (#8)`. Press it to open the picker. |
| **Search** | In the picker, filters bones by name. |
| **Bone** | The bone selected for this group. |
| **Allow bone index assignment for unindexed bones** | Shows bones outside this mesh's bone order and enables the manual Bone index field. Off by default. |
| **Bone index** | Shown when the checkbox is on. Raw MSH bone index; `-1` uses the picked bone. A wrong value weights the group to another bone. |

The `#n` value is the bone's position in the mesh's bone order, not in Blender's
armature. A bone outside that order cannot be mapped by name; the panel hides it
unless the checkbox is on.

### Bone map messages

| Message | Meaning |
|---|---|
| `No armature modifier. Add the model's armature to map groups.` | The mesh has no Armature modifier. |
| `Bone order unknown. Re-import this mesh with its .mmh, then map groups.` | The mesh has no stored bone order. |
| `This rig mixes bone orders. Map groups on the imported mesh.` | Meshes on this armature use different bone orders. |
| `N bone(s) are hidden; enable the checkbox above` | Bones outside the mesh's order are hidden. |
| `N bone(s) need a manual MSH index` | Unindexed bones are visible because the checkbox is on. |
| `N group(s) still need a bone` | One or more vertex groups are unmapped. |
| `X is not in this mesh's bone order` | The selected bone has no valid index for this mesh. |
| `X has no MSH index; set Bone index manually` | The unindexed bone needs a raw index, or a different skeleton. |

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
| `<mesh>: vertex group 'X' has no bone; pick one in Object Properties > ThedasIO Bone Map` | The group is unmapped. Use the Bone Map panel. |
| `binary MAO tint export is not supported; DA2 tint colours live in tints.rim, not the MAO` | Turn off Export tint for a DA2 binary MAO. |
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
| `.mao` material | With its model | Edited materials | Imported with the model that uses it. DAO XML MAOs are rebuilt as Specular BSDF with a DAO Tint group; DA2 binary MAOs are edited in place. |
| `.dds` / `.tga` textures | With its model | Yes | Imported with the model. Re-encoded to DDS with the source compression. |
| `.tnt` tint | With its model | Yes | Read with the model. Written by Export tint for DAO; DA2 tint colours live in `tints.rim`. |
| `.phy` collision | Yes, with PHY on | Yes, with PHY on | Capsule, sphere, and box edit and add. Mesh edit only. |
| `.ani` animation | Yes | Yes | Edited keys re-encoded; untouched channels keep their bytes. |
| `.cub` cutscene blend tree | Read only | No | Listed in the filter, not imported. |
| `.anb`, `.fxe` | No | No | Not supported. |
| `.erf` / `.rim` archives | Read only | Never written | Origins reads ERF V2.0, V2.1, and V2.2. DA2 reads ERF V3 with hashed entry paths. |

### Archives

| Version | Used by | Payload |
|---|---|---|
| ERF V2.0 | Dragon Age: Origins stock game and older repacks | Stored |
| ERF V2.1 | `dao_prc_gib` environment areas | zlib |
| ERF V2.2 | Origins DLC and promotional modules in AddIns | Blowfish encrypted |
| ERF V3.0 | Dragon Age II | Per-entry compression and encryption; paths are hashed |

Origins V2.2 archives decrypt with the 24 stock DAO module passwords. A repack
that uses another password is still indexed, but importing from it reports `no
known password for ERF module N`.

Dragon Age II archives do not store normal file paths. The scan recovers names
and Resource paths where it can; unknown entries remain `hash_...`. A DA2 row
may have an empty Resource column. The Archive column still identifies the
package.

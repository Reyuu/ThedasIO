# Importing

ThedasIO imports models from either Dragon Age game. Choose the game in the
Import panel first; the asset database and archive reader follow that choice.

## From the game database

1. Open the ThedasIO tab, Import panel, and set **Game** to Dragon Age: Origins
   or Dragon Age II.
2. Set the matching folder once in the add-on preferences: **Game folder** for
   Origins or **Dragon Age II folder** for II. Scan uses that folder when no scan
   folder is given.
3. Press **Scan**. It indexes the game's archives and loose models and textures
   into a database. Each game has its own database, so scanning one does not
   replace the other. Origins archives use ERF V2.0, V2.1, and V2.2; DA2 uses ERF
   V3 with hashed entry paths.
4. Tick **Include AddIns** to scan the DLC and mod folder too. For Origins it
   defaults to `Documents\BioWare\Dragon Age\AddIns`; for DA2 it uses the game's
   `addins` folder. A path field appears while the box is ticked if your mods
   live elsewhere. The game folder and AddIns folder are scanned in one pass.
5. Read the scan report in the message bar. It shows the archive, entry, and
   asset counts. If it reports unreadable archives, those archives' entries are
   missing from the list. See [Reference: scan report](reference.md#scan-report).
6. Filter by Extension, File name, and Package. Press **Search**. Search reads
   the database only, so it does not rescan.
7. Select a row. Press **Import Selected**.

Selecting an `.ani` row plays the animation on the active armature instead of
importing a model. See [Animation](animation.md).

Only `.msh` and `.mmh` rows import on their own. Selecting a `.mao`, texture, or
`.cub` row reports `No importer for <file>`. Materials and textures come in with
the model that uses them.

When the same file name appears in more than one place, the copy the game would
actually load wins: the AddIns copy beats the base game, because DLC loads later,
and inside a folder an `override` subfolder beats everything else. Loose files
beside the model always beat archive copies.

The import pulls in the sibling `.msh`, `.mao` and textures automatically (this
works only for `.mmh` files). Materials are rebuilt from the MAO channel packing,
with a DAO Tint node group per side driven by `.tnt` values.

Textures are unpacked to a `thedas_io_textures/` folder beside the `.blend`. Edit
them in an external program, then press Reload textures from files in the Export
panel.

Pose bones, vertex weights and UVs work normally in the viewport. To map new or
renamed vertex groups to bones, see [Bone map](bone-map.md).

A mesh that stores two texture coordinate sets in one cell (some Dragon Age II
meshes do) imports both, as `UVMap` and `UVMap.001`.

**Weld** is on by default: rows sitting at the same position are merged into one
vertex so editing the surface does not crack the seams. Turn it off to keep every
row from the source file as its own vertex, which is what you want when re-UVing
or when the original split matters. Export takes either layout.

## Reading the asset list

| Column | Meaning |
|---|---|
| **File** | Resource name. |
| **Type** | Extension, for example `mmh` or `dds`. |
| **Resource** | The path inside the archive, when known. In DA2 this often distinguishes variants such as `humanfemale` and `humanmale`. |
| **Archive** | The `.erf` or `.rim` package that contains the resource. |

The selected-item box below the list shows shortened **Archive** and **Resource**
paths. A leading `...\` means the left side of the path was shortened. The
Package filter still filters by archive name; the list column is called Archive.

## Dragon Age II names and paths

DA2 archives store hashed paths rather than file names. Scan recovers names and
internal paths where it can, using the model hierarchy references and a bundled
name table. Some resources may still appear as `hash_...`; they are listed but
cannot be identified by name. A DA2 row may also have no Resource path when the
path could not be recovered. The Archive column still identifies the package.

If an existing DA2 database predates Resource path recovery, scan again to fill
the new paths in.

## From disk

Switch the source to Disk. Pick a File, tick Resolve imports from local files to
pick up siblings from disk, and press Import from file.

A `.ani` picked here plays on the active armature, the same as from the database.

## Collision

When PHY support is on (see [Collision](collision.md)), importing a `.mmh` also
imports the `.phy` beside it as proxy shapes on their bones. Without the switch
on, the `.phy` is ignored.

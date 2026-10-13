# Importing

## From the game database

1. Open the ThedasIO tab, Import panel.
2. Press Scan. It indexes every `.rim`/`.erf` archive plus loose models and textures under the folder into the asset database. The database file defaults to a location under the application data folder. Scan reads three archive layouts: stock (V2.0), the zlib archives in `dao_prc_gib`'s environment areas (V2.1), and the encrypted DLC archives (V2.2), which are decrypted with the stock DAO module passwords.
3. Tick **Include AddIns** to scan the AddIns folder as well, so DLC and mods resolve too. It defaults to `Documents\BioWare\Dragon Age\AddIns`; a path field appears while the box is ticked if you keep your mods elsewhere. The game folder and AddIns are scanned in one pass, so you only ever need one database.
4. Read the scan report in the message bar. It shows the archive, entry, and asset counts. If it reports unreadable archives, those archives' entries are missing from the list. See [Reference: scan report](reference.md#scan-report).
5. Filter by Extension, File name, Package. Press Search. Search reads the database only, so it does not rescan.
6. Select a row. Press Import Selected.

Selecting an `.ani` row plays the animation on the active armature instead of importing a model. See [Animation](animation.md).

Only `.msh` and `.mmh` rows import on their own. Selecting a `.mao`, texture, or `.cub` row reports `No importer for <file>`. Materials and textures come in with the model that uses them.

When the same file name appears in more than one place, the copy the game would actually load wins: the AddIns copy beats the base game, because DLC loads later, and inside a folder an `override` subfolder beats everything else. Loose files beside the model always beat archive copies.

The import pulls in the sibling `.msh`, `.mao` and textures automatically (this works only for `.mmh` files). Materials are rebuilt from the MAO channel packing, with a DAO Tint node group per side driven by `.tnt` values.

Textures are unpacked to a `thedas_io_textures/` folder beside the `.blend`. Edit them in an external program, then press Reload textures from files in the Export panel.

Pose bones, vertex weights and UVs work normally in the viewport.

**Weld** is on by default: rows sitting at the same position are merged into one vertex so editing the surface does not crack the seams. Turn it off to keep every row from the source file as its own vertex, which is what you want when re-UVing or when the original split matters. Export takes either layout.

## From disk

Switch the source to Disk. Pick a File, tick Resolve imports from local files to pick up siblings from disk, and press Import from file.

A `.ani` picked here plays on the active armature, the same as from the database.

## Collision

When PHY support is on (see [Collision](collision.md)), importing a `.mmh` also imports the `.phy` beside it as proxy shapes on their bones. Without the switch on, the `.phy` is ignored.

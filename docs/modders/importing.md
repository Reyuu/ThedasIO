# Importing

## From the game database

1. Open the ThedasIO tab, Import panel.
2. Press Scan. It indexes every `.rim`/`.erf` archive plus loose models and textures under the folder into the asset database. The database file defaults to a location under the application data folder.
3. Filter by Extension, File name, Package. Press Search.
4. Select a row. Press Import Selected.

The import pulls in the sibling `.msh`, `.mao` and textures automatically (this works only for `.mmh` files). Materials are rebuilt from the MAO channel packing, with a DAO Tint node group per side driven by `.tnt` values.

Textures are unpacked to a `thedas_io_textures/` folder beside the `.blend`. Edit them in an external program, then press Reload textures from files in the Export panel.

Pose bones, vertex weights and UVs work normally in the viewport.

## From disk

Switch the source to Disk. Pick a File, tick Resolve imports from local files to pick up siblings from disk, and press Import from file.

# Substance Painter round trip

Paint the model textures in Substance Painter and bring them back into DAO layout.

## Exporting for Substance

1. Open the ThedasIO tab, Substance Painter panel. Set Folder.
2. Press Export for Substance. It writes `model.obj` (and its associated `model.mtl`) plus `textures/<material>/<map>.png`, with DAO's packed channels split per the MAO semantic: diffuse RGB to basecolor, specular alpha (gloss) to glossiness, normals unswizzled to DirectX convention.
3. Split packed maps defaults to on. On splits packed hair-style maps into basecolor/specular/opacity; off keeps the map whole so nothing is silently lossy.

## Project setup in Painter

1. Create the project with the **Specular Glossiness** template and **DirectX** normals.
2. Drag the `textures/` folder into Import Resources (usages auto-detect from the file stems) and assign each map to its channel via fill layers.

## Export template (one-time setup)

Build a preset in the Export Textures window's Output Templates tab (by copying `PBR Specular Glossiness`) or copy [Dragon Age Origins.spexp](../../substance/Dragon%20Age%20Origins.spexp) preset to your resources directory.

Rows 1-4 are the working set for standard opaque armour; add 5-7 only if your material actually has those MAO slots (transparent glass/ghosts, glowing runes, parallax height) - the import ignores files with no corresponding slot, so extra maps are harmless but pointless, while a *present-but-unpainted* map overwrites its source:

| File | Source |
|---|---|
| `$textureSet_basecolor` | Diffuse (input map - the Spec-Gloss color channel; *not* Base Color, which doesn't exist in this workflow and exports black) |
| `$textureSet_normal` | Normal DirectX (converted map - baked + painted combined) |
| `$textureSet_specular` | Specular (input map) |
| `$textureSet_glossiness` | Glossiness (input map) |
| `$textureSet_opacity` | Opacity (input map; transparent sets only) |
| `$textureSet_emissive` | Emissive (input map) |
| `$textureSet_height` | Height (input map) |

Point the output directory at the export's `textures/` folder, Size at or above the largest source map (1024+ for armour). Omit output maps for channels the texture set doesn't have - SP warns `can't be generated` for those (emissive on a set without the channel is the usual one) and they export nothing (which is exactly what we want).

## Import from Substance

1. Press Import from Substance. It reads the flat files back, preferring over the per-material subdirectories. Bare `<map>.png` names with no `$textureSet_` prefix also work when a single material is imported, and are reported (not silently dropped) when several are.
2. The maps are repacked to DAO layout and land in the texture cache, and the normal export re-encodes them to DDS with the source compression. By default each map is resampled to its slot's source resolution; tick **Keep Substance resolution** to keep the painted size instead - nothing is downscaled quietly either way, and the live Blender images are rescaled to match so the reload path keeps working.

Tint masks and lightmaps pass through untouched - neither has a paintable equivalent.

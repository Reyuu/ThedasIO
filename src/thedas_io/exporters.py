"""Export operators: models and materials, plus the Substance round trip."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import bpy
from PIL import Image as PilImage

from .asset_db.scanner import leaf_name
from .export import ITEMS, check_model, connected, export_all
from .export_registry import _push_renames
from .fsio import save_image_atomic, write_text_atomic
from .geometry import decode_chunk
from .substance import (
    MAP_NORMAL,
    attribute_bare,
    collect_sp_maps,
    mtl_text,
    obj_text,
    repack_images,
    unpack_images,
    unpack_plan,
)
from .textures import _cache_dir, _scale_live_images, reload_textures


def _commit_edit_cages(context: Any) -> str:
    # leave edit mode so cage edits reach the mesh datablock
    if not context.mode.startswith("EDIT") or context.view_layer.objects.active is None:
        return ""
    name = context.view_layer.objects.active.name
    bpy.ops.object.mode_set(mode="OBJECT")
    return name


class THEDAS_IO_OT_check_model(bpy.types.Operator):
    bl_idname = "thedas_io.check_model"
    bl_label = "Check DAO model"
    bl_description = (
        "Report what export would fix up silently or fail on: "
        "unweighted verts, missing materials and textures, "
        "degenerate faces, non-tri polygons, unapplied "
        "modifiers, shape keys and texture sizes."
    )

    def execute(self, context: Any) -> set:
        if not ITEMS:
            self.report({"WARNING"}, "Nothing imported yet")
            return {"CANCELLED"}

        scene = context.scene
        try:
            rows = scene.thedas_io_export_items
        except AttributeError:
            rows = ()
        checked = {r.filename for r in rows if r.use} if rows else None
        if checked is not None and not checked:
            self.report({"WARNING"}, "Nothing checked to export")
            return {"CANCELLED"}

        notes = check_model(connected(checked) if checked is not None else None)
        for note in notes:
            self.report({"WARNING"}, note)
        self.report({"INFO"}, f"Checked model: {len(notes)} note(s)" if notes else "Model clean")
        return {"FINISHED"}


class THEDAS_IO_OT_export_assets(bpy.types.Operator):
    bl_idname = "thedas_io.export_assets"
    bl_label = "Export DAO assets"
    bl_description = (
        "Write every checked hierarchy as standalone files. "
        "Untouched resources come out byte-identical; edited "
        "meshes, materials and textures are re-encoded"
    )
    directory: bpy.props.StringProperty(  # type: ignore
        name="Folder",
        subtype="DIR_PATH",
        description="Export here instead of the Export to folder for this run",
    )

    def execute(self, context: Any) -> set:
        if not ITEMS:
            self.report({"WARNING"}, "Nothing imported yet")
            return {"CANCELLED"}

        scene = context.scene
        try:
            rows = scene.thedas_io_export_items
        except AttributeError:
            rows = ()
        checked = {r.filename for r in rows if r.use} if rows else None
        if checked is not None and not checked:
            self.report({"WARNING"}, "Nothing checked to export")
            return {"CANCELLED"}

        left = _commit_edit_cages(context)
        dest = bpy.path.abspath(self.directory or scene.thedas_io_export_dir)
        _push_renames(scene)
        written, errors = export_all(
            dest,
            scene.thedas_io_export_tint,
            scene.thedas_io_tint_name,
            connected(checked) if checked is not None else None,
        )

        scene.thedas_io_export_dir = dest
        for error in errors:
            self.report({"WARNING"}, error)
        if left:
            self.report({"INFO"}, f"Left Edit Mode on {left} so its edits export")
        self.report({"INFO"}, f"Exported {len(written)} files to {dest}")
        if errors:
            # partial override is broken, name missing files
            self.report({"ERROR"}, f"SKIPPED {len(errors)}: {'; '.join(errors)}")
        return {"FINISHED"}


def _checked_keys(scene: Any) -> set | None:
    # checked roots. None means "no object list at all, export everything";
    # a list that exists but has nothing ticked resolves to an empty set, so a
    # caller can tell "all" from "none" instead of exporting everything
    try:
        rows = scene.thedas_io_export_items
    except AttributeError:
        return None
    if not rows:
        return None
    return connected({r.filename for r in rows if getattr(r, "use", True)})


class THEDAS_IO_OT_export_substance(bpy.types.Operator):
    bl_idname = "thedas_io.export_substance"
    bl_label = "Export for Substance Painter"
    bl_description = (
        "Write model.obj + model.mtl plus unpacked PNG texture "
        "sets (DirectX normals, Specular-Glossiness layout) for "
        "painting in Substance Painter"
    )
    directory: bpy.props.StringProperty(  # type: ignore
        name="Folder",
        subtype="DIR_PATH",
        description="Export here instead of the Substance folder for this run",
    )

    def execute(self, context: Any) -> set:
        if not ITEMS:
            self.report({"WARNING"}, "Nothing imported yet")
            return {"CANCELLED"}

        scene = context.scene
        folder = self.directory or getattr(scene, "thedas_io_sp_dir", "")
        if not folder:
            # Path("") is Path("."), so an empty folder used to pass the old
            # guard and write model.obj into whatever Blender's cwd happened
            # to be
            self.report({"WARNING"}, "Pick a Substance folder first")
            return {"CANCELLED"}
        dest = Path(bpy.path.abspath(folder))

        scene.thedas_io_sp_dir = str(dest)
        keys = _checked_keys(scene)
        wanted = [k for k in ITEMS if keys is None or k in keys]
        if keys is not None and not wanted:
            self.report({"WARNING"}, "Nothing checked to export")
            return {"CANCELLED"}
        split_packed = bool(getattr(scene, "thedas_io_sp_split_packed", True))

        # chunk to mao via live materials, material equals mao name
        chunks, maos = [], {}
        for key in wanted:
            item = ITEMS[key]
            if item.get("kind") != "msh":
                continue
            mesh = item.get("model")
            if mesh is None:
                continue
            for entry in item.get("objects") or []:
                obj, chunk = entry if isinstance(entry, (tuple, list)) else (entry, None)
                if chunk is None:
                    continue
                try:
                    decoded = decode_chunk(mesh, chunk)
                except (ValueError, ReferenceError):
                    continue
                mat_name = ""
                try:
                    mats = obj.data.materials
                    if mats:
                        mat_name = mats[0].name if mats[0] is not None else ""
                except (AttributeError, ReferenceError):
                    pass
                chunks.append((chunk.name or key, decoded, mat_name))
                if mat_name and mat_name not in maos:
                    hit = next(
                        (
                            ITEMS[k2]
                            for k2 in wanted
                            if ITEMS[k2].get("kind") == "mao"
                            and (ITEMS[k2].get("model") or {}).get("name") == mat_name
                        ),
                        None,
                    )
                    if hit is not None:
                        maos[mat_name] = hit

        if not chunks:
            self.report({"WARNING"}, "No meshes in the checked hierarchies")
            return {"CANCELLED"}

        dest.mkdir(parents=True, exist_ok=True)
        write_text_atomic(dest / "model.obj", obj_text([(n, m, d) for n, d, m in chunks]))
        written, errors = ["model.obj"], []
        tex_dir = dest / "textures"
        mtl_maps: dict = {}
        for mat_name, item in maos.items():
            model = item.get("model")
            if not isinstance(model, dict):
                continue
            images = {}
            for slot, res in (model.get("textures") or {}).items():
                if not res:
                    continue
                src = next(
                    (
                        ITEMS[k2]
                        for k2 in wanted
                        if leaf_name(k2).lower() == leaf_name(res).lower()
                        and ITEMS[k2].get("kind") == "texture"
                    ),
                    None,
                )
                if src is None:
                    errors.append(f"{mat_name}: {res} not imported; skipped")
                    continue
                try:
                    pil = PilImage.open(io.BytesIO(src["raw"]))
                    pil.load()
                    images[slot] = pil
                except (OSError, ValueError) as e:
                    errors.append(f"{mat_name}: {res}: {e}")
                    continue
            try:
                maps = unpack_images(model, images, split_packed)
            except (OSError, ValueError, KeyError) as e:
                errors.append(f"{mat_name}: unpack failed: {e}")
                continue

            out_dir = tex_dir / mat_name
            out_dir.mkdir(parents=True, exist_ok=True)
            plan = unpack_plan(model, split_packed)
            for slot in model.get("textures") or {}:
                stem, note = plan.get(slot, (None, None))
                if stem is None and note:
                    errors.append(f"{mat_name}: {slot}: {note}")

            for stem, pil in maps.items():
                path = out_dir / f"{stem}.png"
                save_image_atomic(pil, path, "PNG")
                written.append(str(path.relative_to(dest)))
            mtl_maps[mat_name] = set(maps)

            # meshmap copy for substance auto-assign, do not rebake in substance
            if MAP_NORMAL in maps:
                meshmap_dir = dest / "meshmaps"
                meshmap_dir.mkdir(parents=True, exist_ok=True)
                meshmap = meshmap_dir / f"{mat_name}_normal_base.png"
                save_image_atomic(maps[MAP_NORMAL], meshmap, "PNG")
                written.append(str(meshmap.relative_to(dest)))

        write_text_atomic(dest / "model.mtl", mtl_text([(m, mtl_maps.get(m, ())) for m in maos]))
        written.append("model.mtl")

        for error in errors:
            self.report({"WARNING"}, error)
        self.report(
            {"INFO"},
            f"Substance set: {len(written)} files in {dest} "
            f"(DirectX normals, Specular-Glossiness template)",
        )
        return {"FINISHED"}


class THEDAS_IO_OT_import_substance(bpy.types.Operator):
    bl_idname = "thedas_io.import_substance"
    bl_label = "Import from Substance Painter"
    bl_description = (
        "Read the $textureSet_$map PNGs Substance exported back "
        "into DAO layout at each slot's source resolution (or "
        "the painted size with Keep Substance resolution), then "
        "reload them into the live images"
    )
    directory: bpy.props.StringProperty(  # type: ignore
        name="Folder",
        subtype="DIR_PATH",
        description="Read Substance output here instead of the Substance folder for this run",
    )

    def execute(self, context: Any) -> set:
        if not ITEMS:
            self.report({"WARNING"}, "Nothing imported yet")
            return {"CANCELLED"}

        scene = context.scene
        base = Path(bpy.path.abspath(self.directory or getattr(scene, "thedas_io_sp_dir", "")))
        src = base / "textures"
        if not src.is_dir():
            self.report({"WARNING"}, f"No textures/ folder in {base} (export for Substance first)")
            return {"CANCELLED"}

        split_packed = bool(getattr(scene, "thedas_io_sp_split_packed", True))
        keep_size = bool(getattr(scene, "thedas_io_sp_keep_resolution", False))
        folder = _cache_dir()

        def read_png(png: Any) -> Any:
            with PilImage.open(png) as im:
                im.load()
                return im.copy()

        have = collect_sp_maps(src)
        mao_names = [
            ((item.get("model") or {}).get("name") or "")
            for item in ITEMS.values()
            if item.get("kind") == "mao" and isinstance(item.get("model"), dict)
        ]
        have, bare_warnings = attribute_bare(have, mao_names)
        done, failed = list(bare_warnings), []

        for item in ITEMS.values():
            if item.get("kind") != "mao":
                continue
            model = item.get("model")
            if not isinstance(model, dict):
                continue
            mat_name = model.get("name") or ""
            paths = have.get(mat_name)
            if not paths:
                continue  # material sp never saw, source bytes stand

            sp = {}
            for stem, png in paths.items():
                try:
                    sp[stem] = read_png(png)
                except OSError as e:
                    failed.append(f"{mat_name}/{png.name}: {e}")

            # dao maps differ per map, sp exports uniform, keep source alpha

            sizes, sources = {}, {}
            for slot, res in (model.get("textures") or {}).items():
                if not res:
                    continue
                src_item = next(
                    (
                        ITEMS[k2]
                        for k2 in ITEMS
                        if leaf_name(k2).lower() == leaf_name(res).lower()
                        and ITEMS[k2].get("kind") == "texture"
                    ),
                    None,
                )
                if src_item is None:
                    continue
                try:
                    with PilImage.open(io.BytesIO(src_item["raw"])) as im:
                        im.load()
                        sizes[slot] = tuple(im.size)
                        sources[slot] = im.copy()
                except OSError:
                    continue

            try:
                repacked = repack_images(
                    model, sp, split_packed, None if keep_size else sizes or None, sources or None
                )
            except (OSError, ValueError, KeyError, RuntimeError) as e:
                failed.append(f"{mat_name}: repack failed: {e}")
                continue

            if keep_size:
                skipped = _scale_live_images(model, repacked, failed)
            else:
                skipped = set()

            for slot, pil in repacked.items():
                if slot in skipped:
                    continue
                res = (model.get("textures") or {}).get(slot)
                if not res:
                    continue
                # dao-layout pixels into cache, reload path re-packs into blend
                try:
                    (folder / leaf_name(res)).parent.mkdir(parents=True, exist_ok=True)
                    save_image_atomic(pil, folder / leaf_name(res), "PNG")
                except OSError as e:
                    failed.append(f"{mat_name}: {res}: {e}")
                    continue
                done.append(f"{mat_name}/{res}")

        reloaded, _, reload_failed = reload_textures()
        failed.extend(reload_failed)
        for note in failed:
            self.report({"WARNING"}, note)

        if not done and not reloaded:
            self.report({"WARNING"}, "Nothing to import: no <material>/*.png found")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Imported {len(done)} map(s) from {src}")
        return {"FINISHED"}

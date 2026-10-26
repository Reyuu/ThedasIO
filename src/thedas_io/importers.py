"""Asset import: one database row (or disk file) to a live Blender scene."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import bpy

from .ani.operators import _play_animation
from .asset_browser import _scene_db
from .asset_db.scanner import _db_path, find_exact
from .assets import extract, importer_for, resolve
from .blender_materials import apply_materials
from .builders import build_chunk_object, build_hierarchy
from .export import remember
from .export_registry import _show_materials, _store_registry, _sync_export_items
from .mmh.description import MMHReader
from .msh.description import MSHReader
from .phy.operators import _load_collision


def _import_row(
    context: Any,
    row: dict,
    db: Any,
    local_only: bool,
    emit: Any,
    weld: bool = True,
) -> set:
    # shared msh/mmh import for one row, emit reports to user
    game = getattr(context.scene, "thedas_io_game", "DAO")
    # a blank override must still mean this game's default db, not DAO's
    db = _db_path(db, game)

    if not row.get("name_hash"):
        hit = find_exact(row["filename"], db_path=db, game=game)
        if hit is not None and hit["path"] == row["path"]:
            row = {**row, "name_hash": hit["name_hash"]}

    local = extract(row)
    kind = importer_for(row["ext"])
    if kind == "msh":
        raw = local.read_bytes()
        mesh = MSHReader(raw).mesh
        objs = [
            build_chunk_object(context, mesh, chunk, raw, row["filename"], weld)
            for chunk in mesh.chunks  # type: ignore
        ]
        notes: list = []
        stem = Path(row["filename"]).stem
        mao = resolve(f"{stem}.mao", row, db, local_only, game=game)
        if mao is None:
            notes.append(
                f"No MAO found for {stem}.mao (an .msh names no material; "
                "import its .mmh for textures)"
            )
        else:
            apply_materials(mao, objs, row=row, db_path=db, notes=notes, local_only=local_only)

        for note in dict.fromkeys(notes):
            emit({"WARNING"}, note)
        emit({"INFO"}, f"Imported {len(mesh.chunks)} chunks from {row['filename']}")  # type: ignore
        return {"FINISHED"}
    if kind == "mmh":
        raw = local.read_bytes()
        tree = MMHReader(raw).tree

        chunks = {}
        msh_name = str(tree.values.get(6005) or "")  # type: ignore
        sibling = resolve(msh_name, row, db, local_only, game=game)
        if sibling is not None and sibling.is_file():
            sraw = sibling.read_bytes()
            smesh = MSHReader(sraw).mesh
            for chunk in smesh.chunks:  # type: ignore
                chunks[chunk.name] = build_chunk_object(context, smesh, chunk, sraw, msh_name, weld)

        reg = remember(row["filename"], "mmh", tree, raw)
        stats = build_hierarchy(context, tree, chunks)
        reg["hosts"] = stats["hosts"]
        reg["armature"] = stats["armature"]
        reg["meshes"] = list(chunks.values())  # tint export reads their materials
        notes = []
        stem = Path(row["filename"]).stem
        for name in stats["linked"]:
            node = tree.find(name)  # type: ignore
            linked = chunks.get(node.values.get(6006))
            if linked is not None:
                mesh_mao, tried = _mao_for_node(node, stem, row, db, local_only)
                if mesh_mao is None:
                    notes.append(f"No MAO found for {', '.join(tried)} (scan its package first)")
                else:
                    apply_materials(
                        mesh_mao,
                        [linked],
                        node.values.get(6335),
                        row=row,
                        db_path=db,
                        notes=notes,
                        local_only=local_only,
                    )
        note = f", missing chunks: {stats['missing']}" if stats["missing"] else ""
        collisions = _load_collision(
            context, row, stem, reg, stats["armature"], db, local_only, emit
        )
        for warning in dict.fromkeys(notes):
            emit({"WARNING"}, warning)
        col = f", {len(collisions)} collision" if collisions else ""
        emit(
            {"INFO"},
            f"Imported {tree.name}: "  # type: ignore
            f"{stats['bones']} bones, {stats['empties']} empties, "
            f"{len(stats['linked'])} meshes{col}{note}",
        )
        return {"FINISHED"}
    emit({"WARNING"}, f"No importer for {row['filename']}")
    return {"CANCELLED"}


def _mao_for_node(
    node: Any,
    stem: str,
    row: dict,
    db_path: Any = None,
    local_only: bool = False,
) -> Any:
    # mao file for one mshh node, tries verbatim then lowered

    tried = []
    matobj = node.values.get(6001)
    if matobj:
        tried.append(f"{matobj}.mao")
    tried.append(f"{stem}.mao")

    for name in dict.fromkeys(tried):
        hit = resolve(name, row, db_path, local_only)
        if hit is not None:
            return hit, tried
    return None, tried


class THEDAS_IO_OT_import_asset(bpy.types.Operator):
    bl_idname = "thedas_io.import_asset"
    bl_label = "Import DAO asset"
    bl_description = (
        "Import the selected asset: a model with its sibling mesh, "
        "material and textures resolved from the database, "
        "or a .ani played on the active armature"
    )

    def execute(self, context: Any) -> set:
        scene = context.scene
        try:
            item = scene.thedas_io_assets[scene.thedas_io_index]
        except IndexError:
            self.report({"WARNING"}, "No asset selected")
            return {"CANCELLED"}
        row = {
            "package": item.package or None,
            "filename": item.filename,
            "ext": item.ext,
            "path": item.path,
            "name_hash": item.name_hash or None,
        }
        if str(item.ext).lower().lstrip(".") == "ani":
            try:
                raw = Path(str(extract(row))).read_bytes()
            except Exception as exc:  # noqa: BLE001
                self.report({"ERROR"}, f"Could not extract {item.filename}: {exc}")
                return {"CANCELLED"}
            return _play_animation(context, raw, item.filename, 30.0, self.report)
        result = _import_row(
            context, row, _scene_db(scene), False, self.report, scene.thedas_io_weld
        )

        if result == {"FINISHED"}:
            _store_registry(scene)
            _sync_export_items(scene)
            _show_materials()
        return result


class THEDAS_IO_OT_import_file(bpy.types.Operator):
    bl_idname = "thedas_io.import_file"
    bl_label = "Import DAO file"
    bl_description = (
        "Import one file from disk: a .msh/.mmh model, resolving its "
        "siblings next to the file instead of from the game, "
        "or a .ani played on the active armature"
    )

    def execute(self, context: Any) -> set:
        scene = context.scene
        path = bpy.path.abspath(scene.thedas_io_disk_file or "")
        if not path or not Path(path).is_file():
            self.report({"WARNING"}, "Pick a file first")
            return {"CANCELLED"}
        p = Path(path)
        if p.suffix.lower() == ".ani":
            try:
                raw = p.read_bytes()
            except OSError as exc:
                self.report({"ERROR"}, f"Could not read {p.name}: {exc}")
                return {"CANCELLED"}
            return _play_animation(context, raw, p.name, 30.0, self.report)
        row = {"package": None, "filename": p.name, "ext": p.suffix, "path": str(p)}
        result = _import_row(
            context,
            row,
            None,
            scene.thedas_io_disk_local,
            self.report,
            scene.thedas_io_weld,
        )

        if result == {"FINISHED"}:
            _store_registry(scene)
            _sync_export_items(scene)
            _show_materials()
        return result

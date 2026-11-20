"""Two SVGs from one Scene: text-free graphics and the annotated final figure.

Both variants come from the same validated Scene and the same emitter; the
graphics variant only removes text and objects the host explicitly marked as
text decoration. It never redraws geometry and never writes back to the project.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from .project import json_text, load_project
from .svg import to_svg


def svg_sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _semantic(obj):
    value = obj.get("semantic")
    return value if isinstance(value, dict) else {}


def is_text_object(obj):
    """Text kinds plus host-declared text paths and text decorations."""
    semantic = _semantic(obj)
    return (obj["kind"] == "text" or semantic.get("layer") == "text"
            or semantic.get("text_decoration") is True or "label_for" in semantic)


def _prune_orphan_groups(scene, excluded):
    """Remove groups left with no visible descendant and no outside reference."""
    by_id = {obj["id"]: obj for obj in scene["objects"]}
    children = {}
    for obj in scene["objects"]:
        children.setdefault(obj.get("parent"), []).append(obj["id"])
    referenced = set()
    for obj in scene["objects"]:
        if obj["id"] in excluded:
            continue
        geometry = obj.get("geometry") or {}
        if obj["kind"] == "connector":
            for key in ("from", "to"):
                if isinstance(geometry.get(key), str):
                    referenced.add(geometry[key])
        for key in ("clip", "mask"):
            if isinstance(obj.get(key), str):
                referenced.add(obj[key])
    for relation in scene.get("occlusions", []):
        for key in ("over", "under", "frame"):
            if isinstance(relation.get(key), str):
                referenced.add(relation[key])
    removed, changed = [], True
    live = {obj["id"] for obj in scene["objects"] if obj["id"] not in excluded}
    while changed:
        changed = False
        for oid in sorted(live):
            obj = by_id[oid]
            if obj["kind"] != "group" or oid in referenced:
                continue
            if not any(child in live for child in children.get(oid, [])):
                live.discard(oid)
                removed.append(oid)
                changed = True
    return live, removed


def graphics_scene(scene, *, exclude=()):
    """Return a validated copy without text and text decorations, plus IDs."""
    ids = {obj["id"] for obj in scene["objects"]}
    excluded = {oid for oid in exclude}
    unknown = excluded - ids
    if unknown:
        raise ValueError("unknown object to exclude: " + ", ".join(sorted(unknown)))
    excluded |= {obj["id"] for obj in scene["objects"] if is_text_object(obj)}
    parents = {obj["id"]: obj.get("parent") for obj in scene["objects"]}
    for oid, parent in parents.items():
        if oid not in excluded and parent in excluded:
            raise ValueError(f"excluded object is an ancestor of {oid}; unparent it first")
    for obj in scene["objects"]:
        if obj["kind"] == "connector" and obj["id"] not in excluded:
            geometry = obj.get("geometry") or {}
            if geometry.get("from") in excluded or geometry.get("to") in excluded:
                excluded.add(obj["id"])
    for relation in scene.get("occlusions", []):
        if {relation.get("over"), relation.get("under"), relation.get("frame")} & excluded:
            raise ValueError("occlusion references excluded text; bake or remove it first")
    result = copy.deepcopy(scene)
    result["objects"] = [obj for obj in result["objects"] if obj["id"] not in excluded]
    live, pruned = _prune_orphan_groups(result, excluded)
    result["objects"] = [obj for obj in result["objects"] if obj["id"] in live]
    return result, sorted(excluded), pruned


def export_variants(project, output, *, exclude=()):
    """Write figure.graphics.svg, figure.final.svg and a binding report."""
    scene = load_project(project)
    final = to_svg(scene)
    graphics_scene_value, excluded, pruned = graphics_scene(scene, exclude=exclude)
    graphics = to_svg(graphics_scene_value)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    targets = {"figure.graphics.svg": graphics, "figure.final.svg": final}
    for name in (*targets, "variants.json"):
        if (output / name).exists():
            raise ValueError("variant output already exists: " + str(output / name))
    for name, text in targets.items():
        (output / name).write_bytes(text.encode("utf-8"))
    report = {"schema_version": "0.1", "kind": "export_variants",
              "project": str(Path(project).resolve()), "revision": scene["revision"],
              "canvas": scene["canvas"],
              "variants": {name: {"sha256": svg_sha256(text), "bytes": len(text.encode("utf-8"))}
                           for name, text in targets.items()},
              "object_count": len(scene["objects"]),
              "graphics_object_count": len(graphics_scene_value["objects"]),
              "excluded_objects": excluded, "pruned_groups": pruned,
              "text_detection": "kind=text, semantic.layer=text, semantic.text_decoration or semantic.label_for",
              "limitations": [
                  "Path-drawn text is removed only when the host marks that object with semantic.layer='text'.",
                  "Both variants share the Scene canvas, IDs and untouched geometry; nothing is redrawn.",
                  "Unused gradients, clips and masks may remain in defs; they are inert.",
                  "The project itself is unchanged; this writes a new directory only."]}
    (output / "variants.json").write_bytes(json_text(report).encode("utf-8"))
    return {"status": "success", "output": str(output.resolve()),
            "project": report["project"], "revision": report["revision"],
            "object_count": report["object_count"],
            "graphics_object_count": report["graphics_object_count"],
            "excluded_objects": excluded, "pruned_groups": pruned,
            "variants": report["variants"]}

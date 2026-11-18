"""Transactional in-memory edits and dependency-closed object export."""
from __future__ import annotations
import copy
import re
from .geometry import Geometry, inverse, point, union
from .scene import validate


def _apply_set(nodes, operation):
    """Apply object fields to a transaction copy without validating or committing it."""
    if (not isinstance(operation, dict) or set(operation) != {"id", "set"}
            or not isinstance(operation["id"], str) or operation["id"] not in nodes):
        raise ValueError("patch must select an existing object ID")
    updates = operation["set"]
    if not isinstance(updates, dict) or not updates or set(updates)-{"geometry", "style", "transform", "visible", "semantic"}:
        raise ValueError("unsupported patch fields; object IDs/parents are immutable in prototype")
    obj = nodes[operation["id"]]
    for key, value in updates.items():
        if key in {"geometry", "style", "semantic"}:
            if not isinstance(value, dict):
                raise ValueError(f"{key} patch must be an object")
            obj[key] = {**obj.get(key, {}), **copy.deepcopy(value)}
        else:
            obj[key] = copy.deepcopy(value)


def apply_patch(scene, patch):
    if patch.get("schema_version") in ("0.2", "0.3", "0.4"):
        from .lifecycle import structural_patch
        return structural_patch(scene, patch)
    if set(patch)!={"base_revision","operations"} or patch["base_revision"]!=scene["revision"]:
        raise ValueError("revision_conflict or invalid patch fields")
    result=copy.deepcopy(scene)
    nodes={o["id"]:o for o in result["objects"]}
    if not isinstance(patch["operations"],list) or not patch["operations"]:
        raise ValueError("patch operations must be a nonempty array")
    for operation in patch["operations"]:
        _apply_set(nodes, operation)
    result["revision"]+=1
    validate(result)
    return result


def export_scene(scene, selected, padding=4):
    validate(scene)
    baked_report = None
    if scene.get("occlusions"):
        from .scene_effects import bake_occlusions
        scene = bake_occlusions(scene)
        baked_report = scene["provenance"]["occlusion_export"]
    if not selected or len(set(selected))!=len(selected):raise ValueError("select unique object IDs")
    if isinstance(padding,bool) or not isinstance(padding,(int,float)) or not 0<=padding<=100:
        raise ValueError("export padding must be 0..100")
    geo=Geometry(scene)
    if set(selected)-set(geo.nodes):raise ValueError("unknown object ID in export")
    keep=set()
    def descendants(oid):
        keep.add(oid)
        for child in geo.children.get(oid,[]):descendants(child)
    for oid in selected:descendants(oid)
    box=union([(geo.visible_bounds(oid) if scene["schema_version"] in {"0.2", "0.3"} else geo.bounds(oid)) for oid in selected])
    if box is None:raise ValueError("selected objects have no visible bounds")
    for oid in list(keep):
        parent=geo.nodes[oid].get("parent")
        while parent:
            keep.add(parent);parent=geo.nodes[parent].get("parent")
    objects=[]
    for original in scene["objects"]:
        if original["id"] not in keep:continue
        obj=copy.deepcopy(original)
        if obj["kind"]=="connector":
            # Freeze the exported line so it does not require unselected endpoint objects.
            p1,p2=geo.endpoints(obj["id"])
            inv=inverse(geo.world(obj["id"]))
            p1,p2=point(inv,*p1),point(inv,*p2)
            obj["kind"]="path"
            obj["geometry"]={"d":f"M {p1[0]} {p1[1]} L {p2[0]} {p2[1]}"}
            obj["style"]={**obj.get("style",{}),"fill":"none","arrow_end":True}
        objects.append(obj)
    references=set()
    for obj in objects:
        references.update(obj[key] for key in ("clip", "mask") if key in obj)
        for value in obj.get("style",{}).values():
            if isinstance(value,str):references.update(re.findall(r"url\(#([\w.-]+)\)",value))
    if scene["schema_version"] in {"0.2", "0.3"}:
        from .composition import _semantic
        class ResourceRefs(dict):
            def __getitem__(self, key):
                references.add(key)
                return super().__getitem__(key)
        resource_map = ResourceRefs((r["id"], r["id"]) for r in scene.get("resources", []))
        for obj in objects:
            if "semantic" in obj:
                _semantic(obj["semantic"], {oid: oid for oid in keep}, resource_map)
    width,height=max(1,box[2]-box[0]+2*padding),max(1,box[3]-box[1]+2*padding)
    result={"schema_version":scene["schema_version"],"revision":scene["revision"],
            "canvas":{"width":width,"height":height,"viewBox":[box[0]-padding,box[1]-padding,width,height],"background":"none"},
            "objects":objects,"resources":[copy.deepcopy(r) for r in scene.get("resources",[]) if r["id"] in references],
            "provenance":{"mode":"object-export","selected":selected,"source_revision":scene["revision"]}}
    if baked_report is not None:
        result["provenance"]["occlusion_export"] = baked_report
    validate(result)
    return result

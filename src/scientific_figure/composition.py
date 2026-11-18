"""Compose editable Scenes, never flattened SVGs.

Matrices map original local scene units directly into the declared parent; no
viewBox translation, viewport scaling, fitting or clipping is implicit. A painted
asset canvas requires asset_background='preserve' (an editable local rect) or
'omit', on the plan or placement. Root siblings follow declaration/placement order;
optional order entries use the existing structural Patch sibling-order contract.

Semantic references are explicit object_id/resource_id, object_ids/resource_ids,
or typed {type: object|object_ref|resource|resource_ref, id|ref: local_id} records
(kind may replace type). Ordinary semantic strings are never guessed to be IDs.
"""
from __future__ import annotations

import copy
from pathlib import Path

from .composition_context import (FILENAME, build_composition_context, namespaced_id,
                                  snapshot_member, validate_composition_context)
from .context import canonical_hash
from .bundles import validate_bundle
from .editing import apply_patch
from .fonts import font_info
from .project import load_project, write_project
from .scene import REF, load_json, schema_validate, validate


_name = namespaced_id


def _plan_version(plan):
    """Do not let a newer member silently widen the old plan's vocabulary."""
    schema_validate(plan, "composition-plan-0.2" if plan.get("schema_version") == "0.2" else "composition-plan")
    # Container/style values were historically checked by Scene0.1. Keep that
    # check even if imported member geometry promotes the resulting Scene.
    if plan["schema_version"] == "0.1":
        probe = {"schema_version": "0.1", "revision": 0, "canvas": plan["canvas"],
                 "objects": copy.deepcopy(plan.get("containers", []))}
        validate(probe)
        for connection in plan.get("connections", []):
            probe["objects"] = [{"id": "style_probe", "kind": "rect",
                                 "geometry": {"x": 0, "y": 0, "width": 1, "height": 1},
                                 "style": connection.get("style", {})}]
            validate(probe)
        return "0.1"
    new_styles = {"stroke_dasharray", "stroke_dashoffset"}
    uses_new = any("anchors" in obj or new_styles & obj.get("style", {}).keys()
                   for obj in plan.get("containers", []) + plan.get("connections", []))
    uses_new = uses_new or any(connection[end]["anchor"].startswith("port:")
                               for connection in plan.get("connections", []) for end in ("from", "to"))
    return "0.3" if uses_new else "0.1"


def _semantic(value, objects, resources):
    if isinstance(value, list):
        return [_semantic(v, objects, resources) for v in value]
    if not isinstance(value, dict):
        return copy.deepcopy(value)
    result = {}
    typed = value.get("type", value.get("kind"))
    typed_map = (objects if typed in {"object", "object_ref"} else
                 resources if typed in {"resource", "resource_ref"} else None) if isinstance(typed, str) else None
    for key, item in value.items():
        mapping = objects if key in {"object_id", "object_ids"} else resources if key in {"resource_id", "resource_ids"} else None
        if mapping is None and typed_map is not None and key in {"id", "ref"}:
            mapping = typed_map
        if mapping is not None:
            def resolve(oid):
                if not isinstance(oid, str) or oid not in mapping:
                    raise ValueError("unknown semantic typed reference")
                return mapping[oid]
            if key in {"object_ids", "resource_ids"}:
                if not isinstance(item, list):
                    raise ValueError("semantic reference lists must be arrays")
                result[key] = [resolve(oid) for oid in item]
            else:
                result[key] = resolve(item)
        else:
            result[key] = _semantic(item, objects, resources)
    return result


def compose(plan_path, bundle_directory, output, *, scale=3):
    """Write a fresh composite project and return its normal project receipt.

    Added receipt fields: bundle_sha256 and id_map_location. The exact per-instance
    maps live in scene.json#/provenance/composition/instances/<instance_id>/id_map
    with separate objects/resources maps and a wrapper_id alongside them. Bundle
    digest, source evidence and member receipt/artifact hashes are embedded in the
    Scene, so the existing review binding covers the imported provenance as well.
    """
    plan_path = Path(plan_path)
    plan = load_json(plan_path)
    scene_version = _plan_version(plan)
    verified = validate_bundle(bundle_directory)
    bundle = Path(verified["bundle"])
    if Path(output).resolve().is_relative_to(bundle.resolve()):
        raise ValueError("composition output cannot modify its immutable bundle")
    assets = {a["id"]: a for a in verified["manifest"]["assets"]}
    instances, members, objects, resources = {}, {}, [], []
    snapshots, occlusions = {}, []
    declarations = set()
    containers = {c["id"]: c for c in plan.get("containers", [])}
    if len(containers) != len(plan.get("containers", [])):
        raise ValueError("duplicate container ID")
    declarations.update(containers)
    for item in plan["placements"] + plan.get("connections", []):
        label = item.get("instance_id", item.get("id"))
        if label in declarations:
            raise ValueError("duplicate composition declaration ID")
        declarations.add(label)
    def parent_of(item):
        parent = item.get("parent")
        if parent is not None and (parent not in containers or containers[parent]["kind"] != "group"):
            raise ValueError("composition parent must name a declared group container")
        return parent
    for container in plan.get("containers", []):
        parent_of(container)
        objects.append(copy.deepcopy(container))
    for placement in plan["placements"]:
        aid, iid = placement["asset_id"], placement["instance_id"]
        if aid not in assets:
            raise ValueError("unknown bundle asset ID")
        asset = assets[aid]
        source = load_project(bundle / asset["project"])
        if source["schema_version"] not in {"0.1", "0.2", "0.3"}:
            raise ValueError("unsupported composition member Scene version")
        scene_version = max(scene_version, source["schema_version"])
        if aid not in snapshots:
            snapshots[aid] = snapshot_member(source, bundle / asset["project"])
        # Fonts are recorded dependencies and are never copied into bundles.
        # A different selected font would silently invalidate text measurements.
        if asset["font"]["sha256"] != font_info()["sha256"]:
            raise ValueError("bundle font differs from the selected runtime font")
        policy = placement.get("asset_background", plan.get("asset_background"))
        painted = source["canvas"].get("background", "none") != "none"
        if painted and policy is None:
            raise ValueError("painted asset canvas requires an explicit asset_background policy")
        wrapper = _name(iid, "g")
        maps = {"objects": {o["id"]: _name(iid, "o", o["id"]) for o in source["objects"]},
                "resources": {r["id"]: _name(iid, "r", r["id"]) for r in source.get("resources", [])}}
        instances[iid] = {"asset_id": aid, "wrapper_id": wrapper, "id_map": maps,
                          "source_viewBox": copy.deepcopy(asset["viewBox"]),
                          "asset_background": policy or "omit"}
        if aid not in members:
            member_receipt = load_json(bundle / asset["project"] / "receipt.json")
            members[aid] = {"member_receipt_sha256": asset["member_receipt_sha256"],
                            "artifacts": copy.deepcopy(member_receipt["artifacts"]),
                            "source": copy.deepcopy(asset["source"]),
                            "runtime_sha256": asset["runtime_sha256"],
                            "font_sha256": asset["font"]["sha256"]}
        root = {"id": wrapper, "kind": "group", "transform": copy.deepcopy(placement["transform"])}
        parent = parent_of(placement)
        if parent is not None:
            root["parent"] = parent
        objects.append(root)
        if painted and policy == "preserve":
            x, y, width, height = asset["viewBox"]
            background_id = _name(iid, "b")
            objects.append({"id": background_id, "kind": "rect", "parent": wrapper,
                            "geometry": {"x": x, "y": y, "width": width, "height": height},
                            "style": {"fill": source["canvas"]["background"]}})
            instances[iid]["background_id"] = background_id
        for resource in source.get("resources", []):
            resource = copy.deepcopy(resource)
            resource["id"] = maps["resources"][resource["id"]]
            resources.append(resource)
        for relation in source.get("occlusions", []):
            relation = copy.deepcopy(relation)
            relation["id"] = _name(iid, "x", relation["id"])
            relation["over"] = maps["objects"][relation["over"]]
            relation["under"] = maps["objects"][relation["under"]]
            relation["frame"] = maps["objects"][relation["frame"]] if relation["frame"] else wrapper
            occlusions.append(relation)
        for original in source["objects"]:
            obj = copy.deepcopy(original)
            obj["id"] = maps["objects"][original["id"]]
            obj["parent"] = maps["objects"][original["parent"]] if original.get("parent") else wrapper
            if obj["kind"] == "connector":
                for end in ("from", "to"):
                    obj["geometry"][end] = maps["objects"][obj["geometry"][end]]
            for key in ("fill", "stroke"):
                paint = obj.get("style", {}).get(key)
                reference = REF.fullmatch(paint) if isinstance(paint, str) else None
                if reference:
                    obj["style"][key] = f"url(#{maps['resources'][reference[1]]})"
            for effect in ("clip", "mask"):
                if effect in obj:
                    obj[effect] = maps["resources"][obj[effect]]
            if "semantic" in obj:
                obj["semantic"] = _semantic(obj["semantic"], maps["objects"], maps["resources"])
            objects.append(obj)
        if len(objects) > 3000 or len(resources) > 3000:
            raise ValueError("composition exceeds object/resource limits")
    def endpoint(record):
        instance = instances.get(record["instance_id"])
        if instance is None or record["object_id"] not in instance["id_map"]["objects"]:
            raise ValueError("unknown composition connection endpoint")
        return instance["id_map"]["objects"][record["object_id"]]
    for connection in plan.get("connections", []):
        obj = {"id": connection["id"], "kind": "connector",
               "geometry": {"from": endpoint(connection["from"]), "to": endpoint(connection["to"]),
                            "from_anchor": connection["from"]["anchor"], "to_anchor": connection["to"]["anchor"]},
               "style": copy.deepcopy(connection.get("style", {"stroke": "#000000", "stroke_width": 2}))}
        parent = parent_of(connection)
        if parent is not None:
            obj["parent"] = parent
        objects.append(obj)
    scene = {"schema_version": scene_version, "revision": 0, "canvas": copy.deepcopy(plan["canvas"]),
             "objects": objects, "resources": resources,
             "provenance": {"mode": "composition", "composition": {
                 "schema_version": "0.1", "bundle_sha256": verified["bundle_sha256"],
                 "members": members, "instances": instances}}}
    if occlusions:
        scene["occlusions"] = occlusions
    validate(scene)
    if plan.get("order"):
        aliases = {iid: entry["wrapper_id"] for iid, entry in instances.items()}
        orders, parents = [], set()
        for order in plan["order"]:
            parent = order["parent"]
            if parent is not None and parent not in containers:
                raise ValueError("order parent must name a declared group container")
            if parent in parents:
                raise ValueError("duplicate sibling order declaration")
            parents.add(parent)
            orders.append({"op": "reorder", "parent": parent,
                           "order": [aliases.get(oid, oid) for oid in order["order"]]})
        scene = apply_patch(scene, {"schema_version": "0.3", "base_revision": 0, "operations": orders})
    context = build_composition_context(plan, verified["bundle_sha256"], snapshots, instances)
    scene["provenance"]["composition"]["context_sha256"] = canonical_hash(context)
    extras = {FILENAME: context}
    validate_composition_context(scene, extras)
    # Reverify immediately before committing; no output is written on a changed bundle.
    if validate_bundle(bundle)["bundle_sha256"] != verified["bundle_sha256"]:
        raise ValueError("bundle changed during composition")
    receipt = write_project(scene, output, scale=scale, extras=extras)
    return {**receipt, "bundle_sha256": verified["bundle_sha256"],
            "id_map_location": "scene.json#/provenance/composition/instances/<instance_id>/id_map"}

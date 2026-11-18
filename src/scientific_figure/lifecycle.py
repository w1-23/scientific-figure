"""Atomic structural editing with stable roots and explicit reference migration."""
from __future__ import annotations
import copy

from .scene import validate


def descendants(scene, root):
    nodes = {o["id"]: o for o in scene["objects"]}
    if root not in nodes:
        raise ValueError("unknown subtree root")
    keep = {root}
    changed = True
    while changed:
        previous = len(keep)
        keep.update(o["id"] for o in scene["objects"] if o.get("parent") in keep)
        changed = len(keep) != previous
    return keep


def structural_patch(scene, patch):
    validate(scene)
    if patch.get("schema_version") == "0.4":
        return _exact_patch(scene, patch)
    if (set(patch) != {"schema_version", "base_revision", "operations"}
            or patch["schema_version"] not in ("0.2", "0.3") or patch["base_revision"] != scene["revision"]
            or patch["schema_version"] == "0.3" and type(patch["base_revision"]) is not int):
        raise ValueError("revision_conflict or invalid structural patch")
    if not isinstance(patch["operations"], list) or not patch["operations"] or not all(isinstance(op,dict) for op in patch["operations"]):
        raise ValueError("structural operations must be nonempty objects")
    result = copy.deepcopy(scene)
    lineage = []
    for operation in patch["operations"]:
        kind = operation.get("op")
        nodes = {o["id"]: o for o in result["objects"]}
        if kind == "set":
            from .editing import _apply_set, apply_patch
            if set(operation) != {"op", "id", "set"}:
                raise ValueError("invalid set operation")
            fields = {"id": operation["id"], "set": operation["set"]}
            if patch["schema_version"] == "0.3":
                # References may be temporarily unresolved until the transaction ends.
                _apply_set(nodes, fields)
            else:
                # Preserve 0.2's per-set validation without an extra committed revision.
                result = apply_patch(result, {"base_revision": result["revision"], "operations": [fields]})
                result["revision"] -= 1
        elif patch["schema_version"] == "0.3" and kind == "add_resource":
            if set(operation) != {"op", "resource"} or not isinstance(operation["resource"], dict):
                raise ValueError("invalid add_resource")
            resource = operation["resource"]
            if set(resource)-{"id", "kind", "start", "end", "stops"}:
                raise ValueError("unsupported resource fields")
            result.setdefault("resources", []).append(copy.deepcopy(resource))
            lineage.append({"operation": kind, "id": resource.get("id")})
        elif patch["schema_version"] == "0.3" and kind in ("update_resource", "remove_resource"):
            expected = {"op", "id", "set"} if kind == "update_resource" else {"op", "id"}
            if set(operation) != expected or not isinstance(operation["id"], str):
                raise ValueError(f"invalid {kind}")
            resources = result.get("resources", [])
            index = next((i for i, resource in enumerate(resources) if resource.get("id") == operation["id"]), None)
            if index is None:
                raise ValueError("unknown resource ID")
            if kind == "update_resource":
                updates = operation["set"]
                if not isinstance(updates, dict) or not updates or set(updates)-{"kind", "start", "end", "stops"}:
                    raise ValueError("unsupported resource update fields; resource IDs are immutable")
                resources[index].update(copy.deepcopy(updates))
            else:
                resources.pop(index)
            lineage.append({"operation": kind, "id": operation["id"]})
        elif kind == "replace_subtree":
            if set(operation)-{"op", "id", "objects", "id_map"} or not {"id", "objects"} <= set(operation):
                raise ValueError("invalid subtree replacement")
            root = operation["id"]
            old = descendants(result, root)
            replacement = copy.deepcopy(operation["objects"])
            if not isinstance(replacement, list) or not replacement or replacement[0].get("id") != root:
                raise ValueError("replacement must start with the stable root")
            new_ids = {o["id"] for o in replacement}
            if len(new_ids) != len(replacement) or new_ids & (set(nodes)-old):
                raise ValueError("replacement ID collision")
            original = nodes[root]
            if replacement[0].get("parent", original.get("parent")) != original.get("parent"):
                raise ValueError("replacement cannot silently reparent root")
            for field in ("parent", "transform", "style"):
                if field not in replacement[0] and field in original:
                    replacement[0][field] = copy.deepcopy(original[field])
            replacement[0]["semantic"] = {**original.get("semantic", {}), **replacement[0].get("semantic", {})}
            for obj in replacement[1:]:
                if obj.get("parent") not in new_ids:
                    raise ValueError("replacement node escapes target subtree")
            # Validate reachability (cycles are caught by validate below).
            trial = {**result, "objects": replacement}
            if descendants(trial, root) != new_ids:
                raise ValueError("disconnected replacement")
            mapping = operation.get("id_map", {})
            if not isinstance(mapping, dict) or any(k not in old or v not in new_ids for k,v in mapping.items()):
                raise ValueError("invalid ID migration map")
            output = []
            for obj in result["objects"]:
                if obj["id"] == root:
                    output.extend(replacement)
                if obj["id"] in old:
                    continue
                if obj["kind"] == "connector":
                    for end in ("from", "to"):
                        target = obj["geometry"][end]
                        if target in old and target not in new_ids:
                            if target not in mapping:
                                raise ValueError("replacement requires connector target migration")
                            obj["geometry"][end] = mapping[target]
                output.append(obj)
            result["objects"] = output
            lineage.append({"operation": kind, "root": root, "removed": sorted(old-new_ids),
                            "added": sorted(new_ids-old), "id_map": mapping})
        elif kind == "add_subtree":
            if set(operation) != {"op", "objects"} or not isinstance(operation["objects"], list) or not operation["objects"]:
                raise ValueError("invalid add_subtree")
            added = copy.deepcopy(operation["objects"])
            ids = {o["id"] for o in added}
            if ids & set(nodes) or len(ids) != len(added):
                raise ValueError("added ID collision")
            if descendants({"objects": added}, added[0]["id"]) != ids:
                raise ValueError("added nodes must form one subtree")
            result["objects"].extend(added)
            lineage.append({"operation": kind, "added": sorted(ids)})
        elif kind == "remove_subtree":
            if set(operation) != {"op", "id"}:
                raise ValueError("invalid remove_subtree")
            removed = descendants(result, operation["id"])
            result["objects"] = [o for o in result["objects"] if o["id"] not in removed]
            lineage.append({"operation": kind, "removed": sorted(removed)})
        elif kind == "reorder":
            if set(operation) != {"op", "parent", "order"} or not isinstance(operation["order"], list):
                raise ValueError("invalid reorder")
            siblings = [o for o in result["objects"] if o.get("parent") == operation["parent"]]
            order = operation["order"]
            if len(order) != len(siblings) or set(order) != {o["id"] for o in siblings}:
                raise ValueError("reorder requires exactly the existing siblings")
            iterator = iter([nodes[oid] for oid in order])
            result["objects"] = [next(iterator) if o.get("parent") == operation["parent"] else o for o in result["objects"]]
            lineage.append({"operation": kind, "parent": operation["parent"], "order": order})
        else:
            raise ValueError("unsupported structural operation")
    result["revision"] = scene["revision"]+1
    result.setdefault("provenance", {}).setdefault("structural_edits", []).append(
        {"base_revision": scene["revision"], "revision": result["revision"], "changes": lineage})
    validate(result)
    return result


def _exact_patch(scene, patch):
    """Patch 0.4: exact stable-ID records, removals and complete sibling orders.

    Unlike subtree replacement, put_object changes exactly one ID, including
    absent fields, kind and parent. References are validated after the whole
    transaction, so reparenting and reference migration can be atomic.
    """
    if (set(patch) != {"schema_version", "base_revision", "operations"}
            or type(patch["base_revision"]) is not int
            or patch["base_revision"] != scene["revision"]):
        raise ValueError("revision_conflict or invalid exact patch")
    operations = patch["operations"]
    if not isinstance(operations, list) or not operations:
        raise ValueError("exact patch operations must be nonempty")
    result = copy.deepcopy(scene)
    for op in operations:
        if not isinstance(op, dict):
            raise ValueError("invalid exact patch operation")
        kind = op.get("op")
        if kind in {"put_object", "put_resource"}:
            field = "object" if kind == "put_object" else "resource"
            collection = "objects" if field == "object" else "resources"
            if set(op) != {"op", field} or not isinstance(op[field], dict) or not isinstance(op[field].get("id"), str):
                raise ValueError("invalid exact put")
            records = result.setdefault(collection, [])
            index = next((i for i, item in enumerate(records) if item["id"] == op[field]["id"]), len(records))
            if index == len(records):
                records.append(copy.deepcopy(op[field]))
            else:
                records[index] = copy.deepcopy(op[field])
        elif kind in {"remove_object", "remove_resource"}:
            if set(op) != {"op", "id"} or not isinstance(op["id"], str):
                raise ValueError("invalid exact removal")
            collection = "objects" if kind == "remove_object" else "resources"
            if op["id"] not in {item["id"] for item in result.get(collection, [])}:
                raise ValueError("unknown exact removal ID")
            result[collection] = [item for item in result[collection] if item["id"] != op["id"]]
        elif kind == "scene_fields":
            unset = op.get("unset", [])
            if (set(op) - {"op", "set", "unset"} or not {"op", "set"} <= set(op)
                    or not isinstance(op["set"], dict)
                    or set(op["set"]) - {"schema_version", "canvas", "provenance", "occlusions"}
                    or not isinstance(unset, list) or any(key != "occlusions" for key in unset)
                    or len(unset) != len(set(unset)) or set(unset) & set(op["set"])
                    or not op["set"] and not unset):
                raise ValueError("unsupported exact scene fields")
            result.update(copy.deepcopy(op["set"]))
            for key in unset:
                result.pop(key, None)
        elif kind == "order_objects":
            order = op.get("order")
            nodes = {item["id"]: item for item in result["objects"]}
            if (set(op) != {"op", "order"} or not isinstance(order, list)
                    or not all(isinstance(oid, str) for oid in order)
                    or len(order) != len(nodes) or set(order) != set(nodes)):
                raise ValueError("exact order requires every object ID once")
            result["objects"] = [nodes[oid] for oid in order]
        else:
            raise ValueError("unsupported exact structural operation")
    result["revision"] = scene["revision"] + 1
    result.setdefault("provenance", {}).setdefault("structural_edits", []).append(
        {"base_revision": scene["revision"], "revision": result["revision"],
         "changes": [{"operation": op["op"],
                      **({"id": op["id"]} if "id" in op else {}),
                      **({"id": op["object"]["id"]} if "object" in op else {}),
                      **({"id": op["resource"]["id"]} if "resource" in op else {})}
                     for op in operations]})
    return validate(result)

"""Safe host-generation snapshots and B/U/C candidate transactions (version 0.1).

No source is imported, evaluated or run. A host runs its own tools separately and
writes exactly ``scene.json`` and ``generation-input.json`` into a source folder.
Use :func:`generation_input` to make the latter. Sources must be closed vector Scene
snapshots (Scene0.1 or 0.2) without provenance; contextual/semantic project provenance cannot be
silently imported. Existing bound context is preserved and checked on the merge.

``attach_generation(scene, input_record, baseline=None)`` returns a copied Scene
and normal ``{"generation.json": ...}`` extras for ``write_project``. The baseline
(default: scene) contains only generated IDs, with a self-contained reference
closure. Its IDs must equal input.scope.objects/resources. Unlisted project IDs
remain user-owned. scope.canvas is fixed for the lifetime of this generation.

Input fields are schema_version, language (python/javascript), source {text,
sha256}, params (JSON object), backend {name,version}, inputs (inert JSON object),
scope {objects,resources,canvas}, dependencies [{object_id,depends_on:[{type:
object|resource,id}]}]. Dependencies describe baked geometry, not live execution.
They form a finite DAG; a changed retained operand or its ancestors/resources
conflicts with candidate changes to downstream geometry. No trusted recomputation
helper is implemented. Hosts must declare their actual geometry dependencies.

The project extra stores input, complete normalized pure baseline, and its hash.
Scene versions and occlusions are preserved. Occlusions merge as one atomic
ordered array; pure baseline relation IDs are generated, current-only relation
IDs stay user-owned and collide with newly generated IDs even if identical.
Clip/mask resource typing and live references use normal Scene validation.
Surviving baked dependents cannot drop previous dependency declarations.
Scene provenance.generation binds that whole extra, expiring existing reviews.
Diff output contains immutable source copies, proposed-scene.json, a complete
merged/ project with SVG/PNG, conflicts.json, report.json, and candidate.json with
all payload hashes and current revision/canonical-state binding. Accept verifies
both original source files and snapshots, every payload (including previews),
recomputes the merge, then uses Patch0.4 and the existing project transaction.
Conflict resolution means fixing inputs/current state and making a fresh diff.
Hashes detect mutation; they are not signatures against a malicious reauthor.
"""
from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
import shutil
import tempfile

from .context import canonical_hash, validate_context
from .editing import apply_patch
from .scene import ID, REF, finite, load_json, validate

_VERSION = "0.1"
_MISSING = object()
_INPUT_KEYS = {"schema_version", "language", "source", "params", "backend", "inputs", "scope", "dependencies"}
_SOURCE_FILES = {"scene.json", "generation-input.json"}


def _exact(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(f"invalid generation {label} fields")


def _copy(value):
    return value if value is _MISSING else copy.deepcopy(value)


def _ids(values, label):
    if (not isinstance(values, list) or len(values) > 3000
            or any(not isinstance(oid, str) or not ID.fullmatch(oid) for oid in values)
            or len(values) != len(set(values))):
        raise ValueError(f"invalid generation {label} IDs")
    return set(values)


def _semantic_refs(value, objects, resources):
    """Same explicit reference vocabulary as composition; never guess strings."""
    if isinstance(value, list):
        for item in value:
            _semantic_refs(item, objects, resources)
    elif isinstance(value, dict):
        typed = value.get("type", value.get("kind"))
        if "type" in value and "kind" in value and value["type"] != value["kind"]:
            raise ValueError("ambiguous generation semantic reference type")
        table = objects if typed in ("object", "object_ref") else resources if typed in ("resource", "resource_ref") else None
        if isinstance(typed, str) and typed.endswith("_ref") and table is None:
            raise ValueError("unsupported generation semantic reference type")
        if table is not None and not ({"id", "ref"} & set(value)):
            raise ValueError("missing generation semantic typed reference")
        for key, item in value.items():
            allowed = objects if key in {"object_id", "object_ids"} else resources if key in {"resource_id", "resource_ids"} else None
            if allowed is None and table is not None and key in {"id", "ref"}:
                allowed = table
            if allowed is not None:
                refs = item if key.endswith("_ids") else [item]
                if not isinstance(refs, list) or any(not isinstance(ref, str) or ref not in allowed for ref in refs):
                    raise ValueError("dangling generation semantic typed reference")
            else:
                _semantic_refs(item, objects, resources)


def _scene_refs(scene):
    validate(scene)
    if scene["schema_version"] not in ("0.1", "0.2", "0.3"):
        raise ValueError("unsupported: generation Scene version")
    objects = {obj["id"] for obj in scene["objects"]}
    resources = {res["id"] for res in scene.get("resources", [])}
    if len(resources) > 3000:
        raise ValueError("generation resource budget exceeded")
    for obj in scene["objects"]:
        _semantic_refs(obj.get("semantic", {}), objects, resources)


def _project_provenance(scene):
    provenance = scene.get("provenance", {})
    if set(provenance) - {"generation", "structural_edits", "context", "brief"}:
        raise ValueError("unsupported: generation project provenance; use a fresh host Scene")
    if "brief" in provenance and (not isinstance(provenance["brief"], dict)
                                   or set(provenance["brief"]) - {"protected_ids"}
                                   or "context" not in provenance):
        raise ValueError("unsupported: generation semantic brief requires bound context")


def _pure(scene):
    _scene_refs(scene)
    if scene.get("provenance"):
        raise ValueError("unsupported: generation baseline/candidate provenance must be empty")
    # Revision, runtime/receipts and bindings are not pure generated state.
    return {"schema_version": scene["schema_version"], "revision": 0, "canvas": copy.deepcopy(scene["canvas"]),
            "objects": copy.deepcopy(scene["objects"]),
            "resources": sorted(copy.deepcopy(scene.get("resources", [])), key=lambda r: r["id"]),
            **({"occlusions": copy.deepcopy(scene["occlusions"])} if "occlusions" in scene else {})}


def generation_input(scene, *, language, source, params, backend, inputs=None,
                     dependencies=None, canvas=True):
    """Make a strict inert input record for a pure generated Scene.

    ``source`` is literal text, ``backend`` is {name,version}, ``params`` and
    ``inputs`` are finite JSON objects. No external file paths are opened.
    IDs are derived only from this pure Scene, never from the later user Scene.
    The returned value is the file format of generation-input.json.
    """
    pure = _pure(scene)
    if not isinstance(source, str):
        raise ValueError("generation source must be text")
    record = {"schema_version": _VERSION, "language": language,
              "source": {"text": source, "sha256": hashlib.sha256(source.encode("utf-8")).hexdigest()},
              "params": copy.deepcopy(params), "backend": copy.deepcopy(backend),
              "inputs": copy.deepcopy(inputs if inputs is not None else {}),
              "scope": {"objects": sorted(obj["id"] for obj in pure["objects"]),
                        "resources": sorted(res["id"] for res in pure["resources"]), "canvas": canvas},
              "dependencies": copy.deepcopy(dependencies if dependencies is not None else [])}
    _validate_input(record, pure)
    return record


def _validate_input(record, baseline):
    _exact(record, _INPUT_KEYS, "input")
    if record["schema_version"] != _VERSION or record["language"] not in ("python", "javascript"):
        raise ValueError("unsupported generation input version/language")
    source = record["source"]
    _exact(source, {"text", "sha256"}, "source")
    if (not isinstance(source["text"], str) or len(source["text"].encode("utf-8")) > 1_000_000
            or source["sha256"] != hashlib.sha256(source["text"].encode("utf-8")).hexdigest()):
        raise ValueError("generation source text/hash mismatch")
    _exact(record["backend"], {"name", "version"}, "backend")
    if any(not isinstance(v, str) or not 0 < len(v) <= 200 for v in record["backend"].values()):
        raise ValueError("invalid generation backend identity")
    if any(not isinstance(record[key], dict) for key in ("params", "inputs")):
        raise ValueError("generation params/inputs must be JSON objects")
    finite(record)
    canonical_hash(record)  # Reject non-JSON data before persistence.
    if 'planning_context' in record['inputs']:
        from .planning import validate_plan
        from .planning_tools import validate_context as validate_planning_context
        plan = record['inputs'].get('figure_plan')
        if not isinstance(plan, dict) or canonical_hash(plan) != record['inputs'].get('figure_plan_sha256'):
            raise ValueError('planning context requires bound FigurePlan')
        validate_plan(plan)
        validate_planning_context(plan, record['inputs']['planning_context'])
    scope = record["scope"]
    _exact(scope, {"objects", "resources", "canvas"}, "scope")
    objects, resources = _ids(scope["objects"], "object"), _ids(scope["resources"], "resource")
    if (type(scope["canvas"]) is not bool or objects != {o["id"] for o in baseline["objects"]}
            or resources != {r["id"] for r in baseline["resources"]}):
        raise ValueError("generation scope must exactly own the pure baseline IDs")
    deps = record["dependencies"]
    if not isinstance(deps, list) or len(deps) > 3000:
        raise ValueError("invalid generation dependency graph")
    graph = {}
    edges = 0
    for entry in deps:
        _exact(entry, {"object_id", "depends_on"}, "dependency")
        oid, refs = entry["object_id"], entry["depends_on"]
        if not isinstance(oid, str) or oid not in objects or oid in graph or not isinstance(refs, list) or not refs:
            raise ValueError("invalid generation dependency node")
        graph[oid] = []
        seen = set()
        for ref in refs:
            _exact(ref, {"type", "id"}, "dependency reference")
            if ref["type"] not in ("object", "resource") or not isinstance(ref["id"], str):
                raise ValueError("invalid generation dependency reference")
            key = (ref["type"], ref["id"])
            if ref["id"] not in (objects if ref["type"] == "object" else resources) or key in seen:
                raise ValueError("dangling/duplicate generation dependency reference")
            seen.add(key)
            edges += 1
            if edges > 12000:
                raise ValueError("generation dependency edge budget exceeded")
            if ref["type"] == "object":
                graph[oid].append(ref["id"])
    frames = record['inputs'].get('dependency_frames', {})
    nodes = {o['id']: o for o in baseline['objects']}
    if not isinstance(frames, dict) or set(frames) - set(graph):
        raise ValueError('invalid declared dependency frames')
    for target, frame in frames.items():
        if not isinstance(frame, str) or frame not in nodes or nodes[frame]['kind'] != 'group':
            raise ValueError('dependency frame must be a baseline group')
        for oid in [target, *graph[target]]:
            seen, parent = set(), nodes[oid].get('parent')
            while parent and parent not in seen:
                seen.add(parent); parent = nodes[parent].get('parent')
            if frame not in seen: raise ValueError('dependency frame must contain source and target')
    active, done = set(), set()
    def visit(oid, depth=0):
        if oid in active or depth > 100:
            raise ValueError("cyclic/deep generation dependency graph")
        if oid in done:
            return
        active.add(oid)
        for ref in graph.get(oid, []):
            visit(ref, depth+1)
        active.remove(oid)
        done.add(oid)
    for oid in graph:
        visit(oid)


def _document(baseline, record):
    pure = _pure(baseline)
    _validate_input(record, pure)
    return {"schema_version": _VERSION, "input": copy.deepcopy(record),
            "baseline": pure, "baseline_sha256": canonical_hash(pure)}


def _binding(document):
    return {"schema_version": _VERSION, "sha256": canonical_hash(document)}


def validate_generation(scene, extras):
    """Validate the entire portable generation closure without executing sources.

    Pure baseline refs may name objects removed by the user; current Scene refs
    must close independently. This preserves user deletions across later reruns.
    """
    provenance = scene.get("provenance", {})
    binding = provenance.get("generation")
    if "generation.json" not in extras:
        if "generation" in provenance:
            raise ValueError("missing generation.json for Scene generation binding")
        return
    document = extras["generation.json"]
    _exact(document, {"schema_version", "input", "baseline", "baseline_sha256"}, "snapshot")
    if document["schema_version"] != _VERSION:
        raise ValueError("unsupported generation snapshot version")
    pure = _pure(document["baseline"])
    if pure != document["baseline"] or canonical_hash(pure) != document["baseline_sha256"]:
        raise ValueError("generation pure baseline/hash mismatch")
    _validate_input(document["input"], pure)
    if binding != _binding(document):
        raise ValueError("Scene generation binding mismatch")
    _scene_refs(scene)
    _project_provenance(scene)
    validate_context(scene, extras)


def attach_generation(scene, input_record, baseline=None):
    """Attach an initial source/params/scope/baseline through normal project extras.

    For a fresh project use ``scene, extras = attach_generation(scene, record)``
    then ``write_project(scene, directory, extras=extras)``. For a subset, supply
    a closed pure ``baseline`` and an input record made from that baseline. Every
    baseline object/resource must initially match its project counterpart exactly;
    user-created IDs outside that scope are never claimed. No revision is added.
    Existing context extras, if any, must also be supplied to write_project.
    """
    _scene_refs(scene)
    _project_provenance(scene)
    if "generation" in scene.get("provenance", {}):
        raise ValueError("generation already attached; use diff/accept")
    if baseline is None:
        baseline = {k: copy.deepcopy(v) for k, v in scene.items() if k != "provenance"}
    document = _document(baseline, input_record)
    for key in ("objects", "resources", "occlusions"):
        current = {v["id"]: v for v in scene.get(key, [])}
        if any(current.get(v["id"]) != v for v in document["baseline"].get(key, [])):
            raise ValueError("initial generation baseline differs from project")
    if input_record["scope"]["canvas"] and scene["canvas"] != document["baseline"]["canvas"]:
        raise ValueError("initial generation canvas differs from project")
    result = copy.deepcopy(scene)
    result.setdefault("provenance", {})["generation"] = _binding(document)
    return result, {"generation.json": document}


def _value(value):
    return {"present": False} if value is _MISSING else {"present": True, "value": copy.deepcopy(value)}


def _conflict(conflicts, path, reason, b=_MISSING, u=_MISSING, c=_MISSING):
    conflicts.append({"path": path, "reason": reason, "baseline": _value(b),
                      "current": _value(u), "candidate": _value(c)})


def _merge(b, u, c, path, conflicts):
    if u == c:
        return _copy(u)
    if u == b:
        return _copy(c)
    if c == b:
        return _copy(u)
    if isinstance(u, dict) and isinstance(c, dict) and (isinstance(b, dict) or b is _MISSING):
        base = {} if b is _MISSING else b
        result = {}
        for key in sorted(set(base) | set(u) | set(c)):
            merged = _merge(base.get(key, _MISSING), u.get(key, _MISSING), c.get(key, _MISSING), path+[key], conflicts)
            if merged is not _MISSING:
                result[key] = merged
        return result
    _conflict(conflicts, path, "delete_change" if u is _MISSING or c is _MISSING else "divergent_field", b, u, c)
    return _copy(u)


def _records(b, u, c, collection, conflicts):
    tables = [{v["id"]: v for v in scene.get(collection, [])} for scene in (b, u, c)]
    result = {}
    for oid in sorted(set().union(*tables)):
        base, current, candidate = [table.get(oid, _MISSING) for table in tables]
        if base is _MISSING and current is not _MISSING and candidate is not _MISSING:
            _conflict(conflicts, [collection, oid], "id_collision", base, current, candidate)
            merged = _copy(current)
        elif base is not _MISSING and (current is _MISSING or candidate is _MISSING):
            # Presence/absence is atomic against modifications to the record.
            if current == candidate:
                merged = _copy(current)
            elif current == base:
                merged = _copy(candidate)
            elif candidate == base:
                merged = _copy(current)
            else:
                _conflict(conflicts, [collection, oid], "delete_change", base, current, candidate)
                merged = _copy(current)
        else:
            merged = _merge(base, current, candidate, [collection, oid], conflicts)
        if merged is not _MISSING:
            result[oid] = merged
    return result


def _sibling_order(b, u, c, nodes, conflicts):
    """Merge common-ID order atomically and splice additions by branch anchors.

    Contradictory insertion/reparent anchors fail closed as an order conflict.
    Cross-parent array placement has no Scene rendering meaning; output is DFS.
    """
    children = {}
    parents = {None} | {obj.get("parent") for obj in nodes.values()}
    for parent in sorted(parents, key=lambda p: p or ""):
        members = {oid for oid, obj in nodes.items() if obj.get("parent") == parent}
        seqs = [[o["id"] for o in scene["objects"] if o.get("parent") == parent and o["id"] in members] for scene in (b, u, c)]
        common = set(seqs[0]) & set(seqs[1]) & set(seqs[2])
        projected = [[oid for oid in seq if oid in common] for seq in seqs]
        chosen = _merge(*projected, ["order", parent], conflicts)
        edges = {oid: set() for oid in members}
        for a, z in zip(chosen, chosen[1:]):
            edges[a].add(z)
        for seq in seqs[1:]:
            # Preserve runs of added/moved IDs and their nearest common anchors.
            for i, oid in enumerate(seq):
                if oid not in common:
                    if i:
                        edges[seq[i-1]].add(oid)
                    if i+1 < len(seq):
                        edges[oid].add(seq[i+1])
        preferred = list(dict.fromkeys(chosen + seqs[1] + seqs[2] + sorted(members)))
        incoming = {oid: 0 for oid in members}
        for targets in edges.values():
            for target in targets:
                incoming[target] += 1
        order = []
        while len(order) < len(members):
            oid = next((oid for oid in preferred if incoming[oid] == 0 and oid not in order), None)
            if oid is None:
                _conflict(conflicts, ["order", parent], "incompatible_order", *seqs)
                order = list(dict.fromkeys(seqs[1] + seqs[2] + sorted(members)))
                break
            order.append(oid)
            for target in edges[oid]:
                incoming[target] -= 1
        children[parent] = order
    result, visited = [], set()
    def emit(parent, depth=0):
        if depth > 100:
            return  # Final Scene validation reports excessive merged depth.
        for oid in children.get(parent, []):
            if oid not in visited:
                visited.add(oid)
                result.append(nodes[oid])
                emit(oid, depth+1)
    emit(None)
    # Keep invalid hierarchies visible to final validation rather than dropping IDs.
    result.extend(nodes[oid] for oid in sorted(set(nodes)-visited))
    return result


def _geometry_state(scene, kind, oid, shared_frame=()):
    """Conservative operand fingerprint: descendants, ancestors and paint resources.

    Group bounds and inherited transforms/styles can affect baked geometry; they
    must not be hidden behind field-disjoint merges. Semantic labels are inert.
    """
    nodes = {o["id"]: o for o in scene["objects"]}
    resources = {r["id"]: r for r in scene.get("resources", [])}
    if kind == "resource":
        return resources.get(oid)
    seen = set()
    def own_state(obj):
        state = {k: copy.deepcopy(v) for k, v in obj.items() if k != "semantic"}
        if obj['id'] in shared_frame:
            state.pop('transform', None)
        paints = {}
        for value in obj.get("style", {}).values():
            ref = REF.fullmatch(value) if isinstance(value, str) else None
            if ref:
                paints[ref[1]] = resources.get(ref[1])
        state["paints"] = paints
        state["effects"] = {key: resources.get(obj[key]) for key in ("clip", "mask") if key in obj}
        return state
    def node(target, depth=0):
        if target in seen:
            return {"ref": target}
        if depth > 100:
            return {"invalid_depth": target}
        seen.add(target)
        obj = nodes.get(target)
        if obj is None:
            return None
        state = own_state(obj)
        # Ancestors supply transforms/style, not siblings. Including a sibling's
        # path here would spuriously make an operand depend on its baked output.
        parent, ancestors, ancestor_ids = obj.get("parent"), [], set()
        while parent:
            if parent in ancestor_ids or parent not in nodes:
                ancestors.append({"invalid_parent": parent})
                break
            ancestor_ids.add(parent)
            ancestor = nodes[parent]
            ancestors.append(own_state(ancestor))
            parent = ancestor.get("parent")
        state["ancestors"] = ancestors
        if obj["kind"] == "group":
            state["children"] = [node(o["id"], depth+1) for o in scene["objects"] if o.get("parent") == target]
        if obj["kind"] == "connector":
            state["endpoints"] = [node(obj["geometry"][key], depth+1) for key in ("from", "to")]
        return state
    return {"canvas": scene["canvas"], "operand": node(oid), "occlusions": scene.get("occlusions", [])}


def _dependency_conflicts(b, u, c, merged, old_input, new_input, conflicts):
    # Keep old edges too: removing a declaration cannot bypass an old dependency.
    graph = {}
    for record in (old_input, new_input):
        for entry in record["dependencies"]:
            graph.setdefault(entry["object_id"], set()).update((ref["type"], ref["id"]) for ref in entry["depends_on"])
    bnodes, cnodes = ({o["id"]: o for o in scene["objects"]} for scene in (b, c))
    new_graph = {entry["object_id"]: {(ref["type"], ref["id"]) for ref in entry["depends_on"]}
                 for entry in new_input["dependencies"]}
    for entry in old_input["dependencies"]:
        dependent = entry["object_id"]
        previous_refs = {(ref["type"], ref["id"]) for ref in entry["depends_on"]}
        if dependent in cnodes and previous_refs - new_graph.get(dependent, set()):
            _conflict(conflicts, ["dependencies", dependent], "dependency_declaration_removed")
    fingerprints = {}
    indices = [{o['id']: o for o in s['objects']} for s in (b, u, c, merged)]
    def ancestors(nodes, oid):
        result = set()
        parent = nodes.get(oid, {}).get('parent')
        while parent and parent not in result:
            result.add(parent)
            parent = nodes.get(parent, {}).get('parent')
        return result
    def fingerprint(index, scene, kind, ref, dependent):
        # A shared ancestor's affine moves both the source and baked output.
        # Ignore only that affine, and only for ancestors shared in ALL states;
        # retain inherited styles, own transforms, reparenting and outside frames.
        declared = old_input['inputs'].get('dependency_frames', {}).get(dependent)
        if declared != new_input['inputs'].get('dependency_frames', {}).get(dependent):
            declared = None
        shared = set.intersection(*(ancestors(nodes, ref) & ancestors(nodes, dependent)
                                    for nodes in indices)) if kind == 'object' else set()
        frame = ({declared} | set.intersection(*(ancestors(nodes, declared) for nodes in indices))) if declared in shared else set()
        key = (index, kind, ref, tuple(sorted(frame)))
        if key not in fingerprints:
            fingerprints[key] = canonical_hash(_geometry_state(scene, kind, ref, frame))
        return fingerprints[key]
    comparisons = 0
    for dependent in sorted(graph):
        if dependent not in cnodes:
            continue
        before, after = bnodes.get(dependent, {}), cnodes[dependent]
        fields = ("kind", "geometry", "transform", "parent", "style", "visible")
        if all(before.get(key, _MISSING) == after.get(key, _MISSING) for key in fields):
            continue
        refs, visited, pending = set(), set(), [dependent]
        while pending:
            oid = pending.pop()
            if oid in visited:
                continue
            visited.add(oid)
            for kind, ref in graph.get(oid, set()):
                refs.add((kind, ref))
                if kind == "object":
                    pending.append(ref)
        for kind, ref in sorted(refs):
            comparisons += 1
            if comparisons > 12000:
                raise ValueError("generation transitive dependency work budget exceeded")
            base_state, user_state, candidate_state, merged_state = [fingerprint(i, s, kind, ref, dependent) for i, s in enumerate((b, u, c, merged))]
            if user_state != base_state and merged_state != candidate_state:
                _conflict(conflicts, ["dependencies", dependent, kind, ref], "stale_baked_geometry",
                          base_state, user_state, candidate_state)


def _patch_between(current, merged):
    operations = []
    for collection, singular in (("objects", "object"), ("resources", "resource")):
        before = {o["id"]: o for o in current.get(collection, [])}
        after = {o["id"]: o for o in merged.get(collection, [])}
        for oid in sorted(set(before)-set(after)):
            operations.append({"op": "remove_"+singular, "id": oid})
        for oid in sorted(after):
            if before.get(oid) != after[oid]:
                operations.append({"op": "put_"+singular, singular: after[oid]})
    operations.append({"op": "order_objects", "order": [o["id"] for o in merged["objects"]]})
    fields = {key: merged[key] for key in ("schema_version", "canvas", "provenance", "occlusions")
              if key in merged and current.get(key, _MISSING) != merged[key]}
    unset = [key for key in ("occlusions",) if key in current and key not in merged]
    if fields or unset:
        operations.append({"op": "scene_fields", "set": fields, **({"unset": unset} if unset else {})})
    return {"schema_version": "0.4", "base_revision": current["revision"], "operations": operations}


def _compute(current, extras, candidate, record):
    validate_generation(current, extras)
    if "generation.json" not in extras:
        raise ValueError("project has no generation baseline; attach_generation first")
    previous = extras["generation.json"]
    b, c = previous["baseline"], _pure(candidate)
    new_document = _document(c, record)
    if previous["input"]["scope"]["canvas"] != record["scope"]["canvas"]:
        raise ValueError("generation canvas ownership cannot change")
    conflicts = []
    nodes = _records(b, current, c, "objects", conflicts)
    resources = _records(b, current, c, "resources", conflicts)
    b_ids = {v["id"] for key in ("objects", "resources", "occlusions") for v in b.get(key, [])}
    u_ids = {v["id"] for key in ("objects", "resources", "occlusions") for v in current.get(key, [])}
    c_ids = {v["id"] for key in ("objects", "resources", "occlusions") for v in c.get(key, [])}
    for oid in sorted(c_ids & (u_ids-b_ids)):
        if not any(f["reason"] == "id_collision" and f["path"][-1] == oid for f in conflicts):
            _conflict(conflicts, ["ids", oid], "id_collision")
    merged = copy.deepcopy(current)
    merged["schema_version"] = _merge(b["schema_version"], current["schema_version"], c["schema_version"], ["schema_version"], conflicts)
    relations = _merge(b.get("occlusions", _MISSING), current.get("occlusions", _MISSING),
                       c.get("occlusions", _MISSING), ["occlusions"], conflicts)
    if relations is _MISSING:
        merged.pop("occlusions", None)
    else:
        merged["occlusions"] = relations
    merged["objects"] = _sibling_order(b, current, c, nodes, conflicts)
    merged["resources"] = [resources[rid] for rid in sorted(resources)]
    if record["scope"]["canvas"]:
        merged["canvas"] = _merge(b["canvas"], current["canvas"], c["canvas"], ["canvas"], conflicts)
    _dependency_conflicts(b, current, c, merged, previous["input"], record, conflicts)
    merged.setdefault("provenance", {})["generation"] = _binding(new_document)
    new_extras = copy.deepcopy(extras)
    new_extras["generation.json"] = new_document
    if "figure_plan_sha256" in record["inputs"]:
        from .planning import validate_planning
        new_extras["figure-plan.json"] = copy.deepcopy(record["inputs"].get("figure_plan"))
        validate_planning(merged, new_extras)
    elif "figure-plan.json" in new_extras:
        raise ValueError("FigurePlan generation requires an explicitly bound replacement plan")
    proposed = copy.deepcopy(merged)
    fallback = False
    try:
        validate_generation(merged, new_extras)
    except (ValueError, TypeError, KeyError) as error:
        _conflict(conflicts, ["closure"], "invalid_merged_closure")
        conflicts[-1]["detail"] = str(error)
        fallback = True
        merged = copy.deepcopy(current)
        merged.setdefault("provenance", {})["generation"] = _binding(new_document)
    patch = _patch_between(current, merged)
    result = apply_patch(current, patch)
    validate_generation(result, new_extras)
    # Report stable IDs, not a misleading count of matching array positions.
    changes = {}
    for collection in ("objects", "resources"):
        before = {v["id"]: v for v in current.get(collection, [])}
        after = {v["id"]: v for v in result.get(collection, [])}
        changes[collection] = {"updated": sorted(oid for oid in set(before) & set(after) if before[oid] != after[oid]),
                               "added": sorted(set(after)-set(before)), "removed": sorted(set(before)-set(after)),
                               "preserved": sorted(oid for oid in set(before) & set(after) if before[oid] == after[oid])}
    report = {"schema_version": _VERSION, "status": "conflicts" if conflicts else "ready",
              "conflict_count": len(conflicts), "preview_fallback_to_current": fallback,
              "changes": changes, "baseline_sha256": new_document["baseline_sha256"],
              "limitations": ["Sources are inert; host must declare actual baked-geometry dependencies.",
                              "Only closed Scene0.1/0.2/0.3 candidates without provenance are supported; occlusions are atomic.",
                              "Conflicts require a fresh diff; no automatic geometry recomputation."]}
    return result, new_extras, conflicts, report, proposed


def _read_source(directory):
    from .bundles import _tree
    root = Path(directory).absolute()
    artifacts, directories = _tree(root)
    if directories or set(artifacts) != _SOURCE_FILES:
        raise ValueError("generation source requires exactly scene.json and generation-input.json")
    scene, record = load_json(root/"scene.json"), load_json(root/"generation-input.json")
    _document(scene, record)
    return scene, record, artifacts


def diff_generation(project, candidate, output):
    """Write a separate immutable diff directory; never modify the current project."""
    from .bundles import _tree
    from .project import (digest, json_text, load_project, project_extras, project_lock,
                          project_state_sha256, write_project)
    project, source, output = (Path(p).resolve() for p in (project, candidate, output))
    if output.is_relative_to(project) or output.is_relative_to(source) or project.is_relative_to(output) or source.is_relative_to(output):
        raise ValueError("generation diff output must be separate from project and source")
    current = load_project(project)
    extras = project_extras(project)
    scene, record, source_artifacts = _read_source(source)
    binding = {"revision": current["revision"], "scene_sha256": canonical_hash(current),
               "state_sha256": project_state_sha256(current, extras),
               "receipt_sha256": digest(project/"receipt.json")}
    merged, new_extras, conflicts, report, proposed = _compute(current, extras, scene, record)
    scale = load_json(project/"receipt.json")["render_scale"]
    output.parent.mkdir(parents=True, exist_ok=True)
    with project_lock(output):
        if os.path.lexists(output):
            raise ValueError("generation diff output already exists")
        stage = Path(tempfile.mkdtemp(prefix="."+output.name+".stage-", dir=output.parent))
        try:
            (stage/"source").mkdir()
            for name in sorted(_SOURCE_FILES):
                shutil.copyfile(source/name, stage/"source"/name)
            if _read_source(source)[2] != source_artifacts or _read_source(stage/"source")[2] != source_artifacts:
                raise ValueError("generation source changed during diff")
            reference = None
            if (project/"reference.png").is_file():
                from PIL import Image
                with Image.open(project/"reference.png") as image:
                    reference = image.copy()
            write_project(merged, stage/"merged", scale=scale, reference=reference, extras=new_extras)
            for name, value in (("conflicts.json", conflicts), ("report.json", report), ("proposed-scene.json", proposed)):
                (stage/name).write_text(json_text(value), encoding="utf-8")
            artifacts, _ = _tree(stage)
            document = {"schema_version": _VERSION, "kind": "scientific-figure-generation-diff",
                        "binding": binding, "source": {"directory": str(source), "artifacts": source_artifacts},
                        "artifacts": artifacts}
            (stage/"candidate.json").write_text(json_text(document), encoding="utf-8")
            # Recheck even same-revision edits before publishing the diff snapshot.
            if (project_state_sha256(load_project(project), project_extras(project)) != binding["state_sha256"]
                    or digest(project/"receipt.json") != binding["receipt_sha256"]):
                raise ValueError("generation stale current project during diff")
            stage.rename(output)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    return {"status": report["status"], "candidate": str(output), "base_revision": current["revision"],
            "conflict_count": len(conflicts), "preview": str(output/"merged"/"preview.png")}


def accept_generation(project, candidate, *, base_revision):
    """Recheck an existing diff and atomically accept it with a pure C baseline.

    Original source files must still exist byte-for-byte. The complete copied C
    is also independently checked and used for recomputation. Nothing is written
    to the project if conflicts, stale state, bad refs, render or I/O errors occur.
    """
    from .bundles import _inventory_matches, _tree, _verified_project
    from .project import (digest, load_project, project_extras, project_state_sha256, write_project)
    from .render import render_svg, runtime_info
    from .svg import to_svg
    project, root = Path(project).resolve(), Path(candidate).resolve()
    actual, directories = _tree(root)
    document = load_json(root/"candidate.json")
    _exact(document, {"schema_version", "kind", "binding", "source", "artifacts"}, "diff")
    if document["schema_version"] != _VERSION or document["kind"] != "scientific-figure-generation-diff":
        raise ValueError("unsupported generation diff")
    _inventory_matches({name: info for name, info in actual.items() if name != "candidate.json"}, document["artifacts"])
    expected = {"candidate.json", "conflicts.json", "report.json", "proposed-scene.json"} | {"source/"+name for name in _SOURCE_FILES}
    if directories != {"source", "merged"} or set(actual)-expected != {"merged/"+name for name in _tree(root/"merged")[0]}:
        raise ValueError("unsupported generation diff artifacts")
    _exact(document["source"], {"directory", "artifacts"}, "diff source")
    scene, record, original = _read_source(document["source"]["directory"])
    copied_scene, copied_record, copied = _read_source(root/"source")
    if original != document["source"]["artifacts"] or copied != original or scene != copied_scene or record != copied_record:
        raise ValueError("generation source candidate changed since diff")
    current, extras = load_project(project), project_extras(project)
    binding = {"revision": current["revision"], "scene_sha256": canonical_hash(current),
               "state_sha256": project_state_sha256(current, extras),
               "receipt_sha256": digest(project/"receipt.json")}
    if type(base_revision) is not int or base_revision != current["revision"] or binding != document["binding"]:
        raise ValueError("generation stale current project revision/content binding")
    merged, new_extras, conflicts, report, proposed = _compute(current, extras, copied_scene, copied_record)
    if (load_json(root/"conflicts.json") != conflicts or load_json(root/"report.json") != report
            or load_json(root/"proposed-scene.json") != proposed):
        raise ValueError("generation diff does not match recomputed merge")
    saved, receipt = _verified_project(root/"merged")
    if saved != merged:
        raise ValueError("generation merged project differs from recomputation")
    saved_extras = project_extras(root/"merged")
    if {k: v for k, v in saved_extras.items() if k != "review.json"} != {k: v for k, v in new_extras.items() if k != "review.json"}:
        raise ValueError("generation merged extras differ from recomputation")
    scale = load_json(project/"receipt.json")["render_scale"]
    if receipt["render_scale"] != scale or receipt["runtime"] != runtime_info():
        raise ValueError("generation renderer changed; create a fresh diff")
    if (root/"merged"/"preview.png").read_bytes() != render_svg(to_svg(merged), scale):
        raise ValueError("generation candidate preview differs from recomputed rendering")
    if conflicts:
        raise ValueError("generation unresolved conflicts; create a fresh diff")
    if _read_source(document["source"]["directory"])[2] != original or _tree(root)[0] != actual:
        raise ValueError("generation source/diff changed during acceptance")
    # write_project checks canonical state and receipt again under its normal lock.
    return write_project(merged, project, scale=scale, extras=new_extras,
                         expected_revision=base_revision, expected_state_sha256=binding["state_sha256"],
                         expected_receipt_sha256=binding["receipt_sha256"])

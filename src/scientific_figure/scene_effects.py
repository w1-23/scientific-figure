"""Scene 0.2 bounded vector effects. Source geometry is the only edit state.

Occlusions read opaque source operands, never another occlusion's output.
Cycles in over/under therefore do not create recursive evaluation. Derived cuts
are disposable. Unsupported transparency/paint combinations fail explicitly.
"""
from __future__ import annotations

from copy import deepcopy
from fontTools.pens.recordingPen import RecordingPen
from fontTools.svgLib.path import parse_path

from .geometry import Geometry, IDENTITY, inverse, multiply


def _path_check(d, *, closed=True):
    from .pathops_backend import _recording
    if not isinstance(d, str) or not d or len(d) > 20000:
        raise ValueError("effect path requires 1..20000 characters")
    pen = _recording(d, closed=closed)
    if len(pen.value) > 2048:
        raise ValueError("effect path exceeds 2048 commands")


def validate_resource(resource):
    from .scene import number
    kind = resource.get("kind")
    if kind == "clip_path":
        if set(resource)-{"id", "kind", "d", "fill_rule"}:
            raise ValueError("unsupported clip resource fields")
        _path_check(resource.get("d"))
        if resource.get("fill_rule", "nonzero") not in {"nonzero", "evenodd"}:
            raise ValueError("invalid clip fill rule")
    elif kind == "alpha_mask":
        if set(resource) != {"id", "kind", "region", "shapes"}:
            raise ValueError("alpha mask requires region and shapes only")
        _region(resource["region"])
        if not isinstance(resource["shapes"], list) or not 1 <= len(resource["shapes"]) <= 16:
            raise ValueError("mask requires 1..16 vector shapes")
        for shape in resource["shapes"]:
            if not isinstance(shape, dict) or set(shape) != {"d", "alpha"}:
                raise ValueError("mask shapes require d and alpha")
            _path_check(shape["d"])
            number(shape["alpha"], "mask alpha")
            if not 0 <= shape["alpha"] <= 1:
                raise ValueError("mask alpha outside 0..1")
    else:
        raise ValueError("unsupported Scene0.2 resource")


def _region(region):
    from .scene import number
    if not isinstance(region, list) or len(region) != 4:
        raise ValueError("region requires [x,y,width,height]")
    for value in region:
        number(value, "region coordinate")
    if min(region[2:]) <= 0:
        raise ValueError("region extent must be positive")


def region_path(region):
    x, y, w, h = region
    return f"M{x} {y}H{x+w}V{y+h}H{x}Z"


def _source(scene, oid, geometry):
    """An opaque closed fill or constant local round stroke, in world space."""
    from .pathops_backend import stroke_outline, transformed
    obj, style = geometry.nodes[oid], geometry.style(oid)
    if obj["kind"] != "path":
        raise ValueError("occlusion operands currently require path objects")
    current = oid
    while current:
        node = geometry.nodes[current]
        if node.get("clip") or node.get("mask") or not node.get("visible", True) or node.get("style", {}).get("opacity", 1) != 1:
            raise ValueError("occlusion operands require visible unclipped opaque ancestry")
        current = node.get("parent")
    fill, stroke = style.get("fill", "#000000"), style.get("stroke", "none")
    if fill != "none" and stroke != "none":
        raise ValueError("occlusion operands cannot combine fill and stroke")
    if style.get("arrow_end"):
        raise ValueError("occlusion arrow markers unsupported")
    if fill == "none" and style.get("stroke_dasharray"):
        raise ValueError("dashed occlusion operands unsupported")
    paint = fill if fill != "none" else stroke
    if paint == "none" or paint.startswith("url(") or (len(paint) == 9 and paint[-2:].lower() != "ff"):
        raise ValueError("occlusion requires a solid opaque paint")
    if style.get("fill_opacity", 1) != 1 or style.get("stroke_opacity", 1) != 1:
        raise ValueError("translucent occlusion operands unsupported")
    d = obj["geometry"]["d"]
    _path_check(d, closed=fill != "none")
    if fill == "none":
        d = stroke_outline(d, style.get("stroke_width", 1))
    elif style.get("fill_rule", "nonzero") == "evenodd":
        from .pathops_backend import boolean
        d = boolean("union", d, "", fill_rule="evenodd")
    return transformed(d, geometry.world(oid)), paint


def validate_effects(scene):
    """Validate typed refs and bounded evaluation; old Scene0.1 never calls this."""
    from .scene import ID, number
    resources = {r["id"]: r for r in scene.get("resources", [])}
    if len(resources) > 256:
        raise ValueError("effect resource budget exceeded")
    geometry = Geometry(scene)
    for obj in scene["objects"]:
        for field, kind in (("clip", "clip_path"), ("mask", "alpha_mask")):
            if field in obj and (obj[field] not in resources or resources[obj[field]]["kind"] != kind):
                raise ValueError(f"invalid typed {field} resource")
    relations = scene.get("occlusions", [])
    if not isinstance(relations, list) or len(relations) > 32:
        raise ValueError("occlusions require at most32 records")
    ids = set(geometry.nodes) | set(resources)
    total_chars = sum(len(r.get("d", "")) + sum(len(s["d"]) for s in r.get("shapes", [])) for r in resources.values())
    for relation in relations:
        if not isinstance(relation, dict) or set(relation) != {"id", "over", "under", "frame", "region", "gap"}:
            raise ValueError("occlusion requires id, over, under, frame, region, gap")
        rid = relation["id"]
        if not isinstance(rid, str) or not ID.fullmatch(rid) or rid.startswith("sf-") or rid in ids:
            raise ValueError("duplicate or invalid occlusion ID")
        ids.add(rid)
        over, under, frame = relation["over"], relation["under"], relation["frame"]
        if over not in geometry.nodes or under not in geometry.nodes or over == under:
            raise ValueError("invalid occlusion object refs")
        if frame is not None and (frame not in geometry.nodes or geometry.nodes[frame]["kind"] != "group"):
            raise ValueError("occlusion frame must reference group or null world")
        _region(relation["region"])
        number(relation["gap"], "occlusion gap")
        if not 0 <= relation["gap"] <= 1000:
            raise ValueError("occlusion gap outside0..1000")
        for oid in (over, under):
            total_chars += len(geometry.nodes[oid].get("geometry", {}).get("d", ""))
            _source(scene, oid, geometry)
    if total_chars > 300000 or len({r[key] for r in relations for key in ("over", "under")}) > 16:
        raise ValueError("effect geometry workload budget exceeded")
    # Contradictory/overlapping regions for the same pair are ambiguous even if
    # they happen not to intersect the current operands. Reject, do not infer.
    from .pathops_backend import boolean, transformed
    for index, left in enumerate(relations):
        for right in relations[index+1:]:
            if {left["over"], left["under"]} & {right["over"], right["under"]}:
                regions = [transformed(region_path(r["region"]), geometry.world(r["frame"]) if r["frame"] else IDENTITY) for r in (left, right)]
                if boolean("intersection", *regions):
                    raise ValueError("overlapping occlusion regions sharing operands are unsupported")


def evaluate(scene):
    """Return local derived paths and explicit relationship diagnostics.

    A region must contain one intersection component. Pairwise intersections
    outside all declared regions report needs_review. This is a bounded geometric
    diagnostic, not scientific topology inference or an aesthetic approval.
    """
    from .pathops_backend import boolean, transformed, stroke_outline
    geometry = Geometry(scene)
    sources, paints, cuts, diagnostics, regions_by_pair = {}, {}, {}, [], {}
    relations = scene.get("occlusions", [])
    for relation in relations:
        for oid in (relation["over"], relation["under"]):
            if oid not in sources:
                sources[oid], paints[oid] = _source(scene, oid, geometry)
    for relation in relations:
        over, under = relation["over"], relation["under"]
        world = geometry.world(relation["frame"]) if relation["frame"] else IDENTITY
        to_frame = inverse(world)
        a, b = [transformed(sources[oid], to_frame) for oid in (over, under)]
        region = region_path(relation["region"])
        intersection = boolean("intersection", a, b)
        local_intersection = boolean("intersection", intersection, region)
        pen = RecordingPen()
        if local_intersection:
            parse_path(local_intersection, pen)
        components = sum(op == "moveTo" for op, _ in pen.value)
        status = "current" if components == 1 else "needs_review"
        diagnostics.append({"id": relation["id"], "status": status, "intersection_contours": components,
                            "reason": "one declared local intersection" if status == "current" else "intersection absent or multiple contours; host must review region"})
        expanded = a
        if relation["gap"]:
            expanded = boolean("union", a, stroke_outline(a, 2*relation["gap"]))
        cut = transformed(boolean("intersection", expanded, region), world)
        cuts[under] = boolean("union", cuts.get(under, ""), cut)
        pair = tuple(sorted((over, under)))
        regions_by_pair[pair] = boolean("union", regions_by_pair.get(pair, ""), transformed(region, world))
    for pair, region in regions_by_pair.items():
        intersection = boolean("intersection", sources[pair[0]], sources[pair[1]])
        if boolean("difference", intersection, region):
            diagnostics.append({"objects": list(pair), "status": "needs_review", "reason": "source intersection outside declared regions"})
    # Different declared pairs can create an undeclared new intersection after a
    # move too. Report it rather than silently assigning the current global order.
    keys = sorted(sources)
    for index, left in enumerate(keys):
        for right in keys[index+1:]:
            if (left, right) not in regions_by_pair and boolean("intersection", sources[left], sources[right]):
                diagnostics.append({"objects": [left, right], "status": "needs_review", "reason": "undeclared operand-pair intersection"})
    derived = {}
    for oid, cut in cuts.items():
        d = boolean("difference", sources[oid], cut)
        derived[oid] = {"d": transformed(d, inverse(geometry.world(oid))), "paint": paints[oid]}
    return {"paths": derived, "relations": diagnostics,
            "status": "needs_review" if any(d["status"] == "needs_review" for d in diagnostics) else "current"}


def bake_occlusions(scene):
    """Freeze current cuts before selected export, keeping IDs/style/ancestry.

    Clip/mask resources remain actual resources; only external occlusion operands
    are removed as live dependencies. Caller still closes clip/mask references.
    """
    result = deepcopy(scene)
    evaluated = evaluate(scene)
    for obj in result["objects"]:
        if obj["id"] in evaluated["paths"]:
            derived = evaluated["paths"][obj["id"]]
            if not derived["d"]:
                obj["visible"] = False
                continue
            obj["kind"] = "path"
            obj["geometry"] = {"d": derived["d"]}
            obj.setdefault("style", {}).update(fill=derived["paint"], stroke="none", fill_rule="nonzero")
    result.pop("occlusions", None)
    result.setdefault("provenance", {})["occlusion_export"] = {"baked": True, "evaluation_status": evaluated["status"], "relations": evaluated["relations"]}
    return result

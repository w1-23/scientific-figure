"""Execute a host-authored plan; semantic decisions are never inferred silently."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
from PIL import Image
from .scene import schema_validate, validate
from .geometry import Geometry, inverse, multiply


def reconstruct(image_path: Path | None, plan: dict):
    version = plan.get("schema_version")
    semantic = version == "0.3" and plan.get("mode") == "semantic"
    adaptive = version == "0.2" or (version == "0.3" and plan.get("mode") == "pixel")
    schema_validate(plan, {"0.2": "reconstruction-plan-v02", "0.3": "reconstruction-plan-v03"}.get(
        version, "reconstruction-plan"))
    if semantic and "source" not in plan:
        scene = {"schema_version": "0.1", "revision": 0, "canvas": copy.deepcopy(plan["canvas"]),
                 "objects": [], "resources": copy.deepcopy(plan.get("resources", [])),
                 "provenance": {"mode": "semantic", "notes": plan.get("notes", [])}}
        return _construct_semantic(scene, plan)
    if adaptive:
        from .candidate_search import SearchBudget
        from .evaluation import profile_values
        budget = SearchBudget(plan.get("search_budget"))
        profile = profile_values(plan.get("adaptive_profile"))
    source=plan["source"]
    if image_path is None:
        raise ValueError("source requires a reference image")
    if hashlib.sha256(image_path.read_bytes()).hexdigest()!=source["sha256"]:
        raise ValueError("source hash does not match the reconstruction plan")
    with Image.open(image_path) as image:
        if list(image.size)!=source["size"]:raise ValueError("source dimensions do not match plan")
    x,y,w,h=source["region"]
    if min(w,h)<=0 or x+w>source["size"][0] or y+h>source["size"][1]:raise ValueError("source region is outside image")
    # First version deliberately maps one source pixel to one scene unit.
    if not semantic and (plan["canvas"].get("width")!=w or plan["canvas"].get("height")!=h):
        raise ValueError("prototype requires canvas size to equal source region size (1 pixel = 1 unit)")
    scene={"schema_version":"0.1","revision":0,"canvas":copy.deepcopy(plan["canvas"]),
           "objects":[],"resources":copy.deepcopy(plan.get("resources",[])),
           "provenance":{"mode":"reference-reconstruction","source":copy.deepcopy(source),
                         "source_to_scene":[1,0,0,1,-x,-y],"notes":plan.get("notes",[])}}
    if semantic:
        scene["provenance"]["mode"] = "semantic"
        # Reference coordinates map to target user units; host geometry is already in those units.
        vx, vy, vw, vh = plan["canvas"].get("viewBox", [0, 0, plan["canvas"]["width"], plan["canvas"]["height"]])
        sx, sy = vw / w, vh / h
        scene["provenance"]["source_to_scene"] = [sx, 0, 0, sy, vx - x*sx, vy - y*sy]
        return _construct_semantic(scene, plan)
    from .quality import policy_values
    drawing_policy=policy_values(plan.get("drawing_policy"))
    scene["provenance"]["drawing_policy"]=drawing_policy
    completed=set(); summaries=[]
    for step in plan["steps"]:
        sid=step["step_id"]
        if sid in completed or not set(step.get("depends_on",[]))<=completed:
            raise ValueError(f"duplicate or unsatisfied step dependency: {sid}")
        obj=copy.deepcopy(step["object"])
        if step["operation"]=="construct":
            if "parameters" in step or "region" in step:raise ValueError("construct takes geometry in object, not parameters/region")
            obj.setdefault("semantic",{}).setdefault("evidence","host_assisted")
            scene["objects"].append(obj)
            summaries.append({"step_id":sid,"strategy":"host-geometry","object_id":obj["id"]})
        else:
            region=step.get("region")
            if not region:raise ValueError("needs_input: pixel operation requires region")
            rx,ry,rw,rh=region
            if min(rw,rh)<=0 or rx+rw>w or ry+rh>h:raise ValueError("pixel region outside source region")
            params=step.get("parameters",{})
            placement=[1,0,0,1,rx,ry]
            if obj.get("parent"):
                geometry=Geometry(scene)
                if obj["parent"] not in geometry.nodes:raise ValueError("pixel operation parent must be constructed first")
                placement=multiply(inverse(geometry.world(obj["parent"])),placement)
            if step["operation"]=="auto_reconstruct":
                from .analysis import analyze
                from .candidate_search import search
                if set(obj)-{"id","kind","parent","semantic"} or obj.get("kind")!="group":
                    raise ValueError("auto_reconstruct requires a group declaration")
                if set(params)-{"colors","background","parts","methods"}:
                    raise ValueError("unsupported adaptive parameters")
                evidence=analyze(image_path,[x+rx,y+ry,rw,rh],colors=params.get("colors",6),
                                 background=params.get("background"),parts=params.get("parts"))
                candidate,report=search(image_path,evidence,object_id=obj["id"],methods=params.get("methods"),
                                        profile=profile,budget=budget)
                root=candidate["objects"][0]
                root.update({k:v for k,v in obj.items() if k in {"parent","semantic"}})
                root["transform"]=placement
                scene["objects"].extend(candidate["objects"])
                summary={"strategy":"adaptive_candidates","analysis":evidence,"candidates":report,
                         "status":report["selection"]["status"]}
            elif step["operation"]=="fit_stroke":
                from .strokes import fit_stroke
                if set(obj)-{"id","kind","parent","semantic"} or obj.get("kind")!="path":
                    raise ValueError("fit_stroke needs a path declaration without fixed geometry/style")
                fitted,summary=fit_stroke(image_path,[x+rx,y+ry,rw,rh],object_id=obj["id"],parameters=params)
                fitted.update({k:v for k,v in obj.items() if k in {"parent","semantic"}})
                fitted["transform"]=placement
                scene["objects"].append(fitted)
            else:
                from .quality import reviewed_trace
                if obj.get("kind")!="group" or "transform" in obj or "geometry" in obj:
                    raise ValueError("trace_region output must be a group without geometry/transform")
                if set(params)-{"palette","hsv","colors","min_area","simplify","max_paths"}:
                    raise ValueError("unsupported trace parameters")
                paths,summary=reviewed_trace(image_path,[x+rx,y+ry,rw,rh],object_id=obj["id"],parameters=params,
                                             policy={**drawing_policy,**step.get("policy",{})})
                obj["transform"]=placement
                obj.setdefault("semantic",{}).setdefault("evidence","observed-contour")
                scene["objects"].append(obj);scene["objects"].extend(paths)
            summaries.append({"step_id":sid,**summary})
        completed.add(sid)
    scene["provenance"]["steps"]=summaries
    validate(scene)
    if adaptive:
        from .analysis import analyze
        from .evaluation import evaluate
        context=analyze(image_path,source["region"],colors=8,
                        background=plan["canvas"].get("background") if plan["canvas"].get("background")!="none" else None)
        scene["provenance"]["adaptive_context"]={"analysis":context,"profile":profile}
        # Bind review before attaching its own result; geometry is separately fingerprinted.
        audit=evaluate(image_path,context,scene,profile=profile,budget=budget)
        scene["provenance"]["adaptive_context"]["audit"]=audit
        scene["provenance"]["search_budget"]=budget.report()
    return scene


def _construct_semantic(scene, plan):
    brief = copy.deepcopy(plan["brief"])
    if brief["target_size"] != [scene["canvas"]["width"], scene["canvas"]["height"]]:
        raise ValueError("brief target_size must equal canvas dimensions")
    completed, summaries = set(), []
    for step in plan["steps"]:
        sid = step["step_id"]
        if sid in completed or not set(step.get("depends_on", [])) <= completed:
            raise ValueError(f"duplicate or unsatisfied step dependency: {sid}")
        obj = copy.deepcopy(step["object"])
        obj.setdefault("semantic", {}).setdefault("evidence", "host_assisted")
        scene["objects"].append(obj)
        summaries.append({"step_id": sid, "strategy": "host-geometry", "object_id": obj["id"]})
        completed.add(sid)
    scene["provenance"].update(brief=brief, steps=summaries,
                                complexity_limits={"draw_nodes": 128, "segments": 2400,
                                                   **plan.get("complexity_limits", {})})
    audit = semantic_audit(scene)
    if audit["status"] != "passed":
        raise ValueError("semantic mechanical audit rejected: " + ", ".join(audit["reasons"]))
    scene["provenance"]["semantic_audit"] = audit
    return scene


def semantic_audit(scene):
    """Recompute mechanical constraints for review; never certify scientific semantics.

    Counts include hidden drawable nodes and parsed path segments (including closes),
    so hiding geometry or using implicit SVG commands cannot bypass the budget.
    The binding excludes provenance but fingerprints the brief and limits separately.
    """
    from fontTools.pens.recordingPen import RecordingPen
    from fontTools.svgLib.path import parse_path

    validate(scene)
    provenance = scene.get("provenance", {})
    brief = provenance.get("brief", {})
    limits = {"draw_nodes": 128, "segments": 2400, **provenance.get("complexity_limits", {})}
    for key, maximum in (("draw_nodes", 3000), ("segments", 20000)):
        if type(limits[key]) is not int or not 1 <= limits[key] <= maximum:
            raise ValueError(f"invalid semantic complexity limit: {key}")
    if set(limits) != {"draw_nodes", "segments"}:
        raise ValueError("unknown semantic complexity limits")
    geometry = Geometry(scene)
    drawable = [obj for obj in scene["objects"] if obj["kind"] != "group"]
    segments = 0
    for obj in drawable:
        if obj["kind"] == "path":
            pen = RecordingPen()
            parse_path(obj["geometry"]["d"], pen)
            segments += sum(op in {"lineTo", "curveTo", "qCurveTo", "closePath"} for op, _ in pen.value)
    if scene["schema_version"] == "0.2":
        effect_paths = [r["d"] for r in scene.get("resources", []) if r["kind"] == "clip_path"]
        effect_paths += [shape["d"] for r in scene.get("resources", []) if r["kind"] == "alpha_mask" for shape in r["shapes"]]
        for d in effect_paths:
            pen = RecordingPen()
            parse_path(d, pen)
            segments += sum(op in {"lineTo", "curveTo", "qCurveTo", "closePath"} for op, _ in pen.value)
    counts = {"draw_nodes": len(drawable), "segments": segments}
    reasons = [f"complexity_budget:{key}" for key in counts if counts[key] > limits[key]]
    viewport = scene["canvas"].get("viewBox", [0, 0, scene["canvas"]["width"], scene["canvas"]["height"]])
    resources = {resource["id"]: resource for resource in scene.get("resources", [])}

    def painted(paint):
        if paint == "none":
            return False
        if paint.startswith("url(#"):
            return any(painted(color) for _, color in resources[paint[5:-1]]["stops"])
        return not (len(paint) == 9 and paint[-2:] == "00")

    visible_cache = {}

    def visible(oid):
        if oid in visible_cache:
            return visible_cache[oid]
        obj = geometry.nodes[oid]
        current = oid
        while current:
            node = geometry.nodes[current]
            if not node.get("visible", True) or node.get("style", {}).get("opacity", 1) == 0:
                visible_cache[oid] = False
                return False
            current = node.get("parent")
        if obj["kind"] == "group":
            result = any(visible(child) for child in geometry.children.get(oid, []))
        else:
            style = geometry.style(oid)
            fill = obj["kind"] != "connector" and painted(style.get("fill", "#000000")) and style.get("fill_opacity", 1) > 0
            stroke = painted(style.get("stroke", "none")) and style.get("stroke_opacity", 1) > 0
            box = geometry.bounds(oid)
            vx, vy, vw, vh = viewport
            result = bool((fill or stroke) and box and box[2] > vx and box[3] > vy
                          and box[0] < vx + vw and box[1] < vy + vh)
        visible_cache[oid] = result
        return result

    protected, effect_review = [], []
    for oid in brief.get("protected_ids", []):
        exists = oid in geometry.nodes
        shown = exists and visible(oid)
        record = {"id": oid, "exists": exists, "visible": shown}
        if exists and scene["schema_version"] == "0.2":
            affected = bool(scene.get("occlusions"))
            current = oid
            while current:
                node = geometry.nodes[current]
                affected = affected or "clip" in node or "mask" in node
                current = node.get("parent")
            if geometry.nodes[oid]["kind"] == "group":
                # Group coverage includes descendants and cannot be proven from
                # its original bounds, even when the effect is on a child.
                affected = affected or any("clip" in n or "mask" in n for n in scene["objects"])
            if affected:
                shown = shown and geometry.visible_bounds(oid) is not None
                record.update(visible=shown, effect_coverage="needs_review" if shown else "empty_conservative_bounds")
                if shown:
                    effect_review.append(f"protected_effect_coverage:{oid}")
        protected.append(record)
        if not exists:
            reasons.append(f"protected_id_missing:{oid}")
        elif not shown:
            reasons.append(f"protected_id_not_visible:{oid}")

    def fingerprint(data):
        return hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False,
                                        separators=(",", ":")).encode()).hexdigest()

    return {"status": "rejected" if reasons else "needs_review" if effect_review else "passed", "scope": "mechanical_only",
            "scientific_review": "host_required", "C": counts, "limits": limits,
            "protected_ids": protected, "reasons": reasons + effect_review,
            "binding": {"revision": scene["revision"],
                        "scene_sha256": fingerprint({k: v for k, v in scene.items() if k != "provenance"}),
                        "brief_sha256": fingerprint(brief), "limits_sha256": fingerprint(limits)},
            "limitations": ["Visibility checks flags, opacity, paint and bounds; occlusion and exact painted coverage need host review.",
                            "Scientific identity, relations and pixel topology are not certified."]}

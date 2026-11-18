"""Compare composed geometry to source pixels, independent of path count.

These are engineering diagnostics, not scientific correctness or a universal Q.
Runtime selection never reads benchmark annotations.
"""
from __future__ import annotations
import copy
import hashlib
import json
import re
from io import BytesIO

import cv2
import numpy as np
from PIL import Image

from .analysis import bind_analysis, foreground, palette_masks, rgb
from .geometry import Geometry
from .quality import mask_metrics, topology
from .render import render_svg
from .scene import finite, validate
from .svg import to_svg


DEFAULT_PROFILE = {"min_iou": 0.88, "max_color_mae": 0.10, "min_region_iou": 0.48,
                   "max_paths": 128, "max_segments": 2400, "appearance_slack": 0.012,
                   "min_internal_coverage": 0.72}


def fingerprint(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False,
                                    separators=(",", ":")).encode()).hexdigest()


def profile_values(profile=None):
    profile = profile or {}
    if set(profile)-set(DEFAULT_PROFILE):
        raise ValueError("unknown adaptive profile fields")
    result = {**DEFAULT_PROFILE, **profile}
    finite(result)
    for key, value in result.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("invalid adaptive profile value")
        if key in {"max_paths", "max_segments"}:
            if type(value) is not int or not 1 <= value <= 20000:
                raise ValueError("invalid complexity budget")
        elif not 0 <= value <= 1:
            raise ValueError("adaptive tolerances must be in [0,1]")
    return result


def render_pixels(scene):
    return np.asarray(Image.open(BytesIO(render_svg(to_svg(scene), 1))).convert("RGBA"))


def composite(pixels, background):
    values = pixels.astype(float)/255
    alpha = values[:, :, 3:4]
    return values[:, :, :3]*alpha + (rgb(background).astype(float)/255)*(1-alpha)


def current_audit(scene):
    audit=copy.deepcopy(scene.get("provenance",{}).get("adaptive_context",{}).get("audit",{}))
    binding=audit.get("binding",{})
    context=scene.get("provenance",{}).get("adaptive_context",{})
    if (binding.get("revision")!=scene["revision"]
            or binding.get("scene_sha256")!=fingerprint({k:v for k,v in scene.items() if k!="provenance"})
            or binding.get("analysis_sha256")!=fingerprint(context.get("analysis",{}))
            or binding.get("profile_sha256")!=fingerprint(context.get("profile",{}))):
        audit["status"]="stale_after_edit"
    return audit


def evaluate(path, analysis, scene, *, profile=None, budget=None):
    config = profile_values(profile)
    reference = bind_analysis(path, analysis)
    validate(scene)
    h, w = reference.shape[:2]
    if scene["canvas"].get("viewBox", [0, 0, w, h]) != [0, 0, w, h]:
        raise ValueError("candidate must use the source-local viewBox")
    if [scene["canvas"]["width"], scene["canvas"]["height"]] != [w, h]:
        raise ValueError("candidate canvas must equal source ROI")
    if budget is not None: budget.render()
    actual = render_pixels(scene)
    a = foreground(reference, analysis["background"])
    if np.any(reference[:,:,3]<128):
        b=actual[:,:,3]>=128
    else:
        # Use the same compositing semantics on both sides: a painted white hole
        # and an actual transparent hole are visually equivalent on an opaque source.
        b=np.max(np.abs(composite(actual,analysis["background"])*255-rgb(analysis["background"])),axis=2)>14
    geometry = mask_metrics(a, b, scale=1)
    active = a | b
    delta = np.abs(composite(reference, analysis["background"])-composite(actual, analysis["background"]))
    color_error = float(delta[active].mean()) if active.any() else 0.0
    alpha_error = float(np.abs(reference[:, :, 3].astype(float)-actual[:, :, 3])[active].mean()/255) if active.any() else 0.0
    source_regions = palette_masks(reference, analysis["palette"], analysis["background"],active=a)
    actual_regions = palette_masks(actual, analysis["palette"], analysis["background"],active=b)
    regions = []
    for i, (src, dst) in enumerate(zip(source_regions, actual_regions, strict=True)):
        # Retain small regions in evidence; only evaluate sufficiently resolved regions here.
        if src.sum() < 3:
            continue
        # Boundary blending creates narrow color bands, not additional material compartments.
        # Keep their error diagnostic, but require a source-resolved interior before using
        # a quantization bin as an automatic missing-region constraint. Host parts are
        # checked separately even when thin; this never discards them from the geometry.
        interior=int(np.count_nonzero(cv2.erode(src.astype(np.uint8),np.ones((3,3),np.uint8),borderValue=0)))
        regions.append({"region_id": f"region_{i+1}", "source_pixels": int(src.sum()),
                        "interior_pixels":interior,"constraint_applied":interior>=3,
                        **mask_metrics(src, dst, scale=1)})
    from .internal_structure import internal_edges, edge_comparison
    internal = edge_comparison(internal_edges(reference, a, analysis["background"]),
                               internal_edges(actual, a, analysis["background"]))
    geo = Geometry(scene)
    tasks = []
    for part in analysis["parts"]:
        matches = [o for o in scene["objects"] if o.get("semantic", {}).get("part_id") == part["id"]]
        addressable=len(matches)==1 and geo.visible(matches[0]["id"])
        effect_iou=0.0
        if addressable:
            hidden=copy.deepcopy(scene)
            for node in hidden["objects"]:
                if node["id"]==matches[0]["id"]:node["visible"]=False
            if budget is not None: budget.render()
            removed=render_pixels(hidden)
            effect=np.max(np.abs(composite(actual,analysis["background"])-composite(removed,analysis["background"])),axis=2)>0.03
            expected=a & (np.linalg.norm(reference[:,:,:3].astype(float)-rgb(part["color"]),axis=2)<=40)
            effect_iou=mask_metrics(expected,effect,scale=1)["iou"] if expected.any() else 0.0
        tasks.append({"part_id": part["id"], "required": part.get("required", True),
                      "node_ids": [o["id"] for o in matches], "addressable": addressable,
                      "visibility_edit_iou": effect_iou,
                      "task_passed": addressable and effect_iou>=config["min_region_iou"]})
    drawable = [o for o in scene["objects"] if o["kind"] != "group"]
    segments = sum(len(re.findall(r"[LlCcQqAaHhVvSsTt]", o.get("geometry", {}).get("d", "")))
                   if o["kind"] == "path" else 1 for o in drawable)
    reasons = []
    if geometry["iou"] < config["min_iou"]: reasons.append("outline_deviation")
    if not geometry["topology_equal"]: reasons.append("visible_topology_changed")
    if color_error > config["max_color_mae"]: reasons.append("source_appearance_loss")
    if analysis["features"].get("partial_alpha") and alpha_error>config["max_color_mae"]: reasons.append("source_partial_alpha_loss")
    if any(r["constraint_applied"] and r["iou"] < config["min_region_iou"] for r in regions): reasons.append("internal_region_missing_or_merged")
    if internal["constraint_applied"] and (internal["coverage"] < config["min_internal_coverage"]
            or any(c["coverage"] < 0.5 for c in internal["components"])):
        reasons.append("internal_boundary_missing")
    if any(t["required"] and not t["addressable"] for t in tasks): reasons.append("required_part_not_addressable")
    if any(t["required"] and t["addressable"] and not t["task_passed"] for t in tasks): reasons.append("part_edit_affects_wrong_region")
    if len(drawable) > config["max_paths"] or segments > config["max_segments"]: reasons.append("complexity_budget")
    return {"status": "passed" if not reasons else "needs_review", "reasons": reasons,
            "F": {**geometry, "source_color_mae": round(color_error, 6), "alpha_mae": round(alpha_error, 6), "internal_boundary": internal},
            "T": {"silhouette_equal": topology(a) == topology(b), "regions": regions,
                  "scope": "visible raster components/holes; no complete self-intersection/occlusion proof"},
            "E": {"status": ("visibility_task_passed" if all(t["task_passed"] for t in tasks) else "task_failed") if tasks else "not_specified", "tasks": tasks,
                  "note": "Visibility edit verified against source color evidence; semantic completeness remains host-reviewed."},
            "C": {"draw_nodes": len(drawable), "segments": segments},
            "binding": {"source_sha256": analysis["source"]["sha256"], "analysis_sha256": fingerprint(analysis),
                        "scene_sha256": fingerprint({k:v for k,v in scene.items() if k!="provenance"}),
                        "profile_sha256":fingerprint(config), "revision": scene["revision"]},
            "profile": config, "scientific_review": "host_required"}


def select_candidate(records, profile=None):
    """Hard constraints, Pareto dominance, then bounded appearance/complexity tradeoff."""
    config = profile_values(profile)
    feasible = [r for r in records if r.get("audit", {}).get("status") == "passed"]
    if not feasible:
        return None, {"status": "needs_review", "reason": "no_candidate_meets_constraints", "pareto": []}
    def objectives(record):
        q = record["audit"]
        return (q["F"]["source_color_mae"], 1-q["F"]["iou"], q["C"]["segments"], q["C"]["draw_nodes"])
    front = []
    for item in feasible:
        values = objectives(item)
        if not any(all(a <= b for a, b in zip(objectives(other), values)) and
                   any(a < b for a, b in zip(objectives(other), values)) for other in feasible if other is not item):
            front.append(item)
    best_error = min(r["audit"]["F"]["source_color_mae"] for r in front)
    short = [r for r in front if r["audit"]["F"]["source_color_mae"] <= best_error+config["appearance_slack"]]
    chosen = min(short, key=lambda r: (r["audit"]["C"]["draw_nodes"], r["audit"]["C"]["segments"],
                                      r["audit"]["F"]["source_color_mae"], r["candidate_id"]))
    return chosen, {"status": "selected", "candidate_id": chosen["candidate_id"],
                    "pareto": [r["candidate_id"] for r in front],
                    "reason": "feasible Pareto candidate; appearance slack then fewer draw nodes/segments",
                    "host_visual_review": "required", "weights": "no_global_weighted_score"}

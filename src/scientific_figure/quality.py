"""Generic drawing policy and bounded render/compare/repair for traced regions.

Measures geometric fidelity against an unsimplified contour baseline, not biology.
Each candidate is rendered by the same renderer used for delivered PNGs.
"""
from __future__ import annotations
import copy
import re
from io import BytesIO

import cv2
import numpy as np
from PIL import Image

from .render import render_svg
from .svg import to_svg
from .vectorize import trace_region

def scene_review(scene):
    from .geometry import Geometry
    geometry=Geometry(scene)
    canvas=scene["canvas"];vb=canvas.get("viewBox",[0,0,canvas["width"],canvas["height"]])
    issues=[];fills=set()
    for obj in scene["objects"]:
        if not geometry.visible(obj["id"]):continue
        style=geometry.style(obj["id"]);box=geometry.bounds(obj["id"])
        fill=style.get("fill","#000000")
        if fill!="none":fills.add(fill)
        if style.get("stroke","none")!="none" and style.get("stroke_width",1)<0.6:
            issues.append({"id":obj["id"],"code":"thin_stroke","action":"check final-size legibility"})
        if box and obj["kind"]!="group" and (box[0]<vb[0]-1 or box[1]<vb[1]-1 or box[2]>vb[0]+vb[2]+1 or box[3]>vb[1]+vb[3]+1):
            issues.append({"id":obj["id"],"code":"outside_canvas","action":"check crop or placement"})
        if box and obj["kind"]=="path" and fill!="none":
            w,h=box[2]-box[0],box[3]-box[1]
            if min(w,h)>0 and max(w,h)/min(w,h)>15:
                issues.append({"id":obj["id"],"code":"possible_outlined_stroke","action":"consider a centerline instead of a filled contour"})
    policy=scene.get("provenance",{}).get("drawing_policy",DEFAULT_POLICY)
    if len(fills)>policy.get("color_budget",12):
        issues.append({"code":"global_color_budget","action":"host must verify palette roles; no automatic recoloring"})
    reviews=[]
    for step in scene.get("provenance",{}).get("steps",[]):
        review=step.get("self_review")
        if review:reviews.append({"step_id":step["step_id"],"status":review["status"],"repairs":review["repairs"]})
        elif "status" in step:reviews.append({"step_id":step["step_id"],"status":step["status"]})
    status="needs_review" if issues or any(r["status"]!="passed" for r in reviews) else "passed" if reviews else "unexecuted"
    if scene["revision"]>0 and reviews:status="stale_after_edit"
    context=scene.get("provenance",{}).get("adaptive_context")
    if context:
        from .evaluation import current_audit
        audit=current_audit(scene)
        status=audit.get("status","needs_review")
        if issues and status=="passed":status="needs_review"
    return {"status":status,"issues":issues,"pixel_operation_reviews":reviews,
            "scope":"geometry/fill rendering checks only; scientific meaning and aesthetics need host visual review"}


DEFAULT_POLICY={"smooth":True,"curve_tolerance":0.8,"min_iou":0.96,
                "max_boundary_error_px":1.2,"max_rgb_mae":0.025,
                "max_segments":1600,"max_rounds":3,"color_budget":12}


def policy_values(policy):
    policy=policy or {}
    if set(policy)-set(DEFAULT_POLICY):raise ValueError("unknown drawing policy fields")
    result={**DEFAULT_POLICY,**policy}
    for key,value in result.items():
        if key=="smooth":
            if not isinstance(value,bool):raise ValueError("smooth must be boolean")
        elif isinstance(value,bool) or not isinstance(value,(int,float)) or not np.isfinite(value):
            raise ValueError(f"invalid drawing policy value: {key}")
    if not 0<result["min_iou"]<=1 or not 0<result["max_rgb_mae"]<=1:raise ValueError("invalid quality thresholds")
    if not 0<result["curve_tolerance"]<=10 or not 0<result["max_boundary_error_px"]<=10:raise ValueError("invalid curve/boundary tolerance")
    for key,limit in [("max_segments",20000),("max_rounds",3),("color_budget",64)]:
        if type(result[key]) is not int or not 1<=result[key]<=limit:raise ValueError(f"invalid {key}")
    return result


def topology(mask):
    contours,hierarchy=cv2.findContours(mask.astype(np.uint8),cv2.RETR_CCOMP,cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None:return [0,0]
    return [int(sum(item[3]==-1 for item in hierarchy[0])),int(sum(item[3]!=-1 for item in hierarchy[0]))]


def mask_metrics(reference,candidate,scale=2):
    a,b=reference>0,candidate>0
    union=int(np.count_nonzero(a|b))
    iou=float(np.count_nonzero(a&b)/union) if union else 1.0
    ae=cv2.morphologyEx(a.astype(np.uint8),cv2.MORPH_GRADIENT,np.ones((3,3),np.uint8))>0
    be=cv2.morphologyEx(b.astype(np.uint8),cv2.MORPH_GRADIENT,np.ones((3,3),np.uint8))>0
    if ae.any() and be.any():
        da=cv2.distanceTransform((~ae).astype(np.uint8),cv2.DIST_L2,3)
        db=cv2.distanceTransform((~be).astype(np.uint8),cv2.DIST_L2,3)
        error=max(float(np.quantile(da[be],0.95)),float(np.quantile(db[ae],0.95)))/scale
    else:error=0.0 if not ae.any() and not be.any() else float(max(a.shape))/scale
    return {"iou":round(iou,6),"boundary_error_px_p95":round(error,4),
            "topology_equal":topology(a)==topology(b)}


def raster(nodes,width,height, *, masks=False):
    objects=copy.deepcopy(nodes)
    for obj in objects:
        obj.pop("parent",None)
        if masks:obj["style"]["fill"]="#ffffff"
    scene={"schema_version":"0.1","revision":0,"canvas":{"width":width,"height":height},"objects":objects}
    return np.asarray(Image.open(BytesIO(render_svg(to_svg(scene),2))).convert("RGBA"))


def audit_candidate(baseline,candidate,width,height):
    # Per-path masks prevent one color/object from hiding damage to another.
    metrics=[]
    for original,proposed in zip(baseline,candidate,strict=True):
        a=raster([original],width,height,masks=True)[:,:,3]>=128
        b=raster([proposed],width,height,masks=True)[:,:,3]>=128
        item={"id":original["id"],**mask_metrics(a,b)};metrics.append(item)
    a=raster(baseline,width,height).astype(float)/255
    b=raster(candidate,width,height).astype(float)/255
    # Premultiplied color avoids arbitrary RGB values of transparent pixels.
    a[:,:,:3]*=a[:,:,3:4];b[:,:,:3]*=b[:,:,3:4]
    active=(a[:,:,3]>0)|(b[:,:,3]>0)
    mae=float(np.abs(a[active]-b[active]).mean()) if active.any() else 0
    segments=sum(len(re.findall(r"[LlCcQqAaHhVvSsTt]",o["geometry"]["d"])) for o in candidate)
    return {"paths":metrics,"worst_iou":min((m["iou"] for m in metrics),default=1),
            "worst_boundary_error_px":max((m["boundary_error_px_p95"] for m in metrics),default=0),
            "topology_equal":all(m["topology_equal"] for m in metrics),
            "premultiplied_rgba_mae":round(mae,6),"segments":segments}


def reviewed_trace(image_path,region,*,object_id,parameters=None,policy=None):
    config=policy_values(policy)
    params=copy.deepcopy(parameters or {})
    epsilon=params.pop("simplify",0.7)
    baseline,summary=trace_region(image_path,region,object_id=object_id,simplify=0,**params)
    width,height=region[2:]
    if width*height>512*512:raise ValueError("unsupported: self-audited trace region exceeds prototype 512x512 budget")
    history=[];chosen=baseline;chosen_audit=None
    for attempt in range(config["max_rounds"]):
        conservative=attempt==config["max_rounds"]-1 and attempt>0
        candidate,_=trace_region(image_path,region,object_id=object_id,
                                 simplify=0 if conservative else epsilon/(2**attempt),**params)
        fitted=[]
        if config["smooth"] and not conservative:
            from .curves import smooth_path
            for obj in candidate:
                try:
                    d,report=smooth_path(obj["geometry"]["d"],tolerance=config["curve_tolerance"]/(2**attempt))
                    obj["geometry"]["d"]=d
                except ValueError as error:
                    # A degenerate local ring keeps its original contour; never invent a replacement.
                    report={"fallback":"original_contour","reason":str(error)}
                fitted.append(report)
        audit=audit_candidate(baseline,candidate,width,height)
        reasons=[]
        if audit["worst_iou"]<config["min_iou"]:reasons.append("outline_iou")
        if audit["worst_boundary_error_px"]>config["max_boundary_error_px"]:reasons.append("boundary_deviation")
        if not audit["topology_equal"]:reasons.append("holes_or_components_changed")
        if audit["premultiplied_rgba_mae"]>config["max_rgb_mae"]:reasons.append("fill_color_or_overlap_changed")
        if audit["segments"]>config["max_segments"]:reasons.append("path_complexity_budget")
        if len({o["style"]["fill"] for o in candidate})>config["color_budget"]:reasons.append("fill_color_budget")
        history.append({"round":attempt+1,"strategy":"conservative_contours" if conservative else "fit_curves" if config["smooth"] else "simplified_contours",
                        "simplify":0 if conservative else epsilon/(2**attempt),"fit_reports":fitted,
                        "audit":audit,"accepted":not reasons,"reasons":reasons})
        chosen,chosen_audit=candidate,audit
        if not reasons:break
    summary.update({"policy":config,"self_review":{"status":"passed" if history[-1]["accepted"] else "needs_review",
                    "reference":"unsimplified pixel-center contours, not scientific truth",
                    "history":history,"repairs":len(history)-1},"path_count":len(chosen),
                    "segments":chosen_audit["segments"]})
    return chosen,summary

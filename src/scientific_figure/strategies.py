"""Generic reconstruction families; no scientific object vocabulary/templates."""
from __future__ import annotations
import math

import cv2
import numpy as np

from .analysis import foreground, hex_color, palette_masks, rgb
from .curves import smooth_path


METHODS = ("primitive_fit", "flat_contour", "color_layers", "color_layers_fine", "region_bands", "stroke_layers")


def _structure_layers(pixels, analysis, object_id, method, scene):
    from .color_partition import surface_palette
    from .pixel_regions import pixel_paths
    from .internal_structure import ridge_mask, skeletonize, skeleton_chains
    from .curves import fit_open_path
    active = foreground(pixels, analysis["background"])
    ridges = ridge_mask(pixels, active, analysis["background"]) if method == "stroke_layers" else np.zeros_like(active)
    image = pixels[:, :, :3].copy()
    if ridges.any():
        image = cv2.inpaint(image, ridges.astype(np.uint8)*255, 3, cv2.INPAINT_TELEA)
    image = cv2.bilateralFilter(image, 5, 20, 2)
    count = analysis["configuration"]["colors"]
    palette = surface_palette(image, active, count)
    for part in analysis["parts"]:
        if part["color"] not in palette and len(palette) < 16:
            palette.append(part["color"])
    rgba = np.dstack((image, pixels[:, :, 3]))
    masks = palette_masks(rgba, palette, analysis["background"], active=active)
    dominant = max(range(len(masks)), key=lambda i: int(masks[i].sum()))
    nodes = scene["objects"]
    groups = {}
    def group(color, role):
        distances = [float(np.linalg.norm(rgb(color).astype(float)-rgb(p["color"]))) for p in analysis["parts"]]
        part = analysis["parts"][int(np.argmin(distances))] if distances and min(distances) <= 40 else None
        key = ("part", part["id"]) if part else (role, color)
        if key not in groups:
            gid = f"{object_id}_structure_{len(groups)+1}"
            semantic = {"role": part.get("role", "host_part") if part else role,
                        "evidence": "host_provided" if part else "observed_contrast_hypothesis"}
            if part:
                semantic["part_id"] = part["id"]
            nodes.append({"id":gid,"kind":"group","parent":object_id,"style":{"fill":color,"stroke":color},"semantic":semantic})
            groups[key] = (gid, color)
        return groups[key]
    order = [dominant]+[j for j in range(len(masks)) if j != dominant]
    for position, i in enumerate(order):
        paths = pixel_paths(active if position == 0 else masks[i])
        if not paths:
            continue
        color = palette[i]
        gid, inherited = group(color, "observed_color_region")
        # One compound region can contain multiple visible islands without claiming
        # those islands are one scientific object. Its actual boundary cost is counted.
        for j,d in enumerate(paths):
            fitted = []
            for ring in d.split("Z"):
                if not ring.strip():
                    continue
                ring += "Z"
                if ring.count("L") >= 12:
                    try:
                        ring, _ = smooth_path(ring, tolerance=0.45, corner_angle=100)
                    except ValueError:
                        pass
                fitted.append(ring)
            d = "".join(fitted)
            style = {"fill_rule":"evenodd", "stroke":"none"}
            if color != inherited:
                style["fill"] = color
            nodes.append({"id":f"{object_id}_band_{i+1}_{j+1}","kind":"path","parent":gid,
                          "geometry":{"d":d},"style":style})
    if ridges.any():
        skeleton = skeletonize(ridges)
        chains, graph = skeleton_chains(skeleton)
        distance = cv2.distanceTransform(ridges.astype(np.uint8), cv2.DIST_L2, 5)
        for i,points in enumerate(chains):
            if len(points) < 3:
                continue
            xx = np.clip(points[:,0].astype(int),0,pixels.shape[1]-1)
            yy = np.clip(points[:,1].astype(int),0,pixels.shape[0]-1)
            color = hex_color(np.median(pixels[yy,xx,:3], axis=0))
            gid, inherited = group(color, "observed_internal_stroke")
            style = {"fill":"none","stroke_width":float(max(1,2*np.median(distance[yy,xx])-1))}
            if color != inherited:
                style["stroke"] = color
            nodes.append({"id":f"{object_id}_stroke_{i+1}","kind":"path","parent":gid,
                          "geometry":{"d":fit_open_path(points)},"style":style,
                          "semantic":{"role":"observed_centerline","evidence":"dark_ridge_hypothesis"}})
        scene.setdefault("provenance",{})["stroke_graph"] = graph
    scene.setdefault("provenance",{})["representation"] = {
        "method":method,"palette":palette,"palette_method":"weighted_lab_source" if method == "region_bands" else "weighted_lab_inpainted_surface",
        "color_limit":count,"scientific_identity":"unknown"}
    return scene


def mask_paths(mask, *, smooth=True):
    contours, hierarchy = cv2.findContours(mask.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None:
        return []
    paths = []
    for i, ring in enumerate(contours):
        if hierarchy[0][i][3] != -1:
            continue
        rings = [ring]
        child = hierarchy[0][i][2]
        while child != -1:
            rings.append(contours[child]); child = hierarchy[0][child][0]
        parts = []
        for contour in rings:
            pts = contour.reshape(-1, 2)
            if len(pts) < 3 or abs(cv2.contourArea(contour)) == 0:
                continue
            parts.append("M" + "L".join(f"{x+0.5:g},{y+0.5:g}" for x, y in pts) + "Z")
        if parts:
            if len(paths)>=256:
                raise ValueError("contour component budget exceeded")
            d = "".join(parts)
            if smooth:
                try:
                    d, _ = smooth_path(d, tolerance=0.35)
                except ValueError:
                    pass  # Explicit conservative representation of a degenerate ring.
            paths.append(d)
    return paths


def build_candidate(pixels, analysis, object_id, method):
    if method not in METHODS:
        raise ValueError("unsupported reconstruction method")
    h, w = pixels.shape[:2]
    active = foreground(pixels, analysis["background"])
    root = {"id": object_id, "kind": "group", "semantic": {"role": "reconstructed_object", "evidence": "source_visual"}}
    scene = {"schema_version": "0.1", "revision": 0, "canvas": {"width": w, "height": h, "background": "none"},
             "objects": [root]}
    nodes = scene["objects"]
    if method in {"region_bands", "stroke_layers"}:
        return _structure_layers(pixels, analysis, object_id, method, scene)
    fill = hex_color(np.median(pixels[:, :, :3][active], axis=0))
    if method == "primitive_fit":
        yy, xx = np.nonzero(active)
        x0, y0, x1, y1 = float(xx.min()), float(yy.min()), float(xx.max()+1), float(yy.max()+1)
        cx, cy, rx, ry = (x0+x1)/2, (y0+y1)/2, (x1-x0)/2, (y1-y0)/2
        gy, gx = np.indices(active.shape)
        ellipse_mask = ((gx+0.5-cx)/rx)**2+((gy+0.5-cy)/ry)**2 <= 1
        rect_mask = (gx >= x0) & (gx < x1) & (gy >= y0) & (gy < y1)
        ellipse_error = np.count_nonzero(ellipse_mask != active)
        rect_error = np.count_nonzero(rect_mask != active)
        if ellipse_error < rect_error:
            node = {"kind": "ellipse", "geometry": {"cx": cx, "cy": cy, "rx": rx, "ry": ry}}
        else:
            node = {"kind": "rect", "geometry": {"x": x0, "y": y0, "width": x1-x0, "height": y1-y0}}
        node.update({"id": object_id+"_body", "parent": object_id, "style": {"fill": fill}})
        nodes.append(node)
    elif method == "flat_contour":
        for i, d in enumerate(mask_paths(active)):
            nodes.append({"id": f"{object_id}_body_{i+1}", "kind": "path", "parent": object_id,
                          "geometry": {"d": d}, "style": {"fill": fill, "fill_rule": "evenodd"}})
    else:
        masks = palette_masks(pixels, analysis["palette"], analysis["background"])
        # A continuous base prevents transparent cracks between adjacent color masks.
        # It retains actual silhouette holes; upper color layers supply internal regions.
        dominant = max(range(len(masks)), key=lambda i: int(masks[i].sum()))
        order = [dominant] + [i for i in range(len(masks)) if i != dominant]
        part_groups = {}
        for i in order:
            color = analysis["palette"][i]
            mask = active if i == dominant else masks[i]
            paths = mask_paths(mask, smooth=method != "color_layers_fine")
            if not paths:
                continue
            part = None
            if analysis["parts"]:
                distances = [float(np.linalg.norm(rgb(color).astype(float)-rgb(p["color"]))) for p in analysis["parts"]]
                if min(distances) <= 40:
                    part = analysis["parts"][int(np.argmin(distances))]
            key = part["id"] if part else f"region_{i+1}"
            if key not in part_groups:
                gid = f"{object_id}_layer_{len(part_groups)+1}"
                semantic = {"role": part.get("role", "host_part") if part else "observed_color_region",
                            "evidence": "host_provided" if part else "observed_color_partition"}
                if part:
                    semantic["part_id"] = part["id"]
                nodes.append({"id": gid, "kind": "group", "parent": object_id,
                              "style": {"fill":color}, "semantic": semantic})
                part_groups[key] = gid
            inherited=next(o["style"]["fill"] for o in nodes if o["id"]==part_groups[key])
            for j, d in enumerate(paths):
                style={"fill_rule":"evenodd"}
                if color!=inherited:style["fill"]=color
                nodes.append({"id": f"{object_id}_r{i+1}_p{j+1}", "kind": "path", "parent": part_groups[key],
                              "geometry": {"d": d}, "style": style})
    if len(nodes) == 1:
        raise ValueError("needs_input: no resolved contours; thin strokes need a different backend")
    # A single-color representation can expose a single host part, but never invent several.
    if method in {"primitive_fit", "flat_contour"} and len(analysis["parts"]) == 1:
        part = analysis["parts"][0]
        if np.linalg.norm(rgb(fill).astype(float)-rgb(part["color"])) <= 40:
            root["semantic"]["part_id"] = part["id"]
            root["style"]={"fill":fill}
            for node in nodes[1:]:node.get("style",{}).pop("fill",None)
    return scene

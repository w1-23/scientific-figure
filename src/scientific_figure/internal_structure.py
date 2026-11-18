"""Observed internal contrast, without assigning scientific or annotation roles.

Edges are measured from source RGB, independently of candidate palette masks.
Dark narrow ridges are an alternative drawing hypothesis, not semantic truth.
"""
from __future__ import annotations

import cv2
import numpy as np

VERSION = "rgb-contrast-v1"


def visible_rgb(pixels, background):
    from .analysis import rgb
    alpha = pixels[:, :, 3:4].astype(float)/255
    return np.rint(pixels[:, :, :3]*alpha + rgb(background)*(1-alpha)).astype(np.uint8)


def internal_edges(pixels, active, background):
    image = visible_rgb(pixels, background)
    edges = np.zeros(active.shape, dtype=np.uint8)
    for channel in cv2.split(image):
        blurred = cv2.GaussianBlur(channel, (3, 3), 0.6)
        edges |= cv2.Canny(blurred, 24, 60)
    # Do not relabel the silhouette (including actual holes) as an internal edge.
    interior = cv2.erode(active.astype(np.uint8), np.ones((5, 5), np.uint8), borderValue=0)
    return (edges != 0) & (interior != 0)


def edge_comparison(source, actual):
    """Tolerant coverage and per-visible-component diagnostics; no topology proof."""
    def covered(a, b):
        if not a.any():
            return 1.0
        if not b.any():
            return 0.0
        distance = cv2.distanceTransform((~b).astype(np.uint8), cv2.DIST_L2, 5)
        return float(np.mean(distance[a] <= 1.5))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(source.astype(np.uint8), 8)
    components = []
    for index in range(1, count):
        size = int(stats[index, cv2.CC_STAT_AREA])
        if size >= 12:
            components.append({"source_pixels": size,
                               "bbox": stats[index, :4].astype(int).tolist(),
                               "coverage": round(covered(labels == index, actual), 6)})
    return {"source_pixels": int(source.sum()), "actual_pixels": int(actual.sum()),
            "coverage": round(covered(source, actual), 6),
            "precision": round(covered(actual, source), 6),
            "components": components, "tolerance_px": 1.5,
            "constraint_applied": int(source.sum()) >= 12,
            "scope": "visible contrast coverage; endpoints/occlusion/scientific identity not certified"}


def ridge_mask(pixels, active, background):
    gray = cv2.cvtColor(visible_rgb(pixels, background), cv2.COLOR_RGB2GRAY)
    # Closing estimates the local lighter surface around a narrow dark mark.
    response = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT,
                                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    mask = (response >= 24) & active
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    keep = np.flatnonzero(stats[:, cv2.CC_STAT_AREA] >= 3)
    keep = keep[keep != 0]
    return np.isin(labels, keep) if count > 1 else np.zeros_like(active)


def skeletonize(mask):
    """Bounded Zhang-Suen thinning; no optional OpenCV-contrib dependency."""
    image = np.pad(mask.astype(np.uint8), 1)
    for _ in range(512):
        changed = False
        for phase in (0, 1):
            p = [image[:-2, 1:-1], image[:-2, 2:], image[1:-1, 2:], image[2:, 2:],
                 image[2:, 1:-1], image[2:, :-2], image[1:-1, :-2], image[:-2, :-2]]
            neighbours = sum(p)
            transitions = sum((p[i] == 0) & (p[(i+1) % 8] == 1) for i in range(8))
            if phase == 0:
                condition = (p[0]*p[2]*p[4] == 0) & (p[2]*p[4]*p[6] == 0)
            else:
                condition = (p[0]*p[2]*p[6] == 0) & (p[0]*p[4]*p[6] == 0)
            remove = (image[1:-1, 1:-1] == 1) & (neighbours >= 2) & (neighbours <= 6) & (transitions == 1) & condition
            if remove.any():
                image[1:-1, 1:-1][remove] = 0
                changed = True
        if not changed:
            return image[1:-1, 1:-1].astype(bool)
    raise ValueError("skeleton iteration budget exceeded")


def skeleton_chains(skeleton):
    """Trace every graph edge once, splitting at endpoints and branches.

    Suppress diagonal shortcuts when an orthogonal route exists. Pixel graph
    junctions remain observed hypotheses, not inferred scientific connections.
    """
    points = set(zip(*np.nonzero(skeleton)))
    if len(points) > 40000:
        raise ValueError("stroke graph pixel budget exceeded")
    graph = {}
    for y, x in sorted(points):
        neighbours = []
        for dy, dx in ((-1,0),(0,1),(1,0),(0,-1),(-1,-1),(-1,1),(1,1),(1,-1)):
            other = (y+dy, x+dx)
            if other in points and not (dy and dx and ((y+dy,x) in points or (y,x+dx) in points)):
                neighbours.append(other)
        graph[(y,x)] = sorted(neighbours)
    used, chains = set(), []
    def edge(a, b):
        return tuple(sorted((a,b)))
    starts = [p for p in graph if len(graph[p]) != 2] + [p for p in graph if len(graph[p]) == 2]
    for start in starts:
        for following in graph[start]:
            if edge(start, following) in used:
                continue
            chain = [start, following]
            used.add(edge(start, following))
            while len(graph[chain[-1]]) == 2 and chain[-1] != start:
                nxt = next(p for p in graph[chain[-1]] if p != chain[-2])
                key = edge(chain[-1], nxt)
                if key in used:
                    break
                used.add(key); chain.append(nxt)
            chains.append(np.asarray([(x+0.5, y+0.5) for y,x in chain], dtype=float))
            if len(chains) > 512:
                raise ValueError("stroke branch budget exceeded")
    return chains, {"endpoints": sum(len(v) == 1 for v in graph.values()),
                    "junction_pixels": sum(len(v) > 2 for v in graph.values()),
                    "isolated_pixels": sum(not v for v in graph.values())}


def contrast_summary(pixels, active, background):
    """Cheap RGB edge observation, with no ridge or skeleton work."""
    edges = internal_edges(pixels, active, background)
    return {"method": VERSION, "evidence": "observed_rgb_contrast",
            "internal_edge_pixels": int(edges.sum()), "scientific_identity": "unknown",
            "limitations": ["RGB edges do not certify stroke roles, connectivity or hidden structure."]}


def structure_summary(pixels, active, background):
    edges = internal_edges(pixels, active, background)
    ridges = ridge_mask(pixels, active, background)
    skeleton = skeletonize(ridges)
    try:
        _, graph = skeleton_chains(skeleton)
    except ValueError as error:
        graph = {"status": "unresolved_budget", "reason": str(error)}
    return {"method": VERSION, "evidence": "observed_rgb_contrast",
            "internal_edge_pixels": int(edges.sum()), "dark_ridge_pixels": int(ridges.sum()),
            "ridge_graph": graph, "scientific_identity": "unknown",
            "limitations": ["Dark ridges may include annotation lines, texture or boundaries.",
                            "Brightness, color and stroke roles are hypotheses; hidden structure is not recovered."]}

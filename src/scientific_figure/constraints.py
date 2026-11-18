"""Raster evidence as explicit construction constraints, not a picture to look at.

``constraints build`` turns one reference image into a bounded, deterministic
pack: multi-scale edge maps with typed components, shape/region/negative-space/
repetition candidates, palette, and coordinate mappings. ``constraints check``
renders the *current* project (and each bound object alone), compares edges,
regions and repetition inside declared source regions, and returns pass /
soft-violation / hard-violation per binding.

Boundary: components and contours are geometric candidates. Classes such as
``strong_structural`` or ``texture`` are deterministic heuristic buckets; they
are not object identities, not scientific evidence and not probabilities.
Nothing here writes a Scene or changes the user's geometry.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from io import BytesIO
import hashlib
import json
import math
from pathlib import Path
import platform

import cv2
import numpy as np
import PIL
from PIL import Image, ImageDraw

from .coords import load_oriented, source_to_roi
from .evidence import _palette, _staged_directory
from .geometry import Geometry
from .scene import schema_validate, validate

MAX_COMPONENTS = 4000
MAX_CONTOURS = 2000
MAX_REGIONS = 400
MAX_HOLES = 400
MAX_REPETITIONS = 200
MAX_ALIGN_PIXELS = 24_000_000

SCALES = {
    "coarse": {"sigma": 2.4, "low": 30, "high": 85, "min_area": 90},
    "medium": {"sigma": 1.1, "low": 55, "high": 135, "min_area": 24},
    "fine": {"sigma": 0.0, "low": 22, "high": 65, "min_area": 6},
}
CLASS_COLORS = {
    "strong_structural": (18, 92, 200), "internal_boundary": (0, 160, 150),
    "fine_stroke": (220, 60, 120), "texture": (150, 150, 150),
    "antialias": (200, 200, 200), "gradient_transition": (240, 160, 40),
    "uncertain": (120, 80, 180),
}
PARAMETERS = {
    "algorithm": "multi-scale-typed-edge-constraints-v1",
    "scales": SCALES,
    "classification": {"fine_max_enclosed": 40, "fine_min_length": 15,
                       "antialias_max_area": 12, "antialias_max_gradient": 120,
                       "gradient_max_gradient": 110, "structural_min_enclosed": 400,
                       "internal_min_enclosed": 60},
    "regions": {"palette_colors": 8, "min_region_area": 60, "min_hole_area": 30},
    "repetition": {"min_area": 25, "min_members": 4, "area_ratio": 1.6},
    "alignment": {"tolerance_px": 2.0, "recall_min": 0.72, "precision_min": 0.5,
                  "soft_offset_slack": 1.0, "hard_recall_gap": 0.15},
    "limits": {"components": MAX_COMPONENTS, "contours": MAX_CONTOURS,
               "regions": MAX_REGIONS, "holes": MAX_HOLES,
               "repetitions": MAX_REPETITIONS, "align_pixels": MAX_ALIGN_PIXELS},
}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
                    encoding="utf-8")


def _safe_name(name):
    from pathlib import PurePosixPath
    path = PurePosixPath(name)
    if (len(path.parts) != 1 or path.is_absolute() or path.name in {"", ".", ".."}
            or any(c in name for c in "\\:/") or name != path.name):
        raise ValueError("unsafe artifact reference path")
    return path.name


def _region(value, size, label="ROI"):
    if (not isinstance(value, (list, tuple)) or len(value) != 4
            or any(type(v) is not int for v in value)):
        raise ValueError(f"{label} must be integer [x, y, width, height]")
    x, y, w, h = value
    if min(x, y) < 0 or min(w, h) <= 0 or x + w > size[0] or y + h > size[1]:
        raise ValueError(f"{label} outside EXIF-oriented source bounds")
    return list(value)


def _background(value):
    if value is None:
        return None
    if isinstance(value, str) and len(value) == 7 and value[0] == "#":
        int(value[1:], 16)
        return value.lower()
    if (isinstance(value, (list, tuple)) and len(value) == 3
            and all(type(v) is int and 0 <= v <= 255 for v in value)):
        return "#" + "".join(f"{v:02x}" for v in value)
    raise ValueError("background must be #RRGGBB, three RGB integers, or null")


def _request(value, source):
    if not isinstance(value, dict):
        raise ValueError("constraint request must be an object")
    unknown = set(value) - {"schema_version", "roi", "background", "scales", "bindings"}
    if unknown:
        raise ValueError("unsupported constraint request fields: " + ", ".join(sorted(unknown)))
    if value.get("schema_version") != "0.1":
        raise ValueError("constraint request schema_version must be 0.1")
    image, meta = load_oriented(source, region=value.get("roi"),
                                background=_background(value.get("background")))
    scales = value.get("scales", ["coarse", "medium", "fine"])
    if (not isinstance(scales, (list, tuple)) or not scales
            or set(scales) - set(SCALES) or len(set(scales)) != len(scales)):
        raise ValueError("scales must be a nonempty subset of coarse/medium/fine")
    bindings = _bindings(value.get("bindings", []), meta["size"])
    return image, meta, [s for s in SCALES if s in scales], bindings


def _bindings(value, size):
    if not isinstance(value, list) or len(value) > 500:
        raise ValueError("bindings must be a list of at most 500 entries")
    out, seen = [], set()
    for entry in value:
        if not isinstance(entry, dict):
            raise ValueError("binding must be an object")
        unknown = set(entry) - {"object_id", "source_region", "kind", "allowed_deviation",
                                "recall_min", "group", "min_count", "notes"}
        if unknown:
            raise ValueError("unsupported binding fields: " + ", ".join(sorted(unknown)))
        oid = entry.get("object_id")
        if not isinstance(oid, str) or not oid or len(oid) > 100:
            raise ValueError("binding object_id must be a nonempty string")
        if oid in seen:
            raise ValueError("duplicate binding object_id")
        seen.add(oid)
        kind = entry.get("kind", "boundary")
        if kind not in {"boundary", "region", "negative_space", "repetition"}:
            raise ValueError("unsupported binding kind")
        deviation = entry.get("allowed_deviation", PARAMETERS["alignment"]["tolerance_px"])
        if type(deviation) not in {int, float} or not 0 < deviation <= 40:
            raise ValueError("allowed_deviation must be in (0, 40]")
        recall = entry.get("recall_min", PARAMETERS["alignment"]["recall_min"])
        if type(recall) not in {int, float} or not 0 <= recall <= 1:
            raise ValueError("recall_min must be in [0, 1]")
        out.append({"object_id": oid, "kind": kind,
                    "source_region": _region(entry["source_region"], size, "binding region")
                    if "source_region" in entry else None,
                    "allowed_deviation": float(deviation), "recall_min": float(recall),
                    "group": entry.get("group"), "min_count": entry.get("min_count", 2),
                    "notes": str(entry.get("notes", ""))[:2000]})
    return out


def _edges(gray, scale):
    params = SCALES[scale]
    if params["sigma"]:
        gray = cv2.GaussianBlur(gray, (5, 5), params["sigma"], borderType=cv2.BORDER_REPLICATE)
    return cv2.Canny(gray, params["low"], params["high"], L2gradient=True)


def _gradient(gray):
    dx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3, borderType=cv2.BORDER_REPLICATE)
    dy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3, borderType=cv2.BORDER_REPLICATE)
    return np.clip(np.hypot(dx, dy), 0, 255)


def _classify(enclosed, length, mean_gradient, area):
    """Deterministic buckets; ordering is documented and not a probability.

    ``enclosed`` is the area enclosed by the edge loop, which separates a long
    object outline from a thin open stroke far better than skeleton thickness.
    """
    p = PARAMETERS["classification"]
    if area < p["antialias_max_area"] and mean_gradient < p["antialias_max_gradient"]:
        return "antialias"
    if enclosed < p["fine_max_enclosed"] and length > p["fine_min_length"]:
        return "fine_stroke"
    if mean_gradient < p["gradient_max_gradient"]:
        return "gradient_transition"
    if enclosed >= p["structural_min_enclosed"]:
        return "strong_structural"
    if enclosed >= p["internal_min_enclosed"]:
        return "internal_boundary"
    return "uncertain"


def _confidence(edge_class, length, area):
    base = {"strong_structural": 0.78, "internal_boundary": 0.58, "fine_stroke": 0.62,
            "gradient_transition": 0.34, "texture": 0.22, "antialias": 0.12,
            "uncertain": 0.3}[edge_class]
    if edge_class in {"strong_structural", "internal_boundary"}:
        base = min(0.98, base + min(0.18, length / 4000))
    return round(base, 3)


def _components(edges, gradient):
    count, labels, stats, _ = cv2.connectedComponentsWithStats(edges, connectivity=8)
    if count <= 1:
        return [], labels
    keep = [i for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] >= 4]
    keep.sort(key=lambda i: (-stats[i, cv2.CC_STAT_AREA], i))
    truncated = len(keep) > MAX_COMPONENTS
    keep = keep[:MAX_COMPONENTS]
    records = []
    for i in keep:
        x, y, w, h, area = (int(v) for v in stats[i])
        mask = (labels[y:y + h, x:x + w] == i).astype(np.uint8)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        length = max((cv2.arcLength(c, False) for c in contours), default=0.0)
        enclosed = max((cv2.contourArea(c) for c in contours), default=0.0)
        thickness = area / length if length else float(area)
        mean_gradient = float(np.mean(gradient[y:y + h, x:x + w][mask > 0])) if area else 0.0
        edge_class = _classify(enclosed, length, mean_gradient, area)
        records.append({"id": f"edge-{i}", "bbox": [x, y, x + w, y + h], "area": area,
                        "length": round(length, 3), "thickness": round(thickness, 3),
                        "enclosed_area": round(enclosed, 2),
                        "mean_gradient": round(mean_gradient, 2), "class": edge_class,
                        "confidence": _confidence(edge_class, length, area)})
    return records, labels


def _tag_texture(records, width, height):
    """Small components in dense neighbourhoods become texture candidates."""
    small = [r for r in records if r["area"] < 40]
    if len(small) < 12:
        return
    grid = Counter((r["bbox"][0] // 48, r["bbox"][1] // 48) for r in small)
    dense = {cell for cell, n in grid.items() if n >= 6}
    for record in small:
        cell = (record["bbox"][0] // 48, record["bbox"][1] // 48)
        if cell in dense and record["class"] in {"uncertain", "internal_boundary"}:
            record["class"] = "texture"
            record["confidence"] = _confidence("texture", record["length"], record["area"])


def _contours(edges, labels, component_classes, scale):
    min_area = SCALES[scale]["min_area"]
    found, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    found = [c for c in found if cv2.contourArea(c) >= min_area]
    found.sort(key=lambda c: (-cv2.contourArea(c), cv2.boundingRect(c)[:2]))
    truncated = len(found) > MAX_CONTOURS
    records = []
    height, width = edges.shape[:2]
    for index, contour in enumerate(found[:MAX_CONTOURS], 1):
        x, y, w, h = cv2.boundingRect(contour)
        perimeter = cv2.arcLength(contour, True)
        area = cv2.contourArea(contour)
        if len(contour) >= 3:
            approx = cv2.approxPolyDP(contour, 0.01 * perimeter, True).reshape(-1, 2)
        else:
            approx = contour.reshape(-1, 2)
        if len(approx) > 64:
            step = int(math.ceil(len(approx) / 64))
            approx = approx[::step]
        votes = Counter()
        for px, py in approx:
            px, py = int(np.clip(px, 0, width - 1)), int(np.clip(py, 0, height - 1))
            label = int(labels[py, px])
            if label in component_classes:
                votes[component_classes[label]] += 1
        edge_class = votes.most_common(1)[0][0] if votes else "uncertain"
        fill = area / (w * h) if w * h else 0.0
        records.append({"id": f"contour-{scale}-{index}", "scale": scale, "class": edge_class,
                        "bbox": [x, y, x + w, y + h], "area": round(area, 2),
                        "perimeter": round(perimeter, 2), "fill_ratio": round(fill, 4),
                        "centroid": [round(x + w / 2, 2), round(y + h / 2, 2)],
                        "points": [[int(px), int(py)] for px, py in approx],
                        "confidence": _confidence(edge_class, perimeter, area)})
    return records, truncated


def _regions(rgb):
    entries, _ = _palette(Image.fromarray(rgb))
    entries = entries[:PARAMETERS["regions"]["palette_colors"]]
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.int16)
    centers = np.array([cv2.cvtColor(np.uint8([[e["rgb"]]]), cv2.COLOR_RGB2LAB)[0, 0]
                        for e in entries], dtype=np.int16)
    distance = np.stack([np.sum((lab - c) ** 2, axis=2) for c in centers], axis=0)
    index = np.argmin(distance, axis=0)
    min_region = PARAMETERS["regions"]["min_region_area"]
    min_hole = PARAMETERS["regions"]["min_hole_area"]
    regions, holes = [], []
    for k, entry in enumerate(entries):
        mask = (index == k).astype(np.uint8)
        count, labels, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
        order = [i for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] >= min_region]
        order.sort(key=lambda i: (-stats[i, cv2.CC_STAT_AREA], i))
        for i in order[:max(0, MAX_REGIONS - len(regions))]:
            x, y, w, h, area = (int(v) for v in stats[i])
            piece = (labels[y:y + h, x:x + w] == i).astype(np.uint8)
            outer, _ = cv2.findContours(piece, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            perimeter = sum(cv2.arcLength(c, True) for c in outer)
            regions.append({"id": f"region-{k}-{i}", "bbox": [x, y, x + w, y + h],
                            "area": area, "perimeter": round(perimeter, 2),
                            "color": entry["hex"], "fill_ratio": round(area / (w * h), 4),
                            "roid": f"region-{k}-{i}", "touches_border": bool(
                                x == 0 or y == 0 or x + w >= rgb.shape[1] or y + h >= rgb.shape[0])})
        holes_found, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        if hierarchy is None:
            continue
        for hole_index, contour in enumerate(holes_found):
            if hierarchy[0][hole_index][3] == -1:  # external boundary, not an enclosed hole
                continue
            if cv2.contourArea(contour) < min_hole:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            if len(holes) >= MAX_HOLES:
                break
            holes.append({"id": f"hole-{k}-{len(holes)}", "bbox": [x, y, x + w, y + h],
                          "area": round(cv2.contourArea(contour), 2), "color": entry["hex"]})
    return regions, holes, entries


def _repetitions(contours):
    candidates = [c for c in contours if c["area"] >= PARAMETERS["repetition"]["min_area"]
                  and len(c["points"]) >= 3]
    # RETR_LIST returns concentric outer/inner contours of one shape; keep the
    # largest area per source-pixel centroid so one object is not a "repeat".
    best = {}
    for candidate in candidates:
        key = (round(candidate["centroid"][0]), round(candidate["centroid"][1]))
        if key not in best or candidate["area"] > best[key]["area"]:
            best[key] = candidate
    candidates = list(best.values())
    buckets = {}
    for c in candidates:
        x0, y0, x1, y1 = c["bbox"]
        w, h = max(1, x1 - x0), max(1, y1 - y0)
        key = (round(math.sqrt(c["area"]) / 3), round(w / h * 3), round(c["fill_ratio"] * 8))
        buckets.setdefault(key, []).append(c)
    groups = []
    for members in buckets.values():
        if len(members) < PARAMETERS["repetition"]["min_members"]:
            continue
        members = sorted(members, key=lambda c: (c["centroid"][1], c["centroid"][0]))
        centers = np.array([m["centroid"] for m in members])
        if len(centers) > 1:
            delta = centers[:, None, :] - centers[None, :, :]
            nearest = np.sqrt(np.sum(delta ** 2, axis=2))
            np.fill_diagonal(nearest, np.inf)
            spacing = float(np.median(np.min(nearest, axis=1)))
        else:
            spacing = 0.0
        xs, ys = centers[:, 0], centers[:, 1]
        areas = [m["area"] for m in members]
        spread = max(xs.max() - xs.min(), ys.max() - ys.min())
        if spacing < 1.0 or spread < 2 * math.sqrt(max(areas)):
            continue  # nested or stacked duplicates are not a repeated arrangement
        rect = cv2.minAreaRect(np.array([p for m in members for p in m["points"]], dtype=np.float32))
        groups.append({"id": f"repeat-{len(groups) + 1}", "count": len(members),
                       "bbox": [float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())],
                       "centers": [[round(float(x), 2), round(float(y), 2)] for x, y in centers],
                       "median_spacing": round(spacing, 2),
                       "size_range": [round(math.sqrt(min(areas)), 2), round(math.sqrt(max(areas)), 2)],
                       "orientation": round(float(rect[2]), 2),
                       "member_ids": [m["id"] for m in members]})
        if len(groups) >= MAX_REPETITIONS:
            break
    return groups


def _classified_image(shape, components):
    canvas = np.full((shape[0], shape[1], 3), 255, np.uint8)
    for record in components:
        color = CLASS_COLORS[record["class"]]
        x0, y0, x1, y1 = record["bbox"]
        cv2.rectangle(canvas, (x0, y0), (x1 - 1, y1 - 1), color, 1)
    return Image.fromarray(canvas)


def _overlay(rgb, regions, holes):
    canvas = rgb.copy()
    for index, region in enumerate(regions[:300]):
        color = (30 + (index * 53) % 200, 60 + (index * 97) % 180, 90 + (index * 29) % 160)
        x0, y0, x1, y1 = region["bbox"]
        cv2.rectangle(canvas, (x0, y0), (x1 - 1, y1 - 1), color, 1)
    for hole in holes[:300]:
        x0, y0, x1, y1 = [int(v) for v in hole["bbox"]]
        cv2.rectangle(canvas, (x0, y0), (x1 - 1, y1 - 1), (255, 0, 128), 2)
    return Image.fromarray(canvas)


def _build(source, request_path, stage):
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    rgb_image, meta, scales, bindings = _request(request, source)
    rgb = np.asarray(rgb_image)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gradient = _gradient(gray)
    records, edge_packs = [], {}
    for scale in scales:
        edges = _edges(gray, scale)
        components, labels = _components(edges, gradient)
        _tag_texture(components, rgb.shape[1], rgb.shape[0])
        classes = {int(r["id"].split("-")[1]): r["class"] for r in components}
        contours, truncated = _contours(edges, labels, classes, scale)
        records.append(components)
        edge_packs[scale] = {"artifact": f"edges_{scale}.png", "components": components,
                             "contours": contours, "truncated": truncated}
    all_components = [r for group in records for r in group]
    all_contours = [c for pack in edge_packs.values() for c in pack["contours"]]
    regions, holes, palette = _regions(rgb)
    repetitions = _repetitions(all_contours)
    classified = _classified_image(rgb.shape[:2], all_components)
    overlay = _overlay(rgb, regions, holes)
    images = {"normalized.png": rgb_image, "classified.png": classified,
              "regions.png": overlay}
    for scale in scales:
        images[f"edges_{scale}.png"] = Image.fromarray(_edges(gray, scale), mode="L")
    for name, image in images.items():
        image.save(stage / name, format="PNG", optimize=False, compress_level=9)
    artifacts = [{"path": name, "sha256": _hash((stage / name).read_bytes()),
                  "bytes": (stage / name).stat().st_size} for name in images]
    source_record = {**meta, "sha256": _hash(Path(source).read_bytes())}
    palette_record = [{"hex": e["hex"], "pixels": e["pixels"]} for e in palette]
    payload = {"source": source_record, "scales": scales, "bindings": bindings,
               "edge_packs": edge_packs, "regions": regions, "holes": holes,
               "repetitions": repetitions, "palette": palette_record}
    digest = _hash(json.dumps(payload, sort_keys=True).encode())
    pack = {"schema_version": "0.1", "kind": "constraint_pack", "created_at": _now(),
            "source": source_record,
            "parameters": PARAMETERS, "scales": scales,
            "mappings": {"source_to_roi": source_to_roi(meta["roi"]),
                         "roi_to_source": [1, 0, 0, 1, meta["roi"][0], meta["roi"][1]]},
            "bindings": bindings, "edge_packs": edge_packs, "regions": regions,
            "holes": holes, "repetitions": repetitions, "palette": palette_record,
            "artifacts": artifacts, "pack_sha256": digest,
            "versions": {"python": platform.python_version(), "numpy": np.__version__,
                         "pillow": PIL.__version__, "opencv": cv2.__version__},
            "limits": PARAMETERS["limits"]}
    schema_validate(pack, "constraint-pack")
    _write_json(stage / "constraints.json", pack)


def build_constraints(source, request, output):
    """Write one new atomic constraint pack and return its inline snapshot."""
    with _staged_directory(output) as (stage, target):
        _build(source, request, stage)
        result = validate_constraint_pack(stage)
    result["directory"] = str(target)
    return result


def validate_constraint_pack(directory):
    """Validate structure, artifact bytes and recorded candidate budgets."""
    root = Path(directory).resolve()
    pack = json.loads((root / "constraints.json").read_text(encoding="utf-8"))
    schema_validate(pack, "constraint-pack")
    if pack["parameters"] != PARAMETERS:
        raise ValueError("unsupported constraint parameters")
    declared = {record["path"] for record in pack["artifacts"]} | {"constraints.json"}
    if {path.name for path in root.iterdir()} != declared:
        raise ValueError("unexpected or missing constraint artifacts")
    payload = {key: pack[key] for key in ("source", "scales", "bindings",
                                          "edge_packs", "regions", "holes",
                                          "repetitions", "palette")}
    if _hash(json.dumps(payload, sort_keys=True).encode()) != pack["pack_sha256"]:
        raise ValueError("constraint pack content hash mismatch")
    if len(pack["regions"]) > MAX_REGIONS or len(pack["holes"]) > MAX_HOLES:
        raise ValueError("constraint candidate budget exceeded")
    for record in pack["artifacts"]:
        path = root / _safe_name(record["path"])
        if not path.is_file() or path.stat().st_size != record["bytes"]:
            raise ValueError("constraint artifact size mismatch")
        if _hash(path.read_bytes()) != record["sha256"]:
            raise ValueError("constraint artifact hash mismatch")
        with Image.open(path) as image:
            image.load()
            if image.width * image.height > MAX_ALIGN_PIXELS:
                raise ValueError("constraint raster exceeds pixel budget")
    return pack


def _load_pack(directory):
    root = Path(directory).resolve()
    pack = validate_constraint_pack(root)
    edges = {}
    for scale, entry in pack["edge_packs"].items():
        with Image.open(root / _safe_name(entry["artifact"])) as image:
            edges[scale] = np.asarray(image.convert("L"))
    return pack, edges, root


def _subtree(scene, oid):
    """Scene containing only the object's subtree (plus its ancestor transforms)."""
    geometry = Geometry(scene)
    if oid not in geometry.nodes:
        raise ValueError(f"binding object missing from project: {oid}")
    keep, stack = set(), [oid]
    while stack:
        node = stack.pop()
        if node in keep:
            continue
        keep.add(node)
        stack.extend(geometry.children.get(node, []))
    ancestors, current = set(), geometry.nodes[oid].get("parent")
    while current:
        ancestors.add(current)
        current = geometry.nodes[current].get("parent")
    allowed = keep | ancestors
    nodes = {o["id"]: o for o in scene["objects"]}
    objects = []
    for obj in scene["objects"]:
        if obj["id"] not in allowed:
            continue
        if obj["kind"] == "connector" and not {obj["geometry"]["from"], obj["geometry"]["to"]} <= allowed:
            continue
        objects.append(obj)
    filtered = {**scene, "objects": objects,
                "resources": scene.get("resources", [])}
    if "occlusions" in scene:
        filtered["occlusions"] = [o for o in scene["occlusions"]
                                  if {o["over"], o["under"], o["frame"]} <= allowed]
    validate(filtered)
    return filtered


def _render(scene, scale=1):
    from .render import render_svg
    from .svg import to_svg
    data = render_svg(to_svg(scene), scale=scale)
    with Image.open(BytesIO(data)) as image:
        return image.convert("RGB")


def _edge_metrics(source_edges, render_edges, tolerance):
    if source_edges.shape != render_edges.shape:
        raise ValueError("edge maps must share one size")
    if not source_edges.any() or not render_edges.any():
        return {"recall": 0.0, "precision": 0.0, "mean_offset": None, "max_offset": None,
                "source_edge_pixels": int(source_edges.sum() / 255),
                "render_edge_pixels": int(render_edges.sum() / 255)}
    to_render = cv2.distanceTransform(255 - render_edges, cv2.DIST_L2, 5)
    to_source = cv2.distanceTransform(255 - source_edges, cv2.DIST_L2, 5)
    source_values = to_render[source_edges > 0]
    render_values = to_source[render_edges > 0]
    return {"recall": round(float(np.mean(source_values <= tolerance)), 4),
            "precision": round(float(np.mean(render_values <= tolerance)), 4),
            "mean_offset": round(float(np.mean(source_values)), 3),
            "max_offset": round(float(np.percentile(source_values, 95)), 3),
            "source_edge_pixels": int(source_values.size),
            "render_edge_pixels": int(render_values.size)}


def _signature(rgb):
    """Coarse region signature: significant components, enclosed holes, background share."""
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    height, width = gray.shape[:2]
    quantized = np.uint8(gray // 32)
    count, _, stats, _ = cv2.connectedComponentsWithStats(quantized, connectivity=8)
    areas = sorted((int(stats[i, cv2.CC_STAT_AREA]) for i in range(1, count)), reverse=True)
    total = quantized.size
    significant = [a for a in areas if a >= total * 0.004]
    background = (gray > 240).astype(np.uint8)
    bg_count, _, bg_stats, _ = cv2.connectedComponentsWithStats(background, connectivity=4)
    holes = 0
    for i in range(1, bg_count):
        x, y, w, h, area = (int(v) for v in bg_stats[i])
        if area >= 16 and x > 0 and y > 0 and x + w < width and y + h < height:
            holes += 1
    return {"regions": len(significant), "largest": areas[0] if areas else 0,
            "background_fraction": round(float(np.mean(background)), 4), "holes": holes}


def _count_components(rgb, size_range):
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, SCALES["medium"]["low"], SCALES["medium"]["high"], L2gradient=True)
    count, _, stats, _ = cv2.connectedComponentsWithStats(edges, connectivity=8)
    low, high = size_range
    found = 0
    for i in range(1, count):
        area = stats[i, cv2.CC_STAT_AREA]
        if low * low * 0.15 <= area <= high * high * 4:
            found += 1
    return found


def check_constraints(project, pack_directory, *, bindings=None, output=None):
    """Compare the current project against one constraint pack."""
    from .project import load_project
    pack, pack_edges, root = _load_pack(pack_directory)
    scene = load_project(project)
    roi = pack["source"]["roi"]
    size = (roi[2], roi[3])
    full = _render(scene)
    scale_note = None
    if full.size != size:
        scale_note = {"render_size": list(full.size), "pack_size": list(size),
                      "mode": "lanczos_resample_to_source_roi"}
        full = full.resize(size, Image.Resampling.LANCZOS)
    full_rgb = np.asarray(full)
    full_gray = cv2.cvtColor(full_rgb, cv2.COLOR_RGB2GRAY)
    render_edges = {s: _edges(full_gray, s) for s in pack["scales"] if s in {*pack_edges}}
    use = "medium" if "medium" in pack["scales"] else pack["scales"][0]
    overall = _edge_metrics(pack_edges[use], render_edges[use],
                            PARAMETERS["alignment"]["tolerance_px"])
    region_source = _signature(np.asarray(Image.open(root / "normalized.png").convert("RGB")))
    region_render = _signature(full_rgb)
    results = []
    for binding in (bindings or pack.get("bindings", [])):
        results.append(_check_binding(scene, pack, pack_edges, binding, size, root))
    hard = [r for r in results if r["status"] == "failed"]
    soft = [r for r in results if r["status"] == "needs_review"]
    report = {"schema_version": "0.1", "kind": "constraint_check", "created_at": _now(),
              "pack_sha256": pack["pack_sha256"], "project": str(Path(project).resolve()),
              "render": {"size": list(full.size), "resample": scale_note,
                         "sha256": _hash(full.tobytes())},
              "edge_scale": use, "overall": {
                  **overall,
                  "region_delta": {key: region_render[key] - region_source[key]
                                   for key in region_source},
                  "recall_min": PARAMETERS["alignment"]["recall_min"],
                  "status": "needs_review" if soft or hard else
                            ("passed" if overall["recall"] >= PARAMETERS["alignment"]["recall_min"]
                             else "failed")},
              "bindings": results,
              "summary": {"passed": len(results) - len(soft) - len(hard),
                          "needs_review": len(soft), "failed": len(hard)},
              "visual_review": "host_required", "scientific_review": "not_executed",
              "limitations": [
                  "Edge agreement measures geometry near visible boundaries, not scientific meaning.",
                  "A high score cannot certify that an omitted structure was unimportant.",
                  "Typed edge classes are heuristic buckets, not object detection.",
                  "Figures with gradients or painterly edges need explicit soft constraints."]}
    if output is not None:
        Path(output).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def _check_binding(scene, pack, pack_edges, binding, size, root):
    roi = binding["source_region"] or pack["source"]["roi"]
    x, y, w, h = roi
    tolerance = binding["allowed_deviation"]
    use = "medium" if "medium" in pack["scales"] else pack["scales"][0]
    source_edges = pack_edges[use][y:y + h, x:x + w]
    subtree = _subtree(scene, binding["object_id"])
    render = _render(subtree)
    if render.size != size:
        render = render.resize(size, Image.Resampling.LANCZOS)
    gray = cv2.cvtColor(np.asarray(render), cv2.COLOR_RGB2GRAY)
    render_edges = _edges(gray, use)[y:y + h, x:x + w]
    metrics = _edge_metrics(source_edges, render_edges, tolerance)
    status, notes = "passed", []
    if metrics["recall"] is None:
        status = "failed"
        notes.append("No renderable edges for this object.")
    else:
        if metrics["recall"] < binding["recall_min"]:
            gap = binding["recall_min"] - metrics["recall"]
            status = "failed" if gap >= PARAMETERS["alignment"]["hard_recall_gap"] else "needs_review"
            notes.append(f"Boundary recall {metrics['recall']:.3f} below {binding['recall_min']:.3f}.")
        if metrics["mean_offset"] is not None and metrics["mean_offset"] > tolerance:
            status = "failed" if metrics["mean_offset"] > tolerance + 2 * PARAMETERS["alignment"]["soft_offset_slack"] else "needs_review"
            notes.append(f"Mean boundary offset {metrics['mean_offset']:.2f}px exceeds {tolerance:.2f}px.")
    detail = {}
    if binding["kind"] == "region":
        normalized = np.asarray(Image.open(root / "normalized.png").convert("RGB"))
        ox, oy = pack["source"]["roi"][:2]
        source_crop = normalized[y - oy:y - oy + h, x - ox:x - ox + w]
        rendered_crop = np.asarray(render)[y:y + h, x:x + w]
        detail["source"] = _signature(source_crop) if source_crop.size else None
        detail["render"] = _signature(rendered_crop)
        if detail["source"] and abs(detail["source"]["regions"] - detail["render"]["regions"]) > 2:
            status = "needs_review"
            notes.append("Region count differs by more than two.")
    elif binding["kind"] == "negative_space":
        source_holes = [hole for hole in pack["holes"]
                        if x <= hole["bbox"][0] <= x + w and y <= hole["bbox"][1] <= y + h]
        rendered = _signature(np.asarray(render)[y:y + h, x:x + w])
        detail = {"source_holes": len(source_holes),
                  "render_region_signature": rendered}
        if source_holes and rendered["regions"] < max(1, len(source_holes)):
            status = "needs_review"
            notes.append("Enclosed negative space candidates are not visibly separated.")
    elif binding["kind"] == "repetition":
        group = next((g for g in pack["repetitions"] if g["id"] == binding["group"]), None)
        if group is None:
            status = "failed"
            notes.append("Unknown repetition group in constraint pack.")
        else:
            found = _count_components(np.asarray(render)[y:y + h, x:x + w], group["size_range"])
            detail = {"group": group["id"], "expected": group["count"], "detected": found,
                      "min_count": binding["min_count"]}
            if found < binding["min_count"]:
                status = "failed"
                notes.append(f"Only {found} repeated candidates detected; at least {binding['min_count']} required.")
            elif found < group["count"] * 0.6:
                status = "needs_review"
                notes.append("Fewer repeated candidates than the reference group.")
    return {"object_id": binding["object_id"], "kind": binding["kind"], "source_region": roi,
            "allowed_deviation": tolerance, "recall_min": binding["recall_min"],
            "status": status, "metrics": metrics, "detail": detail, "notes": notes}


def review_checks(report):
    """Convert a constraint report into plan-review checks (no silent pass)."""
    if report.get("kind") != "constraint_check":
        raise ValueError("expected a constraint_check report")
    checks = []
    for binding in report.get("bindings", []):
        status = {"passed": "passed", "needs_review": "needs_review", "failed": "failed"}[binding["status"]]
        checks.append({"kind": "fidelity", "object_id": binding["object_id"],
                       "constraint": binding["kind"], "status": status,
                       "recall": binding["metrics"].get("recall"),
                       "mean_offset": binding["metrics"].get("mean_offset"),
                       "notes": binding["notes"]})
    return checks

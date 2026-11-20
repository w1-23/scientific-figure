"""Deterministic Scene-level layout audit.

This module answers mechanical, reproducible questions about a Scene's layout:
does text overlap other text, does a label sit on a connector, does anything
leave the canvas, are the declared panels aligned. It deliberately has no PASS
verdict: a clean audit is `clear`, which means the declared checks ran and
found nothing, not that the figure is scientifically correct or attractive.

Every finding carries the object IDs, the measured value and the threshold
that produced it. Findings that cannot be measured (no target output size,
gradient fills, missing glyphs) are reported as `not_auditable` instead of
being treated as clean.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

from .fonts import check_text, text_line_boxes
from .geometry import Geometry, local_bounds, point, transform_box

SCHEMA_VERSION = "0.1"

DEFAULT_THRESHOLDS = {
    "text_text_overlap": 0.05,          # overlap area / smaller line box
    "overlap_min_area_pt2": 1.0,        # absolute floor, filters float noise
    "text_stroke_inset_pt": 0.6,        # text box inset before stroke tests
    "stroke_penetration_pt": 0.5,       # deeper than this is a fail
    "canvas_fail_pt": 0.5,
    "font_min_pt": 5.0,
    # The 7pt ceiling in the SVG audit targets journal single-panel figures;
    # mechanism titles are routinely larger, so this only flags extreme cases.
    "font_max_pt": 24.0,
    "large_text_pt": 18.0,
    "padding_min_pt": 2.0,
    "padding_fail_pt": 0.5,
    "gutter_tolerance_pt": 1.5,
    "align_tolerance_pt": 0.5,
    "connector_clearance_pt": 1.0,
    "contrast_normal": 4.5,
    "contrast_large": 3.0,
    "content_bbox_warn_ratio": 0.9,
    "margin_imbalance_warn": 0.5,
}

PANEL_ROLES = {"panel", "card", "frame", "container", "node"}
OVERLAP_CHECKS = {"text_text_overlap"}

CHECKS = (
    "text_text_overlap",
    "text_stroke_intersection",
    "text_container_padding",
    "object_canvas_overflow",
    "font_glyph_size",
    "aligned_panels_and_gutters",
    "connector_clearance",
    "contrast_semantic_color",
    "negative_space_balance",
    "required_structure",
    "stale_exemption",
)

LIMITATIONS = [
    "Measures declared geometry only; scientific meaning and visual design still need host review.",
    "Text boxes come from the same font metrics the emitter uses; a renderer may differ by a pixel or two.",
    "Rotated or skewed strokes are tested through axis-aligned bands and are reported as approximations.",
    "Connectors are straight segments in Scene 0.3; routed polylines are not measured.",
]


# ---------------------------------------------------------------- geometry ---

def _area(rect):
    return max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])


def _intersection(a, b):
    rect = [max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])]
    return rect if rect[0] < rect[2] and rect[1] < rect[3] else None


def overlap_ratio(a, b):
    inter = _intersection(a, b)
    if inter is None:
        return 0.0
    smaller = min(_area(a), _area(b))
    return _area(inter) / smaller if smaller > 0 else 0.0


def inflate(rect, distance):
    return [rect[0] - distance, rect[1] - distance, rect[2] + distance, rect[3] + distance]


def shrink(rect, distance):
    return inflate(rect, -distance)


def _point_in_rect(point_xy, rect):
    return rect[0] <= point_xy[0] <= rect[2] and rect[1] <= point_xy[1] <= rect[3]


def _contains(outer, inner, tolerance=1e-9):
    return (inner[0] >= outer[0] - tolerance and inner[1] >= outer[1] - tolerance
            and inner[2] <= outer[2] + tolerance and inner[3] <= outer[3] + tolerance)


def clip_segment(p, q, rect):
    """Liang-Barsky: the visible parameter range of segment p-q inside rect."""
    x0, y0, x1, y1 = rect
    t0, t1 = 0.0, 1.0
    dx, dy = q[0] - p[0], q[1] - p[1]
    for den, num in ((-dx, p[0] - x0), (dx, x1 - p[0]), (-dy, p[1] - y0), (dy, y1 - p[1])):
        if den == 0:
            if num < 0:
                return None
        else:
            t = num / den
            if den < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
    return (t0, t1) if t0 <= t1 else None


def segment_rect_hit(p, q, rect):
    return clip_segment(p, q, rect) is not None


def _point_rect_distance(p, rect):
    dx = max(rect[0] - p[0], 0.0, p[0] - rect[2])
    dy = max(rect[1] - p[1], 0.0, p[1] - rect[3])
    return math.hypot(dx, dy)


def segment_rect_distance(p, q, rect):
    if segment_rect_hit(p, q, rect):
        return 0.0
    best = min(_point_rect_distance(p, rect), _point_rect_distance(q, rect))
    for corner in ((rect[0], rect[1]), (rect[2], rect[1]), (rect[0], rect[3]), (rect[2], rect[3])):
        best = min(best, _point_segment_distance(corner, p, q))
    return best


def _point_segment_distance(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(p[0] - ax, p[1] - ay)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def _matrix_scale(matrix):
    """Uniform scale of a 2x2 linear part; non-uniform scales are approximated."""
    return math.sqrt(abs(matrix[0] * matrix[3] - matrix[1] * matrix[2]))


def _is_axis_aligned(matrix):
    return abs(matrix[1]) < 1e-9 and abs(matrix[2]) < 1e-9


# ------------------------------------------------------------------ colors ---

def parse_color(value):
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if text in {"", "none", "transparent"}:
        return None
    if text.startswith("url("):
        return None
    if text.startswith("#"):
        body = text[1:]
        if len(body) == 3:
            body = "".join(c * 2 for c in body)
        if len(body) == 6:
            try:
                return tuple(int(body[i:i + 2], 16) for i in (0, 2, 4))
            except ValueError:
                return None
        return None
    named = {"white": (255, 255, 255), "black": (0, 0, 0), "red": (255, 0, 0),
             "green": (0, 128, 0), "blue": (0, 0, 255), "gray": (128, 128, 128),
             "grey": (128, 128, 128), "navy": (0, 0, 128), "orange": (255, 165, 0)}
    return named.get(text)


def _luminance(rgb):
    def channel(value):
        v = value / 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a, b):
    la, lb = _luminance(a), _luminance(b)
    high, low = max(la, lb), min(la, lb)
    return (high + 0.05) / (low + 0.05)


# --------------------------------------------------------------- utilities ---

def _round(value):
    return None if value is None else round(float(value), 6)


def _round_box(box):
    return None if box is None else [_round(v) for v in box]


def _semantic(obj):
    value = obj.get("semantic")
    return value if isinstance(value, dict) else {}


def _role(obj):
    role = _semantic(obj).get("role")
    return role if isinstance(role, str) else None


def _check(kind, status, objects, measurement, threshold, message, *, fixable=False,
           approximation=None, not_auditable_reason=None):
    payload = {
        "id": kind + ":" + "+".join(objects) if objects else kind,
        "kind": kind,
        "status": status,
        "severity": {"fail": "fail", "warn": "warn"}.get(status, "info"),
        "objects": list(objects),
        "measurement": measurement,
        "threshold": threshold,
        "exemptions": [],
        "message": message,
        "fixable": fixable,
        "not_auditable_reason": not_auditable_reason,
    }
    if approximation:
        payload["measurement"]["approximation"] = approximation
    return payload


def _dedupe(checks):
    seen, result = set(), []
    for check in checks:
        base = check["id"]
        if check["id"] in seen:
            index = 2
            while f"{base}#{index}" in seen:
                index += 1
            check["id"] = f"{base}#{index}"
        seen.add(check["id"])
        result.append(check)
    return result


# ------------------------------------------------------------------ target ---

def resolve_target(scene, request):
    """Return physical-output context, or None when none was declared."""
    declared = (request or {}).get("target")
    source = "request"
    if declared is None:
        declared = (scene.get("provenance", {}).get("qa", {}) or {}).get("target") \
            if isinstance(scene.get("provenance", {}).get("qa", {}), dict) else None
        source = "provenance"
    if not isinstance(declared, dict):
        return None
    width_mm = declared.get("width_mm")
    dpi = declared.get("dpi")
    width_px = declared.get("width_px")
    if width_mm is None and width_px is not None and dpi:
        width_mm = float(width_px) / float(dpi) * 25.4
    if not isinstance(width_mm, (int, float)) or not math.isfinite(width_mm) or width_mm <= 0:
        return None
    canvas = scene["canvas"]
    view_box = canvas.get("viewBox") or [0, 0, canvas["width"], canvas["height"]]
    width_units = float(view_box[2])
    if width_units <= 0:
        return None
    units_to_pt = (float(width_mm) / 25.4 * 72.0) / width_units
    return {
        "width_mm": _round(width_mm),
        "dpi": _round(dpi) if isinstance(dpi, (int, float)) else None,
        "units_per_mm": _round(width_units / float(width_mm)),
        "units_to_pt": _round(units_to_pt),
        "source": source,
        "auditable": True,
    }


class _Units:
    """Convert pt thresholds into Scene units; fall back to raw units without a target."""

    def __init__(self, target):
        self.target = target
        self.available = target is not None
        self.pt = 1.0 / target["units_to_pt"] if target else 1.0

    def length(self, value_pt):
        return value_pt * self.pt

    def area(self, value_pt2):
        return value_pt2 * self.pt * self.pt

    @property
    def kind(self):
        return "pt" if self.available else "world_units"


# ------------------------------------------------------------------- scene ---

def _view_box(scene):
    canvas = scene["canvas"]
    return canvas.get("viewBox") or [0, 0, canvas["width"], canvas["height"]]


def _text_entries(scene, geometry):
    entries = {}
    for obj in scene["objects"]:
        if obj["kind"] != "text" or not geometry.visible(obj["id"]):
            continue
        boxes = [transform_box(box, geometry.world(obj["id"]))
                 for box in text_line_boxes(obj["geometry"], geometry.style(obj["id"]))]
        boxes = [box for box in boxes if box]
        if not boxes:
            continue
        entries[obj["id"]] = {
            "object": obj,
            "boxes": boxes,
            "bbox": [min(b[0] for b in boxes), min(b[1] for b in boxes),
                     max(b[2] for b in boxes), max(b[3] for b in boxes)],
        }
    return entries


def _ancestors(geometry, oid):
    nodes = geometry.nodes
    current = nodes[oid].get("parent")
    while current:
        yield current
        current = nodes[current].get("parent")


def _container(scene, geometry, oid):
    nodes = geometry.nodes
    for ancestor in _ancestors(geometry, oid):
        obj = nodes[ancestor]
        if obj["kind"] == "group":
            continue
        if not geometry.visible(ancestor):
            return None
        return ancestor
    center = _cell_center(text_entries_box(geometry, oid))
    candidates = []
    for obj in scene["objects"]:
        if obj["id"] == oid or obj["kind"] in {"text", "connector", "group"}:
            continue
        if not geometry.visible(obj["id"]):
            continue
        box = geometry.bounds(obj["id"])
        if box and _point_in_rect(center, box):
            candidates.append((_area(box), obj["id"]))
    return min(candidates)[1] if candidates else None


def text_entries_box(geometry, oid):
    boxes = [transform_box(box, geometry.world(oid))
             for box in text_line_boxes(geometry.nodes[oid]["geometry"], geometry.style(oid))]
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _cell_center(box):
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


# ------------------------------------------------------------------- checks ---

def _check_text_overlap(entries, thresholds, units):
    checks = []
    ids = sorted(entries)
    for i, left in enumerate(ids):
        for right in ids[i + 1:]:
            best = None
            for la, box_a in enumerate(entries[left]["boxes"]):
                for lb, box_b in enumerate(entries[right]["boxes"]):
                    inter = _intersection(box_a, box_b)
                    if inter is None:
                        continue
                    area = _area(inter)
                    if area < units.area(thresholds["overlap_min_area_pt2"]):
                        continue
                    ratio = overlap_ratio(box_a, box_b)
                    if ratio >= thresholds["text_text_overlap"]:
                        if best is None or ratio > best[0]:
                            best = (ratio, area, la, lb, box_a, box_b)
            if best:
                ratio, area, la, lb, box_a, box_b = best
                checks.append(_check(
                    "text_text_overlap", "fail", [left, right],
                    {"overlap_ratio": _round(ratio), "overlap_area": _round(area),
                     "line_index": la, "other_line_index": lb,
                     "boxes": {"a": _round_box(box_a), "b": _round_box(box_b)}},
                    {"max_overlap_ratio": thresholds["text_text_overlap"]},
                    f"text boxes overlap by {ratio:.0%} (limit {thresholds['text_text_overlap']:.0%})",
                    fixable=False))
    return checks


def _edge_bands(corners, stroke_width, approximation):
    bands = []
    for i in range(4):
        p, q = corners[i], corners[(i + 1) % 4]
        rect = [min(p[0], q[0]), min(p[1], q[1]), max(p[0], q[0]), max(p[1], q[1])]
        bands.append(inflate(rect, stroke_width / 2))
    return bands, approximation


def _stroke_objects(scene, geometry):
    result = []
    for obj in scene["objects"]:
        oid = obj["id"]
        if obj["kind"] not in {"rect", "ellipse", "path"} or not geometry.visible(oid):
            continue
        style = geometry.style(oid)
        if style.get("stroke", "none") == "none" or not style.get("stroke_width", 1):
            continue
        result.append(oid)
    return result


def _check_text_stroke(entries, scene, geometry, thresholds, units):
    checks = []
    for oid in _stroke_objects(scene, geometry):
        obj = geometry.nodes[oid]
        style = geometry.style(oid)
        width = float(style.get("stroke_width", 1))
        matrix = geometry.world(oid)
        approximation = None
        if obj["kind"] == "rect":
            g = obj["geometry"]
            local = [(g["x"], g["y"]), (g["x"] + g["width"], g["y"]),
                     (g["x"] + g["width"], g["y"] + g["height"]), (g["x"], g["y"] + g["height"])]
            corners = [point(matrix, *xy) for xy in local]
            if not _is_axis_aligned(matrix):
                approximation = "rotated_rect_band"
        else:
            box = geometry.bounds(oid)
            if box is None:
                continue
            corners = [(box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])]
            approximation = "aabb_perimeter"
        bands, approximation = _edge_bands(corners, width, approximation)
        inset = units.length(thresholds["text_stroke_inset_pt"])
        penetration_limit = units.length(thresholds["stroke_penetration_pt"])
        for tid in sorted(entries):
            if tid == oid:
                continue
            worst = None
            for index, text_box in enumerate(entries[tid]["boxes"]):
                box = shrink(text_box, inset)
                for band in bands:
                    inter = _intersection(box, band)
                    if inter is None:
                        continue
                    depth = min(inter[2] - inter[0], inter[3] - inter[1])
                    if depth > penetration_limit and (worst is None or depth > worst[0]):
                        worst = (depth, index, box, band)
            if worst:
                depth, index, box, band = worst
                status = "warn" if approximation else "fail"
                checks.append(_check(
                    "text_stroke_intersection", status, [tid, oid],
                    {"penetration": _round(depth), "line_index": index,
                     "text_box": _round_box(box), "band": _round_box(band)},
                    {"max_penetration": _round(penetration_limit),
                     "units": units.kind},
                    ("text intersects a stroke band" if not approximation
                     else "text intersects an approximated stroke band"),
                    approximation=approximation))
    return checks


def _check_container_padding(entries, scene, geometry, thresholds, units):
    checks, uncontained = [], []
    for tid in sorted(entries):
        container = _container(scene, geometry, tid)
        if container is None:
            uncontained.append(tid)
            continue
        box = geometry.bounds(container)
        if box is None:
            uncontained.append(tid)
            continue
        style = geometry.style(container)
        stroke = float(style.get("stroke_width", 1)) / 2 if style.get("stroke", "none") != "none" else 0.0
        inner = shrink(box, stroke)
        text_box = entries[tid]["bbox"]
        fail_tolerance = units.length(thresholds["padding_fail_pt"])
        exit_depth = max(inner[0] - text_box[0], inner[1] - text_box[1],
                         text_box[2] - inner[2], text_box[3] - inner[3], 0.0)
        if exit_depth > fail_tolerance:
            checks.append(_check(
                "text_container_padding", "fail", [tid, container],
                {"exit_depth": _round(exit_depth), "container_inner": _round_box(inner),
                 "text_box": _round_box(text_box)},
                {"max_exit_depth": _round(fail_tolerance), "units": units.kind},
                "text leaves its container", fixable=True))
            continue
        padding = min(text_box[0] - inner[0], text_box[1] - inner[1],
                      inner[2] - text_box[2], inner[3] - text_box[3])
        if padding < units.length(thresholds["padding_min_pt"]):
            checks.append(_check(
                "text_container_padding", "warn", [tid, container],
                {"min_padding": _round(padding)},
                {"min_padding": _round(units.length(thresholds["padding_min_pt"])),
                 "units": units.kind},
                "text sits close to its container edge", fixable=True))
    if uncontained:
        checks.append(_check(
            "text_container_padding", "info", uncontained,
            {"texts_without_container": len(uncontained)}, None,
            "no container object was declared for these texts; containment was not audited",
            not_auditable_reason="no_container"))
    return checks


def _check_canvas_overflow(scene, geometry, thresholds, units):
    checks = []
    view_box = _view_box(scene)
    tolerance = units.length(thresholds["canvas_fail_pt"])
    for obj in scene["objects"]:
        oid = obj["id"]
        if obj["kind"] == "group" or not geometry.visible(oid):
            continue
        box = geometry.visible_bounds(oid)
        if box is None:
            continue
        depth = max(view_box[0] - box[0], view_box[1] - box[1],
                    box[2] - view_box[0] - view_box[2], box[3] - view_box[1] - view_box[3])
        if depth <= 0:
            continue
        status = "fail" if depth > tolerance else "warn"
        checks.append(_check(
            "object_canvas_overflow", status, [oid],
            {"overflow": _round(depth), "visible_bbox": _round_box(box),
             "view_box": _round_box(view_box)},
            {"fail_above": _round(tolerance), "units": units.kind},
            f"object extends {depth:.2f} outside the canvas", fixable=True))
    return checks


def _check_font_size(entries, scene, geometry, thresholds, units, target):
    if not entries:
        return []
    if target is None:
        return [_check(
            "font_glyph_size", "not_auditable", sorted(entries), {},
            {"min_pt": thresholds["font_min_pt"], "max_pt": thresholds["font_max_pt"]},
            "no target output size was declared; physical font size cannot be measured",
            not_auditable_reason="missing_target_output_size")]
    checks, unknown_family = [], []
    for oid in sorted(entries):
        obj = entries[oid]["object"]
        style = geometry.style(oid)
        size = float(style.get("font_size", 14))
        scale = _matrix_scale(geometry.world(oid))
        size_pt = size * scale * units.target["units_to_pt"]
        family = style.get("font_family")
        try:
            check_text(obj["geometry"]["text"], family)
        except ValueError as error:
            message = str(error)
            if message.startswith("needs_input"):
                checks.append(_check(
                    "font_glyph_size", "not_auditable", [oid],
                    {"font_size_pt": _round(size_pt)}, None,
                    "the selected font has no glyphs for this text",
                    not_auditable_reason="missing_glyphs"))
                continue
            unknown_family.append(oid)
            continue
        if size_pt < thresholds["font_min_pt"]:
            checks.append(_check(
                "font_glyph_size", "fail", [oid], {"font_size_pt": _round(size_pt)},
                {"min_pt": thresholds["font_min_pt"]},
                f"effective font size {size_pt:.1f}pt is below {thresholds['font_min_pt']}pt",
                fixable=True))
        elif size_pt > thresholds["font_max_pt"]:
            checks.append(_check(
                "font_glyph_size", "warn", [oid], {"font_size_pt": _round(size_pt)},
                {"max_pt": thresholds["font_max_pt"]},
                f"effective font size {size_pt:.1f}pt is above {thresholds['font_max_pt']}pt"))
    if unknown_family:
        checks.append(_check(
            "font_glyph_size", "not_auditable", unknown_family,
            {"texts": len(unknown_family)}, None,
            "text declares a font family this engine does not verify",
            not_auditable_reason="unverified_font_family"))
    return checks


def _panels(scene, geometry):
    by_parent = {}
    for obj in scene["objects"]:
        if _role(obj) in PANEL_ROLES and geometry.visible(obj["id"]):
            by_parent.setdefault(obj.get("parent"), []).append(obj["id"])
    return by_parent


def _check_panels(scene, geometry, thresholds, units):
    groups = _panels(scene, geometry)
    if not groups:
        return [_check("aligned_panels_and_gutters", "info", [], {"panels": 0}, None,
                       "no objects declare a panel role; alignment was not audited",
                       not_auditable_reason="no_panels")]
    checks = []
    tolerance = units.length(thresholds["align_tolerance_pt"])
    gutter_limit = units.length(thresholds["gutter_tolerance_pt"])
    for parent, ids in sorted(groups.items()):
        if len(ids) < 2:
            continue
        boxes = {oid: geometry.bounds(oid) for oid in ids}
        boxes = {oid: box for oid, box in boxes.items() if box}
        rows = _group_rows(boxes)
        for row in rows:
            if len(row) < 2:
                continue
            ordered = sorted(row, key=lambda oid: boxes[oid][0])
            tops = [boxes[oid][1] for oid in ordered]
            bottoms = [boxes[oid][3] for oid in ordered]
            if max(tops) - min(tops) > tolerance or max(bottoms) - min(bottoms) > tolerance:
                checks.append(_check(
                    "aligned_panels_and_gutters", "warn", ordered,
                    {"top_spread": _round(max(tops) - min(tops)),
                     "bottom_spread": _round(max(bottoms) - min(bottoms))},
                    {"max_spread": _round(tolerance), "units": units.kind},
                    "panels in a row do not share top/bottom edges", fixable=True))
            gaps = [boxes[ordered[i + 1]][0] - boxes[ordered[i]][2] for i in range(len(ordered) - 1)]
            if any(gap < 0 for gap in gaps):
                checks.append(_check(
                    "aligned_panels_and_gutters", "fail", ordered, {"gaps": [_round(g) for g in gaps]},
                    {"min_gap": 0.0},
                    "panels in a row overlap", fixable=True))
                continue
            if gaps:
                gaps_sorted = sorted(gaps)
                median = gaps_sorted[len(gaps_sorted) // 2]
                spread = max(abs(gap - median) for gap in gaps)
                if spread > gutter_limit:
                    checks.append(_check(
                        "aligned_panels_and_gutters", "warn", ordered,
                        {"gaps": [_round(g) for g in gaps], "median_gap": _round(median),
                         "max_deviation": _round(spread)},
                        {"max_gutter_deviation": _round(gutter_limit), "units": units.kind},
                        "panel gutters are uneven", fixable=True))
    return checks


def _group_rows(boxes):
    rows = []
    for oid in sorted(boxes, key=lambda k: (boxes[k][1], boxes[k][0])):
        box = boxes[oid]
        placed = False
        for row in rows:
            reference = boxes[row[0]]
            overlap = min(box[3], reference[3]) - max(box[1], reference[1])
            if overlap > 0 and overlap >= 0.5 * min(box[3] - box[1], reference[3] - reference[1]):
                row.append(oid)
                placed = True
                break
        if not placed:
            rows.append([oid])
    return rows


def _check_connectors(entries, scene, geometry, thresholds, units):
    checks = []
    clearance = units.length(thresholds["connector_clearance_pt"])
    for obj in scene["objects"]:
        oid = obj["id"]
        if obj["kind"] != "connector" or not geometry.visible(oid):
            continue
        try:
            start, end = geometry.endpoints(oid)
        except ValueError:
            checks.append(_check("connector_clearance", "not_auditable", [oid], {}, None,
                                 "connector endpoints cannot be resolved",
                                 not_auditable_reason="unresolved_endpoints"))
            continue
        for tid in sorted(entries):
            if entries[tid]["object"] is obj:
                continue
            label_for = _semantic(entries[tid]["object"]).get("label_for")
            if label_for == oid:
                continue
            worst = None
            for index, text_box in enumerate(entries[tid]["boxes"]):
                distance = segment_rect_distance(start, end, text_box)
                if worst is None or distance < worst[0]:
                    worst = (distance, index)
            if worst is None or worst[0] > clearance:
                continue
            distance, index = worst
            status = "fail" if distance <= 1e-9 else "warn"
            checks.append(_check(
                "connector_clearance", status, [tid, oid],
                {"distance": _round(distance), "line_index": index,
                 "segment": [_round(start[0]), _round(start[1]), _round(end[0]), _round(end[1])]},
                {"min_clearance": _round(clearance), "units": units.kind},
                ("connector passes through text" if status == "fail"
                 else "connector runs close to text")))
    return checks


def _paint_order(scene, geometry):
    """Document order with groups expanded in place: the emitter's paint order."""
    order = {}

    def visit(oid):
        order[oid] = len(order)
        for child in geometry.children.get(oid, []):
            visit(child)

    for obj in scene["objects"]:
        if not obj.get("parent"):
            visit(obj["id"])
    return order


def _background_for(scene, geometry, oid, order):
    for ancestor in _ancestors(geometry, oid):
        fill = geometry.style(ancestor).get("fill", "none")
        if fill != "none":
            return fill, ancestor
    # A sibling painted earlier under the text is its real backdrop; the last
    # one wins. This is an approximation of compositing, not alpha blending.
    center = _cell_center(text_entries_box(geometry, oid))
    backdrop, index = None, -1
    for obj in scene["objects"]:
        other = obj["id"]
        if other == oid or obj["kind"] in {"text", "connector", "group"}:
            continue
        if order.get(other, -1) >= order.get(oid, 0) or not geometry.visible(other):
            continue
        fill = geometry.style(other).get("fill", "none")
        if fill in {"none", "transparent"}:
            continue
        box = geometry.bounds(other)
        if box and _point_in_rect(center, box) and order[other] > index:
            backdrop, index = fill, order[other]
    if backdrop is not None:
        return backdrop, "underlying_object"
    canvas = scene["canvas"].get("background", "none")
    return (canvas if canvas != "none" else "#ffffff"), "canvas"


def _check_contrast(entries, scene, geometry, thresholds, units, request):
    checks, unmeasurable = [], []
    expected = (request or {}).get("color_semantics") or {}
    order = _paint_order(scene, geometry)
    for oid in sorted(entries):
        style = geometry.style(oid)
        fill_text = style.get("fill", "#000000")
        background, source = _background_for(scene, geometry, oid, order)
        foreground = parse_color(fill_text)
        behind = parse_color(background)
        if foreground is None or behind is None:
            unmeasurable.append(oid)
            continue
        if isinstance(expected, dict) and oid in expected:
            want = parse_color(expected[oid])
            if want is None or want != foreground:
                checks.append(_check(
                    "contrast_semantic_color", "fail", [oid],
                    {"expected": expected[oid], "actual": fill_text}, None,
                    "text fill differs from the locked semantic colour"))
                continue
        size_pt = float(style.get("font_size", 14)) * _matrix_scale(geometry.world(oid))
        if units.available:
            size_pt *= units.target["units_to_pt"]
        limit = thresholds["contrast_large"] if (units.available and size_pt >= thresholds["large_text_pt"]) \
            else thresholds["contrast_normal"]
        ratio = contrast_ratio(foreground, behind)
        if ratio < limit:
            checks.append(_check(
                "contrast_semantic_color", "warn", [oid],
                {"contrast": _round(ratio), "fill": fill_text, "background": background,
                 "background_source": source},
                {"min_contrast": limit},
                f"text contrast {ratio:.1f}:1 is below {limit}:1"))
    if unmeasurable:
        checks.append(_check(
            "contrast_semantic_color", "not_auditable", unmeasurable,
            {"texts": len(unmeasurable)}, None,
            "gradient, transparent or unparsed colours cannot be measured",
            not_auditable_reason="unparsed_colour"))
    return checks


def _check_negative_space(scene, geometry, thresholds, units):
    view_box = _view_box(scene)
    boxes = [geometry.visible_bounds(obj["id"]) for obj in scene["objects"]
             if obj["kind"] != "group" and geometry.visible(obj["id"])]
    boxes = [box for box in boxes if box]
    if not boxes:
        return [_check("negative_space_balance", "info", [], {"objects": 0}, None,
                       "no visible objects")]
    content = [min(b[0] for b in boxes), min(b[1] for b in boxes),
               max(b[2] for b in boxes), max(b[3] for b in boxes)]
    ratio = _area(content) / _area(view_box) if _area(view_box) else 0.0
    margins = {
        "left": content[0] - view_box[0], "top": content[1] - view_box[1],
        "right": view_box[0] + view_box[2] - content[2],
        "bottom": view_box[1] + view_box[3] - content[3],
    }
    imbalance = 0.0
    if margins and max(margins.values()) > 0:
        imbalance = (max(margins.values()) - min(margins.values())) / max(margins.values())
    measurement = {"content_bbox_ratio": _round(ratio),
                   "margins": {k: _round(v) for k, v in margins.items()},
                   "margin_imbalance": _round(imbalance)}
    if ratio > thresholds["content_bbox_warn_ratio"]:
        return [_check("negative_space_balance", "warn", [],
                       measurement, {"max_content_ratio": thresholds["content_bbox_warn_ratio"]},
                       "content nearly fills the canvas; check breathing room")]
    if imbalance > thresholds["margin_imbalance_warn"]:
        return [_check("negative_space_balance", "warn", [], measurement,
                       {"max_margin_imbalance": thresholds["margin_imbalance_warn"]},
                       "margins are visibly unbalanced")]
    return [_check("negative_space_balance", "info", [], measurement, None,
                   "content does not crowd the canvas")]


def _check_required(scene, geometry, request):
    required = (request or {}).get("required")
    if not required:
        return [_check("required_structure", "info", [], {},
                       None, "no required objects were declared in the request",
                       not_auditable_reason="no_required_declared")]
    checks = []
    for item in required:
        oid = item.get("id")
        obj = geometry.nodes.get(oid)
        missing = []
        if obj is None or not geometry.visible(oid):
            missing.append("present")
        else:
            if item.get("kind") and obj["kind"] != item["kind"]:
                missing.append("kind")
            if item.get("role") and _role(obj) != item["role"]:
                missing.append("role")
            if item.get("text") and item["text"] not in (obj.get("geometry", {}).get("text") or ""):
                missing.append("text")
        if missing:
            checks.append(_check(
                "required_structure", "fail", [oid] if oid else [],
                {"missing": missing}, None,
                "required object is missing or does not match the request", fixable=False))
    return checks


# --------------------------------------------------------------- exemptions ---

def validate_request(request):
    if request is None:
        return {}
    if not isinstance(request, dict):
        raise ValueError("qa request must be an object")
    from .scene import schema_validate

    schema_validate(request, "scene-qa-request")
    unknown = set(request.get("thresholds") or {}) - set(DEFAULT_THRESHOLDS)
    if unknown:
        raise ValueError(f"unknown thresholds: {sorted(unknown)}")
    for entry in request.get("exemptions") or []:
        if not entry["reason"].strip():
            raise ValueError("exemption requires a non-empty reason")
        if entry["check"] not in CHECKS:
            raise ValueError(f"unknown exempted check {entry['check']!r}")
        if entry.get("allow_overlap") and entry["check"] not in OVERLAP_CHECKS:
            raise ValueError("allow_overlap is only valid for overlap checks")
    return request


def _exemption_matches(entry, check, geometry):
    if entry.get("check") != check["kind"] and entry.get("check") != check["id"]:
        return False
    objects = entry.get("objects") or []
    if objects and not set(objects).issubset(set(check["objects"])):
        return False
    if entry.get("role"):
        if not check["objects"]:
            return False
        for oid in check["objects"]:
            if _role(geometry.nodes[oid]) != entry["role"]:
                return False
    if entry.get("parent"):
        parent = entry["parent"]
        for oid in check["objects"]:
            if parent not in set(_ancestors(geometry, oid)):
                return False
    if entry.get("allow_overlap") and check["kind"] not in OVERLAP_CHECKS:
        return False
    return True


def _apply_exemptions(checks, exemptions, geometry):
    applied = []
    for index, entry in enumerate(exemptions or []):
        identifier = entry.get("id") or f"ex-{index + 1}"
        hits = 0
        for check in checks:
            if check["status"] in {"not_auditable", "info"}:
                continue
            if _exemption_matches(entry, check, geometry):
                check["status"] = "exempted"
                check["severity"] = "info"
                check["exemptions"].append({"id": identifier, "reason": entry["reason"]})
                hits += 1
        applied.append({"id": identifier, "applied": bool(hits), "check": entry.get("check"),
                        "objects": entry.get("objects") or [], "reason": entry["reason"]})
        if not hits:
            checks.append(_check(
                "stale_exemption", "info", [], {"exemption": identifier}, None,
                "this exemption matched no finding; it may be stale"))
    return checks, applied


# -------------------------------------------------------------------- audit ---

def _overall(checks):
    fail = any(c["status"] == "fail" for c in checks)
    warn = any(c["status"] == "warn" for c in checks)
    blocked = any(c["status"] == "not_auditable" for c in checks)
    if fail:
        return "failed"
    if warn or blocked:
        return "needs_review"
    return "clear"


def audit_scene(scene, *, request=None):
    """Audit one Scene dict and return a deterministic report."""
    from .scene import validate

    started = time.perf_counter()
    validate(scene)
    request = validate_request(request)
    thresholds = {**DEFAULT_THRESHOLDS, **(request.get("thresholds") or {})}
    target = resolve_target(scene, request)
    units = _Units(target)
    geometry = Geometry(scene)
    entries = _text_entries(scene, geometry)

    checks = []
    checks += _check_text_overlap(entries, thresholds, units)
    checks += _check_text_stroke(entries, scene, geometry, thresholds, units)
    checks += _check_container_padding(entries, scene, geometry, thresholds, units)
    checks += _check_canvas_overflow(scene, geometry, thresholds, units)
    checks += _check_font_size(entries, scene, geometry, thresholds, units, target)
    checks += _check_panels(scene, geometry, thresholds, units)
    checks += _check_connectors(entries, scene, geometry, thresholds, units)
    checks += _check_contrast(entries, scene, geometry, thresholds, units, request)
    checks += _check_negative_space(scene, geometry, thresholds, units)
    checks += _check_required(scene, geometry, request)

    exemptions = request.get("exemptions") or []
    checks, applied = _apply_exemptions(checks, exemptions, geometry)
    checks = sorted(_dedupe(checks), key=lambda c: c["id"])

    summary = {"fail": 0, "warn": 0, "info": 0, "exempted": 0, "not_auditable": 0, "clear": 0}
    for check in checks:
        summary[check["status"]] = summary.get(check["status"], 0) + 1
    status = _overall(checks)

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "scene_qa",
        "status": status,
        "scene_revision": scene.get("revision", 0),
        "canvas": {key: scene["canvas"].get(key) for key in ("width", "height", "viewBox")
                   if key in scene["canvas"]},
        "target": target or {"auditable": False},
        "thresholds": thresholds,
        "units": units.kind,
        "summary": summary,
        "checks": checks,
        "exemptions": applied,
        "limitations": list(LIMITATIONS),
        "scope": "mechanical layout geometry only; scientific meaning and aesthetics need host review",
        "elapsed_ms": _round((time.perf_counter() - started) * 1000),
    }


def load_request(path):
    if path is None:
        return {}
    from .scene import load_json

    request = load_json(path)
    validate_request(request)
    return request


def write_json(document, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    return {"path": str(path.resolve()), "bytes": path.stat().st_size}


def failures(report):
    return [check["id"] for check in report["checks"] if check["status"] == "fail"]

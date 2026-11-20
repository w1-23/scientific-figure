"""Bounded, deterministic repairs for layout audit findings.

Only four low-risk operations are attempted: wrapping text to its container,
nudging a label off a connector, equalising declared panel gutters, and moving
a layout object back inside the canvas. Every candidate is applied in memory,
re-audited, and kept only when the target finding disappears and no new fail
appears. Anything else stays as a report for the host.

Candidates are never written over the source project: the fixed Scene is
returned and the caller decides whether to write it to a new directory.
"""
from __future__ import annotations

import copy
import math

from .editing import apply_patch
from .fonts import text_line_boxes
from .geometry import Geometry, inverse, point, transform_box
from .layout_audit import audit_scene, shrink
from .text_tools import wrap_text

LAYOUT_ROLES = {"panel", "card", "frame", "container", "node", "label", "text",
                "title", "annotation", "caption", "legend", "axis", "tick"}

MAX_SHIFT = 12.0
MARGIN = 2.0


def _semantic(obj):
    value = obj.get("semantic")
    return value if isinstance(value, dict) else {}


def _role(obj):
    value = _semantic(obj).get("role")
    return value if isinstance(value, str) else None


def _protected(obj):
    semantic = _semantic(obj)
    if semantic.get("protected") or semantic.get("locked"):
        return True
    role = _role(obj)
    return role is not None and role not in LAYOUT_ROLES


def _local_delta(scene, oid, dx, dy):
    """Convert a world-space translation into the object's parent space."""
    geometry = Geometry(scene)
    parent = geometry.nodes[oid].get("parent")
    matrix = inverse(geometry.world(parent)) if parent else [1, 0, 0, 1, 0, 0]
    return point([matrix[0], matrix[1], matrix[2], matrix[3], 0, 0], dx, dy)


def _move_operation(scene, oid, dx, dy):
    geometry = Geometry(scene)
    obj = geometry.nodes[oid]
    if obj["kind"] == "connector" or obj["kind"] == "group":
        matrix = obj.get("transform", [1, 0, 0, 1, 0, 0])
        ldx, ldy = _local_delta(scene, oid, dx, dy)
        return {"op": "set", "id": oid,
                "set": {"transform": [matrix[0], matrix[1], matrix[2], matrix[3],
                                      matrix[4] + ldx, matrix[5] + ldy]}}
    if obj["kind"] == "text":
        ldx, ldy = _local_delta(scene, oid, dx, dy)
        g = obj["geometry"]
        return {"op": "set", "id": oid,
                "set": {"geometry": {"x": g["x"] + ldx, "y": g["y"] + ldy}}}
    # rect / ellipse / path: translate through the transform to keep geometry stable.
    matrix = obj.get("transform", [1, 0, 0, 1, 0, 0])
    ldx, ldy = _local_delta(scene, oid, dx, dy)
    return {"op": "set", "id": oid,
            "set": {"transform": [matrix[0], matrix[1], matrix[2], matrix[3],
                                  matrix[4] + ldx, matrix[5] + ldy]}}


def _patch(scene, operations):
    return {"schema_version": "0.3", "base_revision": scene["revision"],
            "operations": operations}


def _text_wrap(scene, geometry, check):
    oid = check["objects"][0]
    container = check["objects"][1]
    obj = geometry.nodes[oid]
    if obj["kind"] != "text" or _protected(obj):
        return None
    box = geometry.bounds(container)
    if box is None:
        return None
    style = geometry.style(container)
    stroke = float(style.get("stroke_width", 1)) / 2 if style.get("stroke", "none") != "none" else 0.0
    inner = (box[2] - box[0]) - 2 * stroke - 2 * MARGIN
    if inner <= 0:
        return None
    box_x, box_y = obj["geometry"]["x"], obj["geometry"]["y"]
    text_style = geometry.style(oid)
    proposal = wrap_text(obj["geometry"]["text"], inner,
                         font_size=text_style.get("font_size", 14),
                         font_family=text_style.get("font_family"))
    wrapped = proposal["text"]
    if wrapped == obj["geometry"]["text"]:
        return None
    operations = [{"op": "set", "id": oid, "set": {"geometry": {"text": wrapped}}}]
    # Wrapped lines grow downward from the baseline; keep the block inside the
    # container by shifting it, never by shrinking the font.
    inner_box = shrink(box, stroke + MARGIN / 2)
    boxes = [transform_box(line, geometry.world(oid))
             for line in text_line_boxes({"x": box_x, "y": box_y, "text": wrapped}, text_style)]
    left, top = min(b[0] for b in boxes), min(b[1] for b in boxes)
    right, bottom = max(b[2] for b in boxes), max(b[3] for b in boxes)
    dx = dy = 0.0
    if right > inner_box[2]:
        dx = inner_box[2] - right
    elif left < inner_box[0]:
        dx = inner_box[0] - left
    if bottom > inner_box[3]:
        dy = inner_box[3] - bottom
    elif top < inner_box[1]:
        dy = inner_box[1] - top
    if dx or dy:
        ldx, ldy = _local_delta(scene, oid, dx, dy)
        operations.append({"op": "set", "id": oid,
                           "set": {"geometry": {"x": box_x + ldx, "y": box_y + ldy}}})
    return operations if len(operations) > 1 else operations[0]


def _canvas_shift(scene, geometry, check):
    oid = check["objects"][0]
    obj = geometry.nodes[oid]
    if obj["kind"] in {"connector", "group"} or _protected(obj):
        return None
    if obj["kind"] != "text" and _role(obj) is None:
        return None
    box = geometry.visible_bounds(oid)
    if box is None:
        return None
    canvas = scene["canvas"]
    view = canvas.get("viewBox") or [0, 0, canvas["width"], canvas["height"]]
    dx = dy = 0.0
    if box[0] < view[0] + MARGIN:
        dx = view[0] + MARGIN - box[0]
    elif box[2] > view[0] + view[2] - MARGIN:
        dx = view[0] + view[2] - MARGIN - box[2]
    if box[1] < view[1] + MARGIN:
        dy = view[1] + MARGIN - box[1]
    elif box[3] > view[1] + view[3] - MARGIN:
        dy = view[1] + view[3] - MARGIN - box[3]
    if dx == 0 and dy == 0:
        return None
    return _move_operation(scene, oid, dx, dy)


def _connector_candidates(scene, geometry, check):
    """Shortest first, both normal directions: moving a label off its line."""
    text_id, connector_id = check["objects"][0], check["objects"][1]
    obj = geometry.nodes[text_id]
    if obj["kind"] != "text" or _protected(obj):
        return []
    try:
        start, end = geometry.endpoints(connector_id)
    except ValueError:
        return []
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length == 0:
        return []
    normal = (-dy / length, dx / length)
    candidates = []
    for step in (MAX_SHIFT / 4, MAX_SHIFT / 2, MAX_SHIFT):
        for sign in (1, -1):
            candidates.append(_move_operation(scene, text_id, normal[0] * step * sign,
                                              normal[1] * step * sign))
    return candidates


def _panel_shift(scene, geometry, check):
    ids = [oid for oid in check["objects"] if oid in geometry.nodes]
    if len(ids) < 2 or any(_protected(geometry.nodes[oid]) for oid in ids):
        return None
    boxes = {oid: geometry.bounds(oid) for oid in ids}
    if any(box is None for box in boxes.values()):
        return None
    ordered = sorted(ids, key=lambda oid: boxes[oid][0])
    gaps = [boxes[ordered[i + 1]][0] - boxes[ordered[i]][2] for i in range(len(ordered) - 1)]
    if not gaps:
        return None
    gaps_sorted = sorted(gaps)
    median = gaps_sorted[len(gaps_sorted) // 2]
    target_top = sorted(boxes[oid][1] for oid in ordered)[len(ordered) // 2]
    ops, cursor = [], None
    for oid in ordered:
        box = boxes[oid]
        dx = 0.0 if cursor is None else cursor - box[0]
        dy = target_top - box[1]
        ops.append(_move_operation(scene, oid, dx, dy))
        cursor = box[2] + dx + median
    return {"op": "multi", "operations": ops}


PRIORITY = {"text_container_padding": 0, "text_text_overlap": 1,
            "text_stroke_intersection": 2, "connector_clearance": 3,
            "aligned_panels_and_gutters": 4, "object_canvas_overflow": 5}


def _proposals(scene, report):
    geometry = Geometry(scene)
    proposals = []
    checks = sorted(report["checks"],
                    key=lambda item: (PRIORITY.get(item["kind"], 9), item["id"]))
    for check in checks:
        if check["status"] not in {"fail", "warn"}:
            continue
        kind = check["kind"]
        operation = None
        try:
            if kind == "text_container_padding" and len(check["objects"]) == 2:
                operation = _text_wrap(scene, geometry, check)
            elif kind == "object_canvas_overflow" and len(check["objects"]) == 1:
                operation = _canvas_shift(scene, geometry, check)
            elif kind == "connector_clearance" and len(check["objects"]) == 2:
                for candidate in _connector_candidates(scene, geometry, check):
                    proposals.append({"issue_id": check["id"], "kind": kind,
                                      "objects": list(check["objects"]),
                                      "operation": candidate})
                continue
            elif kind == "aligned_panels_and_gutters":
                operation = _panel_shift(scene, geometry, check)
        except (ValueError, KeyError):
            operation = None
        if operation is None:
            continue
        if isinstance(operation, list):
            proposals.append({"issue_id": check["id"], "kind": kind,
                              "objects": list(check["objects"]),
                              "operation": {"op": "batch", "operations": operation}})
        elif operation.get("op") == "multi":
            proposals.append({"issue_id": check["id"], "kind": kind,
                              "objects": list(check["objects"]),
                              "operation": {"op": "batch", "operations": operation["operations"]}})
        else:
            proposals.append({"issue_id": check["id"], "kind": kind,
                              "objects": list(check["objects"]), "operation": operation})
    return proposals


def _expand(operation):
    if operation.get("op") == "batch":
        return operation["operations"]
    return [operation]


def _statuses(report):
    return {check["id"]: check["status"] for check in report["checks"]}


def _audit(scene, request):
    return audit_scene(scene, request=request)


def fix_scene(scene, report, *, request=None, max_rounds=3, max_candidates=64):
    """Return a fixed copy of the Scene plus per-fix records."""
    if not isinstance(max_rounds, int) or isinstance(max_rounds, bool) or not 1 <= max_rounds <= 5:
        raise ValueError("max_rounds must be 1..5")
    working = copy.deepcopy(scene)
    current = report
    applied, rejected, skipped = [], [], []
    budget = max_candidates
    rounds = 0
    attempted = set()
    for round_index in range(max_rounds):
        progress = False
        while budget > 0:
            pending = [p for p in _proposals(working, current) if p["issue_id"] not in attempted]
            if not pending:
                break
            issue = pending[0]["issue_id"]
            group = [p for p in pending if p["issue_id"] == issue]
            accepted = False
            for proposal in group:
                if budget <= 0:
                    break
                budget -= 1
                before = _statuses(current)
                try:
                    candidate = apply_patch(working, _patch(working, _expand(proposal["operation"])))
                except (ValueError, KeyError) as error:
                    rejected.append({**proposal, "status": "rejected", "round": round_index + 1,
                                     "reason": f"invalid candidate: {error}"})
                    continue
                trial = _audit(candidate, request)
                after = _statuses(trial)
                new_fails = {cid for cid, status in after.items() if status == "fail"} - \
                            {cid for cid, status in before.items() if status == "fail"}
                target_left = any(cid.startswith(issue) and status in {"fail", "warn"}
                                  for cid, status in after.items())
                if new_fails or target_left:
                    rejected.append({**proposal, "status": "rejected", "round": round_index + 1,
                                     "reason": "new findings" if new_fails else "finding remains"})
                    continue
                working, current = candidate, trial
                applied.append({**proposal, "status": "accepted", "round": round_index + 1})
                accepted = True
                progress = True
                break
            attempted.add(issue)
            if accepted:
                continue
        rounds += 1
        if not progress:
            break
    for proposal in _proposals(working, current):
        if proposal["issue_id"] not in {item["issue_id"] for item in applied + rejected}:
            skipped.append({**proposal, "status": "skipped", "reason": "budget or round limit"})
    unchanged = not applied
    status = ("no_changes" if unchanged and not (rejected or skipped)
              else "needs_review" if unchanged
              else "fixed" if current["status"] == "clear" else "partially_fixed")
    return {
        "status": status,
        "scene": working,
        "report_before": report,
        "report_after": current,
        "applied": applied,
        "rejected": rejected,
        "skipped": skipped,
        "rounds": rounds,
        "budget_exhausted": budget <= 0,
    }

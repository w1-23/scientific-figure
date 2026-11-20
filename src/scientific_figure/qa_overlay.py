"""Diagnostic SVG overlay for a layout audit report.

The overlay is a separate artifact: it re-emits the Scene through the normal
SVG backend and appends one `<g id="qa-overlay">` group with boxes, labels and
a per-finding `<title>`. The Scene itself is never modified, and overlay text
is never fed back into an audit.
"""
from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path

from .geometry import Geometry
from .svg import to_svg

NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", NS)

COLORS = {"fail": "#d64545", "warn": "#d97706", "not_auditable": "#6b7280",
          "exempted": "#1f5f8b", "info": "#8a94a6", "clear": "#2f855a"}
PREFIX = {"fail": "F", "warn": "W", "not_auditable": "N", "exempted": "E",
          "info": "I", "clear": "C"}


def _element(tag, attributes):
    return ET.Element(f"{{{NS}}}{tag}",
                      {key: _fmt(value) if isinstance(value, float) else str(value)
                       for key, value in attributes.items()})


def _fmt(value):
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _text(parent, x, y, content, *, fill, size, weight="normal"):
    node = _element("text", {
        "x": float(x), "y": float(y), "fill": fill,
        "font-size": size, "font-family": "system-ui, sans-serif",
        "font-weight": weight, "paint-order": "stroke",
        "stroke": "#ffffff", "stroke-width": size * 0.22,
    })
    node.text = content
    parent.append(node)
    return node


def _box(parent, rect, color, width, *, dashed=True):
    attributes = {"x": float(rect[0]), "y": float(rect[1]),
                  "width": float(rect[2] - rect[0]), "height": float(rect[3] - rect[1]),
                  "fill": "none", "stroke": color, "stroke-width": width}
    if dashed:
        attributes["stroke-dasharray"] = f"{_fmt(width * 5)} {_fmt(width * 3)}"
    parent.append(_element("rect", attributes))


def overlay_svg(scene, report):
    """Return the Scene SVG plus one deterministic diagnostic group."""
    root = ET.fromstring(to_svg(scene))
    canvas = scene["canvas"]
    width = float(canvas.get("viewBox", [0, 0, canvas["width"]])[2] or canvas["width"])
    unit = max(0.6, width / 1200)
    font = max(8.0, width / 90)
    group = _element("g", {"id": "qa-overlay", "pointer-events": "none"})
    group.set("data-status", report["status"])
    geometry = Geometry(scene)

    draws = [check for check in report["checks"]
             if check["status"] in {"fail", "warn", "not_auditable", "exempted"}]
    for check in sorted(draws, key=lambda item: item["id"]):
        color = COLORS[check["status"]]
        status_group = _element("g", {"data-check": check["id"], "data-status": check["status"]})
        title = ET.SubElement(status_group, f"{{{NS}}}title")
        title.text = f"{check['kind']} [{check['status']}] {', '.join(check['objects'])}: {check['message']}"
        drew = False
        for oid in check["objects"]:
            if oid not in geometry.nodes:
                continue
            box = geometry.visible_bounds(oid) if geometry.nodes[oid]["kind"] != "group" \
                else geometry.bounds(oid)
            if box is None:
                continue
            _box(status_group, box, color, unit)
            if not drew:
                _text(status_group, box[0], max(box[1] - unit * 4, font),
                      f"{PREFIX[check['status']]}: {check['kind']}", fill=color, size=font,
                      weight="600")
                drew = True
        for key in ("text_box", "band", "container_inner", "visible_bbox", "segment"):
            value = check["measurement"].get(key) if isinstance(check["measurement"], dict) else None
            if not isinstance(value, list) or len(value) != 4:
                continue
            if key == "segment":
                line = _element("line", {"x1": float(value[0]), "y1": float(value[1]),
                                         "x2": float(value[2]), "y2": float(value[3]),
                                         "stroke": color, "stroke-width": unit * 1.5})
                status_group.append(line)
            else:
                _box(status_group, value, color, unit * 0.8)
        boxes = check["measurement"].get("boxes") if isinstance(check["measurement"], dict) else None
        if isinstance(boxes, dict):
            for value in boxes.values():
                if isinstance(value, list) and len(value) == 4:
                    _box(status_group, value, color, unit * 0.8)
        if len(status_group):
            group.append(status_group)
    root.append(group)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")


def write_overlay(scene, report, path):
    xml = overlay_svg(scene, report)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(xml, encoding="utf-8")
    return {"path": str(path.resolve()), "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "status": report["status"]}

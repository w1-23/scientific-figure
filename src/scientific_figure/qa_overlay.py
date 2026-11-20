"""Diagnostic SVG overlay for a layout audit report.

The overlay is a separate artifact: it re-emits the Scene through the normal
SVG backend and appends one `<g id="qa-overlay">` group with boxes, labels and
a per-finding `<title>`. It deliberately stays inside the engine's printable
SVG subset (no scripts, CSS, external references or data attributes), so the
result renders with `figure-cli render` and stays readable in a browser. The
Scene itself is never modified, and overlay text is never fed back into an
audit.
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


def _title(parent, content):
    node = ET.SubElement(parent, f"{{{NS}}}title")
    node.text = content
    return node


def _text(parent, x, y, content, *, fill, size):
    """Coloured text over a white halo drawn as a separate copy (no paint-order)."""
    halo = _element("text", {
        "x": float(x), "y": float(y), "fill": "#ffffff",
        "font-size": size, "stroke": "#ffffff", "stroke-width": size * 0.4,
    })
    halo.text = content
    parent.append(halo)
    node = _element("text", {"x": float(x), "y": float(y), "fill": fill,
                             "font-size": size})
    node.text = content
    parent.append(node)
    return node


def _label_position(box, content, size, view, unit):
    """Keep a label inside the canvas even when its box is partly outside."""
    estimate = 0.62 * size * len(content)
    x = min(max(box[0], view[0] + unit * 2),
            view[0] + view[2] - estimate - unit * 2)
    y = box[1] - unit * 4
    if y < view[1] + size:
        y = min(box[3] + size * 1.3, view[1] + view[3] - unit * 2)
    return x, max(y, view[1] + size)


def _box(parent, rect, color, width):
    parent.append(_element("rect", {
        "x": float(rect[0]), "y": float(rect[1]),
        "width": float(rect[2] - rect[0]), "height": float(rect[3] - rect[1]),
        "fill": "none", "stroke": color, "stroke-width": width,
        "stroke-dasharray": f"{_fmt(width * 5)} {_fmt(width * 3)}",
    }))


def _segment(parent, line, color, width):
    parent.append(_element("path", {
        "d": f"M {_fmt(float(line[0]))} {_fmt(float(line[1]))} "
             f"L {_fmt(float(line[2]))} {_fmt(float(line[3]))}",
        "fill": "none", "stroke": color, "stroke-width": width,
    }))


def overlay_svg(scene, report):
    """Return the Scene SVG plus one deterministic diagnostic group."""
    root = ET.fromstring(to_svg(scene))
    canvas = scene["canvas"]
    width = float(canvas.get("viewBox", [0, 0, canvas["width"]])[2] or canvas["width"])
    unit = max(0.6, width / 1200)
    font = max(8.0, width / 90)
    view = canvas.get("viewBox") or [0, 0, canvas["width"], canvas["height"]]
    group = _element("g", {"id": "qa-overlay"})
    _title(group, f"layout audit: {report['status']} "
                  f"(fail {report['summary']['fail']}, warn {report['summary']['warn']}, "
                  f"not auditable {report['summary']['not_auditable']})")

    geometry = Geometry(scene)
    draws = [check for check in report["checks"]
             if check["status"] in {"fail", "warn", "not_auditable", "exempted"}]
    for check in sorted(draws, key=lambda item: item["id"]):
        color = COLORS[check["status"]]
        status_group = _element("g", {})
        _title(status_group, f"{check['kind']} [{check['status']}] "
                             f"{', '.join(check['objects'])}: {check['message']}")
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
                label = f"{PREFIX[check['status']]}: {check['kind']}"
                x, y = _label_position(box, label, font, view, unit)
                _text(status_group, x, y, label, fill=color, size=font)
                drew = True
        for key in ("text_box", "band", "container_inner", "visible_bbox"):
            value = check["measurement"].get(key) if isinstance(check["measurement"], dict) else None
            if isinstance(value, list) and len(value) == 4:
                _box(status_group, value, color, unit * 0.8)
        segment = check["measurement"].get("segment") if isinstance(check["measurement"], dict) else None
        if isinstance(segment, list) and len(segment) == 4:
            _segment(status_group, segment, color, unit * 1.5)
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

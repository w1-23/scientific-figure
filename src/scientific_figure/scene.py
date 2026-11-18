"""Validation and derived inventory; no implicit object identity generation."""
from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from importlib.resources import files
from pathlib import Path

from jsonschema import Draft202012Validator
from .fonts import check_text
from .geometry import Geometry, local_bounds, IDENTITY

ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,99}$")
COLOR = re.compile(r"^(#[0-9a-fA-F]{3}|#[0-9a-fA-F]{6}|#[0-9a-fA-F]{8}|none)$")
REF = re.compile(r"^url\(#([A-Za-z_][A-Za-z0-9_.-]*)\)$")
STYLE = {"fill", "stroke", "stroke_width", "opacity", "fill_opacity", "stroke_opacity",
         "fill_rule", "font_size", "font_family", "anchor", "arrow_end"}
STYLE_V03 = STYLE | {"stroke_dasharray", "stroke_dashoffset"}
EFFECT_VERSIONS = {"0.2", "0.3"}


def load_json(path):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError(f"duplicate JSON key: {key}")
            out[key] = value
        return out
    data = Path(path).read_text(encoding="utf-8")
    if len(data) > 20_000_000:
        raise ValueError("JSON input exceeds 20 MB")
    return json.loads(data, object_pairs_hook=unique,
                      parse_constant=lambda v: (_ for _ in ()).throw(ValueError(f"nonfinite value {v}")))


@lru_cache(maxsize=64)
def _validator(name):
    schema = json.loads(files("scientific_figure").joinpath("schemas", name + ".schema.json").read_text())
    return Draft202012Validator(schema)


def schema_validate(data, name):
    errors = sorted(_validator(name).iter_errors(data), key=lambda e: str(e.json_path))
    if errors:
        raise ValueError(f"{errors[0].json_path}: {errors[0].message}")
    finite(data)


def finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("nonfinite number")
    if isinstance(value, dict):
        for item in value.values(): finite(item)
    elif isinstance(value, list):
        for item in value: finite(item)


def number(value, label, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    if abs(value) > 10_000_000 or (positive and value <= 0):
        raise ValueError(f"{label} outside supported range")


def _gradient(resource, radial):
    allowed = {"id", "kind", "center", "radius", "focus", "units", "stops"} if radial else {"id", "kind", "start", "end", "stops"}
    if set(resource) - allowed:
        raise ValueError("unsupported gradient fields")
    for field in (("center",) if radial else ("start", "end")):
        coords = resource.get(field, [])
        if not isinstance(coords, (list, tuple)) or len(coords) != 2:
            raise ValueError("gradient coordinates require two numbers")
        for value in coords: number(value, "gradient coordinate")
    if radial:
        number(resource.get("radius"), "gradient radius", positive=True)
        if resource.get("units", "objectBoundingBox") not in {"objectBoundingBox", "userSpaceOnUse"}:
            raise ValueError("unsupported gradient units")
        focus = resource.get("focus", resource["center"])
        if not isinstance(focus, list) or len(focus) != 2:
            raise ValueError("gradient focus requires two numbers")
        for value in focus: number(value, "gradient focus")
        if math.hypot(focus[0] - resource["center"][0], focus[1] - resource["center"][1]) >= resource["radius"]:
            raise ValueError("gradient focus must be strictly inside its circle")
    stops = resource.get("stops", [])
    if not isinstance(stops, (list, tuple)) or len(stops) < 2:
        raise ValueError("gradient needs at least two stops")
    if radial and len(stops) > 256:
        raise ValueError("radial gradient exceeds 256 stops")
    previous = -1
    for stop in stops:
        if not isinstance(stop, (list, tuple)) or len(stop) != 2:
            raise ValueError("invalid gradient stop")
        number(stop[0], "stop offset")
        if (not 0 <= stop[0] <= 1 or stop[0] < previous or not isinstance(stop[1], str)
                or not COLOR.fullmatch(stop[1]) or stop[1] == "none"):
            raise ValueError("invalid gradient stop")
        previous = stop[0]


def validate(scene):
    version = scene.get("schema_version")
    schema_validate(scene, {"0.2": "scene-0.2", "0.3": "scene-0.3"}.get(version, "scene"))
    canvas = scene["canvas"]
    if not COLOR.fullmatch(canvas.get("background", "none")):
        raise ValueError("invalid canvas background")
    if "viewBox" in canvas:
        for v in canvas["viewBox"]: number(v, "viewBox")
        if min(canvas["viewBox"][2:]) <= 0: raise ValueError("invalid viewBox")
    ids, resource_ids = set(), set()
    for resource in scene.get("resources", []):
        rid = resource.get("id", "")
        if not isinstance(rid, str) or not ID.fullmatch(rid) or rid.startswith("sf-") or rid in resource_ids:
            raise ValueError("invalid or duplicate resource ID")
        resource_ids.add(rid)
        if version in EFFECT_VERSIONS and resource.get("kind") in {"clip_path", "alpha_mask"}:
            from .scene_effects import validate_resource
            validate_resource(resource)
        elif resource.get("kind") == "linear_gradient":
            _gradient(resource, False)
        elif version == "0.3" and resource.get("kind") == "radial_gradient":
            _gradient(resource, True)
        else:
            raise ValueError("unsupported resource kind or fields")
    paint_ids = {r["id"] for r in scene.get("resources", []) if r["kind"] in {"linear_gradient", "radial_gradient"}}
    nodes = {}
    for obj in scene["objects"]:
        oid = obj["id"]
        if oid in ids or oid in resource_ids or oid.startswith("sf-"):
            raise ValueError(f"duplicate or reserved ID: {oid}")
        ids.add(oid); nodes[oid] = obj
        matrix = obj.get("transform", IDENTITY)
        for v in matrix: number(v, "transform")
        if abs(matrix[0] * matrix[3] - matrix[1] * matrix[2]) < 1e-10:
            raise ValueError(f"singular transform: {oid}")
        if "anchors" in obj:
            if obj["kind"] == "connector":
                raise ValueError("connector cannot own persistent ports")
            for coords in obj["anchors"].values():
                for value in coords: number(value, "port coordinate")
        style = obj.get("style", {})
        allowed = STYLE_V03 if version == "0.3" else STYLE
        if set(style) - allowed: raise ValueError(f"unsupported style fields on {oid}: {set(style) - allowed}")
        for key, value in style.items():
            if key in {"fill", "stroke"}:
                ref = REF.fullmatch(value) if isinstance(value, str) else None
                if not isinstance(value, str) or not (COLOR.fullmatch(value) or ref and ref[1] in paint_ids):
                    raise ValueError(f"invalid paint on {oid}")
            elif key in {"opacity", "fill_opacity", "stroke_opacity"}:
                number(value, key)
                if not 0 <= value <= 1: raise ValueError(f"invalid {key}")
            elif key in {"font_size", "stroke_width"}:
                number(value, key, positive=True)
            elif key == "stroke_dashoffset": number(value, key)
            elif key == "stroke_dasharray":
                if not isinstance(value, list) or not 1 <= len(value) <= 32:
                    raise ValueError("dash array needs 1..32 entries")
                for length in value:
                    number(length, "dash length")
                    if length < 0: raise ValueError("negative dash length")
                if not any(value): raise ValueError("dash array cannot be all zero")
            elif key == "fill_rule" and value not in {"nonzero", "evenodd"}: raise ValueError("invalid fill rule")
            elif key == "anchor" and value not in {"start", "middle", "end"}: raise ValueError("invalid text anchor")
            elif key == "arrow_end" and not isinstance(value, bool): raise ValueError("arrow_end must be boolean")
            elif key == "font_family" and not isinstance(value, str): raise ValueError("font family must be string")
        g, kind = obj.get("geometry", {}), obj["kind"]
        required = {"group": set(), "rect": {"x", "y", "width", "height"},
                    "ellipse": {"cx", "cy", "rx", "ry"}, "path": {"d"},
                    "text": {"x", "y", "text"}, "connector": {"from", "to"}}[kind]
        optional = {"rect": {"rx"}, "connector": {"from_anchor", "to_anchor"}}.get(kind, set())
        if required - set(g) or set(g) - required - optional: raise ValueError(f"invalid {kind} geometry on {oid}")
        for key, value in g.items():
            if key in {"x", "y", "cx", "cy", "width", "height", "rx", "ry"}:
                number(value, key, positive=key in {"width", "height", "rx", "ry"})
        if kind == "text":
            if not isinstance(g["text"], str) or len(g["text"]) > 10000: raise ValueError("invalid text")
        if kind == "path":
            if not isinstance(g["d"], str) or len(g["d"]) > 200_000 or re.search(r"[^MmZzLlHhVvCcSsQqTtAa0-9eE+.,\s-]", g["d"]):
                raise ValueError(f"invalid path syntax on {oid}")
            box = local_bounds(obj, style)
            if box is None: raise ValueError(f"empty path {oid}")
            for v in box: number(v, "path bounds")
    dependencies = {oid: [] for oid in ids}
    for oid, obj in nodes.items():
        parent = obj.get("parent")
        if parent:
            if parent not in nodes or nodes[parent]["kind"] != "group": raise ValueError(f"invalid parent of {oid}")
            dependencies[parent].append(oid)
        if obj["kind"] == "connector":
            g = obj["geometry"]
            for key in ["from", "to"]:
                target = g[key]
                if target not in nodes or nodes[target]["kind"] == "connector": raise ValueError("invalid connector target")
                anchor = g.get(key + "_anchor", "right" if key == "from" else "left")
                if version == "0.3" and isinstance(anchor, str) and anchor.startswith("port:"):
                    if anchor[5:] not in nodes[target].get("anchors", {}): raise ValueError("unknown persistent port")
                else:
                    if anchor not in {"left", "right", "top", "bottom", "center"}: raise ValueError("invalid anchor")
                    dependencies[oid].append(target)
    visited, active = set(), set()
    def visit(oid, depth=0):
        if depth > 100: raise ValueError("object hierarchy exceeds 100 levels")
        if oid in active: raise ValueError("cyclic scene or connector dependency")
        if oid in visited: return
        active.add(oid)
        for dep in dependencies[oid]: visit(dep, depth + 1)
        active.remove(oid); visited.add(oid)
    for oid in ids: visit(oid)
    geometry = Geometry(scene)
    for oid, obj in nodes.items():
        if obj["kind"] == "text": check_text(obj["geometry"]["text"], geometry.style(oid).get("font_family"))
        geometry.bounds(oid)
    if version in EFFECT_VERSIONS:
        from .scene_effects import validate_effects
        validate_effects(scene)
    return scene


def manifest(scene):
    validate(scene)
    geometry = Geometry(scene)
    return {"schema_version": "0.1", "revision": scene["revision"], "canvas": scene["canvas"],
            "objects": [{"id": o["id"], "svg_id": o["id"], "kind": o["kind"], "parent": o.get("parent"),
                         "semantic": o.get("semantic", {}), "bbox": geometry.bounds(o["id"]),
                         **({"visible_bbox": geometry.visible_bounds(o["id"]), "visible_bbox_scope": "conservative_not_pixel_coverage"} if scene["schema_version"] in EFFECT_VERSIONS else {}),
                         "visible": geometry.visible(o["id"]), "editable": ["transform", "style", "visible"] +
                         (["geometry"] if o["kind"] != "group" else [])} for o in scene["objects"]]}

"""Small declarative style profiles; Scene styles remain the rendering authority."""
from __future__ import annotations

import copy
import hashlib

from .fonts import check_text, font_info
from .geometry import Geometry
from .scene import schema_validate, validate


# Defaults are the current SVG emitter/geometry defaults, not inferred styling.
DEFAULTS = {"fill": "#000000", "stroke": "none", "stroke_width": 1,
            "opacity": 1, "fill_opacity": 1, "stroke_opacity": 1, "font_size": 14}
NUMERIC_FIELDS = {"stroke_width", "opacity", "fill_opacity", "stroke_opacity", "font_size"}


def validate_profile(profile: dict) -> None:
    schema_validate(profile, "style-profile")
    tokens = profile["tokens"]
    if len({token["id"] for token in tokens}) != len(tokens):
        raise ValueError("duplicate style token ID")
    for token in tokens:
        if token["type"] == "linear_gradient":
            # Reuse the actual Scene resource contract (including ordered stops).
            validate({"schema_version": "0.1", "revision": 0,
                      "canvas": {"width": 1, "height": 1}, "objects": [],
                      "resources": [{"id": "profile_gradient", **token["value"]}]})
        elif token["type"] == "font":
            check_text("", token["value"])
            if "font" in profile and token["value"] != profile["font"]["family"]:
                raise ValueError("profile supports a single font family")
    if "font" in profile:
        font = profile["font"]
        check_text("", font["family"])
        for script in font["scripts"]:
            check_text("A" if script == "latin" else "中", font["family"])


def binding_token(profile: dict | None, binding: dict) -> dict:
    """Check token/field compatibility even when token resolution is disabled."""
    if profile is None:
        raise ValueError("token binding requires a style_profile")
    tokens = {token["id"]: token for token in profile["tokens"]}
    if binding["token"] not in tokens:
        raise ValueError(f"unknown token ID: {binding['token']}")
    token = tokens[binding["token"]]
    field = binding["field"]
    expected = ({"color", "linear_gradient"} if field in {"fill", "stroke"} else
                {"number"} if field in NUMERIC_FIELDS else
                {"font"} if field == "font_family" else set())
    if token["type"] not in expected:
        raise ValueError(f"token type incompatible with field: {field}")
    if field in {"stroke_width", "font_size"} and token["value"] <= 0:
        raise ValueError(f"token for {field} must be positive")
    if field in {"opacity", "fill_opacity", "stroke_opacity"} and token["value"] > 1:
        raise ValueError(f"token for {field} must be in [0, 1]")
    return token


def _gradient_id(profile, token):
    return "ctx-" + hashlib.sha256((profile["id"] + ":" + token["id"]).encode()).hexdigest()[:24]


def resolve_bindings(scene: dict, profile: dict | None, bindings: list) -> None:
    """Mutate only the explicitly bound style fields in an already copied Scene."""
    nodes = {obj["id"]: obj for obj in scene["objects"]}
    for binding in bindings:
        token = binding_token(profile, binding)
        value = copy.deepcopy(token["value"])
        if token["type"] == "linear_gradient":
            rid = _gradient_id(profile, token)
            resource = {"id": rid, **value}
            if rid in nodes:
                raise ValueError(f"context gradient ID collides with object: {rid}")
            existing = next((r for r in scene.get("resources", []) if r["id"] == rid), None)
            if existing is not None and existing != resource:
                raise ValueError(f"context gradient ID collision: {rid}")
            if existing is None:
                scene.setdefault("resources", []).append(resource)
            value = f"url(#{rid})"
        nodes[binding["object_id"]].setdefault("style", {})[binding["field"]] = value


def _color(value):
    if not isinstance(value, str) or not value.startswith("#"):
        return value
    value = value.lower()
    if len(value) == 4:
        value = "#" + "".join(char * 2 for char in value[1:])
    if len(value) == 7:
        value += "ff"
    return value


def _same(actual, token, resources):
    if token["type"] == "linear_gradient":
        if not isinstance(actual, str) or not actual.startswith("url(#"):
            return False
        resource = resources.get(actual[5:-1])
        if resource is None:
            return False
        expected = token["value"]
        return (resource["kind"] == expected["kind"] and resource["start"] == expected["start"]
                and resource["end"] == expected["end"]
                and [[offset, _color(color)] for offset, color in resource["stops"]]
                == [[offset, _color(color)] for offset, color in expected["stops"]])
    if token["type"] == "color":
        return _color(actual) == _color(token["value"])
    return actual == token["value"]


def profile_mismatches(scene: dict, profile: dict | None, bindings: list) -> list:
    """Inspect resolved, inherited Scene styles without repainting anything."""
    geometry = Geometry(scene)
    resources = {r["id"]: r for r in scene.get("resources", [])}
    mismatches = []
    for binding in bindings:
        token = binding_token(profile, binding)
        field, oid = binding["field"], binding["object_id"]
        style = geometry.style(oid)
        # Group opacity composites rather than inheriting; Geometry handles that.
        actual = style.get(field, font_info()["family"] if field == "font_family" else DEFAULTS.get(field))
        if not _same(actual, token, resources):
            mismatches.append({"kind": "token", "object_id": oid, "field": field,
                               "token": token["id"], "expected": copy.deepcopy(token["value"]),
                               "actual": actual})
    if profile is None:
        return mismatches
    for obj in scene["objects"]:
        oid = obj["id"]
        if obj["kind"] == "rect" and "corners" in profile:
            radius = obj["geometry"].get("rx", 0)
            if radius != profile["corners"]["radius"]:
                mismatches.append({"kind": "corners", "object_id": oid,
                                   "expected": profile["corners"]["radius"], "actual": radius})
        if obj["kind"] == "text" and "font" in profile:
            family = geometry.style(oid).get("font_family", font_info()["family"])
            if family != profile["font"]["family"]:
                mismatches.append({"kind": "font", "object_id": oid,
                                   "expected": profile["font"]["family"], "actual": family})
            text = obj["geometry"]["text"]
            has_cjk = any(0x2e80 <= ord(c) <= 0x9fff or 0xac00 <= ord(c) <= 0xd7af
                          or 0x20000 <= ord(c) <= 0x323af for c in text)
            if has_cjk and "cjk" not in profile["font"]["scripts"]:
                mismatches.append({"kind": "font_script", "object_id": oid,
                                   "expected": "cjk", "actual": profile["font"]["scripts"]})
    return mismatches

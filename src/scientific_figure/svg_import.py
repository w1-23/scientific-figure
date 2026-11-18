"""Strict, bounded SVG import; Scene0.2 by default, Scene0.3 by explicit request.

This is an exact subset importer, not an SVG repair tool. Unsupported syntax or
semantics raise ValueError with the source tag/attribute. Scene0.3 additionally
supports radial gradients and numeric stroke dash arrays/offsets. No executable
SVG or CSS is retained, and selecting 0.3 never widens unrelated whitelists.
"""
from __future__ import annotations

from io import BytesIO
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from PIL.ImageColor import colormap

from .fonts import check_text, font_info
from .geometry import Geometry, IDENTITY, multiply
from .scene import ID, REF, number, validate

MAX_BYTES = 1_048_576
MAX_ELEMENTS = 3000
MAX_RESOURCES = 256
MAX_PATH_CHARACTERS = 300_000
_NS = "http://www.w3.org/2000/svg"
_NUM = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_NUMBER = re.compile(_NUM)
_STYLES = {"fill": "fill", "stroke": "stroke", "stroke-width": "stroke_width",
           "opacity": "opacity", "fill-opacity": "fill_opacity", "stroke-opacity": "stroke_opacity",
           "fill-rule": "fill_rule", "font-size": "font_size", "font-family": "font_family",
           "text-anchor": "anchor"}
_PRESENTATION = set(_STYLES) | {"stroke-linecap", "stroke-linejoin", "clip-rule"}
_COMMON = {"id", "transform", "clip-path", "mask"} | _PRESENTATION
_SPECIFIC = {
    "svg": {"width", "height", "viewBox", "version", "preserveAspectRatio"} | _COMMON,
    "g": _COMMON,
    "path": {"d"} | _COMMON,
    "rect": {"x", "y", "width", "height", "rx", "ry"} | _COMMON,
    "ellipse": {"cx", "cy", "rx", "ry"} | _COMMON,
    "circle": {"cx", "cy", "r"} | _COMMON,
    "text": {"x", "y"} | _COMMON,
    "tspan": {"x", "dy"},
    "defs": set(),
    "linearGradient": {"id", "x1", "y1", "x2", "y2", "gradientUnits", "spreadMethod"},
    "stop": {"offset", "stop-color", "stop-opacity"},
    "clipPath": {"id", "clipPathUnits", "transform", "clip-rule"},
    "mask": {"id", "maskUnits", "maskContentUnits", "mask-type", "x", "y", "width", "height"},
    "title": {"id"}, "desc": {"id"},
}
_SHAPES = {"path", "rect", "ellipse", "circle"}
_STYLES_V03 = {**_STYLES, "stroke-dasharray": "stroke_dasharray", "stroke-dashoffset": "stroke_dashoffset"}
_SPECIFIC_V03 = {tag: allowed | ({"stroke-dasharray", "stroke-dashoffset"}
                                if tag in {"svg", "g", "text"} | _SHAPES else set())
                 for tag, allowed in _SPECIFIC.items()}
_SPECIFIC_V03["radialGradient"] = {"id", "cx", "cy", "r", "fx", "fy", "gradientUnits", "spreadMethod"}


def _fail(tag, attribute, detail="unsupported"):
    raise ValueError(f"<{tag}> @{attribute}: {detail}")


def _scalar(value, tag, attr, *, length=False, fraction=False):
    value = value.strip()
    divisor = 1
    if fraction and value.endswith("%"):
        value, divisor = value[:-1], 100
    elif length and value.endswith("px"):
        value = value[:-2]
    if not re.fullmatch(_NUM, value):
        _fail(tag, attr, "expected finite unitless number" + (" or px" if length else ""))
    result = float(value) / divisor
    try:
        number(result, attr)
    except ValueError as exc:
        _fail(tag, attr, str(exc))
    return result


def _numbers(value, tag, attr):
    # Require separators (or a sign) between operands, not arbitrary tokenizer gaps.
    tokens = list(_NUMBER.finditer(value))
    if not tokens or value[:tokens[0].start()].strip() or value[tokens[-1].end():].strip():
        _fail(tag, attr, "invalid numeric list")
    for left, right in zip(tokens, tokens[1:]):
        gap = value[left.end():right.start()]
        if (gap and not re.fullmatch(r"\s*,?\s*", gap)) or (not gap and value[right.start()] not in "+-"):
            _fail(tag, attr, "invalid numeric separator")
    if len(tokens) > 384:
        _fail(tag, attr, "numeric operand budget exceeded")
    return [_scalar(token.group(), tag, attr) for token in tokens]


def _transform(value, tag):
    result, end, count = list(IDENTITY), 0, 0
    for match in re.finditer(r"([A-Za-z]+)\s*\(([^()]*)\)", value):
        gap = value[end:match.start()]
        if (not count and gap.strip()) or (count and not re.fullmatch(r"\s*,?\s*", gap)):
            _fail(tag, "transform", "invalid transform list")
        name, raw = match.groups()
        args = _numbers(raw, tag, "transform")
        if name == "matrix" and len(args) == 6:
            matrix = args
        elif name == "translate" and len(args) in {1, 2}:
            matrix = [1, 0, 0, 1, args[0], args[1] if len(args) == 2 else 0]
        elif name == "scale" and len(args) in {1, 2}:
            matrix = [args[0], 0, 0, args[-1], 0, 0]
        elif name == "rotate" and len(args) in {1, 3}:
            angle = math.radians(args[0])
            c, s = math.cos(angle), math.sin(angle)
            matrix = [c, s, -s, c, 0, 0]
            if len(args) == 3:
                x, y = args[1:]
                matrix = multiply(multiply([1, 0, 0, 1, x, y], matrix), [1, 0, 0, 1, -x, -y])
        else:
            _fail(tag, "transform", f"unsupported {name} or operand count")
        result = multiply(result, matrix)
        for v in result:
            try:
                number(v, "transform")
            except ValueError as exc:
                _fail(tag, "transform", str(exc))
        if abs(result[0]*result[3]-result[1]*result[2]) < 1e-10:
            _fail(tag, "transform", "singular matrix")
        end, count = match.end(), count + 1
        if count > 64:
            _fail(tag, "transform", "at most 64 transforms")
    if not count or value[end:].strip():
        _fail(tag, "transform", "invalid transform list")
    return result


def _color(value, tag, attr, *, paint=True):
    value = value.strip()
    if paint and (value == "none" or REF.fullmatch(value)):
        return value
    value = colormap.get(value.lower(), value)
    # Pillow caches named colors in this shared table as RGB tuples after getrgb().
    if isinstance(value, tuple):
        value = "#" + "".join(f"{component:02x}" for component in value)
    if re.fullmatch(r"#[0-9a-fA-F]{3,4}", value):
        value = "#" + "".join(c*2 for c in value[1:])
    if not re.fullmatch(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?", value):
        _fail(tag, attr, "unsupported color or local paint reference")
    return value.lower()


class _Importer:
    def __init__(self, root, tags, used, schema_version):
        self.root, self.tags, self.used = root, tags, used
        self.styles = _STYLES_V03 if schema_version == "0.3" else _STYLES
        self.paint_kinds = ("linear_gradient", "radial_gradient") if schema_version == "0.3" else ("linear_gradient",)
        self.resource_tags = {"linearGradient", "clipPath", "mask"} | ({"radialGradient"} if schema_version == "0.3" else set())
        self.serial, self.path_chars = 0, 0
        self.scene = {"schema_version": schema_version, "revision": 0, "canvas": {}, "objects": [], "resources": [],
                      "provenance": {"svg_import": {"ignored_metadata": []}}}
        self.references = []
        self.object_tags = {}

    def identifier(self, node):
        if "id" in node.attrib:
            return node.attrib["id"]
        while True:
            self.serial += 1
            oid = f"svg-import-{self.serial}"
            if oid not in self.used:
                self.used.add(oid)
                return oid

    def numeric(self, node, attr, default=None, *, positive=False, fraction=False):
        tag = self.tags[node]
        raw = node.get(attr)
        if raw is None:
            if default is None:
                _fail(tag, attr, "required explicit value")
            raw = str(default)
        value = _scalar(raw, tag, attr, length=not fraction, fraction=fraction)
        if positive and value <= 0:
            _fail(tag, attr, "must be positive")
        return value

    def style(self, node, inherited):
        tag, own = self.tags[node], {}
        for attr, key in self.styles.items():
            if attr not in node.attrib:
                continue
            raw = node.attrib[attr].strip()
            if attr in {"fill", "stroke"}:
                own[key] = _color(raw, tag, attr)
                if REF.fullmatch(own[key]):
                    self.references.append((tag, attr, REF.fullmatch(own[key])[1], self.paint_kinds))
            elif attr == "stroke-dasharray":
                if raw == "none":
                    _fail(tag, attr, "none is not supported by the Scene numeric dash-array contract")
                values = _numbers(raw, tag, attr)
                if not 1 <= len(values) <= 32 or any(value < 0 for value in values) or not any(values):
                    _fail(tag, attr, "requires 1..32 nonnegative numeric lengths, not all zero")
                own[key] = values
            elif attr == "stroke-dashoffset":
                own[key] = _scalar(raw, tag, attr, length=True)
            elif attr in {"opacity", "fill-opacity", "stroke-opacity"}:
                own[key] = _scalar(raw, tag, attr)
                if not 0 <= own[key] <= 1:
                    _fail(tag, attr, "must be in 0..1")
            elif attr in {"font-size", "stroke-width"}:
                own[key] = self.numeric(node, attr, positive=True)
            elif attr == "font-family":
                # One exact selected family, optionally quoted; never CSS fallback lists.
                family = raw[1:-1] if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'" else raw
                if family != font_info()["family"]:
                    _fail(tag, attr, f"requires selected engine family {font_info()['family']!r}")
                own[key] = family
            else:
                allowed = {"fill-rule": {"nonzero", "evenodd"}, "text-anchor": {"start", "middle", "end"}}[attr]
                if raw not in allowed:
                    _fail(tag, attr, "unsupported value")
                own[key] = raw
        effective = {k: v for k, v in inherited.items() if k != "opacity"}
        effective.update(own)
        for attr, default, allowed in (("stroke-linecap", "butt", {"butt", "round", "square"}),
                                       ("stroke-linejoin", "miter", {"miter", "round", "bevel"}),
                                       ("clip-rule", "nonzero", {"nonzero", "evenodd"})):
            value = node.get(attr, effective.get(attr, default))
            if value not in allowed:
                _fail(tag, attr, "unsupported value")
            effective[attr] = value
        return own, effective

    def path(self, d, tag, *, closed=False):
        from .pathops_backend import _recording
        self.path_chars += len(d)
        if self.path_chars > MAX_PATH_CHARACTERS:
            _fail(tag, "d", "total path characters exceed 300000")
        try:
            _recording(d, closed=closed)
        except (ValueError, TypeError, IndexError, OverflowError) as exc:
            _fail(tag, "d", str(exc))
        return d

    def geometry(self, node):
        tag = self.tags[node]
        if tag == "path":
            if "d" not in node.attrib:
                _fail(tag, "d", "required")
            return "path", {"d": self.path(node.attrib["d"], tag)}
        if tag == "rect":
            g = {a: self.numeric(node, a, 0 if a in {"x", "y"} else None,
                                  positive=a in {"width", "height"}) for a in ("x", "y", "width", "height")}
            if "rx" in node.attrib or "ry" in node.attrib:
                rx = self.numeric(node, "rx", node.get("ry"))
                ry = self.numeric(node, "ry", node.get("rx"))
                if min(rx, ry) < 0 or rx != ry:
                    _fail(tag, "rx/ry", "requires equal nonnegative corner radii")
                if rx:
                    g["rx"] = rx
            return tag, g
        if tag in {"ellipse", "circle"}:
            g = {a: self.numeric(node, a, 0) for a in ("cx", "cy")}
            for a in ("rx", "ry"):
                g[a] = self.numeric(node, "r" if tag == "circle" else a, positive=True)
            return "ellipse", g
        _fail(tag, "geometry", "unsupported vector shape")

    def vector_path(self, node, matrix=IDENTITY):
        from .pathops_backend import transformed
        kind, g = self.geometry(node)
        tag = self.tags[node]
        if kind == "path":
            d = g["d"]
        elif kind == "rect":
            x, y, w, h = (g[a] for a in ("x", "y", "width", "height"))
            if g.get("rx"):
                _fail(tag, "rx", "rounded resource rectangles unsupported")
            d = f"M{x} {y}h{w}v{h}h{-w}Z"
        else:
            x, y, rx, ry = (g[a] for a in ("cx", "cy", "rx", "ry"))
            d = f"M{x-rx} {y}a{rx} {ry} 0 1 0 {2*rx} 0a{rx} {ry} 0 1 0 {-2*rx} 0Z"
        own_matrix = _transform(node.attrib["transform"], tag) if "transform" in node.attrib else IDENTITY
        matrix = multiply(matrix, own_matrix)
        # fontTools' transformed() approximates arcs as cubics: refuse that conversion.
        if matrix != IDENTITY:
            if re.search("[Aa]", d):
                _fail(tag, "transform", "transformed resource arcs cannot be represented exactly")
            try:
                d = transformed(d, matrix)
            except ValueError as exc:
                _fail(tag, "transform", str(exc))
        # Count synthesized geometry too; path sources were already counted.
        if kind != "path" or matrix != IDENTITY:
            self.path(d, tag, closed=True)
        else:
            from .pathops_backend import _recording
            try:
                _recording(d, closed=True)
            except ValueError as exc:
                _fail(tag, "d", str(exc))
        return d

    def leaf(self, node):
        if len(node):
            _fail(self.tags[node], self.tags[node[0]], "unsupported child")
        if (node.text or "").strip():
            _fail(self.tags[node], "text", "unexpected character content")

    def resource(self, node, inherited):
        tag, rid = self.tags[node], self.identifier(node)
        if len(self.scene["resources"]) >= MAX_RESOURCES:
            _fail(tag, "resources", "at most 256 resources")
        if tag in {"linearGradient", "radialGradient"}:
            units = node.get("gradientUnits", "objectBoundingBox")
            if units not in ({"objectBoundingBox", "userSpaceOnUse"} if tag == "radialGradient" else {"objectBoundingBox"}):
                _fail(tag, "gradientUnits", "unsupported coordinate units")
            if node.get("spreadMethod", "pad") != "pad":
                _fail(tag, "spreadMethod", "only pad supported")
            if tag == "linearGradient":
                start = [self.numeric(node, a, d, fraction=True) for a, d in (("x1", 0), ("y1", 0))]
                end = [self.numeric(node, a, d, fraction=True) for a, d in (("x2", 1), ("y2", 0))]
                resource = {"id": rid, "kind": "linear_gradient", "start": start, "end": end}
            else:
                fractions = units == "objectBoundingBox"
                # User-space percentage defaults depend on the viewport. Require
                # explicit geometry instead of substituting bounding-box defaults.
                default = 0.5 if fractions else None
                center = [self.numeric(node, a, default, fraction=fractions) for a in ("cx", "cy")]
                radius = self.numeric(node, "r", default, positive=True, fraction=fractions)
                focus = [self.numeric(node, a, d, fraction=fractions) for a, d in zip(("fx", "fy"), center)]
                if math.hypot(focus[0] - center[0], focus[1] - center[1]) >= radius:
                    _fail(tag, "fx/fy", "focus must be strictly inside the radial circle")
                resource = {"id": rid, "kind": "radial_gradient", "center": center,
                            "radius": radius, "focus": focus, "units": units}
            stops = []
            for stop in node:
                if self.tags[stop] != "stop":
                    _fail(tag, self.tags[stop], "expected stop")
                self.leaf(stop)
                offset = self.numeric(stop, "offset", fraction=True)
                color = _color(stop.get("stop-color", "#000000"), "stop", "stop-color", paint=False)
                opacity = _scalar(stop.get("stop-opacity", "1"), "stop", "stop-opacity")
                if not 0 <= opacity <= 1:
                    _fail("stop", "stop-opacity", "must be in 0..1")
                alpha = (int(color[7:9], 16) if len(color) == 9 else 255) * opacity
                if abs(alpha - round(alpha)) > 1e-9:
                    _fail("stop", "stop-opacity", "alpha is not exactly representable as RGBA8")
                color = color[:7] + (f"{round(alpha):02x}" if alpha != 255 else "")
                if not 0 <= offset <= 1 or (stops and offset < stops[-1][0]):
                    _fail("stop", "offset", "requires ordered values in 0..1")
                stops.append([offset, color])
                if tag == "radialGradient" and len(stops) > 256:
                    _fail(tag, "stop", "at most 256 radial stops")
            if len(stops) < 2:
                _fail(tag, "stop", "requires at least two stops")
            resource["stops"] = stops
        elif tag == "clipPath":
            if node.get("clipPathUnits", "userSpaceOnUse") != "userSpaceOnUse":
                _fail(tag, "clipPathUnits", "requires userSpaceOnUse")
            if len(node) != 1 or self.tags[node[0]] not in _SHAPES:
                _fail(tag, "children", "requires one closed vector shape")
            child = node[0]
            self.resource_shape(child)
            if node.get("clip-rule", "nonzero") not in {"nonzero", "evenodd"}:
                _fail(tag, "clip-rule", "unsupported value")
            _, effective = self.style(child, {**inherited, "clip-rule": node.get("clip-rule", inherited.get("clip-rule", "nonzero"))})
            rule = effective["clip-rule"]
            matrix = _transform(node.attrib["transform"], tag) if "transform" in node.attrib else IDENTITY
            resource = {"id": rid, "kind": "clip_path", "d": self.vector_path(child, matrix), "fill_rule": rule}
        else:
            for attr in ("maskUnits", "maskContentUnits"):
                if node.get(attr) != "userSpaceOnUse":
                    _fail(tag, attr, "requires explicit userSpaceOnUse")
            if node.get("mask-type") != "alpha":
                _fail(tag, "mask-type", "requires explicit alpha")
            region = [self.numeric(node, a, positive=a in {"width", "height"}) for a in ("x", "y", "width", "height")]
            if not 1 <= len(node) <= 16:
                _fail(tag, "children", "requires 1..16 constant-alpha vector shapes")
            shapes = []
            for child in node:
                self.resource_shape(child)
                _, style = self.style(child, inherited)
                if style.get("fill_rule", "nonzero") != "nonzero":
                    _fail(self.tags[child], "fill-rule", "mask requires nonzero")
                if style.get("stroke", "none") != "none":
                    _fail(self.tags[child], "stroke", "mask strokes unsupported")
                fill = style.get("fill", "#000000")
                if fill.startswith("url("):
                    _fail(self.tags[child], "fill", "mask requires constant alpha")
                alpha = (0 if fill == "none" else int(fill[7:9], 16)/255 if len(fill) == 9 else 1)
                alpha *= style.get("fill_opacity", 1) * style.get("opacity", 1)
                shapes.append({"d": self.vector_path(child), "alpha": alpha})
            resource = {"id": rid, "kind": "alpha_mask", "region": region, "shapes": shapes}
        if (node.text or "").strip():
            _fail(tag, "text", "unexpected character content")
        if resource["kind"] in {"clip_path", "alpha_mask"}:
            from .scene_effects import validate_resource
            try:
                validate_resource(resource)
            except ValueError as exc:
                _fail(tag, "geometry", str(exc))
        self.scene["resources"].append(resource)

    def resource_shape(self, node):
        tag = self.tags[node]
        if tag not in _SHAPES:
            _fail(tag, "resource", "requires vector shape")
        # Resource shape identities/effects cannot survive in Scene0.2 resources.
        for attr in ("id", "clip-path", "mask"):
            if attr in node.attrib:
                _fail(tag, attr, "unsupported inside resource")
        self.leaf(node)

    def text(self, node, effective):
        tag = self.tags[node]
        if "font_family" not in effective:
            _fail(tag, "font-family", "standalone text requires explicit selected engine family")
        x, y = self.numeric(node, "x", 0), self.numeric(node, "y", 0)
        # resvg's SVG text processing collapses XML whitespace to single spaces.
        normalize = lambda s: re.sub(r"[ \t\r\n]+", " ", s or "")
        first = normalize(node.text).strip(" ")
        if len(node) and normalize(node.text) != first:
            _fail(tag, "text", "multiline edge whitespace cannot be preserved exactly")
        lines = [first]
        for child in node:
            if self.tags[child] != "tspan":
                _fail(tag, self.tags[child], "only explicit multiline tspan supported")
            if len(child) or (child.tail or "").strip():
                _fail("tspan", "content", "mixed or nested text unsupported")
            if self.numeric(child, "x") != x or not math.isclose(self.numeric(child, "dy"), effective.get("font_size", 16)*1.2, rel_tol=0, abs_tol=1e-10):
                _fail("tspan", "x/dy", "requires same x and exact 1.2 font-size line spacing")
            line = normalize(child.text).strip(" ")
            if normalize(child.text) != line:
                _fail("tspan", "text", "multiline edge whitespace cannot be preserved exactly")
            if not line:
                _fail("tspan", "text", "empty multiline spans unsupported")
            lines.append(line)
        if len(lines) > 1 and not first:
            _fail(tag, "text", "requires explicit first line before tspan")
        text = "\n".join(lines)
        if not text or len(text) > 10000:
            _fail(tag, "text", "requires 1..10000 characters")
        try:
            check_text(text, effective["font_family"])
        except ValueError as exc:
            _fail(tag, "text", str(exc))
        return {"x": x, "y": y, "text": text}

    def walk(self, node, parent=None, inherited=None):
        tag = self.tags[node]
        inherited = inherited or {}
        if tag in {"title", "desc"}:
            if node not in list(self.root):
                _fail(tag, "metadata", "metadata supported only on root")
            if len(node):
                _fail(tag, "children", "metadata must be plain text")
            self.scene["provenance"]["svg_import"]["ignored_metadata"].append(
                {"tag": tag, "text": node.text or "", **({"id": node.attrib["id"]} if "id" in node.attrib else {})})
            return
        if tag == "defs":
            if (node.text or "").strip():
                _fail(tag, "text", "unexpected character content")
            for child in node:
                if self.tags[child] not in self.resource_tags:
                    _fail(tag, self.tags[child], "defs requires supported resources")
                self.resource(child, inherited)
            return
        if tag not in {"svg", "g", "text"} | _SHAPES or (tag == "svg" and node is not self.root):
            _fail(tag, "placement", "unsupported or nested SVG element")
        own, effective = self.style(node, inherited)
        if node is self.root:
            own.setdefault("font_size", 16)
            effective.setdefault("font_size", 16)
        kind, g = ("group", {}) if tag in {"svg", "g"} else (("text", self.text(node, effective)) if tag == "text" else self.geometry(node))
        if kind not in {"group", "text"}:
            self.leaf(node)
        if kind != "group" and effective.get("stroke", "none") != "none":
            for attr in ("stroke-linecap", "stroke-linejoin"):
                if effective[attr] != "round":
                    _fail(tag, attr, "engine requires round for painted strokes")
        obj = {"id": self.identifier(node), "kind": kind, "geometry": g, "style": own}
        if parent:
            obj["parent"] = parent
        if "transform" in node.attrib:
            obj["transform"] = _transform(node.attrib["transform"], tag)
        for attr, field, resource_kind in (("clip-path", "clip", "clip_path"), ("mask", "mask", "alpha_mask")):
            if attr in node.attrib:
                match = REF.fullmatch(node.attrib[attr].strip())
                if not match:
                    _fail(tag, attr, "requires local typed url(#id)")
                obj[field] = match[1]
                self.references.append((tag, attr, match[1], (resource_kind,)))
        self.scene["objects"].append(obj)
        self.object_tags[obj["id"]] = tag
        if kind == "group":
            if (node.text or "").strip():
                _fail(tag, "text", "unexpected character content")
            for child in node:
                self.walk(child, obj["id"], effective)

    def run(self):
        root = self.root
        for attr, allowed in (("version", {"1.1", "2.0"}), ("preserveAspectRatio", {"xMidYMid meet", "xMidYMid"})):
            if attr in root.attrib and root.attrib[attr].strip() not in allowed:
                _fail("svg", attr, "unsupported value")
        canvas = self.scene["canvas"]
        for attr in ("width", "height"):
            canvas[attr] = self.numeric(root, attr, positive=True)
            if canvas[attr] > 8192:
                _fail("svg", attr, "canvas limit is 8192")
        if "viewBox" in root.attrib:
            vb = _numbers(root.attrib["viewBox"], "svg", "viewBox")
            if len(vb) != 4 or min(vb[2:]) <= 0:
                _fail("svg", "viewBox", "requires x y positive-width positive-height")
            canvas["viewBox"] = vb
        self.scene["provenance"]["svg_import"]["source_canvas"] = {
            k: root.attrib[k] for k in ("width", "height", "viewBox") if k in root.attrib}
        self.walk(root)
        resources = {r["id"]: r["kind"] for r in self.scene["resources"]}
        for tag, attr, rid, kinds in self.references:
            if resources.get(rid) not in kinds:
                _fail(tag, attr, f"missing or wrong typed resource #{rid}")
        # Individually bounded matrices can overflow when nested. Check their
        # composed coefficients before geometry validation/evaluation uses them.
        geometry = Geometry(self.scene)
        for obj in self.scene["objects"]:
            try:
                for value in geometry.world(obj["id"]):
                    number(value, "composed transform")
            except ValueError as exc:
                _fail(self.object_tags[obj["id"]], "transform", str(exc))
        try:
            validate(self.scene)
            for obj in self.scene["objects"]:
                box = geometry.bounds(obj["id"])
                if box is not None:
                    for value in box:
                        number(value, "world bounds")
        except (ValueError, TypeError, IndexError, OverflowError) as exc:
            _fail("svg", "scene", str(exc))
        return self.scene


def import_svg(path, *, schema_version="0.2") -> dict:
    """Read <=1 MiB of UTF-8 SVG and return an independently editable validated Scene.

    The default is strict Scene0.2. Explicit schema_version='0.3' adds radial
    gradients and numeric dash arrays/offsets. Radial userSpaceOnUse requires
    explicit numeric/px cx, cy, r; its percentage coordinates are unsupported.
    Bounding-box radial coordinates accept numbers/percentages and SVG defaults.
    Dash arrays require unitless numeric lengths (1..32, nonnegative, not all
    zero); 'none', percentages and CSS inheritance/reset keywords are rejected.
    Limits: 3000 XML elements, depth 80, 256 resources, 300000 total path
    characters, 64 transforms per element, finite operands bounded by 1e7.
    CSS/style, foreign namespaces, external links, DTD/entities and unsupported
    features fail explicitly. The path is input only; no files are written.
    """
    if type(schema_version) is not str or schema_version not in ("0.2", "0.3"):
        raise ValueError("SVG import schema_version must be '0.2' or '0.3'")
    specific = _SPECIFIC_V03 if schema_version == "0.3" else _SPECIFIC
    with Path(path).open("rb") as source:
        data = source.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        _fail("svg", "XML", "input exceeds 1 MiB")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError as exc:
        _fail("svg", "encoding", f"requires UTF-8: {exc}")
    declaration = re.match(r'<\?xml\s+([^?]*)\?>', text)
    if declaration:
        encoding = re.search(r'''encoding\s*=\s*['"]([^'"]+)['"]''', declaration[1])
        if encoding and encoding[1].lower() not in {"utf-8", "utf8"}:
            _fail("svg", "encoding", "requires UTF-8 declaration")
    if re.search(r"<!DOCTYPE|<!ENTITY", text, re.I):
        _fail("svg", "XML", "DTD/entity declarations forbidden")
    if re.search(r"<\?(?!xml\s)|<!--", text):
        _fail("svg", "XML", "processing instructions/comments unsupported")
    tags, used, depth, root_namespace = {}, set(), 0, None
    try:
        parser = ET.iterparse(BytesIO(data), events=("start-ns", "start", "end"))
        for event, node in parser:
            if event == "start-ns":
                prefix, uri = node
                if uri != _NS:
                    _fail("svg", "xmlns" + (":" + prefix if prefix else ""), "foreign namespace")
            elif event == "start":
                depth += 1
                if depth > 80 or len(tags) >= MAX_ELEMENTS:
                    _fail("svg", "XML", "element/depth budget exceeded (3000/80)")
                namespace, tag = (node.tag[1:].split("}", 1) if node.tag.startswith("{") else ("", node.tag))
                if root_namespace is None:
                    root_namespace = namespace
                    if tag != "svg":
                        _fail(tag, "root", "requires svg")
                if namespace not in {"", _NS} or namespace != root_namespace:
                    _fail(tag, "xmlns", "foreign or mixed namespace")
                if tag not in specific:
                    _fail(tag, "tag", "unsupported element")
                for attr in node.attrib:
                    if attr not in specific[tag]:
                        _fail(tag, attr, "unsupported attribute/CSS/event/external reference")
                oid = node.get("id")
                if oid is not None:
                    if not ID.fullmatch(oid) or oid.startswith("sf-") or oid in used:
                        _fail(tag, "id", "invalid, duplicate or reserved ID")
                    used.add(oid)
                tags[node] = tag
            else:
                depth -= 1
                if (node.tail or "").strip():
                    _fail(tags[node], "tail", "unexpected character content")
        root = parser.root
    except ET.ParseError as exc:
        _fail("svg", "XML", str(exc))
    return _Importer(root, tags, used, schema_version).run()

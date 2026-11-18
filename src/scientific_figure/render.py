"""CPU-only headless rendering using resvg; no runtime network requests."""
from __future__ import annotations
import math
import xml.etree.ElementTree as ET
from importlib.metadata import version
import resvg_py
from .fonts import font_info


def render_svg(svg: str, scale: float=2) -> bytes:
    if not math.isfinite(scale) or not 0 < scale <= 16:
        raise ValueError("render scale must be >0 and <=16")
    if "<!DOCTYPE" in svg.upper() or "<!ENTITY" in svg.upper():
        raise ValueError("unsupported XML declaration")
    root=ET.fromstring(svg)
    import re
    namespace = "{http://www.w3.org/2000/svg}"
    common = {"id", "transform", "display", "fill", "stroke", "stroke-width", "opacity", "fill-opacity", "stroke-opacity",
              "fill-rule", "font-size", "font-family", "text-anchor", "stroke-linejoin", "stroke-linecap", "clip-path", "mask", "marker-end",
              "stroke-dasharray", "stroke-dashoffset"}
    specific = {"svg": {"width", "height", "viewBox", "version"}, "g": set(), "defs": set(),
                "linearGradient": {"x1", "y1", "x2", "y2"},
                "radialGradient": {"cx", "cy", "r", "fx", "fy", "gradientUnits"}, "stop": {"offset", "stop-color"},
                "path": {"d", "clip-rule"}, "rect": {"x", "y", "width", "height", "rx"},
                "ellipse": {"cx", "cy", "rx", "ry"}, "text": {"x", "y"}, "tspan": {"x", "dy"}, "title": set(),
                "marker": {"viewBox", "refX", "refY", "markerWidth", "markerHeight", "orient"},
                "clipPath": {"clipPathUnits"}, "mask": {"maskUnits", "maskContentUnits", "mask-type", "x", "y", "width", "height"}}
    ids = {}
    for node in root.iter():
        tag = node.tag[len(namespace):] if node.tag.startswith(namespace) else node.tag
        if tag not in specific or (node.tag.startswith("{") and not node.tag.startswith(namespace)):
            raise ValueError("renderer accepts the prototype SVG subset only")
        if set(node.attrib) - common - specific[tag]:
            raise ValueError("unsupported SVG attributes, external resources, scripts or CSS")
        oid = node.get("id")
        if oid:
            if oid in ids:
                raise ValueError("duplicate SVG ID")
            ids[oid] = tag
    targets = {"fill": {"linearGradient", "radialGradient"}, "stroke": {"linearGradient", "radialGradient"},
               "clip-path": {"clipPath"}, "mask": {"mask"}, "marker-end": {"marker"}}
    for node in root.iter():
        for key, value in node.attrib.items():
            if "url(" in value or key in {"clip-path", "mask", "marker-end"}:
                match = re.fullmatch(r"url\(#([A-Za-z_][A-Za-z0-9_.-]*)\)", value)
                if key not in targets or not match or ids.get(match[1]) not in targets[key]:
                    raise ValueError("invalid typed SVG resource reference")
        if node.tag.endswith("}mask") or node.tag == "mask":
            if node.get("mask-type") != "alpha" or node.get("maskUnits") != "userSpaceOnUse" or node.get("maskContentUnits") != "userSpaceOnUse":
                raise ValueError("only explicit user-space alpha masks supported")
        if node.tag.endswith("}clipPath") or node.tag == "clipPath":
            if node.get("clipPathUnits") != "userSpaceOnUse":
                raise ValueError("only user-space clipping supported")
    width,height=float(root.attrib["width"]),float(root.attrib["height"])
    if not math.isfinite(width*height) or min(width,height)<=0 or width*height*scale*scale>40_000_000:
        raise ValueError("render exceeds 40 megapixel budget")
    return resvg_py.svg_to_bytes(svg_string=svg, zoom=scale, skip_system_fonts=True,
                                font_files=[font_info()["path"]], font_family=font_info()["family"])


def runtime_info():
    font=font_info()
    return {"renderer":"resvg-py", "renderer_version":version("resvg-py"), "device":"cpu",
            "font":{k:v for k,v in font.items() if k!="glyphs"}}

"""Deterministic SVG output. Group opacity and compound paths remain intact."""
from __future__ import annotations
import xml.etree.ElementTree as ET
from .fonts import font_info
from .geometry import Geometry, inverse, point
from .scene import validate

NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", NS)

def element(tag, attrib=None):
    return ET.Element(f"{{{NS}}}{tag}", {k:fmt(v) for k,v in (attrib or {}).items()})

def fmt(value):
    if isinstance(value,(int,float)):
        return f"{value:.8g}"
    return str(value)

def matrix_attr(matrix):
    return "matrix("+" ".join(fmt(v) for v in matrix)+")"


def to_svg(scene):
    validate(scene)
    canvas = scene["canvas"]
    vb = canvas.get("viewBox",[0,0,canvas["width"],canvas["height"]])
    root = element("svg", {"width":canvas["width"],"height":canvas["height"],
                           "viewBox":" ".join(fmt(v) for v in vb), "version":"1.1",
                           "font-family":font_info()["family"],"font-size":14,
                           "fill":"#000000","stroke-linejoin":"round","stroke-linecap":"round"})
    defs = element("defs")
    root.append(defs)
    for res in scene.get("resources",[]):
        if res["kind"] == "clip_path":
            clip = element("clipPath", {"id": res["id"], "clipPathUnits": "userSpaceOnUse"})
            clip.append(element("path", {"d": res["d"], "clip-rule": res.get("fill_rule", "nonzero")}))
            defs.append(clip)
        elif res["kind"] == "alpha_mask":
            x, y, w, h = res["region"]
            mask = element("mask", {"id": res["id"], "maskUnits": "userSpaceOnUse", "maskContentUnits": "userSpaceOnUse",
                                    "mask-type": "alpha", "x": x, "y": y, "width": w, "height": h})
            for shape in res["shapes"]:
                mask.append(element("path", {"d": shape["d"], "fill": "#ffffff", "fill-opacity": shape["alpha"]}))
            defs.append(mask)
        else:
            if res["kind"] == "radial_gradient":
                focus = res.get("focus", res["center"])
                gradient = element("radialGradient", {"id": res["id"], "cx": res["center"][0],
                    "cy": res["center"][1], "r": res["radius"], "fx": focus[0], "fy": focus[1],
                    "gradientUnits": res.get("units", "objectBoundingBox")})
            else:
                gradient = element("linearGradient", {"id":res["id"],"x1":res["start"][0],"y1":res["start"][1],
                                                      "x2":res["end"][0],"y2":res["end"][1]})
            for offset,color in res["stops"]:
                gradient.append(element("stop",{"offset":offset,"stop-color":color}))
            defs.append(gradient)
    if canvas.get("background","none") != "none":
        root.append(element("rect",{"x":vb[0],"y":vb[1],"width":vb[2],"height":vb[3],"fill":canvas["background"]}))
    geo = Geometry(scene)
    if scene.get("occlusions"):
        from .scene_effects import evaluate
        derived = evaluate(scene)["paths"]
    else:
        derived = {}
    keys = {"stroke_width":"stroke-width","fill_opacity":"fill-opacity","stroke_opacity":"stroke-opacity",
            "fill_rule":"fill-rule","font_size":"font-size","font_family":"font-family","anchor":"text-anchor",
            "stroke_dasharray":"stroke-dasharray","stroke_dashoffset":"stroke-dashoffset"}
    def emit(oid, parent):
        obj = geo.nodes[oid]
        attrs = {"id":oid}
        if "transform" in obj: attrs["transform"] = matrix_attr(obj["transform"])
        if not obj.get("visible",True): attrs["display"]="none"
        for key,value in obj.get("style",{}).items():
            if key != "arrow_end":
                attrs[keys.get(key, key)] = " ".join(fmt(v) for v in value) if key == "stroke_dasharray" else value
        for field, attribute in (("clip", "clip-path"), ("mask", "mask")):
            if field in obj:
                attrs[attribute] = f"url(#{obj[field]})"
        g,kind = obj.get("geometry",{}),obj["kind"]
        if oid in derived:
            g, kind = {"d": derived[oid]["d"]}, "path"
            attrs.update(fill=derived[oid]["paint"], stroke="none")
            attrs["fill-rule"] = "nonzero"
            if not g["d"]:
                attrs["display"] = "none"
        if kind=="group":
            node = element("g",attrs)
        elif kind in {"rect","ellipse","path"}:
            node = element(kind,{**attrs,**g})
        elif kind=="text":
            node = element("text",{**attrs,"x":g["x"],"y":g["y"]})
            for i,line in enumerate(g["text"].split("\n")):
                if i==0:node.text=line
                else:
                    span = element("tspan",{"x":g["x"],"dy":geo.style(oid).get("font_size",14)*1.2})
                    span.text=line;node.append(span)
        else:
            p1,p2=geo.endpoints(oid)
            inv=inverse(geo.world(oid))
            p1,p2=point(inv,*p1),point(inv,*p2)
            node=element("path",{**attrs,"d":f"M {fmt(p1[0])} {fmt(p1[1])} L {fmt(p2[0])} {fmt(p2[1])}","fill":"none"})
        if obj.get("semantic",{}).get("name"):
            title=element("title");title.text=str(obj["semantic"]["name"]);node.append(title)
        if kind=="connector" or geo.style(oid).get("arrow_end"):
            marker_id="sf-arrow-"+oid
            marker=element("marker",{"id":marker_id,"viewBox":"0 0 10 10","refX":9,"refY":5,
                                      "markerWidth":6,"markerHeight":6,"orient":"auto-start-reverse"})
            marker.append(element("path",{"d":"M 0 0 L 10 5 L 0 10 Z", "fill":geo.style(oid).get("stroke","#000000")}))
            defs.append(marker);node.set("marker-end",f"url(#{marker_id})")
        parent.append(node)
        if kind=="group":
            for child in geo.children.get(oid,[]):emit(child,node)
    for oid in geo.children[None]:emit(oid,root)
    return ET.tostring(root,encoding="unicode",xml_declaration=True)+"\n"

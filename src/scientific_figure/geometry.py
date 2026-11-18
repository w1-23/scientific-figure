"""Scene geometry in SVG user units. Matrices map local points into world space."""
from __future__ import annotations

from fontTools.pens.boundsPen import BoundsPen
from fontTools.svgLib.path import parse_path
from .fonts import text_bounds

IDENTITY = [1, 0, 0, 1, 0, 0]


def multiply(a, b):
    """Return a @ b: apply b first, then a."""
    return [a[0]*b[0]+a[2]*b[1], a[1]*b[0]+a[3]*b[1],
            a[0]*b[2]+a[2]*b[3], a[1]*b[2]+a[3]*b[3],
            a[0]*b[4]+a[2]*b[5]+a[4], a[1]*b[4]+a[3]*b[5]+a[5]]


def point(matrix, x, y):
    a, b, c, d, e, f = matrix
    return a*x+c*y+e, b*x+d*y+f


def inverse(matrix):
    a, b, c, d, e, f = matrix
    det = a*d-b*c
    if abs(det) < 1e-10:
        raise ValueError("singular transform")
    return [d/det, -b/det, -c/det, a/det, (c*f-d*e)/det, (b*e-a*f)/det]


def union(boxes):
    boxes = [b for b in boxes if b is not None]
    return ([min(b[0] for b in boxes), min(b[1] for b in boxes),
             max(b[2] for b in boxes), max(b[3] for b in boxes)] if boxes else None)


def transform_box(box, matrix):
    if box is None:
        return None
    pts = [point(matrix, x, y) for x, y in [(box[0],box[1]), (box[0],box[3]),
                                           (box[2],box[1]), (box[2],box[3])]]
    return [min(p[0] for p in pts), min(p[1] for p in pts),
            max(p[0] for p in pts), max(p[1] for p in pts)]


def local_bounds(node, style):
    g, kind = node.get("geometry", {}), node["kind"]
    if kind == "rect":
        box = [g["x"],g["y"],g["x"]+g["width"],g["y"]+g["height"]]
    elif kind == "ellipse":
        box = [g["cx"]-g["rx"],g["cy"]-g["ry"],g["cx"]+g["rx"],g["cy"]+g["ry"]]
    elif kind == "path":
        pen = BoundsPen(None)
        parse_path(g["d"], pen)
        box = list(pen.bounds) if pen.bounds else None
    elif kind == "text":
        box = text_bounds(g, style)
    else:
        return None
    if box is not None:
        pad = style.get("stroke_width", 1)/2 if style.get("stroke", "none") != "none" else 0
        # Extra slack for text rasterizer metrics; miter joins are not used by the emitter.
        if style.get("arrow_end"):
            pad = max(pad,style.get("stroke_width",1)*6)
        pad += 1 if kind == "text" else 0
        box = [box[0]-pad,box[1]-pad,box[2]+pad,box[3]+pad]
    return box


class Geometry:
    def __init__(self, scene):
        self.scene = scene
        self.nodes = {o["id"]: o for o in scene["objects"]}
        self.children = {None: []}
        for obj in scene["objects"]:
            self.children.setdefault(obj.get("parent"), []).append(obj["id"])
        self._world, self._styles, self._bounds = {}, {}, {}

    def world(self, oid):
        if oid not in self._world:
            obj = self.nodes[oid]
            parent = self.world(obj["parent"]) if obj.get("parent") else IDENTITY
            self._world[oid] = multiply(parent, obj.get("transform", IDENTITY))
        return self._world[oid]

    def style(self, oid):
        if oid not in self._styles:
            obj = self.nodes[oid]
            inherited = self.style(obj["parent"]) if obj.get("parent") else {}
            # opacity is composited on groups, not inherited as a paint attribute.
            self._styles[oid] = {**{k:v for k,v in inherited.items() if k != "opacity"},
                                 **obj.get("style", {})}
        return self._styles[oid]

    def visible(self, oid):
        obj = self.nodes[oid]
        return obj.get("visible", True) and (not obj.get("parent") or self.visible(obj["parent"]))

    def visible_bounds(self, oid):
        """Conservative bounds after local cuts and clip/mask extents.

        Nonempty bounds are not proof of visible painted pixels, especially for
        text or a clip hole. Original bounds()/bbox anchors retain old semantics.
        """
        obj = self.nodes[oid]
        if obj["kind"] == "group":
            return union([self.visible_bounds(child) for child in self.children.get(oid, [])])
        box = self.bounds(oid)
        if self.scene.get("occlusions"):
            if not hasattr(self, "_derived_effects"):
                from .scene_effects import evaluate
                self._derived_effects = evaluate(self.scene)["paths"]
            derived = self._derived_effects.get(oid)
            if derived is not None:
                box = (transform_box(local_bounds({"kind": "path", "geometry": {"d": derived["d"]}}, {}), self.world(oid))
                       if derived["d"] else None)
        resources = {r["id"]: r for r in self.scene.get("resources", [])}
        current = oid
        while current and box is not None:
            node = self.nodes[current]
            if node.get("style", {}).get("opacity", 1) == 0 or not node.get("visible", True):
                return None
            for field in ("clip", "mask"):
                if field not in node:
                    continue
                resource = resources[node[field]]
                if field == "clip":
                    extent = local_bounds({"kind": "path", "geometry": {"d": resource["d"]}}, {})
                else:
                    x, y, w, h = resource["region"]
                    extent = union([local_bounds({"kind": "path", "geometry": {"d": shape["d"]}}, {})
                                    for shape in resource["shapes"] if shape["alpha"] > 0])
                    if extent is not None:
                        extent = [max(x, extent[0]), max(y, extent[1]), min(x+w, extent[2]), min(y+h, extent[3])]
                if extent is not None and (extent[0] >= extent[2] or extent[1] >= extent[3]):
                    return None
                extent = transform_box(extent, self.world(current))
                if extent is None:
                    return None
                box = [max(box[0], extent[0]), max(box[1], extent[1]), min(box[2], extent[2]), min(box[3], extent[3])]
                if box[0] >= box[2] or box[1] >= box[3]:
                    return None
            current = node.get("parent")
        return box

    def anchor(self, oid, name):
        if isinstance(name, str) and name.startswith("port:"):
            if not self.visible(oid):
                raise ValueError("connector target is hidden")
            xy = self.nodes[oid].get("anchors", {}).get(name[5:])
            if xy is None:
                raise ValueError("unknown persistent port")
            return point(self.world(oid), *xy)
        box = self.bounds(oid)
        if box is None:
            raise ValueError(f"connector target {oid} has no visible bounds")
        x0,y0,x1,y1 = box
        return {"center":((x0+x1)/2,(y0+y1)/2), "left":(x0,(y0+y1)/2),
                "right":(x1,(y0+y1)/2), "top":((x0+x1)/2,y0),
                "bottom":((x0+x1)/2,y1)}[name]

    def endpoints(self, oid):
        g = self.nodes[oid]["geometry"]
        return self.anchor(g["from"], g.get("from_anchor","right")), self.anchor(g["to"], g.get("to_anchor","left"))

    def bounds(self, oid):
        if oid in self._bounds:
            return self._bounds[oid]
        obj = self.nodes[oid]
        if not self.visible(oid):
            result = None
        elif obj["kind"] == "group":
            result = union([self.bounds(k) for k in self.children.get(oid, [])])
        elif obj["kind"] == "connector":
            p1,p2 = self.endpoints(oid)
            pad = max(6, self.style(oid).get("stroke_width",2)*5)
            result = [min(p1[0],p2[0])-pad, min(p1[1],p2[1])-pad,
                      max(p1[0],p2[0])+pad, max(p1[1],p2[1])+pad]
        else:
            result = transform_box(local_bounds(obj,self.style(oid)),self.world(oid))
        self._bounds[oid] = result
        return result

"""Recipe fragments: ordinary Scene objects, committed as one transaction."""
from __future__ import annotations

from copy import deepcopy
import hashlib

from jsonschema import Draft202012Validator

from ..fonts import font_info, text_bounds
from ..geometry import IDENTITY, inverse
from ..scene import COLOR, ID, REF, finite, number

VERSION = "1.0.0"
SOURCE = {
    "kind": "original_geometry",
    "author": "Scientific Figure project contributors",
    "license": "project_license_unset",
    "url": None,
    "commit": None,
    "notice": "Original geometric formulas; no external artwork or copied paths. Public distribution license is not set.",
}


def normalize(schema, params):
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ValueError("recipe params must be a JSON object")
    result = {key: deepcopy(value["default"]) for key, value in schema["properties"].items()
              if "default" in value}
    result.update(deepcopy(params))
    errors = sorted(Draft202012Validator(schema).iter_errors(result), key=lambda e: str(e.json_path))
    if errors:
        raise ValueError(f"recipe params {errors[0].json_path}: {errors[0].message}")
    finite(result)
    # JSON Schema considers integral floats integers; normalize seeds/counts so
    # loops and serialized snapshots have one representation.
    def clean(value, spec):
        if spec.get("type") == "integer":
            return int(value)
        if spec.get("type") == "array":
            return [clean(v, spec.get("items", {})) for v in value]
        if spec.get("type") == "object":
            return {k: clean(v, spec.get("properties", {}).get(k, {})) for k, v in value.items()}
        return value
    return clean(result, schema)


def namespace(instance):
    if not isinstance(instance, str) or not ID.fullmatch(instance) or instance.startswith("sf-"):
        raise ValueError("instance_id must be a nonreserved Scene ID of at most 100 characters")
    digest = hashlib.sha256(instance.encode("utf-8")).hexdigest()[:16]
    return f"recipe.{instance[:32]}.{digest}"


def fmt(value):
    # Enough significant digits for geometry, independent of locale.
    return format(0 if abs(value) < 1e-12 else value, ".12g")


def poly(points, closed=False):
    return "M" + " L".join(f"{fmt(x)} {fmt(y)}" for x, y in points) + (" Z" if closed else "")


def ellipse_path(cx, cy, rx, ry):
    return (f"M{fmt(cx-rx)} {fmt(cy)} A{fmt(rx)} {fmt(ry)} 0 1 0 {fmt(cx+rx)} {fmt(cy)} "
            f"A{fmt(rx)} {fmt(ry)} 0 1 0 {fmt(cx-rx)} {fmt(cy)} Z")


def line_metrics(text, size):
    box = text_bounds({"x": 0, "y": 0, "text": text}, {"font_size": size})
    return box, box[2] - box[0], box[3] - box[1]


class Fragment:
    """Temporary local part tree; has no reference to the mutable builder."""

    def __init__(self, instance, matrix):
        self.instance_id = instance
        self.scene_version = None
        self.prefix = namespace(instance)
        if matrix is None:
            matrix = list(IDENTITY)
        if not isinstance(matrix, (list, tuple)) or len(matrix) != 6:
            raise ValueError("transform must contain six affine coefficients")
        for value in matrix:
            number(value, "recipe transform")
        inverse(matrix)
        self.objects = []
        self.resources = []
        self.occlusions = []
        self.parts = {}
        self.resource_ids = {}
        self.ports = {}
        self.dependencies = []
        self.add("root", "group", transform=list(matrix))

    def oid(self, key):
        value = self.prefix + "." + key
        if not ID.fullmatch(value):
            raise ValueError("recipe part key exceeds Scene ID budget")
        return value

    def add(self, key, kind, geometry=None, *, parent="root", style=None, **fields):
        if key in self.parts:
            raise ValueError("duplicate recipe part key")
        obj = {"id": self.oid(key), "kind": kind, **fields}
        if key != "root":
            obj["parent"] = self.parts[parent]
        if geometry is not None:
            obj["geometry"] = geometry
        if style is not None:
            obj["style"] = deepcopy(style)
        self.parts[key] = obj["id"]
        self.objects.append(obj)
        return obj["id"]

    def group(self, key, **options):
        return self.add(key, "group", **options)

    def path(self, key, d, fill="#6286a6", *, stroke="none", width=1, **options):
        return self.add(key, "path", {"d": d}, style={"fill": fill, "stroke": stroke,
                        "stroke_width": width}, **options)

    def rect(self, key, x, y, width, height, fill="#e6edf3", *, radius=0, **options):
        geometry = dict(x=x, y=y, width=width, height=height)
        if radius:
            geometry["rx"] = radius
        return self.add(key, "rect", geometry, style={"fill": fill}, **options)

    def ellipse(self, key, x, y, rx, ry, fill="#6286a6", **options):
        return self.add(key, "ellipse", dict(cx=x, cy=y, rx=rx, ry=ry), style={"fill": fill}, **options)

    def text(self, key, x, y, text, size, **options):
        return self.add(key, "text", dict(x=x, y=y, text=text),
                        style={"fill": "#253442", "font_size": size,
                               "font_family": font_info()["family"]}, **options)

    def gradient(self, key, colors):
        rid = self.oid("resource." + key)
        self.resource_ids[key] = rid
        self.resources.append({"id": rid, "kind": "linear_gradient", "start": [0, 0],
                               "end": [1, 1], "stops": [[i/(len(colors)-1), c] for i, c in enumerate(colors)]})
        return f"url(#{rid})"

    def depend(self, target, sources, relation="geometry"):
        self.dependencies.append({"target": self.parts[target],
                                  "sources": [self.parts[key] for key in sources], "kind": relation})

    def bind(self, bindings):
        if bindings is None:
            return
        if not isinstance(bindings, dict):
            raise ValueError("style_bindings must map part.fill or part.stroke to concrete paints")
        nodes = {obj["id"]: obj for obj in self.objects}
        for binding, paint in bindings.items():
            if not isinstance(binding, str) or "." not in binding:
                raise ValueError("style binding requires part.fill or part.stroke")
            key, field = binding.rsplit(".", 1)
            if key not in self.parts or key == "root" or field not in {"fill", "stroke"}:
                raise ValueError(f"unsupported style binding: {binding}")
            if nodes[self.parts[key]]["kind"] == "group":
                raise ValueError("bind concrete leaf parts, not groups")
            if not isinstance(paint, str) or not COLOR.fullmatch(paint):
                raise ValueError("style tokens require an explicit profile resolver; pass a concrete Scene color")
            nodes[self.parts[key]].setdefault("style", {})[field] = paint

    def commit(self, builder, scene_version):
        self.scene_version = scene_version
        if len(self.objects) > 1000 or len(self.resources) > 32 or len(self.occlusions) > 32:
            raise ValueError("recipe fragment budget exceeded")
        if scene_version == "0.3":
            self.objects[0]["anchors"] = deepcopy(self.ports)
        for obj in self.objects:
            for value in obj.get("style", {}).values():
                match = REF.fullmatch(value) if isinstance(value, str) else None
                if match:
                    self.dependencies.append({"target": obj["id"], "sources": [match[1]], "kind": "paint_resource"})
        if "parent" in self.objects[0]:
            self.dependencies.append({"target": self.parts["root"],
                                      "sources": [self.objects[0]["parent"]], "kind": "parent"})
        builder.add_many(self.objects, resources=self.resources, occlusions=self.occlusions)

    def result(self, recipe_id, params, bindings, limitations):
        return {"recipe_id": recipe_id, "recipe": recipe_id, "version": VERSION,
                "instance_id": self.instance_id, "root_id": self.parts["root"],
                "part_ids": dict(self.parts), "resource_ids": dict(self.resource_ids),
                "ports": {name: {"object_id": self.parts["root"],
                                  "anchor": "port:" + name if self.scene_version == "0.3" else None,
                                  "persistent": self.scene_version == "0.3",
                                  "position": list(xy), "space": "root_local"} for name, xy in self.ports.items()},
                "dependencies": deepcopy(self.dependencies),
                "font_dependencies": [{key: font_info()[key] for key in ("family", "sha256")}]
                    if any(obj["kind"] == "text" for obj in self.objects) else [],
                "params": deepcopy(params), "normalized_params": deepcopy(params),
                "transform": list(self.objects[0]["transform"]), "parent": self.objects[0].get("parent"),
                "style_bindings": deepcopy(bindings or {}), "limitations": list(limitations),
                "source": deepcopy(SOURCE), "budgets": {"objects": len(self.objects),
                "resources": len(self.resources), "occlusions": len(self.occlusions)},
                "occlusion_ids": [r["id"] for r in self.occlusions]}

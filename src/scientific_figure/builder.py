"""Transactional authoring of ordinary Scenes with explicit stable IDs.

Single additions retain immediate validation; add_many validates a closed batch
once. Helper anchors remain ephemeral; Scene0.3 ports are persisted explicitly.
Cloning copies subtrees, resources and relations without sharing mutable state.
"""
from __future__ import annotations

from copy import deepcopy

from .geometry import Geometry, IDENTITY, inverse, multiply, point
from .scene import REF, number, validate

_KEEP = object()


class SceneBuilder:
    def __init__(self, width, height, *, background="none", schema_version="0.1"):
        self._scene = validate({"schema_version": schema_version, "revision": 0,
                                "canvas": {"width": width, "height": height,
                                           "background": background},
                                "objects": [], "resources": []})
        self._anchors = {}

    def build(self):
        """Return a validated, independent snapshot, with no helper-only fields."""
        return validate(deepcopy(self._scene))

    def _commit(self, objects=(), resources=(), occlusions=()):
        candidate = deepcopy(self._scene)
        candidate["objects"].extend(deepcopy(objects))
        candidate["resources"].extend(deepcopy(resources))
        if occlusions:
            candidate.setdefault("occlusions", []).extend(deepcopy(occlusions))
        validate(candidate)
        self._scene = candidate

    def add_many(self, objects, *, resources=(), occlusions=()):
        """Atomically add a closed fragment, including batch-local references.

        Inputs are copied. Invalid geometry, IDs, references or budgets leave the
        builder unchanged. This does not change single-add validation semantics.
        """
        if not all(isinstance(items, (list, tuple)) for items in (objects, resources, occlusions)):
            raise ValueError("batch collections must be lists or tuples")
        self._commit(objects, resources, occlusions)
        return [obj["id"] for obj in objects]

    def port(self, oid, name, xy):
        """Persist a named local connection port (Scene0.3 only)."""
        if self._scene["schema_version"] != "0.3":
            raise ValueError("persistent ports require Scene0.3")
        candidate = deepcopy(self._scene)
        nodes = {obj["id"]: obj for obj in candidate["objects"]}
        if oid not in nodes:
            raise ValueError("port owner must exist")
        ports = nodes[oid].setdefault("anchors", {})
        if name in ports:
            raise ValueError("duplicate port")
        ports[name] = list(xy)
        validate(candidate)
        self._scene = candidate
        return "port:" + name

    def add(self, oid, kind, geometry=None, *, parent=None, transform=None,
            style=None, semantic=None, visible=True, clip=None, mask=None, anchors=None):
        """Construct any engine primitive; return its ID. Inputs are copied."""
        obj = {"id": oid, "kind": kind, "visible": visible}
        if geometry is not None:
            obj["geometry"] = geometry
        if parent is not None:
            obj["parent"] = parent
        for key, value in (("transform", transform), ("style", style), ("semantic", semantic), ("clip", clip), ("mask", mask), ("anchors", anchors)):
            if value is not None:
                obj[key] = value
        self._commit([obj])
        return oid

    def group(self, oid, **options):
        return self.add(oid, "group", **options)

    def rect(self, oid, x, y, width, height, **options):
        return self.add(oid, "rect", {"x": x, "y": y, "width": width, "height": height}, **options)

    def ellipse(self, oid, cx, cy, rx, ry, **options):
        return self.add(oid, "ellipse", {"cx": cx, "cy": cy, "rx": rx, "ry": ry}, **options)

    def path(self, oid, d, **options):
        """Add a path; explicit empty geometry is omitted and returns None."""
        if d == "":
            return None
        return self.add(oid, "path", {"d": d}, **options)

    def text(self, oid, x, y, text, **options):
        return self.add(oid, "text", {"x": x, "y": y, "text": text}, **options)

    def resource(self, rid, kind, **data):
        """Add a resource supported by the builder's Scene version; return ID."""
        self._commit(resources=[{**data, "id": rid, "kind": kind}])
        return rid

    def occlusion(self, rid, over, under, *, frame, region, gap=0):
        """Declare a Scene0.2 local relation; operands are evaluated from source."""
        self._commit(occlusions=[{"id": rid, "over": over, "under": under,
                                 "frame": frame, "region": region, "gap": gap}])
        return rid

    def anchor(self, oid, name, xy):
        """Define an explicit local anchor on an existing node; return its name."""
        if oid not in Geometry(self._scene).nodes:
            raise ValueError("anchor owner must exist")
        if not isinstance(name, str) or not name or (oid, name) in self._anchors:
            raise ValueError("anchor name must be nonempty and unique on its owner")
        if not isinstance(xy, (list, tuple)) or len(xy) != 2:
            raise ValueError("anchor requires two coordinates")
        for value in xy:
            number(value, "anchor coordinate")
        self._anchors[oid, name] = tuple(xy)
        return name

    def anchor_point(self, oid, name, *, relative_to=None):
        """Resolve an explicit anchor in world space or another node's space."""
        geometry = Geometry(self._scene)
        try:
            xy = self._anchors[oid, name]
            matrix = geometry.world(oid)
            if relative_to is not None:
                matrix = multiply(inverse(geometry.world(relative_to)), matrix)
        except KeyError as exc:
            raise ValueError("unknown node or explicit anchor") from exc
        return point(matrix, *xy)

    def transform(self, oid, matrix, *, compose=False):
        """Set a node transform, or compose old @ matrix in local space."""
        candidate = self.build()
        nodes = {obj["id"]: obj for obj in candidate["objects"]}
        if oid not in nodes:
            raise ValueError("unknown node")
        if not isinstance(matrix, (list, tuple)) or len(matrix) != 6:
            raise ValueError("transform requires six coefficients")
        for value in matrix:
            number(value, "transform coefficient")
        inverse(matrix)
        obj = nodes[oid]
        obj["transform"] = multiply(obj.get("transform", IDENTITY), matrix) if compose else list(matrix)
        validate(candidate)
        self._scene = candidate
        return oid

    def clone(self, oid, ids, *, resource_ids=None, occlusion_ids=None, parent=_KEEP, transform=None):
        """Independently copy a subtree and its explicit resources.

        ids must map every subtree node to a new ID; resource_ids must map every
        paint or typed semantic resource reference to a new ID. Internal connector
        and typed semantic object references are remapped; references to objects
        outside the subtree are rejected. Semantic references follow composition's
        existing contract; ordinary strings are never guessed to be IDs.
        The root retains its original parent/transform unless overridden.
        Anchors are copied too; no source dictionary is shared with the clone.
        """
        from .composition import _semantic

        geometry = Geometry(self._scene)
        if oid not in geometry.nodes:
            raise ValueError("unknown clone root")
        selected = set()

        def collect(node):
            selected.add(node)
            for child in geometry.children.get(node, []):
                collect(child)

        collect(oid)
        if not isinstance(ids, dict) or set(ids) != selected:
            raise ValueError("ids must map the complete subtree")
        objects = deepcopy([obj for obj in self._scene["objects"] if obj["id"] in selected])
        referenced = set()

        class ResourceReferences(dict):
            def __getitem__(self, key):
                referenced.add(key)
                return super().__getitem__(key)

        # Discover semantic-only resources through the same resolver that remaps
        # them, preserving composition's typed-reference rules and validation.
        resource_refs = ResourceReferences((r["id"], r["id"]) for r in self._scene["resources"])
        for obj in objects:
            if "semantic" in obj:
                _semantic(obj["semantic"], ids, resource_refs)
            for value in obj.get("style", {}).values():
                match = REF.fullmatch(value) if isinstance(value, str) else None
                if match:
                    referenced.add(match[1])
            for effect in ("clip", "mask"):
                if effect in obj:
                    referenced.add(obj[effect])
        resource_ids = {} if resource_ids is None else resource_ids
        if not isinstance(resource_ids, dict) or set(resource_ids) != referenced:
            raise ValueError("resource_ids must map all explicitly referenced resources")
        resources = deepcopy([r for r in self._scene["resources"] if r["id"] in referenced])
        for resource in resources:
            resource["id"] = resource_ids[resource["id"]]
        for obj in objects:
            original_id = obj["id"]
            obj["id"] = ids[original_id]
            if "semantic" in obj:
                obj["semantic"] = _semantic(obj["semantic"], ids, resource_ids)
            if obj.get("parent") in selected:
                obj["parent"] = ids[obj["parent"]]
            if original_id == oid:
                if parent is not _KEEP:
                    obj.pop("parent", None)
                    if parent is not None:
                        obj["parent"] = parent
                if transform is not None:
                    obj["transform"] = list(transform)
            for key, value in obj.get("style", {}).items():
                match = REF.fullmatch(value) if isinstance(value, str) else None
                if match:
                    obj["style"][key] = f"url(#{resource_ids[match[1]]})"
            for effect in ("clip", "mask"):
                if effect in obj:
                    obj[effect] = resource_ids[obj[effect]]
            if obj["kind"] == "connector":
                for key in ("from", "to"):
                    target = obj["geometry"][key]
                    if target not in selected:
                        raise ValueError("clone connector endpoints must be inside the subtree")
                    obj["geometry"][key] = ids[target]
        relations = deepcopy([r for r in self._scene.get("occlusions", [])
                              if {r["over"], r["under"], r["frame"]} & selected])
        occlusion_ids = {} if occlusion_ids is None else occlusion_ids
        if not isinstance(occlusion_ids, dict) or set(occlusion_ids) != {r["id"] for r in relations}:
            raise ValueError("occlusion_ids must map all subtree relations")
        for relation in relations:
            if not {relation["over"], relation["under"], relation["frame"]} <= selected:
                raise ValueError("clone occlusion requires operands and frame inside subtree")
            relation["id"] = occlusion_ids[relation["id"]]
            for key in ("over", "under", "frame"):
                relation[key] = ids[relation[key]]
        self._commit(objects, resources, relations)
        self._anchors.update({(ids[node], name): xy for (node, name), xy
                              in list(self._anchors.items()) if node in selected})
        return ids[oid]

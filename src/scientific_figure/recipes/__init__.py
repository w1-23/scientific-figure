"""Deterministic, versioned scientific drawing recipes.

``describe(id)`` exposes the strict parameter schema and construction limits;
``build(builder, recipe_id=..., instance_id=..., params=...)`` adds one ordinary,
editable fragment atomically. Current Scene geometry remains authoritative after
editing. Returned inputs/dependencies are snapshots for explicit regeneration,
not a second scene or an automatic template-update service.
"""
from copy import deepcopy

from ..scene import ID
from .base import Fragment, VERSION, normalize
from .catalog import CATALOG
from .families import BUILDERS

__all__ = ["list", "list_recipes", "describe", "build"]


def _version(version):
    if version != VERSION:
        raise ValueError(f"unsupported recipe version {version!r}; supported version is {VERSION}")


def list(*, version=VERSION):
    """Return an ordered, independent JSON-friendly list of built-in entries."""
    _version(version)
    fields = ("id", "kind", "version", "summary", "tier", "status", "capabilities", "source")
    return [{key: deepcopy(spec[key]) for key in fields} for spec in CATALOG.values()]


list_recipes = list


def describe(recipe_id, *, version=VERSION):
    """Return schema, defaults, ranges, part/port contract, source and limits."""
    _version(version)
    if not isinstance(recipe_id, str) or recipe_id not in CATALOG:
        raise ValueError(f"unknown built-in recipe: {recipe_id!r}")
    return deepcopy(CATALOG[recipe_id])


def build(builder, *, recipe_id, instance_id, params=None, version=VERSION,
          transform=None, style_bindings=None, parent=None):
    """Build local geometry, then call ``builder.add_many`` exactly once.

    Only concrete paint overrides on existing leaf parts are accepted. Resolve
    StyleProfile tokens explicitly before calling this API. No Python module,
    expression, path, or arbitrary callable is accepted from request parameters.
    Scene0.3 persists ports on the root; older Scenes return helper points only.
    Invalid input or a rejected final batch leaves the builder unchanged.
    """
    spec = describe(recipe_id, version=version)
    normalized = normalize(spec["params_schema"], params)
    scene_version = builder.build()["schema_version"]
    if scene_version not in {"0.1", "0.2", "0.3"}:
        raise ValueError("unsupported Scene version")
    if recipe_id == "interwoven_bands" and scene_version == "0.1":
        raise ValueError("interwoven_bands requires Scene0.2 or later for explicit occlusions")
    fragment = Fragment(instance_id, transform)
    if parent is not None:
        if not isinstance(parent, str) or not ID.fullmatch(parent):
            raise ValueError("parent must be an existing group ID")
        fragment.objects[0]["parent"] = parent
    BUILDERS[recipe_id](fragment, normalized)
    fragment.bind(style_bindings)
    fragment.commit(builder, scene_version)
    return fragment.result(recipe_id, normalized, style_bindings, spec["limitations"])

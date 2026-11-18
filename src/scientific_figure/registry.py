"""Fixed local recipe/family/vector catalog with explicit provenance and pins.

Only the bundled Python recipe mapping can describe executable recipes. Registry
JSON is data: it never imports code, downloads URLs, resolves floating versions,
or silently normalizes unsupported SVG. External vectors use the existing strict
SVG importer after checking the exact distributed bytes and license-text digest.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from importlib.resources import files
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from . import recipes
from .scene import schema_validate, validate
from .svg_import import MAX_BYTES as SVG_MAX_BYTES, import_svg

__all__ = ["list_entries", "resolve", "describe", "load_asset", "validate_registry", "search"]

_HASH = re.compile(r"^[0-9a-f]{64}$")
_COMPONENT = re.compile(r"^[A-Za-z0-9_.-]+$")
_RESERVED = {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)],
             *[f"LPT{i}" for i in range(1, 10)]}
_MANIFEST_MAX = 1_048_576
_LICENSE_MAX = 131_072


def _linked(path):
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _relative(name):
    if not isinstance(name, str) or not name or len(name) > 240:
        raise ValueError("registry file paths require bounded relative names")
    parts = name.split("/")
    if any(not _COMPONENT.fullmatch(part) or part in {".", ".."} or part.endswith(".")
           or part.split(".")[0].upper() in _RESERVED for part in parts):
        raise ValueError("registry file paths must be safe relative paths without traversal, drives or device names")
    return parts


def _location(registry):
    if registry is None:
        return files("scientific_figure").joinpath("data", "registry"), "catalog.json"
    if not isinstance(registry, (str, Path)):
        raise ValueError("registry must be a local catalog file or directory path")
    path = Path(registry)
    try:
        if _linked(path):
            raise ValueError("registry path must not be a symlink or junction")
        if path.is_dir():
            return path.resolve(strict=True), "catalog.json"
        if path.is_file():
            return path.parent.resolve(strict=True), path.name
    except OSError as exc:
        raise ValueError("cannot open registry location") from exc
    raise ValueError("registry must be an existing catalog file or directory")


def _file(root, name):
    parts = _relative(name)
    target = root
    try:
        for component in parts:
            target = target.joinpath(component)
            if isinstance(target, Path) and _linked(target):
                raise ValueError("registry files must not use symlinks or junctions")
        if isinstance(target, Path):
            # Resolve containment as well as checking every component, including
            # junctions on Windows. The caller-selected root is the trust scope.
            if not target.resolve(strict=True).is_relative_to(root.resolve(strict=True)):
                raise ValueError("registry file escapes its root")
        if not target.is_file():
            raise ValueError("registry file must be a regular file")
    except OSError as exc:
        raise ValueError(f"missing or unreadable registry file: {name}") from exc
    return target


def _read(root, name, limit):
    target = _file(root, name)
    try:
        with target.open("rb") as stream:
            data = stream.read(limit+1)
    except OSError as exc:
        raise ValueError(f"cannot read registry file: {name}") from exc
    if len(data) > limit:
        raise ValueError(f"registry file exceeds byte budget: {name}")
    return data


def _json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate registry JSON key: {key}")
            result[key] = value
        return result
    def nonfinite(value):
        raise ValueError(f"nonfinite registry JSON value: {value}")
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=unique, parse_constant=nonfinite)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("registry catalog must be valid UTF-8 JSON") from exc


def _recipe_entry(recipe_id, version):
    spec = recipes.describe(recipe_id, version=version)
    return {
        "id": recipe_id, "kind": "recipe", "version": version,
        "title": recipe_id.replace("_", " ").title(), "description": spec["summary"],
        "semantic_scope": deepcopy(spec["suitable_for"]), "limitations": deepcopy(spec["limitations"]),
        "source": {"url": spec["source"]["url"], "ref": version,
                   "author": spec["source"]["author"], "original_sha256": None},
        "license": {"identifier": spec["source"]["license"], "url": None,
                    "text_path": None, "text_sha256": None, "notice": spec["source"]["notice"],
                    "attribution": spec["source"]["author"]+"; original geometric construction."},
        "distribution": {"scope": "project_license_unset", "conditions": ["Project public distribution license is not set"]},
        "modifications": [], "protected": {"colors": [], "geometry": False, "meaning": []},
        "capabilities": {"minimum_scene_version": spec["capabilities"]["minimum_scene_version"], "importer": "none"},
        "recipe_id": recipe_id,
    }


def _catalog(registry):
    root, name = _location(registry)
    data = _json(_read(root, name, _MANIFEST_MAX))
    schema_validate(data, "registry")
    entries = deepcopy(data["entries"])
    if data.get("include_builtin_recipes", False):
        entries.extend(_recipe_entry(item["id"], item["version"]) for item in recipes.list_recipes())
    # Validate the synthesized records too. The manifest itself is limited to64
    # entries, plus the small trusted recipe catalog; no dynamic modules exist.
    for entry in entries:
        schema_validate({"schema_version": "0.1", "entries": [entry]}, "registry")
    seen, kinds = set(), {}
    for entry in entries:
        key = entry["id"], entry["version"]
        if key in seen:
            raise ValueError("duplicate registry id/version")
        seen.add(key)
        if entry["id"] in kinds and kinds[entry["id"]] != entry["kind"]:
            raise ValueError("a registry ID cannot change kind between versions")
        kinds[entry["id"]] = entry["kind"]
        if entry["kind"] == "recipe":
            spec = recipes.describe(entry["recipe_id"], version=entry["version"])
            if (entry["license"]["identifier"] != spec["source"]["license"]
                    or entry["distribution"]["scope"] != "project_license_unset"):
                raise ValueError("original recipe license must remain project_license_unset")
        for recipe_id in entry.get("related_recipes", []):
            recipes.describe(recipe_id)
        for file_name in [entry.get("asset", {}).get("path"), entry["license"]["text_path"]]:
            if file_name is not None:
                _relative(file_name)
    return root, sorted(entries, key=lambda item: (item["kind"], item["id"], item["version"]))


def list_entries(registry=None):
    """List independent metadata records; this does not execute or import assets.

    ``registry`` is None for the package catalog, or a directory containing
    catalog.json, or an explicit local JSON file. Custom catalogs use the same
    strict schema and may only refer to local relative files under their root.
    """
    return deepcopy(_catalog(registry)[1])


def _select(entries, asset_id, version):
    if not isinstance(asset_id, str):
        raise ValueError("registry id must be a string")
    matches = [entry for entry in entries if entry["id"] == asset_id]
    if version is not None:
        if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
            raise ValueError("registry version must be a fixed numeric version, not latest")
        matches = [entry for entry in matches if entry["version"] == version]
    if len(matches) != 1:
        raise ValueError("unknown registry id/version or ambiguous version; specify a fixed version")
    return matches[0]


def _verified(root, entry):
    asset_data = None
    if "asset" in entry:
        asset = entry["asset"]
        asset_data = _read(root, asset["path"], SVG_MAX_BYTES)
        if hashlib.sha256(asset_data).hexdigest() != asset["sha256"]:
            raise ValueError("registry distributed asset SHA256 mismatch")
    license_spec = entry["license"]
    if license_spec["text_path"] is not None:
        text = _read(root, license_spec["text_path"], _LICENSE_MAX)
        if hashlib.sha256(text).hexdigest() != license_spec["text_sha256"]:
            raise ValueError("registry license text SHA256 mismatch")
    return asset_data


def resolve(asset_id, *, version=None, registry=None, expected_sha256=None):
    """Resolve one fixed entry and verify its local asset and license hashes.

    An omitted version is accepted only for an unambiguous single installed
    version. ``expected_sha256`` pins the distributed SVG bytes, never the
    recorded upstream original SHA. Source URLs are attribution data, not fetch
    instructions. The absent upstream original is not re-verified here.
    """
    root, entries = _catalog(registry)
    entry = _select(entries, asset_id, version)
    if expected_sha256 is not None:
        if not isinstance(expected_sha256, str) or not _HASH.fullmatch(expected_sha256):
            raise ValueError("expected_sha256 must be a lowercase 64-character SHA256")
        if "asset" not in entry or expected_sha256 != entry["asset"]["sha256"]:
            raise ValueError("registry expected distributed SHA256 mismatch")
    _verified(root, entry)
    return deepcopy(entry)


def describe(asset_id, *, version=None, registry=None):
    """Resolve metadata, with the trusted full contract for recipe entries."""
    entry = resolve(asset_id, version=version, registry=registry)
    if entry["kind"] == "recipe":
        entry["recipe_spec"] = recipes.describe(entry["recipe_id"], version=entry["version"])
    return entry


def load_asset(entry, *, registry=None):
    """Import a resolved external vector as an independent validated Scene.

    The provided record must still match its selected catalog entry. Verified
    bytes are copied to a private temporary file because the existing importer
    accepts a path; import never reopens an unchecked registry asset. Temporary
    paths are not stored in the Scene. No local Python or URL is ever executed.
    """
    if not isinstance(entry, dict) or entry.get("kind") != "external_asset":
        raise ValueError("load_asset requires an external_asset entry, not a recipe or symbol family")
    root, entries = _catalog(registry)
    current = _select(entries, entry.get("id"), entry.get("version"))
    if current != entry:
        raise ValueError("registry entry was changed or does not match this catalog")
    data = _verified(root, current)
    with TemporaryDirectory(prefix="scientific-figure-registry-") as directory:
        source = Path(directory) / "verified.svg"
        source.write_bytes(data)
        scene = import_svg(source)
    origin = {"id": current["id"], "version": current["version"],
              "distributed_sha256": current["asset"]["sha256"],
              "original_sha256_status": "recorded_upstream_not_distributed",
              "entry": deepcopy(current)}
    scene.setdefault("provenance", {})["registry_asset"] = deepcopy(origin)
    # Selected exports retain the ancestor tree but replace top-level provenance.
    # Keep the attribution on the imported root too, so a selected path remains
    # accompanied by creator/license/source/modification metadata when reopened
    # or composed using the existing semantic-data preservation contract.
    for obj in scene["objects"]:
        if not obj.get("parent"):
            obj.setdefault("semantic", {})["registry_asset"] = deepcopy(origin)
    return validate(scene)


# Aliases identify existing entries only; they never register a new resource.
_SEARCH_ALIASES = {
    "arrow": ("箭头",), "flow_node": ("流程节点",), "legend": ("图例",),
    "zoom_callout": ("局部放大",), "leaf": ("叶片", "叶子"),
    "cell_symbol": ("cell", "细胞",), "vessel_segment": ("血管",),
    "particle": ("nanoparticle", "纳米颗粒", "粒子"), "loop_band": ("环带",),
    "interwoven_bands": ("交织带",), "fruit": ("水果",),
    "simple_microbe": ("微生物",), "lab_lamp": ("实验灯",),
    "lab_book": ("实验记录本",), "cluster": ("簇",),
    "twemoji.lemon": ("柠檬",), "family.fruit": ("水果",),
    "family.cell_symbol": ("细胞",),
}
_PART_ALIASES = {"细胞膜": "membrane", "细胞质": "cytoplasm", "细胞核": "nucleus",
                 "外壳": "shell", "核心": "core", "叶片": "blade", "叶主脉": "midrib"}
_SEARCH_FIELDS = {"id": 8, "title": 5, "description": 3, "semantic_scope": 3,
                  "capabilities": 1, "limitations": 1}


def _search_request(request):
    # Validate the flat JSON contract before touching the catalog or doing work.
    allowed = {"query", "required_parts", "style", "scene_version", "limit"}
    if type(request) is not dict or any(type(key) is not str for key in request):
        raise ValueError("search request must be a JSON object")
    if set(request) - allowed:
        raise ValueError("unsupported search request fields")
    query = request.get("query")
    if type(query) is not str or not query.strip() or len(query) > 512:
        raise ValueError("search query requires a nonempty string of at most 512 characters")
    parts = request.get("required_parts", [])
    if (type(parts) is not list or len(parts) > 32
            or any(type(part) is not str or not part.strip() or len(part) > 100 for part in parts)):
        raise ValueError("required_parts requires at most 32 nonempty strings of at most 100 characters")
    if "style" in request and (type(request["style"]) is not str
                               or not request["style"].strip() or len(request["style"]) > 128):
        raise ValueError("style requires a nonempty string of at most 128 characters")
    if "scene_version" in request and (type(request["scene_version"]) is not str
                                       or request["scene_version"] not in {"0.1", "0.2", "0.3"}):
        raise ValueError("scene_version must be 0.1, 0.2 or 0.3")
    limit = request.get("limit", 5)
    if type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError("search limit must be an integer from 1 to 10")
    try:
        size = len(json.dumps(request, ensure_ascii=False).encode("utf-8"))
    except UnicodeError as exc:
        raise ValueError("search request must contain valid Unicode") from exc
    if size > 8192:
        raise ValueError("search request exceeds 8192 byte budget")
    terms = _search_terms(query)
    if len(terms) > 64:
        raise ValueError("search query exceeds 64 term budget")
    return terms, parts, limit


def _search_terms(text):
    # Whole Latin words prevent 'rat' matching 'illustration'. Chinese aliases
    # below allow a small known vocabulary inside unsegmented Chinese queries.
    return sorted(set(re.findall(r"[a-z0-9]+|[㐀-鿿]+", text.casefold())))


def _search_text(value):
    if isinstance(value, dict):
        return " ".join(str(key) + " " + _search_text(item) for key, item in value.items())
    if isinstance(value, list):
        return " ".join(_search_text(item) for item in value)
    return str(value) if value is not None else ""


def _search_matches(entry, spec, query, terms):
    fields = {key: _search_text(entry[key]) for key in _SEARCH_FIELDS}
    weights = dict(_SEARCH_FIELDS)
    if spec is not None:
        for key in ("params_schema", "part_keys", "ports"):
            fields["recipe_spec." + key] = _search_text(spec[key])
            weights["recipe_spec." + key] = 2
    basis = []
    for field, text in fields.items():
        words = set(_search_terms(text))
        for term in terms:
            if term in words:
                basis.append({"term": term, "field": field, "value": term})
    for alias in _SEARCH_ALIASES.get(entry["id"], ()):
        if alias in terms or (re.search(r"[㐀-鿿]", alias) and alias in query):
            basis.append({"term": alias, "field": "alias", "value": entry["id"]})
    weights["alias"] = 6
    score = sum(weights[item["field"]] for item in basis)
    if query.strip().casefold() == entry["id"].casefold():
        score += 20
    return basis, score


def _search_parts(spec, required):
    coverage, missing = [], []
    for part in required:
        name = _PART_ALIASES.get(part.strip(), part.strip().casefold())
        matched = None
        for key in spec["part_keys"] if spec is not None else ():
            bare = key.removesuffix(" (optional)").casefold()
            pattern = re.escape(bare).replace(r"\.n", r"\.[0-9]+")
            if name == bare or re.fullmatch(pattern, name):
                matched = key
                break
        coverage.append({"part": part, "part_key": matched,
                         "status": "declared_requires_params" if matched is not None else "not_declared"})
        if matched is None:
            missing.append(part)
    return coverage, missing


def search(request, *, registry=None):
    """Search fixed local metadata; return candidates, never select or build one.

    Request is a JSON object with query (1..512 characters), optional
    required_parts (<=32 strings, each 1..100 characters), style (1..128),
    scene_version (0.1/0.2/0.3), and limit (integer 1..10, default 5).
    Total UTF-8 JSON is limited to 8192 bytes and query to 64 unique terms.
    Unknown fields, Python-only values and invalid types raise ValueError.

    Query terms alone admit entries. Required parts and style cannot manufacture
    a query hit. Scores rank lexical matches only, including explicit limitation
    matches. Recipe parts are declared potential parts: their actual presence
    depends on parameters and constraints in recipe_spec. Assets and families
    have no inferred semantic part contract. Files/hashes of matching entries are
    verified; SVG import and arbitrary source execution are never performed.

    Returns candidates with the original entry fields plus match_basis,
    matched_terms, retrieval_score, missing_parts, part_coverage, fit and fit
    reasons. Fit is compatible/partial/unsupported/needs_review/metadata_only;
    compatibility covers declared metadata only. Recipe candidates include the
    full recipe_spec. Asset candidates are enriched metadata: call resolve with
    their fixed id/version before passing the resolved entry to load_asset.
    A suggested program fallback is advisory and never executes or selects it.
    """
    terms, required, limit = _search_request(request)
    root, entries = _catalog(registry)
    candidates = []
    for entry in entries:
        spec = (recipes.describe(entry["recipe_id"], version=entry["version"])
                if entry["kind"] == "recipe" else None)
        basis, score = _search_matches(entry, spec, request["query"], terms)
        if not basis:
            continue
        _verified(root, entry)
        candidate = deepcopy(entry)
        if spec is not None:
            candidate["recipe_spec"] = spec
        coverage, missing = _search_parts(spec, required)
        minimum = entry["capabilities"]["minimum_scene_version"]
        scene_fit = ("unknown" if "scene_version" not in request else
                     "compatible" if request["scene_version"] >= minimum else "unsupported")
        # No style taxonomy is declared by the catalog. Lexical hits do not
        # certify stylistic suitability; preserve the host's review obligation.
        style_fit = "unknown" if "style" in request else "not_requested"
        reasons = []
        if entry["kind"] == "symbol_family":
            fit = "metadata_only"
            reasons.append("Family metadata is not a constructible asset or recipe.")
        elif scene_fit == "unsupported":
            fit = "unsupported"
            reasons.append("Target Scene version is below the declared minimum.")
        elif missing:
            fit = "partial"
            reasons.append("Required parts are not declared; use explicit program construction for missing structure.")
        elif style_fit == "unknown" or all(item["field"] == "limitations" for item in basis):
            fit = "needs_review"
        else:
            fit = "compatible"
        if required and spec is not None:
            reasons.append("Declared parts require explicit parameters and constraints; no defaults were chosen or geometry built.")
        if "style" in request:
            reasons.append("Requested style has no declared compatibility contract; host review is required.")
        candidate.update({"matched_terms": sorted({item["term"] for item in basis}),
                          "match_basis": basis, "retrieval_score": score,
                          "missing_parts": missing, "part_coverage": coverage,
                          "fit": fit, "fit_reasons": reasons,
                          "scene_fit": scene_fit, "style_fit": style_fit,
                          "constructible": entry["kind"] != "symbol_family",
                          "import_status": "not_run" if "asset" in entry else "not_applicable"})
        candidates.append(candidate)
    candidates.sort(key=lambda item: (-item["retrieval_score"], item["id"], item["version"], item["kind"]))
    candidates = candidates[:limit]
    fallback = None
    if not any(item["fit"] == "compatible" for item in candidates):
        fallback = {"kind": "program", "status": "suggested", "automatic": False,
                    "reason": "No returned candidate fully covers the declared request; host must explicitly choose construction and supply geometry."}
    return {"schema_version": "0.1", "query": request["query"], "candidates": candidates,
            "fallback": fallback, "selection": "host_required",
            "score_scope": "Lexical retrieval ranking only; not semantic confidence, visual fit or scientific correctness.",
            "part_scope": "Declared potential recipe parts only; asset/family semantic parts are unknown.",
            "license_scope": "Per-entry license and distribution conditions apply; project_license_unset is not public distribution permission."}


def validate_registry(registry=None):
    """Validate all records, safe files and hashes, without importing SVG or code."""
    root, entries = _catalog(registry)
    for entry in entries:
        _verified(root, entry)
    return {"schema_version": "0.1", "status": "valid", "entry_count": len(entries),
            "asset_count": sum(entry["kind"] == "external_asset" for entry in entries),
            "validation_scope": "catalog_schema_safe_local_files_and_hashes",
            "svg_import": "not_run; load_asset performs strict SVG validation",
            "original_hash_status": "Recorded source identity only; original files are not distributed."}

"""Attach strict declarative context as portable, cryptographically bound JSON.

External references are {path, sha256} (hash of actual file bytes). The returned
context.json replaces each with {document, sha256} (canonical JSON hash). Copies
of profile/text/layout documents also live at fixed flat extra names. Raster
evidence is verified once at attachment; subsequent validation needs JSON only.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from .fonts import check_text
from .geometry import Geometry
from .scene import load_json, schema_validate, validate
from .style_profile import binding_token, profile_mismatches, resolve_bindings, validate_profile

DOCUMENTS = {"style_profile": ("style-profile.json", "style-profile"),
             "text_manifest": ("text-manifest.json", "text-manifest"),
             "layout_manifest": ("layout-manifest.json", "layout-manifest")}
EXTRA_NAMES = frozenset({"context.json", *(name for name, _ in DOCUMENTS.values())})


def canonical_hash(document) -> str:
    """Hash JSON values independently of whitespace and object key order."""
    return hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _checked_file(ref, base):
    path = (base / ref["path"]).resolve()
    if _file_hash(path) != ref["sha256"]:
        raise ValueError(f"context dependency hash mismatch: {path.name}")
    return path


def _unique(records, key, label):
    ids = [record[key] for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate {label} ID")
    return set(ids)


def _refs(values, allowed, label):
    missing = set(values) - allowed
    if missing:
        raise ValueError(f"unknown {label} ID: {sorted(missing)[0]}")


def _preserved(context):
    return sorted(set(context.get("preserved_ids", [])) |
                  {entry["object_id"] for entry in context.get("objects", []) if entry.get("preserve")})


def _binding(context):
    """Typed ID sets plus the complete normalized context digest expire reviews."""
    refs = {"objects": sorted({entry["object_id"] for entry in context.get("objects", [])}
                              | {entry["object_id"] for entry in context.get("token_bindings", [])}
                              | set(context.get("preserved_ids", []))),
            "roles": sorted(entry["id"] for entry in context.get("roles", [])),
            "representations": sorted(entry["id"] for entry in context.get("representations", [])),
            "uncertainties": sorted(entry["id"] for entry in context.get("uncertainties", [])),
            "evidence": sorted(entry["id"] for entry in context.get("evidence", [])),
            "preserved": _preserved(context)}
    for key in DOCUMENTS:
        if key in context:
            doc = context[key]["document"]
            refs[key] = doc["id"]
            if key == "text_manifest":
                refs["text_entries"] = sorted(entry["id"] for entry in doc["entries"])
                refs["objects"] = sorted(set(refs["objects"]) | {e["object_id"] for e in doc["entries"]})
            elif key == "layout_manifest":
                refs["layout_entries"] = sorted(entry["id"] for entry in doc["entries"])
                refs["objects"] = sorted(set(refs["objects"]) | {oid for e in doc["entries"] for oid in e["object_ids"]})
            else:
                refs["tokens"] = sorted(token["id"] for token in doc["tokens"])
    return {"schema_version": "0.1", "id": context["id"], "sha256": canonical_hash(context), "refs": refs}


def _validate_evidence_snapshot(ref, scene):
    doc, manifest = ref["document"], ref["manifest"]
    schema_validate(doc, "evidence")
    schema_validate(manifest, "evidence-manifest")
    if "path" in doc["source"]:
        raise ValueError("context evidence snapshot contains an external source path")
    if canonical_hash(doc) != ref["sha256"] or canonical_hash(manifest) != ref["manifest_sha256"]:
        raise ValueError("context evidence hash mismatch")
    for key, value in {"created_at": doc["created_at"], "source_sha256": doc["source"]["sha256"],
                       "roi": doc["source"]["roi"], "channels": doc["channels"],
                       "parameters": doc["parameters"], "versions": doc["versions"]}.items():
        if manifest[key] != value:
            raise ValueError(f"context evidence manifest mismatch: {key}")
    records = manifest["artifacts"]
    originals = [r for r in records if r["path"] == "evidence.json"]
    if len(originals) != 1 or originals[0]["sha256"] != ref["source_document_sha256"]:
        raise ValueError("context evidence original document hash mismatch")
    if [r for r in records if r["path"] != "evidence.json"] != doc["artifacts"]:
        raise ValueError("context evidence artifact hash bindings mismatch")
    source = scene.get("provenance", {}).get("source")
    if source is not None and source.get("sha256") != doc["source"]["sha256"]:
        raise ValueError("context evidence source SHA does not match Scene source")


def _attach_evidence(ref, base, scene):
    if "path" not in ref:
        _validate_evidence_snapshot(ref, scene)
        return copy.deepcopy(ref)
    from .evidence import validate_evidence

    path = _checked_file(ref, base)
    if path.name != "evidence.json":
        raise ValueError("context evidence must reference evidence.json")
    validate_evidence(path.parent)
    document = load_json(path)
    source = document["source"]
    if "path" not in source or _file_hash(source["path"]) != source["sha256"]:
        raise ValueError("context evidence source hash mismatch at attachment")
    # The manifest binds the original bytes; the normalized copy has its own hash.
    manifest = load_json(path.parent / "manifest.json")
    document["source"].pop("path")
    result = {"id": ref["id"], "document": document, "sha256": canonical_hash(document),
              "manifest": manifest, "manifest_sha256": canonical_hash(manifest),
              "source_document_sha256": ref["sha256"]}
    _validate_evidence_snapshot(result, scene)
    return result


def _validate_documents(context, extras):
    for key, (filename, schema) in DOCUMENTS.items():
        if key not in context:
            if filename in extras:
                raise ValueError(f"unbound context extra: {filename}")
            continue
        ref = context[key]
        if "path" in ref:
            raise ValueError("context snapshot has an unresolved external document")
        doc = ref["document"]
        schema_validate(doc, schema)
        if canonical_hash(doc) != ref["sha256"]:
            raise ValueError(f"context dependency hash mismatch: {filename}")
        if filename not in extras or canonical_hash(extras[filename]) != ref["sha256"]:
            raise ValueError(f"context extra hash mismatch: {filename}")
        if key == "style_profile":
            validate_profile(doc)


def _validate_refs(scene, context):
    nodes = {obj["id"]: obj for obj in scene["objects"]}
    objects = set(nodes)
    roles = _unique(context.get("roles", []), "id", "role")
    representations = _unique(context.get("representations", []), "id", "representation")
    uncertainties = _unique(context.get("uncertainties", []), "id", "uncertainty")
    evidence = _unique(context.get("evidence", []), "id", "evidence")
    geometry = Geometry(scene)
    _unique(context.get("objects", []), "object_id", "context object")
    _refs(_preserved(context), objects, "preserved object")

    def entry_refs(entry):
        _refs(entry.get("uncertainty_ids", []), uncertainties, "uncertainty")
        _refs(entry.get("evidence_ids", []), evidence, "evidence")

    for entry in context.get("objects", []):
        _refs([entry["object_id"]], objects, "object")
        if "role_id" in entry:
            _refs([entry["role_id"]], roles, "role")
        if "representation_id" in entry:
            _refs([entry["representation_id"]], representations, "representation")
        entry_refs(entry)
    for key in ("text_manifest", "layout_manifest"):
        if key not in context:
            continue
        entries = context[key]["document"]["entries"]
        _unique(entries, "id", key + " entry")
        if key == "text_manifest":
            _unique(entries, "object_id", "text object")
        for entry in entries:
            entry_refs(entry)
            if key == "text_manifest":
                oid = entry["object_id"]
                _refs([oid], objects, "text object")
                if nodes[oid]["kind"] != "text":
                    raise ValueError(f"text manifest object is not text: {oid}")
                check_text(entry["text"], geometry.style(oid).get("font_family"))
            else:
                _refs(entry["object_ids"], objects, "layout object")
    profile = context.get("style_profile", {}).get("document")
    seen = set()
    for binding in context.get("token_bindings", []):
        _refs([binding["object_id"]], objects, "token object")
        pair = (binding["object_id"], binding["field"])
        if pair in seen:
            raise ValueError("duplicate token binding for object field")
        seen.add(pair)
        binding_token(profile, binding)


def apply_context(scene: dict, context_path, *, resolve_tokens: bool = False) -> tuple[dict, dict]:
    """Return a copied Scene and flat, self-contained extras; never edit source files.

    Relative dependency paths are resolved against the context file. Evidence refs
    additionally require an ``id``. Reattaching a normalized snapshot is supported.
    Token resolution is opt-in and writes only explicitly listed style bindings.
    """
    validate(scene)
    path = Path(context_path).resolve()
    context = load_json(path)
    schema_validate(context, "context")
    context = copy.deepcopy(context)
    extras = {}
    for key, (filename, schema) in DOCUMENTS.items():
        if key not in context:
            continue
        ref = context[key]
        if "path" in ref:
            doc = load_json(_checked_file(ref, path.parent))
            schema_validate(doc, schema)
            context[key] = {"document": doc, "sha256": canonical_hash(doc)}
        extras[filename] = copy.deepcopy(context[key]["document"])
    context["evidence"] = [_attach_evidence(ref, path.parent, scene) for ref in context.get("evidence", [])]
    extras["context.json"] = context
    result = copy.deepcopy(scene)
    _validate_documents(context, extras)
    _validate_refs(result, context)
    preserved = _preserved(context)
    provenance = result.setdefault("provenance", {})
    if preserved:
        # Existing semantic policy is extended visibly, never replaced or hidden.
        brief = provenance.setdefault("brief", {})
        brief["protected_ids"] = sorted(set(brief.get("protected_ids", [])) | set(preserved))
    if resolve_tokens:
        resolve_bindings(result, context.get("style_profile", {}).get("document"), context.get("token_bindings", []))
    provenance["context"] = _binding(context)
    validate_context(result, extras)
    return result, extras


def validate_context(scene: dict, extras_dict: dict) -> None:
    """Validate all active context bindings without opening source/PNG files.

    Unrelated project extras may coexist. Orphan profile/manifest extras and active
    contexts missing their context.json are rejected so load/write cannot detach
    semantic provenance silently.
    """
    validate(scene)
    extras = extras_dict
    active = scene.get("provenance", {}).get("context")
    if "context.json" not in extras:
        if active is not None or any(name in extras for name in EXTRA_NAMES - {"context.json"}):
            raise ValueError("missing context.json for bound context extras")
        return
    context = extras["context.json"]
    schema_validate(context, "context")
    _validate_documents(context, extras)
    _validate_refs(scene, context)
    for ref in context.get("evidence", []):
        if "path" in ref:
            raise ValueError("context evidence snapshot is not self-contained")
        _validate_evidence_snapshot(ref, scene)
    if active != _binding(context):
        raise ValueError("Scene context provenance hash or typed ID binding mismatch")
    protected = set(scene.get("provenance", {}).get("brief", {}).get("protected_ids", []))
    if not set(_preserved(context)) <= protected:
        raise ValueError("context preserved IDs must explicitly extend semantic brief protected_ids")
    if _preserved(context):
        from .reconstruct import semantic_audit
        failures = [reason for reason in semantic_audit(scene)["reasons"] if reason.startswith("protected_id_")]
        if failures:
            raise ValueError("context preserved object invalid: " + ", ".join(failures))


def _layout_matches(entry, geometry):
    boxes = [geometry.bounds(oid) for oid in entry["object_ids"]]
    if any(box is None for box in boxes):
        return False
    tolerance = entry.get("tolerance", 1)
    first, rest = boxes[0], boxes[1:]
    relation = entry["relation"]
    if relation in {"aligned_left", "aligned_top", "same_width", "same_height"}:
        def measure(box):
            return {"aligned_left": box[0], "aligned_top": box[1],
                    "same_width": box[2] - box[0], "same_height": box[3] - box[1]}[relation]
        return all(abs(measure(box) - measure(first)) <= tolerance for box in rest)
    if relation == "contains":
        return all(first[0] - tolerance <= box[0] and first[1] - tolerance <= box[1]
                   and first[2] + tolerance >= box[2] and first[3] + tolerance >= box[3] for box in rest)
    if relation == "left_of":
        return all(left[2] <= right[0] + tolerance for left, right in zip(boxes, boxes[1:]))
    return all(top[3] <= bottom[1] + tolerance for top, bottom in zip(boxes, boxes[1:]))


def style_check(scene: dict, extras_dict: dict) -> dict:
    """Mechanical intent/style comparison only; never mutate or certify semantics."""
    validate_context(scene, extras_dict)
    context = extras_dict.get("context.json")
    mismatches = []
    if context is not None:
        mismatches = profile_mismatches(scene, context.get("style_profile", {}).get("document"),
                                        context.get("token_bindings", []))
        nodes = {obj["id"]: obj for obj in scene["objects"]}
        for entry in context.get("text_manifest", {}).get("document", {}).get("entries", []):
            actual = nodes[entry["object_id"]]["geometry"]["text"]
            if actual != entry["text"]:
                mismatches.append({"kind": "text", "entry_id": entry["id"], "object_id": entry["object_id"],
                                   "expected": entry["text"], "actual": actual})
        geometry = Geometry(scene)
        for entry in context.get("layout_manifest", {}).get("document", {}).get("entries", []):
            if not _layout_matches(entry, geometry):
                mismatches.append({"kind": "layout", "entry_id": entry["id"],
                                   "object_ids": entry["object_ids"], "relation": entry["relation"]})
    return {"schema_version": "0.1", "status": "needs_review" if mismatches else "passed",
            "scope": "mechanical_style_only", "mismatches": mismatches,
            "context_sha256": canonical_hash(context) if context is not None else None,
            "scientific_review": "host_required"}

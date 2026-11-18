"""Portable composition intent; member snapshots are inert source provenance.

The sole active extra is ``composition-context.json``. Its canonical digest is
bound by ``provenance.composition.context_sha256``. Original receipt/document
bytes are retained as JSON text and verified against their original SHA256s;
no source files are opened at validation. Aggregate tables are deterministically
rebuilt from snapshots and instance maps, never from current Scene geometry.
Current text/styles/transforms may change. Referenced IDs and protected object
visibility must remain valid. Source reviews never become composite reviews.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from .context import DOCUMENTS, _binding, _validate_documents, _validate_evidence_snapshot, canonical_hash
from .scene import ID, finite, load_json, schema_validate, validate
from .style_profile import binding_token

FILENAME = "composition-context.json"
_TABLES = ("intent", "roles", "representations", "uncertainties", "evidence", "objects",
           "style_profiles", "tokens", "token_bindings", "text_manifests", "text_entries",
           "layout_manifests", "layout_entries", "plans", "protected_objects")
_METADATA = ("roles", "representations", "uncertainties", "evidence", "style_profiles", "tokens",
             "text_manifests", "text_entries", "layout_manifests", "layout_entries", "relations")


def namespaced_id(instance, category, local=""):
    """Stable bounded IDs, isolated even when the same member is placed twice."""
    prefix = hashlib.sha256(instance.encode("utf-8")).hexdigest()[:24]
    suffix = hashlib.sha256(local.encode("utf-8")).hexdigest()[:24] if local else "root"
    return f"c_{prefix}_{category}_{suffix}"


def _raw_snapshot(path):
    raw = Path(path).read_bytes()
    return {"document": load_json(path), "text": raw.decode("utf-8"),
            "sha256": hashlib.sha256(raw).hexdigest()}


def snapshot_member(scene, directory):
    """Capture verified member metadata, not a second current geometry model."""
    from .project import PROJECT_JSON_EXTRAS

    provenance = scene.get("provenance", {})
    if "composition" in provenance or provenance.get("mode") == "composition":
        raise ValueError("nested composition members are unsupported: explicit metadata remapping is required")
    directory = Path(directory)
    receipt = _raw_snapshot(directory / "receipt.json")
    return {"scene_sha256": receipt["document"]["artifacts"]["scene.json"]["sha256"],
            "scene_version": scene["schema_version"],
            "namespace": {"objects": [o["id"] for o in scene["objects"]],
                          "resources": [r["id"] for r in scene.get("resources", [])],
                          "relations": [r["id"] for r in scene.get("occlusions", [])]},
            "provenance": {"document": copy.deepcopy(provenance), "sha256": canonical_hash(provenance)},
            "receipt": receipt,
            "documents": {name: _raw_snapshot(directory / name) for name in sorted(PROJECT_JSON_EXTRAS)
                          if (directory / name).is_file()}}


def _raw_document(snapshot, label):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate composition snapshot JSON key: {key}")
            result[key] = value
        return result

    raw = snapshot["text"].encode("utf-8")
    if hashlib.sha256(raw).hexdigest() != snapshot["sha256"]:
        raise ValueError(f"composition snapshot SHA mismatch: {label}")
    try:
        document = json.loads(snapshot["text"], object_pairs_hook=unique)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ValueError(f"invalid composition snapshot JSON: {label}") from error
    finite(document)
    if canonical_hash(document) != canonical_hash(snapshot["document"]):
        raise ValueError(f"composition snapshot document mismatch: {label}")
    return document, len(raw)


def _validate_member(member, legacy):
    from .project import PROJECT_JSON_EXTRAS

    receipt, _ = _raw_document(member["receipt"], "receipt")
    if (member["receipt"]["sha256"] != legacy["member_receipt_sha256"]
            or receipt.get("schema_version") != "0.1" or receipt.get("status") != "success"
            or receipt.get("artifacts") != legacy["artifacts"]):
        raise ValueError("composition member receipt binding mismatch")
    if (canonical_hash(receipt.get("runtime")) != legacy["runtime_sha256"]
            or receipt.get("runtime", {}).get("font", {}).get("sha256") != legacy["font_sha256"]):
        raise ValueError("composition member runtime/font binding mismatch")
    if member["scene_sha256"] != receipt["artifacts"].get("scene.json", {}).get("sha256"):
        raise ValueError("composition member Scene SHA mismatch")
    provenance = member["provenance"]["document"]
    if canonical_hash(provenance) != member["provenance"]["sha256"]:
        raise ValueError("composition member provenance SHA mismatch")
    if provenance.get("source") != legacy["source"]:
        raise ValueError("composition member source binding mismatch")
    if "composition" in provenance or provenance.get("mode") == "composition":
        raise ValueError("nested composition members are unsupported")
    namespace = member["namespace"]
    for category, values in namespace.items():
        if len(values) != len(set(values)) or any(not ID.fullmatch(value) for value in values):
            raise ValueError(f"invalid composition source {category} namespace")
    if set(namespace["objects"]) & set(namespace["resources"]):
        raise ValueError("composition source object/resource collision")
    expected = set(receipt["artifacts"]) & PROJECT_JSON_EXTRAS
    if set(member["documents"]) != expected or FILENAME in expected:
        raise ValueError("composition member document closure mismatch")
    documents = {}
    for name, snapshot in member["documents"].items():
        document, size = _raw_document(snapshot, name)
        if receipt["artifacts"][name] != {"sha256": snapshot["sha256"], "bytes": size}:
            raise ValueError(f"composition member document receipt mismatch: {name}")
        documents[name] = document
    context = documents.get("context.json")
    if context is None:
        if "context" in provenance or any(name in documents for name, _ in DOCUMENTS.values()):
            raise ValueError("composition member missing bound context snapshot")
    else:
        schema_validate(context, "context")
        _validate_documents(context, documents)
        if provenance.get("context") != _binding(context):
            raise ValueError("composition member context binding mismatch")
        for ref in context.get("evidence", []):
            if "path" in ref:
                raise ValueError("composition member evidence must be a portable snapshot")
            _validate_evidence_snapshot(ref, {"provenance": provenance})
        for binding in context.get("token_bindings", []):
            binding_token(context.get("style_profile", {}).get("document"), binding)
    return documents


def _mapped(value, maps):
    """Only explicit typed references are remapped; prose is never interpreted."""
    if isinstance(value, list):
        return [_mapped(item, maps) for item in value]
    if not isinstance(value, dict):
        return copy.deepcopy(value)
    singular = {"object_id": "objects", "resource_id": "resources", "role_id": "roles",
                "representation_id": "representations", "token": "tokens"}
    plural = {"object_ids": "objects", "resource_ids": "resources", "protected_ids": "objects",
              "preserved_ids": "objects", "uncertainty_ids": "uncertainties", "evidence_ids": "evidence"}
    typed = value.get("type", value.get("kind"))
    typed_category = {"object": "objects", "object_ref": "objects", "resource": "resources",
                      "resource_ref": "resources"}.get(typed) if isinstance(typed, str) else None
    result = {}
    for key, item in value.items():
        category = singular.get(key, plural.get(key))
        if key in {"id", "ref"} and typed_category:
            category = typed_category
        if category:
            refs = item if key in plural else [item]
            if not isinstance(refs, list) or any(not isinstance(ref, str) or ref not in maps[category] for ref in refs):
                raise ValueError(f"unknown composition metadata reference: {key}")
            result[key] = [maps[category][ref] for ref in refs] if key in plural else maps[category][item]
        else:
            result[key] = _mapped(item, maps)
    return result


def _aggregate(members, instances):
    tables, instance_maps = {name: {} for name in _TABLES}, {}
    for iid, instance in instances.items():
        member = members[instance["asset_id"]]
        docs = {name: snapshot["document"] for name, snapshot in member["documents"].items()}
        context = docs.get("context.json", {})
        maps = copy.deepcopy(instance["id_map"])
        maps.update({category: {} for category in _METADATA})
        for category in ("roles", "representations", "uncertainties", "evidence"):
            maps[category] = {entry["id"]: namespaced_id(iid, "m", category + ":" + entry["id"])
                              for entry in context.get(category, [])}
            if len(maps[category]) != len(context.get(category, [])):
                raise ValueError(f"duplicate composition member {category} ID")
        maps["relations"] = {rid: namespaced_id(iid, "x", rid) for rid in member["namespace"]["relations"]}
        for field, category, entries in (("style_profile", "style_profiles", "tokens"),
                                         ("text_manifest", "text_manifests", "text_entries"),
                                         ("layout_manifest", "layout_manifests", "layout_entries")):
            if field not in context:
                continue
            document = context[field]["document"]
            maps[category][document["id"]] = namespaced_id(iid, "m", category + ":" + document["id"])
            records = document["tokens" if entries == "tokens" else "entries"]
            maps[entries] = {entry["id"]: namespaced_id(iid, "m", entries + ":" + entry["id"]) for entry in records}
            if len(maps[entries]) != len(records):
                raise ValueError(f"duplicate composition member {entries} ID")
        instance_maps[iid] = {category: maps[category] for category in _METADATA}

        def record(category, local, value, *, target=None):
            target = target or maps[category][local]
            if target in tables[category]:
                raise ValueError("composition metadata namespace collision")
            tables[category][target] = {"instance_id": iid, "source_id": local, "value": copy.deepcopy(value)}

        provenance = member["provenance"]["document"]
        intent = {"provenance_sha256": member["provenance"]["sha256"],
                  "source": copy.deepcopy(provenance.get("source")),
                  "documents": {name: snapshot["sha256"] for name, snapshot in member["documents"].items()}}
        if "brief" in provenance:
            intent["brief"] = _mapped(provenance["brief"], maps)
        record("intent", instance["asset_id"], intent, target=namespaced_id(iid, "intent"))
        for category in ("roles", "representations", "uncertainties"):
            for entry in context.get(category, []):
                value = _mapped(entry, maps)
                value["id"] = maps[category][entry["id"]]
                record(category, entry["id"], value)
        for entry in context.get("evidence", []):
            # Evidence itself remains a full member snapshot, with its original hashes.
            record("evidence", entry["id"], {"member_document": "context.json", "evidence_id": entry["id"]})
        for entry in context.get("objects", []):
            record("objects", entry["object_id"], _mapped(entry, maps))
        for field, category, entries in (("style_profile", "style_profiles", "tokens"),
                                         ("text_manifest", "text_manifests", "text_entries"),
                                         ("layout_manifest", "layout_manifests", "layout_entries")):
            if field not in context:
                continue
            original = context[field]["document"]
            record(category, original["id"], {"id": maps[category][original["id"]],
                                             "member_document": DOCUMENTS[field][0],
                                             "entry_ids": list(maps[entries].values())})
            for entry in original["tokens" if entries == "tokens" else "entries"]:
                value = _mapped(entry, maps)
                value["id"] = maps[entries][entry["id"]]
                record(entries, entry["id"], value)
        for binding in context.get("token_bindings", []):
            value = _mapped(binding, maps)
            value["profile_id"] = next(iter(maps["style_profiles"].values()))
            record("token_bindings", binding["object_id"] + ":" + binding["field"], value,
                   target=namespaced_id(iid, "binding", binding["object_id"] + ":" + binding["field"]))
        protected = {}
        for oid in provenance.get("brief", {}).get("protected_ids", []):
            protected.setdefault(oid, []).append("provenance.brief.protected_ids")
        for oid in context.get("preserved_ids", []):
            protected.setdefault(oid, []).append("context.preserved_ids")
        for entry in context.get("objects", []):
            if entry.get("preserve"):
                protected.setdefault(entry["object_id"], []).append("context.objects.preserve")
        for oid, reasons in protected.items():
            if oid not in maps["objects"]:
                raise ValueError("unknown composition protected object")
            record("protected_objects", oid, {"object_id": maps["objects"][oid], "reasons": reasons},
                   target=maps["objects"][oid])
        # Plan documents stay inert. These references expose the exact namespace
        # needed to interpret their member-local IDs without copying current paths.
        for name, document in docs.items():
            if "plan" in name:
                record("plans", document.get("id", name),
                       {"member_document": name, "sha256": member["documents"][name]["sha256"],
                        "object_id_map": copy.deepcopy(maps["objects"]),
                        "resource_id_map": copy.deepcopy(maps["resources"])},
                       target=namespaced_id(iid, "plan", name))
    return tables, instance_maps


def build_composition_context(plan, bundle_sha256, members, instances):
    """Return the extra document; callers bind its hash before write_project."""
    tables, metadata_maps = _aggregate(members, instances)
    document = {"schema_version": "0.1", "bundle_sha256": bundle_sha256,
                "plan": {"document": copy.deepcopy(plan), "sha256": canonical_hash(plan)},
                "members": copy.deepcopy(members), "instances": copy.deepcopy(instances),
                "metadata_id_maps": metadata_maps, "aggregate": tables,
                "protected_ids": sorted(tables["protected_objects"])}
    if len(json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False)) + 1 > 20_000_000:
        raise ValueError("composition context exceeds portable project JSON size limit")
    return document


def validate_composition_context(scene, documents):
    """Validate the complete portable closure on project load and write.

    Old compositions without context_sha256 remain readable. A present extra
    always requires its Scene binding. Member source paths are never dereferenced.
    Metadata references must resolve in the current Scene, but original text,
    styles, resources, transforms and layout measurements are not current values.
    Generation attached to a composite is unsupported and explicitly rejected.
    """
    composition = scene.get("provenance", {}).get("composition", {})
    if not isinstance(composition, dict):
        raise ValueError("invalid composition provenance record")
    if FILENAME not in documents:
        if "context_sha256" in composition:
            raise ValueError("missing composition-context.json for composition binding")
        return
    validate(scene)
    document = documents[FILENAME]
    schema_validate(document, "composition-context")
    if (scene.get("provenance", {}).get("mode") != "composition"
            or composition.get("context_sha256") != canonical_hash(document)):
        raise ValueError("Scene composition context SHA binding mismatch")
    if "generation" in scene.get("provenance", {}) or "generation.json" in documents:
        raise ValueError("generation provenance on composited scenes is unsupported")
    if document["bundle_sha256"] != composition.get("bundle_sha256"):
        raise ValueError("composition bundle SHA mismatch")
    if document["instances"] != composition.get("instances") or set(document["members"]) != set(composition.get("members", {})):
        raise ValueError("composition member/instance binding mismatch")
    plan = document["plan"]["document"]
    if canonical_hash(plan) != document["plan"]["sha256"]:
        raise ValueError("composition plan SHA mismatch")
    schema_validate(plan, "composition-plan-0.2" if plan.get("schema_version") == "0.2" else "composition-plan")
    if {p["instance_id"]: p["asset_id"] for p in plan["placements"]} != {
            iid: instance["asset_id"] for iid, instance in document["instances"].items()}:
        raise ValueError("composition source plan instances mismatch")
    for aid, member in document["members"].items():
        _validate_member(member, composition["members"][aid])
    all_ids = set()
    for iid, instance in document["instances"].items():
        if instance["asset_id"] not in document["members"]:
            raise ValueError("unknown composition instance member")
        member = document["members"][instance["asset_id"]]
        for category, code in (("objects", "o"), ("resources", "r")):
            expected = {local: namespaced_id(iid, code, local) for local in member["namespace"][category]}
            if instance["id_map"][category] != expected:
                raise ValueError("composition instance ID map mismatch")
        imported = {instance["wrapper_id"], *instance["id_map"]["objects"].values(), *instance["id_map"]["resources"].values()}
        if instance["wrapper_id"] != namespaced_id(iid, "g") or all_ids & imported:
            raise ValueError("composition instance namespace collision")
        all_ids.update(imported)
    aggregate, maps = _aggregate(document["members"], document["instances"])
    if aggregate != document["aggregate"] or maps != document["metadata_id_maps"]:
        raise ValueError("composition aggregate metadata/ID map mismatch")
    if document["protected_ids"] != sorted(aggregate["protected_objects"]):
        raise ValueError("composition protected ID binding mismatch")
    nodes = {obj["id"]: obj for obj in scene["objects"]}

    def refs(ids):
        if set(ids) - nodes.keys():
            raise ValueError("unknown current composition metadata object reference")

    for entry in aggregate["objects"].values():
        refs([entry["value"]["object_id"]])
    for entry in aggregate["text_entries"].values():
        oid = entry["value"]["object_id"]
        refs([oid])
        if nodes[oid]["kind"] != "text":
            raise ValueError("composition text manifest object is not text")
    for entry in aggregate["layout_entries"].values():
        refs(entry["value"]["object_ids"])
    for entry in aggregate["token_bindings"].values():
        refs([entry["value"]["object_id"]])
    refs(document["protected_ids"])
    if document["protected_ids"]:
        from .reconstruct import semantic_audit
        # Reuse visibility policy without inheriting member semantic budgets.
        trial = {**scene, "provenance": {"brief": {"protected_ids": document["protected_ids"]}}}
        failures = [reason for reason in semantic_audit(trial)["reasons"] if reason.startswith("protected_id_")]
        if failures:
            raise ValueError("composition protected object invalid: " + ", ".join(failures))

"""Host-authored assessments bound to the actual scene and rendered artifacts.

The engine verifies bindings, not whether a host looked at or understood an image.
No model provider, API client, or visual judge runs here.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from .scene import schema_validate


def content_digest(value):
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def artifact_binding(scene, preview, reference, runtime, scale):
    def file_digest(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest() if path and Path(path).is_file() else None
    return {"scene_sha256": content_digest(scene), "revision": scene["revision"],
            "source_sha256": scene.get("provenance", {}).get("source", {}).get("sha256"),
            "reference_sha256": file_digest(reference), "preview_sha256": file_digest(preview),
            "render_config_sha256": content_digest({"runtime": runtime, "scale": scale})}


def project_binding(scene, directory):
    from .scene import load_json
    root = Path(directory)
    receipt = load_json(root / "receipt.json")
    return artifact_binding(scene, root / "preview.png", root / "reference.png",
                            receipt["runtime"], receipt.get("render_scale", 3))


def validate_record(record, scene, binding):
    schema_validate(record, "host-review")
    if record["binding"] != binding:
        raise ValueError("host_review_binding_conflict: obtain the current review binding and inspect its preview")
    ids = {o["id"] for o in scene["objects"]} | {"$scene"}
    for section in ("visual", "scientific"):
        if any(f["object_id"] not in ids for f in record[section]["findings"]):
            raise ValueError("host review references an unknown object ID")
    return copy.deepcopy(record)


def review_document(scene, binding, previous=None):
    from .quality import scene_review
    from .evaluation import current_audit
    record = previous.get("host_record") if isinstance(previous, dict) else None
    result = {"schema_version": "0.3", "binding": binding,
              "mechanical": {"geometry_validation": "passed", "drawing": scene_review(scene)},
              "adaptive": current_audit(scene) if "adaptive_context" in scene.get("provenance", {}) else None,
              "visual": {"status": "not_executed", "findings": []},
              "scientific": {"status": "not_executed", "findings": []}}
    if scene.get("provenance", {}).get("mode") == "semantic":
        from .reconstruct import semantic_audit
        result["mechanical"]["semantic"] = semantic_audit(scene)
    if record:
        result["host_record"] = copy.deepcopy(record)
        current = record.get("binding") == binding
        result["host_record_current"] = current
        for section in ("visual", "scientific"):
            result[section] = copy.deepcopy(record[section])
            if not current:
                result[section]["last_status"] = result[section]["status"]
                result[section]["status"] = "stale_after_change"
    return result


def current_project_review(scene, directory):
    from .scene import load_json
    path = Path(directory) / "review.json"
    previous = load_json(path) if path.is_file() else None
    return review_document(scene, project_binding(scene, directory), previous)

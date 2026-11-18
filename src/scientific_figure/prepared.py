"""Inert, on-demand prepared content and externally supplied candidate validation.

No runtime or OCR adapter is installed here. A consumption result contains actual
file bytes encoded as base64 for a caller's tool boundary. It proves local reads,
not model understanding, message delivery, or ordering before a model request.
The original request and original images must still be available for consumption.
Hashes provide consistency; use expected_manifest_sha256 from a trusted channel
when an independently anchored identity is needed. No executable is accepted.
"""
from __future__ import annotations

import base64
import copy
from io import BytesIO
import json
import math
from pathlib import Path
import re

from PIL import Image

from .evidence import (MAX_INPUT_BYTES, MAX_INPUT_PIXELS, _background, _channels,
                       _expected_names, _hash, _load_source, _read_bounded,
                       _region, _safe_artifact, validate_evidence)
from .preparation import MAX_REQUEST_BYTES, _unique
from .scene import schema_validate

MAX_PACKAGE_BYTES = 64 * 1024 * 1024
MAX_JSON_BYTES = 1024 * 1024
MAX_CANDIDATES = 1000
COORDINATE_SPACE = "exif-oriented-pixel-boundaries"
_SHA = re.compile(r"^[a-f0-9]{64}$")
_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,99}$")


def _json(data):
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_unique,
                          parse_constant=lambda value: (_ for _ in ()).throw(
                              ValueError(f"nonfinite JSON value: {value}")))
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("invalid bounded JSON document") from exc


def _json_object(value, limit=MAX_JSON_BYTES):
    """Require JSON data, retaining no caller-owned mutable objects."""
    def check(item, depth=0):
        if depth > 32:
            raise ValueError("JSON nesting exceeds budget")
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise ValueError("JSON keys must be strings")
            for child in item.values():
                check(child, depth + 1)
        elif type(item) is list:
            for child in item:
                check(child, depth + 1)
        elif type(item) not in (str, int, float, bool, type(None)):
            raise ValueError("expected JSON data")
    check(value)
    data = json.dumps(value, allow_nan=False, ensure_ascii=True).encode("utf-8")
    if len(data) > limit or type(value) is not dict:
        raise ValueError("expected a bounded JSON object")
    return _json(data)


def _fields(value, required, optional=()):
    if type(value) is not dict or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise ValueError("missing or unsupported fields")


def _identifier(value):
    if type(value) is not str or not _ID.fullmatch(value):
        raise ValueError("invalid identifier")


def _sha(value):
    if type(value) is not str or not _SHA.fullmatch(value):
        raise ValueError("invalid SHA256")


def _number(value, lower, upper, label):
    if type(value) not in (int, float) or not lower <= value <= upper or not math.isfinite(value):
        raise ValueError(f"invalid {label}")


def _strings(value, *, maximum=100, length=1000):
    if type(value) is not list or len(value) > maximum or any(
            type(item) is not str or not item or len(item) > length for item in value):
        raise ValueError("expected bounded list of nonempty strings")


def _bbox(value, roi):
    if type(value) is not list or len(value) != 4:
        raise ValueError("bbox must be source [x, y, width, height]")
    for number in value:
        _number(number, 0, MAX_INPUT_PIXELS, "bbox coordinate")
    x, y, w, h = value
    rx, ry, rw, rh = roi
    if w <= 0 or h <= 0 or x < rx or y < ry or x + w > rx + rw or y + h > ry + rh:
        raise ValueError("candidate bbox outside source ROI")


def _directory(path):
    path = Path(path)
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)() or not path.is_dir():
        raise ValueError("missing or linked prepared directory")
    return path.resolve()


def _read_json(path):
    return _json(_read_bounded(path, MAX_JSON_BYTES))


def _selection(values, available, label):
    if (type(values) is not list or not values or len(values) > len(available)
            or any(type(v) is not str for v in values)):
        raise ValueError(f"{label} must be a nonempty list")
    if len(set(values)) != len(values) or not set(values) <= set(available):
        raise ValueError(f"duplicate or unknown {label}")
    return values


def consume(prepared, *, input_ids=None, views=None, host_stage="on_demand",
            expected_manifest_sha256=None, max_bytes=MAX_PACKAGE_BYTES):
    """Validate a prepare directory or its exact receipt; return JSON plus content.

    views are literal artifact names, defaulting to source.png. The entire package
    is validated, even when only some inputs/views are consumed. max_bytes may
    lower the 64 MiB aggregate package AND original-source byte budgets. Per-image
    limits remain those of evidence.py. A caller-declared host_stage is unverified.
    All returned base64 content is inert data; this function sends no messages.
    """
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_PACKAGE_BYTES:
        raise ValueError("invalid aggregate byte budget")
    if host_stage not in ("on_demand", "before_model_request", "after_model_request"):
        raise ValueError("unsupported host stage")
    receipt = _json_object(prepared) if type(prepared) is dict else None
    if receipt is not None:
        if type(receipt.get("directory")) is not str:
            raise ValueError("receipt requires directory")
        directory = receipt["directory"]
    else:
        directory = prepared
    root = _directory(directory)
    if {p.name for p in root.iterdir()} != {"input-manifest.json", "inputs"}:
        raise ValueError("unexpected or missing prepared files")
    manifest_path = _safe_artifact(root, "input-manifest.json")
    manifest_bytes = _read_bounded(manifest_path, MAX_JSON_BYTES)
    manifest_hash = _hash(manifest_bytes)
    if expected_manifest_sha256 is not None:
        _sha(expected_manifest_sha256)
        if manifest_hash != expected_manifest_sha256:
            raise ValueError("preparation manifest hash mismatch")
    manifest = _json(manifest_bytes)
    schema_validate(manifest, "preparation-manifest")
    entries = manifest["inputs"]
    ids = [entry["id"] for entry in entries]
    if len({name.casefold() for name in ids}) != len(ids):
        raise ValueError("duplicate input IDs")
    inputs_root = _directory(root / "inputs")
    if inputs_root.parent != root or {p.name for p in inputs_root.iterdir()} != set(ids):
        raise ValueError("unexpected or missing input directories")
    selected = _selection(input_ids if input_ids is not None else ids, ids, "input IDs")
    views = ["source.png"] if views is None else views
    _selection(views, set().union(*(_expected_names(e["channels"]) for e in entries)), "views")

    # Verify the actual request as well as its recorded hash, without executing it.
    request_path = Path(manifest["request"]["path"])
    if not request_path.is_absolute():
        raise ValueError("request path must be absolute")
    request_data = _read_bounded(request_path, MAX_REQUEST_BYTES)
    if _hash(request_data) != manifest["request"]["sha256"]:
        raise ValueError("request hash mismatch")
    request = _json(request_data)
    schema_validate(request, "preparation-request")
    if request["task"] != manifest["task"] or [i["id"] for i in request["inputs"]] != ids:
        raise ValueError("request and preparation inputs differ")

    total_bytes, source_bytes = len(manifest_bytes), 0
    package_files = [{"path": "input-manifest.json", "sha256": manifest_hash,
                      "bytes": len(manifest_bytes)}]
    consumed, evidence_receipts = [], []
    for entry, item in zip(entries, request["inputs"]):
        name = entry["id"]
        prefix = f"inputs/{name}"
        if entry["evidence"] != f"{prefix}/evidence.json" or entry["manifest"] != f"{prefix}/manifest.json":
            raise ValueError("input path does not match its ID")
        folder = _directory(inputs_root / name)
        if folder.parent != inputs_root:
            raise ValueError("input path escapes prepared directory")
        expected_names = _expected_names(entry["channels"]) | {"evidence.json", "manifest.json"}
        if {p.name for p in folder.iterdir()} != expected_names:
            raise ValueError("unexpected or missing evidence files")
        snapshots = {}
        for filename in sorted(expected_names):
            path = _safe_artifact(folder, filename)
            limit = MAX_JSON_BYTES if filename.endswith(".json") else MAX_INPUT_BYTES
            if path.stat().st_size > min(limit, max_bytes - total_bytes):
                raise ValueError("prepared package exceeds byte budget")
            data = _read_bounded(path, min(limit, max_bytes - total_bytes))
            total_bytes += len(data)
            record = {"path": f"{prefix}/{filename}", "sha256": _hash(data), "bytes": len(data)}
            package_files.append(record)
            snapshots[filename] = record
        for field in ("evidence", "manifest"):
            if snapshots[f"{field}.json"]["sha256"] != entry[f"{field}_sha256"]:
                raise ValueError(f"prepared {field} hash mismatch")
        evidence = _read_json(folder / "evidence.json")
        evidence_receipt = validate_evidence(folder)
        evidence_receipts.append({"id": name, **evidence_receipt})
        source = evidence["source"]
        if any(entry[key] != value for key, value in {
                "source_path": source["path"], "source_sha256": source["sha256"],
                "roi": source["roi"], "channels": evidence["channels"]}.items()):
            raise ValueError("preparation source, ROI or channels mismatch")
        original = Path(item["path"])
        if not original.is_absolute():
            original = request_path.parent / original
        original = original.resolve()
        if str(original) != source["path"] or not Path(source["path"]).is_absolute():
            raise ValueError("source path mismatch")
        if _channels(item.get("channels")) != entry["channels"]:
            raise ValueError("request channels mismatch")
        if original.stat().st_size > max_bytes - source_bytes:
            raise ValueError("original sources exceed byte budget")
        rgb, actual_source = _load_source(original, item.get("roi"),
                                           _background(item.get("background", request.get("background"))))
        source_bytes += actual_source["bytes"]
        if source_bytes > max_bytes:
            raise ValueError("original sources exceed byte budget")
        if actual_source != source:
            raise ValueError("original source hash or ROI metadata mismatch")
        source_png = _read_bounded(folder / "source.png", MAX_INPUT_BYTES)
        if _hash(source_png) != snapshots["source.png"]["sha256"]:
            raise ValueError("source artifact changed during consumption")
        with Image.open(BytesIO(source_png)) as image:
            if image.size != rgb.size or image.tobytes() != rgb.tobytes():
                raise ValueError("source raster does not match actual original ROI")
        # Re-read hashes after validation so the record does not describe stale bytes.
        for filename, record in snapshots.items():
            data = _read_bounded(folder / filename, MAX_INPUT_BYTES)
            if len(data) != record["bytes"] or _hash(data) != record["sha256"]:
                raise ValueError("prepared file changed during consumption")
        if name not in selected:
            continue
        _selection(views, _expected_names(entry["channels"]), "views for selected input")
        contents = []
        for filename in views:
            data = _read_bounded(folder / filename, MAX_INPUT_BYTES)
            record = snapshots[filename]
            if len(data) != record["bytes"] or _hash(data) != record["sha256"]:
                raise ValueError("consumed content hash mismatch")
            contents.append({**record, "media_type": "image/png", "encoding": "base64",
                             "content": base64.b64encode(data).decode("ascii")})
        consumed.append({"id": name, "source_sha256": source["sha256"], "roi": source["roi"],
                         "coordinate_space": source["coordinate_space"],
                         "mappings": evidence["mappings"], "views": contents})
    expected_receipt = {"schema_version": "0.1", "directory": str(root),
                        "input_manifest_path": str(manifest_path), "task": manifest["task"],
                        "preflight_status": "not_integrated", "inputs": evidence_receipts}
    if receipt is not None and json.dumps(receipt, sort_keys=True) != json.dumps(expected_receipt, sort_keys=True):
        raise ValueError("preparation receipt does not match actual directory")
    if total_bytes > max_bytes or _read_bounded(manifest_path, MAX_JSON_BYTES) != manifest_bytes:
        raise ValueError("preparation manifest changed or package exceeds byte budget")
    package_digest = _hash(json.dumps(package_files, sort_keys=True, separators=(",", ":")).encode())
    return {"schema_version": "0.1", "kind": "prepared_consumption", "status": "consumed",
            "boundary": "local_tool_return", "runtime_preflight": "not_integrated",
            "declared_host_stage": host_stage, "host_call_order_verified": False,
            "input_manifest_sha256": manifest_hash, "package_sha256": package_digest,
            "package_bytes": total_bytes, "package_files": package_files, "inputs": consumed,
            "limitations": ["Proves local file content reads only; no model understanding or delivery proof.",
                            "No runtime adapter or verified first-request ordering.",
                            "Hashes establish consistency, not authenticity against coordinated rewrites."]}


def runtime_preflight():
    """No user-created receipt can upgrade this status to integrated."""
    return {"status": "not_integrated", "adapter_status": "adapter_not_available",
            "host_call_order_verified": False,
            "reason": "No actual external runtime adapter is available."}


def candidate_adapter_status():
    return {"ocr": "unavailable", "repetition": "unavailable",
            "external_candidates": "validation_only", "scene_mutation": False}


def validate_candidates(document, image_path, *, roi=None, background=None):
    """Validate external OCR/repetition proposals against an actual source image.

    Document keys: schema_version='0.1', kind='external_candidates', provider,
    image_sha256, coordinate_space, source_size, roi, candidates. Each candidate
    has candidate_id, kind ('ocr'/'repetition'), bbox, rotation (-180..180),
    confidence (0..1), limitations (strings), and text for OCR, or instances
    (source bboxes) and count for repetition. Instance boxes must fit the group.
    Coordinates describe the EXIF-oriented source, never a diagnostic board.
    Candidate validity does not verify recognized text or scientific counts.
    """
    doc = _json_object(document)
    _fields(doc, {"schema_version", "kind", "provider", "image_sha256", "coordinate_space",
                  "source_size", "roi", "candidates"})
    if doc["schema_version"] != "0.1" or doc["kind"] != "external_candidates":
        raise ValueError("unsupported candidates format")
    if type(doc["provider"]) is not str or not 1 <= len(doc["provider"]) <= 200:
        raise ValueError("provider must be a bounded name")
    _sha(doc["image_sha256"])
    _, source = _load_source(Path(image_path).resolve(), roi, _background(background))
    for key, expected in {"image_sha256": source["sha256"], "source_size": source["size"],
                          "roi": source["roi"], "coordinate_space": COORDINATE_SPACE}.items():
        if doc[key] != expected:
            raise ValueError(f"candidate source binding mismatch: {key}")
    # Equality alone would allow booleans in integer sizes/ROI.
    if type(doc["source_size"]) is not list or any(type(v) is not int for v in doc["source_size"]):
        raise ValueError("invalid source size")
    _region(doc["roi"], source["size"])
    candidates = doc["candidates"]
    if type(candidates) is not list or len(candidates) > MAX_CANDIDATES:
        raise ValueError("candidate count exceeds budget")
    ids, instance_total = set(), 0
    common = {"candidate_id", "kind", "bbox", "rotation", "confidence", "limitations"}
    for candidate in candidates:
        if type(candidate) is not dict or candidate.get("kind") not in ("ocr", "repetition"):
            raise ValueError("unsupported candidate kind")
        ocr = candidate["kind"] == "ocr"
        _fields(candidate, common | ({"text"} if ocr else {"instances", "count"}))
        _identifier(candidate["candidate_id"])
        if candidate["candidate_id"] in ids:
            raise ValueError("duplicate candidate ID")
        ids.add(candidate["candidate_id"])
        _bbox(candidate["bbox"], source["roi"])
        _number(candidate["rotation"], -180, 180, "rotation")
        _number(candidate["confidence"], 0, 1, "confidence")
        _strings(candidate["limitations"])
        if ocr:
            if type(candidate["text"]) is not str or not 1 <= len(candidate["text"]) <= 10000:
                raise ValueError("invalid OCR text")
        else:
            boxes = candidate["instances"]
            if type(boxes) is not list or not 1 <= len(boxes) <= MAX_CANDIDATES:
                raise ValueError("invalid repetition instances")
            instance_total += len(boxes)
            if instance_total > MAX_CANDIDATES:
                raise ValueError("repetition instance budget exceeded")
            if type(candidate["count"]) is not int or candidate["count"] != len(boxes):
                raise ValueError("count must equal proposed instance count")
            for box in boxes:
                _bbox(box, candidate["bbox"])
            if len({tuple(box) for box in boxes}) != len(boxes):
                raise ValueError("duplicate repetition instance")
    return {"schema_version": "0.1", "kind": "validated_external_candidates", "valid": True,
            "document": copy.deepcopy(doc), "adapter_status": candidate_adapter_status(),
            "scene_mutation": False,
            "limitations": ["External proposals only; text accuracy is unverified.",
                            "Repetition count is not a scientific molecule or cell count."]}

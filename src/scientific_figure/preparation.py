"""Explicit request preparation; no model selection or runtime integration claim."""
from __future__ import annotations

import json
from pathlib import Path
import re

from .evidence import (_hash, _now, _read_bounded, _receipt, _staged_directory,
                       _write_json, generate_evidence)
from .scene import load_json, schema_validate

MAX_REQUEST_BYTES = 1024 * 1024


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def prepare(request_path, output):
    """Prepare all explicit inputs atomically; paths resolve against the request.

    A single_asset request has exactly one input. An asset_bundle has one or more.
    The receipt and input-manifest explicitly report preflight_status as
    not_integrated: these CPU diagnostics cannot verify a model runtime hookup.
    """
    request_path = Path(request_path).resolve()
    request_bytes = _read_bounded(request_path, MAX_REQUEST_BYTES)
    request = json.loads(request_bytes.decode("utf-8"), object_pairs_hook=_unique,
                         parse_constant=lambda value: (_ for _ in ()).throw(
                             ValueError(f"nonfinite JSON value: {value}")))
    schema_validate(request, "preparation-request")
    ids = [item["id"] for item in request["inputs"]]
    if len({name.casefold() for name in ids}) != len(ids):
        raise ValueError("input IDs must be unique, including case")
    for name in ids:
        if re.fullmatch(r"(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", name):
            raise ValueError("input ID is a reserved filename")
    with _staged_directory(output) as (stage, target):
        (stage / "inputs").mkdir()
        entries = []
        for item in request["inputs"]:
            path = Path(item["path"])
            if not path.is_absolute():
                path = request_path.parent / path
            path = path.resolve()
            generated = stage / "inputs" / item["id"]
            generate_evidence(path, generated, channels=item.get("channels"),
                              region=item.get("roi"),
                              background=item.get("background", request.get("background")))
            evidence = load_json(generated / "evidence.json")
            prefix = f"inputs/{item['id']}"
            entries.append({"id": item["id"], "source_path": str(path),
                            "source_sha256": evidence["source"]["sha256"],
                            "roi": evidence["source"]["roi"], "channels": evidence["channels"],
                            "evidence": f"{prefix}/evidence.json",
                            "evidence_sha256": _hash((generated / "evidence.json").read_bytes()),
                            "manifest": f"{prefix}/manifest.json",
                            "manifest_sha256": _hash((generated / "manifest.json").read_bytes())})
        manifest = {"schema_version": "0.1", "kind": "preparation_manifest",
                    "task": request["task"], "preflight_status": "not_integrated",
                    "created_at": _now(),
                    "request": {"path": str(request_path), "sha256": _hash(request_bytes)},
                    "inputs": entries}
        schema_validate(manifest, "preparation-manifest")
        _write_json(stage / "input-manifest.json", manifest)
    return {"schema_version": "0.1", "directory": str(target.resolve()),
            "input_manifest_path": str(target.resolve() / "input-manifest.json"),
            "task": request["task"], "preflight_status": "not_integrated",
            "inputs": [{"id": entry["id"], **_receipt(target / "inputs" / entry["id"], entry["channels"])}
                       for entry in entries]}

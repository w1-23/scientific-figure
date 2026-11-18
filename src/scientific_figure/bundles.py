"""Immutable, byte-bound collections of complete editable projects.

Historical source hashes and runtime font paths are evidence, not file references.
Project artifacts are contained; fonts remain declared family/SHA dependencies and
are never copied from the host. A bundle makes no review declarations.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import tempfile

from PIL import Image

from .project import (PROJECT_JSON_EXTRAS, digest, json_text, load_project,
                      project_lock)
from .reviews import artifact_binding, content_digest
from .scene import load_json, manifest, schema_validate
from .svg import to_svg

MAX_FILES = 4096
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
_HASH = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED = {"scene.json", "figure.svg", "preview.png", "objects.json", "qa.json", "review.json"}
_OPTIONAL = set(PROJECT_JSON_EXTRAS) | {"reference.png", "comparison.png", "overlay.png"}


def _safe_path(name):
    if (not isinstance(name, str) or not name or len(name) > 240 or "\\" in name
            or ":" in name or "\x00" in name or PureWindowsPath(name).drive):
        raise ValueError("invalid artifact path")
    path = PurePosixPath(name)
    if path.is_absolute() or any(p in {"", ".", ".."} or p.endswith((" ", ".")) for p in name.split("/")):
        raise ValueError("artifact path escapes bundle")
    return path


def _no_link(path):
    if path.is_symlink() or path.is_junction():
        raise ValueError(f"symlinks and junctions are forbidden: {path.name}")


def _tree(directory):
    """Inventory without following links or allocating unbounded file listings."""
    root = Path(directory).absolute()
    for parent in (root, *root.parents):
        _no_link(parent)
    if not root.is_dir():
        raise ValueError("project/bundle directory does not exist")
    result, directories, total = {}, set(), 0
    def walk(folder, depth=0):
        nonlocal total
        if depth > 8:
            raise ValueError("artifact directory depth exceeds limit")
        with os.scandir(folder) as entries:
            for entry in entries:
                path = Path(entry.path)
                name = path.relative_to(root).as_posix()
                _safe_path(name)
                _no_link(path)
                if entry.is_dir(follow_symlinks=False):
                    directories.add(name)
                    if len(directories) > MAX_FILES:
                        raise ValueError("too many artifact directories")
                    walk(path, depth+1)
                elif entry.is_file(follow_symlinks=False):
                    size = entry.stat(follow_symlinks=False).st_size
                    total += size
                    if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES or len(result) >= MAX_FILES:
                        raise ValueError("artifact size/count limit exceeded")
                    result[name] = {"sha256": digest(path), "bytes": size}
                else:
                    raise ValueError("only regular artifact files are allowed")
    walk(root)
    return result, directories


def _inventory_matches(actual, declared):
    if not isinstance(declared, dict) or set(actual) != set(declared):
        raise ValueError("missing or undeclared artifact files")
    for name, info in declared.items():
        _safe_path(name)
        if (not isinstance(info, dict) or set(info) != {"sha256", "bytes"}
                or not isinstance(info["sha256"], str) or not _HASH.fullmatch(info["sha256"])
                or type(info["bytes"]) is not int or not 0 <= info["bytes"] <= MAX_FILE_BYTES):
            raise ValueError("invalid artifact hash/byte declaration")
        if actual[name] != info:
            raise ValueError(f"artifact hash/size mismatch: {name}")


def _runtime(receipt):
    runtime = receipt.get("runtime")
    if (not isinstance(runtime, dict) or not {"renderer", "renderer_version", "device", "font"} <= runtime.keys()
            or any(not isinstance(runtime[k], str) or not runtime[k] for k in ("renderer", "renderer_version", "device"))):
        raise ValueError("invalid member runtime")
    font = runtime["font"]
    if (not isinstance(font, dict) or not {"family", "path", "sha256"} <= font.keys()
            or any(not isinstance(font[k], str) or not font[k] for k in ("family", "path", "sha256"))
            or not _HASH.fullmatch(font["sha256"])):
        raise ValueError("invalid member font")
    return runtime


def _verified_project(directory):
    actual, directories = _tree(directory)
    if directories or not _REQUIRED | {"receipt.json"} <= set(actual):
        raise ValueError("member project is missing required artifacts or has directories")
    if set(actual) - _REQUIRED - _OPTIONAL - {"receipt.json"}:
        raise ValueError("undeclared/unsupported project files")
    root = Path(directory)
    receipt = load_json(root / "receipt.json")
    if (not isinstance(receipt, dict) or receipt.get("schema_version") != "0.1"
            or receipt.get("status") != "success" or type(receipt.get("revision")) is not int
            or receipt["revision"] < 0):
        raise ValueError("invalid member receipt")
    _inventory_matches({k: v for k, v in actual.items() if k != "receipt.json"}, receipt.get("artifacts"))
    runtime = _runtime(receipt)
    scale = receipt.get("render_scale")
    if isinstance(scale, bool) or not isinstance(scale, (float, int)) or not 0 < scale <= 16:
        raise ValueError("invalid member render scale")
    scene = load_project(root)
    if len(scene.get("resources", [])) > 3000:
        raise ValueError("member resource count exceeds limit")
    # Re-emission checks the complete SVG subset and rejects external URLs/scripts.
    if (root / "figure.svg").read_text(encoding="utf-8") != to_svg(scene):
        raise ValueError("member SVG does not match editable Scene")
    if load_json(root / "objects.json") != manifest(scene):
        raise ValueError("member object inventory does not match Scene")
    for name in set(actual) & (_OPTIONAL | {"qa.json", "review.json"}):
        if name.endswith(".json") and not isinstance(load_json(root / name), dict):
            raise ValueError("member JSON artifacts must be objects")
    qa = load_json(root / "qa.json")
    if (qa.get("execution") != "completed" or qa.get("geometry_validation") != "passed"
            or qa.get("vector_only") is not True or type(qa.get("object_count")) is not int
            or qa["object_count"] != len(scene["objects"])):
        raise ValueError("invalid member mechanical QA")
    for name in actual:
        if name.endswith(".png"):
            try:
                with Image.open(root / name) as image:
                    if image.format != "PNG" or image.width * image.height > 40_000_000:
                        raise ValueError("invalid member image or image size limit")
                    image.verify()
            except (OSError, SyntaxError, Image.DecompressionBombError) as error:
                raise ValueError("invalid member image artifact") from error
    review = load_json(root / "review.json")
    binding = artifact_binding(scene, root / "preview.png", root / "reference.png", runtime, scale)
    if review.get("binding") != binding:
        raise ValueError("member review artifact binding mismatch")
    for section in ("visual", "scientific"):
        item = review.get(section)
        if (not isinstance(item, dict) or item.get("status") not in
                {"not_executed", "passed", "uncertain", "stale_after_change", "failed"}
                or not isinstance(item.get("findings"), list)):
            raise ValueError("invalid member review section")
    source = scene.get("provenance", {}).get("source")
    if source is not None and (not isinstance(source, dict) or not isinstance(source.get("sha256"), str)
                               or not _HASH.fullmatch(source["sha256"])):
        raise ValueError("invalid member source evidence")
    return scene, receipt


def _asset_record(asset_id, scene, receipt, member_digest):
    canvas = copy.deepcopy(scene["canvas"])
    runtime = copy.deepcopy(receipt["runtime"])
    return {"id": asset_id, "project": f"assets/{asset_id}", "member_receipt_sha256": member_digest,
            "id_namespace": {"objects": [o["id"] for o in scene["objects"]],
                             "resources": [r["id"] for r in scene.get("resources", [])]},
            "source": copy.deepcopy(scene.get("provenance", {}).get("source")),
            "canvas": canvas, "viewBox": canvas.get("viewBox", [0, 0, canvas["width"], canvas["height"]]),
            "font": {"family": runtime["font"]["family"], "sha256": runtime["font"]["sha256"]},
            "runtime": runtime, "runtime_sha256": content_digest(runtime)}


def validate_bundle(directory):
    """Return status, bundle path/digest, asset_count and the verified manifest.

    Every root/member hash, required artifact, exact file set and metadata binding
    is checked. Local object/resource IDs are scoped per asset, never globally.
    """
    root = Path(directory).absolute()
    actual, directories = _tree(root)
    if not {"manifest.json", "receipt.json"} <= set(actual):
        raise ValueError("bundle is missing manifest or receipt")
    receipt = load_json(root / "receipt.json")
    schema_validate(receipt, "bundle-receipt")
    _inventory_matches({k: v for k, v in actual.items() if k != "receipt.json"}, receipt["artifacts"])
    document = load_json(root / "manifest.json")
    schema_validate(document, "bundle-manifest")
    ids, expected_files, expected_dirs = set(), {"manifest.json", "receipt.json"}, {"assets"}
    for asset in document["assets"]:
        aid = asset["id"]
        # Windows names are case insensitive; reject ambiguous aliases on all hosts.
        if aid.casefold() in ids or asset["project"] != f"assets/{aid}":
            raise ValueError("duplicate asset ID or invalid project path")
        ids.add(aid.casefold())
        _safe_path(asset["project"])
        scene, member = _verified_project(root / asset["project"])
        expected = _asset_record(aid, scene, member, digest(root / asset["project"] / "receipt.json"))
        if asset != expected:
            raise ValueError("asset metadata does not match verified member")
        expected_dirs.add(asset["project"])
        expected_files.update(f"{asset['project']}/{name}" for name in member["artifacts"])
        expected_files.add(f"{asset['project']}/receipt.json")
    if set(actual) != expected_files or directories != expected_dirs:
        raise ValueError("undeclared bundle files/directories")
    return {"status": "valid", "bundle": str(root.resolve()), "bundle_sha256": digest(root / "receipt.json"),
            "asset_count": len(document["assets"]), "manifest": document}


def create_bundle(plan_path, output):
    """Copy a 0.1 asset plan into a new, staged and immutable bundle directory."""
    plan_path = Path(plan_path).absolute()
    plan = load_json(plan_path)
    schema_validate(plan, "bundle-plan")
    output = Path(output).absolute()
    members, ids = [], set()
    for asset in plan["assets"]:
        aid, relative = asset["id"], asset["project"]
        _safe_path(aid)
        if aid.casefold() in ids:
            raise ValueError("duplicate asset ID")
        ids.add(aid.casefold())
        # Plans may refer to siblings, but cannot use absolute or platform-specific paths.
        if (PurePosixPath(relative).is_absolute() or PureWindowsPath(relative).drive
                or "\\" in relative or ":" in relative or "\x00" in relative):
            raise ValueError("project path must be relative to the bundle plan")
        project = plan_path.parent / relative
        if output.resolve().is_relative_to(project.resolve()):
            raise ValueError("bundle output cannot be inside a member project")
        scene, receipt = _verified_project(project)
        members.append((aid, project, scene, receipt))
    output.parent.mkdir(parents=True, exist_ok=True)
    with project_lock(output):
        if os.path.lexists(output):
            raise ValueError("bundle output already exists; bundles are immutable")
        stage = Path(tempfile.mkdtemp(prefix="."+output.name+".stage-", dir=output.parent))
        try:
            (stage / "assets").mkdir()
            records = []
            for aid, project, scene, receipt in members:
                shutil.copytree(project, stage / "assets" / aid, symlinks=True)
                records.append(_asset_record(aid, scene, receipt, digest(stage / "assets" / aid / "receipt.json")))
            document = {"schema_version": "0.1", "kind": "scientific-figure-asset-bundle", "assets": records}
            (stage / "manifest.json").write_text(json_text(document), encoding="utf-8")
            artifacts, _ = _tree(stage)
            receipt = {"schema_version": "0.1", "kind": "scientific-figure-asset-bundle", "status": "success", "artifacts": artifacts}
            (stage / "receipt.json").write_text(json_text(receipt), encoding="utf-8")
            verified = validate_bundle(stage)
            stage.rename(output)
            return {"status": "success", "bundle": str(output.resolve()),
                    "bundle_sha256": verified["bundle_sha256"], "asset_count": verified["asset_count"]}
        finally:
            if stage.exists():
                shutil.rmtree(stage)

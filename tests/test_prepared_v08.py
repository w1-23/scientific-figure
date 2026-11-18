"""Real prepared directories and externally supplied, source-bound candidates."""
import base64
import copy
import hashlib
import json

from PIL import Image, ImageDraw
import pytest

from scientific_figure.preparation import prepare
from scientific_figure.prepared import (COORDINATE_SPACE, MAX_PACKAGE_BYTES,
                                        candidate_adapter_status, consume,
                                        runtime_preflight, validate_candidates)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, value):
    path.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")


@pytest.fixture
def package(tmp_path):
    image_path = tmp_path / "input.png"
    image = Image.new("RGB", (96, 72), "white")
    ImageDraw.Draw(image).rectangle((20, 20, 50, 40), fill="#203040")
    image.save(image_path)
    request_path = tmp_path / "request.json"
    save(request_path, {"schema_version": "0.1", "task": "single_asset",
                        "inputs": [{"id": "main", "path": "input.png", "roi": [10, 8, 60, 40]}]})
    root = tmp_path / "prepared"
    receipt = prepare(request_path, root)
    return root, image_path, receipt


def repair_outer(root):
    manifest = read(root / "input-manifest.json")
    for entry in manifest["inputs"]:
        for field in ("evidence", "manifest"):
            entry[field + "_sha256"] = digest(root / entry[field])
    save(root / "input-manifest.json", manifest)


def rewrite_evidence(root, edit):
    folder = root / "inputs/main"
    evidence = read(folder / "evidence.json")
    manifest = read(folder / "manifest.json")
    edit(evidence, manifest)
    save(folder / "evidence.json", evidence)
    record = next(r for r in manifest["artifacts"] if r["path"] == "evidence.json")
    record.update(sha256=digest(folder / "evidence.json"), bytes=(folder / "evidence.json").stat().st_size)
    save(folder / "manifest.json", manifest)
    repair_outer(root)


def test_consumes_actual_content_and_never_claims_runtime_integration(package):
    root, original, receipt = package
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = consume(receipt, views=["source.png", "evidence_board.png"],
                     host_stage="before_model_request",
                     expected_manifest_sha256=digest(root / "input-manifest.json"))
    assert result["status"] == "consumed" and result["boundary"] == "local_tool_return"
    assert result["runtime_preflight"] == "not_integrated"
    assert result["host_call_order_verified"] is False
    assert result["inputs"][0]["source_sha256"] == digest(original)
    assert result["inputs"][0]["roi"] == [10, 8, 60, 40]
    assert result["inputs"][0]["mappings"]["source_to_local"] == [1, 0, 0, 1, -10, -8]
    for view in result["inputs"][0]["views"]:
        content = base64.b64decode(view["content"], validate=True)
        assert content == (root / view["path"]).read_bytes()
        assert len(content) == view["bytes"]
        assert hashlib.sha256(content).hexdigest() == view["sha256"]
    assert result["package_bytes"] == sum(p.stat().st_size for p in before)
    assert before == {p: p.read_bytes() for p in before}
    again = consume(root, views=["source.png", "evidence_board.png"])
    assert result["package_sha256"] == again["package_sha256"]
    assert runtime_preflight()["status"] == "not_integrated"
    assert runtime_preflight()["adapter_status"] == "adapter_not_available"
    with pytest.raises(TypeError):
        runtime_preflight({"status": "integrated", "first_request": True})
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("change", [
    lambda r: r["inputs"][0].update(board_path="somewhere-else.png"),
    lambda r: r["inputs"][0].update(valid=1),
    lambda r: r.update(preflight_status="integrated"),
])
def test_rejects_forged_prepare_receipt(package, change):
    _, _, receipt = package
    change(receipt)
    with pytest.raises(ValueError, match="receipt"):
        consume(receipt)


@pytest.mark.parametrize("filename", ["source.png", "evidence_board.png", "evidence.json", "manifest.json"])
def test_missing_actual_evidence_file(package, filename):
    root, _, _ = package
    (root / "inputs/main" / filename).unlink()
    with pytest.raises(ValueError, match="missing"):
        consume(root)


def test_rejects_wrong_manifest_anchor_and_artifact_bytes(package):
    root, _, _ = package
    with pytest.raises(ValueError, match="manifest hash"):
        consume(root, expected_manifest_sha256="0" * 64)
    path = root / "inputs/main/contrast_edges.png"
    data = path.read_bytes()
    path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    with pytest.raises(ValueError, match="hash"):
        consume(root)


def test_rejects_wrong_original_hash(package):
    root, original, _ = package
    Image.new("RGB", (96, 72), "red").save(original)
    with pytest.raises(ValueError, match="original source hash"):
        consume(root)


def test_original_source_is_required(package):
    root, original, _ = package
    original.unlink()
    with pytest.raises((ValueError, OSError)):
        consume(root)


@pytest.mark.parametrize("change, message", [
    (lambda m: m["inputs"][0].update(evidence="inputs/other/evidence.json"), "input path"),
    (lambda m: m["inputs"][0].update(roi=[11, 8, 60, 40]), "ROI"),
    (lambda m: m["inputs"][0].update(source_sha256="0" * 64), "source"),
    (lambda m: m["inputs"][0].update(evidence="../evidence.json"), "does not match"),
])
def test_outer_path_roi_and_provenance_binding(package, change, message):
    root, _, _ = package
    manifest = read(root / "input-manifest.json")
    change(manifest)
    save(root / "input-manifest.json", manifest)
    with pytest.raises(ValueError, match=message):
        consume(root)


def test_rejects_mapping_rewrite_even_with_repaired_hashes(package):
    root, _, _ = package
    rewrite_evidence(root, lambda ev, _: ev["mappings"]["source_to_local"].__setitem__(4, -11))
    with pytest.raises(ValueError, match="coordinate mapping"):
        consume(root)


def test_rejects_consistently_rehashed_wrong_source_crop(package):
    root, _, _ = package
    path = root / "inputs/main/source.png"
    Image.new("RGB", (60, 40), "blue").save(path)
    def update(evidence, manifest):
        for records in (evidence["artifacts"], manifest["artifacts"]):
            record = next(r for r in records if r["path"] == "source.png")
            record.update(sha256=digest(path), bytes=path.stat().st_size)
    rewrite_evidence(root, update)
    with pytest.raises(ValueError, match="actual original ROI"):
        consume(root)


def test_request_is_hash_bound(package):
    root, _, _ = package
    request_path = root.parent / "request.json"
    request_path.write_bytes(request_path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="request hash"):
        consume(root)


def test_budgets_selections_and_unexpected_files(package):
    root, _, _ = package
    for kwargs in ({"max_bytes": 10}, {"max_bytes": MAX_PACKAGE_BYTES + 1},
                   {"max_bytes": True}, {"views": ["../source.png"]},
                   {"views": ["palette.png"]}, {"views": []}, {"input_ids": ["absent"]},
                   {"input_ids": "main"}, {"views": ["source.png", "source.png"]}):
        with pytest.raises(ValueError):
            consume(root, **kwargs)
    (root / "extra").write_text("unexpected")
    with pytest.raises(ValueError, match="unexpected"):
        consume(root)


def test_symlink_input_directory_rejected(package, tmp_path):
    root, _, _ = package
    folder = root / "inputs/main"
    moved = tmp_path / "outside"
    folder.rename(moved)
    try:
        folder.symlink_to(moved, target_is_directory=True)
    except OSError:
        pytest.skip("local account cannot create symlinks")
    with pytest.raises(ValueError, match="linked"):
        consume(root)


def test_bundle_validates_unselected_input_too(package):
    root, original, _ = package
    request = root.parent / "bundle.json"
    save(request, {"schema_version": "0.1", "task": "asset_bundle",
                   "inputs": [{"id": name, "path": str(original)} for name in ("a", "b")]})
    output = root.parent / "bundle"
    prepare(request, output)
    result = consume(output, input_ids=["a"])
    assert [entry["id"] for entry in result["inputs"]] == ["a"]
    (output / "inputs/b/source.png").unlink()
    with pytest.raises(ValueError, match="missing"):
        consume(output, input_ids=["a"])


def candidates(original):
    return {"schema_version": "0.1", "kind": "external_candidates", "provider": "host supplied",
            "image_sha256": digest(original), "coordinate_space": COORDINATE_SPACE,
            "source_size": [96, 72], "roi": [10, 8, 60, 40],
            "candidates": [
                {"candidate_id": "label", "kind": "ocr", "bbox": [12, 10, 20, 10],
                 "rotation": 0, "confidence": 0.7, "limitations": ["PTL may be ambiguous"], "text": "PTL"},
                {"candidate_id": "repeat", "kind": "repetition", "bbox": [20, 20, 30, 20],
                 "rotation": 15, "confidence": 0.5, "limitations": [],
                 "instances": [[20, 20, 5, 5], [35, 25, 5, 5]], "count": 2}]}


def test_external_candidates_are_bound_inert_and_do_not_install_models(package):
    _, original, _ = package
    doc = candidates(original)
    before = copy.deepcopy(doc)
    result = validate_candidates(doc, original, roi=[10, 8, 60, 40])
    assert result["valid"] and result["document"] == doc == before
    assert result["scene_mutation"] is False
    assert result["adapter_status"]["ocr"] == "unavailable"
    assert candidate_adapter_status()["repetition"] == "unavailable"
    result["document"]["candidates"][0]["text"] = "changed"
    assert doc == before


@pytest.mark.parametrize("change", [
    lambda d: d.update(image_sha256="0" * 64),
    lambda d: d.update(coordinate_space="board"),
    lambda d: d.update(roi=[0, 0, 60, 40]),
    lambda d: d.update(source_size=[72, 96]),
    lambda d: d["candidates"][0].update(bbox=[9, 10, 20, 10]),
    lambda d: d["candidates"][0].update(bbox=[60, 10, 20, 10]),
    lambda d: d["candidates"][0].update(bbox=[12, 10, 0, 10]),
    lambda d: d["candidates"][0].update(rotation=True),
    lambda d: d["candidates"][0].update(rotation=10 ** 1000),
    lambda d: d["candidates"][0].update(confidence=1.1),
    lambda d: d["candidates"][0].update(confidence=float("nan")),
    lambda d: d["candidates"][1].update(count=True),
    lambda d: d["candidates"][1].update(count=3),
    lambda d: d["candidates"][1].update(instances=[[10, 10, 5, 5]], count=1),
    lambda d: d["candidates"][1].update(instances=[[20, 20, 5, 5]] * 2),
    lambda d: d["candidates"][1].update(candidate_id="label"),
    lambda d: d.update(executable="run-ocr.exe"),
    lambda d: d["candidates"][0].pop("limitations"),
])
def test_external_candidate_wrong_hash_coords_bounds_and_fields(package, change):
    _, original, _ = package
    doc = candidates(original)
    change(doc)
    with pytest.raises(ValueError):
        validate_candidates(doc, original, roi=[10, 8, 60, 40])

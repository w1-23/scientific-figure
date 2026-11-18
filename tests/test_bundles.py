import json
import os

import pytest

from scientific_figure.bundles import create_bundle, validate_bundle
from scientific_figure.project import digest, load_project, write_project


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def asset_scene():
    return {"schema_version": "0.1", "revision": 0,
            "canvas": {"width": 80, "height": 50, "viewBox": [10, 20, 80, 50], "background": "none"},
            "resources": [{"id": "shade", "kind": "linear_gradient", "start": [0, 0], "end": [1, 0],
                           "stops": [[0, "#ff0000"], [1, "#0000ff"]]}],
            "objects": [
                {"id": "group", "kind": "group", "style": {"opacity": 0.5}},
                {"id": "left", "kind": "rect", "parent": "group",
                 "geometry": {"x": 10, "y": 20, "width": 15, "height": 15},
                 "style": {"fill": "url(#shade)", "stroke": "url(#shade)", "stroke_width": 1},
                 "semantic": {"name": "left", "peer": {"object_id": "right"},
                              "palette": {"type": "resource", "id": "shade"},
                              "object_ids": ["left", "right"]}},
                {"id": "right", "kind": "rect", "parent": "group",
                 "geometry": {"x": 50, "y": 20, "width": 15, "height": 15}},
                {"id": "wire", "kind": "connector", "parent": "group",
                 "geometry": {"from": "left", "to": "right"}, "style": {"stroke": "#000000"}}]}


@pytest.fixture
def bundle(tmp_path):
    original = tmp_path / "original"
    write_project(asset_scene(), original, scale=1)
    plan = save(tmp_path / "bundle-plan.json", {"schema_version": "0.1", "assets": [
        {"id": "first", "project": "original"}, {"id": "second", "project": "original"}]})
    output = tmp_path / "bundle"
    create_bundle(plan, output)
    return output


def rehash_root(bundle):
    receipt = json.loads((bundle / "receipt.json").read_text())
    for name in receipt["artifacts"]:
        receipt["artifacts"][name] = {"sha256": digest(bundle / name), "bytes": (bundle / name).stat().st_size}
    save(bundle / "receipt.json", receipt)


def test_complete_sourcefree_assets_with_shared_local_names_are_selfcontained(bundle, tmp_path):
    result = validate_bundle(bundle)
    assert result["status"] == "valid" and result["asset_count"] == 2
    assert result["bundle_sha256"] == digest(bundle / "receipt.json")
    manifest = result["manifest"]
    assert manifest["assets"][0]["source"] is None
    assert manifest["assets"][0]["id_namespace"] == manifest["assets"][1]["id_namespace"]
    assert manifest["assets"][0]["viewBox"] == [10, 20, 80, 50]
    assert set(manifest) == {"schema_version", "kind", "assets"}
    import shutil
    shutil.rmtree(tmp_path / "original")
    for entry in manifest["assets"]:
        member = bundle / entry["project"]
        assert load_project(member) == asset_scene()
        assert (member / "review.json").is_file()
        assert set(entry["font"]) == {"family", "sha256"}
        assert entry["font"]["sha256"] == entry["runtime"]["font"]["sha256"]
    assert not (bundle / "fonts").exists()
    validate_bundle(bundle)


@pytest.mark.parametrize("name", ["manifest.json", "assets/first/figure.svg", "assets/first/scene.json"])
def test_changed_hash_is_rejected(bundle, name):
    with (bundle / name).open("ab") as handle:
        handle.write(b" ")
    with pytest.raises(ValueError, match="hash/size"):
        validate_bundle(bundle)


@pytest.mark.parametrize("name", ["../outside", "/absolute", "assets/first/../scene.json", "C:/escape", "assets\\escape"])
def test_receipt_path_escape_rejected(bundle, name):
    receipt = json.loads((bundle / "receipt.json").read_text())
    receipt["artifacts"][name] = {"sha256": "0" * 64, "bytes": 0}
    save(bundle / "receipt.json", receipt)
    with pytest.raises(ValueError):
        validate_bundle(bundle)


def test_undeclared_file_and_empty_directory_rejected(bundle):
    (bundle / "extra.txt").write_text("undeclared")
    with pytest.raises(ValueError, match="undeclared"):
        validate_bundle(bundle)
    (bundle / "extra.txt").unlink()
    (bundle / "extra").mkdir()
    with pytest.raises(ValueError, match="undeclared"):
        validate_bundle(bundle)


def test_missing_required_member_artifact_rejected_even_after_rehash(bundle):
    path = bundle / "assets/first/review.json"
    path.unlink()
    member = json.loads((path.parent / "receipt.json").read_text())
    del member["artifacts"]["review.json"]
    save(path.parent / "receipt.json", member)
    receipt = json.loads((bundle / "receipt.json").read_text())
    del receipt["artifacts"]["assets/first/review.json"]
    save(bundle / "receipt.json", receipt)
    rehash_root(bundle)
    with pytest.raises(ValueError, match="required"):
        validate_bundle(bundle)


@pytest.mark.parametrize("value", [True, -1, "123"])
def test_bad_artifact_types_rejected(bundle, value):
    receipt = json.loads((bundle / "receipt.json").read_text())
    receipt["artifacts"]["manifest.json"]["bytes"] = value
    save(bundle / "receipt.json", receipt)
    with pytest.raises(ValueError):
        validate_bundle(bundle)


def test_metadata_forgery_rejected_after_root_rehash(bundle):
    manifest = json.loads((bundle / "manifest.json").read_text())
    manifest["assets"][0]["viewBox"][0] = 999
    save(bundle / "manifest.json", manifest)
    rehash_root(bundle)
    with pytest.raises(ValueError, match="metadata"):
        validate_bundle(bundle)


def test_bundle_is_immutable_and_invalid_creation_leaves_no_output(bundle, tmp_path):
    before = (bundle / "receipt.json").read_bytes()
    plan = tmp_path / "bundle-plan.json"
    with pytest.raises(ValueError, match="immutable"):
        create_bundle(plan, bundle)
    assert (bundle / "receipt.json").read_bytes() == before
    source = tmp_path / "original"
    (source / "undeclared").write_text("bad")
    output = tmp_path / "failed"
    with pytest.raises(ValueError):
        create_bundle(plan, output)
    assert not output.exists()
    assert not list(tmp_path.glob(".failed*"))


def test_symlink_rejected_when_supported(bundle, tmp_path):
    link = bundle / "linked"
    try:
        os.symlink(tmp_path / "original", link, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(ValueError, match="symlinks"):
        validate_bundle(bundle)


def test_external_svg_reference_rejected_after_member_hash_is_rebound(bundle):
    member = bundle / "assets/first"
    svg = member / "figure.svg"
    svg.write_text(svg.read_text().replace("#000000", "url(https://external.invalid/paint)"))
    receipt = json.loads((member / "receipt.json").read_text())
    receipt["artifacts"]["figure.svg"] = {"sha256": digest(svg), "bytes": svg.stat().st_size}
    save(member / "receipt.json", receipt)
    rehash_root(bundle)
    with pytest.raises(ValueError, match="SVG"):
        validate_bundle(bundle)


def test_inventory_limits_are_enforced(bundle, monkeypatch):
    import scientific_figure.bundles as module
    monkeypatch.setattr(module, "MAX_TOTAL_BYTES", 10)
    with pytest.raises(ValueError, match="limit"):
        validate_bundle(bundle)


def test_forged_member_byte_type_is_rejected_after_root_rehash(bundle):
    receipt_path = bundle / "assets/first/receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["artifacts"]["scene.json"]["bytes"] = True
    save(receipt_path, receipt)
    rehash_root(bundle)
    with pytest.raises(ValueError, match="declaration"):
        validate_bundle(bundle)


def test_absolute_plan_member_and_duplicate_asset_ids_rejected(tmp_path):
    for assets in ([{"id": "asset", "project": str(tmp_path)}],
                   [{"id": "asset", "project": "."}, {"id": "Asset", "project": "."}]):
        plan = save(tmp_path / "plan.json", {"schema_version": "0.1", "assets": assets})
        with pytest.raises(ValueError):
            create_bundle(plan, tmp_path / "output")
        assert not (tmp_path / "output").exists()

"""Local registry behavior, attribution closure, digest pins and path confinement."""
from copy import deepcopy
import hashlib
from importlib.resources import files
from io import BytesIO
import json
from pathlib import Path
import shutil

from PIL import Image
import pytest

from scientific_figure import recipes, registry
from scientific_figure.editing import apply_patch, export_scene
from scientific_figure.project import load_project, write_project
from scientific_figure.render import render_svg
from scientific_figure.scene import validate
from scientific_figure.svg import to_svg


ASSET_ID = "twemoji.lemon"
VERSION = "14.0.2"
ORIGINAL = "d61acad4354635834f30e08921b03c2412f5bf0669846f8a93c7ac085b0315da"
DISTRIBUTED = "7cf1a9607d3532651fe5355f34c3340097ec5aa301f23a1519caba0f0abf348b"


@pytest.fixture
def local_catalog(tmp_path):
    root = tmp_path / "registry"
    shutil.copytree(files("scientific_figure").joinpath("data", "registry"), root)
    return root


def read_manifest(root):
    return json.loads((root / "catalog.json").read_text(encoding="utf-8"))


def save_manifest(root, data):
    (root / "catalog.json").write_text(json.dumps(data), encoding="utf-8")


def asset_record(data):
    return next(item for item in data["entries"] if item["id"] == ASSET_ID)


def test_builtin_catalog_separates_three_kinds_and_uses_trusted_recipe_contracts():
    entries = registry.list_entries()
    assert {entry["kind"] for entry in entries} == {"recipe", "symbol_family", "external_asset"}
    assert {entry["recipe_id"] for entry in entries if entry["kind"] == "recipe"} == {
        item["id"] for item in recipes.list_recipes()}
    particle = registry.describe("particle", version="1.0.0")
    assert particle["recipe_spec"] == recipes.describe("particle")
    assert particle["license"]["identifier"] == "project_license_unset"
    family = registry.resolve("family.fruit", version="1.0.0")
    assert family["kind"] == "symbol_family" and "asset" not in family
    for metadata in [particle, family]:
        with pytest.raises(ValueError, match="external_asset"):
            registry.load_asset(metadata)
    entries[0]["source"]["author"] = "changed"
    assert registry.list_entries()[0]["source"]["author"] != "changed"
    assert registry.validate_registry()["status"] == "valid"


def test_distributed_hash_is_distinct_from_upstream_and_attribution_is_complete():
    entry = registry.resolve(ASSET_ID, version=VERSION, expected_sha256=DISTRIBUTED)
    assert entry["asset"]["sha256"] == DISTRIBUTED
    assert entry["source"]["original_sha256"] == ORIGINAL != DISTRIBUTED
    assert entry["source"]["author"] == "Twitter, Inc. and contributors"
    assert entry["source"]["ref"] == "v14.0.2"
    assert entry["source"]["url"].endswith("/v14.0.2/assets/svg/1f34b.svg")
    assert entry["license"]["identifier"] == "CC-BY-4.0"
    assert "144" in " ".join(entry["modifications"])
    assert entry["distribution"]["scope"] == "redistributable_with_attribution"
    text = files("scientific_figure").joinpath("data", "registry", entry["license"]["text_path"]).read_bytes()
    assert b"Attribution 4.0 International" in text
    assert hashlib.sha256(text).hexdigest() == entry["license"]["text_sha256"]
    with pytest.raises(ValueError, match="expected distributed"):
        registry.resolve(ASSET_ID, version=VERSION, expected_sha256=ORIGINAL)


def test_asset_import_is_independent_editable_vector_and_project_preserves_notices(tmp_path):
    entry = registry.resolve(ASSET_ID, version=VERSION)
    scene = registry.load_asset(entry)
    again = registry.load_asset(entry)
    assert scene == again
    assert validate(scene) is scene
    assert scene["canvas"]["width"] == scene["canvas"]["height"] == 144
    assert all(obj["kind"] in {"group", "path"} for obj in scene["objects"])
    paths = [obj for obj in scene["objects"] if obj["kind"] == "path"]
    assert len(paths) == 4
    license_record = scene["provenance"]["registry_asset"]["entry"]["license"]
    assert license_record == entry["license"]
    assert "verified.svg" not in json.dumps(scene)
    source = files("scientific_figure").joinpath("data", "registry", entry["asset"]["path"]).read_text(encoding="utf-8")
    original_pixels = Image.open(BytesIO(render_svg(source, 1))).convert("RGBA").tobytes()
    imported_pixels = Image.open(BytesIO(render_svg(to_svg(scene), 1))).convert("RGBA").tobytes()
    assert imported_pixels == original_pixels
    selected = paths[0]["id"]
    changed = apply_patch(scene, {"base_revision": 0, "operations": [
        {"id": selected, "set": {"style": {"fill": "#123456"}}}]})
    assert again == scene
    assert [obj for obj in changed["objects"] if obj["id"] != selected] == [
        obj for obj in scene["objects"] if obj["id"] != selected]
    exported = export_scene(changed, [selected])
    assert [obj["id"] for obj in exported["objects"] if obj["kind"] == "path"] == [selected]
    exported_origin = next(obj["semantic"]["registry_asset"] for obj in exported["objects"]
                           if "registry_asset" in obj.get("semantic", {}))
    assert exported_origin["entry"]["license"] == license_record
    assert exported_origin["entry"]["source"] == entry["source"]
    write_project(changed, tmp_path / "project", scale=1)
    reopened = load_project(tmp_path / "project")
    assert reopened == changed
    assert reopened["provenance"]["registry_asset"]["entry"]["license"] == license_record


def test_custom_local_catalog_supports_directory_or_json_file_and_is_portable(local_catalog, tmp_path):
    entry = registry.resolve(ASSET_ID, version=VERSION, registry=local_catalog)
    assert registry.resolve(ASSET_ID, registry=local_catalog / "catalog.json") == entry
    scene = registry.load_asset(entry, registry=local_catalog)
    relocated = tmp_path / "moved"
    shutil.move(str(local_catalog), relocated)
    assert registry.load_asset(entry, registry=relocated) == scene
    assert str(relocated) not in json.dumps(scene)
    assert registry.validate_registry(relocated)["asset_count"] == 1


@pytest.mark.parametrize("which", ["asset", "license"])
def test_changed_asset_or_license_bytes_are_rejected_at_resolve_and_load(local_catalog, which):
    entry = registry.resolve(ASSET_ID, registry=local_catalog)
    name = entry["asset"]["path"] if which == "asset" else entry["license"]["text_path"]
    path = local_catalog / name
    path.write_bytes(path.read_bytes()+b"\nchanged")
    for action in [lambda: registry.resolve(ASSET_ID, registry=local_catalog),
                   lambda: registry.load_asset(entry, registry=local_catalog),
                   lambda: registry.validate_registry(local_catalog)]:
        with pytest.raises(ValueError, match="SHA256 mismatch"):
            action()


def test_modified_resolved_record_cannot_substitute_files_or_license(local_catalog):
    original = registry.resolve(ASSET_ID, registry=local_catalog)
    for field, value in [("asset", {**original["asset"], "path": "../outside.svg"}),
                         ("license", {**original["license"], "identifier": "MIT"}),
                         ("source", {**original["source"], "original_sha256": "0"*64})]:
        modified = {**original, field: value}
        with pytest.raises(ValueError, match="entry was changed"):
            registry.load_asset(modified, registry=local_catalog)


@pytest.mark.parametrize("path", ["../outside.svg", "/absolute.svg", "assets/../../outside.svg",
                                  "assets\\outside.svg", "C:/outside.svg", "https://example.com/a.svg",
                                  "assets//a.svg", "assets/./a.svg", "assets/NUL.svg", "assets/name."])
def test_unsafe_paths_are_rejected_as_catalog_data(local_catalog, path):
    data = read_manifest(local_catalog)
    asset_record(data)["asset"]["path"] = path
    save_manifest(local_catalog, data)
    with pytest.raises(ValueError):
        registry.list_entries(local_catalog)


@pytest.mark.parametrize("directory", [False, True])
def test_symlink_file_and_directory_escape_rejected_even_with_matching_hash(local_catalog, tmp_path, directory):
    entry = registry.resolve(ASSET_ID, registry=local_catalog)
    path = local_catalog / entry["asset"]["path"]
    outside = tmp_path / "outside"
    outside.mkdir()
    shutil.copyfile(path, outside / path.name)
    link = path.parent if directory else path
    if directory:
        shutil.rmtree(link)
        target = outside
    else:
        link.unlink()
        target = outside / path.name
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        pytest.skip(f"local Windows symlink permission unavailable: {exc}")
    with pytest.raises(ValueError, match="symlink|junction"):
        registry.resolve(ASSET_ID, registry=local_catalog)
    with pytest.raises(ValueError, match="symlink|junction"):
        registry.load_asset(entry, registry=local_catalog)


def test_duplicate_and_ambiguous_versions_never_choose_latest(local_catalog):
    data = read_manifest(local_catalog)
    duplicate = deepcopy(asset_record(data))
    data["entries"].append(duplicate)
    save_manifest(local_catalog, data)
    with pytest.raises(ValueError, match="duplicate"):
        registry.list_entries(local_catalog)
    duplicate["version"] = "14.0.3"
    save_manifest(local_catalog, data)
    with pytest.raises(ValueError, match="ambiguous"):
        registry.resolve(ASSET_ID, registry=local_catalog)
    assert registry.resolve(ASSET_ID, version=VERSION, registry=local_catalog)["version"] == VERSION
    for version in ["latest", "v14.0.2", "999.0.0"]:
        with pytest.raises(ValueError):
            registry.resolve(ASSET_ID, version=version, registry=local_catalog)


def test_unknown_fields_dynamic_modules_and_unknown_recipes_are_rejected(local_catalog):
    data = read_manifest(local_catalog)
    data["entries"][0]["module"] = "os"
    save_manifest(local_catalog, data)
    with pytest.raises(ValueError):
        registry.list_entries(local_catalog)
    del data["entries"][0]["module"]
    data["entries"].append({**registry.resolve("particle"), "id": "unsafe_alias", "recipe_id": "os.system"})
    save_manifest(local_catalog, data)
    with pytest.raises(ValueError, match="unknown built-in recipe"):
        registry.list_entries(local_catalog)


def test_original_recipes_cannot_be_relicensed_by_custom_registry(local_catalog):
    data = read_manifest(local_catalog)
    entry = registry.resolve("particle")
    entry["id"] = "renamed_particle"
    entry["license"]["identifier"] = "MIT"
    data["entries"].append(entry)
    save_manifest(local_catalog, data)
    with pytest.raises(ValueError, match="project_license_unset"):
        registry.list_entries(local_catalog)


def test_original_hash_format_and_duplicate_json_keys_are_strict(local_catalog):
    data = read_manifest(local_catalog)
    asset_record(data)["source"]["original_sha256"] = "not-a-digest"
    save_manifest(local_catalog, data)
    with pytest.raises(ValueError):
        registry.list_entries(local_catalog)
    (local_catalog / "catalog.json").write_text('{"schema_version":"0.1","schema_version":"0.1","entries":[]}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate registry JSON key"):
        registry.list_entries(local_catalog)


def test_hash_verified_but_unsupported_svg_is_not_rasterized_or_silently_stripped(local_catalog):
    data = read_manifest(local_catalog)
    entry = asset_record(data)
    bad = b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><filter id="blur"/></svg>'
    (local_catalog / entry["asset"]["path"]).write_bytes(bad)
    entry["asset"]["sha256"] = hashlib.sha256(bad).hexdigest()
    entry["modifications"].append("Test candidate with unsupported filter")
    save_manifest(local_catalog, data)
    resolved = registry.resolve(ASSET_ID, registry=local_catalog)
    with pytest.raises(ValueError):
        registry.load_asset(resolved, registry=local_catalog)


@pytest.mark.parametrize("which", ["asset", "license"])
def test_registry_asset_and_license_byte_budgets(local_catalog, which):
    data = read_manifest(local_catalog)
    entry = asset_record(data)
    name = entry["asset"]["path"] if which == "asset" else entry["license"]["text_path"]
    path = local_catalog / name
    path.write_bytes(b" "*(1_048_577 if which == "asset" else 131_073))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if which == "asset":
        entry["asset"]["sha256"] = digest
    else:
        entry["license"]["text_sha256"] = digest
    save_manifest(local_catalog, data)
    with pytest.raises(ValueError, match="byte budget"):
        registry.resolve(ASSET_ID, registry=local_catalog)

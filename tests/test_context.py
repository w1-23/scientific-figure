"""Context intent, opt-in styling and portable evidence bindings."""
import copy
import hashlib
import json
import shutil

import pytest
from PIL import Image

from scientific_figure.context import apply_context, canonical_hash, style_check, validate_context
from scientific_figure.evidence import generate_evidence
from scientific_figure.fonts import font_info
from scientific_figure.scene import schema_validate
from scientific_figure.style_profile import validate_profile


def write_json(path, document):
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


@pytest.fixture
def scene():
    return {"schema_version": "0.1", "revision": 0,
            "canvas": {"width": 100, "height": 80},
            "objects": [{"id": "body", "kind": "rect",
                         "geometry": {"x": 10, "y": 10, "width": 30, "height": 20},
                         "style": {"fill": "#ffffff"}},
                        {"id": "label", "kind": "text",
                         "geometry": {"x": 50, "y": 20, "text": "A"}}]}


@pytest.fixture
def context_files(tmp_path):
    profile = {"schema_version": "0.1", "id": "paper", "line_cap": "round", "line_join": "round",
               "tokens": [{"id": "main", "type": "color", "value": "#aaccdd"},
                          {"id": "thin", "type": "number", "value": 2}]}
    text = {"schema_version": "0.1", "id": "labels", "entries": [
        {"id": "label_A", "object_id": "label", "text": "A", "uncertainty_ids": ["identity"]}]}
    layout = {"schema_version": "0.1", "id": "layout", "entries": [
        {"id": "order", "object_ids": ["body", "label"], "relation": "left_of"}]}
    context = {"schema_version": "0.1", "id": "figure_context", "purpose": "Explain one compartment",
               "style_profile": write_json(tmp_path / "profile.json", profile),
               "text_manifest": write_json(tmp_path / "text.json", text),
               "layout_manifest": write_json(tmp_path / "layout.json", layout),
               "roles": [{"id": "compartment", "description": "Main compartment"}],
               "representations": [{"id": "outline", "kind": "boundary", "description": "Simple boundary"}],
               "uncertainties": [{"id": "identity", "description": "Host must confirm identity"}],
               "objects": [{"object_id": "body", "role_id": "compartment", "representation_id": "outline",
                            "uncertainty_ids": ["identity"], "preserve": True}],
               "token_bindings": [{"object_id": "body", "field": "fill", "token": "main"}]}
    write_json(tmp_path / "context.json", context)
    return tmp_path / "context.json", context


def test_attach_keeps_scene_styles_authoritative(scene, context_files):
    path, _ = context_files
    original = copy.deepcopy(scene)
    attached, extras = apply_context(scene, path)
    assert scene == original
    assert attached["objects"] == scene["objects"]
    assert set(extras) == {"context.json", "style-profile.json", "text-manifest.json", "layout-manifest.json"}
    assert attached["provenance"]["brief"]["protected_ids"] == ["body"]
    assert attached["provenance"]["context"]["refs"]["roles"] == ["compartment"]
    assert attached["provenance"]["context"]["refs"]["text_entries"] == ["label_A"]
    assert attached["provenance"]["context"]["sha256"] == canonical_hash(extras["context.json"])
    assert style_check(attached, extras)["status"] == "needs_review"
    before = copy.deepcopy(attached)
    report = style_check(attached, extras)
    assert attached == before
    assert report["mismatches"][0]["actual"] == "#ffffff"


def test_explicit_token_resolution(scene, context_files):
    path, _ = context_files
    attached, extras = apply_context(scene, path, resolve_tokens=True)
    assert attached["objects"][0]["style"]["fill"] == "#aaccdd"
    assert style_check(attached, extras)["status"] == "passed"
    assert scene["objects"][0]["style"]["fill"] == "#ffffff"


@pytest.mark.parametrize("defect", ["object", "role", "representation", "uncertainty", "evidence", "preserved", "token", "type", "duplicate"])
def test_invalid_refs_and_tokens_rejected_even_without_resolution(scene, context_files, defect):
    path, context = context_files
    annotation = context["objects"][0]
    if defect == "object":
        annotation["object_id"] = "missing"
    elif defect in {"role", "representation"}:
        annotation[defect + "_id"] = "missing"
    elif defect in {"uncertainty", "evidence"}:
        annotation[defect + "_ids"] = ["missing"]
    elif defect == "preserved":
        context["preserved_ids"] = ["missing"]
    elif defect == "token":
        context["token_bindings"][0]["token"] = "missing"
    elif defect == "type":
        context["token_bindings"][0]["field"] = "stroke_width"
    else:
        context["token_bindings"] *= 2
    write_json(path, context)
    with pytest.raises(ValueError):
        apply_context(scene, path)


def test_strict_documents_reject_expressions_and_unknown_fields(scene, context_files):
    path, context = context_files
    profile_path = path.parent / "profile.json"
    profile = json.loads(profile_path.read_text())
    profile["tokens"][0]["value"] = "eval('red')"
    context["style_profile"] = write_json(profile_path, profile)
    write_json(path, context)
    with pytest.raises(ValueError):
        apply_context(scene, path)
    context["schema_version"] = "0.2"
    with pytest.raises(ValueError):
        schema_validate(context, "context")
    context["schema_version"] = "0.1"
    context["execute"] = "anything"
    with pytest.raises(ValueError):
        schema_validate(context, "context")


def test_raw_dependency_hash_is_verified(scene, context_files):
    path, _ = context_files
    profile_path = path.parent / "profile.json"
    profile_path.write_text(profile_path.read_text() + " ")
    with pytest.raises(ValueError, match="hash"):
        apply_context(scene, path)


def test_hashes_and_typed_refs_reject_tampered_snapshot(scene, context_files):
    path, _ = context_files
    attached, extras = apply_context(scene, path)
    corrupted = copy.deepcopy(extras)
    corrupted["style-profile.json"]["tokens"][0]["value"] = "#123456"
    with pytest.raises(ValueError, match="hash"):
        validate_context(attached, corrupted)
    corrupted = copy.deepcopy(extras)
    corrupted["context.json"]["purpose"] = "Changed intent"
    with pytest.raises(ValueError, match="provenance"):
        validate_context(attached, corrupted)
    changed = copy.deepcopy(attached)
    changed["provenance"]["context"]["refs"]["roles"] = []
    with pytest.raises(ValueError, match="provenance"):
        validate_context(changed, extras)
    with pytest.raises(ValueError, match="missing context"):
        validate_context(attached, {})


def test_reapply_changes_provenance_digest_and_extends_existing_protecteds(scene, context_files):
    path, context = context_files
    scene["provenance"] = {"brief": {"protected_ids": ["label"], "purpose": "Keep text"}}
    attached, _ = apply_context(scene, path)
    assert attached["provenance"]["brief"]["protected_ids"] == ["body", "label"]
    context["uncertainties"][0]["description"] = "Revised interpretation"
    write_json(path, context)
    changed, _ = apply_context(attached, path)
    assert changed["provenance"]["context"]["sha256"] != attached["provenance"]["context"]["sha256"]
    assert changed["objects"] == attached["objects"]


@pytest.mark.parametrize("defect", ["hidden", "transparent", "outside"])
def test_preserved_objects_cannot_be_hidden(scene, context_files, defect):
    path, _ = context_files
    body = scene["objects"][0]
    if defect == "hidden":
        body["visible"] = False
    elif defect == "transparent":
        body["style"]["opacity"] = 0
    else:
        body["geometry"]["x"] = 200
    with pytest.raises(ValueError, match="preserved object invalid"):
        apply_context(scene, path)


def test_text_and_layout_mismatches_are_reviewable(scene, context_files):
    path, _ = context_files
    attached, extras = apply_context(scene, path, resolve_tokens=True)
    attached["objects"][1]["geometry"].update(text="B", x=5)
    report = style_check(attached, extras)
    assert report["status"] == "needs_review"
    assert {entry["kind"] for entry in report["mismatches"]} == {"text", "layout"}


def test_linear_gradient_token_resolves_to_supported_resource(scene, context_files):
    path, context = context_files
    profile = {"schema_version": "0.1", "id": "paper", "tokens": [{"id": "main", "type": "linear_gradient",
        "value": {"kind": "linear_gradient", "start": [0, 0], "end": [100, 0],
                  "stops": [[0, "#fff"], [1, "#112233"]]}}]}
    context["style_profile"] = write_json(path.parent / "profile.json", profile)
    write_json(path, context)
    attached, extras = apply_context(scene, path, resolve_tokens=True)
    assert attached["resources"][0]["kind"] == "linear_gradient"
    assert style_check(attached, extras)["status"] == "passed"
    unsupported = copy.deepcopy(profile)
    unsupported["tokens"][0]["value"]["kind"] = "radial_gradient"
    with pytest.raises(ValueError):
        validate_profile(unsupported)
    profile["tokens"][0]["value"]["stops"].reverse()
    with pytest.raises(ValueError, match="gradient stop"):
        validate_profile(profile)


def test_current_font_and_cjk_glyph_support_are_explicit(monkeypatch):
    from scientific_figure import fonts
    family = font_info()["family"]
    validate_profile({"schema_version": "0.1", "id": "font", "tokens": [],
                      "font": {"family": family, "scripts": ["latin"]}})
    monkeypatch.setattr(fonts, "font_info", lambda: {"family": family, "glyphs": set(range(128))})
    with pytest.raises(ValueError, match="no glyphs"):
        validate_profile({"schema_version": "0.1", "id": "font", "tokens": [],
                          "font": {"family": family, "scripts": ["latin", "cjk"]}})
    with pytest.raises(ValueError, match="unsupported font"):
        validate_profile({"schema_version": "0.1", "id": "font", "tokens": [],
                          "font": {"family": "Unavailable Font", "scripts": ["latin"]}})


def evidence_context(tmp_path, scene):
    sources = tmp_path / "external"
    sources.mkdir()
    source = sources / "source.png"
    Image.new("RGB", (32, 24), "white").save(source)
    scene["provenance"] = {"source": {"sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                                      "size": [32, 24], "region": [0, 0, 32, 24]}}
    directory = sources / "evidence"
    generate_evidence(source, directory)
    context = {"schema_version": "0.1", "id": "with_evidence", "evidence": [
        {"id": "pixels", "path": "external/evidence/evidence.json",
         "sha256": hashlib.sha256((directory / "evidence.json").read_bytes()).hexdigest()}],
        "objects": [{"object_id": "body", "evidence_ids": ["pixels"]}]}
    path = tmp_path / "context.json"
    write_json(path, context)
    return path, sources, source


def test_bound_snapshots_survive_removal_of_all_original_sources(tmp_path, scene):
    path, sources, source = evidence_context(tmp_path, scene)
    source_path = str(source)
    attached, extras = apply_context(scene, path)
    evidence_ref = extras["context.json"]["evidence"][0]
    assert evidence_ref["document"]["kind"] == "raster_evidence"
    assert "path" not in evidence_ref["document"]["source"]
    assert source_path not in json.dumps(extras)
    shutil.rmtree(sources)
    path.unlink()
    validate_context(attached, extras)
    assert style_check(attached, extras)["status"] == "passed"
    snapshot = tmp_path / "snapshot.json"
    write_json(snapshot, extras["context.json"])
    reapplied, more = apply_context(attached, snapshot)
    assert reapplied == attached
    assert more == extras


def test_profile_and_manifests_survive_missing_source_directory(tmp_path, scene, context_files):
    path, _ = context_files
    attached, extras = apply_context(scene, path)
    for dependency in path.parent.glob("*.json"):
        dependency.unlink()
    validate_context(attached, extras)
    snapshot = tmp_path / "snapshot.json"
    write_json(snapshot, extras["context.json"])
    reapplied, more = apply_context(attached, snapshot)
    assert more == extras
    assert reapplied == attached


@pytest.mark.parametrize("defect", ["source_bytes", "scene_source", "snapshot_source", "manifest"])
def test_evidence_mismatch_is_rejected(tmp_path, scene, defect):
    path, _, source = evidence_context(tmp_path, scene)
    if defect == "source_bytes":
        source.write_bytes(source.read_bytes() + b"changed")
        with pytest.raises(ValueError, match="source hash"):
            apply_context(scene, path)
        return
    if defect == "scene_source":
        scene["provenance"]["source"]["sha256"] = "0" * 64
        with pytest.raises(ValueError, match="source SHA"):
            apply_context(scene, path)
        return
    attached, extras = apply_context(scene, path)
    ref = extras["context.json"]["evidence"][0]
    if defect == "snapshot_source":
        ref["document"]["source"]["sha256"] = "0" * 64
    else:
        ref["manifest"]["source_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash"):
        validate_context(attached, extras)


def test_no_context_does_not_require_extras(scene):
    validate_context(scene, {"review.json": {}})
    assert style_check(scene, {})["status"] == "passed"
    with pytest.raises(ValueError, match="missing context"):
        validate_context(scene, {"style-profile.json": {}})

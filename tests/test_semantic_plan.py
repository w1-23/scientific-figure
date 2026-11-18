"""Semantic intent is host-authored; pixel evidence is explicitly requested."""
import copy
import hashlib
import importlib

import pytest
from PIL import Image, ImageDraw

from scientific_figure.analysis import analyze, bind_analysis
from scientific_figure.reconstruct import reconstruct, semantic_audit


def reference(tmp_path):
    image = Image.new("RGB", (64, 48), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 6, 36, 26), fill="#99ccaa")
    draw.line((15, 12, 30, 20), fill="#224433", width=2)
    path = tmp_path / "source.png"
    image.save(path)
    return path


def semantic_plan(path=None):
    plan = {
        "schema_version": "0.3", "mode": "semantic",
        "canvas": {"width": 96, "height": 72, "background": "#ffffff"},
        "brief": {"purpose": "Explain an editable compartment", "target_size": [96, 72],
                  "simplification": "Use one simple boundary", "protected_ids": ["body"],
                  "allowed_omissions": ["Texture"], "uncertainties": ["Hidden layers are unknown"]},
        "steps": [{"step_id": "body", "operation": "construct",
                   "object": {"id": "body", "kind": "rect",
                              "geometry": {"x": 8, "y": 8, "width": 72, "height": 48},
                              "style": {"fill": "#99ccaa"}}}],
    }
    if path is not None:
        plan["source"] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                          "size": [64, 48], "region": [8, 4, 32, 24]}
    return plan


def forbidden(*args, **kwargs):
    raise AssertionError("unrequested pixel analysis/search/evaluation was called")


def test_semantic_reference_never_calls_pixel_functions(tmp_path, monkeypatch):
    path = reference(tmp_path)
    for module, names in {
        "analysis": ["analyze", "source_crop"],
        "candidate_search": ["search"], "evaluation": ["evaluate", "render_pixels"],
        "quality": ["reviewed_trace"], "strokes": ["fit_stroke"],
        "internal_structure": ["ridge_mask", "skeletonize", "skeleton_chains"],
    }.items():
        loaded = importlib.import_module("scientific_figure." + module)
        for name in names:
            monkeypatch.setattr(loaded, name, forbidden)
    plan = semantic_plan(path)
    plan["resources"] = [{"id": "shade", "kind": "linear_gradient", "start": [0, 0],
                          "end": [96, 0], "stops": [[0, "#99ccaa"], [1, "#224433"]]}]
    plan["steps"][0]["object"]["style"]["fill"] = "url(#shade)"
    original = copy.deepcopy(plan)
    scene = reconstruct(path, plan)
    assert plan == original
    assert scene["resources"] == plan["resources"]
    assert scene["provenance"]["source_to_scene"] == [3, 0, 0, 3, -24, -12]
    assert scene["provenance"]["mode"] == "semantic"
    audit = scene["provenance"]["semantic_audit"]
    assert audit["status"] == "passed" and audit["scope"] == "mechanical_only"
    assert audit["scientific_review"] == "host_required"
    assert audit["C"] == {"draw_nodes": 1, "segments": 0}
    assert audit["limits"] == {"draw_nodes": 128, "segments": 2400}
    assert audit["protected_ids"] == [{"id": "body", "exists": True, "visible": True}]
    assert "adaptive_context" not in scene["provenance"]


def test_reference_mapping_respects_target_viewbox_and_binding(tmp_path):
    path = reference(tmp_path)
    plan = semantic_plan(path)
    plan["canvas"]["viewBox"] = [4, 2, 48, 36]
    scene = reconstruct(path, plan)
    assert scene["provenance"]["source_to_scene"] == [1.5, 0, 0, 1.5, -8, -4]
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="hash"):
        reconstruct(path, plan)


def test_semantic_without_reference_and_recomputed_audit():
    plan = semantic_plan()
    plan["complexity_limits"] = {"draw_nodes": 1}
    scene = reconstruct(None, plan)
    assert "source" not in scene["provenance"]
    original_binding = scene["provenance"]["semantic_audit"]["binding"]
    extra = copy.deepcopy(scene["objects"][0])
    extra["id"] = "extra"
    scene["objects"].append(extra)
    audit = semantic_audit(scene)
    assert audit["status"] == "rejected"
    assert "complexity_budget:draw_nodes" in audit["reasons"]
    assert audit["binding"]["scene_sha256"] != original_binding["scene_sha256"]
    scene["objects"] = [extra]
    assert "protected_id_missing:body" in semantic_audit(scene)["reasons"]


@pytest.mark.parametrize("version", ["0.1", "0.2", "0.3"])
def test_legacy_construct_and_pixel_mode_keep_behavior(tmp_path, version):
    path = reference(tmp_path)
    plan = semantic_plan(path)
    plan.pop("brief")
    plan.pop("mode")
    plan["schema_version"] = version
    if version == "0.3":
        plan["mode"] = "pixel"
    plan["source"]["region"] = [0, 0, 64, 48]
    plan["canvas"].update(width=64, height=48)
    scene = reconstruct(path, plan)
    assert scene["objects"][0]["semantic"]["evidence"] == "host_assisted"
    assert scene["provenance"]["source_to_scene"] == [1, 0, 0, 1, 0, 0]
    assert scene["provenance"]["mode"] == "reference-reconstruction"
    assert "semantic_audit" not in scene["provenance"]
    if version in {"0.2", "0.3"}:
        context = scene["provenance"]["adaptive_context"]
        assert context["analysis"]["schema_version"] == "0.2"
        assert "ridge_graph" in context["analysis"]["internal_structure"]
        assert "audit" in context and "search_budget" in scene["provenance"]
    else:
        assert "adaptive_context" not in scene["provenance"]


@pytest.mark.parametrize("defect", ["missing", "hidden", "transparent", "unpainted", "outside", "hidden_parent"])
def test_required_protected_ids_must_exist_and_be_visible(defect):
    plan = semantic_plan()
    obj = plan["steps"][0]["object"]
    if defect == "missing":
        plan["brief"]["protected_ids"] = ["absent"]
    elif defect == "hidden":
        obj["visible"] = False
    elif defect == "transparent":
        obj["style"]["opacity"] = 0
    elif defect == "unpainted":
        obj["style"]["fill"] = "none"
    elif defect == "outside":
        obj["geometry"]["x"] = 200
    else:
        obj["parent"] = "container"
        plan["steps"].insert(0, {"step_id": "container", "operation": "construct",
                                "object": {"id": "container", "kind": "group", "visible": False}})
    with pytest.raises(ValueError, match="protected_id"):
        reconstruct(None, plan)


def test_segment_budget_counts_implicit_svg_commands_and_task_override():
    plan = semantic_plan()
    plan["steps"][0]["object"] = {
        "id": "body", "kind": "path", "geometry": {"d": "M8 8 24 8 24 24 8 24 Z"}}
    plan["complexity_limits"] = {"segments": 3}
    with pytest.raises(ValueError, match="complexity_budget:segments"):
        reconstruct(None, plan)
    plan["complexity_limits"]["segments"] = 5
    scene = reconstruct(None, plan)
    assert scene["provenance"]["semantic_audit"]["C"]["segments"] == 5


@pytest.mark.parametrize("defect", ["pixel_step", "pixel_parameters", "scene_in_brief", "limit_bounds", "target_size"])
def test_semantic_contract_rejects_pixel_work_and_heavy_brief(defect):
    plan = semantic_plan()
    if defect == "pixel_step":
        plan["steps"][0]["operation"] = "auto_reconstruct"
    elif defect == "pixel_parameters":
        plan["steps"][0]["parameters"] = {}
    elif defect == "scene_in_brief":
        plan["brief"]["objects"] = []
    elif defect == "limit_bounds":
        plan["complexity_limits"] = {"segments": 20001}
    else:
        plan["brief"]["target_size"] = [48, 36]
    with pytest.raises(ValueError):
        reconstruct(None, plan)


def test_explicit_contrast_skips_ridge_skeleton_and_full_analysis(tmp_path, monkeypatch):
    path = reference(tmp_path)
    structure = importlib.import_module("scientific_figure.internal_structure")
    analysis_module = importlib.import_module("scientific_figure.analysis")
    for name in ["ridge_mask", "skeletonize", "skeleton_chains", "structure_summary"]:
        monkeypatch.setattr(structure, name, forbidden)
    monkeypatch.setattr(analysis_module, "topology", forbidden)
    monkeypatch.setattr(analysis_module, "palette_masks", forbidden)
    monkeypatch.setattr(Image.Image, "quantize", forbidden)
    result = analyze(path, background="#ffffff", evidence=["contrast"])
    assert result["kind"] == "evidence_result" and result["persistent"] is False
    assert result["evidence"] == ["contrast"]
    assert result["contrast"]["internal_edge_pixels"] > 0
    assert "ridge_graph" not in result["contrast"] and "palette" not in result
    with pytest.raises(ValueError):
        bind_analysis(path, result)

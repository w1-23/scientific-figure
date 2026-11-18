"""Evidence must constrain the semantic path, not decorate it.

These tests cover the three wiring points where a declared requirement could
previously pass silently: EXIF-consistent source pixels, candidate-side internal
edges, and must-preserve / evidence-ID declarations.
"""
import json

import pytest
from PIL import Image, ImageDraw

from scientific_figure.analysis import analyze, source_crop
from scientific_figure.constraints import build_constraints
from scientific_figure.coords import load_oriented
from scientific_figure.evaluation import evaluate
from scientific_figure.planning import compile_plan, plan_review, validate_evidence_bindings


def test_analysis_roi_uses_exif_oriented_pixels(tmp_path):
    """An analysis ROI must address the same pixels as evidence and measurements."""
    image = Image.new("RGB", (30, 60), "white")
    ImageDraw.Draw(image).rectangle((0, 0, 29, 29), fill="#2244cc")
    exif = Image.Exif()
    exif[274] = 6  # rotate 90 degrees clockwise
    path = tmp_path / "rotated.png"
    image.save(path, exif=exif)

    pixels, meta = source_crop(path)
    assert meta["size"] == [60, 30]
    assert pixels.shape[:2] == (30, 60)
    oriented, oriented_meta = load_oriented(path)
    assert oriented_meta["size"] == meta["size"]

    # Valid only after orientation; the stored raster would reject it.
    crop, region_meta = source_crop(path, [35, 5, 20, 20])
    assert region_meta["region"] == [35, 5, 20, 20]
    assert crop.shape[:2] == (20, 20)
    with pytest.raises(ValueError, match="region outside source"):
        source_crop(path, [45, 5, 20, 20])


def test_candidate_detail_outside_source_silhouette_is_measured(tmp_path):
    """Internal edges of the candidate use the candidate's own foreground."""
    image = Image.new("RGB", (128, 112), "white")
    ImageDraw.Draw(image).ellipse((10, 10, 116, 100), fill="#229966")
    path = tmp_path / "source.png"
    image.save(path)
    analysis = analyze(path, colors=2, background="#ffffff")
    scene = {"schema_version": "0.1", "revision": 0,
             "canvas": {"width": 128, "height": 112}, "objects": [
                 {"id": "body", "kind": "rect", "geometry": {"x": 0, "y": 0, "width": 128, "height": 112},
                  "style": {"fill": "#229966"}},
                 {"id": "extra", "kind": "rect", "geometry": {"x": 12, "y": 12, "width": 14, "height": 14},
                  "style": {"fill": "#2244cc"}}]}
    audit = evaluate(path, analysis, scene)
    internal = audit["F"]["internal_boundary"]
    assert internal["actual_pixels"] > 0
    assert internal["precision"] < 1
    assert internal["source_pixels"] == 0  # a solid reference has no internal edge
    assert "outline_deviation" in audit["reasons"]


def _plan(**overrides):
    plan = {"schema_version": "0.1", "id": "wiring", "task_type": "text_design",
            "purpose": "Host-declared sample workflow", "canvas": {"width": 460, "height": 180},
            "modules": [{"id": "main", "objects": [
                {"id": "sample", "role": "Input sample", "transform": [1, 0, 0, 1, 25, 45],
                 "must_preserve": ["body", "sample material identity"],
                 "construction": {"kind": "recipe", "id": "flow_node", "version": "1.0.0",
                                  "params": {"label": "Sample"}}},
                {"id": "result", "role": "Measured output", "transform": [1, 0, 0, 1, 275, 45],
                 "construction": {"kind": "recipe", "id": "flow_node", "version": "1.0.0",
                                  "params": {"label": "Measure"}}}]}],
            "relations": [], "review_spec": {"required_objects": ["sample", "result"],
                                             "must_preserve": ["sample", "scientific identity"],
                                             "text_policy": "host_authored"}}
    plan.update(overrides)
    return plan


def test_must_preserve_is_checked_or_left_open():
    scene, extras, report = compile_plan(_plan())
    checks = [c for c in report["checks"] if c["kind"] == "must_preserve"]
    object_checks = {c["requirement"]: c for c in checks if c.get("plan_object_id") == "sample"}
    assert object_checks["body"]["status"] == "passed"
    assert object_checks["body"]["resolution"] == "part"
    assert object_checks["sample material identity"]["status"] == "needs_review"
    figure_checks = {c["requirement"]: c for c in checks if c.get("scope") == "figure"}
    assert figure_checks["sample"]["status"] == "passed"
    assert figure_checks["scientific identity"]["status"] == "needs_review"
    # A deleted part can no longer satisfy the requirement.
    edited = json.loads(json.dumps(scene))
    body = extras["generation.json"]["input"]["inputs"]["object_map"]["sample"]["part_ids"]["body"]
    next(o for o in edited["objects"] if o["id"] == body)["visible"] = False
    edited["revision"] = 1
    again = plan_review(edited, _plan(), extras["generation.json"]["input"]["inputs"]["object_map"])
    failed = [c for c in again["checks"] if c.get("kind") == "must_preserve" and c.get("plan_object_id") == "sample"]
    assert [c["status"] for c in failed if c["requirement"] == "body"] == ["failed"]


def _pack(tmp_path):
    import numpy as np
    canvas = np.full((90, 120, 3), 255, np.uint8)
    canvas[20:70, 25:95] = (40, 60, 90)
    source = tmp_path / "reference.png"
    Image.fromarray(canvas).save(source)
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"schema_version": "0.1", "background": "#ffffff"}), encoding="utf-8")
    return build_constraints(source, request, tmp_path / "pack")


def test_declared_evidence_ids_must_resolve_in_the_pack(tmp_path):
    pack = _pack(tmp_path)
    contour = pack["edge_packs"]["medium"]["contours"][0]["id"]
    known = _plan(modules=[{"id": "main", "objects": [
        {"id": "sample", "role": "Input sample", "evidence_ids": [contour],
         "source_region": [0, 0, 120, 90],
         "construction": {"kind": "recipe", "id": "flow_node", "version": "1.0.0"}}]}],
        review_spec={"text_policy": "host_authored"})
    report = validate_evidence_bindings(known, pack)
    assert report == [{"object_id": "sample", "evidence_ids": [contour],
                       "source_region": [0, 0, 120, 90], "intersecting": [contour]}]

    unknown = json.loads(json.dumps(known))
    unknown["modules"][0]["objects"][0]["evidence_ids"] = ["contour-medium-9999"]
    with pytest.raises(ValueError, match="unknown evidence IDs"):
        validate_evidence_bindings(unknown, pack)

    outside = json.loads(json.dumps(known))
    outside["modules"][0]["objects"][0]["source_region"] = [0, 0, 10, 10]
    with pytest.raises(ValueError, match="lies outside the declared source region"):
        validate_evidence_bindings(outside, pack)

    scene, extras, report = compile_plan(
        _plan(modules=[{"id": "main", "objects": [
            {"id": "sample", "role": "Input sample", "evidence_ids": [contour],
             "source_region": [0, 0, 120, 90],
             "construction": {"kind": "recipe", "id": "flow_node", "version": "1.0.0",
                              "params": {"label": "Sample"}}}]}],
            review_spec={"text_policy": "host_authored"}), evidence_pack=pack)
    assert report["evidence_bindings"][0]["object_id"] == "sample"


def test_scope_decides_which_reference_edges_an_object_owns(tmp_path):
    """``object`` holds an object accountable for its interior; ``outline`` does not.

    Both scopes run against the same drawing, whose outer silhouette matches the
    reference exactly but which omits the reference's internal bars.
    """
    from scientific_figure.builder import SceneBuilder
    from scientific_figure.constraints import check_constraints
    from scientific_figure.project import write_project

    image = Image.new("RGB", (240, 160), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 30, 209, 129), fill="#cfe8c0", outline="#3f5e33", width=3)
    for x in range(55, 205, 20):
        draw.rectangle((x, 45, x + 3, 115), fill="#4b7a3a")
    source = tmp_path / "reference.png"
    image.save(source)
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"schema_version": "0.1", "background": "#ffffff"}), encoding="utf-8")
    build_constraints(source, request, tmp_path / "pack")

    builder = SceneBuilder(240, 160, background="#ffffff", schema_version="0.3")
    builder.rect("cell", 30, 30, 180, 100,
                 style={"fill": "#cfe8c0", "stroke": "#3f5e33", "stroke_width": 2})
    write_project(builder.build(), tmp_path / "project", scale=1)

    def run(scope):
        bindings = [{"object_id": "cell", "kind": "boundary", "scope": scope,
                     "source_region": [0, 0, 240, 160],
                     "allowed_deviation": 2.0, "recall_min": 0.6}]
        report = check_constraints(tmp_path / "project", tmp_path / "pack", bindings=bindings)
        return report["bindings"][0]

    outline, interior = run("outline"), run("object")
    assert outline["status"] == "passed", (outline["status"], outline["metrics"], outline["notes"])
    # Precision must stay measurable: masking the edge map must keep 0/255.
    assert outline["metrics"]["precision"] > 0.5, outline["metrics"]
    assert outline["metrics"]["source_edge_pixels"] > 300, outline["metrics"]
    assert interior["status"] != "passed", (interior["status"], interior["metrics"], interior["notes"])
    assert interior["metrics"]["recall"] < outline["metrics"]["recall"] - 0.2
    assert any("recall" in note for note in interior["notes"])

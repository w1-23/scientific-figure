"""Contract tests for bounded, re-audited layout repairs."""
from __future__ import annotations

import hashlib
import json

import pytest

from scientific_figure.auto_layout_fix import fix_scene
from scientific_figure.builder import SceneBuilder
from scientific_figure.layout_audit import audit_scene
from scientific_figure.project import load_project, write_project

TARGET = {"target": {"width_mm": 183, "dpi": 300}}


def _failures(report):
    return {(check["id"], check["status"]) for check in report["checks"]
            if check["status"] == "fail"}


def test_wrapped_text_fits_its_container_without_changing_words():
    builder = SceneBuilder(600, 300, schema_version="0.3")
    builder.rect("card", 40, 100, 140, 90,
                 style={"fill": "#ffffff", "stroke": "#88aacc", "stroke_width": 2},
                 semantic={"role": "panel"})
    builder.text("label", 110, 145, "A very long label that will not fit inside this card",
                 style={"font_size": 14, "anchor": "middle"})
    scene = builder.build()
    before = audit_scene(scene, request=TARGET)
    assert _failures(before)

    result = fix_scene(scene, before, request=TARGET)
    assert result["status"] == "partially_fixed"
    assert not _failures(result["report_after"])
    fixed = [obj for obj in result["scene"]["objects"] if obj["id"] == "label"][0]
    assert fixed["geometry"]["text"].split() == scene["objects"][1]["geometry"]["text"].split()
    assert "\n" in fixed["geometry"]["text"]
    assert scene["objects"][1]["geometry"]["text"].count("\n") == 0
    assert scene["revision"] == 0 and result["scene"]["revision"] == 1


def test_label_is_moved_off_its_connector_and_endpoints_stay():
    builder = SceneBuilder(600, 300, schema_version="0.3")
    builder.rect("left", 60, 140, 80, 40, style={"fill": "#ffffff", "stroke": "#334455"})
    builder.rect("right", 460, 140, 80, 40, style={"fill": "#ffffff", "stroke": "#334455"})
    builder.text("label", 300, 162, "crossing label",
                 style={"font_size": 12, "anchor": "middle"})
    builder.add("wire", "connector", {"from": "left", "to": "right"},
                style={"stroke": "#334455", "stroke_width": 1})
    scene = builder.build()
    before = audit_scene(scene, request=TARGET)
    assert any(check["kind"] == "connector_clearance" and check["status"] == "fail"
               for check in before["checks"])

    result = fix_scene(scene, before, request=TARGET)
    assert any(item["issue_id"] == "connector_clearance:label+wire"
               for item in result["applied"])
    remaining = [check for check in result["report_after"]["checks"]
                 if check["kind"] == "connector_clearance" and check["status"] != "clear"]
    assert remaining == []
    for oid in ("left", "right", "wire"):
        assert [obj for obj in result["scene"]["objects"] if obj["id"] == oid] == \
               [obj for obj in scene["objects"] if obj["id"] == oid]


def test_unfixable_finding_stays_needs_review_without_mutating_the_scene():
    builder = SceneBuilder(120, 100, schema_version="0.3")
    builder.text("wide", 60, 50, "A label far wider than this tiny canvas",
                 style={"font_size": 14, "anchor": "middle"})
    scene = builder.build()
    before = audit_scene(scene, request=TARGET)

    result = fix_scene(scene, before, request=TARGET)
    assert result["status"] == "needs_review"
    assert result["applied"] == []
    assert result["scene"] == scene


def test_protected_objects_are_never_moved():
    builder = SceneBuilder(200, 100, schema_version="0.3")
    builder.text("membrane", 300, 50, "membrane potential",
                 style={"font_size": 14}, semantic={"role": "membrane", "protected": True})
    scene = builder.build()
    before = audit_scene(scene, request=TARGET)

    result = fix_scene(scene, before, request=TARGET)
    assert result["applied"] == []
    assert result["scene"] == scene
    assert any(check["kind"] == "object_canvas_overflow" and check["status"] == "fail"
               for check in result["report_after"]["checks"])


def test_fixed_scene_is_a_new_directory_and_the_source_stays_byte_identical(tmp_path):
    source = tmp_path / "source"
    write_project(_source_scene(), source)
    digest = hashlib.sha256((source / "scene.json").read_bytes()).hexdigest()

    scene = load_project(source)
    report = audit_scene(scene, request=TARGET)
    result = fix_scene(scene, report, request=TARGET)
    assert result["status"] != "no_changes"
    write_project(result["scene"], tmp_path / "fixed")

    assert hashlib.sha256((source / "scene.json").read_bytes()).hexdigest() == digest
    assert json.dumps(load_project(source)) == json.dumps(scene)


def test_bad_round_budget_is_rejected():
    scene = SceneBuilder(100, 100, schema_version="0.3").build()
    with pytest.raises(ValueError):
        fix_scene(scene, audit_scene(scene), max_rounds=0)


def _source_scene():
    builder = SceneBuilder(600, 300, schema_version="0.3")
    builder.rect("card", 40, 100, 140, 90,
                 style={"fill": "#ffffff", "stroke": "#88aacc", "stroke_width": 2},
                 semantic={"role": "panel"})
    builder.text("label", 110, 145, "A very long label that will not fit inside this card",
                 style={"font_size": 14, "anchor": "middle"})
    return builder.build()

"""Contract tests for the deterministic Scene layout audit."""
from __future__ import annotations

import copy
import json
import xml.etree.ElementTree as ET

import pytest

from scientific_figure.builder import SceneBuilder
from scientific_figure.layout_audit import audit_scene, validate_request
from scientific_figure.qa_overlay import overlay_svg, write_overlay
from scientific_figure.scene import schema_validate

TARGET = {"target": {"width_mm": 183, "dpi": 300}}


def _clean_scene():
    builder = SceneBuilder(600, 300, schema_version="0.3")
    builder.text("title", 300, 45, "Citrus anatomy",
                 style={"font_size": 18, "anchor": "middle"})
    for index, x in enumerate((40, 230, 420)):
        builder.rect(f"card{index}", x, 95, 140, 150,
                     style={"fill": "#ffffff", "stroke": "#334455", "stroke_width": 1},
                     semantic={"role": "panel"})
        builder.text(f"label{index}", x + 70, 140, f"Panel {index + 1}",
                     style={"font_size": 14, "anchor": "middle"})
        builder.text(f"body{index}", x + 70, 170, "Tissue section",
                     style={"font_size": 12, "anchor": "middle"})
    return builder.build()


def _by_kind(report, kind):
    return [check for check in report["checks"] if check["kind"] == kind]


def test_clean_scene_reports_clear_without_fake_pass():
    report = audit_scene(_clean_scene(), request=TARGET)
    assert report["status"] == "clear"
    assert report["summary"]["fail"] == 0
    assert report["summary"]["warn"] == 0
    assert report["summary"]["not_auditable"] == 0
    schema_validate(report, "scene-qa-report")
    assert report["scope"].startswith("mechanical layout geometry only")
    assert "pass" not in report["status"]


def test_text_text_overlap_is_a_fail_with_measured_ratio():
    builder = SceneBuilder(600, 300, schema_version="0.3")
    builder.text("left", 200, 120, "Overlapping words here", style={"font_size": 14})
    builder.text("right", 220, 120, "Overlapping words here", style={"font_size": 14})
    report = audit_scene(builder.build(), request=TARGET)
    checks = _by_kind(report, "text_text_overlap")
    assert len(checks) == 1
    check = checks[0]
    assert check["status"] == "fail"
    assert set(check["objects"]) == {"left", "right"}
    assert 0.6 <= check["measurement"]["overlap_ratio"] <= 1.0
    assert report["status"] == "failed"


def test_text_inside_its_container_is_not_a_collision():
    builder = SceneBuilder(600, 300, schema_version="0.3")
    builder.rect("card", 100, 100, 200, 120,
                 style={"fill": "#ffffff", "stroke": "#334455", "stroke_width": 1})
    builder.text("label", 200, 160, "Inside", style={"font_size": 14, "anchor": "middle"})
    report = audit_scene(builder.build(), request=TARGET)
    assert _by_kind(report, "text_text_overlap") == []
    assert [c for c in _by_kind(report, "text_container_padding")
            if c["status"] in {"fail", "warn"} and set(c["objects"]) == {"label", "card"}] == []


def test_multiline_text_does_not_collide_with_its_own_lines():
    builder = SceneBuilder(600, 300, schema_version="0.3")
    builder.text("label", 200, 120, "First line\nSecond line\nThird line",
                 style={"font_size": 14})
    report = audit_scene(builder.build(), request=TARGET)
    assert _by_kind(report, "text_text_overlap") == []


def test_connector_through_text_fails_and_label_for_is_skipped():
    def scene(label_for):
        builder = SceneBuilder(600, 300, schema_version="0.3")
        builder.rect("left", 60, 140, 80, 40, style={"fill": "#ffffff", "stroke": "#334455"})
        builder.rect("right", 460, 140, 80, 40, style={"fill": "#ffffff", "stroke": "#334455"})
        semantic = {"label_for": "wire"} if label_for else None
        builder.text("label", 300, 162, "crossing label",
                     style={"font_size": 12, "anchor": "middle"}, semantic=semantic)
        builder.add("wire", "connector", {"from": "left", "to": "right"},
                    style={"stroke": "#334455", "stroke_width": 1})
        return builder.build()

    checks = _by_kind(audit_scene(scene(False), request=TARGET), "connector_clearance")
    assert [c for c in checks if c["status"] == "fail"], checks
    assert all(c["objects"][1] == "wire" for c in checks)
    skipped = _by_kind(audit_scene(scene(True), request=TARGET), "connector_clearance")
    assert skipped == []


def test_canvas_overflow_tolerance_and_exemption():
    builder = SceneBuilder(600, 300, schema_version="0.3")
    builder.rect("deep", 590, 60, 40, 40, style={"fill": "#ffffff", "stroke": "#334455"})
    builder.rect("bleed", 0.2, 200, 60, 40, style={"fill": "#ffffff", "stroke": "#334455"})
    report = audit_scene(builder.build(), request=TARGET)
    statuses = {c["objects"][0]: c["status"] for c in _by_kind(report, "object_canvas_overflow")}
    assert statuses["deep"] == "fail"
    assert statuses["bleed"] == "warn"

    request = copy.deepcopy(TARGET)
    request["exemptions"] = [{"check": "object_canvas_overflow", "objects": ["bleed"],
                              "reason": "deliberate bleed background"}]
    exempted = audit_scene(builder.build(), request=request)
    statuses = {c["objects"][0]: c["status"] for c in _by_kind(exempted, "object_canvas_overflow")}
    assert statuses["bleed"] == "exempted"
    entry = [e for e in exempted["exemptions"] if e["check"] == "object_canvas_overflow"][0]
    assert entry["applied"] is True
    assert entry["reason"] == "deliberate bleed background"


def test_font_size_needs_a_target_and_flags_tiny_glyphs():
    builder = SceneBuilder(600, 300, schema_version="0.3")
    builder.text("tiny", 200, 120, "tiny", style={"font_size": 3})
    assert _by_kind(audit_scene(builder.build()), "font_glyph_size")[0]["status"] == "not_auditable"
    checks = _by_kind(audit_scene(builder.build(), request=TARGET), "font_glyph_size")
    assert [c for c in checks if c["status"] == "fail" and c["objects"] == ["tiny"]]


def test_panel_gutters_report_the_uneven_pair():
    builder = SceneBuilder(600, 300, schema_version="0.3")
    for index, x in enumerate((40, 142, 251)):
        builder.rect(f"card{index}", x, 100, 100, 100,
                     style={"fill": "#ffffff"}, semantic={"role": "panel"})
    report = audit_scene(builder.build(), request=TARGET)
    checks = [c for c in _by_kind(report, "aligned_panels_and_gutters") if c["status"] == "warn"]
    assert len(checks) == 1
    assert checks[0]["objects"] == ["card0", "card1", "card2"]
    assert checks[0]["measurement"]["gaps"] == [2.0, 9.0]


def test_exemption_requires_a_reason_and_unknown_checks_are_rejected():
    with pytest.raises(ValueError):
        validate_request({"exemptions": [{"check": "text_text_overlap", "reason": " "}]})
    with pytest.raises(ValueError):
        validate_request({"exemptions": [{"check": "nope", "reason": "x"}]})
    with pytest.raises(ValueError):
        validate_request({"thresholds": {"unknown": 1.0}})


def test_stale_exemption_is_reported_instead_of_disappearing():
    request = copy.deepcopy(TARGET)
    request["exemptions"] = [{"check": "object_canvas_overflow",
                              "objects": ["missing"], "reason": "old design"}]
    report = audit_scene(_clean_scene(), request=request)
    stale = _by_kind(report, "stale_exemption")
    assert len(stale) == 1 and stale[0]["status"] == "info"
    assert report["exemptions"][0]["applied"] is False


def test_overlay_is_valid_svg_and_never_touches_the_scene(tmp_path):
    scene = _clean_scene()
    original = json.dumps(scene, sort_keys=True)
    report = audit_scene(scene, request=TARGET)
    xml = overlay_svg(scene, report)
    root = ET.fromstring(xml)
    overlay = [node for node in root.iter() if node.get("id") == "qa-overlay"]
    assert len(overlay) == 1
    assert overlay[0].get("data-status") == report["status"]
    assert json.dumps(scene, sort_keys=True) == original

    path = write_overlay(scene, report, tmp_path / "qa.overlay.svg")
    assert path["bytes"] > 0 and len(path["sha256"]) == 64


def test_report_is_deterministic_except_for_elapsed_time():
    scene = _clean_scene()
    first = audit_scene(scene, request=TARGET)
    second = audit_scene(scene, request=TARGET)
    first.pop("elapsed_ms")
    second.pop("elapsed_ms")
    assert first == second

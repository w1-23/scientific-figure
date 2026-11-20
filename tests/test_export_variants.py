"""Two SVGs from one Scene: shared geometry, no text left in the graphics variant."""
import hashlib
import json
import xml.etree.ElementTree as ET

import pytest

from scientific_figure.cli import execute, parser
from scientific_figure.export_variants import export_variants, graphics_scene
from scientific_figure.project import write_project


def sample_scene():
    return {"schema_version": "0.3", "revision": 0,
            "canvas": {"width": 400, "height": 240, "background": "#ffffff"},
            "objects": [
                {"id": "panel", "kind": "group"},
                {"id": "cell", "kind": "ellipse", "parent": "panel",
                 "geometry": {"cx": 120, "cy": 120, "rx": 60, "ry": 40},
                 "style": {"fill": "#cfe8c0", "stroke": "#335522", "stroke_width": 2}},
                {"id": "label", "kind": "text", "parent": "panel",
                 "geometry": {"x": 220, "y": 120, "text": "Cell"},
                 "style": {"font_size": 14}},
                {"id": "tumor", "kind": "ellipse",
                 "geometry": {"cx": 320, "cy": 120, "rx": 34, "ry": 30},
                 "style": {"fill": "#e8c0c0", "stroke": "#552233", "stroke_width": 2}},
                {"id": "leader", "kind": "connector",
                 "geometry": {"from": "cell", "to": "label"},
                 "style": {"stroke": "#333333", "stroke_width": 1}},
                {"id": "sci_arrow", "kind": "connector",
                 "geometry": {"from": "cell", "to": "tumor"},
                 "style": {"stroke": "#222222", "stroke_width": 1}},
                {"id": "title", "kind": "text",
                 "geometry": {"x": 10, "y": 20, "text": "Figure 1"},
                 "style": {"font_size": 16}},
                {"id": "label_box", "kind": "rect",
                 "geometry": {"x": 205, "y": 104, "width": 70, "height": 24},
                 "style": {"fill": "#ffffff"}, "semantic": {"text_decoration": True}},
                {"id": "glyph_path", "kind": "path",
                 "geometry": {"d": "M 300 60 L 310 70 L 300 80 Z"},
                 "style": {"fill": "#444444"}, "semantic": {"layer": "text"}}]}


def ids(path):
    tree = ET.parse(path)
    return {element.get("id") for element in tree.iter() if element.get("id")}


def element(path, oid):
    for item in ET.parse(path).iter():
        if item.get("id") == oid:
            return item
    raise AssertionError(f"{oid} not found in {path}")


def test_graphics_variant_keeps_science_and_drops_text(tmp_path):
    project = tmp_path / "project"
    write_project(sample_scene(), project, scale=1)
    output = tmp_path / "deliver"
    result = export_variants(project, output)
    assert result["status"] == "success"
    assert sorted(p.name for p in output.iterdir()) == ["figure.final.svg", "figure.graphics.svg", "variants.json"]
    final_ids, graphics_ids = ids(output / "figure.final.svg"), ids(output / "figure.graphics.svg")
    assert {"cell", "tumor", "sci_arrow", "panel"} <= graphics_ids
    assert {"label", "title", "label_box", "glyph_path", "leader"}.isdisjoint(graphics_ids)
    assert {"label", "title", "leader", "label_box", "glyph_path"} <= final_ids
    assert "Cell" in (output / "figure.final.svg").read_text(encoding="utf-8")
    assert "Cell" not in (output / "figure.graphics.svg").read_text(encoding="utf-8")


def test_shared_ids_have_identical_geometry_and_style(tmp_path):
    project = tmp_path / "project"
    write_project(sample_scene(), project, scale=1)
    output = tmp_path / "deliver"
    export_variants(project, output)
    for oid in ("cell", "tumor", "panel"):
        assert element(output / "figure.graphics.svg", oid).attrib == element(output / "figure.final.svg", oid).attrib


def test_report_binds_both_variants_and_lists_exclusions(tmp_path):
    project = tmp_path / "project"
    write_project(sample_scene(), project, scale=1)
    output = tmp_path / "deliver"
    result = export_variants(project, output)
    report = json.loads((output / "variants.json").read_text(encoding="utf-8"))
    assert report["revision"] == 0 and report["canvas"]["width"] == 400
    assert report["object_count"] == 9 and report["graphics_object_count"] == 4
    assert report["excluded_objects"] == ["glyph_path", "label", "label_box", "leader", "title"]
    assert set(report["variants"]) == {"figure.graphics.svg", "figure.final.svg"}
    assert result["variants"] == report["variants"]
    for name, info in report["variants"].items():
        data = (output / name).read_bytes()
        assert info == {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    assert (output / "figure.final.svg").read_bytes() == (project / "figure.svg").read_bytes()
    rendered = {oid for oid in ids(output / "figure.graphics.svg") if not oid.startswith("sf-")}
    assert report["graphics_object_count"] == len(rendered)


def test_explicit_exclude_removes_an_unmarked_path(tmp_path):
    scene = sample_scene()
    scene["objects"].append({"id": "outlined_text", "kind": "path",
                             "geometry": {"d": "M 20 200 L 40 200 L 40 210 Z"},
                             "style": {"fill": "#000000"}})
    project = tmp_path / "project"
    write_project(scene, project, scale=1)
    output = tmp_path / "deliver"
    result = export_variants(project, output, exclude=["outlined_text"])
    assert "outlined_text" in result["excluded_objects"]
    assert "outlined_text" not in ids(output / "figure.graphics.svg")


def test_excluding_an_ancestor_is_refused(tmp_path):
    project = tmp_path / "project"
    write_project(sample_scene(), project, scale=1)
    with pytest.raises(ValueError, match="ancestor"):
        export_variants(project, tmp_path / "deliver", exclude=["panel"])


def test_occlusion_on_text_is_refused_without_guessing(tmp_path):
    scene = sample_scene()
    scene["occlusions"] = [{"id": "cut", "over": "label", "under": "cell",
                            "frame": None, "region": [0, 0, 10, 10], "gap": 0}]
    with pytest.raises(ValueError, match="occlusion references excluded text"):
        graphics_scene(scene)


def test_variant_output_is_never_overwritten(tmp_path):
    project = tmp_path / "project"
    write_project(sample_scene(), project, scale=1)
    output = tmp_path / "deliver"
    output.mkdir()
    (output / "figure.final.svg").write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="already exists"):
        export_variants(project, output)
    assert (output / "figure.final.svg").read_text(encoding="utf-8") == "keep"
    assert not (output / "figure.graphics.svg").exists()


def test_cli_export_variants(tmp_path):
    project = tmp_path / "project"
    write_project(sample_scene(), project, scale=1)
    output = tmp_path / "deliver"
    result = execute(parser().parse_args(["export-variants", str(project), "--output", str(output)]))
    assert result["status"] == "success"
    assert (output / "figure.graphics.svg").is_file() and (output / "figure.final.svg").is_file()

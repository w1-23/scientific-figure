"""Draft previews and stage timings: a fast loop that is not a delivery."""
import json

import pytest

from scientific_figure.cli import execute, parser
from scientific_figure.project import load_project, write_project

PRE_PUBLISH = ["validate", "to_svg", "render", "inventory", "qa", "total"]
PREVIEW_STAGES = ["validate", "to_svg", "render", "publish", "total"]


def simple_scene():
    return {"schema_version": "0.1", "revision": 0,
            "canvas": {"width": 120, "height": 80, "background": "#ffffff"},
            "objects": [
                {"id": "cell", "kind": "rect",
                 "geometry": {"x": 20, "y": 20, "width": 80, "height": 40},
                 "style": {"fill": "#cfe8c0", "stroke": "#3f5e33", "stroke_width": 2}}]}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_preview_writes_only_draft_artifacts(tmp_path):
    out = tmp_path / "preview"
    result = write_project(simple_scene(), out, scale=1, preview=True)
    assert result["status"] == "success" and result["kind"] == "draft_preview"
    assert result["render_scale"] == 1
    assert sorted(p.name for p in out.iterdir()) == ["figure.svg", "preview.json", "preview.png"]
    marker = read_json(out / "preview.json")
    assert marker["kind"] == "draft_preview" and marker["render_scale"] == 1
    assert marker["artifacts"]["preview.png"]["bytes"] == (out / "preview.png").stat().st_size
    assert any("Not an editable" in line for line in result["limitations"])


@pytest.mark.parametrize("name", ["receipt.json", "scene.json", "objects.json", "qa.json"])
def test_preview_is_not_a_project(tmp_path, name):
    out = tmp_path / "preview"
    write_project(simple_scene(), out, scale=1, preview=True)
    assert not (out / name).exists()
    with pytest.raises(ValueError, match="draft preview"):
        load_project(out)


def test_preview_replaces_a_previous_preview(tmp_path):
    out = tmp_path / "preview"
    write_project(simple_scene(), out, scale=1, preview=True)
    changed = simple_scene()
    changed["objects"][0]["style"]["fill"] = "#ffddaa"
    write_project(changed, out, scale=2, preview=True)
    assert read_json(out / "preview.json")["render_scale"] == 2


def test_preview_never_overwrites_a_project_or_unknown_directory(tmp_path):
    project = tmp_path / "project"
    write_project(simple_scene(), project, scale=1)
    with pytest.raises(ValueError, match="not a draft preview"):
        write_project(simple_scene(), project, scale=1, preview=True)
    assert (project / "receipt.json").is_file()
    other = tmp_path / "notes"
    other.mkdir()
    (other / "keep.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="not a draft preview"):
        write_project(simple_scene(), other, scale=1, preview=True)
    assert (other / "keep.txt").is_file()


def test_full_write_records_stage_timings(tmp_path):
    result = write_project(simple_scene(), tmp_path / "project", scale=1)
    timings = result["timings"]
    for name in ("validate", "to_svg", "render", "inventory", "qa", "publish"):
        assert isinstance(timings[name], float) and timings[name] >= 0.0, (name, timings)
    assert timings["total"] >= sum(timings[name] for name in
                                   ("validate", "to_svg", "render", "inventory", "qa")) - 0.01


def test_receipt_timings_cover_the_stages_before_publish(tmp_path):
    write_project(simple_scene(), tmp_path / "project", scale=1)
    receipt = read_json(tmp_path / "project" / "receipt.json")
    assert set(receipt["timings"]) == set(PRE_PUBLISH)
    assert all(isinstance(value, float) for value in receipt["timings"].values())
    assert (tmp_path / "project" / "receipt.json").is_file()


def test_preview_records_timings(tmp_path):
    result = write_project(simple_scene(), tmp_path / "preview", scale=1, preview=True)
    assert set(result["timings"]) == set(PREVIEW_STAGES)
    assert result["timings"]["total"] >= result["timings"]["render"]


def test_cli_preview_renders_at_scale_one_and_writes_no_receipt(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(simple_scene()), encoding="utf-8")
    out = tmp_path / "quick"
    result = execute(parser().parse_args(["draw", str(plan), "--output", str(out), "--preview"]))
    assert result["kind"] == "draft_preview" and result["render_scale"] == 1
    assert (out / "preview.png").is_file() and not (out / "receipt.json").exists()
    scaled = execute(parser().parse_args(
        ["draw", str(plan), "--output", str(tmp_path / "quick2"), "--preview", "--scale", "2"]))
    assert scaled["render_scale"] == 2


def test_cli_draw_without_preview_is_still_a_full_project(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(simple_scene()), encoding="utf-8")
    out = tmp_path / "full"
    result = execute(parser().parse_args(["draw", str(plan), "--output", str(out)]))
    assert result["status"] == "success" and "kind" not in result
    assert (out / "receipt.json").is_file() and read_json(out / "receipt.json")["render_scale"] == 3
    assert load_project(out)["revision"] == 0

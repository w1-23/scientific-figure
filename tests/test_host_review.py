"""Assessments must describe the preview actually inspected, including on clones."""
import copy
import json

import pytest

from scientific_figure.cli import execute, parser
from scientific_figure.project import load_project, write_project
from scientific_figure.reviews import current_project_review, validate_record


def scene():
    return {"schema_version": "0.1", "revision": 0,
            "canvas": {"width": 40, "height": 40, "background": "#ffffff"},
            "objects": [{"id": "body", "kind": "ellipse", "geometry": {"cx": 20, "cy": 20, "rx": 12, "ry": 10},
                         "style": {"fill": "#4488bb"}}]}


def record_for(project):
    return {"schema_version": "0.3", "binding": current_project_review(load_project(project), project)["binding"],
            "reviewer": {"name": "Test host", "role": "host"},
            "visual": {"status": "passed", "findings": []},
            "scientific": {"status": "not_executed", "findings": []}}


def test_record_clone_edit_invalidates_and_preserves_original(tmp_path):
    original, clone = tmp_path / "original", tmp_path / "clone"
    write_project(scene(), original)
    assert current_project_review(load_project(original), original)["visual"]["status"] == "not_executed"
    record = tmp_path / "record.json"
    record.write_text(json.dumps(record_for(original)))
    result = execute(parser().parse_args(["review", str(original), "--record", str(record)]))
    assert result["host_review"]["visual"]["status"] == "passed"
    patch = tmp_path / "patch.json"
    patch.write_text(json.dumps({"base_revision": 0, "operations": [{"id": "body", "set": {"style": {"fill": "#ee7744"}}}]}))
    execute(parser().parse_args(["edit", str(original), "--patch", str(patch), "--output", str(clone)]))
    reviewed = current_project_review(load_project(clone), clone)
    assert reviewed["visual"]["status"] == "stale_after_change"
    assert reviewed["visual"]["last_status"] == "passed"
    assert reviewed["host_record"]["binding"]["revision"] == 0
    assert current_project_review(load_project(original), original)["visual"]["status"] == "passed"
    assert load_project(clone)["revision"] == 1


def test_old_preview_cannot_be_approved_even_with_same_revision(tmp_path):
    project = tmp_path / "figure"
    write_project(scene(), project, scale=1)
    record = record_for(project)
    write_project(load_project(project), project, scale=2, expected_revision=0)
    with pytest.raises(ValueError, match="binding_conflict"):
        validate_record(record, load_project(project), current_project_review(load_project(project), project)["binding"])


def test_resources_are_in_review_scene_fingerprint(tmp_path):
    project = tmp_path / "figure"
    source = scene()
    source["resources"] = [{"id": "paint", "kind": "linear_gradient", "start": [0, 0], "end": [1, 1],
                            "stops": [[0, "#ffffff"], [1, "#000000"]]}]
    source["objects"][0]["style"]["fill"] = "url(#paint)"
    write_project(source, project)
    record = record_for(project)
    modified = copy.deepcopy(source)
    modified["resources"][0]["stops"][1][1] = "#ff0000"
    write_project(modified, project, expected_revision=0, extras={"review.json": {"host_record": record}})
    assert current_project_review(load_project(project), project)["visual"]["status"] == "stale_after_change"


def test_semantic_protected_object_cannot_disappear_on_commit(tmp_path):
    from scientific_figure.editing import apply_patch
    from scientific_figure.reconstruct import reconstruct
    plan = {"schema_version": "0.3", "mode": "semantic", "canvas": scene()["canvas"],
            "brief": {"purpose": "Protected shape", "target_size": [40, 40], "simplification": "One ellipse",
                      "protected_ids": ["body"], "allowed_omissions": [], "uncertainties": []},
            "steps": [{"step_id": "body", "operation": "construct", "object": scene()["objects"][0]}]}
    original = reconstruct(None, plan)
    project = tmp_path / "protected"
    write_project(original, project)
    receipt = (project / "receipt.json").read_bytes()
    hidden = apply_patch(original, {"base_revision": 0, "operations": [{"id": "body", "set": {"visible": False}}]})
    with pytest.raises(ValueError, match="protected_id_not_visible"):
        write_project(hidden, project, expected_revision=0)
    assert (project / "receipt.json").read_bytes() == receipt
    assert load_project(project)["revision"] == 0


def test_unknown_review_object_and_malformed_record_rejected(tmp_path):
    project = tmp_path / "figure"
    write_project(scene(), project)
    record = record_for(project)
    record["visual"]["findings"] = [{"object_id": "invented", "issue": "missing", "evidence": "source", "action": "draw", "remaining_uncertainty": "none"}]
    binding = record["binding"]
    with pytest.raises(ValueError, match="unknown object"):
        validate_record(record, scene(), binding)
    record["visual"]["findings"] = []
    record["visual"]["status"] = "automatically_scientifically_certified"
    with pytest.raises(ValueError):
        validate_record(record, scene(), binding)

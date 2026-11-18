"""Exercise real CLI receipts and persisted context across project operations."""
import hashlib
import json

import pytest

from scientific_figure.cli import main
from scientific_figure.context import validate_context
from scientific_figure.project import load_project
from scientific_figure.scene import load_json


CONTEXT_FILES = {"context.json", "style-profile.json", "text-manifest.json", "layout-manifest.json"}


def write_json(path, document):
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


@pytest.fixture
def cli(capsys):
    def invoke(*arguments, expected=0):
        code = main([str(argument) for argument in arguments])
        captured = capsys.readouterr()
        assert code == expected, (captured.out, captured.err)
        assert captured.err == ""
        # An invocation emits exactly one machine-readable receipt.
        lines = captured.out.strip().splitlines()
        assert len(lines) == 1, captured.out
        return json.loads(lines[0])
    return invoke


@pytest.fixture
def inputs(tmp_path):
    plan = {"schema_version": "0.3", "mode": "semantic",
            "canvas": {"width": 100, "height": 70, "background": "#ffffff"},
            "brief": {"purpose": "Explain one labeled compartment", "target_size": [100, 70],
                      "simplification": "Use a simple boundary and plain text", "protected_ids": [],
                      "allowed_omissions": [], "uncertainties": []},
            "steps": [{"step_id": "body", "operation": "construct", "object": {
                "id": "body", "kind": "rect", "geometry": {"x": 10, "y": 10, "width": 30, "height": 25},
                "style": {"fill": "#ffffff"}}},
                      {"step_id": "label", "operation": "construct", "object": {
                          "id": "label", "kind": "text", "geometry": {"x": 55, "y": 25, "text": "A"}}}]}
    plan_path = tmp_path / "plan.json"
    write_json(plan_path, plan)
    profile = {"schema_version": "0.1", "id": "first_profile", "tokens": [
        {"id": "main", "type": "color", "value": "#aaccdd"}]}
    context = {"schema_version": "0.1", "id": "first_context",
               "style_profile": write_json(tmp_path / "profile.json", profile),
               "text_manifest": write_json(tmp_path / "text.json", {
                   "schema_version": "0.1", "id": "labels", "entries": [
                       {"id": "letter", "object_id": "label", "text": "A"}]}),
               "layout_manifest": write_json(tmp_path / "layout.json", {
                   "schema_version": "0.1", "id": "layout", "entries": [
                       {"id": "order", "object_ids": ["body", "label"], "relation": "left_of"}]}),
               "preserved_ids": ["body"],
               "token_bindings": [{"object_id": "body", "field": "fill", "token": "main"}]}
    context_path = tmp_path / "context.json"
    write_json(context_path, context)
    return plan_path, context_path, context


def context_extras(project):
    return {name: load_json(project / name) for name in CONTEXT_FILES if (project / name).is_file()}


def test_draw_style_check_and_clone_preserve_portable_context(tmp_path, cli, inputs):
    plan, context_path, _ = inputs
    original, clone = tmp_path / "original", tmp_path / "clone"
    result = cli("draw", plan, "--context", context_path, "--output", original, "--scale", 1)
    assert result["revision"] == 0
    before = load_project(original)
    assert before["objects"][0]["style"]["fill"] == "#aaccdd"
    assert before["provenance"]["brief"]["protected_ids"] == ["body"]
    assert set(context_extras(original)) == CONTEXT_FILES
    assert CONTEXT_FILES <= set(load_json(original / "receipt.json")["artifacts"])
    assert cli("style-check", original)["status"] == "passed"
    patch = tmp_path / "patch.json"
    write_json(patch, {"base_revision": 0, "operations": [
        {"id": "body", "set": {"geometry": {"x": 12}}}]})
    cli("edit", original, "--patch", patch, "--output", clone, "--scale", 1)
    assert cli("validate", clone)["revision"] == 1
    assert load_project(original) == before
    assert context_extras(clone) == context_extras(original)
    assert cli("style-check", clone)["status"] == "passed"
    assert load_project(clone)["provenance"]["context"] == before["provenance"]["context"]
    # Original author documents are not needed to load, validate or check a clone.
    for source in (plan, context_path, tmp_path / "profile.json", tmp_path / "text.json", tmp_path / "layout.json"):
        source.unlink()
    loaded = load_project(clone)
    validate_context(loaded, context_extras(clone))
    assert cli("style-check", clone)["status"] == "passed"


@pytest.mark.parametrize("clone_attachment", [False, True], ids=["in_place", "new_output"])
def test_context_replacement_cleans_old_extras_and_expires_review(tmp_path, cli, inputs, clone_attachment):
    plan, context_path, _ = inputs
    original = tmp_path / "original"
    cli("draw", plan, "--context", context_path, "--output", original, "--scale", 1)
    binding = cli("review", original)["host_review"]["binding"]
    record = {"schema_version": "0.3", "binding": binding,
              "reviewer": {"name": "Integration host", "role": "host"},
              "visual": {"status": "passed", "findings": []},
              "scientific": {"status": "uncertain", "findings": []}}
    record_path = tmp_path / "review-record.json"
    write_json(record_path, record)
    reviewed = cli("review", original, "--record", record_path)["host_review"]
    assert reviewed["host_record_current"] is True
    assert reviewed["visual"]["status"] == "passed"
    old_scene = load_project(original)
    old_digest = old_scene["provenance"]["context"]["sha256"]
    next_context = {"schema_version": "0.1", "id": "replacement",
                    "style_profile": write_json(tmp_path / "next-profile.json", {
                        "schema_version": "0.1", "id": "second_profile", "tokens": [
                            {"id": "main", "type": "color", "value": "#ddaa88"}]}),
                    "token_bindings": [{"object_id": "body", "field": "fill", "token": "main"}]}
    replacement = tmp_path / "replacement.json"
    write_json(replacement, next_context)
    target = tmp_path / "replacement-project" if clone_attachment else original
    args = ["context", "attach", original, "--context", replacement, "--base-revision", 0]
    if clone_attachment:
        args += ["--output", target]
    assert cli(*args)["revision"] == 1
    changed = load_project(target)
    assert changed["provenance"]["context"]["sha256"] != old_digest
    assert changed["provenance"]["context"]["id"] == "replacement"
    assert set(context_extras(target)) == {"context.json", "style-profile.json"}
    assert not (target / "text-manifest.json").exists()
    assert not (target / "layout-manifest.json").exists()
    assert {"text-manifest.json", "layout-manifest.json"}.isdisjoint(load_json(target / "receipt.json")["artifacts"])
    assert load_json(target / "style-profile.json")["id"] == "second_profile"
    # Attach records new intent, while the existing rendered scene stays authoritative.
    assert changed["objects"] == old_scene["objects"]
    report = cli("style-check", target)
    assert report["status"] == "needs_review"
    assert report["mismatches"][0]["expected"] == "#ddaa88"
    review = cli("review", target)["host_review"]
    assert review["host_record_current"] is False
    assert review["host_record"]["binding"] == binding
    assert review["visual"]["status"] == "stale_after_change"
    assert review["visual"]["last_status"] == "passed"
    assert review["scientific"]["status"] == "stale_after_change"
    if clone_attachment:
        assert load_project(original) == old_scene
        assert cli("review", original)["host_review"]["host_record_current"] is True
        assert set(context_extras(original)) == CONTEXT_FILES


@pytest.mark.parametrize("operation", ["draw", "attach"])
def test_negative_gradient_stop_offset_fails_before_project_changes(tmp_path, cli, inputs, operation):
    plan, context_path, context = inputs
    output = tmp_path / "project"
    if operation == "attach":
        cli("draw", plan, "--context", context_path, "--output", output, "--scale", 1)
        original_receipt = (output / "receipt.json").read_bytes()
    invalid = {"schema_version": "0.1", "id": "bad_offset", "tokens": [
        {"id": "main", "type": "linear_gradient", "value": {
            "kind": "linear_gradient", "start": [0, 0], "end": [100, 0],
            "stops": [[-0.1, "#ffffff"], [1, "#112233"]]}}]}
    context["style_profile"] = write_json(tmp_path / "invalid-profile.json", invalid)
    write_json(context_path, context)
    if operation == "draw":
        result = cli("draw", plan, "--context", context_path, "--output", output, expected=2)
        assert not output.exists()
    else:
        result = cli("context", "attach", output, "--context", context_path, "--base-revision", 0, expected=2)
        assert (output / "receipt.json").read_bytes() == original_receipt
        assert cli("validate", output)["revision"] == 0
    assert result["status"] == "failed"
    assert result["error"]["type"] == "ValueError"


def test_direct_scene_resource_rejects_negative_gradient_stop_offset(tmp_path, cli):
    scene = {"schema_version": "0.1", "revision": 0,
             "canvas": {"width": 40, "height": 40},
             "resources": [{"id": "paint", "kind": "linear_gradient", "start": [0, 0], "end": [40, 0],
                            "stops": [[-0.1, "#ffffff"], [1, "#112233"]]}],
             "objects": [{"id": "body", "kind": "rect",
                          "geometry": {"x": 5, "y": 5, "width": 30, "height": 30},
                          "style": {"fill": "url(#paint)"}}]}
    source, output = tmp_path / "scene.json", tmp_path / "invalid-scene"
    write_json(source, scene)
    result = cli("draw", source, "--output", output, expected=2)
    assert result["status"] == "failed"
    assert "gradient stop" in result["error"]["message"]
    assert not output.exists()


def test_orphan_profile_is_rejected_even_with_matching_receipt(tmp_path, cli, inputs):
    plan, _, _ = inputs
    output = tmp_path / "plain"
    cli("draw", plan, "--output", output, "--scale", 1)
    orphan = output / "style-profile.json"
    write_json(orphan, {"schema_version": "0.1", "id": "orphan", "tokens": []})
    receipt = load_json(output / "receipt.json")
    receipt["artifacts"][orphan.name] = {"sha256": hashlib.sha256(orphan.read_bytes()).hexdigest(),
                                         "bytes": orphan.stat().st_size}
    write_json(output / "receipt.json", receipt)
    result = cli("validate", output, expected=2)
    assert result["status"] == "failed"
    assert "missing context.json" in result["error"]["message"]

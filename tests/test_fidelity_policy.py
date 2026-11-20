"""Declared fidelity maps to existing checks; uncovered aspects stay open."""
import copy

import pytest

from scientific_figure.planning import (fidelity_bindings, fidelity_policy,
                                        plan_review)
from scientific_figure.scene import validate


def scene():
    return validate({"schema_version": "0.3", "revision": 0,
                     "canvas": {"width": 200, "height": 120, "background": "#ffffff"},
                     "objects": [{"id": "cell", "kind": "ellipse",
                                  "geometry": {"cx": 100, "cy": 60, "rx": 40, "ry": 30},
                                  "style": {"fill": "#cccccc", "stroke": "#333333", "stroke_width": 2}}]})


def plan(fidelity):
    return {"task_type": "reference_reconstruction",
            "modules": [{"id": "main", "objects": [
                {"id": "cell", "role": "cell", "source_id": "reference",
                 "source_region": [10, 10, 80, 60], "fidelity": fidelity,
                 "construction": {"kind": "direct", "objects": []}}]}],
            "relations": [],
            "review_spec": {"required_objects": [], "must_preserve": [], "unknown": []}}


MAPPINGS = {"cell": {"root_id": "cell", "part_ids": {"root": "cell"}}}


def fake_constraints(*bindings):
    return {"schema_version": "0.1", "kind": "constraint_check", "bindings": [
        {"object_id": object_id, "kind": kind, "status": status,
         "metrics": {"recall": recall, "mean_offset": 0.4}, "notes": []}
        for object_id, kind, status, recall in bindings]}


def test_policy_maps_shape_detail_and_holes_to_existing_checks():
    policy = fidelity_policy({"shape": "hard", "detail": "hard",
                              "topology": ["holes", "repetition", "layers"]})
    assert policy["grade"] == "hard"
    assert policy["required_checks"] == ["boundary", "region", "negative_space"]
    assert policy["unchecked_topology"] == ["repetition", "layers"]
    checks = {check["kind"]: check for check in policy["checks"]}
    assert checks["boundary"]["recall_min"] == 0.85 and checks["boundary"]["allowed_deviation"] == 2.0
    assert checks["region"]["recall_min"] == 0.70 and checks["region"]["allowed_deviation"] == 4.0
    assert checks["negative_space"]["allowed_deviation"] == 4.0


def test_soft_and_ignore_grades_do_not_become_strict():
    soft = fidelity_policy({"shape": "soft"})
    assert soft["required_checks"] == ["boundary"]
    assert soft["checks"][0]["recall_min"] == 0.60 and soft["checks"][0]["allowed_deviation"] == 6.0
    assert fidelity_policy({"shape": "ignore"})["required_checks"] == []
    assert fidelity_policy({"shape": "ignore", "detail": "soft"})["required_checks"] == []


def test_explicit_deviation_overrides_each_default():
    policy = fidelity_policy({"shape": "hard", "allowed_deviation": 1.5})
    assert [check["allowed_deviation"] for check in policy["checks"]] == [1.5]


def test_bindings_use_scene_ids_and_schema_fields():
    value = fidelity_bindings(plan({"shape": "hard", "detail": "hard"}), MAPPINGS)
    assert [binding["kind"] for binding in value] == ["boundary", "region"]
    for binding in value:
        assert binding["object_id"] == "cell" and binding["source_region"] == [10, 10, 80, 60]
        assert binding["group"] is None and binding["min_count"] == 1
        assert 0 < binding["allowed_deviation"] <= 40
        assert 0 <= binding["recall_min"] <= 1


def test_review_marks_every_uncovered_required_check():
    report = plan_review(scene(), plan({"shape": "hard", "detail": "hard"}), MAPPINGS)
    assert report["fidelity_policy"][0]["missing"] == ["boundary", "region"]
    assert [entry["constraint"] for entry in report["checks"]
            if entry["kind"] == "fidelity" and entry["status"] == "needs_review"] == ["boundary", "region"]
    assert report["status"] == "needs_review"


def test_constraint_coverage_removes_only_the_covered_check():
    report = plan_review(scene(), plan({"shape": "hard", "detail": "hard"}), MAPPINGS,
                         constraints_report=fake_constraints(("cell", "boundary", "passed", 0.95)))
    policy = report["fidelity_policy"][0]
    assert policy["checked"] == ["boundary"] and policy["missing"] == ["region"]
    fidelity_checks = [entry for entry in report["checks"]
                       if entry["kind"] == "fidelity" and entry["status"] == "needs_review"]
    assert [entry["constraint"] for entry in fidelity_checks] == ["region"]


def test_unchecked_topology_stays_needs_review_not_a_pass():
    report = plan_review(scene(), plan({"shape": "hard", "topology": ["repetition", "connectivity"]}),
                         MAPPINGS)
    fidelity_checks = [entry for entry in report["checks"] if entry["kind"] == "fidelity"]
    assert [entry["constraint"] for entry in fidelity_checks] == ["boundary", "repetition", "connectivity"]
    assert all(entry["status"] == "needs_review" for entry in fidelity_checks)
    assert report["fidelity_policy"][0]["unchecked_topology"] == ["repetition", "connectivity"]


def test_failed_constraint_propagates_to_the_review():
    report = plan_review(scene(), plan({"shape": "soft"}), MAPPINGS,
                         constraints_report=fake_constraints(("cell", "boundary", "failed", 0.2)))
    assert report["status"] == "failed"
    assert report["fidelity_policy"][0]["missing"] == []


def test_policy_rejects_non_object_fidelity():
    with pytest.raises(ValueError, match="fidelity must be an object"):
        fidelity_policy("hard")


def test_plan_review_does_not_mutate_the_input_plan():
    value = plan({"shape": "hard", "detail": "hard", "topology": ["holes"]})
    before = copy.deepcopy(value)
    plan_review(scene(), value, MAPPINGS)
    assert value == before

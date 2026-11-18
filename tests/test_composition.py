import copy
import json
from io import BytesIO

import pytest
from PIL import Image

from scientific_figure.bundles import create_bundle, validate_bundle
from scientific_figure.composition import compose
from scientific_figure.editing import apply_patch
from scientific_figure.geometry import Geometry
from scientific_figure.project import load_project, write_project
from scientific_figure.render import render_svg
from scientific_figure.svg import to_svg

from test_bundles import asset_scene, bundle, save


def composition_plan():
    return {"schema_version": "0.1", "canvas": {"width": 220, "height": 120, "background": "none"},
            "placements": [
                {"asset_id": "first", "instance_id": "A", "transform": [1, 0, 0, 1, 0, 0]},
                {"asset_id": "second", "instance_id": "B", "transform": [1, 0, 0, 1, 100, 0]}],
            "connections": [{"id": "bridge", "from": {"instance_id": "A", "object_id": "right", "anchor": "right"},
                             "to": {"instance_id": "B", "object_id": "left", "anchor": "left"}}]}


def build(tmp_path, bundle, plan=None, name="composite"):
    request = save(tmp_path / "composition-plan.json", plan or composition_plan())
    output = tmp_path / name
    receipt = compose(request, bundle, output, scale=1)
    return load_project(output), receipt, output


def nodes(scene):
    return {o["id"]: o for o in scene["objects"]}


def test_two_assets_same_local_ids_and_resources_are_namespaced_editable_and_bound(bundle, tmp_path):
    scene, receipt, output = build(tmp_path, bundle)
    provenance = scene["provenance"]["composition"]
    a, b = provenance["instances"]["A"], provenance["instances"]["B"]
    assert set(a["id_map"]["objects"].values()).isdisjoint(b["id_map"]["objects"].values())
    assert set(a["id_map"]["resources"].values()).isdisjoint(b["id_map"]["resources"].values())
    assert max(map(len, [o["id"] for o in scene["objects"]] + [r["id"] for r in scene["resources"]])) <= 100
    assert receipt["id_map_location"] == "scene.json#/provenance/composition/instances/<instance_id>/id_map"
    assert receipt["bundle_sha256"] == provenance["bundle_sha256"] == validate_bundle(bundle)["bundle_sha256"]
    assert provenance["members"]["first"]["artifacts"]["scene.json"]["sha256"]
    index = nodes(scene)
    for entry in (a, b):
        mapping = entry["id_map"]
        left = index[mapping["objects"]["left"]]
        assert left["parent"] == mapping["objects"]["group"]
        assert index[mapping["objects"]["group"]]["parent"] == entry["wrapper_id"]
        assert left["style"]["fill"] == left["style"]["stroke"] == f"url(#{mapping['resources']['shade']})"
        assert left["semantic"]["name"] == "left"
        assert left["semantic"]["peer"]["object_id"] == mapping["objects"]["right"]
        assert left["semantic"]["palette"]["id"] == mapping["resources"]["shade"]
        assert left["semantic"]["object_ids"] == [mapping["objects"]["left"], mapping["objects"]["right"]]
        assert index[mapping["objects"]["wire"]]["kind"] == "connector"
    review = json.loads((output / "review.json").read_text())
    assert review["visual"]["status"] == review["scientific"]["status"] == "not_executed"
    assert set(scene["provenance"]) == {"mode", "composition"}
    assert not any(k in scene["provenance"] for k in ("brief", "complexity_limits", "host_record", "semantic_audit"))


def test_live_internal_and_cross_instance_connectors_follow_independent_edits(bundle, tmp_path):
    original, _, output = build(tmp_path, bundle)
    instances = original["provenance"]["composition"]["instances"]
    a = instances["A"]["id_map"]["objects"]
    b = instances["B"]["id_map"]["objects"]
    before = Geometry(original)
    snapshot = copy.deepcopy(original)
    changed = apply_patch(original, {"schema_version": "0.3", "base_revision": 0,
        "operations": [{"op": "set", "id": a["right"], "set": {"geometry": {"x": 70}}}]})
    after = Geometry(changed)
    assert before.endpoints(a["wire"])[1] != after.endpoints(a["wire"])[1]
    assert before.endpoints("bridge")[0] != after.endpoints("bridge")[0]
    assert before.endpoints("bridge")[1] == after.endpoints("bridge")[1]
    assert nodes(changed)[b["right"]] == nodes(original)[b["right"]]
    assert before.endpoints(b["wire"]) == after.endpoints(b["wire"])
    assert original == snapshot
    write_project(changed, output, scale=1, expected_revision=0)
    assert load_project(output)["revision"] == 1
    assert load_project(bundle / "assets/first") == asset_scene()


def test_group_alpha_affine_transform_and_nonzero_viewbox_use_local_units(bundle, tmp_path):
    plan = composition_plan()
    plan["placements"] = [{"asset_id": "first", "instance_id": "A", "transform": [2, 0, 0.5, 1, 5, 7]}]
    del plan["connections"]
    scene, _, _ = build(tmp_path, bundle, plan)
    entry = scene["provenance"]["composition"]["instances"]["A"]
    a = entry["id_map"]["objects"]
    assert Geometry(scene).world(a["left"]) == [2, 0, 0.5, 1, 5, 7]
    assert Geometry(scene).bounds(a["right"]) == [115, 27, 152.5, 42]
    assert nodes(scene)[a["group"]]["style"]["opacity"] == 0.5
    assert nodes(scene)[entry["wrapper_id"]]["transform"] == plan["placements"][0]["transform"]
    image = Image.open(BytesIO(render_svg(to_svg(scene), 1))).convert("RGBA")
    # The opaque right-hand rect receives group alpha exactly once.
    assert 120 <= image.getpixel((130, 32))[3] <= 135
    assert image.getpixel((5, 7))[3] == 0


def test_same_asset_placed_twice_can_edit_resource_independently(bundle, tmp_path):
    plan = composition_plan()
    plan["placements"][1]["asset_id"] = "first"
    original, _, _ = build(tmp_path, bundle, plan)
    entries = original["provenance"]["composition"]["instances"]
    aid = entries["A"]["id_map"]["resources"]["shade"]
    bid = entries["B"]["id_map"]["resources"]["shade"]
    modified = apply_patch(original, {"schema_version": "0.3", "base_revision": 0, "operations": [
        {"op": "update_resource", "id": aid, "set": {"stops": [[0, "#00ff00"], [1, "#ffff00"]]}}]})
    resource = {r["id"]: r for r in modified["resources"]}
    assert resource[aid]["stops"] != resource[bid]["stops"]
    assert original["resources"][0]["stops"] == original["resources"][1]["stops"]


def test_containers_parenting_and_explicit_sibling_order_use_patch(bundle, tmp_path):
    plan = composition_plan()
    plan["containers"] = [{"id": "panel", "kind": "group", "style": {"opacity": 0.8}},
                          {"id": "label", "kind": "text", "parent": "panel", "geometry": {"x": 5, "y": 10, "text": "Panel"}}]
    for item in plan["placements"]:
        item["parent"] = "panel"
    plan["order"] = [{"parent": "panel", "order": ["B", "A", "label"]}]
    scene, receipt, _ = build(tmp_path, bundle, plan)
    instances = scene["provenance"]["composition"]["instances"]
    assert Geometry(scene).children["panel"] == [instances["B"]["wrapper_id"], instances["A"]["wrapper_id"], "label"]
    assert receipt["revision"] == 1


@pytest.mark.parametrize("policy", [None, "preserve", "omit"])
def test_asset_canvas_background_requires_explicit_policy(tmp_path, policy):
    value = asset_scene()
    value["canvas"]["background"] = "#ff000080"
    project = tmp_path / "asset"
    write_project(value, project, scale=1)
    create_bundle(save(tmp_path / "bundle-plan.json", {"schema_version": "0.1", "assets": [{"id": "first", "project": "asset"}]}), tmp_path / "bundle")
    plan = composition_plan()
    plan["placements"] = plan["placements"][:1]
    del plan["connections"]
    if policy:
        plan["asset_background"] = policy
    if policy is None:
        with pytest.raises(ValueError, match="asset_background"):
            build(tmp_path, tmp_path / "bundle", plan)
        assert not (tmp_path / "composite").exists()
    else:
        scene, _, _ = build(tmp_path, tmp_path / "bundle", plan)
        entry = scene["provenance"]["composition"]["instances"]["A"]
        if policy == "preserve":
            background = nodes(scene)[entry["background_id"]]
            assert background["geometry"] == {"x": 10, "y": 20, "width": 80, "height": 50}
            assert background["style"]["fill"] == "#ff000080"
        else:
            assert "background_id" not in entry


@pytest.mark.parametrize("failure", ["unknown_asset", "duplicate_instance", "missing_endpoint", "singular", "bad_parent", "bad_order", "scale"])
def test_invalid_compositions_leave_no_output(bundle, tmp_path, failure):
    plan = composition_plan()
    if failure == "unknown_asset":
        plan["placements"][0]["asset_id"] = "missing"
    elif failure == "duplicate_instance":
        plan["placements"][1]["instance_id"] = "A"
    elif failure == "missing_endpoint":
        plan["connections"][0]["to"]["object_id"] = "missing"
    elif failure == "singular":
        plan["placements"][0]["transform"] = [0, 0, 0, 0, 0, 0]
    elif failure == "bad_parent":
        plan["placements"][0]["parent"] = "missing"
    elif failure == "bad_order":
        plan["order"] = [{"parent": None, "order": ["A"]}]
    request = save(tmp_path / "composition-plan.json", plan)
    output = tmp_path / "failed"
    with pytest.raises(ValueError):
        compose(request, bundle, output, scale=0 if failure == "scale" else 1)
    assert not output.exists()
    assert not list(tmp_path.glob(".failed*"))


def test_changed_bundle_aborts_before_output(bundle, tmp_path):
    (bundle / "assets/first/figure.svg").write_text("tampered")
    with pytest.raises(ValueError):
        build(tmp_path, bundle)
    assert not (tmp_path / "composite").exists()


def test_font_requirement_mismatch_rejects_before_output(bundle, tmp_path, monkeypatch):
    import scientific_figure.composition as module
    monkeypatch.setattr(module, "font_info", lambda: {"sha256": "0" * 64})
    with pytest.raises(ValueError, match="font"):
        build(tmp_path, bundle)
    assert not (tmp_path / "composite").exists()


def test_member_semantic_limits_and_host_approval_do_not_approve_composite(tmp_path):
    from scientific_figure.reconstruct import reconstruct
    from scientific_figure.reviews import current_project_review
    plan = {"schema_version": "0.3", "mode": "semantic", "canvas": {"width": 80, "height": 50},
            "brief": {"purpose": "one box", "target_size": [80, 50], "simplification": "rectangle",
                      "protected_ids": ["left"], "allowed_omissions": [], "uncertainties": []},
            "steps": [{"step_id": "box", "operation": "construct", "object": {
                "id": "left", "kind": "rect", "geometry": {"x": 5, "y": 5, "width": 10, "height": 10}}}]}
    original = reconstruct(None, plan)
    member = tmp_path / "member"
    write_project(original, member, scale=1)
    record = {"schema_version": "0.3", "binding": current_project_review(original, member)["binding"],
              "reviewer": {"name": "test host", "role": "host"},
              "visual": {"status": "passed", "findings": []}, "scientific": {"status": "passed", "findings": []}}
    write_project(original, member, scale=1, expected_revision=0, extras={"review.json": {"host_record": record}})
    create_bundle(save(tmp_path / "bundle-plan.json", {"schema_version": "0.1", "assets": [
        {"id": "first", "project": "member"}]}), tmp_path / "bundle")
    composed = composition_plan()
    composed["placements"] = composed["placements"][:1]
    del composed["connections"]
    scene, _, output = build(tmp_path, tmp_path / "bundle", composed)
    assert set(scene["provenance"]) == {"mode", "composition"}
    review = current_project_review(scene, output)
    assert review["visual"]["status"] == review["scientific"]["status"] == "not_executed"
    assert "host_record" not in review
    assert "semantic" not in review["mechanical"]


def test_default_placement_order_and_deterministic_long_ids(bundle, tmp_path):
    plan = composition_plan()
    del plan["connections"]
    plan["placements"][0]["instance_id"] = "A" * 100
    plan["placements"][1]["instance_id"] = "B" * 100
    first, _, _ = build(tmp_path, bundle, plan, "one")
    second, _, _ = build(tmp_path, bundle, plan, "two")
    assert first == second
    instances = first["provenance"]["composition"]["instances"]
    assert Geometry(first).children[None] == [instances[p["instance_id"]]["wrapper_id"] for p in plan["placements"]]

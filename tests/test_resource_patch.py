import copy
from io import BytesIO

import pytest
from PIL import Image

from scientific_figure.editing import apply_patch, export_scene
from scientific_figure.render import render_svg
from scientific_figure.scene import validate
from scientific_figure.svg import to_svg


def gradient(rid="shade", colors=("#ff0000", "#0000ff")):
    return {"id": rid, "kind": "linear_gradient", "start": [0, 0], "end": [1, 0],
            "stops": [[0, colors[0]], [1, colors[1]]]}


def scene():
    return {"schema_version": "0.1", "revision": 7,
            "canvas": {"width": 100, "height": 50, "background": "#ffffff"},
            "resources": [gradient()], "objects": [
                {"id": oid, "kind": "rect", "geometry": {"x": x, "y": 5, "width": 30, "height": 30},
                 "style": {"fill": "url(#shade)"}}
                for oid, x in (("left", 5), ("right", 55))]}


def patch(*operations, version="0.3", revision=7):
    return {"schema_version": version, "base_revision": revision, "operations": list(operations)}


def pixels(value):
    return Image.open(BytesIO(render_svg(to_svg(value), 1))).convert("RGB")


def test_shared_gradient_update_changes_rendering_of_all_references():
    original = scene()
    snapshot = copy.deepcopy(original)
    request = patch({"op": "update_resource", "id": "shade", "set": {
        "stops": [[0, "#00ff00"], [1, "#ffff00"]], "end": [0, 1]}})
    changed = apply_patch(original, request)
    before, after = pixels(original), pixels(changed)
    for box in ((5, 5, 35, 35), (55, 5, 85, 35)):
        assert before.crop(box).tobytes() != after.crop(box).tobytes()
    assert changed["objects"] == original["objects"]
    assert changed["revision"] == 8
    assert original == snapshot
    # Neither the scene nor the patch payload is aliased by the committed result.
    request["operations"][0]["set"]["stops"][0][1] = "#000000"
    changed["resources"][0]["start"][0] = 0.5
    assert changed["resources"][0]["stops"][0][1] == "#00ff00"
    assert original == snapshot


def test_fork_resource_isolates_object_and_exports_new_resource_closure():
    original = scene()
    private = gradient("private_shade", ("#00ff00", "#ffff00"))
    changed = apply_patch(original, patch(
        {"op": "add_resource", "resource": private},
        {"op": "set", "id": "left", "set": {"style": {"fill": "url(#private_shade)"}}}))
    before, after = pixels(original), pixels(changed)
    assert before.crop((5, 5, 35, 35)).tobytes() != after.crop((5, 5, 35, 35)).tobytes()
    assert before.crop((55, 5, 85, 35)).tobytes() == after.crop((55, 5, 85, 35)).tobytes()
    assert changed["objects"][1] == original["objects"][1]
    assert changed["resources"][0] == original["resources"][0]
    assert changed["revision"] == 8
    exported = export_scene(changed, ["left"], padding=0)
    assert exported["resources"] == [private]
    assert [obj["id"] for obj in exported["objects"]] == ["left"]
    assert exported["revision"] == 8
    image = pixels(exported)
    assert image.size == (30, 30)
    assert image.getpixel((15, 15))[1] == 255
    exported["resources"][0]["stops"][0][1] = "#000000"
    private["stops"][0][1] = "#ffffff"
    assert changed["resources"][1]["stops"][0][1] == "#00ff00"


@pytest.mark.parametrize("reference_first", [False, True])
def test_add_resource_and_set_reference_is_one_transaction_without_initial_resources(reference_first):
    original = scene()
    del original["resources"]
    for obj in original["objects"]:
        obj["style"]["fill"] = "#ffffff"
    operations = [
        {"op": "add_resource", "resource": gradient("new_shade")},
        {"op": "set", "id": "left", "set": {"style": {"fill": "url(#new_shade)"}}}]
    if reference_first:
        operations.reverse()
    changed = apply_patch(original, patch(*operations))
    assert changed["revision"] == original["revision"] + 1
    assert changed["objects"][0]["style"]["fill"] == "url(#new_shade)"
    assert changed["resources"] == [gradient("new_shade")]
    assert "resources" not in original
    validate(changed)


def test_remove_referenced_resource_rejects_entire_transaction():
    original = scene()
    snapshot = copy.deepcopy(original)
    with pytest.raises(ValueError, match="invalid paint"):
        apply_patch(original, patch(
            {"op": "update_resource", "id": "shade", "set": {"end": [0, 1]}},
            {"op": "set", "id": "left", "set": {"visible": False}},
            {"op": "remove_resource", "id": "shade"}))
    assert original == snapshot


def test_remove_then_repair_references_validates_only_final_scene():
    original = scene()
    changed = apply_patch(original, patch(
        {"op": "remove_resource", "id": "shade"},
        {"op": "set", "id": "left", "set": {"style": {"fill": "url(#replacement)"}}},
        {"op": "set", "id": "right", "set": {"style": {"fill": "#abcdef"}}},
        {"op": "add_resource", "resource": gradient("replacement")}))
    assert changed["revision"] == 8
    assert [r["id"] for r in changed["resources"]] == ["replacement"]
    validate(changed)
    assert original == scene()


def test_remove_then_restore_same_resource_allows_intermediate_set():
    changed = apply_patch(scene(), patch(
        {"op": "remove_resource", "id": "shade"},
        {"op": "set", "id": "left", "set": {"geometry": {"width": -1}}},
        {"op": "set", "id": "left", "set": {"geometry": {"width": 35}}},
        {"op": "add_resource", "resource": gradient()}))
    assert changed["revision"] == 8
    assert changed["objects"][0]["geometry"]["width"] == 35
    validate(changed)


def test_resources_share_transaction_with_all_structural_operations():
    original = scene()
    changed = apply_patch(original, patch(
        {"op": "add_subtree", "objects": [
            {"id": "extra", "kind": "group", "style": {"fill": "url(#future)"}},
            {"id": "child", "kind": "rect", "parent": "extra",
             "geometry": {"x": 1, "y": 1, "width": 2, "height": 2}}]},
        {"op": "reorder", "parent": None, "order": ["extra", "right", "left"]},
        {"op": "set", "id": "extra", "set": {"semantic": {"name": "new group"}}},
        {"op": "replace_subtree", "id": "extra", "objects": [
            {"id": "extra", "kind": "group"},
            {"id": "replacement_child", "kind": "ellipse", "parent": "extra",
             "geometry": {"cx": 5, "cy": 5, "rx": 2, "ry": 2}}]},
        {"op": "remove_subtree", "id": "left"},
        {"op": "remove_subtree", "id": "right"},
        {"op": "remove_resource", "id": "shade"},
        {"op": "add_resource", "resource": gradient("future")}))
    assert changed["revision"] == 8
    assert [obj["id"] for obj in changed["objects"]] == ["extra", "replacement_child"]
    assert changed["objects"][0]["style"]["fill"] == "url(#future)"
    assert changed["objects"][0]["semantic"]["name"] == "new group"
    assert len(changed["provenance"]["structural_edits"]) == 1
    validate(changed)
    assert original == scene()


@pytest.mark.parametrize("operation", [
    {"op": "add_resource", "resource": gradient(), "extra": True},
    {"op": "add_resource", "resource": {**gradient(), "extra": True}},
    {"op": "add_resource", "resource": []},
    {"op": "update_resource", "id": "shade", "set": {"id": "renamed"}},
    {"op": "update_resource", "id": "shade", "set": {"extra": 1}},
    {"op": "update_resource", "id": "shade", "set": {}},
    {"op": "update_resource", "id": "shade", "set": []},
    {"op": "update_resource", "id": "shade", "set": {"end": [0, 1]}, "extra": True},
    {"op": "update_resource", "id": "missing", "set": {"end": [0, 1]}},
    {"op": "remove_resource", "id": "missing"},
    {"op": "remove_resource", "id": "shade", "extra": True},
    {"op": "remove_resource"},
    {"op": "set", "id": "left", "set": {"id": "renamed"}},
    {"op": "set", "id": "left", "set": {"visible": False}, "extra": True},
    {"op": "unsupported"},
])
def test_resource_operation_fields_are_strict_and_failures_roll_back(operation):
    original = scene()
    snapshot = copy.deepcopy(original)
    with pytest.raises(ValueError):
        apply_patch(original, patch(
            {"op": "set", "id": "left", "set": {"geometry": {"width": 25}}}, operation))
    assert original == snapshot


@pytest.mark.parametrize("operation, message", [
    ({"op": "add_resource", "resource": gradient()}, "duplicate resource ID"),
    ({"op": "add_resource", "resource": gradient("left")}, "duplicate or reserved ID"),
    ({"op": "update_resource", "id": "shade", "set": {"kind": "radial_gradient"}}, "unsupported resource"),
    ({"op": "update_resource", "id": "shade", "set": {"stops": [[0, "#ff0000"]]}}, "at least two stops"),
    ({"op": "update_resource", "id": "shade", "set": {"end": [float("nan"), 1]}}, "nonfinite"),
])
def test_final_scene_validation_rejects_invalid_resources_atomically(operation, message):
    original = scene()
    snapshot = copy.deepcopy(original)
    with pytest.raises(ValueError, match=message):
        apply_patch(original, patch(operation))
    assert original == snapshot


@pytest.mark.parametrize("change", [
    {"base_revision": 6}, {"base_revision": 7.0}, {"base_revision": True},
    {"extra": True}, {"operations": []}, {"operations": [None]}, {"schema_version": "0.4"},
])
def test_versioned_patch_envelope_is_strict(change):
    original = scene()
    request = patch({"op": "set", "id": "left", "set": {"visible": False}})
    request.update(change)
    with pytest.raises(ValueError):
        apply_patch(original, request)
    assert original == scene()


def test_legacy_set_and_structural_patches_still_work():
    original = scene()
    first = apply_patch(original, {"base_revision": 7, "operations": [
        {"id": "left", "set": {"style": {"opacity": 0.5}}}]})
    second = apply_patch(first, patch(
        {"op": "set", "id": "right", "set": {"visible": False}},
        {"op": "reorder", "parent": None, "order": ["right", "left"]},
        version="0.2", revision=8))
    assert first["revision"] == 8
    assert second["revision"] == 9
    assert second["objects"][0]["id"] == "right"
    assert second["objects"][0]["visible"] is False
    assert second["objects"][1]["style"]["opacity"] == 0.5
    assert original == scene()


def test_02_keeps_intermediate_set_validation_and_rejects_resource_operations():
    original = scene()
    with pytest.raises(ValueError, match="invalid paint"):
        apply_patch(original, patch(
            {"op": "set", "id": "left", "set": {"style": {"fill": "url(#missing)"}}},
            {"op": "set", "id": "left", "set": {"style": {"fill": "url(#shade)"}}},
            version="0.2"))
    with pytest.raises(ValueError, match="unsupported structural operation"):
        apply_patch(original, patch({"op": "add_resource", "resource": gradient("new")}, version="0.2"))
    assert original == scene()

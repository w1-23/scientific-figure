from copy import deepcopy
from io import BytesIO

import pytest
from PIL import Image

from scientific_figure.builder import SceneBuilder
from scientific_figure.editing import apply_patch
from scientific_figure.geometry import Geometry
from scientific_figure.pathops_backend import boolean
from scientific_figure.render import render_svg
from scientific_figure.scene import validate
from scientific_figure.svg import to_svg


def repeated_scene():
    builder = SceneBuilder(180, 100)
    builder.group("panel", transform=[1, 0, 0, 1, 10, 10])
    for index in range(3):
        builder.ellipse(f"dot-{index}", 5, 5, 4, 4, parent="panel",
                        transform=[1, 0, 0, 1, index * 20, 0])
    return builder.build()


def test_loops_emit_deterministic_ids_and_local_coordinates():
    scene = repeated_scene()
    assert scene == repeated_scene()
    assert validate(scene) is scene
    assert [obj["id"] for obj in scene["objects"]] == ["panel", "dot-0", "dot-1", "dot-2"]
    assert all(obj["geometry"]["cx"] == 5 for obj in scene["objects"][1:])
    geometry = Geometry(scene)
    assert geometry.world("dot-2") == [1, 0, 0, 1, 50, 10]
    assert geometry.bounds("dot-2") == [51, 11, 59, 19]
    image = Image.open(BytesIO(render_svg(to_svg(scene), 1))).convert("RGBA")
    assert image.getpixel((55, 15))[3] == 255
    assert image.getpixel((45, 15))[3] == 0
    assert set(scene) == {"schema_version", "revision", "canvas", "objects", "resources"}


def test_explicit_anchors_and_composed_transforms_use_existing_geometry():
    builder = SceneBuilder(200, 150)
    builder.group("panel", transform=[0, 1, -1, 0, 100, 20])
    builder.group("local", parent="panel", transform=[2, 0, 0, 3, 5, 7])
    builder.rect("box", 1, 2, 4, 6, parent="local")
    builder.anchor("local", "port", [4, 6])
    assert builder.anchor_point("local", "port") == pytest.approx((75, 33))
    assert builder.anchor_point("local", "port", relative_to="panel") == pytest.approx((13, 25))
    assert builder.anchor_point("local", "port", relative_to="local") == pytest.approx((4, 6))
    builder.transform("local", [1, 0, 0, 1, 1, 2], compose=True)
    assert builder.anchor_point("local", "port") == pytest.approx((69, 35))
    scene = builder.build()
    assert scene["objects"][2]["geometry"] == {"x": 1, "y": 2, "width": 4, "height": 6}
    assert all("anchors" not in obj for obj in scene["objects"])
    builder.transform("local", [-1, 0, 0, 1, 0, 0])
    assert builder.anchor_point("local", "port", relative_to="panel") == (-4, 6)


def test_generic_primitives_paths_text_and_resources_validate_and_render():
    builder = SceneBuilder(140, 100)
    stops = [[0, "#ff0000"], [1, "#0000ff"]]
    assert builder.resource("shade", "linear_gradient", start=[0, 0], end=[1, 0], stops=stops) == "shade"
    builder.group("panel", style={"fill": "url(#shade)"})
    builder.add("rounded", "rect", {"x": 10, "y": 10, "width": 30, "height": 20, "rx": 3}, parent="panel")
    builder.ellipse("oval", 70, 20, 10, 8, parent="panel")
    builder.path("triangle", "M10 50H30L20 70Z", style={"fill": "#006644"})
    builder.text("label", 50, 60, "Sample", style={"font_size": 12})
    builder.add("link", "connector", {"from": "rounded", "to": "oval",
                                        "from_anchor": "right", "to_anchor": "left"},
                style={"stroke": "#000000", "stroke_width": 2})
    stops[0][1] = "#ffffff"
    scene = builder.build()
    assert validate(scene) is scene
    assert scene["resources"][0]["stops"][0][1] == "#ff0000"
    assert Geometry(scene).endpoints("link") == ((40, 20), (60, 20))
    svg = to_svg(scene)
    assert "<text" in svg and "Sample" in svg and "linearGradient" in svg
    assert Image.open(BytesIO(render_svg(svg, 1))).size == (140, 100)


def test_empty_boolean_is_omitted_instead_of_creating_invalid_scene_path():
    builder = SceneBuilder(40, 40)
    path = "M0 0H30V30H0Z"
    assert builder.path("empty", boolean("difference", path, path)) is None
    assert builder.build()["objects"] == []
    # Omitting empty geometry never allocates an ID.
    assert builder.path("empty", path) == "empty"
    validate(builder.build())


def test_inputs_and_snapshots_are_independent():
    builder = SceneBuilder(100, 100)
    style = {"fill": "#ff0000"}
    geometry = {"x": 0, "y": 0, "width": 10, "height": 10}
    semantic = {"role": "sample", "tags": ["original"]}
    matrix = [1, 0, 0, 1, 10, 10]
    builder.add("box", "rect", geometry, style=style, semantic=semantic, transform=matrix)
    before = builder.build()
    geometry["width"] = 99
    style["fill"] = "#000000"
    semantic["tags"].append("external")
    matrix[4] = 80
    assert builder.build() == before
    snapshot = builder.build()
    snapshot["objects"][0]["geometry"]["width"] = 55
    snapshot["canvas"]["width"] = 150
    assert builder.build() == before


def clone_source():
    builder = SceneBuilder(160, 100)
    builder.resource("shade", "linear_gradient", start=[0, 0], end=[1, 0],
                     stops=[[0, "#ff0000"], [1, "#0000ff"]])
    builder.group("unit", transform=[1, 0, 0, 1, 10, 10], style={"fill": "url(#shade)"})
    builder.rect("left", 0, 0, 10, 10, parent="unit", semantic={"role": "sample"})
    builder.ellipse("right", 25, 5, 5, 5, parent="unit")
    builder.add("wire", "connector", {"from": "left", "to": "right"}, parent="unit")
    builder.anchor("unit", "port", [0, 5])
    builder.anchor("left", "tip", [10, 5])
    return builder


IDS = {"unit": "copy", "left": "copy-left", "right": "copy-right", "wire": "copy-wire"}


def test_clone_remaps_subtree_resources_connectors_and_anchors_independently():
    builder = clone_source()
    original = builder.build()
    assert builder.clone("unit", IDS, resource_ids={"shade": "copy-shade"},
                         transform=[1, 0, 0, 1, 70, 10], parent=None) == "copy"
    scene = builder.build()
    assert scene["objects"][:4] == original["objects"]
    assert [o["id"] for o in scene["objects"]][4:] == ["copy", "copy-left", "copy-right", "copy-wire"]
    nodes = {obj["id"]: obj for obj in scene["objects"]}
    assert nodes["copy"]["style"]["fill"] == "url(#copy-shade)"
    assert nodes["copy-left"]["parent"] == "copy"
    assert nodes["copy-wire"]["geometry"] == {"from": "copy-left", "to": "copy-right"}
    assert builder.anchor_point("copy", "port") == (70, 15)
    assert builder.anchor_point("copy-left", "tip", relative_to="copy") == (10, 5)
    builder.transform("unit", [1, 0, 0, 1, 20, 30])
    assert builder.anchor_point("unit", "port") == (20, 35)
    assert builder.anchor_point("copy", "port") == (70, 15)
    # Ordinary engine edits of the original leave the clone untouched.
    changed = apply_patch(scene, {"base_revision": 0, "operations": [
        {"id": "left", "set": {"geometry": {"width": 7}, "semantic": {"role": "changed"}}}]})
    changed_nodes = {obj["id"]: obj for obj in changed["objects"]}
    assert changed_nodes["copy-left"]["geometry"]["width"] == 10
    assert changed_nodes["copy-left"]["semantic"]["role"] == "sample"
    scene["resources"][0]["stops"][0][1] = "#ffffff"
    assert scene["resources"][1]["stops"][0][1] == "#ff0000"
    assert builder.build()["resources"][0]["stops"][0][1] == "#ff0000"
    validate(changed)
    assert render_svg(to_svg(changed), 1)


def test_clone_requires_explicit_complete_mapping_and_is_transactional():
    builder = clone_source()
    before = builder.build()
    for ids, resources in [({"unit": "copy"}, {"shade": "copy-shade"}),
                            (IDS, {}), (IDS, {"shade": "shade"}),
                            ({**IDS, "left": "copy"}, {"shade": "copy-shade"}),
                            ({**IDS, "left": "left"}, {"shade": "copy-shade"})]:
        with pytest.raises(ValueError):
            builder.clone("unit", ids, resource_ids=resources)
        assert builder.build() == before
    with pytest.raises(ValueError):
        builder.anchor_point("copy", "port")
    builder.group("other")
    builder.add("outside", "connector", {"from": "left", "to": "right"}, parent="other")
    before = builder.build()
    with pytest.raises(ValueError, match="inside the subtree"):
        builder.clone("other", {"other": "other-copy", "outside": "outside-copy"})
    assert builder.build() == before


def test_clone_retains_parent_and_transform_by_default():
    builder = SceneBuilder(100, 100)
    builder.group("outer", transform=[1, 0, 0, 1, 10, 20])
    builder.group("inner", parent="outer", transform=[2, 0, 0, 2, 3, 4])
    builder.rect("box", 0, 0, 5, 5, parent="inner")
    builder.clone("inner", {"inner": "inner-copy", "box": "box-copy"})
    geometry = Geometry(builder.build())
    assert geometry.nodes["inner-copy"]["parent"] == "outer"
    assert geometry.world("box-copy") == geometry.world("box")
    assert geometry.nodes["box-copy"]["geometry"] == geometry.nodes["box"]["geometry"]


@pytest.mark.parametrize("invalid", ["duplicate", "missing-parent", "singular", "nonfinite", "empty", "resource", "collision"])
def test_failed_construction_preserves_valid_builder(invalid):
    builder = SceneBuilder(100, 100)
    builder.rect("box", 0, 0, 10, 10)
    before = builder.build()
    with pytest.raises(ValueError):
        if invalid == "duplicate":
            builder.rect("box", 1, 1, 2, 2)
        elif invalid == "missing-parent":
            builder.rect("new", 0, 0, 1, 1, parent="missing")
        elif invalid == "singular":
            builder.transform("box", [1, 0, 0, 0, 0, 0])
        elif invalid == "nonfinite":
            builder.path("new", "M0 0L1e309 1")
        elif invalid == "empty":
            builder.path("new", " ")
        elif invalid == "resource":
            builder.resource("shade", "pattern", content="unsupported")
        else:
            builder.resource("box", "linear_gradient", start=[0, 0], end=[1, 0],
                             stops=[[0, "#ffffff"], [1, "#000000"]])
    assert builder.build() == before


def test_invalid_anchors_and_transforms_leave_snapshot_unchanged():
    builder = SceneBuilder(100, 100)
    builder.group("group")
    builder.anchor("group", "port", [1, 2])
    before = deepcopy(builder.build())
    for owner, name, xy in [("absent", "a", [0, 0]), ("group", "port", [0, 0]),
                             ("group", "nan", [float("nan"), 0]), ("group", "bad", [0])]:
        with pytest.raises(ValueError):
            builder.anchor(owner, name, xy)
    with pytest.raises(ValueError):
        builder.anchor_point("group", "port", relative_to="absent")
    with pytest.raises(ValueError):
        builder.transform("group", [1, 0])
    assert builder.build() == before
    assert builder.anchor_point("group", "port") == (1, 2)


def test_clone_remaps_nested_typed_semantics_and_copies_semantic_only_resources():
    builder = clone_source()
    builder.resource("semantic-shade", "linear_gradient", start=[0, 0], end=[0, 1],
                     stops=[[0, "#00ff00"], [1, "#000000"]])
    ordinary = {"name": "left", "id": "right", "ref": "semantic-shade",
                "text": "url(#semantic-shade)", "values": ["left", "outside"],
                "precedence": {"type": "annotation", "kind": "resource_ref", "id": "semantic-shade"}}
    semantic = {"object_id": "unit", "object_ids": ["left", "right", "metadata"],
                "resource_id": "shade", "resource_ids": ["shade", "semantic-shade"],
                "nested": [{"type": "object_ref", "id": "right"},
                           {"kind": "object_ref", "ref": "wire"},
                           {"type": "resource_ref", "ref": "semantic-shade"},
                           {"kind": "resource_ref", "id": "shade"},
                           {"type": "object", "ref": "metadata"},
                           {"kind": "resource", "id": "semantic-shade"}],
                "ordinary": ordinary}
    builder.rect("metadata", 0, 30, 5, 5, parent="unit", semantic=semantic)
    original = builder.build()
    ids = {**IDS, "metadata": "copy-metadata"}
    builder.clone("unit", ids, resource_ids={"shade": "copy-shade", "semantic-shade": "copy-semantic-shade"})
    scene = builder.build()
    nodes = {obj["id"]: obj for obj in scene["objects"]}
    assert nodes["metadata"]["semantic"] == semantic
    assert nodes["copy-metadata"]["semantic"] == {
        "object_id": "copy", "object_ids": ["copy-left", "copy-right", "copy-metadata"],
        "resource_id": "copy-shade", "resource_ids": ["copy-shade", "copy-semantic-shade"],
        "nested": [{"type": "object_ref", "id": "copy-right"},
                   {"kind": "object_ref", "ref": "copy-wire"},
                   {"type": "resource_ref", "ref": "copy-semantic-shade"},
                   {"kind": "resource_ref", "id": "copy-shade"},
                   {"type": "object", "ref": "copy-metadata"},
                   {"kind": "resource", "id": "copy-semantic-shade"}],
        "ordinary": ordinary}
    assert scene["objects"][:len(original["objects"])] == original["objects"]
    resources = {resource["id"]: resource for resource in scene["resources"]}
    assert resources["copy-semantic-shade"] == {**resources["semantic-shade"], "id": "copy-semantic-shade"}
    nodes["metadata"]["semantic"]["nested"][0]["id"] = "left"
    nodes["metadata"]["semantic"]["ordinary"]["values"].append("external")
    resources["semantic-shade"]["stops"][0][1] = "#ffffff"
    assert nodes["copy-metadata"]["semantic"]["nested"][0]["id"] == "copy-right"
    assert nodes["copy-metadata"]["semantic"]["ordinary"]["values"] == ["left", "outside"]
    assert resources["copy-semantic-shade"]["stops"][0][1] == "#00ff00"
    assert builder.build()["resources"][:2] == original["resources"]
    validate(scene)
    assert render_svg(to_svg(scene), 1)


RESOURCE_SEMANTICS = [{"resource_id": "shade"}, {"resource_ids": ["shade", "shade"]}] + [
    {discriminator: tag, field: "shade"}
    for discriminator in ("type", "kind") for tag in ("resource", "resource_ref") for field in ("id", "ref")]


@pytest.mark.parametrize("reference", RESOURCE_SEMANTICS)
def test_every_semantic_resource_form_requires_complete_independent_resource_mapping(reference):
    builder = SceneBuilder(100, 100)
    builder.resource("shade", "linear_gradient", start=[0, 0], end=[1, 0],
                     stops=[[0, "#ffffff"], [1, "#000000"]])
    builder.rect("box", 0, 0, 10, 10, semantic={"nested": [reference]})
    before = builder.build()
    for mapping in (None, {}, {"shade": "shade"}, {"shade": "copy-shade", "unused": "extra"}):
        with pytest.raises(ValueError):
            builder.clone("box", {"box": "copy"}, resource_ids=mapping)
        assert builder.build() == before
    builder.clone("box", {"box": "copy"}, resource_ids={"shade": "copy-shade"})
    scene = builder.build()
    cloned = scene["objects"][1]["semantic"]["nested"][0]
    expected = {key: (["copy-shade"] * len(value) if isinstance(value, list) else
                      "copy-shade" if value == "shade" else value) for key, value in reference.items()}
    assert cloned == expected
    assert scene["objects"][0]["semantic"] == {"nested": [reference]}
    assert [r["id"] for r in scene["resources"]] == ["shade", "copy-shade"]
    scene["resources"][0]["stops"][0][1] = "#ff0000"
    assert scene["resources"][1]["stops"][0][1] == "#ffffff"
    validate(scene)


EXTERNAL_OBJECT_SEMANTICS = [{"object_id": "outside"}, {"object_ids": ["root", "outside"]}] + [
    {discriminator: tag, field: "outside"}
    for discriminator in ("type", "kind") for tag in ("object", "object_ref") for field in ("id", "ref")]


@pytest.mark.parametrize("reference", EXTERNAL_OBJECT_SEMANTICS)
def test_clone_rejects_external_typed_object_references_transactionally(reference):
    builder = SceneBuilder(100, 100)
    builder.rect("outside", 0, 0, 5, 5)
    builder.group("root")
    builder.anchor("root", "port", [0, 0])
    builder.rect("child", 10, 10, 5, 5, parent="root", semantic={"nested": [reference]})
    before = builder.build()
    with pytest.raises(ValueError, match="unknown semantic typed reference"):
        builder.clone("root", {"root": "copy", "child": "copy-child"})
    assert builder.build() == before
    with pytest.raises(ValueError):
        builder.anchor_point("copy", "port")


@pytest.mark.parametrize("reference", [
    {"object_id": "missing"}, {"object_ids": "root"}, {"object_ids": ["root", 1]},
    {"resource_id": "missing"}, {"resource_ids": "shade"},
    {"type": "resource_ref", "id": 1}, {"kind": "object_ref", "ref": ["root"]}])
def test_clone_keeps_composition_semantic_validation_and_rejects_dangling_references(reference):
    builder = SceneBuilder(100, 100)
    builder.resource("shade", "linear_gradient", start=[0, 0], end=[1, 0],
                     stops=[[0, "#ffffff"], [1, "#000000"]])
    builder.group("root", semantic={"nested": reference})
    before = builder.build()
    with pytest.raises(ValueError, match="semantic"):
        builder.clone("root", {"root": "copy"}, resource_ids={"shade": "copy-shade"})
    assert builder.build() == before


def test_clone_ordinary_semantic_strings_do_not_require_reference_mappings():
    builder = SceneBuilder(100, 100)
    semantic = {"name": "outside", "id": "missing", "ref": "shade",
                "text": "url(#shade)", "tags": ["outside", "shade"],
                "typed_name": {"type": "annotation", "id": "outside"}}
    builder.rect("box", 0, 0, 5, 5, semantic=semantic)
    builder.clone("box", {"box": "copy"})
    scene = builder.build()
    assert scene["objects"][0]["semantic"] == scene["objects"][1]["semantic"] == semantic
    assert scene["resources"] == []

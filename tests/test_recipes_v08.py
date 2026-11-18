"""Behavioral recipe contracts: reproducibility, structure, editability and ports."""
from copy import deepcopy
from io import BytesIO
import json
import math
from xml.etree import ElementTree

import pytest
from PIL import Image

from scientific_figure import recipes
from scientific_figure.builder import SceneBuilder
from scientific_figure.editing import apply_patch, export_scene
from scientific_figure.geometry import Geometry
from scientific_figure.project import load_project, write_project
from scientific_figure.render import render_svg
from scientific_figure.scene import validate
from scientific_figure.scene_effects import evaluate
from scientific_figure.svg import to_svg


def construct(recipe, params=None, *, version="0.3", instance="sample", **options):
    builder = SceneBuilder(1200, 900, schema_version=version)
    if recipe == "interwoven_bands" and params is None:
        params = recipes.describe(recipe)["examples"][0]
    result = recipes.build(builder, recipe_id=recipe, instance_id=instance, params=params, **options)
    return builder, result


FAMILIES = [item["id"] for item in recipes.list()]


@pytest.mark.parametrize("recipe", FAMILIES)
def test_each_family_is_deterministic_json_and_editable(recipe, tmp_path):
    builder, result = construct(recipe)
    second, again = construct(recipe)
    assert builder.build() == second.build()
    assert result == again
    assert json.loads(json.dumps(result, allow_nan=False)) == result
    scene = builder.build()
    assert validate(scene) is scene
    assert len(result["part_ids"]) > 1
    assert result["source"]["license"] == "project_license_unset"
    root = result["root_id"]
    assert all(obj.get("parent") == root for obj in scene["objects"] if obj["id"] != root)
    assert len(root) <= 100
    assert ElementTree.fromstring(to_svg(scene)).tag == "{http://www.w3.org/2000/svg}svg"
    # Selecting one part exports that part and its ancestors, without siblings.
    leaf = next(obj for obj in scene["objects"] if obj["kind"] not in {"group", "text"})
    exported = export_scene(scene, [leaf["id"]])
    assert set(obj["id"] for obj in exported["objects"]) == {root, leaf["id"]}
    changed = apply_patch(scene, {"base_revision": 0, "operations": [
        {"id": leaf["id"], "set": {"transform": [1, 0, 0, 1, 8, 3]}}]})
    for before, after in zip(scene["objects"], changed["objects"]):
        if before["id"] != leaf["id"]:
            assert before == after
    assert builder.build() == scene
    image = Image.open(BytesIO(render_svg(to_svg(exported), 1))).convert("RGBA")
    assert image.getchannel("A").getbbox() is not None
    saved = tmp_path / recipe
    write_project(changed, saved, scale=.25)
    assert load_project(saved) == changed


# Two nontrivial structural variants per family; changing parameters changes
# local geometry or the editable part tree, not merely an instance transform.
@pytest.mark.parametrize("recipe", FAMILIES)
def test_documented_new_structural_variants(recipe):
    spec = recipes.describe(recipe)
    variants = spec["examples"]
    assert len(variants) >= 2
    scenes = [construct(recipe, p)[0].build() for p in variants[:2]]
    assert scenes[0]["objects"][0]["transform"] == scenes[1]["objects"][0]["transform"]
    assert scenes[0]["objects"][1:] != scenes[1]["objects"][1:]
    shared = set(o["id"] for o in scenes[0]["objects"]) & set(o["id"] for o in scenes[1]["objects"])
    assert len(shared) >= 2


@pytest.mark.parametrize("version", ["0.1", "0.2", "0.3"])
def test_ports_have_local_owner_refs_and_persist_only_on_scene03(version):
    builder, result = construct("arrow", version=version, transform=[0, 2, -3, 0, 250, 60])
    scene = builder.build()
    root = scene["objects"][0]
    assert result["ports"]["end"]["position"] == [100, 0]
    assert result["ports"]["end"]["object_id"] == root["id"]
    assert result["ports"]["end"]["persistent"] == (version == "0.3")
    if version != "0.3":
        assert "anchors" not in root
        assert result["ports"]["end"]["anchor"] is None
    else:
        assert root["anchors"]["end"] == [100, 0]
        target = recipes.build(builder, recipe_id="flow_node", instance_id="target",
                               transform=[1, 0, 0, 1, 600, 100])
        builder.add("link", "connector", {"from": root["id"], "to": target["root_id"],
                    "from_anchor": "port:end", "to_anchor": "port:left"})
        assert Geometry(builder.build()).endpoints("link")[0] == pytest.approx((250, 260))
        builder.transform(root["id"], [1, 0, 0, 1, 10, 20])
        assert Geometry(builder.build()).endpoints("link")[0] == pytest.approx((110, 20))
        exported = export_scene(builder.build(), [root["id"]])
        assert Geometry(exported).anchor(root["id"], "port:end") == pytest.approx((110, 20))
        assert json.loads(json.dumps(exported))["objects"][0]["anchors"] == root["anchors"]


def test_multiple_instances_parent_transform_resources_and_direct_edits_are_independent():
    builder = SceneBuilder(900, 600, schema_version="0.3")
    builder.group("panel", transform=[1, 0, 0, 1, 70, 50])
    first = recipes.build(builder, recipe_id="particle", instance_id="a", params={"shading": True}, parent="panel")
    second = recipes.build(builder, recipe_id="particle", instance_id="b", params={"shading": True},
                           parent="panel", transform=[2, 0, 0, 1, 220, 20])
    scene = builder.build()
    assert set(first["part_ids"].values()).isdisjoint(second["part_ids"].values())
    assert set(first["resource_ids"].values()).isdisjoint(second["resource_ids"].values())
    assert Geometry(scene).anchor(first["root_id"], "port:left") == (32, 50)
    original_b = {o["id"]: deepcopy(o) for o in scene["objects"] if o["id"] in second["part_ids"].values()}
    changed = apply_patch(scene, {"base_revision": 0, "operations": [
        {"id": first["part_ids"]["payload.0"], "set": {"style": {"fill": "#ff0000"}}},
        {"id": first["root_id"], "set": {"transform": [1, 0, 0, 1, 40, 0]}}]})
    assert {o["id"]: o for o in changed["objects"] if o["id"] in original_b} == original_b
    export = export_scene(changed, [second["root_id"]])
    assert [r["id"] for r in export["resources"]] == list(second["resource_ids"].values())
    assert not set(first["part_ids"].values()) & set(o["id"] for o in export["objects"])
    # Returned input snapshots do not alias scene objects/resources.
    first["normalized_params"]["radius"] = 999
    first["ports"]["left"]["position"][0] = -999
    assert builder.build() == scene


def test_text_measurement_enlarges_nodes_and_separates_horizontal_legend_entries():
    short, sr = construct("flow_node", {"label": "A", "width": 20, "height": 20})
    long, lr = construct("flow_node", {"label": "A considerably longer label", "width": 20, "height": 20})
    assert Geometry(long.build()).bounds(lr["part_ids"]["body"])[2] > Geometry(short.build()).bounds(sr["part_ids"]["body"])[2]
    for shape in ["rounded_rect", "ellipse", "diamond"]:
        builder, result = construct("flow_node", {"shape": shape, "label": "Explicit\nnew line", "width": 20, "height": 20})
        geo = Geometry(builder.build())
        body, label = geo.bounds(result["part_ids"]["body"]), geo.bounds(result["part_ids"]["label"])
        assert body[0] < label[0] < label[2] < body[2]
        assert body[1] < label[1] < label[3] < body[3]
    builder, result = construct("legend", {"orientation": "horizontal", "entries": [
        {"label": "A considerably longer legend label", "color": "#123456"},
        {"label": "Next", "color": "#654321"}]})
    geo = Geometry(builder.build())
    assert geo.bounds(result["part_ids"]["label.0"])[2] < geo.bounds(result["part_ids"]["swatch.1"])[0]


@pytest.mark.parametrize("recipe,params", [
    ("arrow", {"start": [0, 0], "end": [0, 0]}),
    ("arrow", {"shaft_width": 40, "head_width": 10}),
    ("leaf", {"length": 20, "width": 60}),
    ("leaf", {"length": 100, "width": 90, "bend": 40}),
    ("cell_symbol", {"nucleus_radius": 100}),
    ("cell_symbol", {"membrane_thickness": 40}),
    ("vessel_segment", {"centerline": [[0, 0], [0, 0]]}),
    ("vessel_segment", {"branches": [[[4, 4], [20, 20]]]}),
    ("particle", {"shell_thickness": 60}),
    ("particle", {"payload_count": 96, "payload_radius": 10}),
    ("particle", {"payload_count": True}),
    ("particle", {"radius": math.nan}),
    ("particle", {"seed": -1}),
    ("particle", {"module": "os"}),
    ("legend", {"entries": [{"label": "x", "color": "token", "other": 2}]}),
    ("zoom_callout", {"roi": [0, 0, -10, 20]}),
    ("zoom_callout", {"inset_origin": [10, 10]}),
    ("loop_band", {"band_width": 80}),
    ("fruit", {"seed_count": 3, "cutaway": False}),
    ("simple_microbe", {"curvature": 80}),
    ("lab_book", {"fold": 80}),
    ("cluster", {"gap": 2, "jitter": 3}),
    ("cluster", {"count": 32}),
    ("interwoven_bands", {"bands": []}),
])
def test_invalid_params_leave_existing_builder_exactly_unchanged(recipe, params):
    builder = SceneBuilder(600, 400, schema_version="0.3")
    builder.rect("existing", 0, 0, 20, 20)
    before = builder.build()
    with pytest.raises(ValueError):
        recipes.build(builder, recipe_id=recipe, instance_id="invalid", params=params)
    assert builder.build() == before


def test_duplicate_ids_bad_parent_style_token_and_transform_are_atomic():
    builder, result = construct("particle", {"shading": True})
    before = builder.build()
    for kwargs in [
        {"instance_id": "sample"}, {"instance_id": "bad", "parent": "missing"},
        {"instance_id": "bad", "transform": [0, 0, 0, 0, 0, 0]},
        {"instance_id": "bad", "style_bindings": {"shell.fill": "carrier_shell"}},
        {"instance_id": "bad", "style_bindings": {"typo.fill": "#123456"}},
        {"instance_id": "bad", "version": "2.0.0"},
        {"instance_id": "sf-reserved"},
    ]:
        with pytest.raises(ValueError):
            recipes.build(builder, recipe_id="particle", **kwargs)
        assert builder.build() == before
    other = recipes.build(builder, recipe_id="particle", instance_id="other",
                          style_bindings={"shell.fill": "#123456", "payload.0.fill": "#abcdef"})
    nodes = {o["id"]: o for o in builder.build()["objects"]}
    assert nodes[other["part_ids"]["shell"]]["style"]["fill"] == "#123456"
    assert nodes[result["part_ids"]["shell"]] == before["objects"][1]


def test_explicit_crossing_order_changes_occlusion_without_inventing_topology():
    params = recipes.describe("interwoven_bands")["examples"][0]
    builder, result = construct("interwoven_bands", params)
    scene = builder.build()
    assert evaluate(scene)["status"] == "current"
    cross = scene["occlusions"][0]
    assert cross["over"] == result["part_ids"]["band.0"]
    assert cross["frame"] == result["root_id"]
    params["crossings"][0]["over"], params["crossings"][0]["under"] = 1, 0
    reverse, reversed_result = construct("interwoven_bands", params)
    assert reverse.build()["occlusions"][0]["over"] == reversed_result["part_ids"]["band.1"]
    assert reverse.build()["objects"] == scene["objects"]
    assert to_svg(reverse.build()) != to_svg(scene)
    params["crossings"][0]["region"] = [0, 0, 2, 2]
    outside, _ = construct("interwoven_bands", params)
    assert evaluate(outside.build())["status"] == "needs_review"
    old = SceneBuilder(200, 200)
    with pytest.raises(ValueError, match="Scene0.2"):
        recipes.build(old, recipe_id="interwoven_bands", instance_id="x", params=params)
    assert old.build()["objects"] == []


def test_seeded_layouts_nonoverlap_and_part_key_stability():
    for recipe, params in [("particle", {"payload_count": 15, "radius": 52}),
                           ("cluster", {"count": 31, "gap": 8, "jitter": 4}),
                           ("cluster", {"count": 9, "layout": "ring", "gap": 8, "jitter": 4})]:
        builder, first = construct(recipe, params)
        prefix = "payload." if recipe == "particle" else "member."
        disks = [o["geometry"] for o in builder.build()["objects"] if o["id"] in
                 [value for key, value in first["part_ids"].items() if key.startswith(prefix)]]
        for i, a in enumerate(disks):
            for b in disks[i+1:]:
                assert math.dist((a["cx"], a["cy"]), (b["cx"], b["cy"])) >= a["rx"]+b["rx"]-1e-9
        changed, second = construct(recipe, {**params, "seed": 99})
        assert first["part_ids"] == second["part_ids"]
        assert builder.build()["objects"] != changed.build()["objects"]
        assert len(builder.build()["objects"][0]["anchors"]) <= 32


def test_registry_is_static_independent_versioned_and_long_instance_ids_fit():
    entries = recipes.list()
    assert len(entries) == 15
    entries[0]["source"]["license"] = "mutated"
    assert recipes.list()[0]["source"]["license"] == "project_license_unset"
    spec = recipes.describe("particle")
    assert spec["params_schema"]["additionalProperties"] is False
    spec["defaults"]["radius"] = 100
    assert recipes.describe("particle")["defaults"]["radius"] == 38
    for function in [lambda: recipes.describe("unknown"), lambda: recipes.list(version="latest"),
                     lambda: recipes.describe("particle", version="0.9")]:
        with pytest.raises(ValueError):
            function()
    builder, first = construct("leaf", instance="a"*100)
    second = recipes.build(builder, recipe_id="leaf", instance_id="a"*99+"b")
    assert set(first["part_ids"].values()).isdisjoint(second["part_ids"].values())
    assert all(len(o["id"]) <= 100 for o in builder.build()["objects"])


def test_aggregate_scene_budget_failure_commits_no_recipe_parts_or_resources():
    builder = SceneBuilder(600, 400, schema_version="0.3")
    builder.add_many([{"id": f"existing.{i}", "kind": "ellipse",
                       "geometry": {"cx": 10, "cy": 10, "rx": 1, "ry": 1}}
                      for i in range(2998)])
    before = builder.build()
    with pytest.raises(ValueError):
        recipes.build(builder, recipe_id="particle", instance_id="over_budget", params={"shading": True})
    assert builder.build() == before


def test_loop_band_hole_is_transparent_in_actual_render():
    builder, result = construct("loop_band", {"rx": 60, "ry": 35, "band_width": 10},
                                transform=[1, 0, 0, 1, 100, 100])
    image = Image.open(BytesIO(render_svg(to_svg(builder.build()), 1))).convert("RGBA")
    assert image.getpixel((100, 100))[3] == 0
    assert image.getpixel((100, 69))[3] > 240
    assert image.getpixel((100, 50))[3] == 0
    assert "loop.0" in result["part_ids"]


def test_vessel_cut_faces_preserve_lumen_openings_in_render():
    builder, _ = construct("vessel_segment", {"centerline": [[0, 0], [120, 0]], "cut_faces": True},
                           transform=[1, 0, 0, 1, 100, 100])
    image = Image.open(BytesIO(render_svg(to_svg(builder.build()), 1))).convert("RGBA")
    assert image.getpixel((100, 100)) == image.getpixel((140, 100))
    assert image.getpixel((100, 113)) == image.getpixel((140, 113))
    assert image.getpixel((100, 100)) != image.getpixel((100, 113))

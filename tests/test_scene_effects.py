from copy import deepcopy
from io import BytesIO

import pytest
from PIL import Image

from scientific_figure.scene import validate
from scientific_figure.svg import to_svg
from scientific_figure.render import render_svg
from scientific_figure.scene_effects import evaluate, bake_occlusions


def scene():
    return {"schema_version": "0.2", "revision": 0, "canvas": {"width": 100, "height": 100},
            "objects": [{"id": "frame", "kind": "group"},
                        {"id": "a", "kind": "path", "parent": "frame", "geometry": {"d": "M10 20H90V40H10Z"}, "style": {"fill": "#286e84"}},
                        {"id": "b", "kind": "path", "parent": "frame", "geometry": {"d": "M40 5H60V85H40Z"}, "style": {"fill": "#d27c3e"}}],
            "resources": [], "occlusions": [{"id": "cross", "over": "a", "under": "b", "frame": "frame", "region": [30, 10, 40, 40], "gap": 3}]}


def image(s):
    return Image.open(BytesIO(render_svg(to_svg(s), 1))).convert("RGBA")


def test_live_cut_source_unchanged_order_independent_and_move_diagnosed():
    s = scene()
    before = deepcopy(s)
    assert validate(s) is s
    ev = evaluate(s)
    assert ev["status"] == "current"
    assert s == before
    initial = image(s)
    assert initial.getpixel((50, 30)) == (40, 110, 132, 255)
    assert initial.getpixel((50, 42))[3] == 0  # transparent gap, not background fill
    s["objects"][1:] = reversed(s["objects"][1:])
    assert image(s).tobytes() == initial.tobytes()
    # Move the over-source: source geometry changes current rendering; stale
    # crossing region is reported rather than certified as current.
    s["objects"][2]["transform"] = [1, 0, 0, 1, 0, 35]
    assert evaluate(s)["status"] == "needs_review"
    assert image(s).tobytes() != initial.tobytes()


def test_whole_group_mirror_nonuniform_scale_and_static_export():
    s = scene()
    s["objects"][0]["transform"] = [-.8, 0, 0, .9, 95, 3]
    im = image(s)
    assert im.getpixel((55, 30)) == (40, 110, 132, 255)
    assert evaluate(s)["status"] == "current"
    baked = bake_occlusions(s)
    assert "occlusions" not in baked
    assert validate(baked)
    assert image(baked).tobytes() == im.tobytes()
    assert s["objects"][2]["geometry"] == {"d": "M40 5H60V85H40Z"}


def test_stroke_width_curve_and_under_move_recompute():
    s = scene()
    a, b = s["objects"][1:]
    a["geometry"] = {"d": "M10 30H90"}
    a["style"] = {"fill": "none", "stroke": "#286e84", "stroke_width": 10}
    baseline = image(s)
    a["style"]["stroke_width"] = 18
    assert image(s).getpixel((50, 40))[3] == 0
    assert baseline.getpixel((50, 40))[3] == 255
    b["transform"] = [1, 0, 0, 1, 10, 0]
    assert evaluate(s)["status"] == "current"
    a["geometry"]["d"] = "M10 30Q50 15 90 30"
    assert image(s).tobytes() != baseline.tobytes()
    a["transform"] = [0, 1, -1, 0, 80, -10]
    assert evaluate(s)["status"] == "needs_review"


def test_local_reversal_can_form_global_cycle_without_geometry_cycle():
    s = scene()
    s["objects"][1]["geometry"]["d"] = "M10 20H90V30H10Z M10 60H90V70H10Z"
    s["occlusions"] = [
        {"id": "top", "over": "a", "under": "b", "frame": "frame", "region": [30, 10, 40, 30], "gap": 2},
        {"id": "bottom", "over": "b", "under": "a", "frame": "frame", "region": [30, 50, 40, 30], "gap": 2}]
    assert evaluate(validate(s))["status"] == "current"
    im = image(s)
    assert im.getpixel((50, 25)) == (40, 110, 132, 255)
    assert im.getpixel((50, 65)) == (210, 124, 62, 255)
    s["objects"][1:] = reversed(s["objects"][1:])
    assert image(s).tobytes() == im.tobytes()


def test_true_clip_alpha_mask_and_group_opacity():
    s = {"schema_version": "0.2", "revision": 0, "canvas": {"width": 100, "height": 100},
         "objects": [{"id": "group", "kind": "group", "style": {"opacity": .5}},
                     {"id": "body", "kind": "rect", "parent": "group", "geometry": {"x": 10, "y": 10, "width": 80, "height": 80},
                      "style": {"fill": "#286e84"}, "clip": "cut", "mask": "fade"}],
         "resources": [{"id": "cut", "kind": "clip_path", "d": "M10 10H70V90H10Z"},
                       {"id": "fade", "kind": "alpha_mask", "region": [0, 0, 100, 100],
                        "shapes": [{"d": "M0 0H100V50H0Z", "alpha": .5}]}]}
    im = image(s)
    assert im.getpixel((20, 20))[3] in {63, 64}
    assert im.getpixel((80, 20))[3] == 0
    assert im.getpixel((20, 80))[3] == 0
    s["objects"][0]["transform"] = [1, 0, 0, 1, 5, 7]
    assert image(s).getpixel((25, 27))[3] in {63, 64}
    assert "mask-type=\"alpha\"" in to_svg(s)


def test_new_scene_version_does_not_change_old_scene_contract():
    s = scene()
    s["schema_version"] = "0.1"
    with pytest.raises(ValueError):
        validate(s)
    s.pop("occlusions")
    s["objects"][1]["clip"] = "anything"
    with pytest.raises(ValueError):
        validate(s)


def test_effect_refs_types_ambiguity_and_unsupported_operands_fail():
    s = scene()
    s["objects"][1]["mask"] = "missing"
    with pytest.raises(ValueError, match="typed mask"):
        validate(s)
    s = scene()
    s["objects"][0]["style"] = {"opacity": .5}
    with pytest.raises(ValueError, match="opaque"):
        validate(s)
    s = scene()
    s["occlusions"].append({**s["occlusions"][0], "id": "conflict", "over": "b", "under": "a"})
    with pytest.raises(ValueError, match="overlapping"):
        validate(s)
    s = scene()
    s["resources"] = [{"id": "paint", "kind": "clip_path", "d": "M0 0H5V5H0Z"}]
    s["objects"][1]["style"]["fill"] = "url(#paint)"
    with pytest.raises(ValueError, match="paint"):
        validate(s)


def test_builder_clone_keeps_live_relations_resources_and_instances_independent():
    from scientific_figure.builder import SceneBuilder
    b = SceneBuilder(210, 100, schema_version="0.2")
    b.resource("clip", "clip_path", d="M0 0H30V30H0Z")
    b.group("unit")
    b.path("a", "M10 20H90V40H10Z", parent="unit", style={"fill": "#286e84"})
    b.path("b", "M40 5H60V85H40Z", parent="unit", style={"fill": "#d27c3e"})
    b.rect("detail", 0, 0, 10, 10, parent="unit", clip="clip")
    b.occlusion("cross", "a", "b", frame="unit", region=[30, 10, 40, 40], gap=3)
    b.clone("unit", {k: "copy-"+k for k in ("unit", "a", "b", "detail")},
            resource_ids={"clip": "copy-clip"}, occlusion_ids={"cross": "copy-cross"}, transform=[1, 0, 0, 1, 105, 0])
    s = b.build()
    assert s["occlusions"][1]["frame"] == "copy-unit"
    assert evaluate(s)["status"] == "current"
    before = image(s)
    b.transform("a", [1, 0, 0, 1, 0, 4])
    after = image(b.build())
    assert before.crop((105, 0, 210, 100)).tobytes() == after.crop((105, 0, 210, 100)).tobytes()
    assert before.crop((0, 0, 100, 100)).tobytes() != after.crop((0, 0, 100, 100)).tobytes()


def test_bundle_roundtrip_composition_remaps_all_effect_refs(tmp_path):
    import json
    import shutil
    from scientific_figure.bundles import create_bundle, validate_bundle
    from scientific_figure.composition import compose
    from scientific_figure.project import load_project, write_project
    s = scene()
    s["resources"] = [{"id": "cut", "kind": "clip_path", "d": "M0 0H15V15H0Z"}]
    s["objects"].append({"id": "detail", "kind": "rect", "parent": "frame", "clip": "cut",
                         "geometry": {"x": 0, "y": 0, "width": 10, "height": 10}})
    write_project(s, tmp_path / "source", scale=1)
    request = tmp_path / "bundle.json"
    request.write_text(json.dumps({"schema_version": "0.1", "assets": [{"id": "asset", "project": "source"}]}))
    create_bundle(request, tmp_path / "bundle")
    shutil.rmtree(tmp_path / "source")
    assert validate_bundle(tmp_path / "bundle")["asset_count"] == 1
    plan = {"schema_version": "0.1", "canvas": {"width": 210, "height": 100},
            "placements": [{"asset_id": "asset", "instance_id": "left", "transform": [1, 0, 0, 1, 0, 0]},
                           {"asset_id": "asset", "instance_id": "right", "transform": [1, 0, 0, 1, 105, 0]}]}
    request.write_text(json.dumps(plan))
    compose(request, tmp_path / "bundle", tmp_path / "composed", scale=1)
    merged = load_project(tmp_path / "composed")
    assert merged["schema_version"] == "0.2"
    assert len(merged["occlusions"]) == 2
    assert evaluate(merged)["status"] == "current"
    im = image(merged)
    assert im.getpixel((50, 30)) == im.getpixel((155, 30)) == (40, 110, 132, 255)
    assert len({r["id"] for r in merged["resources"]}) == 2
    assert all(o["clip"] != "cut" for o in merged["objects"] if "clip" in o)


def test_exact_patch_clears_effects_and_selected_export_bakes_external_cut():
    from scientific_figure.editing import apply_patch, export_scene
    s = scene()
    selected = export_scene(s, ["b"])
    assert not selected.get("occlusions")
    assert {o["id"] for o in selected["objects"]} == {"frame", "b"}
    assert selected["provenance"]["occlusion_export"]["baked"]
    assert image(selected).getchannel("A").getextrema() == (0, 255)
    changed = apply_patch(s, {"schema_version": "0.4", "base_revision": 0, "operations": [
        {"op": "scene_fields", "set": {}, "unset": ["occlusions"]},
        {"op": "remove_object", "id": "a"}]})
    assert changed["revision"] == 1 and not changed.get("occlusions")
    assert image(changed).getpixel((50, 30)) == (210, 124, 62, 255)
    with pytest.raises(ValueError):
        apply_patch(s, {"schema_version": "0.4", "base_revision": 0, "operations": [{"op": "remove_object", "id": "a"}]})
    assert s["revision"] == 0 and len(s["objects"]) == 3
    s.pop("occlusions")
    s["resources"] = [{"id": "cut", "kind": "clip_path", "d": "M0 0H15V15H0Z"}]
    s["objects"][1]["clip"] = "cut"
    unclip = deepcopy(s["objects"][1]); unclip.pop("clip")
    changed = apply_patch(s, {"schema_version": "0.4", "base_revision": 0, "operations": [
        {"op": "put_object", "object": unclip}, {"op": "remove_resource", "id": "cut"}]})
    assert not changed["resources"] and "clip" not in changed["objects"][1]


def test_protected_clipped_coverage_never_passes_on_original_bbox():
    from scientific_figure.reconstruct import semantic_audit
    s = scene(); s.pop("occlusions")
    s["resources"] = [{"id": "cut", "kind": "clip_path", "d": "M1 1H5V5H1Z"}]
    s["objects"][1]["clip"] = "cut"
    s["provenance"] = {"brief": {"protected_ids": ["a"]}}
    assert semantic_audit(s)["status"] == "rejected"
    s["resources"][0]["d"] = "M1 1H95V95H1Z"
    assert semantic_audit(s)["status"] == "needs_review"
    assert semantic_audit(s)["protected_ids"][0]["effect_coverage"] == "needs_review"


def test_renderer_enforces_typed_resource_closure_and_namespace():
    svg = to_svg(scene())
    with pytest.raises(ValueError):
        render_svg(svg.replace("fill=\"#286e84\"", "fill=\"url(#not-here)\""))
    with pytest.raises(ValueError):
        render_svg(svg.replace("<defs", "<defs onclick=\"bad\""))
    with pytest.raises(ValueError):
        render_svg(svg.replace("http://www.w3.org/2000/svg", "https://invalid.example/svg"))
